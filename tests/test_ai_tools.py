"""Testes do tradutor/validador de grid-ops da IA (spec 010).

Validação reusa o schema canônico da 009 (grid_serialization.apply_ops). Sem LLM
aqui — só a camada de tradução/validação das ops.
"""
from app.services import ai_tools
from app.services import grid_serialization


def _fake_model():
    return {
        "id": 1,
        "nome": "TESTE",
        "colunas": list(grid_serialization.FEMSA_COLUMNS),
        "estrutura": None,
        "campos_config": [],
        "encargos_pct": None,
        "taxa_adm_pct": None,
        "imposto_pct": None,
        "salario_formula": None,
    }


def test_ops_validas_passam_pelo_schema_da_009():
    ops = [
        {"op": "set_model_param", "param": "encargos_pct", "valor": 57.91},
        {"op": "rename_column", "header": "OBS", "novo_header": "OBSERVACAO"},
    ]
    # A rename só é válida se 'OBS' existir; garantimos um modelo que a tem.
    model = _fake_model()
    model["colunas"] = ["OBS", "Total Geral"]
    validas, rejeitadas = ai_tools.build_and_validate_ops(ops, model=model)
    assert rejeitadas == []
    assert len(validas) == 2
    # Prova final: aplicar as validas via 009 dá ok.
    res = grid_serialization.apply_ops(model, validas)
    assert res.ok, res.errors


def test_op_desconhecida_e_rejeitada():
    validas, rejeitadas = ai_tools.build_and_validate_ops(
        [{"op": "drop_table", "header": "x"}], model=_fake_model()
    )
    assert validas == []
    assert rejeitadas and "desconhecida" in rejeitadas[0]


def test_add_column_duplicada_rejeitada_pelo_schema():
    model = _fake_model()
    existente = model["colunas"][0]
    validas, rejeitadas = ai_tools.build_and_validate_ops(
        [{"op": "add_column", "header": existente, "tipo": "campo"}], model=model
    )
    assert validas == []
    assert rejeitadas and "já existe" in rejeitadas[0].lower()


def test_duas_add_column_mesmo_header_colisao_incremental():
    # A 2ª deve ser rejeitada por colidir com a 1ª (validação incremental).
    ops = [
        {"op": "add_column", "header": "NOVA COL", "tipo": "constante", "valor": 0},
        {"op": "add_column", "header": "NOVA COL", "tipo": "constante", "valor": 1},
    ]
    validas, rejeitadas = ai_tools.build_and_validate_ops(ops, model=_fake_model())
    assert len(validas) == 1
    assert len(rejeitadas) == 1


def test_op_duplicada_exata_rejeitada():
    op = {"op": "set_model_param", "param": "imposto_pct", "valor": 10}
    validas, rejeitadas = ai_tools.build_and_validate_ops([op, dict(op)], model=_fake_model())
    assert len(validas) == 1
    assert rejeitadas and "duplicada" in rejeitadas[0]


def test_formula_sem_row_rejeitada():
    validas, rejeitadas = ai_tools.build_and_validate_ops(
        [{"op": "add_column", "header": "CALC", "tipo": "formula", "template": "=A+B"}],
        model=_fake_model(),
    )
    assert validas == []
    assert rejeitadas


def test_percentual_fora_de_faixa_rejeitado():
    validas, rejeitadas = ai_tools.build_and_validate_ops(
        [{"op": "set_model_param", "param": "encargos_pct", "valor": 250}],
        model=_fake_model(),
    )
    assert validas == []
    assert rejeitadas


def test_normalize_descarta_chaves_de_ruido():
    norm, erro = ai_tools.normalize_op(
        {"op": "remove_column", "header": "OBS", "lixo": "x", "confidence": 0.9}
    )
    assert erro is None
    assert norm == {"op": "remove_column", "header": "OBS"}


def test_sem_modelo_usa_fake_sintetico():
    # Sem model, ainda valida contra estrutura sintética (colunas FEMSA).
    validas, rejeitadas = ai_tools.build_and_validate_ops(
        [{"op": "set_model_param", "param": "taxa_adm_pct", "valor": 5}]
    )
    assert len(validas) == 1
    assert rejeitadas == []
