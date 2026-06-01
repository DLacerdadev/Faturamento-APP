# Project Context — FATURAMENTO-APP

Sistema de faturamento RH com integração Senior ERP.

## Stack

- **Backend**: FastAPI + SQLAlchemy 2.x + Pydantic
- **ORM**: SQLite em dev (`app.db`), PostgreSQL 16 em prod (via docker-compose)
- **Templates**: Jinja2
- **Frontend**: vanilla JS (sem libs externas)
- **Integração**: SOAP Senior (`app/services/senior_connector.py`)
- **Python**: 3.11+ (prod docker), 3.13 (dev local)

## Estrutura

- `app/main.py` — bootstrap FastAPI, registro de routers, init_db
- `app/db.py` — engine SQLAlchemy, `Base`, `get_db`, `init_db`, `seed_dev_data`
- `app/config.py` — env vars + `DEV_MODE` (true quando `SENIOR_SOAP_USER`/`PASSWORD` vazios)
- `app/models/` — entidades SQLAlchemy
- `app/routers/` — endpoints REST e views HTML
- `app/services/` — integrações e lógica de negócio
- `app/templates/` — Jinja2 (design system: `billing.html`, `customers.html`)
- `app/static/` — CSS, JS, logo

## Convenções

- DEV_MODE silencioso (warning + fallback), prod barulhento (erro propagado).
- Snapshot de dados externos (Senior) em toda referência persistida.
- Migrações compatíveis com dado existente: novas colunas `NULL`able + ALTER documentado no `RUNBOOK.md`.
- UI: mesma paleta, JetBrains Mono, layouts em cards.

## Documentos

- `RUNBOOK.md` — setup dev/prod, migrações, troubleshooting
- `SISTEMA.md` — visão arquitetural

<!-- SPECKIT START -->
## Active Spec Feature

- **003 — Cache e Throttle das Chamadas Senior** — [specs/003-senior-cache-throttle/](specs/003-senior-cache-throttle/)
  - Status: **implementação em disco completa — pendente validação E2E**
  - Backend: novo módulo [`app/services/senior_cache.py`](app/services/senior_cache.py) (TimedCache stdlib, 2 instâncias singleton, semáforo); refatorações em [`app/services/senior_connector.py`](app/services/senior_connector.py) (`fetch_cost_centers`, `fetch_all_cost_centers`, `fetch_active_employees`, `_post_soap_with_retry`); 3 endpoints admin em [`app/routers/integrations.py`](app/routers/integrations.py)
  - TTL CCUs 6h, TTL funcionários 1h, max concorrência 3 SOAPs (configurável via `.env`); retry removido (falha rápida)
  - Documentos: [spec](specs/003-senior-cache-throttle/spec.md) · [plan](specs/003-senior-cache-throttle/plan.md) · [research](specs/003-senior-cache-throttle/research.md) · [data-model](specs/003-senior-cache-throttle/data-model.md) · [contracts](specs/003-senior-cache-throttle/contracts/rest-endpoints.md) · [quickstart](specs/003-senior-cache-throttle/quickstart.md) · [tasks](specs/003-senior-cache-throttle/tasks.md)

### Features concluídas

- **002 — Catálogo de EPIs e Pedido de Compra com Solicitação** — [specs/002-epi-catalog-orders/](specs/002-epi-catalog-orders/) — `/catalogo-epis` + `/epis` v2 com cartesiano, Excel auto-gerado ao salvar, email se SMTP. Migração 002 aplicada.

- **001 — Fluxo de Compra de EPIs por Funcionário** — [specs/001-epi-purchase-flow/](specs/001-epi-purchase-flow/) — `/epis` com multi-select de funcionários e produto cartesiano funcionário×item, revalidação server-side, migração 001 aplicada.

### Features concluídas

- **001 — Fluxo de Compra de EPIs por Funcionário** — [specs/001-epi-purchase-flow/](specs/001-epi-purchase-flow/)
  - Tela `/epis` com multi-select de funcionários e produto cartesiano funcionário×item, revalidação server-side ao salvar, migração 001 aplicada
<!-- SPECKIT END -->
