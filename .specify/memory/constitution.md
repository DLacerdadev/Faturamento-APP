# Project Constitution — FATURAMENTO-APP

Princípios não-negociáveis para qualquer feature.

## P1 — Senior é a fonte da verdade
Dados de funcionários, centros de custo e folha vêm do Senior ERP via integração SOAP (`app/services/senior_connector.py`). Nada de cadastro paralelo. Em DEV_MODE, fallback para SQLite local é tolerado, mas a fonte canônica continua sendo o Senior.

## P2 — DEV_MODE silencioso, prod barulhento
Em DEV_MODE (sem `SENIOR_SOAP_USER`/`SENIOR_SOAP_PASSWORD`), endpoints que dependem do Senior degradam para SQLite/listas vazias com `logger.warning`. Em produção, falha do Senior **não** pode ser silenciada com lista vazia — o usuário deve ver o erro e poder tentar de novo.

## P3 — Stack consistente
Backend: FastAPI + SQLAlchemy + Pydantic. Templates: Jinja2 com design system existente (`billing.html`, `customers.html`). Frontend: vanilla JS — nenhum framework adicional. Persistência: SQLAlchemy ORM contra SQLite (dev) ou PostgreSQL (prod).

## P4 — Snapshot de dados externos
Toda referência persistida a entidade do Senior (funcionário, CCU) deve guardar snapshot (matrícula/código + nome) no momento do salvamento. Isso permite que a tela continue consultável mesmo se a entidade for desligada/renomeada no Senior depois.

## P5 — Migrações compatíveis com dado existente
Alterações de esquema não podem quebrar dados já persistidos. ALTER TABLE com `NULL`/`DEFAULT` para colunas novas. Nenhuma migração pode exigir intervenção manual em produção sem documentação no `RUNBOOK.md`.

## P6 — UI mantém o design system
Toda tela nova herda visual das telas existentes (cores, tipografia JetBrains Mono, layouts com cards). Sem libs visuais novas. Componentes JS reutilizáveis ficam em `app/static/`.
