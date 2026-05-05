from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Float, Text
from sqlalchemy.orm import relationship
from datetime import datetime
from app.db import Base


class BenefitRecord(Base):
    __tablename__ = "benefit_records"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    mes_referencia = Column(String(7), nullable=False, index=True)
    tipo_beneficio = Column(String(100), nullable=False)
    descricao = Column(Text)
    valor = Column(Float, default=0.0)
    quantidade = Column(Float, default=1.0)
    valor_total = Column(Float, default=0.0)
    status = Column(String(20), default="ativo")
    observacoes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    customer = relationship("Customer", back_populates="benefit_records")
    employee = relationship("Employee", back_populates="benefit_records")

    def to_dict(self):
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "employee_id": self.employee_id,
            "mes_referencia": self.mes_referencia,
            "tipo_beneficio": self.tipo_beneficio,
            "descricao": self.descricao,
            "valor": self.valor,
            "quantidade": self.quantidade,
            "valor_total": self.valor_total,
            "status": self.status,
            "observacoes": self.observacoes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None
        }

    def __repr__(self):
        return f"<BenefitRecord {self.tipo_beneficio} - {self.mes_referencia}>"
