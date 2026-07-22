from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from app.db import get_db
from app.session_manager import validate_token
from app.routers.auth import get_token_from_request
from app.models.benefit_event import BenefitEvent

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

# Colunas de benefício do faturamento FEMSA (sugestões para o de-para).
FEMSA_BENEFIT_COLUMNS = [
    "PAGTO. VALE REFEICAO (Valor)",
    "PAGTO. VALE-TRANSPORTE (Valor)",
    "VALE TRANSPORTE NAO UTILIZADO",
    "REEMB. VALE REFEICAO INDEVIDO/DEVOLVIDO",
    "REEMB. DESPESAS KM/ESTAC/PEDAGIO",
    "AJUDA CUSTO COMBUSTÍVEL / KM",
    "PREMIO/BONUS",
    "SEGURO DE VIDA",
]


def _require_user(request: Request, db: Session):
    token = get_token_from_request(request)
    user = validate_token(token, db) if token else None
    if not user:
        raise HTTPException(status_code=401, detail="Não autenticado")
    return user


class BenefitEventIn(BaseModel):
    codeve: int
    descricao: Optional[str] = None
    coluna_femsa: str
    grupo: Optional[str] = None
    ativo: bool = False
    observacao: Optional[str] = None


@router.get("/beneficios", response_class=HTMLResponse)
async def beneficios_page(request: Request, db: Session = Depends(get_db)):
    token = get_token_from_request(request)
    user = validate_token(token, db) if token else None
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse(
        "beneficios.html",
        {"request": request, "user": user, "token": token, "colunas": FEMSA_BENEFIT_COLUMNS},
    )


@router.get("/api/benefit-events")
async def list_events(request: Request, db: Session = Depends(get_db)):
    _require_user(request, db)
    items = db.query(BenefitEvent).order_by(BenefitEvent.grupo, BenefitEvent.codeve).all()
    return {"success": True, "data": [e.to_dict() for e in items], "colunas": FEMSA_BENEFIT_COLUMNS}


@router.post("/api/benefit-events")
async def create_event(payload: BenefitEventIn, request: Request, db: Session = Depends(get_db)):
    _require_user(request, db)
    if db.query(BenefitEvent).filter(BenefitEvent.codeve == payload.codeve).first():
        raise HTTPException(status_code=400, detail="Já existe um evento com esse código (CODEVE).")
    e = BenefitEvent(**payload.dict())
    db.add(e)
    db.commit()
    db.refresh(e)
    return {"success": True, "data": e.to_dict()}


@router.put("/api/benefit-events/{event_id}")
async def update_event(event_id: int, payload: BenefitEventIn, request: Request, db: Session = Depends(get_db)):
    _require_user(request, db)
    e = db.query(BenefitEvent).filter(BenefitEvent.id == event_id).first()
    if not e:
        raise HTTPException(status_code=404, detail="Evento não encontrado")
    for k, v in payload.dict().items():
        setattr(e, k, v)
    db.commit()
    db.refresh(e)
    return {"success": True, "data": e.to_dict()}


@router.patch("/api/benefit-events/{event_id}/toggle")
async def toggle_event(event_id: int, request: Request, db: Session = Depends(get_db)):
    _require_user(request, db)
    e = db.query(BenefitEvent).filter(BenefitEvent.id == event_id).first()
    if not e:
        raise HTTPException(status_code=404, detail="Evento não encontrado")
    e.ativo = not bool(e.ativo)
    db.commit()
    db.refresh(e)
    return {"success": True, "data": e.to_dict()}


@router.delete("/api/benefit-events/{event_id}")
async def delete_event(event_id: int, request: Request, db: Session = Depends(get_db)):
    _require_user(request, db)
    e = db.query(BenefitEvent).filter(BenefitEvent.id == event_id).first()
    if not e:
        raise HTTPException(status_code=404, detail="Evento não encontrado")
    db.delete(e)
    db.commit()
    return {"success": True, "message": "Evento removido"}
