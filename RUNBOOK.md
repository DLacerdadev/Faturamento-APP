# RUNBOOK — Faturamento App

Sistema de faturamento RH com integração Senior ERP.
**Stack**: FastAPI · SQLAlchemy · Jinja2 · Python 3.11+

---

## Credenciais padrão

| Campo  | Valor            |
|--------|------------------|
| Email  | `ti@grupoopus.com` |
| Senha  | `telos@2026`     |

---

## Desenvolvimento local (sem Docker)

### Pré-requisitos

- Python 3.11 ou superior
- `pip` ou `uv` disponível no PATH

### 1. Clonar e entrar no projeto

```bash
cd /home/rob/projects/FATURAMENTO-APP
```

### 2. Criar e ativar o ambiente virtual

```bash
# Com venv padrão
python3 -m venv .venv
source .venv/bin/activate   # Linux / macOS / WSL
# .venv\Scripts\activate    # Windows PowerShell

# OU com uv (mais rápido)
uv venv
source .venv/bin/activate
```

### 3. Instalar dependências

```bash
# Com pip
pip install -r requirements.txt

# OU com uv
uv pip install -r requirements.txt
```

### 4. Variáveis de ambiente (opcional em dev)

Sem `.env`, o sistema sobe automaticamente em **DEV_MODE**:

- Banco de dados: **SQLite** (`app.db` na raiz)
- Dados de teste: carregados automaticamente do `dump.sql` na primeira inicialização
- Integração Senior: **desativada** — retorna dados locais sem erros

Para customizar, copie o exemplo e edite:

```bash
cp .env.example .env
# edite .env conforme necessário
```

> **DEV_MODE** é ativado automaticamente quando `SENIOR_SOAP_USER` ou
> `SENIOR_SOAP_PASSWORD` estão vazios no `.env`.

### 5. Subir o servidor

```bash
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 5000 --reload
```

| Parâmetro  | Efeito                                      |
|------------|---------------------------------------------|
| `--reload` | Reinicia automaticamente ao salvar arquivos |
| `--port`   | Porta padrão do sistema: **5000**           |

### 6. Acessar

```
http://localhost:5000/login
```

> **WSL2 no Windows**: se `localhost` não abrir, use `http://127.0.0.1:5000/login`
> ou o IP do WSL (`ip addr show eth0 | grep "inet "`).

### 7. Parar o servidor

`Ctrl + C` no terminal onde uvicorn está rodando.

Para matar um processo em background:

```bash
pkill -f "uvicorn app.main"
```

---

## Produção com Docker Compose

### Pré-requisitos

- Docker Desktop ou Docker Engine + Docker Compose v2
- Porta **5000** (app) e **5432** (postgres) livres no host

### 1. Configurar credenciais Senior (obrigatório em prod)

Crie um arquivo `.env` na raiz com as credenciais reais:

```bash
cp .env.example .env
```

Edite o `.env`:

```dotenv
# Banco — gerenciado pelo Docker Compose, não alterar abaixo
DATABASE_URL=postgresql://telos:telos%402026@db:5432/telos_db

# Senior ERP — SOAP (obrigatório para integração real)
SENIOR_SOAP_USER=seu_usuario
SENIOR_SOAP_PASSWORD=sua_senha
SENIOR_SOAP_TOKEN=seu_token
SENIOR_SOAP_ENCRYPTION=0

# Senior ERP — MSSQL (opcional)
MSSQL_HOST=host_do_sqlserver
MSSQL_DB=nome_do_banco
MSSQL_USER=usuario
MSSQL_PASS=senha

# Senior ERP — API REST (opcional)
DOMAIN_API=https://api.seniorcloud.com.br
API_KEY=sua_api_key
```

### 2. Build e subir

```bash
docker compose up --build -d
```

O Compose irá:
1. Subir o PostgreSQL 16 e aguardar o healthcheck
2. Carregar `dump.sql` automaticamente no banco
3. Construir a imagem da aplicação
4. Iniciar o servidor na porta 5000

### 3. Verificar status

```bash
docker compose ps
docker compose logs -f app      # logs da aplicação
docker compose logs -f db       # logs do postgres
```

### 4. Acessar

```
http://localhost:5000/login
```

### 5. Parar

```bash
docker compose down             # para os containers (dados preservados)
docker compose down -v          # para E remove volumes (apaga o banco)
```

### 6. Atualizar após mudanças de código

```bash
docker compose up --build -d
```

---

## Variáveis de ambiente — referência completa

| Variável              | Obrigatória  | Padrão (dev)                          | Descrição                              |
|-----------------------|:------------:|---------------------------------------|----------------------------------------|
| `DATABASE_URL`        | Não          | `sqlite:///./app.db`                  | URL do banco; SQLite se ausente        |
| `SENIOR_SOAP_USER`    | Prod apenas  | *(vazio)*                             | Ativa integração Senior via SOAP       |
| `SENIOR_SOAP_PASSWORD`| Prod apenas  | *(vazio)*                             | Senha SOAP Senior                      |
| `SENIOR_SOAP_TOKEN`   | Não          | *(vazio)*                             | Token adicional SOAP                   |
| `SENIOR_SOAP_ENCRYPTION` | Não       | `0`                                   | Criptografia SOAP (0 = desativada)     |
| `SENIOR_SOAP_URL`     | Não          | URL padrão Senior Cloud               | Endpoint WSDL                          |
| `SENIOR_SOAP_NEXTI_URL` | Não        | URL padrão Senior Cloud               | Endpoint Nexti                         |
| `DOMAIN_API`          | Não          | *(vazio)*                             | Domínio API REST Senior                |
| `API_KEY`             | Não          | *(vazio)*                             | Chave API REST Senior                  |
| `MSSQL_HOST`          | Não          | *(vazio)*                             | Host SQL Server Senior                 |
| `MSSQL_PORT`          | Não          | `1433`                                | Porta SQL Server                       |
| `MSSQL_DB`            | Não          | *(vazio)*                             | Nome do banco MSSQL                    |
| `MSSQL_USER`          | Não          | *(vazio)*                             | Usuário MSSQL                          |
| `MSSQL_PASS`          | Não          | *(vazio)*                             | Senha MSSQL                            |

---

## Dados de teste (DEV_MODE)

Quando em DEV_MODE (sem credenciais Senior), na **primeira inicialização**:

- O `dump.sql` é carregado automaticamente no SQLite
- Período disponível: **2025-10** (outubro de 2025)
- **360 funcionários** com eventos de folha completos
- Centros de custo disponíveis: `620083`, `640053`, `640059`

Para recarregar os dados do zero (apagar e recriar o banco):

```bash
rm app.db
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 5000
```

---

## Migrações

### 001 — EPI por funcionário

Spec: [specs/001-epi-purchase-flow/](specs/001-epi-purchase-flow/)

Adiciona vínculo funcionário×item no fluxo de compra de EPIs. Colunas novas:
- `epi_purchase_packages.codccu` — centro de custo da compra
- `epi_purchase_items.employee_numcad` — matrícula Senior (snapshot)
- `epi_purchase_items.employee_nome` — nome Senior (snapshot)

Aplicar em dev (SQLite) e prod (PostgreSQL):

```sql
ALTER TABLE epi_purchase_packages ADD COLUMN codccu VARCHAR(20);
ALTER TABLE epi_purchase_items   ADD COLUMN employee_numcad INTEGER;
ALTER TABLE epi_purchase_items   ADD COLUMN employee_nome   VARCHAR(200);
CREATE INDEX IF NOT EXISTS ix_epi_purchase_packages_codccu ON epi_purchase_packages(codccu);
CREATE INDEX IF NOT EXISTS ix_epi_purchase_items_employee  ON epi_purchase_items(employee_numcad);
```

Linhas legadas (anteriores à migração) ficam com NULL nas novas colunas — a UI exibe rótulo "(legado)".

Em primeira subida limpa (sem `app.db` / banco vazio), `init_db()` cria as colunas automaticamente — nenhum ALTER necessário.

Conferência: `python -c "import sqlite3; print([r[1] for r in sqlite3.connect('app.db').execute('PRAGMA table_info(epi_purchase_items)')])"` deve incluir `employee_numcad` e `employee_nome`.

### 002 — Catálogo de EPIs e Solicitação

Spec: [specs/002-epi-catalog-orders/](specs/002-epi-catalog-orders/)

Introduz catálogo de EPIs e gera Excel de solicitação automaticamente ao salvar. Novas tabelas: `epi_catalog`, `epi_catalog_sizes`. Novas colunas em `epi_purchase_packages` (solicitante_nome, totais agregados, solicitacao_filename, solicitacao_generated_at) e `epi_purchase_items` (epi_id, tamanho, quantidade_por_funcionario, valor_unitario_catalogo).

```sql
CREATE TABLE IF NOT EXISTS epi_catalog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome VARCHAR(200) NOT NULL,
    ativo BOOLEAN NOT NULL DEFAULT 1,
    created_at DATETIME,
    updated_at DATETIME
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_epi_catalog_nome_upper
    ON epi_catalog(UPPER(nome)) WHERE ativo = 1;

CREATE TABLE IF NOT EXISTS epi_catalog_sizes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    epi_id INTEGER NOT NULL REFERENCES epi_catalog(id) ON DELETE CASCADE,
    tamanho VARCHAR(20) NOT NULL,
    valor FLOAT NOT NULL,
    UNIQUE (epi_id, tamanho)
);
CREATE INDEX IF NOT EXISTS ix_epi_catalog_sizes_epi_id ON epi_catalog_sizes(epi_id);

ALTER TABLE epi_purchase_packages ADD COLUMN solicitante_nome VARCHAR(200);
ALTER TABLE epi_purchase_packages ADD COLUMN quantidade_total_geral INTEGER;
ALTER TABLE epi_purchase_packages ADD COLUMN valor_total_compra_geral FLOAT;
ALTER TABLE epi_purchase_packages ADD COLUMN solicitacao_filename VARCHAR(500);
ALTER TABLE epi_purchase_packages ADD COLUMN solicitacao_generated_at DATETIME;

ALTER TABLE epi_purchase_items ADD COLUMN epi_id INTEGER REFERENCES epi_catalog(id);
ALTER TABLE epi_purchase_items ADD COLUMN tamanho VARCHAR(20);
ALTER TABLE epi_purchase_items ADD COLUMN quantidade_por_funcionario INTEGER;
ALTER TABLE epi_purchase_items ADD COLUMN valor_unitario_catalogo FLOAT;

CREATE INDEX IF NOT EXISTS ix_epi_purchase_items_epi_id ON epi_purchase_items(epi_id);
```

Pacotes legados (sem `epi_id`) continuam carregando — UI marca como "Legado" e bloqueia geração de solicitação. Variáveis `.env` opcionais (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_USE_TLS`, `EPI_PURCHASE_EMAIL`) habilitam envio por email; sem elas o sistema mantém apenas o download do Excel.

### 004 — Pedido de compra misto (categoria por item)

Pedido único pode misturar EPI, uniforme e equipamento: a categoria passa a valer
POR ITEM (`epi_purchase_items.categoria`; NULL = vale a categoria do pacote, que
vira derivada — única categoria dos itens ou `'misto'`). Pedidos novos NÃO
persistem mais `employee_nome`/`employee_cargo` (colunas mantidas pelo legado; o
faturamento casa por `employee_numcad`). O Excel da solicitação passou a ser por
ITEM (Item, Categoria, Tamanho, C.A, Qtde, Valor unit., Valor total + total do
pedido), sem nomes de funcionários, e é gerado para QUALQUER categoria (antes: só EPI).

```sql
ALTER TABLE epi_purchase_items ADD COLUMN categoria VARCHAR(20);
```

Migração idempotente aplicada automaticamente no startup (`app/db.py`).

### 005 — Trilha de auditoria (audit_logs)

Tabela nova `audit_logs` (criada pelo `create_all` no startup — sem ALTER manual):
registros IMUTÁVEIS de quem fez o quê (ts, user_id/username/role snapshot, acao
`entidade.verbo`, entidade+id, detalhe JSON com antes/depois, ip, status
ok|negado|erro). Helper `app/services/audit.py` (sessão própria, nunca levanta
exceção, nunca grava senhas). ~57 pontos instrumentados: login/logout/falhas,
usuários e papéis, regras administrativas (%), modelos de exportação (inclusive
upload), pedidos de compra (inclusive exclusão física, com snapshot), preços de
catálogo, exportações de folha/faturamento (modelo, período, CCU — inclusive
jobs, com o DOWNLOAD do arquivo auditado à parte via `exportacao.download`;
o ExportJob guarda user_id/username de quem enfileirou) e importações de dados
(inclusive previews: `modelo.upload_preview`, `importacao.preview`). Consulta
em `/auditoria` (somente admin), com filtros e paginação — sem endpoints de
escrita/exclusão.

---

## Cache Senior e throttle (feature 003)

Spec: [specs/003-senior-cache-throttle/](specs/003-senior-cache-throttle/)

Cache em memória de processo para reduzir chamadas SOAP redundantes:
- `ccu_cache` — lista de centros de custo (T018CCU). TTL default 6h.
- `employees_cache` — funcionários ativos por CCU+mês corrente. TTL default 1h.
- `_SOAP_SEMAPHORE` — máximo de chamadas SOAP simultâneas (default 3); excesso enfileira.

### Variáveis `.env` (opcionais, todas com default razoável)

```dotenv
SENIOR_CACHE_CCU_TTL=21600         # 6h
SENIOR_CACHE_EMPLOYEES_TTL=3600    # 1h
SENIOR_SOAP_MAX_CONCURRENCY=3
```

### Endpoints admin (autenticados via sessão)

**Limpar entradas do cache** (não busca dados novos):

```bash
curl -X POST "http://127.0.0.1:8000/integrations/senior/cache/invalidate?token=$TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"scope":"ccu"}'
# Resposta: {"status":"ok","scope":"ccu","removed":{"ccu":1,"employees":0}}
```

Scopes aceitos: `"ccu"`, `"employees"`, `"all"`. `key` opcional (limpa só essa entrada).

**Forçar revalidação** (limpa + busca + popula):

```bash
curl -X POST "http://127.0.0.1:8000/integrations/senior/cache/refresh?token=$TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"scope":"employees","key":"620039"}'
```

Para `scope=employees`, `key` é obrigatório (codccu). Para `scope=ccu`, key é opcional (default = TELOS_NUMEMP).

**Inspecionar estado**:

```bash
curl "http://127.0.0.1:8000/integrations/senior/cache/stats?token=$TOKEN"
# Mostra entradas atuais, age e ttl_left de cada chave.
```

### Retry desativado

Por decisão arquitetural (evitar amplificar carga durante instabilidade da Senior/F5), retry automático foi removido. Em falha de SOAP (503, timeout, ConnectionError), a chamada falha imediatamente e propaga erro descritivo ao usuário (com support ID do F5 quando disponível). O usuário decide se tenta de novo.

---

## Checklist rápido

### Dev

```bash
source .venv/bin/activate
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 5000 --reload
# → http://localhost:5000/login
```

### Prod (Docker)

```bash
cp .env.example .env   # edite com credenciais reais
docker compose up --build -d
# → http://localhost:5000/login
```
