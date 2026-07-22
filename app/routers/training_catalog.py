from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import or_, func
from pydantic import BaseModel
from typing import Optional

from app.db import get_db
from app.session_manager import validate_token
from app.routers.auth import get_token_from_request
from app.models.training_catalog import TrainingCatalog

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _require_user(request: Request, db: Session):
    token = get_token_from_request(request)
    user = validate_token(token, db) if token else None
    if not user:
        raise HTTPException(status_code=401, detail="Não autenticado")
    return user


# ------------------------- página -------------------------

@router.get("/catalogo-treinamentos", response_class=HTMLResponse)
async def catalogo_treinamentos_page(request: Request, db: Session = Depends(get_db)):
    token = get_token_from_request(request)
    user = validate_token(token, db) if token else None
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse(
        "catalogo_treinamentos.html", {"request": request, "user": user, "token": token}
    )


# ------------------------- API -------------------------

class TrainingIn(BaseModel):
    nome: str
    valor: Optional[float] = None
    carga_horaria: Optional[float] = None
    validade_meses: Optional[int] = None
    ativo: bool = True
    observacao: Optional[str] = None


class TrainingUpdate(BaseModel):
    nome: Optional[str] = None
    valor: Optional[float] = None
    carga_horaria: Optional[float] = None
    validade_meses: Optional[int] = None
    ativo: Optional[bool] = None
    observacao: Optional[str] = None


@router.get("/api/trainings")
async def list_trainings(request: Request, q: Optional[str] = None,
                         ativo: Optional[bool] = None, db: Session = Depends(get_db)):
    _require_user(request, db)
    query = db.query(TrainingCatalog)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(TrainingCatalog.nome.ilike(like),
                                 TrainingCatalog.observacao.ilike(like)))
    if ativo is not None:
        query = query.filter(TrainingCatalog.ativo.is_(ativo))
    items = query.order_by(TrainingCatalog.nome).all()
    total = db.query(func.count(TrainingCatalog.id)).scalar() or 0
    ativos = db.query(func.count(TrainingCatalog.id)).filter(TrainingCatalog.ativo.is_(True)).scalar() or 0
    com_valor = db.query(func.count(TrainingCatalog.id)).filter(
        TrainingCatalog.valor.isnot(None), TrainingCatalog.valor > 0
    ).scalar() or 0
    return {
        "success": True,
        "resumo": {"total": total, "ativos": ativos, "com_valor": com_valor},
        "data": [t.to_dict() for t in items],
    }


@router.post("/api/trainings")
async def create_training(payload: TrainingIn, request: Request, db: Session = Depends(get_db)):
    _require_user(request, db)
    nome = (payload.nome or "").strip()
    if not nome:
        raise HTTPException(status_code=400, detail="Informe o nome do treinamento.")
    t = TrainingCatalog(
        nome=nome, valor=payload.valor, carga_horaria=payload.carga_horaria,
        validade_meses=payload.validade_meses, ativo=payload.ativo,
        observacao=(payload.observacao or None),
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return {"success": True, "data": t.to_dict()}


@router.put("/api/trainings/{train_id}")
async def update_training(train_id: int, payload: TrainingUpdate, request: Request, db: Session = Depends(get_db)):
    _require_user(request, db)
    t = db.query(TrainingCatalog).filter(TrainingCatalog.id == train_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Treinamento não encontrado")
    data = payload.dict(exclude_unset=True)
    if "nome" in data and not (data["nome"] or "").strip():
        raise HTTPException(status_code=400, detail="O nome não pode ficar vazio.")
    for k, v in data.items():
        setattr(t, k, v)
    db.commit()
    db.refresh(t)
    return {"success": True, "data": t.to_dict()}


@router.delete("/api/trainings/{train_id}")
async def delete_training(train_id: int, request: Request, db: Session = Depends(get_db)):
    _require_user(request, db)
    t = db.query(TrainingCatalog).filter(TrainingCatalog.id == train_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Treinamento não encontrado")
    db.delete(t)
    db.commit()
    return {"success": True, "message": "Treinamento removido"}
