from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime
from pathlib import Path
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from app.models.customer import Customer
from app.models.employee import Employee
from app.models.report import Report
from app.config import GENERATED_REPORTS_DIR
from typing import Dict, Any

async def generate_customer_report(
    db: Session,
    file_format: str = "xlsx",
    parameters: Dict[str, Any] = None
) -> str:
    params = parameters or {}
    
    query = db.query(Customer)
    
    if params.get('status'):
        query = query.filter(Customer.status == params['status'])
    if params.get('customer_type'):
        query = query.filter(Customer.customer_type == params['customer_type'])
    if params.get('city'):
        query = query.filter(Customer.city == params['city'])
    
    customers = query.all()
    
    data = [customer.to_dict() for customer in customers]
    df = pd.DataFrame(data)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"customer_report_{timestamp}.{file_format}"
    file_path = GENERATED_REPORTS_DIR / filename
    
    if file_format == "xlsx":
        await save_excel_report(df, file_path, "Customer Report")
    elif file_format == "csv":
        df.to_csv(file_path, index=False)
    else:
        raise ValueError(f"Unsupported format: {file_format}")
    
    return str(file_path)

async def generate_custom_report(
    db: Session,
    report_type: str,
    file_format: str = "xlsx",
    parameters: Dict[str, Any] = None
) -> str:
    params = parameters or {}
    
    if report_type == "employees":
        query = db.query(Employee)
        if params.get('department'):
            query = query.filter(Employee.department == params['department'])
        if params.get('status'):
            query = query.filter(Employee.status == params['status'])
        
        items = query.all()
        data = [item.to_dict() for item in items]
        report_name = "Employee Report"
        
    elif report_type == "summary":
        customer_count = db.query(Customer).count()
        active_customers = db.query(Customer).filter(Customer.status == "active").count()
        total_revenue = db.query(func.sum(Customer.total_revenue)).scalar() or 0
        
        data = [{
            'metric': 'Total Customers',
            'value': customer_count
        }, {
            'metric': 'Active Customers',
            'value': active_customers
        }, {
            'metric': 'Total Revenue',
            'value': total_revenue
        }]
        report_name = "Summary Report"
        
    else:
        raise ValueError(f"Unknown report type: {report_type}")
    
    df = pd.DataFrame(data)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{report_type}_report_{timestamp}.{file_format}"
    file_path = GENERATED_REPORTS_DIR / filename
    
    if file_format == "xlsx":
        await save_excel_report(df, file_path, report_name)
    elif file_format == "csv":
        df.to_csv(file_path, index=False)
    else:
        raise ValueError(f"Unsupported format: {file_format}")
    
    return str(file_path)

async def save_excel_report(df: pd.DataFrame, file_path: Path, sheet_name: str = "Report"):
    with pd.ExcelWriter(file_path, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name=sheet_name, index=False)
        
        workbook = writer.book
        worksheet = writer.sheets[sheet_name]
        
        header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
        header_font = Font(color="FFFFFF", bold=True)
        
        for cell in worksheet[1]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center")
        
        for column in worksheet.columns:
            max_length = 0
            column_letter = column[0].column_letter
            for cell in column:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            adjusted_width = min(max_length + 2, 50)
            worksheet.column_dimensions[column_letter].width = adjusted_width
