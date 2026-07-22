from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from typing import Optional

from app.db import get_db
from app.session_manager import validate_token
from app.routers.auth import get_token_from_request
from app.services import exam_intake

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

ALLOWED_EXT = ("xlsx", "xls", "csv", "pdf")


def _require_user(request: Request, db: Session):
    token = get_token_from_request(request)
    user = validate_token(token, db) if token else None
    if not user:
        raise HTTPException(status_code=401, detail="Não autenticado")
    return user, token


def _ext(filename: str) -> str:
    ext = (filename or "").split(".")[-1].lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(status_code=400, detail="Formato não suportado. Use Excel, CSV ou PDF.")
    return ext


@router.get("/lancamento-exames", response_class=HTMLResponse)
async def lancamento_exames_page(request: Request, db: Session = Depends(get_db)):
    token = get_token_from_request(request)
    user = validate_token(token, db) if token else None
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse(
        "lancamento_exames.html",
        {"request": request, "user": user, "token": token},
    )


def _parse_cc_hints(raw: Optional[str]):
    if not raw:
        return None
    hints = [c.strip() for c in str(raw).split(",") if c.strip()]
    return hints or None


@router.post("/api/exames/preview")
async def preview_exames(request: Request, file: UploadFile = File(...),
                         price_model_id: Optional[int] = Form(None),
                         cc_hints: Optional[str] = Form(None), db: Session = Depends(get_db)):
    _require_user(request, db)
    ext = _ext(file.filename)
    content = await file.read()
    try:
        return exam_intake.preview_medical_exams(db, content, ext, file.filename,
                                                 price_model_id=price_model_id,
                                                 cc_hints=_parse_cc_hints(cc_hints))
    except exam_intake.IntakeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Erro ao ler arquivo: {str(e)}")


@router.post("/api/exames/importar")
async def importar_exames(request: Request, file: UploadFile = File(...),
                          price_model_id: Optional[int] = Form(None),
                          cc_hints: Optional[str] = Form(None), db: Session = Depends(get_db)):
    _require_user(request, db)
    ext = _ext(file.filename)
    content = await file.read()
    return exam_intake.import_to_medical_exams(db, content, ext, file.filename,
                                               price_model_id=price_model_id,
                                               cc_hints=_parse_cc_hints(cc_hints))


@router.get("/api/exames/directory-status")
async def directory_status(request: Request, db: Session = Depends(get_db)):
    _require_user(request, db)
    from app.services.senior_connector import employee_directory_status
    return employee_directory_status()


@router.post("/api/exames/refresh-directory")
async def refresh_directory(request: Request, db: Session = Depends(get_db)):
    """Dispara a varredura completa (folha do mês inteiro) e atualiza o diretório CPF→funcionário/CC."""
    _require_user(request, db)
    from app.services.senior_connector import fetch_employee_directory, employee_directory_status
    try:
        fetch_employee_directory(force=True)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Falha na varredura do Senior: {str(e)}")
    return employee_directory_status()
