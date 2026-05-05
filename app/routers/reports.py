from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
from datetime import datetime
from app.db import get_db
from app.models.report import Report
from app.services.report_generator import generate_customer_report, generate_custom_report

router = APIRouter(prefix="/api/reports", tags=["reports"])

class ReportRequest(BaseModel):
    name: str
    report_type: str
    file_format: str = "xlsx"
    description: str | None = None
    parameters: dict | None = None

@router.get("/")
async def list_reports(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    reports = db.query(Report).order_by(Report.created_at.desc()).offset(skip).limit(limit).all()
    return [report.to_dict() for report in reports]

@router.get("/{report_id}")
async def get_report(report_id: int, db: Session = Depends(get_db)):
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return report.to_dict()

@router.post("/generate")
async def generate_report(request: ReportRequest, db: Session = Depends(get_db)):
    report = None
    try:
        report = Report(
            name=request.name,
            report_type=request.report_type,
            file_format=request.file_format,
            description=request.description,
            status="processing",
            parameters=str(request.parameters) if request.parameters else None
        )
        db.add(report)
        db.commit()
        db.refresh(report)
        
        if request.report_type == "customers":
            file_path = await generate_customer_report(
                db=db,
                file_format=request.file_format,
                parameters=request.parameters or {}
            )
        else:
            file_path = await generate_custom_report(
                db=db,
                report_type=request.report_type,
                file_format=request.file_format,
                parameters=request.parameters or {}
            )
        
        report.file_path = file_path
        report.status = "completed"
        report.completed_at = datetime.utcnow()
        db.commit()
        db.refresh(report)
        
        return report.to_dict()
        
    except Exception as e:
        if report is not None:
            report.status = "failed"
            db.commit()
        raise HTTPException(status_code=500, detail=f"Error generating report: {str(e)}")

@router.get("/{report_id}/download")
async def download_report(report_id: int, db: Session = Depends(get_db)):
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    
    if not report.file_path or report.status != "completed":
        raise HTTPException(status_code=400, detail="Report not available for download")
    
    return FileResponse(
        path=report.file_path,
        filename=f"{report.name}.{report.file_format}",
        media_type="application/octet-stream"
    )

@router.delete("/{report_id}")
async def delete_report(report_id: int, db: Session = Depends(get_db)):
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    
    db.delete(report)
    db.commit()
    return {"message": "Report deleted successfully"}
