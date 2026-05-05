from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_db
from app.services.ingest import process_file_upload
from app.services.normalize import normalize_customer_data
from app.models.customer import Customer
import json

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

@router.post("/customers")
async def upload_customer_file(
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    try:
        raw_data = await process_file_upload(file)
        
        normalized_data = normalize_customer_data(raw_data)
        
        created_count = 0
        updated_count = 0
        errors = []
        
        for customer_data in normalized_data:
            try:
                existing_customer = db.query(Customer).filter(
                    Customer.email == customer_data.get("email")
                ).first()
                
                if existing_customer:
                    for key, value in customer_data.items():
                        setattr(existing_customer, key, value)
                    updated_count += 1
                else:
                    new_customer = Customer(**customer_data)
                    db.add(new_customer)
                    created_count += 1
                
            except Exception as e:
                errors.append({"data": customer_data, "error": str(e)})
        
        db.commit()
        
        return {
            "status": "success",
            "created": created_count,
            "updated": updated_count,
            "errors": errors,
            "total_processed": created_count + updated_count
        }
        
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error processing file: {str(e)}")

@router.post("/generic")
async def upload_generic_file(file: UploadFile = File(...)):
    try:
        raw_data = await process_file_upload(file)
        
        return {
            "status": "success",
            "filename": file.filename,
            "rows_processed": len(raw_data) if isinstance(raw_data, list) else 1,
            "preview": raw_data[:5] if isinstance(raw_data, list) and len(raw_data) > 5 else raw_data
        }
        
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error processing file: {str(e)}")
