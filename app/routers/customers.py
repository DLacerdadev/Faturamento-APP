from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
from pydantic import BaseModel
from app.db import get_db
from app.models.customer import Customer, ReportTemplate
from app.routers.auth import require_login
from app.models.user import User
import os
import shutil

router = APIRouter(prefix="/api/customers", tags=["customers"])

EXCEL_TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "excel_templates")
os.makedirs(EXCEL_TEMPLATES_DIR, exist_ok=True)

class CustomerCreate(BaseModel):
    name: str
    description: str | None = None
    email: str | None = None
    phone: str | None = None
    company: str | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    postal_code: str | None = None
    customer_type: str | None = None
    status: str = "active"
    total_revenue: float = 0.0

class CustomerUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    email: str | None = None
    phone: str | None = None
    company: str | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    postal_code: str | None = None
    customer_type: str | None = None
    status: str | None = None
    total_revenue: float | None = None

@router.get("/")
async def list_customers(skip: int = 0, limit: int = 100, user: User = Depends(require_login), db: Session = Depends(get_db)):
    customers = db.query(Customer).offset(skip).limit(limit).all()
    return [customer.to_dict() for customer in customers]

@router.get("/{customer_id}")
async def get_customer(customer_id: int, user: User = Depends(require_login), db: Session = Depends(get_db)):
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customer.to_dict()

@router.post("/")
async def create_customer(customer: CustomerCreate, user: User = Depends(require_login), db: Session = Depends(get_db)):
    db_customer = Customer(**customer.model_dump())
    db.add(db_customer)
    db.commit()
    db.refresh(db_customer)
    return db_customer.to_dict()

@router.put("/{customer_id}")
async def update_customer(customer_id: int, customer: CustomerUpdate, user: User = Depends(require_login), db: Session = Depends(get_db)):
    db_customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not db_customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    
    for key, value in customer.model_dump(exclude_unset=True).items():
        setattr(db_customer, key, value)
    
    db.commit()
    db.refresh(db_customer)
    return db_customer.to_dict()

@router.delete("/{customer_id}")
async def delete_customer(customer_id: int, user: User = Depends(require_login), db: Session = Depends(get_db)):
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    
    db.delete(customer)
    db.commit()
    return {"message": "Customer deleted successfully"}

@router.post("/{customer_id}/templates")
async def upload_template(
    customer_id: int,
    name: str = Form(...),
    file: UploadFile = File(...),
    user: User = Depends(require_login),
    db: Session = Depends(get_db)
):
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    
    if not file.filename.endswith(('.xlsx', '.xls')):
        raise HTTPException(status_code=400, detail="Only Excel files are allowed")
    
    safe_filename = f"customer_{customer_id}_{file.filename.replace(' ', '_')}"
    file_path = os.path.join(EXCEL_TEMPLATES_DIR, safe_filename)
    
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    template = ReportTemplate(
        customer_id=customer_id,
        name=name,
        file_path=file_path
    )
    db.add(template)
    db.commit()
    db.refresh(template)
    
    return template.to_dict()

@router.get("/{customer_id}/templates")
async def list_templates(customer_id: int, user: User = Depends(require_login), db: Session = Depends(get_db)):
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    
    templates = db.query(ReportTemplate).filter(ReportTemplate.customer_id == customer_id).all()
    return [t.to_dict() for t in templates]

@router.delete("/templates/{template_id}")
async def delete_template(template_id: int, user: User = Depends(require_login), db: Session = Depends(get_db)):
    template = db.query(ReportTemplate).filter(ReportTemplate.id == template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    
    if os.path.exists(template.file_path):
        os.remove(template.file_path)
    
    db.delete(template)
    db.commit()
    return {"message": "Template deleted successfully"}
