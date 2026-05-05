from sqlalchemy import Column, Integer, String, Float, Date, DateTime, ForeignKey, Enum as SQLEnum
from sqlalchemy.orm import relationship
from datetime import datetime
import enum
from app.db import Base


class PayrollDirection(enum.Enum):
    CREDIT = "credit"
    DEBIT = "debit"


class BillingStatus(enum.Enum):
    DRAFT = "draft"
    PROCESSING = "processing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class Company(Base):
    __tablename__ = "billing_companies"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    cnpj_femsa = Column(String(20), unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    units = relationship("Unit", back_populates="company")
    contracts = relationship("EmploymentContract", back_populates="company")
    billing_periods = relationship("BillingPeriod", back_populates="company")


class Unit(Base):
    __tablename__ = "billing_units"
    
    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("billing_companies.id"), nullable=False)
    cnpj_unidade = Column(String(20), unique=True, nullable=False, index=True)
    nome_unidade = Column(String(255), nullable=False)
    centro_custo_femsa = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)
    
    company = relationship("Company", back_populates="units")
    contracts = relationship("EmploymentContract", back_populates="unit")
    payroll_items = relationship("PayrollItem", back_populates="unit")
    exam_records = relationship("BillingExamRecord", back_populates="unit")


class BillingEmployee(Base):
    __tablename__ = "billing_employees"
    
    id = Column(Integer, primary_key=True, index=True)
    cpf = Column(String(14), unique=True, nullable=False, index=True)
    nome = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    contracts = relationship("EmploymentContract", back_populates="employee")
    payroll_items = relationship("PayrollItem", back_populates="employee")
    exam_records = relationship("BillingExamRecord", back_populates="employee")


class EmploymentContract(Base):
    __tablename__ = "billing_employment_contracts"
    
    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("billing_employees.id"), nullable=False)
    company_id = Column(Integer, ForeignKey("billing_companies.id"), nullable=False)
    unit_id = Column(Integer, ForeignKey("billing_units.id"), nullable=False)
    cargo = Column(String(255))
    funcao = Column(String(255))
    salario_base = Column(Float, default=0.0)
    data_admissao = Column(Date)
    data_demissao = Column(Date)
    data_termino_contrato = Column(Date)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    employee = relationship("BillingEmployee", back_populates="contracts")
    company = relationship("Company", back_populates="contracts")
    unit = relationship("Unit", back_populates="contracts")
    payroll_items = relationship("PayrollItem", back_populates="contract")


class BillingPeriod(Base):
    __tablename__ = "billing_periods"
    
    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("billing_companies.id"), nullable=False)
    mes_referencia = Column(String(7), nullable=False)
    status = Column(SQLEnum(BillingStatus), default=BillingStatus.DRAFT)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    company = relationship("Company", back_populates="billing_periods")
    payroll_items = relationship("PayrollItem", back_populates="billing_period")
    exam_records = relationship("BillingExamRecord", back_populates="billing_period")


class PayrollItemType(Base):
    __tablename__ = "billing_payroll_item_types"
    
    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(50), unique=True, nullable=False, index=True)
    description = Column(String(255), nullable=False)
    group = Column(String(100))
    direction = Column(SQLEnum(PayrollDirection), default=PayrollDirection.CREDIT)
    
    payroll_items = relationship("PayrollItem", back_populates="payroll_item_type")


class PayrollItem(Base):
    __tablename__ = "billing_payroll_items"
    
    id = Column(Integer, primary_key=True, index=True)
    billing_period_id = Column(Integer, ForeignKey("billing_periods.id"), nullable=False)
    employee_id = Column(Integer, ForeignKey("billing_employees.id"), nullable=False)
    contract_id = Column(Integer, ForeignKey("billing_employment_contracts.id"))
    unit_id = Column(Integer, ForeignKey("billing_units.id"))
    payroll_item_type_id = Column(Integer, ForeignKey("billing_payroll_item_types.id"), nullable=False)
    quantity = Column(Float, default=0.0)
    amount = Column(Float, default=0.0)
    source_column = Column(String(100))
    notes = Column(String(500))
    created_at = Column(DateTime, default=datetime.utcnow)
    
    billing_period = relationship("BillingPeriod", back_populates="payroll_items")
    employee = relationship("BillingEmployee", back_populates="payroll_items")
    contract = relationship("EmploymentContract", back_populates="payroll_items")
    unit = relationship("Unit", back_populates="payroll_items")
    payroll_item_type = relationship("PayrollItemType", back_populates="payroll_items")


class BillingExamRecord(Base):
    __tablename__ = "billing_exam_records"
    
    id = Column(Integer, primary_key=True, index=True)
    billing_period_id = Column(Integer, ForeignKey("billing_periods.id"), nullable=False)
    unit_id = Column(Integer, ForeignKey("billing_units.id"))
    employee_id = Column(Integer, ForeignKey("billing_employees.id"))
    tipo = Column(String(100))
    exame = Column(String(255))
    data_pedido = Column(Date)
    data_exame = Column(Date)
    data_inativacao = Column(Date)
    valor_cobrar = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    billing_period = relationship("BillingPeriod", back_populates="exam_records")
    unit = relationship("Unit", back_populates="exam_records")
    employee = relationship("BillingEmployee", back_populates="exam_records")


class AdditionalValue(Base):
    __tablename__ = "billing_additional_values"
    
    id = Column(Integer, primary_key=True, index=True)
    codccu = Column(String(50), nullable=False, index=True)
    nome_ccu = Column(String(255))
    descricao = Column(String(255))
    valor = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
