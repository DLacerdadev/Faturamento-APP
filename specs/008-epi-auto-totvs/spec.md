# Feature Specification: EPI Automático via TOTVS (sync diário, trava manual, retorno ao automático)

**Feature ID**: 008-epi-auto-totvs
**Created**: 2026-07-25
**Status**: Draft
**Spec File**: spec.md
**Itens do plano**: 30 (rotina cron diária), 31 (preservar valor manual), 32 (voltar ao automático)
**Onda de execução**: 1 (frota paralela — junto com 009 e 010)
**Agente**: 1 agente dedicado

## Overview

Os valores de EPI/produtos vêm do TOTVS e hoje entram por **upload manual de Excel**
(`import_produtos_totvs()`), com preço editável na tela `/catalogo-produtos`. Não existe
scheduler no projeto (só jobs em memória) nem flag de "preço manual vs automático". Esta spec
entrega três coisas encadeadas: (30) uma **rotina diária** (APScheduler) que busca preços de EPI
**direto do TOTVS Protheus via REST** e atualiza o catálogo; (31) uma **trava**: preços editados
manualmente ficam fixos e a rotina **não** os sobrescreve; (32) uma **opção de reverter** um EPI ao
modo automático, removendo a trava e voltando a receber as atualizações.

**Fonte de dados (confirmada em produção)**: o Protheus expõe o web service REST **`WSGETDATA`** —
um gateway de SQL que recebe um `SELECT` e devolve JSON. Consulta ao TOTVS provou que o valor de EPI
**não está no cadastro** (`SB1010` zerado) e sim no **preço unitário do pedido de compra**
(`SC7010.C7_PRECO`), já **por centro de custo** (`C7_CC`). A rotina pega o último preço por
`(produto, CCU)`. Detalhes de endpoint, autenticação (via env), glossário e a consulta oficial em
[`contracts/totvs-wsgetdata.md`](contracts/totvs-wsgetdata.md). **Credenciais só por variável de
ambiente** (`.env` fora do git) — nunca em código/arquivo versionado.

## User Scenarios & Testing

> **Modelo de dados (decisão do dono):** o valor de EPI é **por centro de custo**. No TOTVS ele já
> nasce assim (`C7_PRECO` por `C7_CC`). Persistimos em **`CCItemPrice`** (`codccu`, `produto_codigo`,
> `valor`) — a alteração num CCU **não** afeta outro. O `is_manual_price`/`last_auto_sync` ficam
> **em `CCItemPrice`**, não no catálogo global.

### Primary Flow

1. Diariamente, a rotina consulta o TOTVS (último `C7_PRECO` por produto × centro de custo) e faz
   upsert em `CCItemPrice` **exceto** nas linhas travadas como manuais.
2. Na tela, cada valor de EPI mostra um selo **"definido pela TOTVS"** com a data da última sync.
3. Gestor edita o valor de um EPI **de um centro de custo** → aquela linha `(CCU, produto)` é
   marcada `manual` (trava) e destacada; **os outros CCUs continuam automáticos**.
4. Nas rodadas seguintes, aquela linha mantém o valor manual; as demais seguem o TOTVS.
5. Gestor clica em "Voltar ao automático" **naquele CCU** → a trava sai e o valor volta a ser o do
   TOTVS (imediatamente e nas próximas rodadas).

### Acceptance Scenarios

- **Scenario 1** (30): Given a rotina agendada, When ela roda, Then cada `(CCU, produto)` EPI não
  travado recebe o último `C7_PRECO` daquele CCU e `last_auto_sync` é atualizado.
- **Scenario 2** (31): Given uma linha `CCItemPrice` com `is_manual_price=True`, When a rotina roda,
  Then o `valor` daquela linha NÃO é alterado.
- **Scenario 3** (31/isolamento): Given o produto X manual no CCU A e automático no CCU B, When a
  rotina roda, Then B é atualizado pelo TOTVS e A permanece com o valor manual — **um não afeta o outro**.
- **Scenario 4** (31): Given um gestor editando o valor de um EPI de um CCU, When salva, Then só
  aquela linha vira `is_manual_price=True` e a UI a marca "manual".
- **Scenario 5** (32): Given uma linha manual, When o gestor escolhe "voltar ao automático", Then
  `is_manual_price` vira `False` e o valor é ressincronizado do TOTVS para aquele CCU.

### Edge Cases

- TOTVS indisponível: rotina registra erro e **não zera** os valores existentes (falha segura).
- Produto EPI sem pedido de compra (sem `C7_PRECO`): fica sem valor automático até haver compra;
  não sobrescreve eventual valor manual.
- Produto/CCU novo no TOTVS: cria a linha `CCItemPrice` como automática.
- Vários pedidos no mesmo dia para o mesmo `(produto, CCU)`: usa o mais recente (emissão + R_E_C_N_O_).
- Rodada manual sob demanda (botão "sincronizar agora") além do agendamento diário.

## Functional Requirements

- **FR-1** (31/32): Adicionar a **`CCItemPrice`** as colunas `is_manual_price` (bool, default False)
  e `last_auto_sync` (datetime, nullable) — migração compatível (P5). É aqui que a trava vive,
  garantindo isolamento por centro de custo.
- **FR-2** (30): Serviço `price_autosync.py` que **consulta o TOTVS via `totvs_connector.py`** (query
  oficial em `contracts/totvs-wsgetdata-epi-precos.sql`), e faz **upsert em `CCItemPrice`** por
  `(codccu, produto_codigo)` com o último `C7_PRECO` **respeitando** a trava manual; grava
  `last_auto_sync`.
- **FR-2b**: Conector `totvs_connector.py` (novo): monta o header Basic das env
  (`TOTVS_WSGETDATA_URL/USER/PASSWORD`), envia o `SELECT` (**fixo no código**, nunca de input do
  usuário), trata HTTP 201=ok / 500=erro, decodifica cp1252, devolve `registros`. Degrada com erro
  claro se env vazio (DEV_MODE).
- **FR-3** (30): Scheduler (APScheduler) no startup, sync 1x/dia (horário `TOTVS_SYNC_HORARIO`,
  default 02:00). Um único worker.
- **FR-4** (31): Ao editar o valor de um EPI **de um CCU** na tela, marcar `is_manual_price=True`
  **só naquela linha** `CCItemPrice`. Nunca propaga para outros CCUs.
- **FR-5** (32): Endpoint + botão "voltar ao automático" **por CCU** que zera a trava e
  ressincroniza aquela linha do TOTVS.
- **FR-6**: Endpoint "sincronizar agora" (gestor+) para disparo manual, auditado.
- **FR-7**: UI: selo **"definido pela TOTVS"** + data da última sync; estado manual/automático e as
  ações por linha (CCU × produto). Onde o valor por CCU é editado hoje (validação de compra) passa a
  exibir e respeitar a trava.
- **FR-8**: Toda alteração auditada (`audit()`), com `codccu` no registro.

## Success Criteria

- **SC-1**: Após uma rodada, EPIs automáticos batem com a fonte TOTVS; manuais permanecem.
- **SC-2**: Reverter ao automático restaura o valor da fonte.
- **SC-3**: A rotina roda sem intervenção e registra sucesso/erro (observável).

## Key Entities

- **CCItemPrice** (`codccu`, `produto_codigo`, `tamanho`, `valor`; **+ `is_manual_price`,
  `last_auto_sync`**) — casa do valor de EPI **por centro de custo** e da trava manual. É o destino
  do upsert da rotina e a fonte que o faturamento já consome (preço do CC → senão global).
- **ProductCatalog** — cadastro do produto (descrição/grupo). Pode guardar um valor de referência,
  mas o valor efetivo por CCU é o `CCItemPrice`.
- **Fonte TOTVS**: `SC7010.C7_PRECO` por `C7_CC` (WSGETDATA) — confirmado.

## Assumptions

- Fonte TOTVS = WSGETDATA (REST → SQL). Valor de EPI = **último `C7_PRECO` por `(produto, CCU)`**
  (grupo `0003`). Confirmado em produção — ver `contracts/totvs-wsgetdata.md` e `...-epi-precos.sql`.
- **A confirmar com o time TOTVS (não bloqueia dev):** outros grupos de EPI além de `0003`; regra de
  desempate quando há múltiplos fornecedores no mesmo dia.
- Um worker (uvicorn sem `--workers`) — coerente com `export_jobs`.
- O `import_produtos_totvs()` por Excel continua como fallback/carga manual do cadastro.

## Out of Scope

- Cadastro/edição de pedidos de compra no TOTVS (só leitura de preço).
- Reescrever a resolução de preço do faturamento (já usa `CCItemPrice` → global).

## Dependencies

- Spec 006 (harness) para os testes.
- Nova dependência: `APScheduler` e `httpx` (integração via nota — orquestradora adiciona ao requirements).
- **TOTVS WSGETDATA** — endpoint + credenciais via env (`.env` local). Ver
  [`contracts/totvs-wsgetdata.md`](contracts/totvs-wsgetdata.md). Os testes **mockam** o conector
  (rodam offline, sem tocar o Protheus).

## File Ownership / Boundaries

**Edita/Cria (exclusivo desta spec)**:
- `app/models/cc_item_price.py` (flags `is_manual_price`/`last_auto_sync` — **onde a trava vive**)
- `app/models/product_catalog.py` (valor de referência opcional)
- `app/services/product_import.py`
- `app/services/price_autosync.py` (novo), `app/services/scheduler.py` (novo),
  `app/services/totvs_connector.py` (novo)
- `app/routers/product_catalog.py`, `app/routers/epi_catalog.py`
- `app/templates/catalogo_produtos.html`
- `tests/test_price_autosync.py`, `tests/test_totvs_connector.py` (novos, com conector mockado)

**Notas de integração (orquestradora aplica no merge — agente NÃO edita)**:
- `app/main.py` (iniciar scheduler no startup), `requirements.txt` (APScheduler + httpx),
  `app/config.py` (`TOTVS_WSGETDATA_URL/USER/PASSWORD` + `is_totvs_configured()` + horário do cron),
  `.env.example` (placeholders TOTVS), `RUNBOOK.md` (migração + cron + rotação de credencial),
  `app/db.py` (colunas novas nullable). O agente entrega essas mudanças como **patch/nota** no
  relatório da branch. **Credencial real só no `.env` local (gitignored), nunca versionada.**

**Não toca**: qualquer arquivo das specs 009/010 (grid, IA) nem de 007 (Skyrail).

## Test Plan (definição de "100% concluída")

- Testes: sync atualiza automáticos; respeita `is_manual_price`; reverter restaura; falha de fonte
  não destrói preços. Scheduler testado com trigger disparado manualmente (sem esperar 24h).
- `pytest` verde.
