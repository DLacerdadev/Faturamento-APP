from sqlalchemy import Column, Integer, String, Float, DateTime, UniqueConstraint
from datetime import datetime
from app.db import Base


class CCItemPrice(Base):
    """Preço de um produto (opcionalmente por variante/tamanho) específico de um
    centro de custo. Criado/atualizado quando o usuário reajusta o valor na
    validação de preço de uma compra. É o override do catálogo global:

        preço efetivo = preço do CC (se existir) → senão preço do catálogo global.

    Chave lógica: (codccu, produto_codigo, tamanho). `tamanho` vazio ("") quando
    o produto não tem variante.
    """
    __tablename__ = "cc_item_prices"
    __table_args__ = (
        UniqueConstraint("codccu", "produto_codigo", "tamanho", name="uq_cc_item_price"),
    )

    id = Column(Integer, primary_key=True, index=True)
    codccu = Column(String(20), nullable=False, index=True)
    produto_codigo = Column(String(40), nullable=False, index=True)
    tamanho = Column(String(20), nullable=False, default="")
    valor = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "codccu": self.codccu,
            "produto_codigo": self.produto_codigo,
            "tamanho": self.tamanho or "",
            "valor": self.valor,
        }
