"""Scheduler de tarefas de fundo (feature 008) — APScheduler.

Agenda a rotina diária de sincronização de preços de EPI do TOTVS
(`price_autosync.run_epi_price_autosync`) 1x/dia no horário `TOTVS_SYNC_HORARIO`
(default 02:00). Um único worker (coerente com `export_jobs`; uvicorn sem
`--workers`).

Uso pela orquestradora (NÃO registrado no main.py por decisão da spec — ver
`specs/008-epi-auto-totvs/INTEGRATION-NOTES.md`):

    from app.services.scheduler import start_scheduler, shutdown_scheduler

    @app.on_event("startup")
    def _startup():
        start_scheduler()

    @app.on_event("shutdown")
    def _shutdown():
        shutdown_scheduler()

O trigger também pode ser disparado manualmente (`trigger_now()`) — usado pelo
endpoint "sincronizar agora" e pelos testes (sem esperar 24h).
"""
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_scheduler = None  # BackgroundScheduler singleton de processo
JOB_ID = "epi_price_autosync_daily"


def _cfg_horario() -> str:
    """Horário do cron (HH:MM). Prioriza config.TOTVS_SYNC_HORARIO, senão env,
    senão default 02:00."""
    try:
        from app import config as app_config
        val = getattr(app_config, "TOTVS_SYNC_HORARIO", None)
        if val:
            return str(val)
    except Exception:
        pass
    return os.getenv("TOTVS_SYNC_HORARIO", "02:00")


def _parse_hora_min(horario: str) -> tuple[int, int]:
    """'02:00' -> (2, 0). Tolerante a formato inválido (cai no default 02:00)."""
    try:
        parts = str(horario).strip().split(":")
        hh = int(parts[0])
        mm = int(parts[1]) if len(parts) > 1 else 0
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            raise ValueError
        return hh, mm
    except Exception:
        logger.warning("TOTVS_SYNC_HORARIO inválido (%r); usando 02:00.", horario)
        return 2, 0


def _run_autosync_job() -> None:
    """Wrapper do job: chama o autosync com sessão própria. Nunca deixa a
    exceção subir para o scheduler (a rotina já é falha-segura, mas garantimos)."""
    try:
        from app.services.price_autosync import run_epi_price_autosync
        run_epi_price_autosync()
    except Exception as exc:  # pragma: no cover - defensivo
        logger.error("Job de autosync de EPI falhou: %s", exc)


def start_scheduler():
    """Inicia o BackgroundScheduler e agenda o autosync diário (idempotente).

    Retorna a instância do scheduler (ou a existente se já iniciado)."""
    global _scheduler
    if _scheduler is not None and getattr(_scheduler, "running", False):
        return _scheduler

    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger

    hh, mm = _parse_hora_min(_cfg_horario())
    if _scheduler is None:
        _scheduler = BackgroundScheduler(daemon=True)

    _scheduler.add_job(
        _run_autosync_job,
        trigger=CronTrigger(hour=hh, minute=mm),
        id=JOB_ID,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    if not getattr(_scheduler, "running", False):
        _scheduler.start()
    logger.info("Scheduler iniciado: autosync de EPI diário às %02d:%02d.", hh, mm)
    return _scheduler


def shutdown_scheduler(wait: bool = False):
    """Desliga o scheduler se estiver rodando (idempotente)."""
    global _scheduler
    if _scheduler is not None and getattr(_scheduler, "running", False):
        try:
            _scheduler.shutdown(wait=wait)
        except Exception as exc:  # pragma: no cover - defensivo
            logger.warning("Falha ao desligar scheduler: %s", exc)
    _scheduler = None


def get_scheduler():
    """Retorna a instância atual do scheduler (ou None se não iniciado)."""
    return _scheduler


def trigger_now() -> dict:
    """Dispara a rotina de autosync IMEDIATAMENTE (síncrono), sem esperar o
    agendamento. Usado pelo endpoint "sincronizar agora" e pelos testes."""
    from app.services.price_autosync import run_epi_price_autosync
    return run_epi_price_autosync()
