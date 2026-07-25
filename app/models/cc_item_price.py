from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, UniqueConstraint
from datetime import datetime
from app.db import Base


class CCItemPrice(Base):
    """Preço de um produto (opcionalmente por variante/tamanho) específico de um
    centro de custo. Criado/atualizado quando o usuário reajusta o valor na
    validação de preço de uma compra. É o override do catálogo global:

        preço efetivo = preço do CC (se existir) → senão preço do catálogo global.

    Chave lógica: (codccu, produto_codigo, tamanho). `tamanho` vazio ("") quando
    o produto não tem variante.

    Feature 008 (EPI automático via TOTVS): o valor de EPI vem do TOTVS por
    centro de custo (último `C7_PRECO` por produto × `C7_CC`). A rotina diária
    (`price_autosync.py`) faz upsert AQUI. A trava manual vive nesta linha:
        * `is_manual_price=True`  → editado manualmente; a rotina NÃO sobrescreve.
        * `is_manual_price=False` → automático; recebe o valor do TOTVS.
    `last_auto_sync` registra quando a rotina tocou (ou tentou tocar) a linha.
    O isolamento por CCU é garantido porque a trava é POR LINHA (codccu,produto):
    manual no CCU A não afeta o automático no CCU B.
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
    # Feature 008: trava de preço manual (default False = automático via TOTVS).
    is_manual_price = Column(Boolean, nullable=False, default=False)
    # Feature 008: timestamp da última sincronização automática do TOTVS.
    last_auto_sync = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "codccu": self.codccu,
            "produto_codigo": self.produto_codigo,
            "tamanho": self.tamanho or "",
            "valor": self.valor,
            "is_manual_price": bool(self.is_manual_price),
            "last_auto_sync": self.last_auto_sync.isoformat() if self.last_auto_sync else None,
        }
