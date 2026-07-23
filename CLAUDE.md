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

- **004 — Relatório de Conciliação Contábil** — [specs/004-relatorio-conciliacao/](specs/004-relatorio-conciliacao/)
  - Status: **implementado (US1–US3) — pendente validação real em prod (T025/US4)**
  - Etapa 3 do Plano de Execução ([docs/PLANO-EXECUCAO-STATUS.md](docs/PLANO-EXECUCAO-STATUS.md)): tela `/conciliacao` (gestor+) mostra a ponte competência inteira × recorte mensal Senior, decomposição por codcal→evento (agregado, sem dados de funcionário)
  - Backend: model [`app/models/codcal_classification.py`](app/models/codcal_classification.py) (tabela `codcal_classifications`, criada via `create_all`); serviço puro [`app/services/conciliacao.py`](app/services/conciliacao.py) (`montar_conciliacao`, `conciliacao_para_xlsx`); router [`app/routers/conciliacao.py`](app/routers/conciliacao.py) — geração via job assíncrono (WS ao vivo, `export_jobs`), resultado JSON não persistido, export .xlsx derivado do job; classificação global de codcal (CRUD gestor+, auditado)
  - Doc de critérios: [docs/CONCILIACAO.md](docs/CONCILIACAO.md) (exemplos reais + aprovação pendentes)
  - Documentos: [spec](specs/004-relatorio-conciliacao/spec.md) · [plan](specs/004-relatorio-conciliacao/plan.md) · [research](specs/004-relatorio-conciliacao/research.md) · [data-model](specs/004-relatorio-conciliacao/data-model.md) · [contracts](specs/004-relatorio-conciliacao/contracts/rest-endpoints.md) · [quickstart](specs/004-relatorio-conciliacao/quickstart.md) · [tasks](specs/004-relatorio-conciliacao/tasks.md)

### Features anteriores

- **003 — Cache e Throttle das Chamadas Senior** — [specs/003-senior-cache-throttle/](specs/003-senior-cache-throttle/) — implementação em disco completa, **pendente validação E2E**. `senior_cache.py` (TTL CCUs 6h, funcionários 1h, máx. 3 SOAPs), refatorações no `senior_connector.py`, 3 endpoints admin.
- **002 — Catálogo de EPIs e Pedido de Compra com Solicitação** — [specs/002-epi-catalog-orders/](specs/002-epi-catalog-orders/) — `/catalogo-epis` + `/epis` v2 com cartesiano, Excel auto-gerado ao salvar, email se SMTP. Migração 002 aplicada.
- **001 — Fluxo de Compra de EPIs por Funcionário** — [specs/001-epi-purchase-flow/](specs/001-epi-purchase-flow/) — `/epis` com multi-select de funcionários e produto cartesiano funcionário×item, revalidação server-side, migração 001 aplicada.
<!-- SPECKIT END -->
