from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from app.db import get_db
from app.session_manager import validate_token
from app.routers.auth import get_token_from_request
from app.services.permissions import require_role
from app.services.audit import audit
from app.models.billing import Company

router = APIRouter()


def _require_user(request: Request, db: Session):
    token = get_token_from_request(request)
    user = validate_token(token, db) if token else None
    if not user:
        raise HTTPException(status_code=401, detail="Não autenticado")
    return user


class ParamsIn(BaseModel):
    encargos_pct: Optional[float] = None
    taxa_adm_pct: Optional[float] = None
    imposto_pct: Optional[float] = None


def _default_contract(db: Session) -> Company:
    # order_by(id) = mesmo contrato usado pela exportação (_build_billing_export),
    # mesmo que exista mais de uma empresa no banco. Determinístico.
    c = db.query(Company).order_by(Company.id).first()
    if not c:
        c = Company(name="Contrato padrão (TELOS/FEMSA)", cnpj_femsa="00000000000000")
        db.add(c)
        db.commit()
        db.refresh(c)
    return c


@router.get("/api/contract-params")
async def get_params(request: Request, db: Session = Depends(get_db)):
    _require_user(request, db)
    c = _default_contract(db)
    return {"success": True, "data": {
        "id": c.id, "nome": c.name,
        "encargos_pct": c.encargos_pct, "taxa_adm_pct": c.taxa_adm_pct, "imposto_pct": c.imposto_pct,
    }}


@router.put("/api/contract-params")
async def set_params(payload: ParamsIn, request: Request, db: Session = Depends(get_db)):
    # Gravação de regra administrativa: exige papel gestor (ou superior).
    user = require_role(request, db, "gestor")
    c = _default_contract(db)
    # Snapshot pré-edição para a auditoria (só campos que mudarem).
    antes = {
        "encargos_pct": c.encargos_pct,
        "taxa_adm_pct": c.taxa_adm_pct,
        "imposto_pct": c.imposto_pct,
    }
    # exclude_unset: campo AUSENTE não altera; campo enviado (inclusive null) grava —
    # null explícito LIMPA o padrão (antes nulos eram ignorados e não dava pra limpar).
    data = payload.dict(exclude_unset=True)
    if "encargos_pct" in data:
        c.encargos_pct = data["encargos_pct"]
    if "taxa_adm_pct" in data:
        c.taxa_adm_pct = data["taxa_adm_pct"]
    if "imposto_pct" in data:
        c.imposto_pct = data["imposto_pct"]
    db.commit()
    alteracoes = {
        campo: {"de": antes[campo], "para": getattr(c, campo)}
        for campo in antes
        if antes[campo] != getattr(c, campo)
    }
    audit(request, "contrato.parametros", entidade="contract_params",
          entidade_id=c.id, detalhe={"alteracoes": alteracoes}, user=user)
    return {"success": True, "data": {
        "encargos_pct": c.encargos_pct, "taxa_adm_pct": c.taxa_adm_pct, "imposto_pct": c.imposto_pct,
    }}
