"""Testes do serviço de IA do faturamento (spec 010) — LLM SEMPRE MOCKADO.

Nenhum teste alcança um modelo real: injetamos `chat_fn` (a string crua que o
LLM "devolveria") ou monkeypatchamos `_call_llm`. Cobrimos:
  - pedidos-exemplo -> ops válidas segundo o schema do contrato;
  - resposta fora do schema -> rejeição/erro tratado;
  - LLM indisponível -> erro claro (LLMUnavailableError);
  - snapshot enviado ao LLM comprovadamente SEM PII.
"""
import json

import pytest

from app.services import ai_service, grid_serialization
from app.services.ai_service import LLMUnavailableError
from app.services import ai_pii
from app.services.ai_pii import PiiLeakError


def _fake_model():
    return {
        "id": 1,
        "nome": "FEMSA",
        "colunas": list(grid_serialization.FEMSA_COLUMNS),
        "estrutura": None,
        "campos_config": [],
        "encargos_pct": None,
        "taxa_adm_pct": None,
        "imposto_pct": None,
        "salario_formula": None,
    }


def _llm_returning(payload_dict):
    """chat_fn fake: ignora entrada e devolve o JSON dado como string."""
    def _fn(message, safe_snapshot):
        return json.dumps(payload_dict, ensure_ascii=False)
    return _fn


# ---------------------------------------------------------------------------
# Pedidos-exemplo -> ops válidas
# ---------------------------------------------------------------------------
def test_pedido_gera_ops_validas():
    llm = _llm_returning({
        "reply": "Adicionei a coluna de Uniformes.",
        "ops": [
            {"op": "add_column", "header": "UNIFORMES (Valor)", "tipo": "constante", "valor": 0},
            {"op": "set_model_param", "param": "encargos_pct", "valor": 57.91},
        ],
    })
    out = ai_service.chat(1, "adicione Uniformes e ajuste encargos", None,
                          model=_fake_model(), chat_fn=llm)
    assert out["ops"], out
    assert len(out["ops"]) == 2
    assert out["rejected"] == []
    # Prova: as ops produzidas aplicam via schema da 009.
    res = grid_serialization.apply_ops(_fake_model(), out["ops"])
    assert res.ok, res.errors


def test_resposta_fora_do_schema_e_rejeitada():
    llm = _llm_returning({
        "reply": "ok",
        "ops": [
            {"op": "explode_universe"},  # desconhecida -> rejeitada
            {"op": "set_model_param", "param": "imposto_pct", "valor": 8},  # válida
        ],
    })
    out = ai_service.chat(1, "faça algo", None, model=_fake_model(), chat_fn=llm)
    assert len(out["ops"]) == 1
    assert out["rejected"]
    assert "explode_universe" in out["reply"] or out["rejected"]


def test_pedido_ambiguo_ops_vazias_nao_inventa():
    llm = _llm_returning({"reply": "Poderia esclarecer qual coluna?", "ops": []})
    out = ai_service.chat(1, "muda lá aquilo", None, model=_fake_model(), chat_fn=llm)
    assert out["ops"] == []
    assert "esclarecer" in out["reply"].lower()


def test_llm_texto_com_cerca_markdown_e_parseado():
    def _fn(message, snap):
        return "```json\n" + json.dumps({"reply": "ok", "ops": []}) + "\n```"
    out = ai_service.chat(1, "x", None, model=_fake_model(), chat_fn=_fn)
    assert out["ops"] == []
    assert out["reply"] == "ok"


# ---------------------------------------------------------------------------
# LLM indisponível / resposta inutilizável -> erro claro
# ---------------------------------------------------------------------------
def test_llm_nao_configurado_levanta_erro_claro(monkeypatch):
    # Sem LLM_BASE_URL/LLM_MODEL, _call_llm falha com mensagem clara.
    monkeypatch.setattr(ai_service, "get_base_url", lambda: "")
    monkeypatch.setattr(ai_service, "get_model", lambda: "")
    with pytest.raises(LLMUnavailableError) as exc:
        ai_service.chat(1, "oi", None, model=_fake_model())
    assert "LLM local não configurado" in str(exc.value)


def test_llm_resposta_nao_json_levanta_erro():
    def _fn(message, snap):
        return "desculpe, não sei responder isso"
    with pytest.raises(LLMUnavailableError):
        ai_service.chat(1, "x", None, model=_fake_model(), chat_fn=_fn)


def test_llm_ops_nao_lista_levanta_erro():
    def _fn(message, snap):
        return json.dumps({"reply": "ok", "ops": {"nao": "lista"}})
    with pytest.raises(LLMUnavailableError):
        ai_service.chat(1, "x", None, model=_fake_model(), chat_fn=_fn)


def test_call_llm_rede_indisponivel(monkeypatch):
    import httpx
    monkeypatch.setattr(ai_service, "get_base_url", lambda: "http://127.0.0.1:1")
    monkeypatch.setattr(ai_service, "get_model", lambda: "fake-model")

    def _boom(*a, **k):
        raise httpx.ConnectError("recusado")
    monkeypatch.setattr(httpx, "post", _boom)
    with pytest.raises(LLMUnavailableError) as exc:
        ai_service._call_llm("oi", {})
    assert "Falha ao chamar o LLM local" in str(exc.value)


# ---------------------------------------------------------------------------
# PROVA: snapshot enviado ao LLM é SEM PII
# ---------------------------------------------------------------------------
def test_snapshot_do_009_nao_tem_pii():
    snap = grid_serialization.get_snapshot(_fake_model())
    safe = ai_pii.sanitize_snapshot(snap)
    ai_pii.assert_no_pii(safe)  # não levanta


def test_sanitize_remove_pii_injetada():
    # Snapshot "envenenado" com PII: CPF, e-mail, matrícula, lista de funcionários.
    poluido = {
        "model_id": 1,
        "nome": "FEMSA",
        "colunas": ["Nome", "Salário"],
        "colunas_meta": [{"header": "Nome", "tipo": "campo"}],
        "params": {"encargos_pct": 10},
        "sample_row": {"Nome": "João da Silva", "CPF": "123.456.789-00"},
        "funcionarios": [
            {"cpf": "111.222.333-44", "nome": "Maria", "email": "maria@x.com"}
        ],
        "cpf": "999.888.777-66",
        "observacao": "contato joao@empresa.com tel 11 98888-7777 matricula 000123456",
    }
    safe = ai_pii.sanitize_snapshot(poluido)
    # Chaves de PII sumiram por completo.
    assert "funcionarios" not in safe
    assert "cpf" not in safe
    assert "observacao" not in safe  # não está na allowlist de topo
    # sample_row preservou o rótulo estrutural (Nome) mas zerou o valor; a chave
    # "CPF" foi removida por ser um rótulo proibido (defesa em profundidade).
    assert safe["sample_row"] == {"Nome": None}
    # E o guard confirma: nada de PII sobrou.
    ai_pii.assert_no_pii(safe)


def test_assert_no_pii_detecta_cpf():
    with pytest.raises(PiiLeakError):
        ai_pii.assert_no_pii({"colunas": ["CPF 123.456.789-00"]})


def test_chat_envia_snapshot_sanitizado_ao_llm():
    """O snapshot que chega à função do LLM não pode conter PII, mesmo que o
    snapshot de entrada esteja poluído."""
    capturado = {}

    def _fn(message, safe_snapshot):
        capturado["snap"] = safe_snapshot
        return json.dumps({"reply": "ok", "ops": []})

    poluido = {
        "colunas": ["A"],
        "funcionarios": [{"cpf": "123.456.789-00", "nome": "Fulano"}],
        "sample_row": {"A": "valor-real-999"},
    }
    ai_service.chat(1, "oi", poluido, model=_fake_model(), chat_fn=_fn)
    snap = capturado["snap"]
    # Não vaza a lista de funcionários nem CPF; e não levanta no guard.
    assert "funcionarios" not in snap
    ai_pii.assert_no_pii(snap)
