"""Cálculo de preço efetivo de um produto para um centro de custo.

Regra: preço efetivo = preço do CC (CCItemPrice, se existir para o produto/variante)
→ senão preço do catálogo global (ProductCatalog.preco).

Usado no fluxo de Compras/Entregas (validação de preço) e, futuramente, no
lançamento no faturamento.
"""
from typing import Optional
from sqlalchemy.orm import Session

from app.models.cc_item_price import CCItemPrice
from app.models.product_catalog import ProductCatalog


def catalog_price(db: Session, produto_codigo: str) -> Optional[float]:
    """Preço do catálogo global (ou None se não cadastrado/zero)."""
    if not produto_codigo:
        return None
    p = db.query(ProductCatalog).filter(ProductCatalog.codigo == str(produto_codigo)).first()
    if p and p.preco:
        return float(p.preco)
    return None


def cc_price(db: Session, codccu: str, produto_codigo: str, tamanho: str = "") -> Optional[float]:
    """Preço específico do CC para o produto/variante (ou None)."""
    if not codccu or not produto_codigo:
        return None
    row = db.query(CCItemPrice).filter(
        CCItemPrice.codccu == str(codccu),
        CCItemPrice.produto_codigo == str(produto_codigo),
        CCItemPrice.tamanho == (tamanho or ""),
    ).first()
    return float(row.valor) if row else None


def effective_price(db: Session, codccu: str, produto_codigo: str, tamanho: str = "") -> Optional[float]:
    """Preço efetivo = preço do CC (se existir) → senão catálogo global. None se nenhum."""
    v = cc_price(db, codccu, produto_codigo, tamanho)
    if v is not None:
        return v
    return catalog_price(db, produto_codigo)


def set_cc_price(db: Session, codccu: str, produto_codigo: str, tamanho: str, valor: float) -> CCItemPrice:
    """Cria/atualiza o preço do CC para o produto/variante (upsert)."""
    tam = tamanho or ""
    row = db.query(CCItemPrice).filter(
        CCItemPrice.codccu == str(codccu),
        CCItemPrice.produto_codigo == str(produto_codigo),
        CCItemPrice.tamanho == tam,
    ).first()
    if row:
        row.valor = float(valor)
    else:
        row = CCItemPrice(codccu=str(codccu), produto_codigo=str(produto_codigo),
                          tamanho=tam, valor=float(valor))
        db.add(row)
    db.commit()
    db.refresh(row)
    return row
