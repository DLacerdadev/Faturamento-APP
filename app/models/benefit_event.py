from sqlalchemy import Column, Integer, String, Boolean, DateTime
from datetime import datetime
from app.db import Base


class BenefitEvent(Base):
    """Mapeamento configurável de EVENTO da folha Senior (CODEVE) -> coluna do
    faturamento FEMSA. Permite ligar/desligar e ajustar sem mexer em código.

    'ativo' = entra na exportação. Eventos pendentes de confirmação de valor
    ficam ativo=False até o usuário validar.
    """
    __tablename__ = "benefit_events"

    id = Column(Integer, primary_key=True, index=True)
    codeve = Column(Integer, unique=True, nullable=False, index=True)  # código do evento na Senior
    descricao = Column(String(255))                                    # descrição (R008EVC/DESEVE)
    coluna_femsa = Column(String(120), nullable=False)                 # coluna-alvo no faturamento
    grupo = Column(String(40))                                         # vr | vt | seguro | premio | combustivel | ...
    ativo = Column(Boolean, default=False)
    observacao = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "codeve": self.codeve,
            "descricao": self.descricao,
            "coluna_femsa": self.coluna_femsa,
            "grupo": self.grupo,
            "ativo": bool(self.ativo),
            "observacao": self.observacao,
        }
