"""Histórico do assistente de IA do faturamento (spec 010).

Duas entidades:
  - AiChatSession: uma conversa (ligada a um usuário e, opcionalmente, ao
    BillingModel que estava aberto). Sem PII: guarda só quem abriu (user_id/
    username snapshot) e o modelo.
  - AiChatMessage: cada turno (role=user|assistant), o texto e — nas respostas
    do assistente — a lista de `ops` produzidas (JSON, só estrutura, sem PII).

NUNCA persistir aqui CPF/nome/valores individuais. O snapshot enviado ao LLM já
é sanitizado (ai_pii); as mensagens gravadas são o texto do gestor e a resposta
estrutural da IA.

Migração: tabelas criadas por `Base.metadata.create_all` (novas tabelas — não
exigem ALTER). A orquestradora registra os imports em app/db.py (INTEGRATION).
"""
from datetime import datetime

from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.types import JSON
from sqlalchemy.orm import relationship

from app.db import Base


class AiChatSession(Base):
    __tablename__ = "ai_chat_sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=True, index=True)   # snapshot — sem FK viva
    username = Column(String(200), nullable=True)
    model_id = Column(Integer, nullable=True, index=True)  # BillingModel aberto (opcional)
    titulo = Column(String(200), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    messages = relationship(
        "AiChatMessage",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="AiChatMessage.id",
    )

    def to_dict(self, with_messages: bool = False):
        d = {
            "id": self.id,
            "user_id": self.user_id,
            "username": self.username,
            "model_id": self.model_id,
            "titulo": self.titulo,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        if with_messages:
            d["messages"] = [m.to_dict() for m in (self.messages or [])]
        return d


class AiChatMessage(Base):
    __tablename__ = "ai_chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("ai_chat_sessions.id"), nullable=False, index=True)
    role = Column(String(20), nullable=False)   # 'user' | 'assistant'
    content = Column(Text, nullable=True)        # texto da mensagem/resposta
    ops = Column(JSON, nullable=True)            # ops produzidas (respostas), sem PII
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    session = relationship("AiChatSession", back_populates="messages")

    def to_dict(self):
        return {
            "id": self.id,
            "session_id": self.session_id,
            "role": self.role,
            "content": self.content,
            "ops": self.ops or [],
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
