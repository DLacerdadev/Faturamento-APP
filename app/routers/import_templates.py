from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

from app.db import get_db
from app.session_manager import validate_token
from app.routers.auth import get_token_from_request
from app.models.import_template import ImportTemplate
from app.services import import_engine
from app.services.audit import audit

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

ALLOWED_EXT = ("xlsx", "xls", "csv")


def _require_user(request: Request, db: Session):
    token = get_token_from_request(request)
    user = validate_token(token, db) if token else None
    if not user:
        raise HTTPException(status_code=401, detail="Não autenticado")
    return user, token


def _file_ext(filename: str) -> str:
    ext = (filename or "").split(".")[-1].lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(status_code=400, detail="Formato não suportado. Use Excel ou CSV.")
    return ext


class TemplateIn(BaseModel):
    nome: str
    categoria: str = "exames"
    descricao: Optional[str] = None
    ativo: bool = True
    sheet_mode: str = "index"
    sheet_index: int = 0
    sheet_name: Optional[str] = None
    header_row: int = 0
    layout: str = "long"
    match_key: str = "cpf"
    decimal_separator: str = ","
    date_formats: List[str] = []
    mapping: Dict[str, Any] = {}
    value_columns: List[Any] = []


# ------------------------- página -------------------------

@router.get("/import-templates", response_class=HTMLResponse)
async def import_templates_page(request: Request, db: Session = Depends(get_db)):
    token = get_token_from_request(request)
    user = validate_token(token, db) if token else None
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse(
        "import_templates.html",
        {"request": request, "user": user, "token": token},
    )


# ------------------------- CRUD -------------------------

@router.get("/api/import-templates")
async def list_templates(request: Request, categoria: Optional[str] = None, db: Session = Depends(get_db)):
    _require_user(request, db)
    q = db.query(ImportTemplate)
    if categoria:
        q = q.filter(ImportTemplate.categoria == categoria)
    items = q.order_by(ImportTemplate.nome).all()
    return {"success": True, "data": [t.to_dict() for t in items]}


@router.get("/api/import-templates/{template_id}")
async def get_template(template_id: int, request: Request, db: Session = Depends(get_db)):
    _require_user(request, db)
    t = db.query(ImportTemplate).filter(ImportTemplate.id == template_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Modelo não encontrado")
    return {"success": True, "data": t.to_dict()}


@router.post("/api/import-templates")
async def create_template(payload: TemplateIn, request: Request, db: Session = Depends(get_db)):
    user, _ = _require_user(request, db)
    t = ImportTemplate(**payload.dict())
    db.add(t)
    db.commit()
    db.refresh(t)
    audit(request, "modelo_importacao.criar", entidade="import_template", entidade_id=t.id,
          detalhe={"nome": t.nome, "categoria": t.categoria}, user=user)
    return {"success": True, "data": t.to_dict()}


@router.put("/api/import-templates/{template_id}")
async def update_template(template_id: int, payload: TemplateIn, request: Request, db: Session = Depends(get_db)):
    user, _ = _require_user(request, db)
    t = db.query(ImportTemplate).filter(ImportTemplate.id == template_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Modelo não encontrado")
    # Diff só dos campos que mudaram; campos estruturais (dict/list) viram
    # marcador "alterado" pra manter o JSON de auditoria pequeno.
    _campos_grandes = ("mapping", "value_columns", "date_formats")
    alteracoes = {}
    for key, value in payload.dict().items():
        antes = getattr(t, key, None)
        if antes != value:
            alteracoes[key] = "alterado" if key in _campos_grandes else {"de": antes, "para": value}
        setattr(t, key, value)
    db.commit()
    db.refresh(t)
    audit(request, "modelo_importacao.editar", entidade="import_template", entidade_id=template_id,
          detalhe={"alteracoes": alteracoes}, user=user)
    return {"success": True, "data": t.to_dict()}


@router.delete("/api/import-templates/{template_id}")
async def delete_template(template_id: int, request: Request, db: Session = Depends(get_db)):
    user, _ = _require_user(request, db)
    t = db.query(ImportTemplate).filter(ImportTemplate.id == template_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Modelo não encontrado")
    nome = t.nome
    db.delete(t)
    db.commit()
    audit(request, "modelo_importacao.excluir", entidade="import_template", entidade_id=template_id,
          detalhe={"nome": nome}, user=user)
    return {"success": True, "message": "Modelo removido"}


# ------------------------- detecção de colunas (apoio à UI) -------------------------

@router.post("/api/import-templates/detect-columns")
async def detect_columns(
    request: Request,
    file: UploadFile = File(...),
    sheet_mode: str = Form("index"),
    sheet_index: int = Form(0),
    sheet_name: Optional[str] = Form(None),
    header_row: int = Form(0),
    db: Session = Depends(get_db),
):
    _require_user(request, db)
    ext = _file_ext(file.filename)
    content = await file.read()
    try:
        info = import_engine.detect_structure(
            content, ext, sheet_mode=sheet_mode, sheet_index=sheet_index,
            sheet_name=sheet_name, header_row=header_row,
        )
        audit(request, "importacao.preview", entidade="importacao",
              detalhe={"etapa": "detect_columns", "arquivo": file.filename}, db=db)
        return {"success": True, **info}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Erro ao ler arquivo: {str(e)}")


# ------------------------- preview e importação -------------------------

def _template_from_form(payload_json: Optional[str], db: Session, template_id: Optional[int]) -> ImportTemplate:
    """Permite preview/import com um modelo salvo (template_id) OU com um modelo
    transitório enviado em JSON (para testar antes de salvar)."""
    import json
    if payload_json:
        data = json.loads(payload_json)
        return ImportTemplate(**{k: v for k, v in data.items() if k != "id"})
    if template_id is not None:
        t = db.query(ImportTemplate).filter(ImportTemplate.id == template_id).first()
        if not t:
            raise HTTPException(status_code=404, detail="Modelo não encontrado")
        return t
    raise HTTPException(status_code=400, detail="Informe template_id ou template (JSON)")


@router.post("/api/import-templates/preview")
async def preview_import(
    request: Request,
    file: UploadFile = File(...),
    template_id: Optional[int] = Form(None),
    template: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    _require_user(request, db)
    ext = _file_ext(file.filename)
    content = await file.read()
    tpl = _template_from_form(template, db, template_id)
    try:
        resultado = import_engine.preview(db, tpl, content, ext)
        audit(request, "importacao.preview", entidade="importacao",
              entidade_id=template_id,
              detalhe={"etapa": "preview", "arquivo": file.filename,
                       "template": getattr(tpl, "nome", None) or getattr(tpl, "name", None)}, db=db)
        return resultado
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Erro no preview: {str(e)}")


@router.post("/api/import-templates/import")
async def run_import(
    request: Request,
    file: UploadFile = File(...),
    template_id: int = Form(...),
    db: Session = Depends(get_db),
):
    user, _ = _require_user(request, db)
    ext = _file_ext(file.filename)
    content = await file.read()
    tpl = db.query(ImportTemplate).filter(ImportTemplate.id == template_id).first()
    if not tpl:
        raise HTTPException(status_code=404, detail="Modelo não encontrado")
    try:
        result = import_engine.import_exams(db, tpl, content, ext)
    except Exception as e:
        audit(request, "importacao.dados", entidade="importacao", entidade_id=tpl.id,
              detalhe={"template": tpl.nome, "arquivo": file.filename,
                       "erro": str(e)[:200]},
              user=user, status="erro")
        raise
    audit(request, "importacao.dados", entidade="importacao", entidade_id=tpl.id,
          detalhe={"template": tpl.nome, "arquivo": file.filename,
                   "n_registros": result.get("exam_records_created"),
                   "linhas_processadas": result.get("rows_processed")},
          user=user)
    return result
