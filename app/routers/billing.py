from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from typing import List, Optional
from app.db import get_db
from app.session_manager import validate_token
from app.services.billing_processor import (
    process_payroll_upload, 
    process_exams_upload,
    get_billing_period_summary
)
from app.models.billing import (
    Company, Unit, BillingEmployee, EmploymentContract,
    BillingPeriod, PayrollItemType, PayrollItem, BillingExamRecord,
    BillingStatus, AdditionalValue
)
from pydantic import BaseModel
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/billing", response_class=HTMLResponse)
async def billing_page(request: Request, token: str = None, db: Session = Depends(get_db)):
    if not token:
        return RedirectResponse(url="/", status_code=302)
    
    user = validate_token(token, db)
    if not user:
        return RedirectResponse(url="/", status_code=302)
    
    companies = db.query(Company).all()
    periods = db.query(BillingPeriod).order_by(BillingPeriod.created_at.desc()).limit(20).all()
    
    return templates.TemplateResponse(
        "billing.html",
        {
            "request": request,
            "token": token,
            "user": user,
            "companies": companies,
            "periods": periods
        }
    )


@router.post("/api/billing/upload-payroll")
async def upload_payroll(
    file: UploadFile = File(...),
    mes_referencia: str = Form(...),
    token: str = Form(None),
    db: Session = Depends(get_db)
):
    if not token:
        raise HTTPException(status_code=401, detail="Token não fornecido")
    
    user = validate_token(token, db)
    if not user:
        raise HTTPException(status_code=401, detail="Token inválido")
    
    if not file.filename.lower().endswith(('.xlsx', '.xls', '.csv')):
        raise HTTPException(status_code=400, detail="Formato de arquivo inválido. Use Excel ou CSV.")
    
    content = await file.read()
    
    result = process_payroll_upload(db, content, file.filename, mes_referencia)
    
    return result


@router.post("/api/billing/upload-exams")
async def upload_exams(
    file: UploadFile = File(...),
    token: str = Form(None),
    db: Session = Depends(get_db)
):
    if not token:
        raise HTTPException(status_code=401, detail="Token não fornecido")
    
    user = validate_token(token, db)
    if not user:
        raise HTTPException(status_code=401, detail="Token inválido")
    
    if not file.filename.lower().endswith(('.xlsx', '.xls', '.csv')):
        raise HTTPException(status_code=400, detail="Formato de arquivo inválido. Use Excel ou CSV.")
    
    content = await file.read()
    
    result = process_exams_upload(db, content, file.filename)
    
    return result


@router.get("/api/billing/periods")
async def list_billing_periods(
    token: str = None,
    company_id: Optional[int] = None,
    db: Session = Depends(get_db)
):
    if not token:
        raise HTTPException(status_code=401, detail="Token não fornecido")
    
    user = validate_token(token, db)
    if not user:
        raise HTTPException(status_code=401, detail="Token inválido")
    
    query = db.query(BillingPeriod)
    if company_id:
        query = query.filter(BillingPeriod.company_id == company_id)
    
    periods = query.order_by(BillingPeriod.created_at.desc()).all()
    
    return [
        {
            "id": p.id,
            "company_id": p.company_id,
            "company_name": p.company.name if p.company else None,
            "mes_referencia": p.mes_referencia,
            "status": p.status.value if p.status else None,
            "created_at": p.created_at.isoformat() if p.created_at else None
        }
        for p in periods
    ]


@router.get("/api/billing/periods/{period_id}/summary")
async def get_period_summary(
    period_id: int,
    token: str = None,
    db: Session = Depends(get_db)
):
    if not token:
        raise HTTPException(status_code=401, detail="Token não fornecido")
    
    user = validate_token(token, db)
    if not user:
        raise HTTPException(status_code=401, detail="Token inválido")
    
    summary = get_billing_period_summary(db, period_id)
    
    if "error" in summary:
        raise HTTPException(status_code=404, detail=summary["error"])
    
    return summary


@router.get("/api/billing/companies")
async def list_companies(
    token: str = None,
    db: Session = Depends(get_db)
):
    if not token:
        raise HTTPException(status_code=401, detail="Token não fornecido")
    
    user = validate_token(token, db)
    if not user:
        raise HTTPException(status_code=401, detail="Token inválido")
    
    companies = db.query(Company).all()
    
    return [
        {
            "id": c.id,
            "name": c.name,
            "cnpj_femsa": c.cnpj_femsa,
            "created_at": c.created_at.isoformat() if c.created_at else None
        }
        for c in companies
    ]


@router.get("/api/billing/payroll-item-types")
async def list_payroll_item_types(
    token: str = None,
    db: Session = Depends(get_db)
):
    if not token:
        raise HTTPException(status_code=401, detail="Token não fornecido")
    
    user = validate_token(token, db)
    if not user:
        raise HTTPException(status_code=401, detail="Token inválido")
    
    types = db.query(PayrollItemType).all()
    
    return [
        {
            "id": t.id,
            "code": t.code,
            "description": t.description,
            "group": t.group,
            "direction": t.direction.value if t.direction else None
        }
        for t in types
    ]


class AdditionalValueCreate(BaseModel):
    codccu: str
    nome_ccu: Optional[str] = None
    descricao: Optional[str] = None
    valor: float


class AdditionalValueUpdate(BaseModel):
    codccu: Optional[str] = None
    nome_ccu: Optional[str] = None
    descricao: Optional[str] = None
    valor: Optional[float] = None


@router.get("/api/billing/additional-values")
async def list_additional_values(
    token: str = None,
    db: Session = Depends(get_db)
):
    if not token:
        raise HTTPException(status_code=401, detail="Token não fornecido")
    
    user = validate_token(token, db)
    if not user:
        raise HTTPException(status_code=401, detail="Token inválido")
    
    values = db.query(AdditionalValue).order_by(AdditionalValue.created_at.desc()).all()
    
    return {
        "status": "ok",
        "data": [
            {
                "id": v.id,
                "codccu": v.codccu,
                "nome_ccu": v.nome_ccu,
                "descricao": v.descricao,
                "valor": v.valor,
                "created_at": v.created_at.isoformat() if v.created_at else None
            }
            for v in values
        ]
    }


@router.post("/api/billing/additional-values")
async def create_additional_value(
    data: AdditionalValueCreate,
    token: str = None,
    db: Session = Depends(get_db)
):
    if not token:
        raise HTTPException(status_code=401, detail="Token não fornecido")
    
    user = validate_token(token, db)
    if not user:
        raise HTTPException(status_code=401, detail="Token inválido")
    
    new_value = AdditionalValue(
        codccu=data.codccu,
        nome_ccu=data.nome_ccu,
        descricao=data.descricao,
        valor=data.valor
    )
    
    db.add(new_value)
    db.commit()
    db.refresh(new_value)
    
    return {
        "status": "ok",
        "message": "Valor adicional criado com sucesso",
        "data": {
            "id": new_value.id,
            "codccu": new_value.codccu,
            "nome_ccu": new_value.nome_ccu,
            "descricao": new_value.descricao,
            "valor": new_value.valor
        }
    }


@router.put("/api/billing/additional-values/{value_id}")
async def update_additional_value(
    value_id: int,
    data: AdditionalValueUpdate,
    token: str = None,
    db: Session = Depends(get_db)
):
    if not token:
        raise HTTPException(status_code=401, detail="Token não fornecido")
    
    user = validate_token(token, db)
    if not user:
        raise HTTPException(status_code=401, detail="Token inválido")
    
    value = db.query(AdditionalValue).filter(AdditionalValue.id == value_id).first()
    if not value:
        raise HTTPException(status_code=404, detail="Valor adicional não encontrado")
    
    if data.codccu is not None:
        value.codccu = data.codccu
    if data.nome_ccu is not None:
        value.nome_ccu = data.nome_ccu
    if data.descricao is not None:
        value.descricao = data.descricao
    if data.valor is not None:
        value.valor = data.valor
    
    db.commit()
    db.refresh(value)
    
    return {
        "status": "ok",
        "message": "Valor adicional atualizado com sucesso",
        "data": {
            "id": value.id,
            "codccu": value.codccu,
            "nome_ccu": value.nome_ccu,
            "descricao": value.descricao,
            "valor": value.valor
        }
    }


@router.delete("/api/billing/additional-values/{value_id}")
async def delete_additional_value(
    value_id: int,
    token: str = None,
    db: Session = Depends(get_db)
):
    if not token:
        raise HTTPException(status_code=401, detail="Token não fornecido")
    
    user = validate_token(token, db)
    if not user:
        raise HTTPException(status_code=401, detail="Token inválido")
    
    value = db.query(AdditionalValue).filter(AdditionalValue.id == value_id).first()
    if not value:
        raise HTTPException(status_code=404, detail="Valor adicional não encontrado")
    
    db.delete(value)
    db.commit()
    
    return {
        "status": "ok",
        "message": "Valor adicional removido com sucesso"
    }
