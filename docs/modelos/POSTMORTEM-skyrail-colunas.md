# Postmortem — Colunas ausentes no faturamento Skyrail (spec 007)

**Data:** 2026-07-25
**Severidade:** URGENTE (item 33 do Plano de Execução, Onda 0)
**Status:** Corrigido, com teste de regressão (`tests/test_skyrail_columns.py`)

## Resumo

Ao gerar o faturamento do cliente **Skyrail** (`/senior/billing/export-skyrail`),
várias colunas do layout saíam **vazias** ou **sumiam** do arquivo. O faturamento
Skyrail é dirigido por um `BillingModel` criado por upload da planilha oficial
(Contrato C1): `parse_model_xlsx()` extrai a estrutura → `derive_colunas()` deriva
as colunas `campo` → `billing_to_femsa_excel()` / `_render_por_estrutura()`
renderizam no layout original.

Reproduzido com o template real `docs/modelos/skyrail_abril.xlsx` (29 colunas,
cabeçalho em 2 linhas, 116 funcionários). Antes da correção, **14 colunas** saíam
em branco na renderização SEM nenhum aviso — e, pior, o layout tratava colunas
per-funcionário como constante fixa. Depois: **todas as 29 colunas ficam presentes
no arquivo** (cabeçalho/estilo/largura); as colunas com fonte real no sistema
preenchem quando há dado; as colunas per-funcionário SEM fonte ficam presentes
porém com **células de dado vazias** (nunca preenchidas com dado fabricado) e
emitem **aviso explícito** no parse e no render.

## Causa raiz

Duas causas independentes, ambas terminando em "coluna vazia sem explicação":

### Causa 1 — colunas de benefício/desconto por funcionário classificadas como `constante` e depois apagadas em silêncio

O layout Skyrail tem colunas de valor **por funcionário** cujo cabeçalho NÃO
corresponde a nenhum campo canônico do sistema (`GERAL_COLUMNS`):

| Coluna | Header | Valores no modelo (variam!) |
|--------|--------|------------------------------|
| I | CAFÉ | 161,6 (constante) |
| J | LANCHE | 161,6 (constante) |
| K | ALMOÇO | 576,8 (constante) |
| L | VT +REF SABADOS | **154 / 0 / 222,4 …** |
| N | SAÚDE DO TRAB. | 310,08 |
| P | UNIFORMES, MATERIAIS E OUTROS | 0 / 537,54 … |
| W | REFEIÇÃO | 2 |
| X | TRANSPORTE | 60 |
| Y | FALTAS | 0 / 109,85 … |
| Z | ATRASOS | **89,88 / 0 / 9,84 …** |
| AA | DSR | 0 / 109,85 … |

O parser (`parse_model_xlsx`) só olha a **primeira linha de dados**. Como o
cabeçalho não casa e a célula tem número, a coluna era classificada como
`"constante"` com `valor` = valor da 1ª linha. Na renderização,
`_resolver_constante()` trata **todo número não-taxa como "valor de exemplo do
template"** e retorna `escrever=False` → **a célula fica em branco**.

Resultado: colunas que na verdade variam por funcionário (ATRASOS, VT+REF
SÁBADOS…) sumiam completamente, e como as fórmulas de SUBTOTAL (O, T, AB, AD)
as referenciam, o total mensal também saía errado.

### Causa 2 — coluna `campo` sem dado no DataFrame ficava vazia EM SILÊNCIO

Colunas que casam com um campo canônico (VT→`PAGTO. VALE-TRANSPORTE (Valor)`,
SEG. DE VIDA→`SEGURO DE VIDA`, UNIFORMES→`UNIFORMES (Valor)`,
EPIS→`EPIS (Valor)`) eram classificadas corretamente como `campo`. Mas quando o
DataFrame montado não tinha valor para elas (sem eventos Senior / sem pedidos
confirmados no período), a célula saía vazia **sem nenhum log ou aviso**
(`serie = df[fonte] if fonte in df.columns else None` → `None` → célula em
branco, calada). Isso mascarava configuração ou dado faltante como "bug de
coluna sumida".

## Decisão de integridade: coluna sem fonte fica PRESENTE porém VAZIA (não fabricamos dado)

O que o bug 33 pedia era **"a coluna aparecer no arquivo"** — não "a coluna
preenchida com dado inventado". Isso é crítico num faturamento:

> O modelo por estrutura (Skyrail) é um **TEMPLATE (layout)**, não a fonte de
> dados. `parse_model_xlsx` só lê a **1ª linha de dados** do template, então NÃO
> HÁ como derivar o valor per-funcionário dele. Repetir o valor da 1ª linha em
> todos os funcionários (ex.: ATRASOS do funcionário nº1 replicado pra todo
> mundo) fabricaria um número **plausível e ERRADO** na fatura — pior que vazio.

Portanto, para as colunas da Causa 1 (variam por funcionário e não têm fonte no
sistema): **a COLUNA fica presente no layout** (cabeçalho, largura, estilo,
mesclagens), mas **a célula de dado sai VAZIA**. No Excel, célula vazia referida
por `=SUM(...)` / `=C+D` conta como **0**, então os SUBTOTAL/TOTAL do modelo
fecham de forma **honesta** (sem o dado que o sistema não tem), em vez de com um
valor fabricado. Subtotais que eram **constante de verdade** capturada do modelo
continuam sendo escritos normalmente.

O preenchimento real per-funcionário dessas colunas depende de:
- **mapear a coluna a uma fonte** — evento Senior (`campos_config` / `BenefitEvent`)
  ou uma das colunas canônicas (`GERAL_COLUMNS`); ou
- a **planilha interativa** das specs 009/010 (edição per-funcionário no layout).

Enquanto isso não existe, a política é **presente + vazio + aviso**. Não
inventamos dado de faturamento.

## Correção (cirúrgica, sem reescrever o motor)

### `app/services/model_structure.py`
- Novo helper `_coluna_varia(ws, idx, data_row, max_row)`: amostra até 25 linhas
  de dados e diz se os valores **variam**.
- Em `parse_model_xlsx`, ao classificar uma coluna como `"constante"` numérica cujo
  cabeçalho não casou: se o valor **varia** entre funcionários, marca
  `"variavel": True` (não é constante de verdade — é uma coluna per-funcionário
  sem fonte no sistema) e acumula em `campos_sem_fonte`.
- Ao fim do parse, `logger.warning` lista essas colunas (coluna presente porém
  células de dado vazias — não fabrica valor a partir do template).

### `app/services/excel_export.py`
- `_resolver_constante`: constante numérica **sem** fonte (com ou sem a flag
  `variavel`) **não é escrita** — a célula de dado sai vazia. A flag `variavel`
  serve para o renderizador AVISAR (não para fabricar). Constante real (mesmo
  valor em todas as linhas, ex.: CAFÉ) e a lógica de taxa/percentual ficam
  **inalteradas**.
- Novo helper `_fonte_vazia_no_df(df, fonte)` + `logger.warning` em
  `_render_por_estrutura` **e** `_render_por_template`: toda coluna `campo` que
  sairia inteira vazia (fonte ausente no df, ou presente mas sem dado para
  nenhum funcionário) gera aviso. Além disso, coluna `constante variavel` também
  gera aviso ("coluna presente, células de dado VAZIAS"). Fim do "sumiço
  silencioso" (FR-4), sem fabricar dado.

Nenhuma mudança foi necessária em `app/routers/integrations.py`
(`_build_billing_export`) nem em `billing_models.py`: o caminho Skyrail já usa a
renderização por estrutura, e a correção mora no parse + render.

## Por que NÃO regride FEMSA / Geral

- FEMSA e GERAL são **dirigidos por colunas** (`BillingModel.estrutura = NULL`):
  nunca entram em `_render_por_estrutura` / `_resolver_constante`. O caminho
  `df.to_excel` de sempre fica intocado.
- A flag `variavel` só é setada por `parse_model_xlsx` (upload de planilha) e só
  faz `_resolver_constante`/o render **avisar** quando presente — inexistente nos
  modelos internos, e nunca escreve valor.
- Testes de não-regressão (`test_nao_regride_femsa` / `test_nao_regride_geral`)
  asseguram que as listas de colunas continuam idênticas byte a byte.

## Colunas que continuam podendo sair vazias (e por quê — não é bug)

Todas ficam **presentes** no arquivo (cabeçalho/estilo/largura); o que varia é se
a célula de dado é preenchida:

- **Benefícios/descontos sem fonte (CAFÉ, LANCHE, ALMOÇO, VT+REF SÁBADOS, SAÚDE
  DO TRAB., NRs, REFEIÇÃO, TRANSPORTE, FALTAS, ATRASOS, DSR, UNIFORMES/MATERIAIS):**
  per-funcionário, sem fonte no sistema → coluna presente, célula de dado VAZIA,
  **com aviso**. Não fabricamos valor a partir do template (ver "Decisão de
  integridade" acima). Preenchimento real depende de mapear a fonte (evento
  Senior / `campos_config`) ou da planilha interativa (specs 009/010).
- **VT / SEG. DE VIDA / UNIFORMES / EPIS:** são `campo` alimentados por dado do
  Senior / pedidos confirmados. Vazias só quando não há esse dado no período —
  agora **com aviso**. O valor do SEGURO DE VIDA é decisão pendente da Etapa 2.

## Teste / PII / offline

`tests/test_skyrail_columns.py` **não** usa `skyrail_abril.xlsx` (gitignored, com
PII real). Constrói em memória, via `_build_synthetic_skyrail_xlsx()`, um `.xlsx`
sintético que reproduz o MESMO padrão estrutural do bug com dados 100% fictícios
(FUNC UM/DOIS/TRÊS): topo institucional + cabeçalho em 2 linhas + colunas com
sinônimo (COLABORADOR/ADMISSAO/SEG. DE VIDA) + colunas de benefício/desconto
variáveis (ALMOCO/ATRASOS/DSR) + constante real (CAFE) + fórmulas de subtotal.
Roda offline (só openpyxl + serviços puros), sem banco, Senior, SMTP ou rede.

Cobre: parse/classificação (variavel vs constante real), sinônimos (Causa 2),
coluna variável PRESENTE porém com célula de dado VAZIA (não fabrica), regressão
específica "não replicar o valor da 1ª linha", aviso de `campo` sem dado, aviso da
coluna variável no render, aviso do parser, `_resolver_constante` (nunca fabrica),
e não-regressão FEMSA/Geral.

## Resultado

`python -m pytest` → **24 passed** (12 smokes da spec 006 + 12 testes Skyrail).
