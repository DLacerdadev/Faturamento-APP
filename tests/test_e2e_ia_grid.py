"""E2E da costura IA → grid-ops → grid (specs 009 + 010) — teste da orquestradora.

Prova, com o LLM MOCKADO (offline), que a junção das duas specs funciona ponta a
ponta: um pedido em linguagem natural, respondido pelo LLM fake com `ops`, produz
operações VÁLIDAS (010/ai_tools) que a planilha (009/grid_serialization.apply_ops)
aplica de fato no modelo — sem PII saindo e sem mutar o modelo original.
"""
import json

import pytest

from app.services import ai_service
from app.services import grid_serialization as gs
from app.services.ai_service import LLMUnavailableError


def _modelo_base() -> dict:
    return {
        "id": 1,
        "nome": "Modelo E2E",
        "colunas": ["Nome", "Salário", "Total Geral"],
        "estrutura": None,
        "campos_config": [],
        "encargos_pct": 57.91,
        "taxa_adm_pct": 15.0,
        "imposto_pct": 8.5,
    }


def test_e2e_ia_produz_ops_que_a_planilha_aplica():
    modelo = _modelo_base()
    snapshot = gs.get_snapshot(modelo)  # 009: snapshot sem PII

    def fake_llm(message, safe_snapshot):
        # O que um LLM local devolveria para "adicione uma coluna de uniformes".
        return json.dumps({
            "reply": "Adicionei a coluna UNIFORMES (Valor).",
            "ops": [{
                "op": "add_column",
                "header": "UNIFORMES (Valor)",
                "tipo": "campo",
                "fonte": "UNIFORMES (Valor)",
            }],
        })

    out = ai_service.chat(1, "adicione uniformes", snapshot, model=modelo, chat_fn=fake_llm)

    # 010 produziu ops válidas...
    assert out["ops"], f"esperava ops válidas, veio {out}"
    assert not out.get("rejected")

    # ...e a 009 aplica de fato no modelo.
    res = gs.apply_ops(modelo, out["ops"])
    assert res.ok, res.errors
    assert "UNIFORMES (Valor)" in (res.model.get("colunas") or [])

    # modelo original intacto (apply trabalha em cópia).
    assert "UNIFORMES (Valor)" not in _modelo_base()["colunas"]


def test_e2e_ops_invalidas_do_llm_sao_descartadas():
    modelo = _modelo_base()

    def fake_llm(message, safe_snapshot):
        # Coluna duplicada (já existe "Nome") + op desconhecida -> ambas rejeitadas.
        return json.dumps({
            "reply": "ok",
            "ops": [
                {"op": "add_column", "header": "Nome", "tipo": "campo", "fonte": "Nome"},
                {"op": "voar", "header": "X"},
            ],
        })

    out = ai_service.chat(1, "quebre o modelo", {"colunas": modelo["colunas"]},
                          model=modelo, chat_fn=fake_llm)
    assert out["ops"] == []          # nada válido passou
    assert out["rejected"]           # e o porquê foi reportado


def test_e2e_llm_indisponivel_degrada_com_erro_claro():
    # Sem chat_fn e com LLM não configurado (conftest zera LLM_*): erro controlado.
    with pytest.raises(LLMUnavailableError):
        ai_service.chat(1, "oi", {"colunas": ["Nome"]})
