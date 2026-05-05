from sqlalchemy import Column, Integer, String, Float, Date, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
from app.db import Base


class ExamRecord(Base):
    """Modelo de registro de exames para o sistema original de gerenciamento"""
    __tablename__ = "exam_records"
    
    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=True)
    cpf = Column(String(14), index=True)
    matricula = Column(String(50))
    nome_funcionario = Column(String(255))
    tipo_exame = Column(String(100))
    data_exame = Column(Date)
    data_validade = Column(Date)
    status = Column(String(50))
    resultado = Column(String(100))
    clinica = Column(String(255))
    observacoes = Column(String(500))
    created_at = Column(DateTime, default=datetime.utcnow)
    
    customer = relationship("Customer")
    
    def to_dict(self):
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "employee_id": self.employee_id,
            "cpf": self.cpf,
            "matricula": self.matricula,
            "nome_funcionario": self.nome_funcionario,
            "tipo_exame": self.tipo_exame,
            "data_exame": str(self.data_exame) if self.data_exame else None,
            "data_validade": str(self.data_validade) if self.data_validade else None,
            "status": self.status,
            "resultado": self.resultado,
            "clinica": self.clinica,
            "observacoes": self.observacoes,
            "created_at": str(self.created_at) if self.created_at else None,
        }
