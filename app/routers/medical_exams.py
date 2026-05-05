from fastapi import APIRouter, Query, Depends
from sqlalchemy.orm import Session
from typing import Optional
from datetime import date
from pydantic import BaseModel
from app.db import get_db
from app.models.medical_exam import MedicalExam

router = APIRouter(prefix="/api/medical-exams", tags=["medical_exams"])


class MedicalExamCreate(BaseModel):
    nome_funcionario: str
    numcad: Optional[int] = None
    data_exame: date
    clinic: float = 0.0
    audio: float = 0.0
    acuid: float = 0.0
    hemo: float = 0.0
    lipidograma: float = 0.0
    rx_coluna: float = 0.0
    met_e_cet: float = 0.0
    acet_u: float = 0.0
    hg: float = 0.0
    retic: float = 0.0
    ac_trans: float = 0.0
    eeg: float = 0.0
    ecg: float = 0.0
    etanol: float = 0.0
    glice: float = 0.0
    gama_gt: float = 0.0
    tgp: float = 0.0
    rx_torax: float = 0.0
    espiro: float = 0.0
    rx_lomb: float = 0.0
    aval_psicossocial: float = 0.0
    total: float = 0.0


class MedicalExamUpdate(BaseModel):
    nome_funcionario: Optional[str] = None
    numcad: Optional[int] = None
    data_exame: Optional[date] = None
    clinic: Optional[float] = None
    audio: Optional[float] = None
    acuid: Optional[float] = None
    hemo: Optional[float] = None
    lipidograma: Optional[float] = None
    rx_coluna: Optional[float] = None
    met_e_cet: Optional[float] = None
    acet_u: Optional[float] = None
    hg: Optional[float] = None
    retic: Optional[float] = None
    ac_trans: Optional[float] = None
    eeg: Optional[float] = None
    ecg: Optional[float] = None
    etanol: Optional[float] = None
    glice: Optional[float] = None
    gama_gt: Optional[float] = None
    tgp: Optional[float] = None
    rx_torax: Optional[float] = None
    espiro: Optional[float] = None
    rx_lomb: Optional[float] = None
    aval_psicossocial: Optional[float] = None
    total: Optional[float] = None


def exam_to_dict(exam: MedicalExam) -> dict:
    return {
        "id": exam.id,
        "nome_funcionario": exam.nome_funcionario,
        "numcad": exam.numcad,
        "data_exame": exam.data_exame.isoformat() if exam.data_exame else None,
        "clinic": exam.clinic or 0.0,
        "audio": exam.audio or 0.0,
        "acuid": exam.acuid or 0.0,
        "hemo": exam.hemo or 0.0,
        "lipidograma": exam.lipidograma or 0.0,
        "rx_coluna": exam.rx_coluna or 0.0,
        "met_e_cet": exam.met_e_cet or 0.0,
        "acet_u": exam.acet_u or 0.0,
        "hg": exam.hg or 0.0,
        "retic": exam.retic or 0.0,
        "ac_trans": exam.ac_trans or 0.0,
        "eeg": exam.eeg or 0.0,
        "ecg": exam.ecg or 0.0,
        "etanol": exam.etanol or 0.0,
        "glice": exam.glice or 0.0,
        "gama_gt": exam.gama_gt or 0.0,
        "tgp": exam.tgp or 0.0,
        "rx_torax": exam.rx_torax or 0.0,
        "espiro": exam.espiro or 0.0,
        "rx_lomb": exam.rx_lomb or 0.0,
        "aval_psicossocial": exam.aval_psicossocial or 0.0,
        "total": exam.total or 0.0,
        "created_at": exam.created_at.isoformat() if exam.created_at else None,
    }


EXAM_VALUE_FIELDS = [
    'clinic', 'audio', 'acuid', 'hemo', 'lipidograma', 'rx_coluna',
    'met_e_cet', 'acet_u', 'hg', 'retic', 'ac_trans', 'eeg', 'ecg',
    'etanol', 'glice', 'gama_gt', 'tgp', 'rx_torax', 'espiro',
    'rx_lomb', 'aval_psicossocial'
]


def compute_total(data) -> float:
    total = 0.0
    for field in EXAM_VALUE_FIELDS:
        total += getattr(data, field, 0.0) or 0.0
    return round(total, 2)


@router.post("")
async def create_exam(data: MedicalExamCreate, db: Session = Depends(get_db)):
    computed_total = compute_total(data)
    exam = MedicalExam(
        nome_funcionario=data.nome_funcionario,
        numcad=data.numcad,
        data_exame=data.data_exame,
        clinic=data.clinic,
        audio=data.audio,
        acuid=data.acuid,
        hemo=data.hemo,
        lipidograma=data.lipidograma,
        rx_coluna=data.rx_coluna,
        met_e_cet=data.met_e_cet,
        acet_u=data.acet_u,
        hg=data.hg,
        retic=data.retic,
        ac_trans=data.ac_trans,
        eeg=data.eeg,
        ecg=data.ecg,
        etanol=data.etanol,
        glice=data.glice,
        gama_gt=data.gama_gt,
        tgp=data.tgp,
        rx_torax=data.rx_torax,
        espiro=data.espiro,
        rx_lomb=data.rx_lomb,
        aval_psicossocial=data.aval_psicossocial,
        total=computed_total,
    )
    db.add(exam)
    db.commit()
    db.refresh(exam)
    return {"status": "success", "data": exam_to_dict(exam)}


@router.get("")
async def list_exams(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    nome: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    query = db.query(MedicalExam)

    if nome:
        query = query.filter(MedicalExam.nome_funcionario.ilike(f"%{nome}%"))

    total = query.count()
    exams = (
        query.order_by(MedicalExam.data_exame.desc(), MedicalExam.id.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    return {
        "status": "ok",
        "data": [exam_to_dict(e) for e in exams],
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": (total + per_page - 1) // per_page,
    }


@router.get("/{exam_id}")
async def get_exam(exam_id: int, db: Session = Depends(get_db)):
    exam = db.query(MedicalExam).filter(MedicalExam.id == exam_id).first()
    if not exam:
        return {"status": "error", "message": "Exame nao encontrado"}
    return {"status": "ok", "data": exam_to_dict(exam)}


@router.put("/{exam_id}")
async def update_exam(exam_id: int, data: MedicalExamUpdate, db: Session = Depends(get_db)):
    exam = db.query(MedicalExam).filter(MedicalExam.id == exam_id).first()
    if not exam:
        return {"status": "error", "message": "Exame nao encontrado"}

    update_data = data.dict(exclude_unset=True)
    for key, value in update_data.items():
        if key != 'total':
            setattr(exam, key, value)

    exam.total = sum(getattr(exam, f, 0.0) or 0.0 for f in EXAM_VALUE_FIELDS)
    exam.total = round(exam.total, 2)

    db.commit()
    db.refresh(exam)
    return {"status": "success", "data": exam_to_dict(exam)}


@router.delete("/{exam_id}")
async def delete_exam(exam_id: int, db: Session = Depends(get_db)):
    exam = db.query(MedicalExam).filter(MedicalExam.id == exam_id).first()
    if not exam:
        return {"status": "error", "message": "Exame nao encontrado"}
    db.delete(exam)
    db.commit()
    return {"status": "success", "message": "Exame removido"}
