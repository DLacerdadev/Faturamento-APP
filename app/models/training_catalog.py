from sqlalchemy import Column, Integer, String, Float, Boolean, Text, DateTime
from datetime import datetime
from app.db import Base


class TrainingCatalog(Base):
    """Catálogo de treinamentos (cadastro manual). Mesmo padrão dos demais
    catálogos: nome + valor editáveis na tela. Carga horária e validade são
    opcionais (comuns em treinamentos de NR). Soft-delete via ativo=False."""
    __tablename__ = "training_catalog"

    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String(255), nullable=False)
    valor = Column(Float, nullable=True)               # R$ por treinamento
    carga_horaria = Column(Float, nullable=True)       # horas
    validade_meses = Column(Integer, nullable=True)    # periodicidade/validade em meses
    ativo = Column(Boolean, nullable=False, default=True, index=True)
    observacao = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "nome": self.nome,
            "valor": self.valor,
            "carga_horaria": self.carga_horaria,
            "validade_meses": self.validade_meses,
            "ativo": bool(self.ativo),
            "observacao": self.observacao,
        }
