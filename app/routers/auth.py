from fastapi import APIRouter, Depends, HTTPException, Request, Form
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from datetime import datetime
from pydantic import BaseModel
from app.db import get_db
from app.models.user import User
from app.config import TEMPLATES_DIR
from app.session_manager import session_manager

router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

class UserCreate(BaseModel):
    email: str
    password: str
    full_name: str | None = None

class LoginRequest(BaseModel):
    email: str
    password: str

def get_token_from_request(request: Request) -> str | None:
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header[7:]
    token = request.query_params.get("token")
    if token:
        return token
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
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.email == login_data.email).first()
    
    if not user or not user.verify_password(login_data.password):
        return JSONResponse(
            status_code=401,
            content={"success": False, "error": "Email ou senha invalidos"}
        )
    
    if not user.is_active:
        return JSONResponse(
            status_code=401,
            content={"success": False, "error": "Usuário está inativo"}
        )
    
    user.last_login = datetime.utcnow().isoformat()
    db.commit()
    
    session_manager.cleanup_expired()
    token = session_manager.create_session(user.id, user.email)
    
    return JSONResponse(content={
        "success": True,
        "token": token,
        "user": {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name
        }
    })

@router.post("/api/auth/register")
async def register(user_data: UserCreate, db: Session = Depends(get_db)):
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
    
    return {"message": "Usuario criado com sucesso", "user": new_user.to_dict()}

@router.get("/api/auth/logout")
async def logout(request: Request):
    token = get_token_from_request(request)
    if token:
        session_manager.delete_session(token)
    return JSONResponse(content={"success": True, "message": "Logged out"})

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
