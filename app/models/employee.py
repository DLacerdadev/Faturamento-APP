from sqlalchemy import Column, Integer, String, Float, Date, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
from app.db import Base


class Employee(Base):
    """Modelo de funcionário para o sistema original de gerenciamento"""
    __tablename__ = "employees"
    
    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    cpf = Column(String(14), index=True)
    nome = Column(String(255), nullable=False)
    matricula = Column(String(50))
    centro_custo = Column(String(100))
    cargo = Column(String(255))
    departamento = Column(String(255))
    data_admissao = Column(Date)
    data_nascimento = Column(Date)
    email = Column(String(255))
    telefone = Column(String(50))
    endereco = Column(String(500))
    cidade = Column(String(100))
    estado = Column(String(50))
    cep = Column(String(20))
    status = Column(String(50), default="ativo")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    customer = relationship("Customer", back_populates="employees")
    benefit_records = relationship("BenefitRecord", back_populates="employee")
    time_records = relationship("TimeRecord", back_populates="employee")
    
    def to_dict(self):
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "cpf": self.cpf,
            "nome": self.nome,
            "matricula": self.matricula,
            "centro_custo": self.centro_custo,
            "cargo": self.cargo,
            "departamento": self.departamento,
            "data_admissao": str(self.data_admissao) if self.data_admissao else None,
            "data_nascimento": str(self.data_nascimento) if self.data_nascimento else None,
            "email": self.email,
            "telefone": self.telefone,
            "endereco": self.endereco,
            "cidade": self.cidade,
            "estado": self.estado,
            "cep": self.cep,
            "status": self.status,
            "created_at": str(self.created_at) if self.created_at else None,
        }
