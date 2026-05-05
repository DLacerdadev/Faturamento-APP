from sqlalchemy import Column, Integer, String, DateTime, Float, Text, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
from app.db import Base

class Customer(Base):
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, index=True)
    description = Column(Text)
    email = Column(String, unique=True, index=True)
    phone = Column(String)
    company = Column(String)
    address = Column(String)
    city = Column(String)
    state = Column(String)
    country = Column(String)
    postal_code = Column(String)
    customer_type = Column(String)
    status = Column(String, default="active")
    total_revenue = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    report_templates = relationship("ReportTemplate", back_populates="customer", cascade="all, delete-orphan")
    employees = relationship("Employee", back_populates="customer", cascade="all, delete-orphan")
    benefit_records = relationship("BenefitRecord", back_populates="customer", cascade="all, delete-orphan")
    time_records = relationship("TimeRecord", back_populates="customer", cascade="all, delete-orphan")
    exam_records = relationship("ExamRecord", back_populates="customer", cascade="all, delete-orphan")
    
    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "email": self.email,
            "phone": self.phone,
            "company": self.company,
            "address": self.address,
            "city": self.city,
            "state": self.state,
            "country": self.country,
            "postal_code": self.postal_code,
            "customer_type": self.customer_type,
            "status": self.status,
            "total_revenue": self.total_revenue,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None
        }

class ReportTemplate(Base):
    __tablename__ = "report_templates"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    name = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    customer = relationship("Customer", back_populates="report_templates")
    
    def to_dict(self):
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "name": self.name,
            "file_path": self.file_path,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }
