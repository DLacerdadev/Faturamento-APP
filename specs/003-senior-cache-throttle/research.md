# Research — Cache e Throttle das Chamadas Senior

Decisões técnicas. Os valores de config (TTLs e concorrência) foram fechados na spec (Q1/Q2/Q3). Este documento detalha como implementar.

---

## R1 — Estrutura do cache em memória

**Decisão**: classe `TimedCache` simples baseada em `dict` + `threading.Lock` + `time.time()`.

```python
import time, threading
from typing import Any, Optional

class TimedCache:
    def __init__(self, ttl_seconds: int, name: str = "cache"):
        self.ttl = ttl_seconds
        self.name = name
        self._data: dict[Any, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key) -> Optional[Any]:
        """Lazy expiration: descarta e retorna None se expirado."""
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            ts, value = entry
            if time.time() - ts > self.ttl:
                self._data.pop(key, None)
                return None
            return value

    def set(self, key, value) -> None:
        with self._lock:
            self._data[key] = (time.time(), value)

    def invalidate(self, key=None) -> int:
        """Remove uma chave específica ou todo o cache. Retorna nº removido."""
        with self._lock:
            if key is None:
                n = len(self._data)
                self._data.clear()
                return n
            return 1 if self._data.pop(key, None) is not None else 0

    def stats(self) -> dict:
        """Snapshot informativo do estado atual."""
        with self._lock:
            now = time.time()
            return {
                "name": self.name,
                "ttl": self.ttl,
                "entries": len(self._data),
                "keys": [
                    {"key": str(k), "age_seconds": round(now - ts, 1), "ttl_left": round(self.ttl - (now - ts), 1)}
                    for k, (ts, _) in self._data.items()
                ],
            }
```

**Racional**:
- Sem dependência externa (P3).
- Thread-safe — uvicorn faz I/O em threads para endpoints sync.
- Lazy expiration é suficiente; sem necessidade de cron/scheduler.
- `stats()` permite endpoint de inspeção (opcional, útil para depurar).

**Alternativas consideradas**:
- `functools.lru_cache`: não tem TTL.
- `cachetools.TTLCache`: dependência nova, viola P3.
- Redis: out of scope; só faz sentido quando há múltiplos workers.

---

## R2 — Acoplamento com `senior_connector`

**Decisão**: módulo novo `app/services/senior_cache.py` com 2 instâncias singleton:

```python
from app.config import SENIOR_CACHE_CCU_TTL, SENIOR_CACHE_EMPLOYEES_TTL
from app.services._timed_cache import TimedCache

ccu_cache = TimedCache(SENIOR_CACHE_CCU_TTL, name="ccu")
employees_cache = TimedCache(SENIOR_CACHE_EMPLOYEES_TTL, name="employees")
```

(`TimedCache` pode viver dentro do mesmo arquivo ou em `_timed_cache.py` — decisão de implementação.)

Funções afetadas em `senior_connector.py`:

| Função | Antes | Depois |
|---|---|---|
| `fetch_all_cost_centers()` | chama `_call_soap_cost_centers(TELOS_NUMEMP)` direto | consulta `ccu_cache[TELOS_NUMEMP]`; miss → SOAP → set |
| `fetch_cost_centers(numemp)` | chama `_call_soap_cost_centers(numemp)` direto | consulta `ccu_cache[numemp]`; miss → SOAP → set |
| `fetch_active_employees(codccu)` | chama `fetch_payroll(periodo, codccu)` direto | consulta `employees_cache[(codccu, mês_corrente)]`; miss → SOAP → set |

**Racional**:
- Mantém `senior_connector.py` como ponto único de contato com Senior (P1).
- Mudança é local; nenhum caller precisa adaptar (compat com features 001/002).
- `_revalidate_active` em `epi_purchases.py` já chama `fetch_active_employees` — ganha cache de graça (cobre Win 2 da spec).

---

## R3 — Semáforo global de concorrência

**Decisão**: `threading.BoundedSemaphore(SENIOR_SOAP_MAX_CONCURRENCY)` instanciado em `app/services/senior_cache.py`. `_post_soap_with_retry` o adquire antes do `requests.post`, libera depois (try/finally).

```python
from app.services.senior_cache import _SOAP_SEMAPHORE
import time

with _SOAP_SEMAPHORE:  # bloqueia se 3 em voo
    t0 = time.time()
    # acquire pode ter esperado — loga se foi significativo
    wait = time.time() - t0  # 0 se entrou direto; >0 se enfileirou
    response = requests.post(...)
```

Na prática, `threading.BoundedSemaphore` não retorna quanto tempo esperou no `__enter__`. Para medir, usa-se:

```python
t0 = time.time()
_SOAP_SEMAPHORE.acquire()
wait_ms = (time.time() - t0) * 1000
try:
    response = requests.post(...)
finally:
    _SOAP_SEMAPHORE.release()
```

**Racional**:
- `BoundedSemaphore` previne over-release acidental.
- Bloqueante (não rejeita): cumpre FR-16.
- Não precisa async — handlers do FastAPI já rodam em thread pool.

**Alternativas consideradas**:
- `asyncio.Semaphore`: exigiria converter handlers para async — refactor grande.
- Queue + worker pool: overkill.

---

## R4 — Configuração via `.env`

**Decisão**: 3 vars novas em `app/config.py`:

```python
SENIOR_CACHE_CCU_TTL = int(os.getenv("SENIOR_CACHE_CCU_TTL", "21600"))  # 6h
SENIOR_CACHE_EMPLOYEES_TTL = int(os.getenv("SENIOR_CACHE_EMPLOYEES_TTL", "3600"))  # 1h
SENIOR_SOAP_MAX_CONCURRENCY = int(os.getenv("SENIOR_SOAP_MAX_CONCURRENCY", "3"))
```

Log de startup em `app/main.py` (ou no próprio `senior_cache.py` na importação):

```python
logger.info("Senior cache config: ccu_ttl=%ss employees_ttl=%ss soap_concurrency=%s",
            SENIOR_CACHE_CCU_TTL, SENIOR_CACHE_EMPLOYEES_TTL, SENIOR_SOAP_MAX_CONCURRENCY)
```

---

## R5 — Endpoints administrativos

**Decisão**: 2 endpoints novos em `app/routers/integrations.py`, ambos POST autenticados.

**Schemas Pydantic**:

```python
from typing import Literal, Optional
class CacheActionInput(BaseModel):
    scope: Literal["ccu", "employees", "all"] = "all"
    key: Optional[str] = None  # opcional; sem ele, atua em toda a categoria
```

**Endpoint 1 — Invalidate** (`POST /integrations/senior/cache/invalidate`):
- Body: `CacheActionInput`
- Comportamento: chama `cache.invalidate(key)` na(s) cache(s) selecionada(s). Não busca dados frescos.
- Response: `{ status: "ok", removed: { ccu: N, employees: M } }`

**Endpoint 2 — Refresh** (`POST /integrations/senior/cache/refresh`):
- Body: `CacheActionInput`
- Comportamento: chama `cache.invalidate(key)` E em seguida chama a função pública (`fetch_all_cost_centers` ou `fetch_active_employees`) para repopular. Retorna os dados novos.
- Response: `{ status: "ok", scope, refreshed: { ccu: {count, sample}, employees: {...} } }`
- 503 se a chamada à Senior falhar.

**Racional**:
- Refresh é uma operação distinta de invalidate (FR-4): o admin pode querer garantir que o próximo acesso seja rápido (já cacheou os dados frescos) — diferente de só limpar e esperar.

---

## R6 — Observabilidade

**Decisão**: logs em nível `INFO` para hit/miss e startup. `DEBUG` para detalhes.

Padrão dos logs (formato `key=value`):

```
INFO cache=hit name=ccu key=6 ttl_left=20850.3s
INFO cache=miss name=ccu key=6
INFO cache=set name=ccu key=6 entries=760
INFO soap=consultaRegistros duration_ms=2340 wait_ms=150 codccu=620039
INFO cache=invalidate scope=all removed=2
```

Wait time no semáforo só é logado se > 100ms (evita poluir o log com waits triviais).

---

## R7 — Retry (registro)

Confirmação: já removido antes do plan. `_post_soap_with_retry` em [app/services/senior_connector.py](FATURAMENTO-APP/app/services/senior_connector.py) faz uma única tentativa, propaga erro com support ID do F5 quando presente. Sem retry, sem backoff, sem loop.

A única mudança nesta feature: adicionar acquire/release do semáforo ao redor do `requests.post`.

---

## Resumo do impacto no código

| Arquivo | Mudança |
|---|---|
| `app/services/senior_cache.py` | **novo** — TimedCache, instâncias `ccu_cache`/`employees_cache`, `_SOAP_SEMAPHORE` |
| `app/services/senior_connector.py` | usar caches em `fetch_*_cost_centers` e `fetch_active_employees`; adicionar acquire/release no `_post_soap_with_retry` |
| `app/config.py` | + 3 vars (TTLs + concorrência) com defaults |
| `app/routers/integrations.py` | + 2 endpoints admin (`/cache/invalidate`, `/cache/refresh`) |
| `app/main.py` | log de startup com config efetiva |
| `.env.example` e `.env` | + 3 vars documentadas |
| `RUNBOOK.md` | + seção "Cache Senior — configuração e admin" |
| `CLAUDE.md` | atualizar Active Spec Feature |
