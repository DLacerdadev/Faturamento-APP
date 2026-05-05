from sqlalchemy import Column, Integer, String, Float, Date, DateTime
from datetime import datetime, date
from app.db import Base


class MedicalExam(Base):
    __tablename__ = "medical_exams"

    id = Column(Integer, primary_key=True, index=True)
    nome_funcionario = Column(String(255), nullable=False, index=True)
    numcad = Column(Integer)
    data_exame = Column(Date, default=date.today)
    clinic = Column(Float, default=0.0)
    audio = Column(Float, default=0.0)
    acuid = Column(Float, default=0.0)
    hemo = Column(Float, default=0.0)
    lipidograma = Column(Float, default=0.0)
    rx_coluna = Column(Float, default=0.0)
    met_e_cet = Column(Float, default=0.0)
    acet_u = Column(Float, default=0.0)
    hg = Column(Float, default=0.0)
    retic = Column(Float, default=0.0)
    ac_trans = Column(Float, default=0.0)
    eeg = Column(Float, default=0.0)
    ecg = Column(Float, default=0.0)
    etanol = Column(Float, default=0.0)
    glice = Column(Float, default=0.0)
    gama_gt = Column(Float, default=0.0)
    tgp = Column(Float, default=0.0)
    rx_torax = Column(Float, default=0.0)
    espiro = Column(Float, default=0.0)
    rx_lomb = Column(Float, default=0.0)
    aval_psicossocial = Column(Float, default=0.0)
    total = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
