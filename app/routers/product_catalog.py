from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import or_
from pydantic import BaseModel
from typing import Optional

from app.db import get_db
from app.session_manager import validate_token
from app.routers.auth import get_token_from_request
from app.models.product_catalog import ProductCatalog
from app.services.product_import import import_produtos_totvs
from app.services.audit import audit

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _require_user(request: Request, db: Session):
    token = get_token_from_request(request)
    user = validate_token(token, db) if token else None
    if not user:
        raise HTTPException(status_code=401, detail="Não autenticado")
    return user


class ProductUpdate(BaseModel):
    descricao: Optional[str] = None
    categoria: Optional[str] = None
    preco: Optional[float] = None
    ativo: Optional[bool] = None


@router.get("/catalogo-produtos", response_class=HTMLResponse)
async def produtos_page(request: Request, db: Session = Depends(get_db)):
    token = get_token_from_request(request)
    user = validate_token(token, db) if token else None
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("catalogo_produtos.html", {"request": request, "user": user, "token": token})


@router.get("/api/products")
async def list_products(request: Request, categoria: Optional[str] = None, q: Optional[str] = None,
                        sem_preco: bool = False, page: int = 1, per_page: int = 50,
                        db: Session = Depends(get_db)):
    _require_user(request, db)
    query = db.query(ProductCatalog)
    if categoria:
        query = query.filter(ProductCatalog.categoria == categoria)
    if sem_preco:
        query = query.filter(or_(ProductCatalog.preco.is_(None), ProductCatalog.preco == 0))
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(ProductCatalog.descricao.ilike(like), ProductCatalog.codigo.ilike(like)))
    total = query.count()
    per_page = max(1, min(per_page, 200))
    items = (query.order_by(ProductCatalog.categoria, ProductCatalog.descricao)
             .offset((page - 1) * per_page).limit(per_page).all())
    # resumo por categoria (do catálogo todo)
    from sqlalchemy import func
    resumo = dict(db.query(ProductCatalog.categoria, func.count()).group_by(ProductCatalog.categoria).all())
    return {
        "success": True, "total": total, "page": page, "per_page": per_page,
        "total_pages": (total + per_page - 1) // per_page,
        "resumo": resumo,
        "data": [p.to_dict() for p in items],
    }


@router.put("/api/products/{prod_id}")
async def update_product(prod_id: int, payload: ProductUpdate, request: Request, db: Session = Depends(get_db)):
    user = _require_user(request, db)
    p = db.query(ProductCatalog).filter(ProductCatalog.id == prod_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Produto não encontrado")
    campos = payload.dict(exclude_unset=True)
    antes = {k: getattr(p, k) for k in campos}
    for k, v in campos.items():
        setattr(p, k, v)
    db.commit()
    db.refresh(p)
    # Propaga para o catálogo de EPIs (preço/nome/status) quando for EPI
    if p.categoria == "epi":
        from app.services.product_import import sync_one_epi
        sync_one_epi(db, p)

    alteracoes = {
        k: {"de": antes[k], "para": getattr(p, k)}
        for k in campos if antes[k] != getattr(p, k)
    }
    if "preco" in alteracoes:
        audit(request, "catalogo_produto.preco", entidade="product_catalog",
              entidade_id=str(p.id),
              detalhe={"codigo": p.codigo,
                       "de": alteracoes["preco"]["de"],
                       "para": alteracoes["preco"]["para"]},
              user=user)
    outras = {k: v for k, v in alteracoes.items() if k != "preco"}
    if outras:
        audit(request, "catalogo_produto.editar", entidade="product_catalog",
              entidade_id=str(p.id),
              detalhe={"codigo": p.codigo, "alteracoes": outras},
              user=user)

    return {"success": True, "data": p.to_dict()}


@router.post("/api/products/import")
async def import_products(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)):
    user = _require_user(request, db)
    ext = (file.filename or "").split(".")[-1].lower()
    if ext not in ("xlsx", "xls"):
        raise HTTPException(status_code=400, detail="Envie o arquivo Excel do cadastro de produtos do TOTVS.")
    content = await file.read()
    try:
        result = import_produtos_totvs(db, content)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Erro ao importar: {str(e)}")

    # Só valores escalares pequenos do resumo (contagens/mensagens) no detalhe
    resumo = {k: v for k, v in result.items()
              if isinstance(v, (int, float, str, bool))} if isinstance(result, dict) else {}
    resumo["arquivo"] = file.filename
    audit(request, "catalogo_produto.importar", entidade="product_catalog",
          detalhe=resumo, user=user)

    return result
