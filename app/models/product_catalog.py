from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime
from datetime import datetime
from app.db import Base


class ProductCatalog(Base):
    """Catálogo de produtos (uniformes, EPIs, equipamentos) importado do TOTVS.
    Preço é editável depois (preenchido na tela)."""
    __tablename__ = "product_catalog"

    id = Column(Integer, primary_key=True, index=True)
    codigo = Column(String(40), unique=True, nullable=False, index=True)
    descricao = Column(String(400))
    grupo = Column(String(20), index=True)        # código do grupo TOTVS (ex.: 0003)
    grupo_nome = Column(String(120))              # ex.: EPI, Uniformes, Ferramentas
    categoria = Column(String(30), index=True)    # uniforme | epi | equipamento
    unidade = Column(String(20))
    preco = Column(Float)                         # editar depois (None = sem preço)
    ativo = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "codigo": self.codigo, "descricao": self.descricao,
            "grupo": self.grupo, "grupo_nome": self.grupo_nome, "categoria": self.categoria,
            "unidade": self.unidade, "preco": self.preco, "ativo": bool(self.ativo),
        }
