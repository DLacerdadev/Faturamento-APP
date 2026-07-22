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
from app.routers.reports import page_router as reports_page_router
from app.routers.auth import router as auth_router, get_token_from_request
from app.routers.data_upload import router as data_upload_router
from app.routers.billing import router as billing_router
from app.routers.medical_exams import router as medical_exams_router
from app.routers.epi_purchases import router as epi_purchases_router
from app.routers.epi_catalog import router as epi_catalog_router
from app.routers.exam_intake import router as exam_intake_router
from app.routers.exam_catalog import router as exam_catalog_router
from app.routers.benefit_events import router as benefit_events_router
from app.routers.contract_params import router as contract_params_router
from app.routers.product_catalog import router as product_catalog_router
from app.routers.training_catalog import router as training_catalog_router
from app.routers.training_records import router as training_records_router
from app.routers.billing_models import router as billing_models_router
from app.routers.users_admin import router as users_admin_router
from app.routers.audit_logs import router as audit_logs_router
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
    
    # NOTA DE SEGURANÇA: o seed de admin com credencial fixa (admin/admin123) foi
    # REMOVIDO — era um backdoor (conta ativa de credencial conhecida). O admin
    # real é semeado em app/db.py (ti@grupoopus.com). Novos usuários só pela
    # tela /usuarios (admin) ou /api/auth/register (admin).

app.include_router(auth_router)
app.include_router(customers_router)
app.include_router(uploads_router)
app.include_router(reports_router)
app.include_router(reports_page_router)
app.include_router(integrations_router)
app.include_router(data_upload_router)
app.include_router(billing_router)
app.include_router(medical_exams_router)
app.include_router(epi_purchases_router)
app.include_router(epi_catalog_router)
app.include_router(exam_intake_router)
app.include_router(exam_catalog_router)
app.include_router(benefit_events_router)
app.include_router(contract_params_router)
app.include_router(product_catalog_router)
app.include_router(training_catalog_router)
app.include_router(training_records_router)
app.include_router(billing_models_router)
app.include_router(users_admin_router)
app.include_router(audit_logs_router)

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

def _compute_dashboard_metrics(db: Session) -> dict:
    """Métricas agregadas (somente contagens — sem dado pessoal) para o dashboard."""
    from sqlalchemy import func
    from app.models.customer import Customer
    from app.models.billing import BillingPeriod
    from app.models.medical_exam import MedicalExam
    from app.models.product_catalog import ProductCatalog
    from app.models.epi_purchase import EpiCatalog
    from app.models.benefit_event import BenefitEvent
    from app.models.exam_catalog import ExamCatalog

    m = {
        "prod_total": 0, "prod_epi": 0, "prod_uniforme": 0, "prod_equip": 0,
        "prod_sem_preco": 0, "prod_com_preco": 0,
        "prod_epi_pct": 0, "prod_uniforme_pct": 0, "prod_equip_pct": 0, "prod_preco_pct": 0,
        "periodos": 0, "ultimo_periodo": "—",
        "exames_total": 0, "exames_rasc": 0,
        "epis": 0, "exam_cat": 0,
        "benef_ativos": 0, "benef_total": 0, "clientes": 0,
    }
    try:
        cats = dict(db.query(ProductCatalog.categoria, func.count()).group_by(ProductCatalog.categoria).all())
        m["prod_epi"] = cats.get("epi", 0)
        m["prod_uniforme"] = cats.get("uniforme", 0)
        m["prod_equip"] = cats.get("equipamento", 0)
        m["prod_total"] = m["prod_epi"] + m["prod_uniforme"] + m["prod_equip"]
        m["prod_sem_preco"] = db.query(func.count(ProductCatalog.id)).filter(
            (ProductCatalog.preco.is_(None)) | (ProductCatalog.preco == 0)
        ).scalar() or 0
        m["prod_com_preco"] = m["prod_total"] - m["prod_sem_preco"]

        def pct(x):
            return round(x / m["prod_total"] * 100, 1) if m["prod_total"] else 0
        m["prod_epi_pct"] = pct(m["prod_epi"])
        m["prod_uniforme_pct"] = pct(m["prod_uniforme"])
        m["prod_equip_pct"] = pct(m["prod_equip"])
        m["prod_preco_pct"] = pct(m["prod_com_preco"])

        m["periodos"] = db.query(func.count(BillingPeriod.id)).scalar() or 0
        ultimo = db.query(func.max(BillingPeriod.mes_referencia)).scalar()
        if ultimo and "-" in str(ultimo):
            yyyy, mm = str(ultimo).split("-")[:2]
            m["ultimo_periodo"] = f"{mm}/{yyyy}"

        m["exames_total"] = db.query(func.count(MedicalExam.id)).scalar() or 0
        m["exames_rasc"] = db.query(func.count(MedicalExam.id)).filter(MedicalExam.status == "rascunho").scalar() or 0
        m["epis"] = db.query(func.count(EpiCatalog.id)).scalar() or 0
        m["exam_cat"] = db.query(func.count(ExamCatalog.id)).scalar() or 0
        m["benef_ativos"] = db.query(func.count(BenefitEvent.id)).filter(BenefitEvent.ativo.is_(True)).scalar() or 0
        m["benef_total"] = db.query(func.count(BenefitEvent.id)).scalar() or 0
        m["clientes"] = db.query(func.count(Customer.id)).scalar() or 0
    except Exception:
        logger.exception("Erro ao computar métricas do dashboard")
    return m


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db)):
    result = get_current_user_from_token(request, db)
    if not result:
        return RedirectResponse(url="/login", status_code=303)
    user, token = result
    metrics = _compute_dashboard_metrics(db)
    return templates.TemplateResponse(
        "dashboard_auth.html",
        {"request": request, "user": user, "token": token, "m": metrics},
    )

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
        return RedirectResponse(url="/customers", status_code=303)
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

@app.get("/epis", response_class=HTMLResponse)
async def epis_page(request: Request, db: Session = Depends(get_db)):
    """Tela de compras de EPIs (spec 001-epi-purchase-flow)."""
    result = get_current_user_from_token(request, db)
    if not result:
        return RedirectResponse(url="/login", status_code=303)
    user, token = result
    return templates.TemplateResponse("epis.html", {"request": request, "user": user, "token": token})

@app.get("/catalogo-epis", response_class=HTMLResponse)
async def catalogo_epis_page(request: Request, db: Session = Depends(get_db)):
    """Catálogo de EPIs (spec 002-epi-catalog-orders)."""
    result = get_current_user_from_token(request, db)
    if not result:
        return RedirectResponse(url="/login", status_code=303)
    user, token = result
    return templates.TemplateResponse("catalogo_epis.html", {"request": request, "user": user, "token": token})

@app.get("/health")
async def health_check():
    return {"status": "healthy", "message": "Sistema funcionando normalmente"}


# /monitor — APENAS EM DEV: tela de inspeção de chamadas Senior (latência, 503,
# resets, timeouts). Acesso só pela URL direta, sem link no menu, sem auth.
# Em produção (DEV_MODE=False) retorna 404 — não vaza nada.
@app.get("/monitor", response_class=HTMLResponse, include_in_schema=False)
async def monitor_page(request: Request):
    from app.config import MONITOR_PAGE_ENABLED
    if not MONITOR_PAGE_ENABLED:
        return JSONResponse(status_code=404, content={"detail": "Not Found"})
    from app.services import monitor_buffer
    return templates.TemplateResponse("monitor.html", {
        "request": request,
        "events": monitor_buffer.get_recent(200),
        "counters": monitor_buffer.get_counters(),
    })


@app.post("/monitor/reset", include_in_schema=False)
async def monitor_reset():
    from app.config import MONITOR_PAGE_ENABLED
    if not MONITOR_PAGE_ENABLED:
        return JSONResponse(status_code=404, content={"detail": "Not Found"})
    from app.services import monitor_buffer
    monitor_buffer.reset()
    return RedirectResponse(url="/monitor", status_code=303)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)
