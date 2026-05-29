# Data Model — Fluxo de Compra de EPIs por Funcionário

## Tabelas

### `epi_purchase_packages` — pacote de compra (1 por compra)

| Coluna | Tipo | Null | Default | Index | Observação |
|---|---|---|---|---|---|
| `id` | INTEGER | NO | autoincr | PK | já existe |
| `empresa` | VARCHAR(100) | NO | "FEMSA" | — | já existe |
| `mes_ano` | DATE | NO | — | — | já existe; dia 1 do mês |
| `observacao` | TEXT | YES | NULL | — | já existe |
| **`codccu`** | **VARCHAR(20)** | **YES** | **NULL** | **idx** | **NOVO**; código do centro de custo Senior; NULL em linhas legadas |
| `created_at` | DATETIME | YES | now | — | já existe |
| `updated_at` | DATETIME | YES | now | — | já existe; auto-update |

### `epi_purchase_items` — linhas individuais (1 por par funcionário × item)

| Coluna | Tipo | Null | Default | Index | Observação |
|---|---|---|---|---|---|
| `id` | INTEGER | NO | autoincr | PK | já existe |
| `package_id` | INTEGER | NO | — | FK `epi_purchase_packages.id` ON DELETE CASCADE | já existe |
| `descricao` | VARCHAR(255) | NO | — | — | já existe |
| `quantidade` | INTEGER | NO | 1 | — | já existe; replicada por funcionário |
| `valor_unitario` | FLOAT | NO | 0.0 | — | já existe; replicado por funcionário |
| `valor_total` | FLOAT | NO | 0.0 | — | já existe; = quantidade × valor_unitario |
| **`employee_numcad`** | **INTEGER** | **YES** | **NULL** | **idx** | **NOVO**; matrícula Senior; NULL em linhas legadas |
| **`employee_nome`** | **VARCHAR(200)** | **YES** | **NULL** | — | **NOVO**; snapshot do nome no momento do save |

### `epi_purchase_documents` — sem mudança

Modelo atual preservado integralmente.

## Relacionamentos

- `EpiPurchasePackage.items` ⇔ `EpiPurchaseItem.package` (1:N, CASCADE) — já existe
- `EpiPurchasePackage.documents` ⇔ `EpiPurchaseDocument.package` (1:N, CASCADE) — já existe
- Sem FK física para funcionário — vínculo é via snapshot `employee_numcad` + `employee_nome` (cumpre P4)

## Invariantes

1. Para qualquer pacote criado pelo novo fluxo: `codccu IS NOT NULL`. Pacotes com `codccu IS NULL` são legados.
2. Para qualquer item criado pelo novo fluxo: `employee_numcad IS NOT NULL` e `employee_nome IS NOT NULL`. Itens com `employee_numcad IS NULL` são legados.
3. `valor_total ≈ quantidade × valor_unitario` (tolerância: arredondamento).
4. Dentro de um pacote do novo fluxo, o par `(employee_numcad, descricao)` pode se repetir somente se vier de itens distintos do form (ex: usuário lança "Luva, qtde=1" duas vezes — aceito).

## Migração

### Em DEV (SQLite `app.db`)

`init_db()` chama `Base.metadata.create_all` que **não altera tabelas existentes**. Para o `app.db` já populado, rodar UMA VEZ:

```sql
ALTER TABLE epi_purchase_packages ADD COLUMN codccu VARCHAR(20);
ALTER TABLE epi_purchase_items   ADD COLUMN employee_numcad INTEGER;
ALTER TABLE epi_purchase_items   ADD COLUMN employee_nome VARCHAR(200);

CREATE INDEX IF NOT EXISTS ix_epi_purchase_packages_codccu     ON epi_purchase_packages(codccu);
CREATE INDEX IF NOT EXISTS ix_epi_purchase_items_employee      ON epi_purchase_items(employee_numcad);
```

### Em PROD (PostgreSQL via docker-compose)

Mesma sequência. Compatível com Postgres 16. Documentar no `RUNBOOK.md` na seção "Migração 001".

### Em primeira subida limpa (sem `app.db`)

`init_db()` cria tudo com as colunas novas — nenhum ALTER necessário.

## Estratégia de leitura para a UI

Listagem de compras (`GET /api/epi-purchases`):

- Para cada pacote, agregar:
  - `funcionarios_distintos` = `count(DISTINCT employee_numcad)` (exclui NULL)
  - `itens_distintos` = `count(DISTINCT (descricao, quantidade, valor_unitario))`
  - `total_linhas` = `count(*)` (linhas em `epi_purchase_items`)
  - `valor_total_compra` = `sum(valor_total)`

Detalhe de um pacote (`GET /api/epi-purchases/{id}`):

- Devolver duas representações:
  - `linhas_flat`: todas as linhas (formato persistido) — útil para audits e relatórios
  - `agrupado`: `{ funcionarios: [{numcad,nome}], itens: [{descricao,qtde,valor_unitario}] }` — útil pra reabrir o form de edição

A reconstrução do "agrupado" para reabrir o form é uma operação puramente em memória: distinct de `employee_numcad`+`employee_nome` e distinct de `(descricao,quantidade,valor_unitario)` dentro do pacote.
