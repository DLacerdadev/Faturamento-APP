from fastapi import APIRouter, Depends, HTTPException, Request, Form
from fastapi.responses import RedirectResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from datetime import datetime
from pydantic import BaseModel
from app.db import get_db
from app.models.user import User
from app.config import TEMPLATES_DIR, SESSION_COOKIE_SECURE, SESSION_COOKIE_SAMESITE
from app.session_manager import session_manager
from app.services.audit import audit

router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Nome do cookie de sessão. httpOnly + SameSite=Lax (default). Em produção HTTPS,
# setar secure=True via env (não está aqui pra dev funcionar em http://).
SESSION_COOKIE = "session_token"
SESSION_COOKIE_MAX_AGE = 60 * 60 * 12  # 12h

class UserCreate(BaseModel):
    email: str
    password: str
    full_name: str | None = None

class LoginRequest(BaseModel):
    email: str
    password: str

def get_token_from_request(request: Request) -> str | None:
    # 1) Cookie httpOnly (uso normal — não vaza em URL/screenshots/logs)
    cookie_token = request.cookies.get(SESSION_COOKIE)
    if cookie_token:
        return cookie_token
    # 2) Authorization: Bearer (clientes API)
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header[7:]
    # NOTA: query string ?token= foi REMOVIDA por segurança — tokens em URL
    # vazam em screenshots, logs do servidor, histórico do browser, proxies.
    return None

def get_current_user(request: Request, db: Session = Depends(get_db)):
    token = get_token_from_request(request)
    if not token:
        return None
    
    session = session_manager.get_session(token)
    if not session:
        return None
    
    user = db.query(User).filter(User.id == session["user_id"]).first()
    return user

def require_login(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user

@router.post("/api/auth/login")
async def api_login(
    login_data: LoginRequest,
    request: Request,
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.email == login_data.email).first()

    if not user or not user.verify_password(login_data.password):
        audit(request, "auth.login_falha", entidade="users", status="negado",
              detalhe={"username_tentado": login_data.email})
        return JSONResponse(
            status_code=401,
            content={"success": False, "error": "Email ou senha invalidos"}
        )

    if not user.is_active:
        audit(request, "auth.login_falha", entidade="users",
              entidade_id=str(user.id), status="negado",
              detalhe={"username_tentado": login_data.email, "motivo": "usuario_inativo"})
        return JSONResponse(
            status_code=401,
            content={"success": False, "error": "Usuário está inativo"}
        )

    user.last_login = datetime.utcnow().isoformat()
    db.commit()

    audit(request, "auth.login", entidade="users", entidade_id=str(user.id), user=user)

    session_manager.cleanup_expired()
    token = session_manager.create_session(user.id, user.email)

    response = JSONResponse(content={
        "success": True,
        "token": token,
        "user": {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name
        }
    })
    # Seta cookie httpOnly — não acessível via JS, não aparece na URL, vai
    # automaticamente em toda request subsequente pro mesmo origem.
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=SESSION_COOKIE_MAX_AGE,
        httponly=True,
        samesite=SESSION_COOKIE_SAMESITE,
        secure=SESSION_COOKIE_SECURE,
        path="/",
    )
    return response

@router.post("/api/auth/register")
async def register(user_data: UserCreate, request: Request, db: Session = Depends(get_db)):
    # Somente admin cria usuários (atalho de criação — a gestão completa fica
    # em /usuarios). Import local para evitar import circular com permissions.
    from app.services.permissions import require_role
    admin = require_role(request, db, "admin")

    existing_email = db.query(User).filter(User.email == user_data.email).first()
    if existing_email:
        raise HTTPException(status_code=400, detail="Email ja cadastrado")
    
    new_user = User(
        username=user_data.email,
        email=user_data.email,
        full_name=user_data.full_name,
        is_active=1
    )
    new_user.set_password(user_data.password)
    
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    audit(request, "usuario.criar", entidade="users", entidade_id=str(new_user.id),
          detalhe={"origem": "register"}, user=admin)

    return {"message": "Usuario criado com sucesso", "user": new_user.to_dict()}

@router.get("/api/auth/logout")
async def logout(request: Request, db: Session = Depends(get_db)):
    token = get_token_from_request(request)
    # Resolve o usuário ANTES de apagar a sessão (depois o token já não valida)
    user = get_current_user(request, db) if token else None
    if token:
        session_manager.delete_session(token)
    audit(request, "auth.logout", entidade="users",
          entidade_id=str(user.id) if user else None, user=user)
    response = JSONResponse(content={"success": True, "message": "Logged out"})
    response.delete_cookie(key=SESSION_COOKIE, path="/")
    return response

@router.get("/api/auth/validate")
async def validate_token(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return JSONResponse(status_code=401, content={"valid": False})
    return JSONResponse(content={
        "valid": True,
        "user": {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name
        }
    })

@router.get("/api/auth/me")
async def get_me(user: User = Depends(require_login)):
    return user.to_dict()
