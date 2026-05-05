import os
from pathlib import Path
from urllib.parse import unquote
from fastapi import FastAPI, Request, Depends
from fastapi.exceptions import HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware
from app.db import init_db, seed_dev_data, get_db
from app.routers import customers_router, uploads_router, reports_router, integrations_router
from app.routers.auth import router as auth_router, get_token_from_request
from app.routers.data_upload import router as data_upload_router
from app.routers.billing import router as billing_router
from app.routers.medical_exams import router as medical_exams_router
from app.routers.epi_purchases import router as epi_purchases_router
from app.config import TEMPLATES_DIR
from app.models.user import User
from app.session_manager import session_manager
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class URLDecodeMiddleware(BaseHTTPMiddleware):
    """Middleware para corrigir URLs com encoding duplo (ex: %3F ao invés de ?)"""
    async def dispatch(self, request: Request, call_next):
        path = request.scope["path"]
        raw_path = request.scope.get("raw_path", b"").decode("utf-8", errors="ignore")
        
        check_path = raw_path if raw_path else path
        
        if "%3F" in check_path or "%3f" in check_path:
            decoded_path = unquote(check_path)
            if "?" in decoded_path:
                parts = decoded_path.split("?", 1)
                new_path = parts[0]
                query_string = parts[1] if len(parts) > 1 else ""
                redirect_url = f"{new_path}?{query_string}"
                return RedirectResponse(url=redirect_url, status_code=302)
        
        if "?token=" in path or "?token%3D" in path:
            if "?" in path:
                parts = path.split("?", 1)
                new_path = parts[0]
                query_string = unquote(parts[1]) if len(parts) > 1 else ""
                redirect_url = f"{new_path}?{query_string}"
                return RedirectResponse(url=redirect_url, status_code=302)
        
        return await call_next(request)

app = FastAPI(
    title="Telos Consultoria",
    description="Sistema de gestão de clientes, relatórios e integrações",
    version="1.0.0"
)

app.add_middleware(URLDecodeMiddleware)

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == 401:
        accept = request.headers.get("accept", "")
        if "text/html" in accept and "/api/" not in str(request.url.path):
            return RedirectResponse(url="/", status_code=302)
        return JSONResponse(status_code=401, content={"detail": exc.detail})
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

def get_current_user_from_token(request: Request, db: Session = Depends(get_db)):
    token = get_token_from_request(request)
    if not token:
        return None
    
    session = session_manager.get_session(token)
    if not session:
        return None
    
    user = db.query(User).filter(User.id == session["user_id"]).first()
    return user, token

@app.on_event("startup")
async def startup_event():
    logger.info("Inicializando banco de dados...")
    init_db()
    logger.info("Banco de dados inicializado com sucesso!")
    seed_dev_data()
    
    from app.db import SessionLocal
    db = SessionLocal()
    try:
        admin_user = db.query(User).filter(User.username == "admin").first()
        if not admin_user:
            admin_user = User(
                username="admin",
                email="admin@sistema.com",
                full_name="Administrador"
            )
            admin_user.set_password("admin123")
            db.add(admin_user)
            db.commit()
            logger.info("Usuário admin criado (admin/admin123)")
    finally:
        db.close()

app.include_router(auth_router)
app.include_router(customers_router)
app.include_router(uploads_router)
app.include_router(reports_router)
app.include_router(integrations_router)
app.include_router(data_upload_router)
app.include_router(billing_router)
app.include_router(medical_exams_router)
app.include_router(epi_purchases_router)

@app.get("/proposta-comercial", response_class=HTMLResponse)
async def proposta_comercial(request: Request):
    return templates.TemplateResponse("documento_comercial.html", {"request": request})

@app.get("/documento-institucional", response_class=HTMLResponse)
async def documento_institucional(request: Request):
    return templates.TemplateResponse("documento_institucional.html", {"request": request})

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return RedirectResponse(url="/login", status_code=303)

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db)):
    result = get_current_user_from_token(request, db)
    if not result:
        return RedirectResponse(url="/login", status_code=303)
    user, token = result
    return templates.TemplateResponse("dashboard_auth.html", {"request": request, "user": user, "token": token})

@app.get("/upload", response_class=HTMLResponse)
async def upload_page(request: Request, db: Session = Depends(get_db)):
    result = get_current_user_from_token(request, db)
    if not result:
        return RedirectResponse(url="/login", status_code=303)
    user, token = result
    return templates.TemplateResponse("upload_page.html", {"request": request, "user": user, "token": token})

@app.get("/data-upload", response_class=HTMLResponse)
async def data_upload_page(request: Request, db: Session = Depends(get_db)):
    result = get_current_user_from_token(request, db)
    if not result:
        return RedirectResponse(url="/login", status_code=303)
    user, token = result
    return templates.TemplateResponse("data_upload.html", {"request": request, "user": user, "token": token})

@app.get("/reports", response_class=HTMLResponse)
async def reports_page(request: Request, db: Session = Depends(get_db)):
    result = get_current_user_from_token(request, db)
    if not result:
        return RedirectResponse(url="/login", status_code=303)
    user, token = result
    return templates.TemplateResponse("reports_list.html", {"request": request, "user": user, "token": token})

@app.get("/customers", response_class=HTMLResponse)
async def customers_page(request: Request, db: Session = Depends(get_db)):
    result = get_current_user_from_token(request, db)
    if not result:
        return RedirectResponse(url="/login", status_code=303)
    user, token = result
    from app.models.customer import Customer
    customers = db.query(Customer).all()
    customers_list = [c.to_dict() for c in customers]
    return templates.TemplateResponse("customers_list.html", {"request": request, "user": user, "token": token, "customers": customers_list})

@app.get("/customers/new", response_class=HTMLResponse)
async def customer_new_page(request: Request, db: Session = Depends(get_db)):
    result = get_current_user_from_token(request, db)
    if not result:
        return RedirectResponse(url="/login", status_code=303)
    user, token = result
    return templates.TemplateResponse("customer_form.html", {"request": request, "user": user, "token": token})

@app.get("/customers/{customer_id}", response_class=HTMLResponse)
async def customer_detail_page(customer_id: int, request: Request, db: Session = Depends(get_db)):
    result = get_current_user_from_token(request, db)
    if not result:
        return RedirectResponse(url="/login", status_code=303)
    user, token = result
    from app.models.customer import Customer, ReportTemplate
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        return RedirectResponse(url="/customers?token=" + token, status_code=303)
    templates_list = db.query(ReportTemplate).filter(ReportTemplate.customer_id == customer_id).all()
    return templates.TemplateResponse("customer_detail.html", {
        "request": request, 
        "user": user, 
        "token": token, 
        "customer": customer.to_dict(),
        "templates": [t.to_dict() for t in templates_list]
    })

@app.get("/billing", response_class=HTMLResponse)
async def billing_page(request: Request, db: Session = Depends(get_db)):
    result = get_current_user_from_token(request, db)
    if not result:
        return RedirectResponse(url="/login", status_code=303)
    user, token = result
    return templates.TemplateResponse("billing.html", {"request": request, "user": user, "token": token})

@app.get("/billing/ui", response_class=HTMLResponse)
async def billing_form_page(request: Request, db: Session = Depends(get_db)):
    """Página para gerar fatura Excel para o financeiro."""
    result = get_current_user_from_token(request, db)
    if not result:
        return RedirectResponse(url="/login", status_code=303)
    user, token = result
    return templates.TemplateResponse("billing_form.html", {"request": request, "user": user, "token": token})

@app.get("/health")
async def health_check():
    return {"status": "healthy", "message": "Sistema funcionando normalmente"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)
