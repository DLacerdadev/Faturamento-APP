# Data Model — Catálogo de EPIs e Pedido de Compra

## Tabelas novas

### `epi_catalog` — catálogo mestre de EPIs

| Coluna | Tipo | Null | Default | Index | Observação |
|---|---|---|---|---|---|
| `id` | INTEGER | NO | autoincr | PK | |
| `nome` | VARCHAR(200) | NO | — | partial UNIQUE em UPPER(nome) WHERE ativo=1 | nome do EPI; case-insensitive entre ativos |
| `ativo` | BOOLEAN | NO | TRUE | idx | soft-delete via `ativo=False` |
| `created_at` | DATETIME | YES | now | — | |
| `updated_at` | DATETIME | YES | now | — | auto-update |

### `epi_catalog_sizes` — tamanhos e valores de cada EPI

| Coluna | Tipo | Null | Default | Index | Observação |
|---|---|---|---|---|---|
| `id` | INTEGER | NO | autoincr | PK | |
| `epi_id` | INTEGER | NO | — | FK `epi_catalog.id` ON DELETE CASCADE; idx | |
| `tamanho` | VARCHAR(20) | NO | — | parte de UNIQUE(epi_id, tamanho) | ex: "P", "M", "G", "GG", "Único", "42", "44" |
| `valor` | FLOAT | NO | — | — | valor unitário > 0 |

**Constraints**:
- `UNIQUE (epi_id, tamanho)` — não permite mesmo tamanho duplicado para o mesmo EPI.
- `CHECK valor > 0` (aplicado em Pydantic; SQLite não força).

## Tabelas existentes — extensões

### `epi_purchase_packages` — novos campos

| Coluna | Tipo | Null | Default | Index | Observação |
|---|---|---|---|---|---|
| **`solicitante_nome`** | **VARCHAR(200)** | **YES** | **NULL** | — | **NOVO**; snapshot do nome (ou email) do usuário no momento do save |
| **`quantidade_total_geral`** | **INTEGER** | **YES** | **NULL** | — | **NOVO**; soma de qtde_total de todos os itens. NULL em pacotes legados |
| **`valor_total_compra_geral`** | **FLOAT** | **YES** | **NULL** | — | **NOVO**; soma de valor_total de todos os itens. NULL em pacotes legados |
| **`solicitacao_filename`** | **VARCHAR(500)** | **YES** | **NULL** | — | **NOVO**; nome do último Excel gerado (relativo a `GENERATED_REPORTS_DIR`) |
| **`solicitacao_generated_at`** | **DATETIME** | **YES** | **NULL** | — | **NOVO**; timestamp da última geração |

Campos preservados da feature 001: `id`, `empresa`, `mes_ano`, `observacao`, `codccu`, `created_at`, `updated_at`.

### `epi_purchase_items` — novos campos

| Coluna | Tipo | Null | Default | Index | Observação |
|---|---|---|---|---|---|
| **`epi_id`** | **INTEGER** | **YES** | **NULL** | **FK `epi_catalog.id`; idx** | **NOVO**; NULL em linhas legadas. **Não-NULL** em itens do novo fluxo |
| **`tamanho`** | **VARCHAR(20)** | **YES** | **NULL** | — | **NOVO**; deve bater com um size cadastrado em `epi_catalog_sizes` no momento do save |
| **`quantidade_por_funcionario`** | **INTEGER** | **YES** | **NULL** | — | **NOVO**; usado para distinguir do `quantidade` legacy (que pode ter valores migrados) |
| **`valor_unitario_catalogo`** | **FLOAT** | **YES** | **NULL** | — | **NOVO**; snapshot do valor do catálogo no momento do save (referência para detectar override no Excel) |

Campos preservados: `id`, `package_id`, `descricao`, `quantidade`, `valor_unitario`, `valor_total`, `employee_numcad`, `employee_nome`.

**Semântica das colunas em pedidos novos (feature 002)**:
- `descricao` = `EpiCatalog.nome` (snapshot, para sobrevivência se EPI for renomeado)
- `quantidade` = `quantidade_por_funcionario` (mantemos a coluna legacy preenchida pela mesma fonte)
- `valor_unitario` = valor efetivamente usado (pode ser igual ao `valor_unitario_catalogo` ou override)
- `valor_total` = `quantidade × valor_unitario` (cada linha continua sendo 1 funcionário × 1 item)

**Detecção de override de valor**: `valor_unitario != valor_unitario_catalogo` (com tolerância de arredondamento).

## Relacionamentos

```
epi_catalog (1) ──────< (N) epi_catalog_sizes              -- CASCADE
epi_catalog (1) ──────< (N) epi_purchase_items.epi_id      -- nullable, sem CASCADE
epi_purchase_packages (1) ──< (N) epi_purchase_items       -- CASCADE (preservado da 001)
epi_purchase_packages (1) ──< (N) epi_purchase_documents   -- CASCADE (preservado da 001)
```

Nota: `epi_purchase_items.epi_id` não tem CASCADE — não queremos perder linhas históricas se o catálogo for excluído. Como excluir está bloqueado quando há vínculo (apenas desativação é permitida via FR-4), na prática não acontece DELETE direto.

## Regras de derivação (calculadas server-side ao salvar/atualizar)

Dado um pacote com itens `I = {i_1, ..., i_k}` e linhas de cartesiano `R` (uma linha por funcionário × item):

- `quantidade_total_geral(pkg) = sum(linha.quantidade for linha in R)`
- `valor_total_compra_geral(pkg) = sum(linha.valor_total for linha in R)`

Por item (agrupamento na visualização):
- `item_qtde_total = funcionarios_distintos × quantidade_por_funcionario`
- `item_valor_total = item_qtde_total × valor_unitario`

## Invariantes

1. **Pedido novo (feature 002)**: para todo `EpiPurchaseItem` criado a partir desta feature, `epi_id IS NOT NULL` e `tamanho IS NOT NULL`.
2. **Pedido legado (feature 001)**: `epi_id IS NULL` E `tamanho IS NULL`. UI deve marcar visualmente.
3. **Totais persistidos**: para pacotes não-legados, `quantidade_total_geral` e `valor_total_compra_geral` são NOT NULL (do ponto de vista de aplicação; o banco aceita NULL para compat com legacy).
4. **Catálogo consistente**: para todo `epi_purchase_items` com `epi_id` não-NULL, a tupla `(epi_id, tamanho)` deve existir em `epi_catalog_sizes` **no momento do save** (após o save o catálogo pode mudar; o item mantém snapshot via `valor_unitario_catalogo`).
5. **Unicidade**: um `EpiCatalog` ativo não pode ter mesmo `UPPER(nome)` de outro ativo.

## Estratégia de listagem para a UI

`GET /api/epi-purchases` (extensão):
- Para cada pacote, devolve `is_legacy = (epi_id IS NULL para todos os items)`.
- Inclui os totais já persistidos (`quantidade_total_geral`, `valor_total_compra_geral`) — sem recalcular.

`GET /api/epi-purchases/{id}` (extensão):
- Inclui `agrupado` (já existe na 001): agrupa itens distintos para reabrir o form de edição.
- Inclui `agrupado_v2`: agrupa por `(epi_id, tamanho, quantidade_por_funcionario, valor_unitario)` para o novo form de catálogo.
- Inclui `solicitacao` block: `{ filename, generated_at, available_for_download: bool }`.

## Migração 002

Documentada em [research.md §R7](research.md) e no `RUNBOOK.md`.

**Compatibilidade**: 100% das linhas existentes em `epi_purchase_packages` e `epi_purchase_items` continuam carregando. UI distingue legado de novo via `epi_id IS NULL`.
