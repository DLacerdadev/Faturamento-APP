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
from app.routers.auth import get_current_user
from app.config import EPI_PURCHASE_EMAIL, is_smtp_configured, GENERATED_REPORTS_DIR

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/epi-purchases", tags=["epi_purchases"])

EPI_UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "epi_documents")
os.makedirs(EPI_UPLOAD_DIR, exist_ok=True)


class EmployeeSelection(BaseModel):
    numcad: int
    nome: str


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


class EpiPackageCreateV2(BaseModel):
    empresa: str = "FEMSA"
    mes_ano: str
    codccu: str = Field(..., min_length=1)
    observacao: Optional[str] = None
    employees: List[EmployeeSelection] = Field(..., min_length=1)
    items: List[EpiItemInput] = Field(..., min_length=1)


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
    `resolved_items` vem de `_resolve_catalog_items`.
    Cada linha replica `quantidade_total_item` (override do usuário, ou
    `qpf × n_funcionários` se não houver override).
    """
    n_emps = len(payload.employees)
    for emp in payload.employees:
        for item in resolved_items:
            qtd_total = item.get("quantidade_total_override") or (item["qpf"] * n_emps)
            pkg.items.append(EpiPurchaseItem(
                descricao=item["epi_nome"],
                quantidade=item["qpf"],
                valor_unitario=item["valor_unitario"],
                valor_total=item["qpf"] * item["valor_unitario"],
                employee_numcad=emp.numcad,
                employee_nome=emp.nome,
                epi_id=item["epi_id"],
                tamanho=item["tamanho"],
                quantidade_por_funcionario=item["qpf"],
                quantidade_total_item=qtd_total,
                valor_unitario_catalogo=item["valor_unitario_catalogo"],
            ))


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

    has_catalog_items = any(getattr(it, "epi_id", None) for it in (pkg.items or []))
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
    ainda está ativo no CCU. Retorna lista de inativos (vazia = tudo OK).
    """
    try:
        active = fetch_active_employees(payload.codccu)
    except Exception:
        # Se a consulta Senior falhar, não bloqueia o save — apenas pula a revalidação.
        # A alternativa (bloquear sempre) seria pior em incidente do Senior.
        return []
    active_numcads = {e.get("numcad") for e in active}
    inactive = []
    for emp in payload.employees:
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
            "epi_id": item.epi_id,
            "tamanho": item.tamanho,
            "quantidade_por_funcionario": item.quantidade_por_funcionario,
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

    # Agrupado v2 (feature 002): por epi_id + tamanho + qtde_por_func + valor_unitario,
    # com totais por item e flag de override de valor vs catálogo.
    is_legacy_pkg = bool(items_list) and all(it.epi_id is None for it in items_list)
    itens_v2_buckets: dict = {}
    for it in items_list:
        if it.epi_id is None:
            continue
        qpf = it.quantidade_por_funcionario if it.quantidade_por_funcionario is not None else it.quantidade
        key = (it.epi_id, it.tamanho or "", qpf, round(it.valor_unitario or 0.0, 6))
        bucket = itens_v2_buckets.setdefault(key, {
            "epi_id": it.epi_id,
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

    solicitacao_block = {
        "filename": pkg.solicitacao_filename,
        "generated_at": pkg.solicitacao_generated_at.isoformat() if pkg.solicitacao_generated_at else None,
        "available_for_download": bool(pkg.solicitacao_filename) and not is_legacy_pkg,
        "download_url": f"/api/epi-purchases/{pkg.id}/solicitacao" if pkg.solicitacao_filename else None,
    }

    return {
        "id": pkg.id,
        "empresa": pkg.empresa,
        "mes_ano": pkg.mes_ano.isoformat() if pkg.mes_ano else None,
        "codccu": pkg.codccu,
        "observacao": pkg.observacao,
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

    # Resolve itens contra o catálogo
    resolved, err = _resolve_catalog_items(db, data.items)
    if err:
        return JSONResponse(status_code=400, content={"status": "error", "message": err})

    # Revalida funcionários ativos
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
        solicitante_nome=_resolve_solicitante(request, db),
    )
    _expand_cartesian(pkg, data, resolved)
    _recompute_totals(pkg)

    db.add(pkg)
    db.commit()
    db.refresh(pkg)

    # Gera Excel da solicitação e atualiza pacote
    try:
        _generate_solicitacao_for(pkg, db)
        db.commit()
        db.refresh(pkg)
    except Exception as e:
        logger.error("Falha ao gerar Excel da solicitação para pkg %s: %s", pkg.id, e)

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

    # Permite EPIs já vinculados ao pacote mesmo se desativados (edição de pacote existente)
    existing_epi_ids = {it.epi_id for it in (pkg.items or []) if it.epi_id is not None}
    resolved, err = _resolve_catalog_items(db, data.items, allow_inactive_ids=existing_epi_ids)
    if err:
        return JSONResponse(status_code=400, content={"status": "error", "message": err})

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
    pkg.solicitante_nome = _resolve_solicitante(request, db)

    for old_item in list(pkg.items):
        db.delete(old_item)
    db.flush()

    _expand_cartesian(pkg, data, resolved)
    _recompute_totals(pkg)

    db.commit()
    db.refresh(pkg)

    try:
        _generate_solicitacao_for(pkg, db)
        db.commit()
        db.refresh(pkg)
    except Exception as e:
        logger.error("Falha ao gerar Excel da solicitação (PUT) para pkg %s: %s", pkg.id, e)

    return {"status": "success", "data": package_to_dict(pkg)}


@router.get("/{package_id}/solicitacao")
async def download_solicitacao(package_id: int, db: Session = Depends(get_db)):
    """Download do Excel da solicitação de compra (feature 002)."""
    pkg = db.query(EpiPurchasePackage).filter(EpiPurchasePackage.id == package_id).first()
    if not pkg:
        return JSONResponse(status_code=404, content={"status": "error", "message": "Pacote não encontrado"})
    if not pkg.solicitacao_filename:
        return JSONResponse(
            status_code=404,
            content={"status": "error", "message": "Pacote sem solicitação gerada (legado ou ainda não salvo no novo fluxo)."},
        )
    filepath = GENERATED_REPORTS_DIR / pkg.solicitacao_filename
    if not filepath.exists():
        return JSONResponse(
            status_code=410,
            content={"status": "error", "message": "Arquivo da solicitação não está mais disponível. Re-salve o pacote para regenerar."},
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
async def delete_package(package_id: int, db: Session = Depends(get_db)):
    pkg = (
        db.query(EpiPurchasePackage)
        .options(joinedload(EpiPurchasePackage.documents))
        .filter(EpiPurchasePackage.id == package_id)
        .first()
    )
    if not pkg:
        return {"status": "error", "message": "Pacote nao encontrado"}

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
    return {"status": "success", "message": "Pacote removido"}


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
