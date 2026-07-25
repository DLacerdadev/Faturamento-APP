# INTEGRATION NOTES — Feature 008 (EPI automático via TOTVS)

Mudanças que a **orquestradora** deve aplicar no merge. O Agente C **NÃO** editou
`app/main.py`, `app/config.py`, `app/db.py`, `requirements.txt`, `.env.example`
nem `RUNBOOK.md` (fronteiras da spec). Segue o patch/nota de cada um.

> **Segurança:** credencial real **só no `.env` local (gitignored)**, nunca
> versionada. O `.env.example` traz apenas placeholders vazios.

---

## 1. `requirements.txt` — nova dependência

Adicionar (o `httpx` provavelmente já existe; confirmar):

```
APScheduler>=3.10
httpx>=0.27
```

Instaladas/validadas no dev local: `apscheduler==3.11.3`, `httpx==0.28.1`.

---

## 2. `app/config.py` — env TOTVS + horário do cron + `is_totvs_configured()`

O conector e o scheduler já funcionam lendo `os.getenv` como fallback, mas o
**local canônico** é o `config.py`. Adicionar:

```python
# Feature 008: TOTVS Protheus via WSGETDATA (REST -> SQL) para preço de EPI.
TOTVS_WSGETDATA_URL = os.getenv("TOTVS_WSGETDATA_URL", "")
TOTVS_WSGETDATA_USER = os.getenv("TOTVS_WSGETDATA_USER", "")
TOTVS_WSGETDATA_PASSWORD = os.getenv("TOTVS_WSGETDATA_PASSWORD", "")
# Horário (HH:MM) do sync diário de preços de EPI. Default 02:00.
TOTVS_SYNC_HORARIO = os.getenv("TOTVS_SYNC_HORARIO", "02:00")


def is_totvs_configured() -> bool:
    """True se URL + usuário + senha do WSGETDATA estão preenchidos."""
    return bool(TOTVS_WSGETDATA_URL and TOTVS_WSGETDATA_USER and TOTVS_WSGETDATA_PASSWORD)
```

As env `TOTVS_WSGETDATA_URL/USER/PASSWORD` e `TOTVS_SYNC_HORARIO` já existem no
`.env` local (conforme briefing). Quando estes atributos existirem em
`app.config`, o conector/scheduler passam a preferi-los automaticamente
(fazem `getattr(app_config, ...)` primeiro).

---

## 3. `.env.example` — placeholders (SEM segredo)

```
# TOTVS Protheus WSGETDATA (feature 008 — preço de EPI por centro de custo)
TOTVS_WSGETDATA_URL=
TOTVS_WSGETDATA_USER=
TOTVS_WSGETDATA_PASSWORD=
TOTVS_SYNC_HORARIO=02:00
```

---

## 4. `app/main.py` — startup/shutdown do scheduler

O scheduler NÃO se auto-registra (decisão da spec). Registrar no bootstrap:

```python
from app.services.scheduler import start_scheduler, shutdown_scheduler

@app.on_event("startup")
def _start_totvs_scheduler():
    # Um único worker (uvicorn sem --workers), coerente com export_jobs.
    start_scheduler()

@app.on_event("shutdown")
def _stop_totvs_scheduler():
    shutdown_scheduler()
```

Observações:
- `start_scheduler()` é idempotente e agenda o job diário no `TOTVS_SYNC_HORARIO`.
- Se rodar com **múltiplos workers**, o job dispararia em cada um — manter 1
  worker (como já é o padrão do projeto) OU proteger com lock. Hoje: 1 worker.
- O job em si é falha-segura: TOTVS indisponível apenas loga e preserva preços.

---

## 5. `app/db.py` — migração idempotente (colunas novas em `cc_item_prices`)

Acrescentar ao bloco `_migrations` de `init_db()` (colunas NULLable/‌default,
compatível com dado existente — P5):

```python
("cc_item_prices", [
    ("is_manual_price", "BOOLEAN DEFAULT 0"),   # em Postgres: "BOOLEAN DEFAULT FALSE"
    ("last_auto_sync", "TIMESTAMP" if _is_pg else "DATETIME"),
]),
```

> Em SQLite `BOOLEAN DEFAULT 0`; em Postgres use `BOOLEAN DEFAULT FALSE`. O bloco
> já ramifica por `_is_pg` — ajuste o DDL conforme o dialeto. Em bancos **novos**
> (create_all) as colunas já nascem do modelo; o ALTER cobre bancos **existentes**
> (dev espelhado / prod).

### RUNBOOK.md — migração manual (bancos já existentes)

Postgres (prod):
```sql
ALTER TABLE cc_item_prices ADD COLUMN IF NOT EXISTS is_manual_price BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE cc_item_prices ADD COLUMN IF NOT EXISTS last_auto_sync TIMESTAMP NULL;
```

Também documentar no RUNBOOK:
- **Cron/rotina**: sync diário de EPI às `TOTVS_SYNC_HORARIO` (default 02:00) via
  APScheduler no processo do app (1 worker). Disparo manual: botão "Sincronizar
  EPIs do TOTVS agora" (gestor+) ou `POST /api/products/sync-totvs`.
- **Rotação de credencial**: a senha `powerbi` foi compartilhada em texto puro —
  rotacionar e, se possível, criar usuário Protheus dedicado com permissão
  mínima (só as tabelas SC7010/SB1010). Atualizar `.env` local; nunca versionar.

---

## 6. Resumo dos arquivos entregues nesta branch (`spec/008-epi`)

Criados:
- `app/services/totvs_connector.py` — conector WSGETDATA (Basic auth, POST do
  SELECT fixo, 201=ok/erro em >=400, decode cp1252, `run_query()`).
- `app/services/price_autosync.py` — `run_epi_price_autosync()` (upsert por
  (codccu, produto) respeitando trava) e `resync_one_cc_item()` (voltar ao auto).
- `app/services/scheduler.py` — APScheduler; `start_scheduler()`/
  `shutdown_scheduler()`/`trigger_now()`. **Não** registrado no main.py (ver §4).
- `tests/test_totvs_connector.py`, `tests/test_price_autosync.py` — conector
  sempre mockado; isolamento por CCU coberto.

Editados:
- `app/models/cc_item_price.py` — colunas `is_manual_price`, `last_auto_sync`.
- `app/routers/product_catalog.py` — endpoints CC-price (listar / editar=trava /
  voltar-automático / sincronizar-agora gestor+), auditados.
- `app/templates/catalogo_produtos.html` — selo "definido pela TOTVS" + data da
  última sync + estado manual/automático + ações por linha (CCU × produto) +
  botão "Sincronizar EPIs do TOTVS agora".

Não tocado: specs 009/010/007.
