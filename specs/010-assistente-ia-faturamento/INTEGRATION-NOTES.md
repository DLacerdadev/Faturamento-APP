# INTEGRATION-NOTES — Spec 010 (Assistente de IA do Faturamento)

Estas mudanças tocam arquivos de INTEGRAÇÃO que a spec 010 **não edita** (fronteira).
A orquestradora aplica no merge. Tudo aqui é aditivo e compatível com dado existente.

## 1. Registrar o router em `app/main.py`

Adicionar o import junto aos demais routers e incluir no app:

```python
from app.routers.ai_chat import router as ai_chat_router
...
app.include_router(ai_chat_router)
```

Rotas expostas (todas gestor+, auditadas):
- `POST /ia/chat` — {model_id, message, snapshot?, session_id?} → {reply, ops[], rejected[], session_id}
- `POST /ia/chat/async` + `GET /ia/chat/status/{job_id}` + `GET /ia/chat/result/{job_id}` — modo job (chamada longa)
- `GET /ia/chat` — página standalone de dev/test (`ai_chat.html`)

Opcional: link "Assistente IA" no menu da sidebar (base.html) apontando para `/ia/chat`.

## 2. Config: `LLM_*` em `app/config.py` + `.env.example`

O `ai_service.py` lê a config via `app.config` com fallback `os.getenv` (mesmo
padrão do `totvs_connector.py`), então funciona **antes e depois** desta
integração. Ideal adicionar em `app/config.py`:

```python
# LLM local self-hosted (endpoint compatível OpenAI, ex.: Ollama/vLLM). Spec 010.
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")   # ex.: http://127.0.0.1:11434/v1
LLM_MODEL    = os.getenv("LLM_MODEL", "")      # ex.: llama3.1:8b-instruct
LLM_API_KEY  = os.getenv("LLM_API_KEY", "")    # opcional (Ollama ignora)
LLM_TIMEOUT  = os.getenv("LLM_TIMEOUT", "60")  # segundos

def is_llm_configured() -> bool:
    return bool(LLM_BASE_URL and LLM_MODEL)
```

E em `.env.example` (comentado, sem segredo real):

```
# Assistente de IA (LLM 100% self-hosted / local — nada sai da infra). Spec 010.
LLM_BASE_URL=
LLM_MODEL=
LLM_API_KEY=
LLM_TIMEOUT=60
```

`requirements.txt`: **nada a adicionar** — o cliente usa `httpx`, que já está no
projeto (é o mesmo do `totvs_connector`).

## 3. Tabelas de chat em `app/db.py`

Registrar o import do model dentro de `init_db()` (junto aos outros models) para
`Base.metadata.create_all` criar as tabelas novas:

```python
from app.models import ..., ai_chat_session
```

São **tabelas novas** (`ai_chat_sessions`, `ai_chat_messages`) — não exigem
`ALTER` nem migração de coluna. `create_all` é idempotente.

> Defesa em profundidade: `app/routers/ai_chat.py` já chama um `_ensure_tables()`
> (`__table__.create(checkfirst=True)`) no import, então o endpoint funciona
> mesmo antes de db.py ser atualizado. Ainda assim, registre no db.py para ficar
> junto do schema canônico.

## 4. `tests/conftest.py` — ISOLAR o LLM no ambiente de teste (IMPORTANTE)

O conftest já zera `SENIOR_*` e `TOTVS_*` no topo (antes de importar `app`). Fazer
o **mesmo com as `LLM_*`** para garantir que nenhum teste alcance um LLM real,
mesmo que o `.env` do dev tenha `LLM_BASE_URL`/`LLM_MODEL` preenchidos:

```python
# Zera as credenciais/endpoint do LLM local (spec 010): nenhum teste pode
# alcançar um modelo real. Os testes mockam o cliente (chat_fn/_call_llm).
for _k in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY"):
    os.environ[_k] = ""
```

Isso não é estritamente necessário para os testes ATUAIS (todos passam `chat_fn`
mockado ou monkeypatcham `get_base_url`/`get_model`/`httpx.post`, nunca abrindo
socket), mas é **defesa em profundidade** e alinhado ao que o conftest já faz com
Senior/TOTVS. Recomendado aplicar.

## 5. RUNBOOK.md — deploy do LLM local

Adicionar seção de infra do LLM self-hosted (dependência externa, out of scope do
código):

- Subir um servidor LLM local com endpoint **compatível com a API OpenAI**
  (ex.: Ollama em `http://127.0.0.1:11434/v1`, ou vLLM). Nada sai da infra.
- Baixar um modelo instruct (ex.: `ollama pull llama3.1:8b-instruct`).
- Definir `LLM_BASE_URL`, `LLM_MODEL` no `.env` de prod.
- O modo assíncrono (`/ia/chat/async`) usa `export_jobs` (thread + poll), que
  exige **1 worker** (mesma limitação já documentada da exportação/conciliação).
- Sem `LLM_BASE_URL`/`LLM_MODEL`, o `/ia/chat` responde **503 com erro claro**
  (degradação controlada) — a tela trata e não quebra.

## 6. Fronteiras respeitadas

Criados só os arquivos da spec 010: `app/services/ai_service.py`,
`app/services/ai_tools.py`, `app/services/ai_pii.py`,
`app/models/ai_chat_session.py`, `app/routers/ai_chat.py`,
`app/static/js/ai_assistant.js`, `app/templates/ai_chat.html`,
`tests/test_ai_service.py`, `tests/test_ai_tools.py`.

Não foram editados: `grid_serialization.py`, `faturamento_grid.py`,
`faturamento_planilha.html`, `billing_model.py`, `conftest.py`, `pytest.ini`,
o contrato, nem `main.py`/`config.py`/`db.py`/`requirements.txt`/`.env.example`/
`base.html` (mudanças acima são as ÚNICAS necessárias, aplicadas pela orquestradora).
```
