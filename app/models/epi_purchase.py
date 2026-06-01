from sqlalchemy import Column, Integer, String, Float, Date, DateTime, ForeignKey, Text, Boolean, UniqueConstraint
from sqlalchemy.orm import relationship
from datetime import datetime, date
from app.db import Base


class EpiCatalog(Base):
    """Catálogo mestre de EPIs (feature 002). Soft-delete via ativo=False."""
    __tablename__ = "epi_catalog"

    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String(200), nullable=False)
    ativo = Column(Boolean, nullable=False, default=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    sizes = relationship("EpiCatalogSize", back_populates="catalog_entry", cascade="all, delete-orphan")


class EpiCatalogSize(Base):
    """Tamanho + valor de um EPI cadastrado (feature 002)."""
    __tablename__ = "epi_catalog_sizes"
    __table_args__ = (UniqueConstraint("epi_id", "tamanho", name="uq_epi_size"),)

    id = Column(Integer, primary_key=True, index=True)
    epi_id = Column(Integer, ForeignKey("epi_catalog.id", ondelete="CASCADE"), nullable=False, index=True)
    tamanho = Column(String(20), nullable=False)
    valor = Column(Float, nullable=False)

    catalog_entry = relationship("EpiCatalog", back_populates="sizes")


class EpiPurchasePackage(Base):
    __tablename__ = "epi_purchase_packages"

    id = Column(Integer, primary_key=True, index=True)
    empresa = Column(String(100), nullable=False, default="FEMSA")
    mes_ano = Column(Date, nullable=False)
    observacao = Column(Text, nullable=True)
    codccu = Column(String(20), nullable=True, index=True)
    # Feature 002: snapshot do usuário que salvou + totais agregados persistidos
    solicitante_nome = Column(String(200), nullable=True)
    quantidade_total_geral = Column(Integer, nullable=True)
    valor_total_compra_geral = Column(Float, nullable=True)
    solicitacao_filename = Column(String(500), nullable=True)
    solicitacao_generated_at = Column(DateTime, nullable=True)
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
    employee_numcad = Column(Integer, nullable=True, index=True)
    employee_nome = Column(String(200), nullable=True)
    # Feature 002: vínculo com catálogo + snapshot do valor de catálogo
    epi_id = Column(Integer, ForeignKey("epi_catalog.id"), nullable=True, index=True)
    tamanho = Column(String(20), nullable=True)
    quantidade_por_funcionario = Column(Integer, nullable=True)
    # Quantidade TOTAL do item na compra (replicada em cada linha do cartesiano).
    # Permite override manual pelo usuário. Quando NULL, calcular como qpf × n_funcionários.
    quantidade_total_item = Column(Integer, nullable=True)
    valor_unitario_catalogo = Column(Float, nullable=True)

    package = relationship("EpiPurchasePackage", back_populates="items")
    catalog_entry = relationship("EpiCatalog")


class EpiPurchaseDocument(Base):
    __tablename__ = "epi_purchase_documents"

    id = Column(Integer, primary_key=True, index=True)
    package_id = Column(Integer, ForeignKey("epi_purchase_packages.id", ondelete="CASCADE"), nullable=False)
    original_filename = Column(String(500), nullable=False)
    stored_filename = Column(String(500), nullable=False)
    uploaded_at = Column(DateTime, default=datetime.utcnow)

    package = relationship("EpiPurchasePackage", back_populates="documents")
