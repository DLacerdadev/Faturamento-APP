from sqlalchemy import Column, Integer, String, Float, Date, DateTime, ForeignKey
from datetime import datetime
from app.db import Base


class TrainingRecord(Base):
    """Lançamento de treinamento por funcionário (processo #7 do faturamento).

    Cada linha = um funcionário que fez um treinamento numa competência
    (mês/ano). Alimentada por lançamento manual e por importação de planilha.
    Vínculo do funcionário por matrícula (numcad) e/ou CPF; centro de custo
    (codccu/nome_ccu) resolvido do arquivo ou da varredura Senior. O treinamento
    pode referenciar o catálogo (training_catalog_id) mas o nome é sempre gravado
    (snapshot) para não depender do catálogo. Data e valor são opcionais."""
    __tablename__ = "training_records"

    id = Column(Integer, primary_key=True, index=True)
    competencia = Column(String(7))                      # 'YYYY-MM'
    codccu = Column(String(20))
    nome_ccu = Column(String(255), nullable=True)
    employee_numcad = Column(Integer, index=True, nullable=True)
    employee_nome = Column(String(255))
    cpf = Column(String(14), nullable=True)
    training_catalog_id = Column(Integer, ForeignKey("training_catalog.id"), nullable=True)
    treinamento_nome = Column(String(255))
    data_treinamento = Column(Date, nullable=True)
    quantidade = Column(Integer, default=1)
    valor = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "competencia": self.competencia,
            "codccu": self.codccu,
            "nome_ccu": self.nome_ccu,
            "employee_numcad": self.employee_numcad,
            "employee_nome": self.employee_nome,
            "cpf": self.cpf,
            "training_catalog_id": self.training_catalog_id,
            "treinamento_nome": self.treinamento_nome,
            "data_treinamento": str(self.data_treinamento) if self.data_treinamento else None,
            "quantidade": self.quantidade,
            "valor": self.valor,
        }
