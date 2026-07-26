"""Cliente do LLM LOCAL (self-hosted) — spec 010.

Fala com um endpoint compatível com a API OpenAI (`/chat/completions`) rodando
na PRÓPRIA infra (ex.: Ollama, vLLM, LM Studio). Nada sai da rede — decisão do
dono. Config lida de `app.config` com fallback para `os.getenv` (mesmo padrão do
`totvs_connector`): `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` (opcional),
`LLM_TIMEOUT`.

Fluxo:
  chat(model_id, message, snapshot) -> {"reply": str, "ops": [GridOp...]}

  1. SANITIZA o snapshot (ai_pii.sanitize_snapshot) — NUNCA envia PII ao LLM.
  2. Monta o system prompt do domínio de faturamento + o snapshot sanitizado.
  3. Chama o endpoint local; espera JSON {"reply","ops"} na resposta do modelo.
  4. Traduz/valida as ops via ai_tools (schema do contrato grid-ops) e devolve.

Degradação controlada: se o LLM local estiver indisponível/mal configurado, ou
devolver algo fora do formato, levanta `LLMUnavailableError` com mensagem clara.
O router traduz isso num erro amigável — a tela nunca vê exceção não-tratada.

TESTES: o LLM é SEMPRE mockado (offline). Os testes injetam um `chat_fn` fake
(ou monkeypatcham `_call_llm`) — nenhuma dependência de modelo real no CI.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable, Dict, List, Optional

import httpx

from app.services import ai_tools
from app.services.ai_pii import sanitize_snapshot, assert_no_pii

logger = logging.getLogger(__name__)


class LLMUnavailableError(Exception):
    """LLM local indisponível, mal configurado ou com resposta inválida."""


# ---------------------------------------------------------------------------
# Config (config.py com fallback os.getenv — igual ao totvs_connector)
# ---------------------------------------------------------------------------
def _cfg(name: str, default: str = "") -> str:
    try:
        from app import config as app_config
        val = getattr(app_config, name, None)
        if val is not None and str(val) != "":
            return str(val)
    except Exception:
        pass
    return os.getenv(name, default)


def get_base_url() -> str:
    # Sem barra final; o path /chat/completions é anexado.
    return _cfg("LLM_BASE_URL", "").rstrip("/")


def get_model() -> str:
    return _cfg("LLM_MODEL", "")


def get_api_key() -> str:
    return _cfg("LLM_API_KEY", "")


def get_timeout() -> float:
    try:
        return float(_cfg("LLM_TIMEOUT", "60") or "60")
    except (TypeError, ValueError):
        return 60.0


def is_configured() -> bool:
    """True se URL e modelo do LLM local estão preenchidos."""
    return bool(get_base_url() and get_model())


# ---------------------------------------------------------------------------
# System prompt do domínio
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """\
Você é o assistente de IA do módulo de FATURAMENTO de um sistema de RH.
Ajuda o gestor a montar e editar MODELOS de planilha de faturamento em linguagem
natural: criar/remover/renomear/mover colunas, configurar campos (evento da folha
+ fórmula), ajustar parâmetros do modelo e adicionar itens.

Você NÃO escreve na planilha diretamente. Você PRODUZ uma lista de operações
`grid-ops` (contrato fechado v1). O front aplica as operações depois da confirmação
do gestor.

REGRAS ABSOLUTAS:
- Você recebe apenas ESTRUTURA (nomes de colunas, tipos, fórmulas, parâmetros).
  NUNCA há CPF, nome de funcionário, matrícula ou valores individuais. Nunca peça
  nem invente dados pessoais.
- Só use estas operações (campos exatos):
  * {"op":"add_column","header":TEXTO,"tipo":"campo|formula|constante|vazio",
     "fonte":TEXTO?,"template":"=...{row}..."?,"valor":NUM?,"after":TEXTO?}
  * {"op":"remove_column","header":TEXTO}
  * {"op":"rename_column","header":TEXTO,"novo_header":TEXTO}
  * {"op":"move_column","header":TEXTO,"before":TEXTO? ,"after":TEXTO?}
  * {"op":"set_field_config","campo":TEXTO,"codigo":"257,259"?,"formula":TEXTO?}
  * {"op":"set_model_param","param":"encargos_pct|taxa_adm_pct|imposto_pct|salario_formula","valor":...}
  * {"op":"add_item","target":"catalogo|linha","payload":{...}}
- tipo "formula" EXIGE "template" contendo "{row}". tipo "constante" EXIGE "valor".
- Não crie coluna com header que já exista no snapshot.
- Percentuais ficam entre 0 e 100.
- Se o pedido for ambíguo, impossível ou perigoso, NÃO invente operações: devolve
  "ops": [] e explica no "reply" o que precisa ser esclarecido.

FORMATO DE SAÍDA (obrigatório): responda SOMENTE com um objeto JSON válido, sem
texto fora dele, sem markdown, no formato:
{"reply": "<explicação curta em português>", "ops": [ <grid-ops...> ]}
"""


# ---------------------------------------------------------------------------
# Chamada ao LLM
# ---------------------------------------------------------------------------
def _build_user_content(message: str, safe_snapshot: Dict[str, Any]) -> str:
    return (
        "Estrutura atual do modelo (SEM dados pessoais):\n"
        + json.dumps(safe_snapshot, ensure_ascii=False)
        + "\n\nPedido do gestor:\n"
        + (message or "").strip()
        + "\n\nResponda SOMENTE com o JSON {\"reply\":..., \"ops\":[...]}."
    )


def _call_llm(message: str, safe_snapshot: Dict[str, Any]) -> str:
    """Faz a chamada HTTP ao endpoint local compatível com OpenAI e devolve o
    conteúdo textual da resposta do modelo. Levanta LLMUnavailableError em falha.
    """
    base = get_base_url()
    model = get_model()
    if not base or not model:
        raise LLMUnavailableError(
            "LLM local não configurado (defina LLM_BASE_URL e LLM_MODEL no .env)."
        )

    url = f"{base}/chat/completions"
    headers = {"Content-Type": "application/json"}
    api_key = get_api_key()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_content(message, safe_snapshot)},
        ],
        "temperature": 0,
        "stream": False,
    }

    try:
        resp = httpx.post(url, json=payload, headers=headers, timeout=get_timeout())
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as exc:
        raise LLMUnavailableError(
            f"Falha ao chamar o LLM local em {base}: {exc}"
        ) from exc
    except (ValueError, json.JSONDecodeError) as exc:
        raise LLMUnavailableError(
            f"Resposta do LLM local não é JSON válido: {exc}"
        ) from exc

    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMUnavailableError(
            f"Resposta do LLM local em formato inesperado: {exc}"
        ) from exc


def _parse_llm_json(content: str) -> Dict[str, Any]:
    """Extrai o objeto JSON {reply, ops} do texto devolvido pelo modelo.

    Tolera cerca de ```json ...``` e texto ao redor: pega o primeiro '{' até o
    último '}'. Levanta LLMUnavailableError se não for parseável.
    """
    if content is None:
        raise LLMUnavailableError("LLM não devolveu conteúdo.")
    txt = str(content).strip()
    # Remove cercas de código markdown se presentes.
    if txt.startswith("```"):
        txt = txt.strip("`")
        if txt.lower().startswith("json"):
            txt = txt[4:]
    try:
        return json.loads(txt)
    except (ValueError, json.JSONDecodeError):
        pass
    ini = txt.find("{")
    fim = txt.rfind("}")
    if ini >= 0 and fim > ini:
        try:
            return json.loads(txt[ini:fim + 1])
        except (ValueError, json.JSONDecodeError) as exc:
            raise LLMUnavailableError(
                f"Não foi possível interpretar a saída do LLM como JSON: {exc}"
            ) from exc
    raise LLMUnavailableError("Saída do LLM não contém um objeto JSON.")


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------
def chat(
    model_id: Optional[int],
    message: str,
    snapshot: Optional[Dict[str, Any]],
    *,
    model: Any = None,
    chat_fn: Optional[Callable[[str, Dict[str, Any]], str]] = None,
) -> Dict[str, Any]:
    """Conversa com o LLM local e devolve {"reply", "ops"} com ops VÁLIDAS.

    - `snapshot`: estrutura do modelo (será sanitizada — sem PII — antes de sair).
      Se ausente e `model` (BillingModel/dict) for passado, deriva via 009.
    - `model`: opcional; usado para validar as ops produzidas contra o schema
      do contrato (grid_serialization.apply_ops sobre uma cópia).
    - `chat_fn`: injeção para TESTES — recebe (message, safe_snapshot) e devolve
      a string crua do LLM. Em produção usa `_call_llm`.

    Levanta LLMUnavailableError se o LLM estiver indisponível ou a resposta for
    inutilizável (degradação controlada — o router transforma em erro amigável).
    """
    # 1) Snapshot sem PII (defesa em profundidade — §5 do contrato / política org).
    if snapshot is None and model is not None:
        try:
            from app.services.grid_serialization import get_snapshot
            snapshot = get_snapshot(model)
        except Exception:  # noqa: BLE001
            snapshot = {}
    safe_snapshot = sanitize_snapshot(snapshot or {})
    # Prova/guard em runtime: nada de PII segue para o LLM.
    assert_no_pii(safe_snapshot)

    # 2) Chama o LLM (real ou injetado no teste).
    caller = chat_fn or _call_llm
    raw = caller(message, safe_snapshot)

    # 3) Interpreta a saída JSON do modelo.
    parsed = _parse_llm_json(raw)
    reply = parsed.get("reply")
    if not isinstance(reply, str):
        reply = "" if reply is None else str(reply)
    raw_ops = parsed.get("ops") or []
    if not isinstance(raw_ops, list):
        raise LLMUnavailableError("Campo 'ops' da resposta do LLM não é uma lista.")

    # 4) Traduz + VALIDA as ops contra o schema do contrato (ai_tools).
    valid_ops, rejected = ai_tools.build_and_validate_ops(raw_ops, model=model)

    if rejected:
        aviso = "; ".join(rejected[:5])
        if reply:
            reply = f"{reply}\n\n(Descartei operações inválidas: {aviso})"
        else:
            reply = f"Não consegui aplicar algumas operações: {aviso}"

    return {"reply": reply, "ops": valid_ops, "rejected": rejected}
