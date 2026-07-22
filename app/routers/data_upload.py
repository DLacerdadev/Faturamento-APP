from fastapi import APIRouter, Request, Depends, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from typing import Optional

from app.db import get_db
from app.session_manager import session_manager, validate_token
from app.models.customer import Customer
from app.services.audit import audit
from app.services.ingest import (
    ingest_employees,
    ingest_benefits,
    ingest_time_records,
    ingest_exam_records
)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def get_current_user(token: str, db: Session):
    # validate_token é função de MÓDULO (token, db) — SessionManager não tem esse método.
    if not token:
        return None
    return validate_token(token, db)


def _audit_user(token: str, db: Session):
    """Resolve o User do token do formulário SÓ pra auditoria — nunca levanta."""
    try:
        return validate_token(token, db) if token else None
    except Exception:
        return None


@router.get("/data-upload", response_class=HTMLResponse)
async def data_upload_page(request: Request, token: str = None, db: Session = Depends(get_db)):
    if not token or not get_current_user(token, db):
        return templates.TemplateResponse("login.html", {"request": request})
    
    customers = db.query(Customer).order_by(Customer.name).all()
    
    return templates.TemplateResponse("data_upload.html", {
        "request": request,
        "token": token,
        "customers": customers
    })


@router.post("/api/data-upload/employees")
async def upload_employees(
    request: Request,
    customer_id: int = Form(...),
    file: UploadFile = File(...),
    token: str = Form(...),
    db: Session = Depends(get_db)
):
    if not get_current_user(token, db):
        raise HTTPException(status_code=401, detail="Não autenticado")
    
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Cliente não encontrado")
    
    if not file.filename:
        raise HTTPException(status_code=400, detail="Arquivo não fornecido")
    
    file_extension = file.filename.split('.')[-1].lower()
    if file_extension not in ['xlsx', 'xls', 'csv']:
        raise HTTPException(status_code=400, detail="Formato de arquivo não suportado. Use Excel ou CSV.")
    
    content = await file.read()
    
    try:
        result = ingest_employees(db, customer_id, content, file_extension)
        audit(request, "importacao.dados", entidade="importacao",
              detalhe={"tipo": "funcionarios", "arquivo": file.filename,
                       "cliente_id": customer_id,
                       "n_registros": result.get("inserted", 0) + result.get("updated", 0)},
              user=_audit_user(token, db), db=db)
        return JSONResponse(content={
            "success": True,
            "message": f"Processamento concluído: {result['inserted']} inseridos, {result['updated']} atualizados",
            "data": result
        })
    except Exception as e:
        audit(request, "importacao.dados", entidade="importacao",
              detalhe={"tipo": "funcionarios", "arquivo": file.filename,
                       "cliente_id": customer_id, "erro": str(e)[:200]},
              user=_audit_user(token, db), db=db, status="erro")
        raise HTTPException(status_code=500, detail=f"Erro ao processar arquivo: {str(e)}")


@router.post("/api/data-upload/benefits")
async def upload_benefits(
    request: Request,
    customer_id: int = Form(...),
    mes_referencia: str = Form(...),
    file: UploadFile = File(...),
    token: str = Form(...),
    db: Session = Depends(get_db)
):
    if not get_current_user(token, db):
        raise HTTPException(status_code=401, detail="Não autenticado")
    
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Cliente não encontrado")
    
    if not file.filename:
        raise HTTPException(status_code=400, detail="Arquivo não fornecido")
    
    file_extension = file.filename.split('.')[-1].lower()
    if file_extension not in ['xlsx', 'xls', 'csv']:
        raise HTTPException(status_code=400, detail="Formato de arquivo não suportado. Use Excel ou CSV.")
    
    content = await file.read()
    
    try:
        result = ingest_benefits(db, customer_id, mes_referencia, content, file_extension)
        audit(request, "importacao.dados", entidade="importacao",
              detalhe={"tipo": "beneficios", "arquivo": file.filename,
                       "cliente_id": customer_id, "mes_referencia": mes_referencia,
                       "n_registros": result.get("inserted", 0)},
              user=_audit_user(token, db), db=db)
        return JSONResponse(content={
            "success": True,
            "message": f"Processamento concluído: {result['inserted']} inseridos, {result['skipped']} ignorados",
            "data": result
        })
    except Exception as e:
        audit(request, "importacao.dados", entidade="importacao",
              detalhe={"tipo": "beneficios", "arquivo": file.filename,
                       "cliente_id": customer_id, "mes_referencia": mes_referencia,
                       "erro": str(e)[:200]},
              user=_audit_user(token, db), db=db, status="erro")
        raise HTTPException(status_code=500, detail=f"Erro ao processar arquivo: {str(e)}")


@router.post("/api/data-upload/time-records")
async def upload_time_records(
    request: Request,
    customer_id: int = Form(...),
    mes_referencia: str = Form(...),
    file: UploadFile = File(...),
    token: str = Form(...),
    db: Session = Depends(get_db)
):
    if not get_current_user(token, db):
        raise HTTPException(status_code=401, detail="Não autenticado")
    
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Cliente não encontrado")
    
    if not file.filename:
        raise HTTPException(status_code=400, detail="Arquivo não fornecido")
    
    file_extension = file.filename.split('.')[-1].lower()
    if file_extension not in ['xlsx', 'xls', 'csv']:
        raise HTTPException(status_code=400, detail="Formato de arquivo não suportado. Use Excel ou CSV.")
    
    content = await file.read()
    
    try:
        result = ingest_time_records(db, customer_id, mes_referencia, content, file_extension)
        audit(request, "importacao.dados", entidade="importacao",
              detalhe={"tipo": "ponto", "arquivo": file.filename,
                       "cliente_id": customer_id, "mes_referencia": mes_referencia,
                       "n_registros": result.get("inserted", 0)},
              user=_audit_user(token, db), db=db)
        return JSONResponse(content={
            "success": True,
            "message": f"Processamento concluído: {result['inserted']} inseridos, {result['skipped']} ignorados",
            "data": result
        })
    except Exception as e:
        audit(request, "importacao.dados", entidade="importacao",
              detalhe={"tipo": "ponto", "arquivo": file.filename,
                       "cliente_id": customer_id, "mes_referencia": mes_referencia,
                       "erro": str(e)[:200]},
              user=_audit_user(token, db), db=db, status="erro")
        raise HTTPException(status_code=500, detail=f"Erro ao processar arquivo: {str(e)}")


@router.post("/api/data-upload/exams")
async def upload_exams(
    request: Request,
    customer_id: int = Form(...),
    file: UploadFile = File(...),
    token: str = Form(...),
    db: Session = Depends(get_db)
):
    if not get_current_user(token, db):
        raise HTTPException(status_code=401, detail="Não autenticado")
    
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Cliente não encontrado")
    
    if not file.filename:
        raise HTTPException(status_code=400, detail="Arquivo não fornecido")
    
    file_extension = file.filename.split('.')[-1].lower()
    if file_extension not in ['xlsx', 'xls', 'csv']:
        raise HTTPException(status_code=400, detail="Formato de arquivo não suportado. Use Excel ou CSV.")
    
    content = await file.read()
    
    try:
        result = ingest_exam_records(db, customer_id, content, file_extension)
        audit(request, "importacao.dados", entidade="importacao",
              detalhe={"tipo": "exames", "arquivo": file.filename,
                       "cliente_id": customer_id,
                       "n_registros": result.get("inserted", 0)},
              user=_audit_user(token, db), db=db)
        return JSONResponse(content={
            "success": True,
            "message": f"Processamento concluído: {result['inserted']} inseridos, {result['skipped']} ignorados",
            "data": result
        })
    except Exception as e:
        audit(request, "importacao.dados", entidade="importacao",
              detalhe={"tipo": "exames", "arquivo": file.filename,
                       "cliente_id": customer_id, "erro": str(e)[:200]},
              user=_audit_user(token, db), db=db, status="erro")
        raise HTTPException(status_code=500, detail=f"Erro ao processar arquivo: {str(e)}")
