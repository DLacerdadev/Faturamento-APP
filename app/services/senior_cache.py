"""
Cache e throttle das chamadas Senior (feature 003).

- TimedCache: cache em memória com TTL e lazy expiration (sem cron).
- ccu_cache: lista de centros de custo (T018CCU). Key = numEmp. TTL default 6h.
- employees_cache: funcionários ativos por CCU+mês. Key = (codccu_str, "YYYY-MM"). TTL default 1h.
- _SOAP_SEMAPHORE: limita chamadas SOAP concorrentes (default 3).

Stdlib pura: dict + time.time() + threading.{Lock, BoundedSemaphore}.
"""
import logging
import threading
import time
from datetime import date
from typing import Any, Optional

from app.config import (
    SENIOR_CACHE_CCU_TTL,
    SENIOR_CACHE_EMPLOYEES_TTL,
    SENIOR_SOAP_MAX_CONCURRENCY,
)

logger = logging.getLogger(__name__)


class TimedCache:
    """Cache thread-safe com TTL e lazy expiration. Pensado para uso single-process."""

    def __init__(self, ttl_seconds: int, name: str = "cache"):
        self.ttl = int(ttl_seconds)
        self.name = name
        self._data: dict[Any, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key) -> Optional[Any]:
        """Retorna o valor cacheado ou None se ausente/expirado.

        Lazy expiration: ao detectar TTL excedido, descarta a entrada antes de retornar None.
        """
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                logger.info("cache=miss name=%s key=%r", self.name, key)
                return None
            ts, value = entry
            age = time.time() - ts
            if age > self.ttl:
                self._data.pop(key, None)
                logger.info("cache=miss name=%s key=%r (expired age=%.1fs)", self.name, key, age)
                return None
            logger.info("cache=hit name=%s key=%r ttl_left=%.1fs", self.name, key, self.ttl - age)
            return value

    def set(self, key, value) -> None:
        with self._lock:
            self._data[key] = (time.time(), value)
        logger.info("cache=set name=%s key=%r", self.name, key)

    def invalidate(self, key=None) -> int:
        """Remove uma chave específica ou TODAS. Retorna nº de entradas removidas."""
        with self._lock:
            if key is None:
                n = len(self._data)
                self._data.clear()
                logger.info("cache=invalidate name=%s scope=all removed=%d", self.name, n)
                return n
            removed = 1 if self._data.pop(key, None) is not None else 0
            logger.info("cache=invalidate name=%s key=%r removed=%d", self.name, key, removed)
            return removed

    def stats(self) -> dict:
        """Snapshot informativo do estado (sem alterar)."""
        with self._lock:
            now = time.time()
            return {
                "name": self.name,
                "ttl": self.ttl,
                "entries": len(self._data),
                "keys": [
                    {
                        "key": str(k),
                        "age_seconds": round(now - ts, 1),
                        "ttl_left": round(self.ttl - (now - ts), 1),
                    }
                    for k, (ts, _) in self._data.items()
                ],
            }


# Instâncias singleton de cache (uma por categoria).
ccu_cache = TimedCache(SENIOR_CACHE_CCU_TTL, name="ccu")
employees_cache = TimedCache(SENIOR_CACHE_EMPLOYEES_TTL, name="employees")


# Semáforo global limitando chamadas SOAP simultâneas.
_SOAP_SEMAPHORE = threading.BoundedSemaphore(SENIOR_SOAP_MAX_CONCURRENCY)


def current_month_key() -> str:
    """Retorna 'YYYY-MM' do mês corrente — usado como parte da chave do employees_cache."""
    return date.today().strftime("%Y-%m")


def soap_concurrency_snapshot() -> dict:
    """Snapshot informativo do semáforo. _value é detalhe de implementação; útil para depurar."""
    try:
        available = _SOAP_SEMAPHORE._value  # type: ignore[attr-defined]
        in_flight = SENIOR_SOAP_MAX_CONCURRENCY - available
    except Exception:
        in_flight = None
    return {
        "max": SENIOR_SOAP_MAX_CONCURRENCY,
        "in_flight_estimated": in_flight,
    }


logger.info(
    "Senior cache config: ccu_ttl=%ss employees_ttl=%ss soap_concurrency=%s",
    SENIOR_CACHE_CCU_TTL,
    SENIOR_CACHE_EMPLOYEES_TTL,
    SENIOR_SOAP_MAX_CONCURRENCY,
)
