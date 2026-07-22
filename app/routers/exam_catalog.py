from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List

from app.db import get_db
from app.session_manager import validate_token
from app.routers.auth import get_token_from_request
from app.models.exam_catalog import ExamCatalog, PriceModel, PriceModelItem

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _require_user(request: Request, db: Session):
    token = get_token_from_request(request)
    user = validate_token(token, db) if token else None
    if not user:
        raise HTTPException(status_code=401, detail="Não autenticado")
    return user


# ------------------------- páginas -------------------------

@router.get("/catalogos", response_class=HTMLResponse)
async def catalogos_hub_page(request: Request, db: Session = Depends(get_db)):
    token = get_token_from_request(request)
    user = validate_token(token, db) if token else None
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse(
        "catalogos.html", {"request": request, "user": user, "token": token}
    )


@router.get("/catalogo-exames", response_class=HTMLResponse)
async def catalogo_exames_page(request: Request, db: Session = Depends(get_db)):
    token = get_token_from_request(request)
    user = validate_token(token, db) if token else None
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse(
        "catalogo_exames.html", {"request": request, "user": user, "token": token}
    )


# ------------------------- catálogo -------------------------

class CatalogUpdate(BaseModel):
    nome: Optional[str] = None
    sinonimos: Optional[List[str]] = None
    ativo: Optional[bool] = None


@router.get("/api/exam-catalog")
async def list_catalog(request: Request, db: Session = Depends(get_db)):
    _require_user(request, db)
    items = db.query(ExamCatalog).order_by(ExamCatalog.nome).all()
    return {"success": True, "data": [c.to_dict() for c in items]}


@router.put("/api/exam-catalog/{cat_id}")
async def update_catalog(cat_id: int, payload: CatalogUpdate, request: Request, db: Session = Depends(get_db)):
    _require_user(request, db)
    c = db.query(ExamCatalog).filter(ExamCatalog.id == cat_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Exame do catálogo não encontrado")
    data = payload.dict(exclude_unset=True)
    for k, v in data.items():
        setattr(c, k, v)
    db.commit()
    db.refresh(c)
    return {"success": True, "data": c.to_dict()}


# ------------------------- modelos de preço -------------------------

class PriceModelIn(BaseModel):
    nome: str
    descricao: Optional[str] = None
    ativo: bool = True


class PriceItemIn(BaseModel):
    exam_catalog_id: int
    preco: float


class PricesIn(BaseModel):
    prices: List[PriceItemIn]


@router.get("/api/price-models")
async def list_models(request: Request, db: Session = Depends(get_db)):
    _require_user(request, db)
    models = db.query(PriceModel).order_by(PriceModel.nome).all()
    return {"success": True, "data": [m.to_dict() for m in models]}


@router.get("/api/price-models/{model_id}")
async def get_model(model_id: int, request: Request, db: Session = Depends(get_db)):
    _require_user(request, db)
    m = db.query(PriceModel).filter(PriceModel.id == model_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Modelo não encontrado")
    return {"success": True, "data": m.to_dict(with_items=True)}


@router.post("/api/price-models")
async def create_model(payload: PriceModelIn, request: Request, db: Session = Depends(get_db)):
    _require_user(request, db)
    m = PriceModel(**payload.dict())
    db.add(m)
    db.commit()
    db.refresh(m)
    return {"success": True, "data": m.to_dict(with_items=True)}


@router.put("/api/price-models/{model_id}")
async def update_model(model_id: int, payload: PriceModelIn, request: Request, db: Session = Depends(get_db)):
    _require_user(request, db)
    m = db.query(PriceModel).filter(PriceModel.id == model_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Modelo não encontrado")
    for k, v in payload.dict().items():
        setattr(m, k, v)
    db.commit()
    db.refresh(m)
    return {"success": True, "data": m.to_dict()}


@router.delete("/api/price-models/{model_id}")
async def delete_model(model_id: int, request: Request, db: Session = Depends(get_db)):
    _require_user(request, db)
    m = db.query(PriceModel).filter(PriceModel.id == model_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Modelo não encontrado")
    db.delete(m)
    db.commit()
    return {"success": True, "message": "Modelo removido"}


@router.put("/api/price-models/{model_id}/prices")
async def set_prices(model_id: int, payload: PricesIn, request: Request, db: Session = Depends(get_db)):
    """Substitui os preços do modelo pelos itens informados (preço > 0)."""
    _require_user(request, db)
    m = db.query(PriceModel).filter(PriceModel.id == model_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Modelo não encontrado")
    valid_ids = {row[0] for row in db.query(ExamCatalog.id).all()}
    db.query(PriceModelItem).filter(PriceModelItem.price_model_id == model_id).delete(synchronize_session=False)
    for item in payload.prices:
        if item.exam_catalog_id in valid_ids and item.preco and item.preco > 0:
            db.add(PriceModelItem(price_model_id=model_id, exam_catalog_id=item.exam_catalog_id, preco=item.preco))
    db.commit()
    m = db.query(PriceModel).filter(PriceModel.id == model_id).first()
    return {"success": True, "data": m.to_dict(with_items=True)}
