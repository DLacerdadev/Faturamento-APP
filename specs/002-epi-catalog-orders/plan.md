# Implementation Plan: Catálogo de EPIs e Pedido de Compra com Solicitação

**Feature ID**: 002-epi-catalog-orders
**Status**: Ready for `/speckit-tasks`
**Spec**: [spec.md](spec.md)
**Predecessora**: [001-epi-purchase-flow](../001-epi-purchase-flow/plan.md)

## Technical Context

- **Language/Runtime**: Python 3.11+ (dev local 3.13)
- **Backend framework**: FastAPI + SQLAlchemy 2.x + Pydantic
- **ORM / Persistence**: SQLite (dev) / PostgreSQL 16 (prod)
- **Frontend**: Jinja2 + vanilla JS — reuso do design system de [epis.html](../../app/templates/epis.html) e [base.html](../../app/templates/base.html)
- **External integrations**: SOAP Senior (T018CCU + consultaRegistros) — reaproveitamento total da feature 001
- **Geração Excel**: `openpyxl` (já instalado)
- **Email**: stdlib `smtplib` + `email.mime` — sem dependência nova
- **Test approach**: Manual E2E + regressão das telas existentes
- **Performance targets**: cálculo de totais em tempo real ≤ 200ms (SC-3); geração de Excel em ≤ 2s para 50+ linhas
- **Compatibility constraints**: pacotes da feature 001 (sem `epi_id`) devem continuar carregando e aparecer marcados como "legado"

Sem itens NEEDS CLARIFICATION — Q1/Q2/Q3 e TD-1..TD-9 estão fechados na spec.

## Constitution Check

| Princípio | Conformidade |
|---|---|
| **P1 — Senior é a fonte da verdade** | ✅ Reusa integralmente os endpoints de CCU e funcionários da 001. Sem cadastro paralelo de pessoas. Catálogo de EPIs é dado **interno do sistema**, não vem do Senior — é um cadastro local legítimo (não viola P1). |
| **P2 — DEV_MODE silencioso, prod barulhento** | ✅ Catálogo é 100% local (independe do Senior). SMTP segue o mesmo princípio: sem `SMTP_HOST` → UI esmaecida silenciosamente; com `SMTP_HOST` → erros propagados ao usuário. |
| **P3 — Stack consistente** | ✅ FastAPI + SQLAlchemy + Pydantic + Jinja2 + vanilla JS. `openpyxl` e `smtplib` já existem. Nenhuma lib nova. |
| **P4 — Snapshot de dados externos** | ✅ Solicitante = snapshot string (TD-5). Valor unitário no pedido = snapshot do catálogo (A2). Funcionários continuam com snapshot da 001. |
| **P5 — Migrações compatíveis** | ✅ Novas colunas nullable (`epi_id`, `tamanho`, `valor_total_compra_geral`, etc.). Novas tabelas adicionadas sem alterar existentes. `ALTER TABLE` documentado no RUNBOOK como "Migração 002". |
| **P6 — UI mantém o design system** | ✅ Nova tela `/catalogo-epis` reusa `base.html` (mesma paleta, JetBrains Mono, padrão de cards). Combobox de catálogo no `/epis` reusa o padrão criado para CCU. |

Sem violações. Complexity Tracking vazio.

## Phase 0 — Research

Ver [research.md](research.md). 9 decisões técnicas (TD-1 a TD-9) já fechadas na spec; o research detalha racional e alternativas consideradas para cada uma.

## Phase 1 — Design Artifacts

- [data-model.md](data-model.md) — 2 tabelas novas (`epi_catalog`, `epi_catalog_sizes`) + extensões em `epi_purchase_packages` e `epi_purchase_items`. Migração compatível.
- [contracts/rest-endpoints.md](contracts/rest-endpoints.md) — 5 endpoints novos (CRUD catálogo) + 2 endpoints de solicitação (download/email) + extensão dos POST/PUT de compra para aceitar `epi_id`/`tamanho`.
- [quickstart.md](quickstart.md) — cenários E2E para cadastrar EPI, criar compra com catálogo, baixar solicitação e enviar por email.

## Phase 2 — Implementation Approach

Ordem sugerida (detalhamento em `/speckit-tasks`):

### Setup
1. Aplicar migração 002 (ALTER TABLE + CREATE TABLE).

### Foundational (bloqueia todas as user stories)
2. Atualizar modelo SQLAlchemy: novas classes `EpiCatalog` + `EpiCatalogSize`, novos campos em `EpiPurchasePackage` e `EpiPurchaseItem`.
3. Documentar migração no `RUNBOOK.md` (seção "Migração 002").

### US1 — Catálogo CRUD (P1)
4. Schemas Pydantic + endpoints CRUD em novo router `app/routers/epi_catalog.py`.
5. Template `app/templates/catalogo_epis.html` + rota GET `/catalogo-epis` em `main.py`.
6. JS inline: form com tamanhos dinâmicos, listagem com busca, toggles ativo/inativo.
7. Link "Catálogo de EPIs" na nav do `base.html`.

### US2 — Compra com catálogo + cálculo persistido (P1)
8. Estender schemas Pydantic em `epi_purchases.py` para aceitar `epi_id` + `tamanho` por item.
9. Atualizar POST/PUT: validação contra catálogo, snapshot de valor, cálculo de `quantidade_total_geral` e `valor_total_compra_geral`, persistência junto com a compra.
10. Atualizar `epis.html`: substituir campos livres por dropdown EPI + dropdown tamanho dependente. Aviso de divergência de valor (FR-8). Sumários de qtde/valor por item e total geral em tempo real.
11. Listagem: nova coluna com badge "Legado" quando `epi_id IS NULL`.

### US3 — Solicitação de compra Excel + email (P2)
12. Criar `app/services/epi_solicitation_excel.py` com `generate_solicitacao_xlsx(pkg) -> bytes` (cabeçalho + tabela itens + totais + funcionários).
13. Persistir nome do arquivo gerado (`solicitacao_filename`) + timestamp no pacote.
14. Endpoint `GET /api/epi-purchases/{id}/solicitacao` para download.
15. Detecção de SMTP em `app/config.py` (var `SMTP_HOST` etc.) + função `send_solicitacao_email` em novo `app/services/email_sender.py`.
16. Endpoint `POST /api/epi-purchases/{id}/solicitacao/email` aceitando `to` opcional.
17. Frontend: após salvar, mostrar "Baixar solicitação" e (se SMTP) "Enviar por email" com input de destinatário pré-preenchido.

### US4 — Legados da 001 (P3)
18. Listagem: badge "Legado" + botões de solicitação desabilitados com tooltip (FR-17).

### Polish
19. Smoke test de regressão: folha, faturamento, exames, /epis, /catalogo-epis.
20. Atualizar `CLAUDE.md`.

## Complexity Tracking

| Princípio | Deviation | Justification |
|---|---|---|
| — | — | — |
