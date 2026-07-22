from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.types import JSON
from sqlalchemy.orm import relationship
from datetime import datetime
from app.db import Base


class ExamCatalog(Base):
    """Catálogo de exames: nome de exibição, sinônimos (para identificação no
    upload) e a coluna correspondente na página de Exames (MedicalExam)."""
    __tablename__ = "exam_catalog"

    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String(255), nullable=False)
    coluna = Column(String(50), unique=True, nullable=False)   # ex.: 'clinic', 'audio'
    sinonimos = Column(JSON, default=list)                      # lista normalizada p/ casar nomes
    ativo = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "nome": self.nome, "coluna": self.coluna,
            "sinonimos": self.sinonimos or [], "ativo": bool(self.ativo),
        }


class PriceModel(Base):
    """Modelo de preço: uma tabela de preços nomeada (clínica/contrato). No
    upload escolhe-se o modelo e os exames identificados recebem o preço dele."""
    __tablename__ = "price_models"

    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String(255), nullable=False)
    descricao = Column(String(500))
    ativo = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    items = relationship("PriceModelItem", back_populates="price_model",
                         cascade="all, delete-orphan")

    def to_dict(self, with_items=False):
        d = {
            "id": self.id, "nome": self.nome, "descricao": self.descricao,
            "ativo": bool(self.ativo),
        }
        if with_items:
            d["items"] = [i.to_dict() for i in self.items]
        return d


class PriceModelItem(Base):
    """Preço de um exame do catálogo dentro de um modelo."""
    __tablename__ = "price_model_items"
    __table_args__ = (UniqueConstraint("price_model_id", "exam_catalog_id", name="uq_model_exam"),)

    id = Column(Integer, primary_key=True, index=True)
    price_model_id = Column(Integer, ForeignKey("price_models.id"), nullable=False)
    exam_catalog_id = Column(Integer, ForeignKey("exam_catalog.id"), nullable=False)
    preco = Column(Float, default=0.0)

    price_model = relationship("PriceModel", back_populates="items")
    exam_catalog = relationship("ExamCatalog")

    def to_dict(self):
        return {
            "id": self.id,
            "price_model_id": self.price_model_id,
            "exam_catalog_id": self.exam_catalog_id,
            "coluna": self.exam_catalog.coluna if self.exam_catalog else None,
            "nome": self.exam_catalog.nome if self.exam_catalog else None,
            "preco": self.preco or 0.0,
        }
