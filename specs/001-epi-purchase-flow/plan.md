# Implementation Plan: Fluxo de Compra de EPIs por Funcionário

**Feature ID**: 001-epi-purchase-flow
**Status**: Ready for `/speckit-tasks`
**Spec**: [spec.md](spec.md)

## Technical Context

- **Language/Runtime**: Python 3.11+ (atual dev usa 3.13 local, prod docker usa 3.11; manter compat com 3.11)
- **Backend framework**: FastAPI
- **ORM / Persistence**: SQLAlchemy 2.x; SQLite em dev (`app.db`), PostgreSQL 16 em prod (via docker-compose)
- **Frontend**: Jinja2 + vanilla JS; reuso do design system de `billing.html` e `customers.html`
- **External integrations**: SOAP Senior — `T018CCU` (centros de custo) e `consultaRegistros` (funcionários). Ambos já implementados em `app/services/senior_connector.py`
- **Test approach**: Manual end-to-end via UI; testes de regressão via execução das demais telas (folha, faturamento, exames) após deploy
- **Performance targets**: Lista de funcionários ativos em ≤ 3s (SC-3); criação completa (10 funcionários × 3 itens = 30 linhas) em ≤ 2 min (SC-1)
- **Compatibility constraints**:
  - Linhas existentes em `epi_purchase_items` (legacy, sem vínculo de funcionário) devem continuar carregáveis
  - Linhas existentes em `epi_purchase_packages` (sem `codccu`) devem continuar carregáveis
  - Demais features (folha, faturamento, exames, benefícios) não podem regredir

Sem itens NEEDS CLARIFICATION restantes.

## Constitution Check

| Princípio | Conformidade |
|---|---|
| **P1 — Senior é a fonte da verdade** | ✅ Centros de custo e funcionários virão exclusivamente do Senior via SOAP. Nenhum cadastro paralelo. |
| **P2 — DEV_MODE silencioso, prod barulhento** | ✅ Em DEV_MODE, endpoint de funcionários ativos cai para SQLite local com warning. Em prod, erro do Senior é repassado ao usuário (FR-12) com botão de retry. |
| **P3 — Stack consistente** | ✅ FastAPI + SQLAlchemy + Pydantic + Jinja2 + vanilla JS. Sem libs novas. |
| **P4 — Snapshot de dados externos** | ✅ FR-7 e A3 já exigem snapshot de matrícula+nome do funcionário em cada linha. Adicionalmente, `codccu` no pacote serve como snapshot do contexto. |
| **P5 — Migrações compatíveis** | ✅ Novas colunas (`codccu` no pacote, `employee_numcad`/`employee_nome` no item) entram como `NULL`able. SQLAlchemy `create_all` cuida na primeira subida; para prod precisaremos de `ALTER TABLE` documentado em `RUNBOOK.md`. |
| **P6 — UI mantém o design system** | ✅ Nova tela `epis.html` herda paleta, tipografia e padrão de cards de `billing.html`. Componente multi-select implementado inline em vanilla JS. |

Sem violações. Sem entradas em Complexity Tracking.

## Phase 0 — Research

Ver [research.md](research.md). Decisões resolvidas:

- **R1 — Modelagem do cartesiano**: linha plana em `epi_purchase_items` com `employee_numcad`/`employee_nome` (não criar tabela junction).
- **R2 — Filtro `codccu` na lista de funcionários**: estender endpoint existente `/api/integrations/senior/employees` com query params `?codccu=` e `?active_only=`.
- **R3 — Definição de "ativo" no código**: função utilitária `is_employee_active(emp_row, today)` em `senior_connector.py`; sentinel `31/12/1900` tratado como sem afastamento.
- **R4 — Componente multi-select**: implementação inline em vanilla JS, baseada em checkboxes com filtro textual; padrão a ser exportado depois como utilitário em `app/static/` se outras telas precisarem.
- **R5 — Revalidação FR-13**: feita server-side comparando `numcad`s recebidos contra `fetch_active_employees(codccu)` no momento do POST/PUT; retorno 409 com lista de afetados.

## Phase 1 — Design Artifacts

- [data-model.md](data-model.md) — colunas novas, índices, migração compatível
- [contracts/rest-endpoints.md](contracts/rest-endpoints.md) — endpoints, request/response, status codes
- [quickstart.md](quickstart.md) — passo a passo para exercitar a feature end-to-end

## Phase 2 — Implementation Approach

Ordem sugerida (detalhamento fino em `/speckit-tasks`):

1. **Backend — modelo + migração**: estender `app/models/epi_purchase.py` com `codccu` em `EpiPurchasePackage` e `employee_numcad`/`employee_nome` em `EpiPurchaseItem`. Subir via `init_db()`; documentar `ALTER TABLE` para prod no `RUNBOOK.md`.
2. **Backend — helper de ativo**: adicionar `is_employee_active()` e `fetch_active_employees(codccu)` em `senior_connector.py`. Em DEV_MODE, ler de `billing_employees`/`billing_employment_contracts` no SQLite.
3. **Backend — endpoint de funcionários filtrados**: estender `GET /api/integrations/senior/employees` com params `codccu` e `active_only`. Retro-compat: chamadas sem params continuam funcionando.
4. **Backend — POST/PUT cartesiano**: alterar `app/routers/epi_purchases.py` para aceitar `codccu`, `employees: [{numcad, nome}]`, `items: [{descricao, quantidade, valor_unitario}]`. Expandir cartesiano server-side. Antes do commit: revalidar `numcad`s via `fetch_active_employees(codccu)`; se algum mudou, retornar 409.
5. **Backend — GET enriquecido**: ajustar serializadores para devolver representação agrupada útil (funcionários distintos, itens distintos, contagem de linhas, total).
6. **Frontend — template + rota**: criar `app/templates/epis.html` (form + listagem). Adicionar rota GET `/epis` em router HTML existente (provavelmente `app/routers/views.py` ou similar — verificar). Reuso visual do `billing.html`.
7. **Frontend — JS da tela**: lógica de carregamento de CCUs, lista de funcionários reativa ao CCU escolhido, multi-select com busca, adição/remoção de itens, validação client-side, submissão.
8. **Manual QA**: rodar o quickstart contra DEV_MODE (SQLite seed) e contra Senior real. Confirmar SC-1 (≤ 2 min), SC-3 (≤ 3s lista), SC-6 (zero regressão).
9. **Cleanup**: remover/migrar dados legados se houver — confirmar com usuário antes; provavelmente manter NULLs.

## Complexity Tracking

| Princípio | Deviation | Justification |
|---|---|---|
| — | — | — |
