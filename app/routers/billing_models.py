from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List

from app.db import get_db
from app.session_manager import validate_token
from app.routers.auth import get_token_from_request
from app.services.permissions import require_role
from app.services.audit import audit
from app.models.billing_model import BillingModel
from app.models.billing import Company
from app.services.excel_export import FEMSA_COLUMNS, GERAL_COLUMNS
from app.services.model_structure import parse_model_xlsx, derive_colunas, validate_estrutura

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _require_user(request: Request, db: Session):
    token = get_token_from_request(request)
    user = validate_token(token, db) if token else None
    if not user:
        raise HTTPException(status_code=401, detail="Não autenticado")
    return user


class BillingModelIn(BaseModel):
    nome: str
    descricao: Optional[str] = None
    ativo: bool = True
    colunas: List[str] = []
    # Percentuais PADRÃO do modelo (preenchem a exportação). None = sem padrão.
    encargos_pct: Optional[float] = None
    taxa_adm_pct: Optional[float] = None
    imposto_pct: Optional[float] = None
    # Fórmula do campo "Salário" (metodologia do cliente). None = salário-base.
    salario_formula: Optional[str] = None


class BillingModelUpdate(BaseModel):
    nome: Optional[str] = None
    descricao: Optional[str] = None
    ativo: Optional[bool] = None
    colunas: Optional[List[str]] = None
    # None explícito no payload LIMPA o padrão (exclude_unset distingue de "não enviado")
    encargos_pct: Optional[float] = None
    taxa_adm_pct: Optional[float] = None
    imposto_pct: Optional[float] = None
    salario_formula: Optional[str] = None
    # Grade "Fórmulas": [{campo, codigo?, codigo_nome?, formula?}] — None limpa tudo.
    campos_config: Optional[List[dict]] = None


def _validar_campos_config(linhas: Optional[List[dict]], colunas_modelo: List[str]) -> Optional[List[dict]]:
    """Valida a grade de fórmulas: campo precisa existir no modelo; codigo aceita
    int ou lista separada por vírgula ("257,259"); formula validada pelo avaliador
    seguro (número puro = valor fixo). Retorna a lista normalizada (ou None)."""
    from app.services.formula_salario import validar_formula
    if linhas is None:
        return None
    validas = []
    cols = set(colunas_modelo or [])
    for i, ln in enumerate(linhas, 1):
        campo = str(ln.get("campo") or "").strip()
        if not campo:
            raise HTTPException(status_code=400, detail=f"Fórmulas, linha {i}: informe o campo.")
        if cols and campo not in cols:
            raise HTTPException(status_code=400, detail=f"Fórmulas, linha {i}: campo '{campo}' não existe nas colunas do modelo.")
        codigo_raw = str(ln.get("codigo") or "").strip()
        codigos = []
        if codigo_raw:
            for parte in codigo_raw.split(","):
                parte = parte.strip()
                if not parte.isdigit():
                    raise HTTPException(status_code=400, detail=f"Fórmulas, linha {i}: código inválido '{parte}' (use números, separados por vírgula).")
                codigos.append(int(parte))
        formula = str(ln.get("formula") or "").strip() or None
        if formula:
            erro = validar_formula(formula)
            if erro:
                raise HTTPException(status_code=400, detail=f"Fórmulas, linha {i} ({campo}): {erro}")
        if not codigos and not formula:
            # linha sem código e sem fórmula = mapeamento padrão; não persiste
            continue
        validas.append({
            "campo": campo,
            "codigo": ",".join(str(c) for c in codigos) if codigos else None,
            "codigo_nome": str(ln.get("codigo_nome") or "").strip() or None,
            "formula": formula,
        })
    return validas or None


def _validar_salario_formula(expr: Optional[str]) -> Optional[str]:
    """Valida a fórmula de salário; retorna a expressão limpa (ou None p/ limpar)."""
    from app.services.formula_salario import validar_formula
    if expr is None or not str(expr).strip():
        return None
    erro = validar_formula(expr)
    if erro:
        raise HTTPException(status_code=400, detail=f"Fórmula do salário: {erro}")
    return str(expr).strip()


class CompanyModelIn(BaseModel):
    billing_model_id: Optional[int] = None  # None = volta ao padrão FEMSA


def _validar_pct(valor: Optional[float], campo: str) -> Optional[float]:
    """Valida percentual na faixa 0-100. None é aceito (limpa/sem padrão)."""
    if valor is None:
        return None
    if not (0 <= valor <= 100):
        raise HTTPException(status_code=400, detail=f"{campo} deve estar entre 0 e 100.")
    return valor


def _sanitize_colunas(colunas: List[str]) -> List[str]:
    """Mantém apenas colunas conhecidas (do GERAL), na ordem canônica do GERAL.
    Evita colunas inventadas/duplicadas e garante que a exportação as reconheça."""
    escolhidas = set(colunas or [])
    return [c for c in GERAL_COLUMNS if c in escolhidas]


@router.get("/modelos-faturamento", response_class=HTMLResponse)
async def modelos_faturamento_page(request: Request, db: Session = Depends(get_db)):
    token = get_token_from_request(request)
    user = validate_token(token, db) if token else None
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse(
        "modelos_faturamento.html",
        {"request": request, "user": user, "token": token},
    )


@router.get("/api/billing-models")
async def list_models(request: Request, db: Session = Depends(get_db)):
    _require_user(request, db)
    items = db.query(BillingModel).order_by(BillingModel.is_base.desc(), BillingModel.nome).all()
    return {"success": True, "data": [m.to_dict() for m in items]}


@router.get("/api/billing-models/columns")
async def all_columns(request: Request, db: Session = Depends(get_db)):
    """Lista completa de colunas conhecidas (base GERAL) para montar os checkboxes.
    'novas' são as 4 colunas de custo que não existem no FEMSA padrão."""
    _require_user(request, db)
    novas = [c for c in GERAL_COLUMNS if c not in FEMSA_COLUMNS]
    return {
        "success": True,
        "colunas": list(GERAL_COLUMNS),
        "femsa": list(FEMSA_COLUMNS),
        "novas": novas,
    }


@router.get("/api/billing-models/{model_id}")
async def get_model(model_id: int, request: Request, db: Session = Depends(get_db)):
    _require_user(request, db)
    m = db.query(BillingModel).filter(BillingModel.id == model_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Modelo não encontrado")
    return {"success": True, "data": m.to_dict(with_colunas=True)}


@router.post("/api/billing-models")
async def create_model(payload: BillingModelIn, request: Request, db: Session = Depends(get_db)):
    user = require_role(request, db, "gestor")
    nome = (payload.nome or "").strip()
    if not nome:
        raise HTTPException(status_code=400, detail="Informe o nome do modelo.")
    if db.query(BillingModel).filter(BillingModel.nome == nome).first():
        raise HTTPException(status_code=400, detail="Já existe um modelo com esse nome.")
    colunas = _sanitize_colunas(payload.colunas)
    if not colunas:
        raise HTTPException(status_code=400, detail="Selecione ao menos uma coluna.")
    m = BillingModel(
        nome=nome,
        descricao=payload.descricao,
        is_base=False,   # só o GERAL (seed) é base
        ativo=payload.ativo,
        colunas=colunas,
        encargos_pct=_validar_pct(payload.encargos_pct, "Encargos sociais (%)"),
        taxa_adm_pct=_validar_pct(payload.taxa_adm_pct, "Taxa administrativa (%)"),
        imposto_pct=_validar_pct(payload.imposto_pct, "Imposto (%)"),
        salario_formula=_validar_salario_formula(payload.salario_formula),
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    audit(request, "modelo.criar", entidade="billing_models", entidade_id=m.id,
          detalhe={"nome": m.nome, "ativo": m.ativo, "n_colunas": len(colunas)},
          user=user)
    return {"success": True, "data": m.to_dict(with_colunas=True)}


# ── Grade "Fórmulas" (aba na tela de modelos) ───────────────────────────────
# Campos preenchidos pelo CADASTRO do funcionário (não são eventos da folha).
_CAMPOS_CADASTRO = {
    "Empresa", "Mês Referência", "Nº Posicão", "CNPJ (Inserir CNPJ agência)",
    "CNPJ FEMSA", "Nome", "CPF", "Função", "Unidade - ", "Centro de Custo - Femsa",
    "Cargo - Femsa", "Motivo - Femsa", "Salário", "Dt Admissão", "Dt Demissão",
    "Término Ctr.", "Período afastamento", "Refeitório - SIM/NÃO", "Período Benefício",
}
# Campos CALCULADOS pelo sistema (totais, encargos, taxas, gross-up).
_CAMPOS_CALCULADOS = {
    "Total Remuneração", "Encargos Sociais", "SALÁRIO BRUTO ", "SALÁRIO LÍQUIDO",
    "Sub-Total", "Total Geral", "ENCARGOS (VALOR)", "ENCARGOS (%)",
    "TRIBUTOS (VALOR)", "TRIBUTOS (%)", "TAXA FATURAMENTO (VALOR)", "TAXA FATURAMENTO (%)",
    "TAXA CONTRATO (VALOR)", "TAXA CONTRATO (%)", "ENCARGOS DE FOLHA",
    "TAXA EXAMES MEDICOS(Valor)", "TAXA EXAMES MEDICOS(%)",
    "TAXA EXAMES MEDICOS COMPLEMENTARES (Valor)", "TAXA EXAMES MEDICOS COMPLEMENTARES (%)",
}
# Campos alimentados por outros BLOCOS do sistema (não eventos de folha).
_CAMPOS_BLOCOS = {
    "(FAT) EXAMES MEDICOS": "exames lançados (casa por CPF)",
    "EXAMES MEDICOS COMPLEMENTARES": "exames complementares",
    "UNIFORMES (Valor)": "pedidos de compra confirmados (uniformes)",
    "EPIS (Valor)": "pedidos de compra confirmados (EPIs)",
    "EQUIPAMENTOS (Valor)": "pedidos de compra confirmados (equipamentos)",
    "TREINAMENTOS (Valor)": "lançamentos de treinamento",
}


@router.get("/api/billing-models/{model_id}/campos")
async def get_model_campos(model_id: int, request: Request, db: Session = Depends(get_db)):
    """Grade "Fórmulas" do modelo: uma linha por campo, pré-preenchida com o
    mapeamento padrão de hoje (códigos de evento e origem) e com a configuração
    salva (codigo/formula) quando houver."""
    _require_user(request, db)
    m = db.query(BillingModel).filter(BillingModel.id == model_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Modelo não encontrado")

    from app.services.excel_export import EVENT_TO_FEMSA_MAPPING
    from app.models.benefit_event import BenefitEvent

    # Inverte o mapeamento fixo: coluna -> [códigos]; nomes vêm do cadastro de benefícios.
    por_coluna: dict = {}
    for cod, mapa in EVENT_TO_FEMSA_MAPPING.items():
        if not mapa:
            continue
        for col in mapa:
            if col:
                por_coluna.setdefault(col, []).append(cod)
    nomes_benef = {}
    for be in db.query(BenefitEvent).all():
        nomes_benef[be.codeve] = be.descricao
        if be.ativo and be.coluna_femsa:
            por_coluna.setdefault(be.coluna_femsa, []).append(be.codeve)

    cfg_por_campo = {c.get("campo"): c for c in (m.campos_config or [])}
    linhas = []
    for col in (m.colunas or []):
        cfg = cfg_por_campo.get(col, {})
        codigos_padrao = sorted(set(por_coluna.get(col, [])))
        if col == "Salário":
            origem = "cadastro do funcionário (salário-base)" + (f" — fórmula: {m.salario_formula}" if m.salario_formula else "")
        elif col in _CAMPOS_CADASTRO:
            origem = "cadastro do funcionário"
        elif col in _CAMPOS_CALCULADOS:
            origem = "calculado pelo sistema"
        elif col in _CAMPOS_BLOCOS:
            origem = _CAMPOS_BLOCOS[col]
        elif codigos_padrao:
            origem = "eventos da folha"
        else:
            origem = "sem origem (vazio)"
        linhas.append({
            "campo": col,
            "origem_padrao": origem,
            "codigos_padrao": codigos_padrao,
            "codigos_padrao_nomes": [nomes_benef.get(c) for c in codigos_padrao],
            "calculado": col in _CAMPOS_CALCULADOS,
            # Config salva (override do gestor) — vazio = usa o padrão acima.
            "codigo": cfg.get("codigo"),
            "codigo_nome": cfg.get("codigo_nome"),
            "formula": cfg.get("formula"),
        })
    return {"success": True, "data": linhas}


# ── Modelos por UPLOAD de planilha ─────────────────────────────────────────
# Limite de tamanho do .xlsx enviado (10 MB).
_MAX_UPLOAD_BYTES = 10 * 1024 * 1024


async def _ler_xlsx_upload(arquivo: UploadFile) -> bytes:
    """Valida extensão/tamanho do upload e devolve os bytes do .xlsx."""
    nome = (arquivo.filename or "").strip()
    if not nome.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Envie um arquivo Excel no formato .xlsx.")
    conteudo = await arquivo.read()
    if not conteudo:
        raise HTTPException(status_code=400, detail="O arquivo enviado está vazio.")
    if len(conteudo) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="Arquivo muito grande: o limite é 10 MB.")
    return conteudo


def _parse_upload(conteudo: bytes, nome_arquivo: str) -> dict:
    """Chama o parser da planilha-modelo traduzindo erro em HTTP 400 (PT-BR)."""
    try:
        return parse_model_xlsx(conteudo, nome_arquivo)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Não foi possível ler a planilha. Verifique se o arquivo é um .xlsx válido.",
        )


@router.post("/api/billing-models/upload-preview")
async def upload_preview(
    request: Request,
    arquivo: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Analisa a planilha-modelo SEM salvar nada: retorna a estrutura detectada,
    as colunas (fontes tipo 'campo') e a lista de problemas de validação, para
    a tela de conferência antes de confirmar a criação do modelo."""
    user = require_role(request, db, "gestor")
    conteudo = await _ler_xlsx_upload(arquivo)
    estrutura = _parse_upload(conteudo, arquivo.filename or "")
    problemas = validate_estrutura(estrutura)
    colunas = derive_colunas(estrutura)
    audit(request, "modelo.upload_preview", entidade="billing_models",
          detalhe={"arquivo": arquivo.filename, "n_colunas": len(colunas),
                   "n_problemas": len(problemas)}, user=user)
    return {
        "success": True,
        "estrutura": estrutura,
        "colunas": colunas,
        "problemas": problemas,
    }


@router.post("/api/billing-models/upload")
async def upload_model(
    request: Request,
    arquivo: UploadFile = File(...),
    nome: str = Form(...),
    descricao: Optional[str] = Form(None),
    encargos_pct: Optional[float] = Form(None),
    taxa_adm_pct: Optional[float] = Form(None),
    imposto_pct: Optional[float] = Form(None),
    db: Session = Depends(get_db),
):
    """Cria um BillingModel a partir da planilha enviada: parseia de novo os
    bytes (não confia no preview do cliente), recusa se a validação acusar
    problemas e salva com colunas derivadas + estrutura + arquivo de origem."""
    user = require_role(request, db, "gestor")
    nome = (nome or "").strip()
    if not nome:
        raise HTTPException(status_code=400, detail="Informe o nome do modelo.")
    if db.query(BillingModel).filter(BillingModel.nome == nome).first():
        raise HTTPException(status_code=400, detail="Já existe um modelo com esse nome.")

    conteudo = await _ler_xlsx_upload(arquivo)
    estrutura = _parse_upload(conteudo, arquivo.filename or "")
    problemas = validate_estrutura(estrutura)
    if problemas:
        raise HTTPException(
            status_code=400,
            detail="A planilha não pôde ser usada como modelo:\n- " + "\n- ".join(problemas),
        )

    # Template SEM PII (opção 2): preserva logo/bordas/formatação na exportação.
    try:
        from app.services.model_structure import gerar_template_sem_pii
        template_bytes = gerar_template_sem_pii(conteudo, estrutura)
    except Exception:
        template_bytes = None

    m = BillingModel(
        nome=nome,
        descricao=(descricao or "").strip() or None,
        is_base=False,
        ativo=True,
        colunas=derive_colunas(estrutura),
        estrutura=estrutura,
        arquivo_origem=(arquivo.filename or "").strip() or None,
        arquivo_template=template_bytes,
        encargos_pct=_validar_pct(encargos_pct, "Encargos sociais (%)"),
        taxa_adm_pct=_validar_pct(taxa_adm_pct, "Taxa administrativa (%)"),
        imposto_pct=_validar_pct(imposto_pct, "Imposto (%)"),
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    audit(request, "modelo.upload", entidade="billing_models", entidade_id=m.id,
          detalhe={"arquivo": m.arquivo_origem, "n_colunas": len(m.colunas or [])},
          user=user)
    return {"success": True, "data": m.to_dict(with_colunas=True)}


@router.put("/api/billing-models/{model_id}")
async def update_model(model_id: int, payload: BillingModelUpdate, request: Request, db: Session = Depends(get_db)):
    user = require_role(request, db, "gestor")
    m = db.query(BillingModel).filter(BillingModel.id == model_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Modelo não encontrado")
    # Snapshot pré-edição para a trilha de auditoria (só campos que mudarem).
    antes = {
        "nome": m.nome,
        "ativo": m.ativo,
        "encargos_pct": m.encargos_pct,
        "taxa_adm_pct": m.taxa_adm_pct,
        "imposto_pct": m.imposto_pct,
        "salario_formula": m.salario_formula,
        "n_colunas": len(m.colunas or []),
    }
    data = payload.dict(exclude_unset=True)
    if "nome" in data:
        nome = (data["nome"] or "").strip()
        if not nome:
            raise HTTPException(status_code=400, detail="Informe o nome do modelo.")
        existe = db.query(BillingModel).filter(BillingModel.nome == nome, BillingModel.id != model_id).first()
        if existe:
            raise HTTPException(status_code=400, detail="Já existe um modelo com esse nome.")
        m.nome = nome
    if "descricao" in data:
        m.descricao = data["descricao"]
    if "ativo" in data:
        m.ativo = data["ativo"]
    if "colunas" in data:
        colunas = _sanitize_colunas(data["colunas"])
        if not colunas:
            raise HTTPException(status_code=400, detail="Selecione ao menos uma coluna.")
        m.colunas = colunas
    # Percentuais padrão: null explícito limpa; ausente não altera (exclude_unset).
    if "encargos_pct" in data:
        m.encargos_pct = _validar_pct(data["encargos_pct"], "Encargos sociais (%)")
    if "taxa_adm_pct" in data:
        m.taxa_adm_pct = _validar_pct(data["taxa_adm_pct"], "Taxa administrativa (%)")
    if "imposto_pct" in data:
        m.imposto_pct = _validar_pct(data["imposto_pct"], "Imposto (%)")
    # Fórmula do salário: null explícito limpa; ausente não altera; inválida = 400.
    if "salario_formula" in data:
        m.salario_formula = _validar_salario_formula(data["salario_formula"])
    # Grade "Fórmulas" por campo: null limpa; ausente não altera; linha inválida = 400.
    if "campos_config" in data:
        m.campos_config = _validar_campos_config(data["campos_config"], m.colunas or [])
    db.commit()
    db.refresh(m)
    depois = {
        "nome": m.nome,
        "ativo": m.ativo,
        "encargos_pct": m.encargos_pct,
        "taxa_adm_pct": m.taxa_adm_pct,
        "imposto_pct": m.imposto_pct,
        "salario_formula": m.salario_formula,
        "n_colunas": len(m.colunas or []),
    }
    alteracoes = {
        campo: {"de": antes[campo], "para": depois[campo]}
        for campo in antes
        if antes[campo] != depois[campo]
    }
    audit(request, "modelo.editar", entidade="billing_models", entidade_id=m.id,
          detalhe={"alteracoes": alteracoes}, user=user)
    return {"success": True, "data": m.to_dict(with_colunas=True)}


@router.get("/api/billing-companies")
async def list_companies(request: Request, db: Session = Depends(get_db)):
    """Contratos (Company) com o modelo de faturamento associado (para o de-para na tela)."""
    _require_user(request, db)
    companies = db.query(Company).order_by(Company.name).all()
    return {
        "success": True,
        "data": [
            {
                "id": c.id,
                "name": c.name,
                "cnpj_femsa": c.cnpj_femsa,
                "billing_model_id": c.billing_model_id,
            }
            for c in companies
        ],
    }


@router.post("/api/billing-companies/{company_id}/model")
async def set_company_model(company_id: int, payload: CompanyModelIn, request: Request, db: Session = Depends(get_db)):
    """Associa (ou desassocia, com None) um modelo de faturamento ao contrato."""
    user = require_role(request, db, "gestor")
    c = db.query(Company).filter(Company.id == company_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Contrato não encontrado")
    mid = payload.billing_model_id
    if mid is not None:
        if not db.query(BillingModel).filter(BillingModel.id == mid).first():
            raise HTTPException(status_code=404, detail="Modelo não encontrado")
    c.billing_model_id = mid
    db.commit()
    audit(request, "modelo.associar_contrato", entidade="billing_models",
          entidade_id=mid, detalhe={"billing_model_id": mid, "company_id": c.id},
          user=user)
    return {"success": True, "company_id": c.id, "billing_model_id": c.billing_model_id}
