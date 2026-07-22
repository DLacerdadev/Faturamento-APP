"""Gestão de usuários (tela /usuarios do administrador).

Endpoints admin-only para listar/criar/editar usuários, papéis e senhas.
Salvaguardas: o admin não desativa/rebaixa a si próprio, e o ÚLTIMO admin
ativo do sistema nunca pode ser rebaixado nem desativado.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from app.db import get_db
from app.session_manager import validate_token
from app.routers.auth import get_token_from_request
from app.services.permissions import require_role, has_role, VALID_ROLES
from app.models.user import User
from app.services.audit import audit

router = APIRouter(tags=["usuarios"])
templates = Jinja2Templates(directory="app/templates")

SENHA_MINIMA = 8  # mínimo de caracteres para senhas


class UserCreateIn(BaseModel):
    username: str
    email: str
    full_name: Optional[str] = None
    role: str = "operador"
    password: str


class UserUpdateIn(BaseModel):
    full_name: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None


class PasswordIn(BaseModel):
    password: str


def _normaliza_role(role: str) -> str:
    """Valida e normaliza o papel — levanta 400 se não for um papel conhecido."""
    r = (role or "").strip().lower()
    if r not in VALID_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"Papel inválido: use um destes — {', '.join(VALID_ROLES)}.",
        )
    return r


def _valida_senha(password: str):
    if not password or len(password) < SENHA_MINIMA:
        raise HTTPException(
            status_code=400,
            detail=f"A senha deve ter no mínimo {SENHA_MINIMA} caracteres.",
        )


def _conta_admins_ativos(db: Session) -> int:
    """Quantidade de admins ativos no sistema (para proteger o último)."""
    return (
        db.query(User)
        .filter(User.role == "admin", User.is_active == 1)
        .count()
    )


def _eh_ultimo_admin_ativo(db: Session, alvo: User) -> bool:
    """True se o usuário alvo é admin ativo e não existe outro admin ativo."""
    if (alvo.role or "operador") != "admin" or not alvo.is_active:
        return False
    return _conta_admins_ativos(db) <= 1


@router.get("/usuarios", response_class=HTMLResponse)
async def usuarios_page(request: Request, db: Session = Depends(get_db)):
    """Tela de gestão de usuários — somente admin. Sem login vai pro /login;
    logado sem papel suficiente volta pro dashboard (padrão das telas HTML)."""
    token = get_token_from_request(request)
    user = validate_token(token, db) if token else None
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    if not has_role(user, "admin"):
        return RedirectResponse(url="/dashboard", status_code=303)
    return templates.TemplateResponse(
        "usuarios.html",
        {"request": request, "user": user, "token": token},
    )


@router.get("/api/users")
async def list_users(request: Request, db: Session = Depends(get_db)):
    require_role(request, db, "admin")
    users = db.query(User).order_by(User.full_name, User.username).all()
    data = []
    for u in users:
        d = u.to_dict()
        d["username"] = u.username  # to_dict não expõe username; a tela precisa
        data.append(d)
    return {"success": True, "data": data}


@router.post("/api/users")
async def create_user(payload: UserCreateIn, request: Request, db: Session = Depends(get_db)):
    admin = require_role(request, db, "admin")

    username = (payload.username or "").strip()
    email = (payload.email or "").strip()
    if not username:
        raise HTTPException(status_code=400, detail="Informe o username.")
    if not email:
        raise HTTPException(status_code=400, detail="Informe o email.")
    role = _normaliza_role(payload.role)
    _valida_senha(payload.password)

    if db.query(User).filter(User.username == username).first():
        raise HTTPException(status_code=400, detail="Username já cadastrado.")
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=400, detail="Email já cadastrado.")

    novo = User(
        username=username,
        email=email,
        full_name=(payload.full_name or "").strip() or None,
        role=role,
        is_active=1,
    )
    novo.set_password(payload.password)
    db.add(novo)
    db.commit()
    db.refresh(novo)

    audit(request, "usuario.criar", entidade="users", entidade_id=str(novo.id),
          detalhe={"role": role, "username_criado": username}, user=admin)

    d = novo.to_dict()
    d["username"] = novo.username
    return {"success": True, "data": d, "message": "Usuário criado com sucesso."}


@router.put("/api/users/{user_id}")
async def update_user(user_id: int, payload: UserUpdateIn, request: Request, db: Session = Depends(get_db)):
    admin = require_role(request, db, "admin")

    alvo = db.query(User).filter(User.id == user_id).first()
    if not alvo:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")

    data = payload.dict(exclude_unset=True)

    # --- Salvaguardas -----------------------------------------------------
    vai_rebaixar = (
        "role" in data
        and (alvo.role or "operador") == "admin"
        and _normaliza_role(data["role"]) != "admin"
    )
    vai_desativar = "is_active" in data and not data["is_active"] and bool(alvo.is_active)

    if alvo.id == admin.id:
        if vai_desativar:
            audit(request, "usuario.editar", entidade="users", entidade_id=str(alvo.id),
                  detalhe={"motivo": "auto_desativacao_bloqueada"}, user=admin, status="negado")
            raise HTTPException(status_code=400, detail="Você não pode desativar a si próprio.")
        if vai_rebaixar:
            audit(request, "usuario.editar", entidade="users", entidade_id=str(alvo.id),
                  detalhe={"motivo": "auto_rebaixamento_bloqueado"}, user=admin, status="negado")
            raise HTTPException(status_code=400, detail="Você não pode rebaixar o próprio papel de admin.")

    if (vai_rebaixar or vai_desativar) and _eh_ultimo_admin_ativo(db, alvo):
        audit(request, "usuario.editar", entidade="users", entidade_id=str(alvo.id),
              detalhe={"motivo": "ultimo_admin_ativo_protegido"}, user=admin, status="negado")
        raise HTTPException(
            status_code=400,
            detail="Este é o último admin ativo do sistema — não pode ser rebaixado nem desativado.",
        )
    # ----------------------------------------------------------------------

    # Snapshot ANTES das mudanças, pra montar o detalhe {"alteracoes": ...}
    antes = {
        "email": alvo.email,
        "full_name": alvo.full_name,
        "role": alvo.role,
        "is_active": bool(alvo.is_active),
    }

    if "email" in data:
        email = (data["email"] or "").strip()
        if not email:
            raise HTTPException(status_code=400, detail="Informe o email.")
        existe = db.query(User).filter(User.email == email, User.id != alvo.id).first()
        if existe:
            raise HTTPException(status_code=400, detail="Email já cadastrado para outro usuário.")
        alvo.email = email

    if "full_name" in data:
        alvo.full_name = (data["full_name"] or "").strip() or None

    if "role" in data:
        alvo.role = _normaliza_role(data["role"])

    if "is_active" in data:
        alvo.is_active = 1 if data["is_active"] else 0

    db.commit()
    db.refresh(alvo)

    depois = {
        "email": alvo.email,
        "full_name": alvo.full_name,
        "role": alvo.role,
        "is_active": bool(alvo.is_active),
    }
    # Só campos enviados; grava o que mudou — is_active/role sempre entram
    alteracoes = {}
    for campo in data:
        if campo not in antes:
            continue
        if antes[campo] != depois[campo] or campo in ("is_active", "role"):
            alteracoes[campo] = {"de": antes[campo], "para": depois[campo]}
    audit(request, "usuario.editar", entidade="users", entidade_id=str(alvo.id),
          detalhe={"alteracoes": alteracoes}, user=admin)

    d = alvo.to_dict()
    d["username"] = alvo.username
    return {"success": True, "data": d, "message": "Usuário atualizado."}


@router.post("/api/users/{user_id}/password")
async def reset_password(user_id: int, payload: PasswordIn, request: Request, db: Session = Depends(get_db)):
    admin = require_role(request, db, "admin")

    alvo = db.query(User).filter(User.id == user_id).first()
    if not alvo:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")

    _valida_senha(payload.password)
    alvo.set_password(payload.password)
    db.commit()

    # NUNCA registrar a senha — só quem foi o alvo
    audit(request, "usuario.trocar_senha", entidade="users", entidade_id=str(alvo.id),
          detalhe={"alvo_user_id": alvo.id}, user=admin)

    return {"success": True, "message": "Senha redefinida com sucesso."}
