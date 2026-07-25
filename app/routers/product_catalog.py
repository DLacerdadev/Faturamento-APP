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
from app.models.cc_item_price import CCItemPrice
from app.services.product_import import import_produtos_totvs
from app.services.audit import audit
from app.services.permissions import require_role

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


class CCPriceUpdate(BaseModel):
    """Edição manual do valor de EPI de um CENTRO DE CUSTO específico.
    Trava aquela linha (is_manual_price=True) — não propaga p/ outros CCUs."""
    codccu: str
    produto_codigo: str
    valor: float


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


# ---------------------------------------------------------------------------
# Feature 008 — EPI automático via TOTVS, valor POR CENTRO DE CUSTO.
# O valor de EPI vive em CCItemPrice (codccu, produto_codigo). A rotina diária
# faz upsert automático; a trava manual é por linha (CCU × produto) — editar um
# CCU não afeta os outros. Endpoints: listar preços por CCU, editar (trava),
# voltar ao automático (destrava + ressincroniza), sincronizar agora (gestor+).
# ---------------------------------------------------------------------------

def _cc_price_row(db: Session, codccu: str, produto_codigo: str):
    return (
        db.query(CCItemPrice)
        .filter(
            CCItemPrice.codccu == str(codccu),
            CCItemPrice.produto_codigo == str(produto_codigo),
            CCItemPrice.tamanho == "",
        )
        .first()
    )


@router.get("/api/products/{prod_id}/cc-prices")
async def list_cc_prices(prod_id: int, request: Request, db: Session = Depends(get_db)):
    """Lista os preços por centro de custo (CCItemPrice) de um produto EPI —
    com o estado manual/automático e a data da última sincronização."""
    _require_user(request, db)
    p = db.query(ProductCatalog).filter(ProductCatalog.id == prod_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Produto não encontrado")
    rows = (
        db.query(CCItemPrice)
        .filter(CCItemPrice.produto_codigo == p.codigo, CCItemPrice.tamanho == "")
        .order_by(CCItemPrice.codccu)
        .all()
    )
    return {"success": True, "produto": p.to_dict(), "data": [r.to_dict() for r in rows]}


@router.put("/api/products/cc-price")
async def update_cc_price(payload: CCPriceUpdate, request: Request, db: Session = Depends(get_db)):
    """Edita o valor de EPI de UM centro de custo → marca SÓ aquela linha como
    manual (trava). Nunca propaga para outros CCUs (isolamento)."""
    user = _require_user(request, db)
    codccu = (payload.codccu or "").strip()
    produto = (payload.produto_codigo or "").strip()
    if not codccu or not produto:
        raise HTTPException(status_code=400, detail="Informe centro de custo e produto.")

    row = _cc_price_row(db, codccu, produto)
    antes_valor = float(row.valor) if row else None
    if row is None:
        row = CCItemPrice(codccu=codccu, produto_codigo=produto, tamanho="",
                          valor=float(payload.valor), is_manual_price=True)
        db.add(row)
    else:
        row.valor = float(payload.valor)
        row.is_manual_price = True  # trava SÓ nesta linha (CCU × produto)
    db.commit()
    db.refresh(row)

    audit(request, "catalogo_produto.cc_preco_manual", entidade="cc_item_price",
          entidade_id=str(row.id),
          detalhe={"codccu": codccu, "produto_codigo": produto,
                   "de": antes_valor, "para": float(row.valor), "manual": True},
          user=user)
    return {"success": True, "data": row.to_dict()}


@router.post("/api/products/cc-price/voltar-automatico")
async def revert_cc_price_to_auto(payload: CCPriceUpdate, request: Request, db: Session = Depends(get_db)):
    """"Voltar ao automático" para UM centro de custo: remove a trava manual e
    ressincroniza aquela linha do TOTVS. Não afeta outros CCUs.

    `valor` do payload é ignorado aqui (reusa o schema); o valor vem do TOTVS."""
    user = _require_user(request, db)
    codccu = (payload.codccu or "").strip()
    produto = (payload.produto_codigo or "").strip()
    if not codccu or not produto:
        raise HTTPException(status_code=400, detail="Informe centro de custo e produto.")

    row = _cc_price_row(db, codccu, produto)
    if row is None:
        raise HTTPException(status_code=404, detail="Preço deste CC/produto não encontrado.")

    row.is_manual_price = False  # destrava só esta linha
    db.commit()

    # Ressincroniza esta linha do TOTVS (falha-segura: não destrói o valor atual).
    from app.services.price_autosync import resync_one_cc_item
    resync = resync_one_cc_item(db, codccu, produto)
    db.refresh(row)

    audit(request, "catalogo_produto.cc_preco_automatico", entidade="cc_item_price",
          entidade_id=str(row.id),
          detalhe={"codccu": codccu, "produto_codigo": produto,
                   "manual": False, "ressincronizado": resync.get("ok"),
                   "valor": resync.get("valor")},
          user=user)
    return {"success": True, "data": row.to_dict(), "resync": resync}


@router.post("/api/products/sync-totvs")
async def sync_totvs_now(request: Request, db: Session = Depends(get_db)):
    """"Sincronizar agora" — dispara a rotina de autosync de EPI do TOTVS sob
    demanda (gestor+). Auditado. Falha de TOTVS não destrói preços existentes."""
    user = require_role(request, db, "gestor")
    from app.services.price_autosync import run_epi_price_autosync
    result = run_epi_price_autosync(db)

    resumo = {k: v for k, v in result.items()
              if isinstance(v, (int, float, str, bool)) or v is None}
    audit(request, "catalogo_produto.sync_totvs", entidade="cc_item_price",
          detalhe=resumo, user=user,
          status="ok" if result.get("ok") else "erro")
    return {"success": bool(result.get("ok")), "result": result}
