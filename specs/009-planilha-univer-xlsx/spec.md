# Feature Specification: Planilha de Faturamento Interativa (Univer) + Motor de Import/Export XLSX

**Feature ID**: 009-planilha-univer-xlsx
**Created**: 2026-07-25
**Status**: Draft
**Spec File**: spec.md
**Itens do plano**: 29 (nova planilha de faturamento — infraestrutura de grid + import/export)
**Onda de execução**: 1 (frota paralela — junto com 008 e 010)
**Agente**: 1 agente dedicado
**Contrato consumido**: [`specs/_contracts/grid-ops-protocol.md`](../_contracts/grid-ops-protocol.md)

## Overview

Hoje o faturamento é uma **tabela HTML estática** renderizada pelo backend; import/export são
dirigidos por pandas/openpyxl (`excel_export.py`, `import_engine.py`, estrutura C1 em
`model_structure.py`). Esta spec cria uma **nova tela de planilha interativa** usando **Univer**
(grid tipo Excel, self-hosted — P3 relaxado com aprovação do dono), na qual todos os dados do
faturamento passam a ser carregados, editados e exportados. Entrega o **motor de
serialização** que converte entre: (a) XLSX ⇄ (b) modelo Univer (JSON) ⇄ (c) `BillingModel`/estrutura
C1 já existentes — reutilizando `excel_export.py`/`model_structure.py` **sem editá-los**
(consumo por import).

Esta tela também expõe o **ponto de montagem da IA** (`<div id="ai-assistant-root">` +
`window.FaturamentoGrid`) definido no contrato, para a spec 010 acoplar o chat sem tocar nesta
página.

## User Scenarios & Testing

### Primary Flow

1. Gestor abre a nova tela `/faturamento/planilha`.
2. Seleciona o modelo/competência/CCU e clica em **"Consultar faturamento"**.
3. A planilha carrega no grid Univer **exatamente o que seria exportado** — o modelo de Excel
   (colunas, cabeçalhos, merges, formatos e dados) renderizado fielmente. É um preview do `.xlsx`.
4. Gestor confere, edita células/colunas se quiser, e então exporta o `.xlsx` (idêntico ao preview);
   ou importa um `.xlsx` que é convertido para o grid.

### Acceptance Scenarios

- **Scenario 1**: Given um `BillingModel`, When abro a planilha, Then o grid mostra as colunas do
  modelo com dados carregados e edição funcional.
- **Scenario 2**: Given edições no grid, When exporto, Then o `.xlsx` gerado reflete o conteúdo do
  grid e preserva a estrutura C1 (headers, merges, formatos) do modelo.
- **Scenario 3**: Given um `.xlsx` importado, When carrego, Then o grid é populado corretamente
  (round-trip XLSX→grid→XLSX estável).
- **Scenario 4**: Given a página aberta, Then existe `<div id="ai-assistant-root">` e
  `window.FaturamentoGrid` com `getSnapshot/applyOps/highlight` conforme o contrato.

### Edge Cases

- Modelo com `arquivo_template` (opção 2) vs reconstruído (opção 1).
- Planilhas grandes (muitas linhas) — performance do grid e do serializador.
- `applyOps` com operação inválida → retorna `ApplyResult` com `errors`, sem corromper o modelo.
- Sem CDN externo (CSP): bundle Univer servido localmente de `app/static/vendor/univer/`.

## Functional Requirements

- **FR-0**: Botão **"Consultar faturamento"** (seleção de modelo/competência/CCU) que renderiza no
  grid Univer o **preview fiel do `.xlsx` que será exportado** — o mesmo bytes-para-grid do
  serializador, garantindo "o que você vê é o que exporta". Endpoint de preview reaproveita
  `export_jobs` se a montagem for longa.
- **FR-1**: Nova página `/faturamento/planilha` (herda `base.html`, design system) com grid Univer.
- **FR-2**: Bundle Univer **self-hosted** em `app/static/vendor/univer/` (nada de CDN; respeita CSP).
- **FR-3**: Serviço `grid_serialization.py`: `xlsx_to_grid(bytes)`, `grid_to_xlsx(grid, model)`,
  `model_to_grid(BillingModel)`, `grid_to_model_ops(...)` — reutilizando funções de
  `excel_export.py`/`model_structure.py` por import (sem editá-las).
- **FR-4**: Endpoint `POST /api/faturamento/grid/apply-ops` que valida e aplica `GridOp[]` sobre o
  modelo/estrutura, conforme o contrato (`ApplyResult`).
- **FR-5**: Endpoints de carga/salvamento do grid e export `.xlsx` (reaproveitando `export_jobs`
  para exportações longas).
- **FR-6**: Expor `window.FaturamentoGrid` (getSnapshot/applyOps/highlight) e o `<div>` de montagem
  da IA — o `getSnapshot` NÃO inclui PII (§5 do contrato).
- **FR-7**: RBAC `gestor+` nas rotas; ações auditadas.

## Success Criteria

- **SC-0**: "Consultar faturamento" mostra no grid o preview idêntico ao `.xlsx` exportado em
  seguida (mesmas colunas/valores) — verificado por teste de igualdade preview × export.
- **SC-1**: Round-trip XLSX→grid→XLSX preserva estrutura e valores (teste de igualdade tolerante).
- **SC-2**: `apply-ops` aplica corretamente add/remove/rename/move column, set_field_config e
  set_model_param, com validação e `ApplyResult`.
- **SC-3**: A tela abre com o contrato de UI satisfeito (div + API global).

## Key Entities

- **GridSnapshot / GridOp / ApplyResult** — ver contrato.
- **BillingModel / Estrutura C1** — reutilizados como destino canônico (não redefinidos).

## Assumptions

- Conversão XLSX permanece no backend (openpyxl já disponível); Univer serializa/renderiza no front.
- O modelo canônico continua sendo `BillingModel`; a planilha é uma nova camada de edição sobre ele.

## Out of Scope

- Substituir/apagar a tela `billing.html` atual (coexistem; migração posterior).
- Lógica de IA (é a spec 010) — aqui só o ponto de montagem e a API do grid.
- Cálculos de faturamento (permanecem em `billing_processor.py`/`excel_export.py`).

## Dependencies

- Spec 006 (harness) para testes.
- Contrato `grid-ops-protocol.md` (compartilhado com 010).
- P3 relaxado (Univer self-hosted) — orquestradora registra a exceção na constituição.

## File Ownership / Boundaries

**Cria (exclusivo desta spec)**:
- `app/services/grid_serialization.py`
- `app/routers/faturamento_grid.py`
- `app/templates/faturamento_planilha.html` (inclui o `<div id="ai-assistant-root">` e
  `<script src="/static/js/ai_assistant.js">` por contrato)
- `app/static/js/faturamento_planilha.js`
- `app/static/vendor/univer/*` (bundle self-hosted)
- `tests/test_grid_serialization.py`

**Lê (NÃO edita)**: `app/services/excel_export.py`, `app/services/model_structure.py`,
`app/models/billing_model.py`.

**Notas de integração (orquestradora aplica no merge)**: registrar router em `app/main.py`;
link no menu (`base.html`); eventuais deps de conversão em `requirements.txt` (evitar — reusar openpyxl).

**Não toca**: arquivos das specs 007/008/010. Em especial, **não edita** `ai_assistant.js` (dono: 010).

## Test Plan (definição de "100% concluída")

- Testes de serialização (round-trip XLSX/grid/model) e de `apply-ops` (todas as ops v1) com
  fixtures anônimas. Teste que garante presença do contrato de UI (div + assinatura da API).
- `pytest` verde.
