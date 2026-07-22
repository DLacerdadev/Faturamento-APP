from fastapi import APIRouter, Depends, UploadFile, File, Form, Request
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session, joinedload
from typing import Optional, List
from datetime import date, datetime
from pydantic import BaseModel, Field
import os
import uuid
import shutil
import logging

from app.db import get_db
from app.models.epi_purchase import (
    EpiPurchasePackage, EpiPurchaseItem, EpiPurchaseDocument,
    EpiCatalog, EpiCatalogSize,
)
from app.services.senior_connector import fetch_active_employees
from app.routers.auth import get_current_user, require_login
from app.services.audit import audit
from app.config import EPI_PURCHASE_EMAIL, is_smtp_configured, GENERATED_REPORTS_DIR

logger = logging.getLogger(__name__)

# Todas as rotas exigem login (dependency no nível do router).
router = APIRouter(prefix="/api/epi-purchases", tags=["epi_purchases"],
                   dependencies=[Depends(require_login)])

EPI_UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "epi_documents")
os.makedirs(EPI_UPLOAD_DIR, exist_ok=True)


class EmployeeSelection(BaseModel):
    # nome é usado só para validação/mensagens (revalidação de ativos) — NÃO é
    # persistido no pedido (o faturamento casa por numcad).
    numcad: int
    nome: Optional[str] = None


class EpiLineInput(BaseModel):
    """Linha 1:1 da compra/entrega — modelo 'controle de EPI'.

    Cada linha representa: funcionário X recebe EPI Y tamanho Z na quantidade Q
    com CA W e valor unit V. Não há cartesiano — cada linha é uma atribuição
    independente.
    """
    numcad: int
    nome: Optional[str] = None   # só para mensagens de validação; não persistido
    cargo: Optional[str] = None  # legado; não persistido
    epi_id: int = Field(..., gt=0)
    tamanho: str = Field(..., min_length=1, max_length=20)
    quantidade: int = Field(..., ge=1)
    valor_unitario: float = Field(..., gt=0)
    ca_numero: Optional[str] = Field(None, max_length=50)


class EpiItemInput(BaseModel):
    """Item da compra (cartesiano funcionário × item).

    `quantidade_total` é opcional: se vier do frontend (override manual), persiste
    em todas as linhas geradas do cartesiano daquele item. Quando NULL, calcula
    `qpf × n_funcionários` automaticamente. Usado na exportação Excel como base
    para 'Qtde total' e 'Valor total'.
    """
    epi_id: int = Field(..., gt=0)
    tamanho: str = Field(..., min_length=1, max_length=20)
    quantidade_por_funcionario: int = Field(..., ge=1)
    quantidade_total: Optional[int] = Field(None, ge=1)
    valor_unitario: float = Field(..., gt=0)


class ProductLineInput(BaseModel):
    """Linha 1:1 de compra/entrega de PRODUTO (uniforme/equipamento), por funcionário.
    Referencia o catálogo de produtos por `produto_codigo`. `tamanho` é opcional
    (uniforme costuma ter; equipamento não). Sem C.A (C.A é só de EPI)."""
    numcad: int
    nome: Optional[str] = None   # só para mensagens de validação; não persistido
    cargo: Optional[str] = None  # legado; não persistido
    produto_codigo: str = Field(..., min_length=1, max_length=40)
    tamanho: Optional[str] = Field(None, max_length=20)
    quantidade: int = Field(..., ge=1)
    valor_unitario: float = Field(..., gt=0)


class EpiPackageCreateV2(BaseModel):
    """Schema da compra/entrega. Modos (COMBINÁVEIS no mesmo pedido — pedido misto):

    - 'linhas' (EPI, 1:1): `linhas` (funcionário↔EPI).
    - 'produtos' (uniforme/equipamento, 1:1): `linhas_produto` (funcionário↔produto).
    - 'cartesiano' (legacy EPI): `employees` + `items`.

    A categoria vale POR ITEM (derivada do catálogo: EPI nas `linhas`, categoria do
    ProductCatalog nas `linhas_produto`). `categoria` do pacote vira derivada
    (única categoria dos itens, ou 'misto') — o campo do payload é só fallback legado.
    """
    empresa: str = "FEMSA"
    mes_ano: str
    codccu: str = Field(..., min_length=1)
    observacao: Optional[str] = None
    categoria: str = "epi"  # legado/fallback — a categoria efetiva é por item
    valor_total_pago: Optional[float] = None  # informado na conciliação (Fase 4)
    # Modo 1:1 EPI
    linhas: Optional[List[EpiLineInput]] = None
    # Modo 1:1 Produto (uniforme/equipamento)
    linhas_produto: Optional[List[ProductLineInput]] = None
    # Modo cartesiano (legacy)
    employees: Optional[List[EmployeeSelection]] = None
    items: Optional[List[EpiItemInput]] = None

    @property
    def usa_modo_linhas(self) -> bool:
        return bool(self.linhas)

    @property
    def usa_modo_produtos(self) -> bool:
        return bool(self.linhas_produto)

    def funcionarios_efetivos(self) -> List[EmployeeSelection]:
        """Lista distinct de funcionários do pacote — para revalidação de ativos
        no Senior — em qualquer modo."""
        seen = {}
        if self.linhas:
            for l in self.linhas:
                seen.setdefault(l.numcad, EmployeeSelection(numcad=l.numcad, nome=l.nome))
        if self.linhas_produto:
            for l in self.linhas_produto:
                seen.setdefault(l.numcad, EmployeeSelection(numcad=l.numcad, nome=l.nome))
        if self.employees:
            for e in self.employees:
                seen.setdefault(e.numcad, e)
        return list(seen.values())


def _resolve_catalog_items(db: Session, items: List[EpiItemInput], allow_inactive_ids: Optional[set] = None) -> tuple:
    """
    Resolve cada item do payload contra o catálogo. Retorna (resolved, error_msg).
    - `resolved` é lista de dicts: {epi_id, epi_nome, tamanho, qpf, valor_unitario, valor_unitario_catalogo}
    - `error_msg` é string com a primeira validação que falhou, ou None se tudo OK.
    `allow_inactive_ids` é um conjunto de epi_ids que podem estar inativos (caso de edição
    de pacote que já referencia EPI desativado).
    """
    allow_inactive_ids = allow_inactive_ids or set()
    resolved = []
    for item in items:
        epi = db.query(EpiCatalog).filter(EpiCatalog.id == item.epi_id).first()
        if epi is None:
            return None, f"EPI id={item.epi_id} não existe no catálogo."
        if not epi.ativo and epi.id not in allow_inactive_ids:
            return None, f"EPI '{epi.nome}' (id={epi.id}) está desativado e não pode ser usado em novas compras."
        size = (
            db.query(EpiCatalogSize)
            .filter(EpiCatalogSize.epi_id == epi.id, EpiCatalogSize.tamanho == item.tamanho)
            .first()
        )
        if size is None:
            return None, f"Tamanho '{item.tamanho}' não está cadastrado para o EPI '{epi.nome}'."
        resolved.append({
            "epi_id": epi.id,
            "epi_nome": epi.nome,
            "tamanho": item.tamanho,
            "qpf": item.quantidade_por_funcionario,
            "quantidade_total_override": item.quantidade_total,
            "valor_unitario": item.valor_unitario,
            "valor_unitario_catalogo": size.valor,
        })
    return resolved, None


def _expand_cartesian(pkg: EpiPurchasePackage, payload: EpiPackageCreateV2, resolved_items: List[dict]) -> None:
    """
    Adiciona linhas |employees| × |items| ao pacote (sem commit).
    Usado APENAS no modo legacy (cartesiano). Modo 1:1 usa `_create_lines`.
    Cada linha replica `quantidade_total_item` (override do usuário, ou
    `qpf × n_funcionários` se não houver override).
    """
    employees = payload.employees or []
    n_emps = len(employees)
    for emp in employees:
        for item in resolved_items:
            qtd_total = item.get("quantidade_total_override") or (item["qpf"] * n_emps)
            pkg.items.append(EpiPurchaseItem(
                descricao=item["epi_nome"],
                quantidade=item["qpf"],
                valor_unitario=item["valor_unitario"],
                valor_total=item["qpf"] * item["valor_unitario"],
                employee_numcad=emp.numcad,
                categoria="epi",
                epi_id=item["epi_id"],
                tamanho=item["tamanho"],
                quantidade_por_funcionario=item["qpf"],
                quantidade_total_item=qtd_total,
                valor_unitario_catalogo=item["valor_unitario_catalogo"],
            ))


def _create_lines(pkg: EpiPurchasePackage, payload: EpiPackageCreateV2, db: Session) -> Optional[str]:
    """
    Modo 1:1: cria UMA linha em EpiPurchaseItem por entrada em `payload.linhas`.
    Resolve cada linha contra o catálogo (epi_id + tamanho). Retorna mensagem
    de erro string, ou None se OK. Sem commit.
    """
    existing_epi_ids = {it.epi_id for it in (pkg.items or []) if it.epi_id is not None}
    for ln in (payload.linhas or []):
        epi = db.query(EpiCatalog).filter(EpiCatalog.id == ln.epi_id).first()
        if epi is None:
            return f"EPI id={ln.epi_id} não existe no catálogo."
        if not epi.ativo and epi.id not in existing_epi_ids:
            return f"EPI '{epi.nome}' (id={epi.id}) está desativado."
        size = (
            db.query(EpiCatalogSize)
            .filter(EpiCatalogSize.epi_id == epi.id, EpiCatalogSize.tamanho == ln.tamanho)
            .first()
        )
        if size is None:
            return f"Tamanho '{ln.tamanho}' não cadastrado para o EPI '{epi.nome}'."

        valor_total = ln.quantidade * ln.valor_unitario
        # CA: usa o da linha se vier; senão, snapshot do ca_padrao do catálogo.
        ca = ln.ca_numero.strip() if ln.ca_numero else (epi.ca_padrao or None)

        pkg.items.append(EpiPurchaseItem(
            descricao=epi.nome,
            quantidade=ln.quantidade,
            valor_unitario=ln.valor_unitario,
            valor_total=valor_total,
            employee_numcad=ln.numcad,
            categoria="epi",
            epi_id=epi.id,
            tamanho=ln.tamanho,
            quantidade_por_funcionario=ln.quantidade,
            quantidade_total_item=None,  # 1:1 não usa override cartesiano; total = soma das linhas
            ca_numero=ca,
            valor_unitario_catalogo=size.valor,
        ))
    return None


def _create_product_lines(pkg: EpiPurchasePackage, payload: EpiPackageCreateV2, db: Session) -> Optional[str]:
    """Modo 1:1 de PRODUTO (uniforme/equipamento): cria uma linha por entrada em
    `linhas_produto`. Resolve contra o ProductCatalog (produto_codigo) e grava o
    preço efetivo (preço do CC → senão catálogo) como snapshot de catálogo. Sem commit."""
    from app.models.product_catalog import ProductCatalog
    from app.services.pricing import effective_price
    for ln in (payload.linhas_produto or []):
        prod = db.query(ProductCatalog).filter(ProductCatalog.codigo == ln.produto_codigo).first()
        if prod is None:
            return f"Produto código '{ln.produto_codigo}' não existe no catálogo."
        if not prod.ativo:
            return f"Produto '{prod.descricao}' ({ln.produto_codigo}) está inativo."
        cat_ref = effective_price(db, payload.codccu, ln.produto_codigo, ln.tamanho or "")
        # Categoria POR ITEM vem do catálogo de produtos (epi | uniforme | equipamento).
        cat_item = (prod.categoria or "").strip().lower()
        if cat_item not in ("epi", "uniforme", "equipamento"):
            cat_item = "equipamento"
        pkg.items.append(EpiPurchaseItem(
            descricao=prod.descricao or ln.produto_codigo,
            quantidade=ln.quantidade,
            valor_unitario=ln.valor_unitario,
            valor_total=ln.quantidade * ln.valor_unitario,
            employee_numcad=ln.numcad,
            categoria=cat_item,
            epi_id=None,
            produto_codigo=ln.produto_codigo,
            tamanho=ln.tamanho,
            quantidade_por_funcionario=ln.quantidade,
            quantidade_total_item=None,  # 1:1 não usa override cartesiano; total = soma das linhas
            valor_unitario_catalogo=cat_ref,
        ))
    return None


def _derive_pkg_categoria(pkg: EpiPurchasePackage, fallback: str = "epi") -> str:
    """Categoria do PACOTE derivada dos itens: a única categoria presente, ou
    'misto' quando o pedido combina categorias. Fallback pro legado sem categoria."""
    cats = {(it.categoria or "").strip().lower() for it in (pkg.items or []) if it.categoria}
    if not cats:
        return fallback or "epi"
    if len(cats) == 1:
        return cats.pop()
    return "misto"


def _recompute_totals(pkg: EpiPurchasePackage) -> None:
    """Recalcula totais agregados a partir das linhas e persiste no pacote (sem commit).

    Usa `quantidade_total_item` (replicado em cada linha do cartesiano) agrupando
    por (epi_id, tamanho, qpf, valor_unitario) para não somar a mesma qtd N vezes.
    Fallback: se nenhuma linha tem quantidade_total_item, soma `quantidade` linha
    a linha (modo legacy).
    """
    items_list = list(pkg.items or [])
    seen_keys: set = set()
    qtde = 0
    valor = 0.0
    fallback = True
    for it in items_list:
        if it.quantidade_total_item is None:
            continue
        fallback = False
        key = (it.epi_id, it.tamanho or "", it.quantidade_por_funcionario,
               round(it.valor_unitario or 0.0, 6))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        qtd_t = it.quantidade_total_item
        qtde += qtd_t
        valor += qtd_t * (it.valor_unitario or 0.0)
    if fallback:
        qtde = sum((it.quantidade or 0) for it in items_list)
        valor = sum((it.valor_total or 0.0) for it in items_list)
    pkg.quantidade_total_geral = qtde
    pkg.valor_total_compra_geral = round(valor, 2)


def _audit_pkg_detail(pkg: EpiPurchasePackage) -> dict:
    """Detalhe padrão de auditoria do pedido: categorias, valor_total, codccu, mes_ref."""
    items_list = list(pkg.items or [])
    categorias = [
        c for c in ("epi", "uniforme", "equipamento")
        if any((it.categoria or pkg.categoria or "epi") == c for it in items_list)
    ] or [pkg.categoria or "epi"]
    return {
        "categorias": categorias,
        "valor_total": pkg.valor_total_compra_geral,
        "codccu": pkg.codccu,
        "mes_ref": pkg.mes_ano.isoformat() if pkg.mes_ano else None,
    }


def _resolve_solicitante(request: Request, db: Session) -> str:
    """Pega o usuário logado e retorna full_name (fallback email, fallback 'Usuário')."""
    try:
        user = get_current_user(request, db)
        if user:
            return (getattr(user, "full_name", None) or getattr(user, "email", None) or "Usuário").strip()
    except Exception:
        pass
    return "Usuário"


def _generate_solicitacao_for(pkg: EpiPurchasePackage, db: Session) -> None:
    """
    Gera o Excel da solicitação (feature 002, FR-14). Grava em GENERATED_REPORTS_DIR
    e atualiza pkg.solicitacao_filename + solicitacao_generated_at. Sem commit.
    Pula silenciosamente se o pacote não tem itens com epi_id (legacy/sem catálogo).
    """
    from app.services.epi_solicitation_excel import generate_solicitacao_xlsx, build_filename

    has_catalog_items = any(
        getattr(it, "epi_id", None) or getattr(it, "produto_codigo", None)
        for it in (pkg.items or [])
    )
    if not has_catalog_items:
        return  # legacy: não gera

    # apaga arquivo anterior se existir
    if pkg.solicitacao_filename:
        old_path = GENERATED_REPORTS_DIR / pkg.solicitacao_filename
        try:
            if old_path.exists():
                old_path.unlink()
        except OSError as e:
            logger.warning("Falha ao remover Excel anterior %s: %s", old_path, e)

    pkg.solicitacao_generated_at = datetime.utcnow()
    pkg.solicitacao_filename = build_filename(pkg)
    xlsx_bytes = generate_solicitacao_xlsx(pkg)
    target = GENERATED_REPORTS_DIR / pkg.solicitacao_filename
    target.write_bytes(xlsx_bytes)


def _revalidate_active(payload: EpiPackageCreateV2) -> List[dict]:
    """
    Spec 001-epi-purchase-flow, FR-13: ao salvar, checa se cada `numcad` recebido
    ainda está ativo no CCU. Funciona com modo cartesiano (employees) ou modo
    1:1 (linhas) via `funcionarios_efetivos()`. Retorna lista de inativos.
    """
    try:
        active = fetch_active_employees(payload.codccu)
    except Exception:
        # Se a consulta Senior falhar, não bloqueia o save — apenas pula a revalidação.
        return []
    active_numcads = {e.get("numcad") for e in active}
    inactive = []
    for emp in payload.funcionarios_efetivos():
        if emp.numcad not in active_numcads:
            inactive.append({
                "numcad": emp.numcad,
                "nome": emp.nome,
                "motivo": "Não está mais ativo no CCU informado",
            })
    return inactive


def _dict_distinct(seq, key):
    seen = set()
    out = []
    for x in seq:
        k = key(x)
        if k not in seen:
            seen.add(k)
            out.append(x)
    return out


def package_to_dict(pkg: EpiPurchasePackage) -> dict:
    items_list = list(pkg.items or [])

    linhas_flat = [
        {
            "id": item.id,
            "descricao": item.descricao,
            "quantidade": item.quantidade,
            "valor_unitario": item.valor_unitario,
            "valor_total": item.valor_total,
            "employee_numcad": item.employee_numcad,
            "employee_nome": item.employee_nome,
            "employee_cargo": item.employee_cargo,
            "categoria": (item.categoria or pkg.categoria or "epi"),
            "epi_id": item.epi_id,
            "produto_codigo": item.produto_codigo,
            "tamanho": item.tamanho,
            "quantidade_por_funcionario": item.quantidade_por_funcionario,
            "ca_numero": item.ca_numero,
            "valor_unitario_catalogo": item.valor_unitario_catalogo,
        }
        for item in items_list
    ]

    funcionarios = _dict_distinct(
        [
            {"numcad": item.employee_numcad, "nome": item.employee_nome}
            for item in items_list
            if item.employee_numcad is not None
        ],
        key=lambda f: f["numcad"],
    )

    # Agrupado legacy (feature 001): por descricao/quantidade/valor_unitario
    itens_distintos = _dict_distinct(
        [
            {
                "descricao": item.descricao,
                "quantidade": item.quantidade,
                "valor_unitario": item.valor_unitario,
            }
            for item in items_list
        ],
        key=lambda i: (i["descricao"], i["quantidade"], i["valor_unitario"]),
    )

    # Agrupado v2 (feature 002): por epi_id (ou produto_codigo) + tamanho + qtde_por_func
    # + valor_unitario, com totais por item e flag de override de valor vs catálogo.
    # Legado = pacote cujas linhas NÃO têm epi_id NEM produto_codigo (compras antigas
    # sem vínculo com catálogo). Produtos (uniforme/equipamento) entram no agrupado_v2
    # via produto_codigo.
    is_legacy_pkg = bool(items_list) and all(
        it.epi_id is None and it.produto_codigo is None for it in items_list
    )
    itens_v2_buckets: dict = {}
    for it in items_list:
        if it.epi_id is None and it.produto_codigo is None:
            continue
        qpf = it.quantidade_por_funcionario if it.quantidade_por_funcionario is not None else it.quantidade
        # Chave do bucket: epi_id quando EPI; senão 'p:'+produto_codigo (produto TOTVS).
        item_key = it.epi_id if it.epi_id is not None else ("p:" + str(it.produto_codigo))
        key = (item_key, it.tamanho or "", qpf, round(it.valor_unitario or 0.0, 6))
        bucket = itens_v2_buckets.setdefault(key, {
            "epi_id": it.epi_id,
            "produto_codigo": it.produto_codigo,
            "categoria": (it.categoria or pkg.categoria or "epi"),
            "epi_nome": it.descricao,
            "tamanho": it.tamanho,
            "quantidade_por_funcionario": qpf,
            "valor_unitario": it.valor_unitario,
            "valor_unitario_catalogo": it.valor_unitario_catalogo,
            "valor_unitario_difere_do_catalogo": (
                it.valor_unitario_catalogo is not None
                and abs((it.valor_unitario_catalogo or 0.0) - (it.valor_unitario or 0.0)) > 0.001
            ),
            "funcionarios_atendidos": set(),
            "quantidade_total_item": None,  # override do usuário, se houver
            "_quantidade_total_fallback": 0,
            "valor_total_item": 0.0,
        })
        if it.employee_numcad is not None:
            bucket["funcionarios_atendidos"].add(it.employee_numcad)
        # qtd_total_item: pega o primeiro valor não-NULL encontrado nas linhas
        if bucket["quantidade_total_item"] is None and it.quantidade_total_item is not None:
            bucket["quantidade_total_item"] = it.quantidade_total_item
        bucket["_quantidade_total_fallback"] += (it.quantidade or 0)
        bucket["valor_total_item"] += (it.valor_total or 0.0)
    itens_v2 = []
    for b in itens_v2_buckets.values():
        n_funcs = len(b["funcionarios_atendidos"])
        b["funcionarios_atendidos"] = n_funcs
        # Se não tem override persistido, calcula qpf × n_funcs
        if b["quantidade_total_item"] is None:
            b["quantidade_total_item"] = (b["quantidade_por_funcionario"] or 0) * n_funcs
        del b["_quantidade_total_fallback"]
        # valor_total_item sempre = quantidade_total × valor_unit (respeita override)
        b["valor_total_item"] = round(
            (b["quantidade_total_item"] or 0) * (b["valor_unitario"] or 0.0), 2
        )
        itens_v2.append(b)

    valor_total_legacy = round(sum(item.valor_total or 0 for item in items_list), 2)
    qtde_total_legacy = sum(item.quantidade or 0 for item in items_list)

    # Download disponível para qualquer pacote com itens de catálogo — o endpoint
    # gera o Excel sob demanda quando o arquivo ainda não existe.
    has_catalog_items = any(
        it.epi_id is not None or it.produto_codigo is not None for it in items_list
    )
    solicitacao_block = {
        "filename": pkg.solicitacao_filename,
        "generated_at": pkg.solicitacao_generated_at.isoformat() if pkg.solicitacao_generated_at else None,
        "available_for_download": has_catalog_items and not is_legacy_pkg,
        "download_url": f"/api/epi-purchases/{pkg.id}/solicitacao" if has_catalog_items else None,
    }

    return {
        "id": pkg.id,
        "empresa": pkg.empresa,
        "mes_ano": pkg.mes_ano.isoformat() if pkg.mes_ano else None,
        "codccu": pkg.codccu,
        "observacao": pkg.observacao,
        "categoria": pkg.categoria or "epi",
        # Categorias distintas dos itens (pedido misto) — ordem fixa pra UI.
        "categorias": [
            c for c in ("epi", "uniforme", "equipamento")
            if any((it.categoria or pkg.categoria or "epi") == c for it in items_list)
        ] or [pkg.categoria or "epi"],
        "status": pkg.status or "rascunho",
        "solicitante_nome": pkg.solicitante_nome,
        "is_legacy": is_legacy_pkg,
        "linhas_flat": linhas_flat,
        "agrupado": {
            "funcionarios": funcionarios,
            "itens": itens_distintos,
        },
        "agrupado_v2": {
            "funcionarios": funcionarios,
            "itens": itens_v2,
        },
        "totais": {
            "funcionarios_distintos": len(funcionarios),
            "itens_distintos": len(itens_distintos),
            "total_linhas": len(items_list),
            "valor_total_compra": valor_total_legacy,
            "quantidade_total_geral": pkg.quantidade_total_geral if pkg.quantidade_total_geral is not None else qtde_total_legacy,
            "valor_total_compra_geral": pkg.valor_total_compra_geral if pkg.valor_total_compra_geral is not None else valor_total_legacy,
        },
        "solicitacao": solicitacao_block,
        # legacy keys (retro-compat)
        "items": linhas_flat,
        "total_geral": valor_total_legacy,
        "documents": [
            {
                "id": doc.id,
                "original_filename": doc.original_filename,
                "uploaded_at": doc.uploaded_at.isoformat() if doc.uploaded_at else None,
            }
            for doc in (pkg.documents or [])
        ],
        "created_at": pkg.created_at.isoformat() if pkg.created_at else None,
        "updated_at": pkg.updated_at.isoformat() if pkg.updated_at else None,
    }


@router.post("")
async def create_package(data: EpiPackageCreateV2, request: Request, db: Session = Depends(get_db)):
    try:
        mes_ano_date = datetime.strptime(data.mes_ano[:7], "%Y-%m").date()
    except ValueError:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "Formato de mes_ano invalido. Use YYYY-MM"},
        )

    # Valida payload: precisa de um dos dois modos
    if not data.usa_modo_linhas and not data.usa_modo_produtos and not (data.employees and data.items):
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "Informe `linhas` (EPI), `linhas_produto` (produto) ou `employees`+`items` (cartesiano)."},
        )

    # Revalida funcionários ativos (ambos os modos)
    inactive = _revalidate_active(data)
    if inactive:
        return JSONResponse(
            status_code=409,
            content={
                "status": "stale",
                "message": "Alguns funcionários selecionados já não estão ativos.",
                "inactive": inactive,
            },
        )

    pkg = EpiPurchasePackage(
        empresa=data.empresa,
        mes_ano=mes_ano_date,
        codccu=data.codccu,
        observacao=data.observacao,
        categoria=(data.categoria or "epi"),
        valor_total_pago=data.valor_total_pago,
        solicitante_nome=_resolve_solicitante(request, db),
    )

    # Pedido MISTO: `linhas` (EPI) e `linhas_produto` (uniforme/equipamento) podem
    # vir juntos no mesmo pedido — categoria vale por item.
    if data.usa_modo_linhas:
        err = _create_lines(pkg, data, db)
        if err:
            return JSONResponse(status_code=400, content={"status": "error", "message": err})
    if data.usa_modo_produtos:
        err = _create_product_lines(pkg, data, db)
        if err:
            return JSONResponse(status_code=400, content={"status": "error", "message": err})
    if not data.usa_modo_linhas and not data.usa_modo_produtos:
        resolved, err = _resolve_catalog_items(db, data.items)
        if err:
            return JSONResponse(status_code=400, content={"status": "error", "message": err})
        _expand_cartesian(pkg, data, resolved)

    pkg.categoria = _derive_pkg_categoria(pkg, fallback=(data.categoria or "epi"))
    _recompute_totals(pkg)
    db.add(pkg)
    db.commit()
    db.refresh(pkg)

    # Gera o Excel da solicitação (pedido de compra) para qualquer categoria.
    try:
        _generate_solicitacao_for(pkg, db)
        db.commit()
        db.refresh(pkg)
    except Exception as e:
        logger.error("Falha ao gerar Excel da solicitação para pkg %s: %s", pkg.id, e)

    audit(request, "pedido.criar", entidade="epi_purchase_packages",
          entidade_id=str(pkg.id), detalhe=_audit_pkg_detail(pkg), db=db)

    return {"status": "success", "data": package_to_dict(pkg)}


@router.get("")
async def list_packages(
    empresa: Optional[str] = None,
    mes_ano: Optional[str] = None,
    page: int = 1,
    per_page: int = 20,
    db: Session = Depends(get_db),
):
    query = db.query(EpiPurchasePackage).options(
        joinedload(EpiPurchasePackage.items),
        joinedload(EpiPurchasePackage.documents),
    )

    if empresa:
        query = query.filter(EpiPurchasePackage.empresa == empresa)
    if mes_ano:
        try:
            filter_date = datetime.strptime(mes_ano[:7], "%Y-%m").date()
            query = query.filter(EpiPurchasePackage.mes_ano == filter_date)
        except ValueError:
            pass

    total = query.count()
    packages = (
        query.order_by(EpiPurchasePackage.mes_ano.desc(), EpiPurchasePackage.id.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    seen = set()
    unique_packages = []
    for p in packages:
        if p.id not in seen:
            seen.add(p.id)
            unique_packages.append(p)

    return {
        "status": "ok",
        "data": [package_to_dict(p) for p in unique_packages],
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": max(1, (total + per_page - 1) // per_page),
    }


@router.get("/smtp-status")
async def get_smtp_status():
    """Frontend usa para mostrar/esmaecer o botão 'Enviar por email'.
    IMPORTANTE: precisa ficar antes de /{package_id} senão o FastAPI tenta
    casar 'smtp-status' como int e devolve 422."""
    return {
        "smtp_configured": is_smtp_configured(),
        "default_recipient": EPI_PURCHASE_EMAIL or "",
    }


@router.get("/{package_id}")
async def get_package(package_id: int, db: Session = Depends(get_db)):
    pkg = (
        db.query(EpiPurchasePackage)
        .options(
            joinedload(EpiPurchasePackage.items),
            joinedload(EpiPurchasePackage.documents),
        )
        .filter(EpiPurchasePackage.id == package_id)
        .first()
    )
    if not pkg:
        return {"status": "error", "message": "Pacote nao encontrado"}
    return {"status": "ok", "data": package_to_dict(pkg)}


@router.put("/{package_id}")
async def update_package(package_id: int, data: EpiPackageCreateV2, request: Request, db: Session = Depends(get_db)):
    pkg = (
        db.query(EpiPurchasePackage)
        .options(joinedload(EpiPurchasePackage.items), joinedload(EpiPurchasePackage.documents))
        .filter(EpiPurchasePackage.id == package_id)
        .first()
    )
    if not pkg:
        return JSONResponse(
            status_code=404,
            content={"status": "error", "message": "Pacote nao encontrado"},
        )

    try:
        mes_ano_date = datetime.strptime(data.mes_ano[:7], "%Y-%m").date()
    except ValueError:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "Formato de mes_ano invalido. Use YYYY-MM"},
        )

    # Valida payload: precisa de um dos dois modos
    if not data.usa_modo_linhas and not data.usa_modo_produtos and not (data.employees and data.items):
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "Informe `linhas` (EPI), `linhas_produto` (produto) ou `employees`+`items` (cartesiano)."},
        )

    inactive = _revalidate_active(data)
    if inactive:
        return JSONResponse(
            status_code=409,
            content={
                "status": "stale",
                "message": "Alguns funcionários selecionados já não estão ativos.",
                "inactive": inactive,
            },
        )

    pkg.empresa = data.empresa
    pkg.mes_ano = mes_ano_date
    pkg.codccu = data.codccu
    pkg.observacao = data.observacao
    pkg.valor_total_pago = data.valor_total_pago
    pkg.solicitante_nome = _resolve_solicitante(request, db)

    # Permite EPIs já vinculados ao pacote mesmo se desativados (capturar ANTES de apagar)
    existing_epi_ids = {it.epi_id for it in (pkg.items or []) if it.epi_id is not None}

    for old_item in list(pkg.items):
        db.delete(old_item)
    db.flush()

    # Pedido MISTO: ambos os modos podem vir juntos (categoria por item).
    if data.usa_modo_linhas:
        err = _create_lines(pkg, data, db)
        if err:
            return JSONResponse(status_code=400, content={"status": "error", "message": err})
    if data.usa_modo_produtos:
        err = _create_product_lines(pkg, data, db)
        if err:
            return JSONResponse(status_code=400, content={"status": "error", "message": err})
    if not data.usa_modo_linhas and not data.usa_modo_produtos:
        resolved, err = _resolve_catalog_items(db, data.items, allow_inactive_ids=existing_epi_ids)
        if err:
            return JSONResponse(status_code=400, content={"status": "error", "message": err})
        _expand_cartesian(pkg, data, resolved)

    pkg.categoria = _derive_pkg_categoria(pkg, fallback=(data.categoria or "epi"))
    _recompute_totals(pkg)

    db.commit()
    db.refresh(pkg)

    # Gera o Excel da solicitação (pedido de compra) para qualquer categoria.
    try:
        _generate_solicitacao_for(pkg, db)
        db.commit()
        db.refresh(pkg)
    except Exception as e:
        logger.error("Falha ao gerar Excel da solicitação (PUT) para pkg %s: %s", pkg.id, e)

    audit(request, "pedido.editar", entidade="epi_purchase_packages",
          entidade_id=str(pkg.id), detalhe=_audit_pkg_detail(pkg), db=db)

    return {"status": "success", "data": package_to_dict(pkg)}


@router.get("/{package_id}/solicitacao")
async def download_solicitacao(package_id: int, db: Session = Depends(get_db)):
    """Download do Excel da solicitação de compra (feature 002).

    Gera SOB DEMANDA quando o pacote ainda não tem arquivo (ex.: pedidos de
    uniforme/equipamento salvos antes da solicitação valer pra toda categoria)
    ou quando o arquivo sumiu do disco."""
    pkg = (
        db.query(EpiPurchasePackage)
        .options(joinedload(EpiPurchasePackage.items))
        .filter(EpiPurchasePackage.id == package_id)
        .first()
    )
    if not pkg:
        return JSONResponse(status_code=404, content={"status": "error", "message": "Pacote não encontrado"})

    filepath = (GENERATED_REPORTS_DIR / pkg.solicitacao_filename) if pkg.solicitacao_filename else None
    if filepath is None or not filepath.exists():
        try:
            _generate_solicitacao_for(pkg, db)
            db.commit()
            db.refresh(pkg)
        except Exception as e:
            logger.error("Falha ao gerar solicitação sob demanda para pkg %s: %s", pkg.id, e)
        if not pkg.solicitacao_filename:
            return JSONResponse(
                status_code=404,
                content={"status": "error", "message": "Pacote sem solicitação (compra legada, sem itens de catálogo)."},
            )
        filepath = GENERATED_REPORTS_DIR / pkg.solicitacao_filename
        if not filepath.exists():
            return JSONResponse(
                status_code=410,
                content={"status": "error", "message": "Não foi possível gerar o arquivo da solicitação."},
            )
    return FileResponse(
        str(filepath),
        filename=pkg.solicitacao_filename,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


class SolicitacaoEmailInput(BaseModel):
    to: Optional[str] = None
    cc: Optional[str] = None
    subject: Optional[str] = None
    body: Optional[str] = None


@router.post("/{package_id}/solicitacao/email")
async def email_solicitacao(package_id: int, data: SolicitacaoEmailInput, db: Session = Depends(get_db)):
    """Envia a solicitação por email (feature 002, FR-16)."""
    if not is_smtp_configured():
        return JSONResponse(
            status_code=503,
            content={"status": "error", "message": "SMTP não configurado no servidor (.env)."},
        )
    pkg = db.query(EpiPurchasePackage).filter(EpiPurchasePackage.id == package_id).first()
    if not pkg:
        return JSONResponse(status_code=404, content={"status": "error", "message": "Pacote não encontrado"})
    if not pkg.solicitacao_filename:
        return JSONResponse(
            status_code=404,
            content={"status": "error", "message": "Pacote sem solicitação gerada (legado ou novo)."},
        )
    filepath = GENERATED_REPORTS_DIR / pkg.solicitacao_filename
    if not filepath.exists():
        return JSONResponse(
            status_code=410,
            content={"status": "error", "message": "Arquivo da solicitação não está mais disponível. Re-salve o pacote para regenerar."},
        )

    to = (data.to or EPI_PURCHASE_EMAIL or "").strip()
    if not to or "@" not in to:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Destinatário inválido."})
    cc = (data.cc or "").strip() or None
    competencia = pkg.mes_ano.strftime("%m/%Y") if pkg.mes_ano else "—"
    subject = (data.subject or f"Solicitação de compra de EPI #{pkg.id} — {pkg.empresa} — {competencia}").strip()
    body = data.body or (
        f"Segue em anexo a solicitação de compra de EPI #{pkg.id}.\n"
        f"Empresa: {pkg.empresa}\nCentro de custo: {pkg.codccu or '—'}\nCompetência: {competencia}\n"
        f"Solicitante: {pkg.solicitante_nome or '—'}\n\n"
        f"Total: {pkg.quantidade_total_geral or 0} unidades — R$ {(pkg.valor_total_compra_geral or 0):,.2f}\n\n"
        f"-- Faturamento App / Telos Consultoria"
    ).replace(",", "X").replace(".", ",").replace("X", ".") if data.body is None else data.body

    try:
        from app.services.email_sender import send_solicitacao_email
        attachment_bytes = filepath.read_bytes()
        send_solicitacao_email(
            to=to, subject=subject, body_text=body,
            attachment_bytes=attachment_bytes,
            attachment_filename=pkg.solicitacao_filename,
            cc=cc,
        )
    except Exception as e:
        logger.error("Falha ao enviar email da solicitação %s: %s", pkg.id, e)
        return JSONResponse(status_code=500, content={"status": "error", "message": f"Falha ao enviar email: {e}"})

    return {"status": "success", "message": f"Email enviado para {to}" + (f" (cc: {cc})" if cc else "")}


@router.delete("/{package_id}")
async def delete_package(package_id: int, request: Request, db: Session = Depends(get_db)):
    pkg = (
        db.query(EpiPurchasePackage)
        .options(joinedload(EpiPurchasePackage.documents))
        .filter(EpiPurchasePackage.id == package_id)
        .first()
    )
    if not pkg:
        return {"status": "error", "message": "Pacote nao encontrado"}

    # Snapshot ANTES de apagar — a exclusão é física e o log é o único vestígio.
    snapshot = _audit_pkg_detail(pkg)
    snapshot["status_anterior"] = pkg.status or "rascunho"

    for doc in pkg.documents:
        filepath = os.path.join(EPI_UPLOAD_DIR, doc.stored_filename)
        if os.path.exists(filepath):
            os.remove(filepath)

    # Apaga Excel da solicitação se existir
    if pkg.solicitacao_filename:
        sol_path = GENERATED_REPORTS_DIR / pkg.solicitacao_filename
        try:
            if sol_path.exists():
                sol_path.unlink()
        except OSError:
            pass

    db.delete(pkg)
    db.commit()

    audit(request, "pedido.excluir", entidade="epi_purchase_packages",
          entidade_id=str(package_id), detalhe=snapshot, db=db)

    return {"status": "success", "message": "Pacote removido"}


@router.post("/{package_id}/confirmar")
async def confirmar_recebimento(package_id: int, request: Request, db: Session = Depends(get_db)):
    """Marca o pedido como recebido/confirmado (status='confirmado').

    Decisão do fluxo: confirmação é SEM recibo — só um botão que muda o status.
    Somente pedidos confirmados entram no faturamento.
    """
    pkg = db.query(EpiPurchasePackage).filter(EpiPurchasePackage.id == package_id).first()
    if not pkg:
        return JSONResponse(status_code=404, content={"status": "error", "message": "Pacote não encontrado"})
    status_anterior = pkg.status or "rascunho"
    pkg.status = "confirmado"
    db.commit()
    db.refresh(pkg)

    audit(request, "pedido.confirmar", entidade="epi_purchase_packages",
          entidade_id=str(pkg.id),
          detalhe={"alteracoes": {"status": {"de": status_anterior, "para": "confirmado"}}},
          db=db)

    return {"status": "success", "message": "Recebimento confirmado", "data": {"id": pkg.id, "status": pkg.status}}


@router.post("/{package_id}/reabrir")
async def reabrir_pedido(package_id: int, request: Request, db: Session = Depends(get_db)):
    """Reabre um pedido confirmado, voltando o status para 'rascunho'
    (estado inicial). Pedidos reabertos saem do faturamento."""
    pkg = db.query(EpiPurchasePackage).filter(EpiPurchasePackage.id == package_id).first()
    if not pkg:
        return JSONResponse(status_code=404, content={"status": "error", "message": "Pacote não encontrado"})
    status_anterior = pkg.status or "rascunho"
    pkg.status = "rascunho"
    db.commit()
    db.refresh(pkg)

    audit(request, "pedido.reabrir", entidade="epi_purchase_packages",
          entidade_id=str(pkg.id),
          detalhe={"alteracoes": {"status": {"de": status_anterior, "para": "rascunho"}}},
          db=db)

    return {"status": "success", "message": "Pedido reaberto", "data": {"id": pkg.id, "status": pkg.status}}


@router.post("/{package_id}/documents")
async def upload_document(
    package_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    pkg = db.query(EpiPurchasePackage).filter(EpiPurchasePackage.id == package_id).first()
    if not pkg:
        return {"status": "error", "message": "Pacote nao encontrado"}

    ext = os.path.splitext(file.filename)[1] if file.filename else ".pdf"
    stored_name = f"{uuid.uuid4().hex}{ext}"
    filepath = os.path.join(EPI_UPLOAD_DIR, stored_name)

    with open(filepath, "wb") as f:
        content = await file.read()
        f.write(content)

    doc = EpiPurchaseDocument(
        package_id=package_id,
        original_filename=file.filename or "documento",
        stored_filename=stored_name,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    return {
        "status": "success",
        "data": {
            "id": doc.id,
            "original_filename": doc.original_filename,
            "uploaded_at": doc.uploaded_at.isoformat() if doc.uploaded_at else None,
        },
    }


@router.get("/{package_id}/documents/{doc_id}/download")
async def download_document(package_id: int, doc_id: int, db: Session = Depends(get_db)):
    doc = (
        db.query(EpiPurchaseDocument)
        .filter(EpiPurchaseDocument.id == doc_id, EpiPurchaseDocument.package_id == package_id)
        .first()
    )
    if not doc:
        return {"status": "error", "message": "Documento nao encontrado"}

    filepath = os.path.join(EPI_UPLOAD_DIR, doc.stored_filename)
    if not os.path.exists(filepath):
        return {"status": "error", "message": "Arquivo nao encontrado no servidor"}

    return FileResponse(
        filepath,
        filename=doc.original_filename,
        media_type="application/octet-stream",
    )


@router.delete("/{package_id}/documents/{doc_id}")
async def delete_document(package_id: int, doc_id: int, db: Session = Depends(get_db)):
    doc = (
        db.query(EpiPurchaseDocument)
        .filter(EpiPurchaseDocument.id == doc_id, EpiPurchaseDocument.package_id == package_id)
        .first()
    )
    if not doc:
        return {"status": "error", "message": "Documento nao encontrado"}

    filepath = os.path.join(EPI_UPLOAD_DIR, doc.stored_filename)
    if os.path.exists(filepath):
        os.remove(filepath)

    db.delete(doc)
    db.commit()
    return {"status": "success", "message": "Documento removido"}
