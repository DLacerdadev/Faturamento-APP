"""Registro em memória de jobs de exportação (executados em thread separada).

Motivo: a exportação da folha faz muitas chamadas SOAP sequenciais à Senior e
pode passar de 100s — estourando o timeout do proxy (Cloudflare devolve 524).
Com o job em segundo plano a requisição volta na hora com um id; o front
consulta o status e baixa o arquivo quando fica pronto. Cada requisição
(iniciar/status/baixar) é curta, então nunca encosta no limite do proxy.

Limitação: estado em memória do processo — vale para deploy de **1 worker**
(uvicorn sem --workers). Jobs somem em restart e não são compartilhados entre
múltiplos workers. Exportações são curtas e re-executáveis, então é aceitável.
"""
import threading
import uuid
from datetime import datetime, timedelta
from typing import Optional, Dict

_JOBS: Dict[str, "ExportJob"] = {}
_LOCK = threading.Lock()
_MAX_JOBS = 100
_RETAIN = timedelta(hours=1)


class ExportJob:
    def __init__(self, job_id: str, descricao: str = "",
                 user_id: Optional[int] = None, username: Optional[str] = None):
        self.id = job_id
        self.descricao = descricao
        # Snapshot de quem ENFILEIROU o job — permite auditar o download depois
        # (a thread do job roda sem contexto de request).
        self.user_id = user_id
        self.username = username
        self.status = "pending"          # pending | running | done | error
        self.done = 0
        self.total = 0
        self.message = "Na fila…"
        self.filename: Optional[str] = None
        self.media_type: Optional[str] = None
        self.content: Optional[bytes] = None
        self.error: Optional[str] = None
        self.created_at = datetime.utcnow()
        self.finished_at: Optional[datetime] = None

    def public(self) -> dict:
        pct = int(self.done / self.total * 100) if self.total else (100 if self.status == "done" else 0)
        return {
            "job_id": self.id,
            "status": self.status,
            "done": self.done,
            "total": self.total,
            "percent": pct,
            "message": self.message,
            "filename": self.filename,
            "error": self.error,
        }


def _prune_locked():
    """Remove jobs finalizados antigos e limita o tamanho do registro."""
    now = datetime.utcnow()
    for k in [k for k, j in _JOBS.items() if j.finished_at and (now - j.finished_at) > _RETAIN]:
        _JOBS.pop(k, None)
    if len(_JOBS) > _MAX_JOBS:
        finished = sorted((j for j in _JOBS.values() if j.finished_at), key=lambda j: j.finished_at)
        for j in finished[: len(_JOBS) - _MAX_JOBS]:
            _JOBS.pop(j.id, None)


def create_job(descricao: str = "", user_id: Optional[int] = None,
               username: Optional[str] = None) -> ExportJob:
    with _LOCK:
        _prune_locked()
        job = ExportJob(uuid.uuid4().hex, descricao, user_id=user_id, username=username)
        _JOBS[job.id] = job
        return job


def get_job(job_id: str) -> Optional[ExportJob]:
    with _LOCK:
        return _JOBS.get(job_id)


def set_running(job_id: str, message: str = "Processando…"):
    with _LOCK:
        j = _JOBS.get(job_id)
        if j:
            j.status = "running"
            j.message = message


def set_progress(job_id: str, done: int, total: int, message: Optional[str] = None):
    with _LOCK:
        j = _JOBS.get(job_id)
        if j:
            j.done, j.total = done, total
            if message is not None:
                j.message = message


def finish_ok(job_id: str, content: bytes, filename: str, media_type: str):
    with _LOCK:
        j = _JOBS.get(job_id)
        if j:
            j.content = content
            j.filename = filename
            j.media_type = media_type
            if j.total:
                j.done = j.total
            j.message = "Concluído"
            j.status = "done"
            j.finished_at = datetime.utcnow()


def finish_error(job_id: str, error: str):
    with _LOCK:
        j = _JOBS.get(job_id)
        if j:
            j.error = error
            j.message = "Erro"
            j.status = "error"
            j.finished_at = datetime.utcnow()
