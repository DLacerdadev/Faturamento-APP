# Data Model — Feature 003

## Sem mudanças de banco

Esta feature não introduz nem altera tabelas no SQLite ou PostgreSQL. Toda persistência é **em memória de processo**.

## Estruturas em memória

### `TimedCache`

Estrutura genérica de cache com TTL e lazy expiration.

| Campo (interno) | Tipo | Descrição |
|---|---|---|
| `ttl` | `int` | TTL em segundos (constante após init). |
| `name` | `str` | Identificador para logs (`"ccu"` ou `"employees"`). |
| `_data` | `dict[hashable, tuple[float, Any]]` | Mapeia chave → (timestamp_unix, valor). |
| `_lock` | `threading.Lock` | Serializa acessos. |

### Instâncias (singletons) em `app/services/senior_cache.py`

| Instância | TTL (default) | Chave | Valor |
|---|---|---|---|
| `ccu_cache` | 21600s (6h) | `int numEmp` | `list[dict {codccu, nomccu}]` retornado por T018CCU |
| `employees_cache` | 3600s (1h) | `tuple (codccu_str, mês_corrente_YYYY-MM_str)` | `list[dict {numcad, nomfun, codccu, nomccu, datadm, datafa, sitafa, dessit, cargo, valsal}]` |

### `_SOAP_SEMAPHORE`

`threading.BoundedSemaphore(SENIOR_SOAP_MAX_CONCURRENCY)` com default 3. Variável módulo-level em `app/services/senior_cache.py`.

## Ciclo de vida das entradas

```text
get(key):
  ├── lookup em _data
  ├── miss → return None
  └── hit →
        ├── age = now - ts
        ├── age > ttl → pop(key), return None  (lazy expiration)
        └── age ≤ ttl → return value
```

```text
set(key, value):
  └── _data[key] = (now, value)
```

```text
invalidate(key=None):
  ├── key=None → clear()
  └── key=X → pop(X)
```

## Validação

- Não há "invariantes" persistentes — cada entrada é descartável.
- Concorrência: garantida pelo `Lock` (`get`, `set`, `invalidate`).
- Reset: restart do processo zera todos os caches (aceitável; A1 da spec).

## Configuração

| Variável `.env` | Tipo | Default | Lida por |
|---|---|---|---|
| `SENIOR_CACHE_CCU_TTL` | int (segundos) | `21600` (6h) | `app.config` → injetado em `ccu_cache` |
| `SENIOR_CACHE_EMPLOYEES_TTL` | int (segundos) | `3600` (1h) | `app.config` → injetado em `employees_cache` |
| `SENIOR_SOAP_MAX_CONCURRENCY` | int (positivo) | `3` | `app.config` → injetado no `_SOAP_SEMAPHORE` |

Valores muito agressivos (TTL 0, concorrência 0) devem ser **detectados na inicialização** e logados como erro, mas o sistema sobe com defaults se acontecer (defensivo).
