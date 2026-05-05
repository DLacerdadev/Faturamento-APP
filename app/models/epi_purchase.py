from sqlalchemy import Column, Integer, String, Float, Date, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from datetime import datetime, date
from app.db import Base


class EpiPurchasePackage(Base):
    __tablename__ = "epi_purchase_packages"

    id = Column(Integer, primary_key=True, index=True)
    empresa = Column(String(100), nullable=False, default="FEMSA")
    mes_ano = Column(Date, nullable=False)
    observacao = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    items = relationship("EpiPurchaseItem", back_populates="package", cascade="all, delete-orphan")
    documents = relationship("EpiPurchaseDocument", back_populates="package", cascade="all, delete-orphan")


class EpiPurchaseItem(Base):
    __tablename__ = "epi_purchase_items"

    id = Column(Integer, primary_key=True, index=True)
    package_id = Column(Integer, ForeignKey("epi_purchase_packages.id", ondelete="CASCADE"), nullable=False)
    descricao = Column(String(255), nullable=False)
    quantidade = Column(Integer, nullable=False, default=1)
    valor_unitario = Column(Float, nullable=False, default=0.0)
    valor_total = Column(Float, nullable=False, default=0.0)

    package = relationship("EpiPurchasePackage", back_populates="items")


class EpiPurchaseDocument(Base):
    __tablename__ = "epi_purchase_documents"

    id = Column(Integer, primary_key=True, index=True)
    package_id = Column(Integer, ForeignKey("epi_purchase_packages.id", ondelete="CASCADE"), nullable=False)
    original_filename = Column(String(500), nullable=False)
    stored_filename = Column(String(500), nullable=False)
    uploaded_at = Column(DateTime, default=datetime.utcnow)

    package = relationship("EpiPurchasePackage", back_populates="documents")
