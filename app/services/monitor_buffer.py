"""Buffer em memória dos eventos do Senior — usado por /monitor (DEV_MODE).

Cada evento é um dict simples com:
  ts        — datetime do evento (UTC, ISO)
  kind      — "soap_start" | "soap_end" | "soap_error" | "slow"
  label     — rótulo curto (consultaRegistros, T018CCU, etc)
  detail    — string livre (codccu, periodo, n_registros, mensagem do erro)
  elapsed_s — duração da chamada (None pra start, float pra end/error)
  severity  — "info" | "warn" | "error"

Buffer thread-safe usando deque com lock + tamanho máximo (default 300).
"""
from __future__ import annotations

import threading
from collections import deque
from datetime import datetime, timezone
from typing import Deque, Dict, List, Optional


_LOCK = threading.Lock()
_BUFFER: Deque[dict] = deque(maxlen=300)
_COUNTERS: Dict[str, int] = {
    "soap_total": 0,
    "soap_ok": 0,
    "soap_error": 0,
    "soap_503": 0,
    "soap_reset": 0,
    "soap_timeout": 0,
    "soap_slow": 0,  # > 5s
}

SLOW_THRESHOLD_S = 5.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record_start(label: str, detail: str = "") -> dict:
    """Marca início de uma chamada. Retorna o evento (pra encadear com record_end)."""
    ev = {
        "ts": _now_iso(),
        "kind": "soap_start",
        "label": label,
        "detail": detail,
        "elapsed_s": None,
        "severity": "info",
    }
    with _LOCK:
        _BUFFER.append(ev)
    return ev


def record_end(label: str, detail: str, elapsed_s: float, *, ok: bool = True) -> None:
    """Marca fim de uma chamada bem-sucedida."""
    kind = "soap_end"
    severity = "info"
    if elapsed_s >= SLOW_THRESHOLD_S:
        kind = "slow"
        severity = "warn"
    ev = {
        "ts": _now_iso(),
        "kind": kind,
        "label": label,
        "detail": detail,
        "elapsed_s": round(elapsed_s, 2),
        "severity": severity,
    }
    with _LOCK:
        _BUFFER.append(ev)
        _COUNTERS["soap_total"] += 1
        if ok:
            _COUNTERS["soap_ok"] += 1
        if elapsed_s >= SLOW_THRESHOLD_S:
            _COUNTERS["soap_slow"] += 1


def record_error(label: str, detail: str, elapsed_s: Optional[float] = None) -> None:
    """Marca falha. Classifica automaticamente em 503/reset/timeout/genérico."""
    d_low = (detail or "").lower()
    classif = "generic"
    if "503" in detail or "f5" in d_low:
        classif = "503"
    elif "10054" in detail or "connectionreset" in d_low or "connection aborted" in d_low:
        classif = "reset"
    elif "timed out" in d_low or "timeout" in d_low:
        classif = "timeout"

    ev = {
        "ts": _now_iso(),
        "kind": "soap_error",
        "label": label,
        "detail": f"[{classif}] {detail}",
        "elapsed_s": round(elapsed_s, 2) if elapsed_s is not None else None,
        "severity": "error",
    }
    with _LOCK:
        _BUFFER.append(ev)
        _COUNTERS["soap_total"] += 1
        _COUNTERS["soap_error"] += 1
        if classif == "503":
            _COUNTERS["soap_503"] += 1
        elif classif == "reset":
            _COUNTERS["soap_reset"] += 1
        elif classif == "timeout":
            _COUNTERS["soap_timeout"] += 1


def get_recent(limit: int = 200) -> List[dict]:
    """Retorna os últimos `limit` eventos em ordem reversa (mais recente primeiro)."""
    with _LOCK:
        items = list(_BUFFER)
    items.reverse()
    return items[:limit]


def get_counters() -> Dict[str, int]:
    with _LOCK:
        return dict(_COUNTERS)


def reset() -> None:
    """Limpa buffer + contadores (útil em dev)."""
    with _LOCK:
        _BUFFER.clear()
        for k in _COUNTERS:
            _COUNTERS[k] = 0
