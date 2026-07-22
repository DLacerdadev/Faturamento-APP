"""
Router do Catálogo de EPIs (feature 002).
CRUD: list, detail, create, update, soft-delete, reactivate.
"""
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func
from typing import List, Optional
from pydantic import BaseModel, Field

from app.db import get_db
from app.models.epi_purchase import EpiCatalog, EpiCatalogSize, EpiPurchaseItem
from app.services.audit import audit
from app.routers.auth import require_login

# Todas as rotas exigem login (dependency no nível do router).
router = APIRouter(prefix="/api/epi-catalog", tags=["epi_catalog"],
                   dependencies=[Depends(require_login)])


class SizeInput(BaseModel):
    tamanho: str = Field(..., min_length=1, max_length=20)
    valor: float = Field(..., gt=0)


class EpiCatalogCreate(BaseModel):
    nome: str = Field(..., min_length=1, max_length=200)
    ca_padrao: Optional[str] = Field(None, max_length=50)
    sizes: List[SizeInput] = Field(..., min_length=1)


def _epi_to_dict(epi: EpiCatalog, in_use_count: int = 0) -> dict:
    return {
        "id": epi.id,
        "nome": epi.nome,
        "ativo": bool(epi.ativo),
        "ca_padrao": epi.ca_padrao,
        "sizes": [
            {"id": s.id, "tamanho": s.tamanho, "valor": s.valor}
            for s in sorted(epi.sizes or [], key=lambda s: (s.tamanho or "").upper())
        ],
        "in_use_count": in_use_count,
        "created_at": epi.created_at.isoformat() if epi.created_at else None,
        "updated_at": epi.updated_at.isoformat() if epi.updated_at else None,
    }


def _validate_unique_name(db: Session, nome: str, exclude_id: Optional[int] = None) -> Optional[str]:
    """Retorna mensagem de erro se já existe outro EPI ativo com o mesmo nome (case-insensitive)."""
    q = db.query(EpiCatalog).filter(
        EpiCatalog.ativo == True,
        func.upper(EpiCatalog.nome) == nome.upper(),
    )
    if exclude_id is not None:
        q = q.filter(EpiCatalog.id != exclude_id)
    if q.first():
        return f"Já existe um EPI ativo com o nome '{nome}'."
    return None


def _validate_unique_sizes(sizes: List[SizeInput]) -> Optional[str]:
    seen = set()
    for s in sizes:
        key = (s.tamanho or "").strip().upper()
        if key in seen:
            return f"Tamanho '{s.tamanho}' está duplicado."
        seen.add(key)
    return None


def _in_use_counts(db: Session, epi_ids: List[int]) -> dict:
    """Retorna mapa {epi_id: count_of_purchase_items}."""
    if not epi_ids:
        return {}
    rows = (
        db.query(EpiPurchaseItem.epi_id, func.count(EpiPurchaseItem.id))
        .filter(EpiPurchaseItem.epi_id.in_(epi_ids))
        .group_by(EpiPurchaseItem.epi_id)
        .all()
    )
    return {row[0]: row[1] for row in rows}


@router.get("")
async def list_catalog(
    q: Optional[str] = Query(None, description="filtro por nome (case-insensitive)"),
    include_inactive: bool = Query(False),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=5000),
    db: Session = Depends(get_db),
):
    query = db.query(EpiCatalog).options(joinedload(EpiCatalog.sizes))
    if not include_inactive:
        query = query.filter(EpiCatalog.ativo == True)
    if q:
        query = query.filter(func.upper(EpiCatalog.nome).contains(q.upper()))

    total = query.count()
    items = (
        query.order_by(func.upper(EpiCatalog.nome), EpiCatalog.id)
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    in_use = _in_use_counts(db, [e.id for e in items])

    return {
        "status": "ok",
        "data": [_epi_to_dict(e, in_use.get(e.id, 0)) for e in items],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


@router.get("/{epi_id}")
async def get_catalog_entry(epi_id: int, db: Session = Depends(get_db)):
    epi = db.query(EpiCatalog).options(joinedload(EpiCatalog.sizes)).filter(EpiCatalog.id == epi_id).first()
    if not epi:
        return JSONResponse(status_code=404, content={"status": "error", "message": "EPI não encontrado"})
    in_use = _in_use_counts(db, [epi.id]).get(epi.id, 0)
    return {"status": "ok", "data": _epi_to_dict(epi, in_use)}


def _sizes_snapshot(sizes) -> list:
    """Lista ordenada [{tamanho, valor}] para comparação/registro na auditoria."""
    return sorted(
        [{"tamanho": (s.tamanho or "").strip(), "valor": s.valor} for s in (sizes or [])],
        key=lambda x: x["tamanho"].upper(),
    )


@router.post("")
async def create_catalog_entry(data: EpiCatalogCreate, request: Request, db: Session = Depends(get_db)):
    err = _validate_unique_sizes(data.sizes)
    if err:
        return JSONResponse(status_code=400, content={"status": "error", "message": err})

    err = _validate_unique_name(db, data.nome)
    if err:
        return JSONResponse(status_code=409, content={"status": "error", "message": err})

    epi = EpiCatalog(
        nome=data.nome.strip(),
        ativo=True,
        ca_padrao=(data.ca_padrao or None) and data.ca_padrao.strip(),
    )
    for s in data.sizes:
        epi.sizes.append(EpiCatalogSize(tamanho=s.tamanho.strip(), valor=s.valor))

    db.add(epi)
    db.commit()
    db.refresh(epi)

    audit(request, "catalogo_epi.criar", entidade="epi_catalog", entidade_id=str(epi.id),
          detalhe={"nome": epi.nome, "ca_padrao": epi.ca_padrao,
                   "sizes": _sizes_snapshot(epi.sizes)},
          db=db)

    return JSONResponse(status_code=201, content={"status": "success", "data": _epi_to_dict(epi, 0)})


@router.put("/{epi_id}")
async def update_catalog_entry(epi_id: int, data: EpiCatalogCreate, request: Request, db: Session = Depends(get_db)):
    epi = db.query(EpiCatalog).options(joinedload(EpiCatalog.sizes)).filter(EpiCatalog.id == epi_id).first()
    if not epi:
        return JSONResponse(status_code=404, content={"status": "error", "message": "EPI não encontrado"})

    err = _validate_unique_sizes(data.sizes)
    if err:
        return JSONResponse(status_code=400, content={"status": "error", "message": err})

    err = _validate_unique_name(db, data.nome, exclude_id=epi.id)
    if err:
        return JSONResponse(status_code=409, content={"status": "error", "message": err})

    # Snapshot ANTES da edição — para registrar só os campos que mudaram
    antes = {
        "nome": epi.nome,
        "ca_padrao": epi.ca_padrao,
        "sizes": _sizes_snapshot(epi.sizes),
    }

    epi.nome = data.nome.strip()
    epi.ca_padrao = (data.ca_padrao or None) and data.ca_padrao.strip()
    for old in list(epi.sizes):
        db.delete(old)
    db.flush()
    for s in data.sizes:
        epi.sizes.append(EpiCatalogSize(tamanho=s.tamanho.strip(), valor=s.valor))

    db.commit()
    db.refresh(epi)

    depois = {
        "nome": epi.nome,
        "ca_padrao": epi.ca_padrao,
        "sizes": _sizes_snapshot(epi.sizes),
    }
    alteracoes = {
        campo: {"de": antes[campo], "para": depois[campo]}
        for campo in antes if antes[campo] != depois[campo]
    }
    if alteracoes:
        audit(request, "catalogo_epi.editar", entidade="epi_catalog", entidade_id=str(epi.id),
              detalhe={"alteracoes": alteracoes}, db=db)

    in_use = _in_use_counts(db, [epi.id]).get(epi.id, 0)
    return {"status": "success", "data": _epi_to_dict(epi, in_use)}


@router.delete("/{epi_id}")
async def deactivate_catalog_entry(epi_id: int, request: Request, db: Session = Depends(get_db)):
    epi = db.query(EpiCatalog).filter(EpiCatalog.id == epi_id).first()
    if not epi:
        return JSONResponse(status_code=404, content={"status": "error", "message": "EPI não encontrado"})

    in_use = _in_use_counts(db, [epi.id]).get(epi.id, 0)
    ativo_antes = bool(epi.ativo)
    epi.ativo = False
    db.commit()

    # Desativação é registrada como edição (soft-delete)
    audit(request, "catalogo_epi.editar", entidade="epi_catalog", entidade_id=str(epi.id),
          detalhe={"alteracoes": {"ativo": {"de": ativo_antes, "para": False}}}, db=db)

    warning = None
    if in_use > 0:
        warning = f"Este EPI tem {in_use} linha(s) de pedido vinculadas; ele continuará visível nelas mas não aparece para novos pedidos."

    return {"status": "success", "message": "EPI desativado", "warning": warning}


@router.post("/{epi_id}/reactivate")
async def reactivate_catalog_entry(epi_id: int, request: Request, db: Session = Depends(get_db)):
    epi = db.query(EpiCatalog).options(joinedload(EpiCatalog.sizes)).filter(EpiCatalog.id == epi_id).first()
    if not epi:
        return JSONResponse(status_code=404, content={"status": "error", "message": "EPI não encontrado"})

    err = _validate_unique_name(db, epi.nome, exclude_id=epi.id)
    if err:
        return JSONResponse(status_code=409, content={"status": "error", "message": err})

    ativo_antes = bool(epi.ativo)
    epi.ativo = True
    db.commit()
    db.refresh(epi)

    # Reativação também é edição do campo ativo
    audit(request, "catalogo_epi.editar", entidade="epi_catalog", entidade_id=str(epi.id),
          detalhe={"alteracoes": {"ativo": {"de": ativo_antes, "para": True}}}, db=db)
    in_use = _in_use_counts(db, [epi.id]).get(epi.id, 0)
    return {"status": "success", "data": _epi_to_dict(epi, in_use)}
