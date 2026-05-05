from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Float, Text
from sqlalchemy.orm import relationship
from datetime import datetime
from app.db import Base


class TimeRecord(Base):
    __tablename__ = "time_records"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    mes_referencia = Column(String(7), nullable=False, index=True)
    data = Column(String(10), nullable=False)
    horas_trabalhadas = Column(Float, default=0.0)
    horas_extras = Column(Float, default=0.0)
    horas_noturnas = Column(Float, default=0.0)
    faltas = Column(Float, default=0.0)
    atrasos = Column(Float, default=0.0)
    adicional_noturno = Column(Float, default=0.0)
    dsr = Column(Float, default=0.0)
    banco_horas = Column(Float, default=0.0)
    observacoes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    customer = relationship("Customer", back_populates="time_records")
    employee = relationship("Employee", back_populates="time_records")

    def to_dict(self):
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "employee_id": self.employee_id,
            "mes_referencia": self.mes_referencia,
            "data": self.data,
            "horas_trabalhadas": self.horas_trabalhadas,
            "horas_extras": self.horas_extras,
            "horas_noturnas": self.horas_noturnas,
            "faltas": self.faltas,
            "atrasos": self.atrasos,
            "adicional_noturno": self.adicional_noturno,
            "dsr": self.dsr,
            "banco_horas": self.banco_horas,
            "observacoes": self.observacoes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None
        }

    def __repr__(self):
        return f"<TimeRecord {self.data} - {self.horas_trabalhadas}h>"
