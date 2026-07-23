# Implementation Plan: Relatório de Conciliação Contábil

**Feature ID**: 004-relatorio-conciliacao
**Status**: Draft
**Spec**: [spec.md](spec.md)

## Technical Context

- **Language/Runtime**: Python 3.11+ (prod docker), 3.13 (dev local)
- **Backend framework**: FastAPI (router novo `app/routers/conciliacao.py`)
- **ORM / Persistence**: SQLAlchemy 2.x — SQLite (dev) / PostgreSQL 16 (prod); 1 tabela nova (`codcal_classifications`)
- **Frontend**: Jinja2 (`app/templates/conciliacao.html` herdando `base.html`) + vanilla JS (poll de job, drill-down)
- **External integrations**: WS SOAP Senior via `app/services/senior_connector.py::fetch_payroll(periodo, numemp, codccu, progress_cb)` — cada evento retorna `codcal`; lista de CCUs via `fetch_all_cost_centers` (cache 6h da feature 003)
- **Async**: reutiliza `app/services/export_jobs.py` (`create_job/set_progress/finish_ok/finish_error`) + thread daemon, mesmo padrão de `/senior/billing/export-async`
- **Excel**: openpyxl (mesma lib de `excel_export.py`)
- **RBAC/Audit**: `require_role(request, db, "gestor")` + `audit(request, acao, ...)`
- **Test approach**: manual via quickstart (a suíte automatizada é a Etapa 4 do Plano de Execução — spec 005; esta feature entra como candidata a caso ponta a ponta lá)
- **Performance targets**: conciliação completa < 2 min (SC-4) — mesma ordem das exportações atuais com throttle/cache da feature 003
- **Compatibility constraints**: migração aditiva (CREATE TABLE apenas), documentada no RUNBOOK.md (P5); nenhum dado por funcionário exposto (FR-2)

Sem itens NEEDS CLARIFICATION — as 3 decisões de escopo foram resolvidas no `/speckit-clarify` (sessão 2026-07-22 na spec).

## Constitution Check

- **P1 (Senior é a fonte da verdade)**: ✅ Números vêm exclusivamente de `fetch_payroll` no momento da geração (FR-10). Nada de armazenamento paralelo de folha; a única persistência nova é a *classificação* de codcal, que é configuração nossa, não dado do Senior.
- **P2 (DEV_MODE silencioso, prod barulhento)**: ✅ Em DEV_MODE a geração usa o fallback local do próprio `fetch_payroll`. Em prod, falha do WS → `finish_error(job_id, msg)` e a tela exibe o erro com opção de tentar de novo — nunca relatório parcial (edge case da spec).
- **P3 (Stack consistente)**: ✅ FastAPI + SQLAlchemy + Jinja2 + vanilla JS; nenhuma lib nova.
- **P4 (Snapshot de dados externos)**: ✅ A tabela de classificação guarda `codcal` + `descricao` (rótulo humano) no salvamento. CCU no filtro exportado grava código + nome na planilha.
- **P5 (Migrações compatíveis)**: ✅ Só `CREATE TABLE codcal_classifications` (aditiva); ALTER documentado no RUNBOOK.md.
- **P6 (UI mantém design system)**: ✅ `conciliacao.html` herda `base.html` (sidebar + topbar, midnight navy/âmbar, cards); JS de poll de job segue o padrão da tela de exportação.

Nenhuma violação — Complexity Tracking vazio.

## Phase 0 — Research

See [research.md](research.md). Resolves all NEEDS CLARIFICATION items.

## Phase 1 — Design Artifacts

- [data-model.md](data-model.md) — entities, columns, relationships, migrations
- [contracts/rest-endpoints.md](contracts/rest-endpoints.md) — REST endpoint contracts, request/response schemas
- [quickstart.md](quickstart.md) — how to exercise the feature end-to-end

## Phase 2 — Implementation Approach

1. **Modelo + migração**: `app/models/codcal_classification.py` (padrão BenefitEvent); registrar em `app/models/__init__.py`; `CREATE TABLE` idempotente no `init_db`; nota de migração no RUNBOOK.md.
2. **Serviço de conciliação**: `app/services/conciliacao.py` — recebe a lista de registros do `fetch_payroll`, agrega por codcal → evento (valor total + qtde de lançamentos, sem funcionários), casa com as classificações do banco e monta o resultado (totais, ponte, resíduo, status fechada/incompleta, codcals não classificados). Função pura sobre dados já buscados — é o alvo natural de teste unitário na spec 005.
3. **Job assíncrono**: `_run_conciliacao_job` no router — chama `fetch_payroll` (todos os CCUs por padrão via `fetch_all_cost_centers`, ou o CCU filtrado), repassa `progress_cb` → `set_progress`, serializa o resultado como JSON e `finish_ok(..., media_type="application/json")`.
4. **Endpoints**: `POST /api/conciliacao/gerar` (job), reuso de `export-status` genérico ou endpoint próprio de status, `GET /api/conciliacao/resultado/{job_id}` (JSON p/ tela), `GET /api/conciliacao/export/{job_id}` (converte o JSON retido do job em .xlsx — **sem segunda ida ao WS**), CRUD de classificação (`GET/PUT /api/conciliacao/classificacoes`). Tudo gestor+ com `audit()`.
5. **Tela**: `app/templates/conciliacao.html` — seletor de competência + CCU, barra de progresso do job, cards de totais (competência inteira / recorte mensal / resíduo / status), tabela de decomposição por codcal com drill-down por evento, edição inline da classificação (gestor+), botão exportar planilha. Item "Conciliação" no menu do `base.html`; `include_router` no `main.py`.
6. **Heurística inicial**: na primeira geração, codcals sem linha na tabela aparecem "não classificados"; a tela sugere classificação (ex.: codcal com evento "SALARIO DIA" → provável mensal) mas quem grava é o gestor — nada é classificado silenciosamente (SC-3).
7. **Documento de conciliação (FR-8/FR-9)**: `docs/CONCILIACAO.md` com a explicação do recorte + 2 exemplos numéricos reais (apenas totais/codcal — sem dados pessoais), seção de aprovação e registro do follow-up TIPCAL na Senior. Preenchido com números reais na validação (quickstart passo final).
8. **Validação real**: gerar a conciliação de uma competência fechada, conferir resíduo zero com classificação completa e bater o recorte mensal com o relatório Senior da mesma competência (SC-1/SC-2) junto à contabilidade.

## Complexity Tracking

| Principle | Deviation | Justification |
|---|---|---|
| — | — | — |
