from sqlalchemy import Column, Integer, String, Boolean, DateTime
from datetime import datetime
from app.db import Base


class CodcalClassification(Base):
    """Classificação GLOBAL de um código de cálculo (CODCAL) da folha Senior,
    usada pela conciliação contábil (Etapa 3 do Plano de Execução).

    Diz se o cálculo entra no 'recorte mensal' (o que a contabilidade confere no
    relatório mensal da Senior) ou fica 'fora do recorte' (parte da competência
    inteira que não aparece no relatório mensal). Uma linha por CODCAL vale para
    TODAS as competências.

    Ausência de linha para um CODCAL presente na folha = "não classificado":
    a conciliação daquela competência fica 'incompleta' até o gestor classificar.
    Assim, um cálculo novo nunca passa despercebido.
    """
    __tablename__ = "codcal_classifications"

    id = Column(Integer, primary_key=True, index=True)
    codcal = Column(Integer, unique=True, nullable=False, index=True)  # código do cálculo na Senior (ex.: 362)
    descricao = Column(String(255))                                    # rótulo humano (o WS não fornece o nome)
    recorte_mensal = Column(Boolean, nullable=False, default=True)     # True = entra no recorte mensal; False = fora
    origem = Column(String(20), default="manual")                     # manual | heuristica | oficial (TIPCAL futuro)
    observacao = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "codcal": self.codcal,
            "descricao": self.descricao,
            "recorte_mensal": bool(self.recorte_mensal),
            "origem": self.origem or "manual",
            "observacao": self.observacao,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
