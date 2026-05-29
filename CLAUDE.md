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

- **001 — Fluxo de Compra de EPIs por Funcionário** — [specs/001-epi-purchase-flow/](specs/001-epi-purchase-flow/)
  - Status: **implementação em disco completa, pendente validação E2E**
  - Tela: `GET /epis` (link na nav)
  - Backend: `POST/PUT /api/epi-purchases` aceitam `{codccu, employees[], items[]}` e geram cartesiano; revalidação server-side com `409` se algum funcionário deixou de estar ativo
  - Migração SQL aplicada em `app.db` (3 colunas novas + 2 índices); detalhes em [RUNBOOK.md](RUNBOOK.md#migrações)
  - Documentos: [spec](specs/001-epi-purchase-flow/spec.md) · [plan](specs/001-epi-purchase-flow/plan.md) · [research](specs/001-epi-purchase-flow/research.md) · [data-model](specs/001-epi-purchase-flow/data-model.md) · [contracts](specs/001-epi-purchase-flow/contracts/rest-endpoints.md) · [quickstart](specs/001-epi-purchase-flow/quickstart.md) · [tasks](specs/001-epi-purchase-flow/tasks.md)
<!-- SPECKIT END -->
