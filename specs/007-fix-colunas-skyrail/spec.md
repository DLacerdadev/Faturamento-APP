# Feature Specification: Correção das Colunas Ausentes no Faturamento Skyrail

**Feature ID**: 007-fix-colunas-skyrail
**Created**: 2026-07-25
**Status**: Draft
**Spec File**: spec.md
**Prioridade**: URGENTE / Imediato (item 33)
**Onda de execução**: 0 (roda logo após 006; mergeada ANTES da frota paralela)
**Agente**: 1 agente dedicado

## Overview

Na geração do faturamento do cliente **Skyrail**, algumas colunas não estão sendo incluídas no
arquivo final. O faturamento por cliente é dirigido por um `BillingModel` criado por upload de
planilha (Contrato C1): `parse_model_xlsx()` extrai a estrutura, `derive_colunas()` deriva a lista
de colunas do tipo `campo`, e `billing_to_femsa_excel()` / `_render_por_estrutura()` renderizam.
Colunas "somem" quando o cabeçalho da planilha Skyrail **não casa** com uma coluna conhecida
(vira `constante`/`vazio` em vez de `campo`), ou quando a `fonte` do campo não existe no DataFrame
montado — resultando em coluna sem dados ou ausente. Esta spec investiga a causa raiz com o
template real (`docs/modelos/skyrail_abril.xlsx`) e corrige, com teste de regressão.

Esta correção precede a reescrita Univer (009) **de propósito**: ambas mexem no núcleo de
exportação (`excel_export.py`, `model_structure.py`); corrigir antes evita conflito e entrega o
urgente primeiro.

## User Scenarios & Testing

### Primary Flow

1. Gestor gera o faturamento da Skyrail para uma competência (`/senior/billing/export-skyrail`).
2. O Excel resultante contém **todas** as colunas do layout Skyrail, preenchidas corretamente.

### Acceptance Scenarios

- **Scenario 1**: Given o `BillingModel` da Skyrail (estrutura do `skyrail_abril.xlsx`), When gero o
  faturamento, Then toda coluna esperada do layout aparece no arquivo com o dado correto (não vazia).
- **Scenario 2**: Given uma coluna cujo cabeçalho usa sinônimo/variação (acento, espaço, caixa),
  When o modelo é interpretado, Then ela é classificada como `campo` com a `fonte` canônica certa.
- **Scenario 3**: Given uma coluna realmente customizada da Skyrail sem fonte no sistema, When gero,
  Then o comportamento é explícito (mapeada por sinônimo, OU reportada como aviso — nunca sumindo em silêncio).

### Edge Cases

- Cabeçalho em duas linhas (header_rows) — a Skyrail tem bloco de título acima.
- Coluna existe na estrutura mas fonte não é criada no DataFrame → hoje fica vazia.
- Modelo com `arquivo_template` (opção 2) vs modelo reconstruído (opção 1).

## Functional Requirements

- **FR-1**: Reproduzir o bug com o template real e identificar a causa raiz exata (documentar).
- **FR-2**: Garantir que todas as colunas do layout Skyrail sejam classificadas e renderizadas.
- **FR-3**: Ampliar o casamento de cabeçalho (`_casar_fonte`/`_SINONIMOS`) e/ou o conjunto de
  colunas conhecidas para cobrir as variações da Skyrail — sem quebrar FEMSA/Geral.
- **FR-4**: Tornar visível qualquer coluna `campo` cuja `fonte` não exista no DataFrame (log/aviso),
  eliminando o "sumiço silencioso".
- **FR-5**: Não regredir os outros modelos (FEMSA, Geral Total) — verificado por teste.

## Success Criteria

- **SC-1**: O Excel da Skyrail passa a conter 100% das colunas do layout, preenchidas.
- **SC-2**: Teste de regressão com o `skyrail_abril.xlsx` (ou fixture derivada anonimizada) passa.
- **SC-3**: Testes dos modelos FEMSA/Geral continuam verdes (sem regressão).

## Key Entities

- **BillingModel** (`estrutura` C1, `colunas`, `campos_config`) — modelo da Skyrail.
- **Estrutura C1** — colunas `{letra, tipo, fonte, header, template, valor}`.

## Assumptions

- O bug está na cadeia parse → derive → render (não em dados do Senior).
- O `skyrail_abril.xlsx` em `docs/modelos/` representa o layout com o problema.

## Out of Scope

- Reescrita do motor de exportação (isso é a spec 009). Aqui é correção cirúrgica.
- Novas colunas de negócio além das que o layout Skyrail já exige.

## Dependencies

- Spec 006 (harness) para escrever o teste de regressão.

## File Ownership / Boundaries

**Edita (exclusivo desta spec)**:
- `app/services/model_structure.py` (casamento de cabeçalho / sinônimos / validação)
- `app/services/excel_export.py` (colunas conhecidas / render por estrutura / avisos)
- `app/routers/billing_models.py` (aviso pós-upload de coluna não reconhecida — se necessário)
- `app/routers/integrations.py` (apenas `_build_billing_export` / caminho `skyrail`, se necessário)

**Cria**: `tests/test_skyrail_columns.py`, `docs/modelos/POSTMORTEM-skyrail-colunas.md`.

**Não toca**: nenhum arquivo das specs 006/008/009/010. (009 lê `excel_export.py`/`model_structure.py`
mas só começa **depois** deste merge, partindo da base já corrigida.)

## Test Plan (definição de "100% concluída")

- Teste de regressão gera o faturamento Skyrail a partir de fixture e assere presença + preenchimento
  de todas as colunas do layout.
- Teste de não-regressão para FEMSA/Geral.
- `pytest` verde.
