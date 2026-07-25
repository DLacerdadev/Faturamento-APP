"""Conector do TOTVS Protheus via WSGETDATA (REST -> SQL) — feature 008.

O Protheus expõe o web service REST `WSGETDATA` que funciona como gateway de
SQL: recebe um `SELECT` (texto puro) no corpo e devolve JSON. Usamos isso para
ler o valor de EPI (último `C7_PRECO` por produto × centro de custo) direto da
fonte, substituindo o upload manual de Excel.

Contrato HTTP (confirmado em produção 2026-07-25 — ver
`specs/008-epi-auto-totvs/contracts/totvs-wsgetdata.md`):
  * POST em `<TOTVS_WSGETDATA_URL>/rest/WSGETDATA/v1`
  * Header `Authorization: Basic base64(user:senha)` (construído em runtime das
    env — nunca guardamos o base64 pronto) + `Content-Type: text/plain`.
  * Body = o `SELECT` (texto puro), dialeto MS SQL Server.
  * Sucesso = HTTP 201 com envelope `{"registros": [...], "qtde": N,
    "dicionario": []}`. Erro = HTTP 500 com `{"code":500,"message":"...ODBC..."}`.
  * Encoding cp1252/latin-1 (não utf-8) — casamos por CÓDIGO, descrição é só
    informativa.

Segurança (obrigatório):
  * Credenciais SÓ via env (`.env` gitignored). Nada em código/arquivo versionado.
  * O `SELECT` é FIXO no código do chamador (`price_autosync`), NUNCA montado a
    partir de entrada do usuário (gateway de SQL = superfície de injeção).
  * Segredo/endpoint nunca vai ao frontend nem a logs. Roda 100% server-side.

Integração (orquestradora — ver INTEGRATION-NOTES.md): o ideal é ler as env em
`app/config.py` (`TOTVS_WSGETDATA_URL/USER/PASSWORD`, `TOTVS_SYNC_HORARIO` e
`is_totvs_configured()`). Enquanto isso não é aplicado, este módulo lê as env
diretamente como fallback, usando `config` quando os atributos existirem.
"""
import base64
import logging
import os

import httpx

logger = logging.getLogger(__name__)

WSGETDATA_PATH = "/rest/WSGETDATA/v1"


def _cfg(name: str, default: str = "") -> str:
    """Lê uma configuração TOTVS: prioriza `app.config` (quando a orquestradora
    já tiver adicionado o atributo), senão cai para a env direta. Assim o módulo
    funciona antes e depois da integração ao config.py."""
    try:
        from app import config as app_config
        val = getattr(app_config, name, None)
        if val is not None and str(val) != "":
            return str(val)
    except Exception:
        pass
    return os.getenv(name, default)


def get_wsgetdata_url() -> str:
    return _cfg("TOTVS_WSGETDATA_URL", "")


def get_wsgetdata_user() -> str:
    return _cfg("TOTVS_WSGETDATA_USER", "")


def get_wsgetdata_password() -> str:
    return _cfg("TOTVS_WSGETDATA_PASSWORD", "")


def is_totvs_configured() -> bool:
    """True se URL + usuário + senha do WSGETDATA estão preenchidos.

    Prefere `config.is_totvs_configured()` quando disponível (após a integração),
    senão calcula localmente a partir das env."""
    try:
        from app import config as app_config
        fn = getattr(app_config, "is_totvs_configured", None)
        if callable(fn):
            return bool(fn())
    except Exception:
        pass
    return bool(get_wsgetdata_url() and get_wsgetdata_user() and get_wsgetdata_password())


def _auth_header() -> str:
    raw = f"{get_wsgetdata_user()}:{get_wsgetdata_password()}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _decode_body(resp: httpx.Response) -> str:
    """Decodifica o corpo como cp1252/latin-1 (o Protheus responde nesse
    encoding), com fallback tolerante para não estourar em bytes inesperados."""
    try:
        return resp.content.decode("cp1252")
    except Exception:
        return resp.content.decode("latin-1", errors="replace")


def run_query(sql: str, timeout: float = 60.0) -> list[dict]:
    """Executa um `SELECT` no WSGETDATA e devolve a lista `registros`.

    - `sql` deve ser FIXO no código do chamador (nunca entrada de usuário).
    - Levanta `RuntimeError` com mensagem clara se as env não estão configuradas
      (degrada em DEV_MODE sem TOTVS) ou se a resposta não vier no formato
      esperado (ex.: HTTP 500 do Protheus). O chamador (price_autosync) captura,
      loga o erro e NÃO destrói os preços existentes (falha segura).
    """
    if not sql or not str(sql).strip():
        raise RuntimeError("Consulta TOTVS vazia.")
    if not is_totvs_configured():
        raise RuntimeError(
            "TOTVS WSGETDATA não configurado (TOTVS_WSGETDATA_URL/USER/PASSWORD "
            "vazios no .env). Sincronização automática de EPI indisponível."
        )

    url = get_wsgetdata_url().rstrip("/") + WSGETDATA_PATH
    headers = {"Authorization": _auth_header(), "Content-Type": "text/plain"}

    resp = httpx.post(url, headers=headers, content=sql.encode("utf-8"), timeout=timeout)

    # Sucesso do WSGETDATA vem como 201 (não 200). Erro vem como 500 com corpo
    # {"code":500,"message":"...ODBC..."}. Tratamos qualquer 4xx/5xx como erro.
    if resp.status_code >= 400:
        detalhe = _decode_body(resp)[:500]
        raise RuntimeError(
            f"TOTVS WSGETDATA retornou HTTP {resp.status_code}: {detalhe}"
        )

    try:
        import json as _json
        payload = _json.loads(_decode_body(resp))
    except Exception as exc:
        raise RuntimeError(f"Resposta do TOTVS não é JSON válido: {exc}")

    if not isinstance(payload, dict) or "registros" not in payload:
        raise RuntimeError(
            "Resposta do TOTVS sem envelope esperado ({'registros': [...]})."
        )

    registros = payload.get("registros") or []
    if not isinstance(registros, list):
        raise RuntimeError("Campo 'registros' do TOTVS não é uma lista.")
    return registros
