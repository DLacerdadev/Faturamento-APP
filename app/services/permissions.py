"""Permissões por papel (cargo) de usuário — contrato C4 do plano.

Hierarquia: 'operador' < 'gestor' < 'admin'. Papéis ficam em users.role
(migração idempotente em app/db.py; seed: ti@grupoopus.com = admin).

Uso nos routers:
    from app.services.permissions import require_role
    user = require_role(request, db, "gestor")   # 401 sem login, 403 sem papel
"""
from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from app.session_manager import validate_token
from app.routers.auth import get_token_from_request

# Ordem hierárquica dos papéis: quanto maior, mais permissões.
ROLE_ORDER = {"operador": 0, "gestor": 1, "admin": 2}

# Papéis válidos para cadastro/edição (tela /usuarios)
VALID_ROLES = tuple(ROLE_ORDER.keys())


def get_request_user(request: Request, db: Session):
    """Resolve o usuário autenticado da request via token (cookie/Bearer).
    Retorna None quando não autenticado — não levanta exceção."""
    token = get_token_from_request(request)
    return validate_token(token, db) if token else None


def role_level(role: str | None) -> int:
    """Nível numérico do papel (papéis desconhecidos/NULL contam como operador)."""
    return ROLE_ORDER.get((role or "operador").strip().lower(), 0)


def has_role(user, minimo: str) -> bool:
    """True se o usuário tem papel >= 'minimo' na hierarquia."""
    if user is None:
        return False
    return role_level(getattr(user, "role", None)) >= ROLE_ORDER.get(minimo, 0)


def require_role(request: Request, db: Session, minimo: str = "operador"):
    """Exige usuário autenticado com papel >= 'minimo'.

    Levanta HTTPException 401 (não autenticado) ou 403 (papel insuficiente).
    Retorna o User quando autorizado.
    """
    user = get_request_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Não autenticado")
    if not has_role(user, minimo):
        raise HTTPException(
            status_code=403,
            detail=f"Permissão insuficiente: requer papel '{minimo}' ou superior.",
        )
    return user
