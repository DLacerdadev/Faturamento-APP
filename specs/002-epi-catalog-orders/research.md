# Research — Catálogo de EPIs e Pedido de Compra

Decisões técnicas detalhadas. As escolhas já foram feitas na spec (seção "Technical Decisions", TD-1 a TD-9); este documento expande o racional e as alternativas avaliadas.

---

## R1 — Schema do catálogo (TD-1)

**Decisão**: 2 tabelas normalizadas — `epi_catalog` + `epi_catalog_sizes` com FK CASCADE.

**Racional**:
- Permite query direta "quais EPIs têm tamanho G?" (`JOIN epi_catalog_sizes ON tamanho='G'`).
- Editar/remover um tamanho específico não exige rewrite de JSON.
- Constraint UNIQUE (epi_id, tamanho) impede duplicidade de tamanho no mesmo EPI sem código manual.
- Aderente à convenção SQLAlchemy do projeto (todas as outras tabelas são normalizadas).

**Alternativas consideradas**:
- **JSON column `sizes_json`**: menos arquivos/joins, mas dificulta queries por tamanho e edição atômica de uma linha. Quebra o padrão de outras tabelas. Descartado.
- **Coluna `valor` em `epi_catalog` + tabela só de tamanhos (sem valor)**: assume preço único por EPI, mas spec exige valor por tamanho. Descartado.

---

## R2 — Local da tela do catálogo (TD-2)

**Decisão**: rota dedicada `GET /catalogo-epis` + link "Catálogo de EPIs" na nav global do `base.html`.

**Racional**:
- Cadastro de EPI é tarefa esporádica (raramente diária); separá-lo evita poluir o form principal de compra.
- Navegação clara: usuário do RH precisa de um lugar fixo para gerenciar o catálogo.
- Permite usar o template inteiro do `base.html` sem precisar reformatar o conteúdo de `/epis`.

**Alternativas consideradas**:
- **Tabs em `/epis` (Compras / Catálogo)**: tela ficaria densa, conflito de scroll, código JS maior por gerenciar dois estados na mesma página. Descartado.
- **Modal-only no /epis**: usuário não tem uma "casa" para ver/editar catálogo sem entrar em fluxo de compra. Descartado.

---

## R3 — Tratamento de pedidos legados da feature 001 (TD-3)

**Decisão**: marcar pedido sem `epi_id` como "legado" na UI; botões de solicitação (download/email) desabilitados com tooltip explicativo. **Sem fluxo de migração inline**.

**Racional**:
- Volume baixo de legados (feature 001 acabou de ser implementada).
- Fluxo de migração inline seria UI complexa (lista de itens × dropdown de catálogo) com pouco retorno.
- Usuário pode simplesmente criar uma nova compra usando o catálogo se precisar regerar a solicitação.

**Alternativas consideradas**:
- **Fluxo de migração inline**: maior complexidade, baixa frequência de uso. Adiar para feature 003 se demanda surgir.
- **Ocultar legados completamente**: perde histórico/auditoria. Descartado.

---

## R4 — Destinatário padrão do email (TD-4)

**Decisão**: variável `EPI_PURCHASE_EMAIL` no `.env` como default; campo editável no momento do envio.

**Racional**:
- Mais simples: 1 valor para todo o sistema.
- Mudar destinatário é tarefa de admin (raro); pode-se ajustar `.env` e reiniciar.
- Não exige tabela de preferências por usuário (que demandaria migração + UI extra).

**Alternativas consideradas**:
- **Preferência por usuário**: requer coluna na tabela `users` + tela de preferências. Custo alto para benefício baixo (todos do RH provavelmente mandam pro mesmo endereço).
- **Digitar a cada envio**: friction alta; pessoas vão usar errado.

---

## R5 — Snapshot do solicitante (TD-5)

**Decisão**: capturar `session.user.full_name` (ou `email` se nome vazio) no momento do save; persistir como string no campo `solicitante_nome` do pacote.

**Racional**:
- Snapshot é coerente com P4 da constitution (todo dado externo persistido com snapshot).
- Independe da sobrevivência da linha do usuário no banco (se for desativado depois).
- String simples evita JOIN na listagem.

**Alternativas consideradas**:
- **FK para `users.id`**: vai depender da linha permanecer; se user for excluído, refere-se a nada. Não casa com P4.
- **JSON com snapshot completo**: overkill — só precisamos do nome.

---

## R6 — Módulo Excel separado (TD-6)

**Decisão**: novo arquivo `app/services/epi_solicitation_excel.py` com função `generate_solicitacao_xlsx(pkg) -> bytes`.

**Racional**:
- `app/services/excel_export.py` já tem ~600 linhas focadas na folha de pagamento; misturar lógica de EPI deixaria difícil de manter.
- Separation of concerns: arquivo dedicado, fácil de testar isoladamente.
- Reusa `openpyxl` (já instalado).

**Layout do Excel** (aderente a A9 da spec):
- Linha 1: brasão/empresa (FEMSA), centro de custo (código + nome)
- Linha 2: competência (mês/ano), solicitante, data/hora
- Linha 4: cabeçalho da tabela de itens
- Linhas 5..N: itens (Nome EPI, Tamanho, Qtde por funcionário, Funcionários atendidos, Qtde total, Valor unit., Valor total)
- Linha N+1: TOTAL GERAL com qtde total e valor total
- Linha N+3..M: bloco "Funcionários atendidos" (matrícula + nome)

---

## R7 — Estratégia de migração (TD-7)

**Decisão**: mesma da feature 001 — colunas nullable, SQL de ALTER TABLE documentado em `RUNBOOK.md` (seção "Migração 002"). `init_db()` cuida de novas tabelas em primeira subida limpa.

**SQL** (executar uma vez em dev e prod):

```sql
-- Catálogo
CREATE TABLE IF NOT EXISTS epi_catalog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome VARCHAR(200) NOT NULL,
    ativo BOOLEAN NOT NULL DEFAULT 1,
    created_at DATETIME,
    updated_at DATETIME
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_epi_catalog_nome_upper ON epi_catalog(UPPER(nome)) WHERE ativo = 1;

CREATE TABLE IF NOT EXISTS epi_catalog_sizes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    epi_id INTEGER NOT NULL REFERENCES epi_catalog(id) ON DELETE CASCADE,
    tamanho VARCHAR(20) NOT NULL,
    valor FLOAT NOT NULL,
    UNIQUE (epi_id, tamanho)
);
CREATE INDEX IF NOT EXISTS ix_epi_catalog_sizes_epi_id ON epi_catalog_sizes(epi_id);

-- Extensões do pacote
ALTER TABLE epi_purchase_packages ADD COLUMN solicitante_nome VARCHAR(200);
ALTER TABLE epi_purchase_packages ADD COLUMN quantidade_total_geral INTEGER;
ALTER TABLE epi_purchase_packages ADD COLUMN valor_total_compra_geral FLOAT;
ALTER TABLE epi_purchase_packages ADD COLUMN solicitacao_filename VARCHAR(500);
ALTER TABLE epi_purchase_packages ADD COLUMN solicitacao_generated_at DATETIME;

-- Extensões dos itens
ALTER TABLE epi_purchase_items ADD COLUMN epi_id INTEGER REFERENCES epi_catalog(id);
ALTER TABLE epi_purchase_items ADD COLUMN tamanho VARCHAR(20);
ALTER TABLE epi_purchase_items ADD COLUMN quantidade_por_funcionario INTEGER;
ALTER TABLE epi_purchase_items ADD COLUMN valor_unitario_catalogo FLOAT;

CREATE INDEX IF NOT EXISTS ix_epi_purchase_items_epi_id ON epi_purchase_items(epi_id);
```

PostgreSQL: mesma sequência, ajustando `BOOLEAN NOT NULL DEFAULT TRUE` e índice parcial sintaxe nativa.

**Racional**: zero risco para dados existentes (todas as colunas novas são nullable). `init_db()` continua atuando para subidas limpas.

---

## R8 — Lib SMTP (TD-8)

**Decisão**: stdlib `smtplib` + `email.mime`. Sem dependência nova.

**Racional**:
- Necessidade simples: enviar um email com anexo Excel.
- stdlib cumpre 100% do requisito.
- Evita libs como `yagmail` ou `flask-mail` (este nem se aplica — não é Flask).

**Detecção de SMTP ativo**:
- `app/config.py` lê `SMTP_HOST` do `.env`.
- Helper `is_smtp_configured() -> bool` retorna True se `SMTP_HOST` não-vazio.
- Frontend consome via endpoint `GET /api/epi-purchases/smtp-status` ou inline no template via context.

**Variáveis `.env`**:
```
SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
SMTP_FROM=noreply@grupoopus.com
SMTP_USE_TLS=1
EPI_PURCHASE_EMAIL=compras@grupoopus.com
```

---

## R9 — Unicidade do nome do EPI (TD-9)

**Decisão**: índice único parcial em `UPPER(nome)` apenas para registros ativos (`WHERE ativo = 1`).

**Racional**:
- Permite "ressuscitar" um EPI desativado com mesmo nome de um novo ativo (cenário comum: descontinuou luva A, criou luva A versão 2).
- Case-insensitive evita "Capacete" vs "CAPACETE" vs "capacete" criando 3 entradas.
- Validação adicional no Pydantic schema antes do INSERT/UPDATE (mensagem amigável) — a constraint do banco é só rede de segurança.

**SQLite**: `CREATE UNIQUE INDEX ... ON epi_catalog(UPPER(nome)) WHERE ativo = 1` (suportado desde SQLite 3.8).
**PostgreSQL**: idem, sintaxe idêntica.

---

## Resumo do impacto no código

| Arquivo | Mudança |
|---|---|
| `app/models/epi_purchase.py` | + 2 classes (`EpiCatalog`, `EpiCatalogSize`); + campos nos modelos existentes |
| `app/routers/epi_catalog.py` | **novo** — CRUD do catálogo |
| `app/routers/epi_purchases.py` | POST/PUT aceitam `epi_id`/`tamanho`; calcula e persiste totais; endpoints de solicitação |
| `app/services/epi_solicitation_excel.py` | **novo** — geração do Excel |
| `app/services/email_sender.py` | **novo** — wrapper sobre smtplib |
| `app/config.py` | + vars SMTP + `EPI_PURCHASE_EMAIL` + helper `is_smtp_configured()` |
| `app/main.py` | + rota GET `/catalogo-epis` |
| `app/templates/catalogo_epis.html` | **novo** |
| `app/templates/epis.html` | substituir campos livres por dropdown EPI/tamanho; aviso de divergência; botões de solicitação |
| `app/templates/base.html` | + link "Catálogo de EPIs" na nav |
| `RUNBOOK.md` | + seção "Migração 002" |
| `.env.example` | + bloco SMTP + `EPI_PURCHASE_EMAIL` |
