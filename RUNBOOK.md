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
