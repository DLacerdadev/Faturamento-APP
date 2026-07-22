from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.types import JSON
from datetime import datetime
from app.db import Base


class AuditLog(Base):
    """Trilha de auditoria imutável das ações do sistema.

    Cada linha é um snapshot do momento da ação: quem (user_id/username/role,
    copiados do usuário na hora — não FK obrigatória, pois o usuário pode ser
    removido depois), o quê (acao no padrão 'entidade.verbo', ex.:
    'pedido.confirmar'), sobre qual registro (entidade + entidade_id) e um
    'detalhe' JSON pequeno (ex.: {"alteracoes": {campo: {"de": x, "para": y}}}).

    NUNCA gravar senhas/hashes/tokens no detalhe; dados pessoais devem ser
    referenciados por id/matrícula. Não há endpoint de update/delete —
    registros são imutáveis por contrato.

    Escrita SEMPRE via app.services.audit.audit() (sessão própria, nunca
    levanta exceção).
    """
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True)
    ts = Column(DateTime, default=datetime.utcnow, index=True)
    user_id = Column(Integer, nullable=True)          # snapshot; sem FK — usuário pode ser removido
    username = Column(String(200), nullable=True)
    role = Column(String(20), nullable=True)
    acao = Column(String(60), nullable=False, index=True)   # padrão 'entidade.verbo' (ex.: 'auth.login')
    entidade = Column(String(60), nullable=True, index=True)
    entidade_id = Column(String(40), nullable=True)
    detalhe = Column(JSON, nullable=True)             # JSON pequeno; nunca senhas/tokens/dados pessoais crus
    ip = Column(String(64), nullable=True)
    status = Column(String(20), default="ok")         # 'ok' | 'erro' | 'negado'

    def to_dict(self):
        return {
            "id": self.id,
            "ts": self.ts.isoformat() if self.ts else None,
            "user_id": self.user_id,
            "username": self.username,
            "role": self.role,
            "acao": self.acao,
            "entidade": self.entidade,
            "entidade_id": self.entidade_id,
            "detalhe": self.detalhe,
            "ip": self.ip,
            "status": self.status,
        }
