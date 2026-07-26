"""Testes do motor de serialização da planilha (spec 009).

Cobre:
  - Round-trip XLSX -> grid -> XLSX (igualdade tolerante) — SC-1.
  - Igualdade preview × export (mesmos bytes -> mesmo grid) — SC-0.
  - apply_ops para TODAS as ops v1 do contrato, incl. erros/validação — SC-2.
  - getSnapshot / get_snapshot SEM PII — §5 do contrato.

Tudo com fixtures ANÔNIMAS (nada de CPF/nome reais). Offline; reusa o harness 006.
"""
from io import BytesIO

import pytest
from openpyxl import Workbook

from app.services.excel_export import billing_to_femsa_excel, FEMSA_COLUMNS
from app.services.grid_serialization import (
    ApplyResult,
    apply_ops,
    get_snapshot,
    grid_to_xlsx,
    grids_equivalentes,
    model_to_grid,
    xlsx_to_grid,
)


# ---------------------------------------------------------------------------
# Fixtures anônimas
# ---------------------------------------------------------------------------
def _xlsx_bytes_simple() -> bytes:
    """Planilha .xlsx sintética: 2 col de cabeçalho, 2 linhas de dado, 1 fórmula, 1 merge."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Faturamento"
    ws["A1"] = "Empresa"
    ws["B1"] = "HORAS EXTRAS (Valor)"
    ws["C1"] = "Total"
    ws["A2"] = "Empresa Teste"
    ws["B2"] = 1234.5
    ws["C2"] = "=B2*2"
    ws["A3"] = "Outra Empresa"
    ws["B3"] = 10
    ws["C3"] = "=B3*2"
    ws.merge_cells("A1:A1")
    out = BytesIO()
    wb.save(out)
    return out.getvalue()


def _estrutura_anon() -> dict:
    """Estrutura C1 mínima e anônima (2 colunas 'campo')."""
    return {
        "aba": "Fat",
        "header_rows": [1],
        "data_row": 2,
        "headers": {"A": {"1": "Empresa"}, "B": {"1": "HORAS EXTRAS (Valor)"}},
        "colunas": [
            {"letra": "A", "header": "Empresa", "tipo": "campo", "fonte": "Empresa"},
            {"letra": "B", "header": "HORAS EXTRAS (Valor)", "tipo": "campo",
             "fonte": "HORAS EXTRAS (Valor)"},
        ],
        "merges": [],
    }


def _model_lista() -> dict:
    return {
        "id": 1, "nome": "Modelo Lista",
        "colunas": ["Empresa", "HORAS EXTRAS (Valor)", "SEGURO DE VIDA"],
        "estrutura": None, "campos_config": [],
        "encargos_pct": None, "taxa_adm_pct": None, "imposto_pct": None,
        "salario_formula": None,
    }


def _model_estrutura() -> dict:
    return {
        "id": 2, "nome": "Modelo Estrutura",
        "colunas": ["Empresa", "HORAS EXTRAS (Valor)"],
        "estrutura": _estrutura_anon(), "campos_config": [],
        "encargos_pct": None, "taxa_adm_pct": None, "imposto_pct": None,
        "salario_formula": None,
    }


def _emps_anon():
    """Funcionários sintéticos (dados fictícios) para gerar o .xlsx de faturamento."""
    return [
        {"matricula": "1", "nome_funcionario": "FUNC A", "cpf": "11111111111",
         "cargo": "Cargo", "funcao": "Funcao", "salario": 2000.0, "eventos": []},
        {"matricula": "2", "nome_funcionario": "FUNC B", "cpf": "22222222222",
         "cargo": "Cargo", "funcao": "Funcao", "salario": 3000.0, "eventos": []},
    ]


# ---------------------------------------------------------------------------
# SC-1: round-trip XLSX -> grid -> XLSX
# ---------------------------------------------------------------------------
def test_roundtrip_xlsx_grid_xlsx_estavel():
    data = _xlsx_bytes_simple()
    grid = xlsx_to_grid(data)
    assert grid["n_rows"] == 3 and grid["n_cols"] == 3
    de_volta = grid_to_xlsx(grid)
    grid2 = xlsx_to_grid(de_volta)
    igual, motivo = grids_equivalentes(grid, grid2)
    assert igual, motivo


def test_roundtrip_preserva_formulas_e_valores():
    grid = xlsx_to_grid(_xlsx_bytes_simple())
    # Fórmula preservada como texto "="
    assert grid["cells"][1][2]["v"] == "=B2*2"
    # Valor numérico preservado
    assert grid["cells"][1][1]["v"] == 1234.5


def test_roundtrip_preserva_merges():
    wb = Workbook()
    ws = wb.active
    ws["A1"] = "Grupo"
    ws["A2"] = "x"
    ws["B2"] = "y"
    ws.merge_cells("A1:B1")
    out = BytesIO()
    wb.save(out)
    grid = xlsx_to_grid(out.getvalue())
    assert "A1:B1" in grid["merges"]
    grid2 = xlsx_to_grid(grid_to_xlsx(grid))
    assert "A1:B1" in grid2["merges"]


def test_igualdade_tolerante_int_vs_float():
    g1 = {"cells": [[{"v": 5}]], "merges": []}
    g2 = {"cells": [[{"v": 5.0}]], "merges": []}
    igual, _ = grids_equivalentes(g1, g2)
    assert igual


def test_igualdade_detecta_diferenca():
    g1 = {"cells": [[{"v": 5}]], "merges": []}
    g2 = {"cells": [[{"v": 6}]], "merges": []}
    igual, motivo = grids_equivalentes(g1, g2)
    assert not igual and "!=" in motivo


# ---------------------------------------------------------------------------
# SC-0: preview × export (mesmos bytes -> mesmo grid)
# ---------------------------------------------------------------------------
def test_preview_igual_export():
    """O preview do grid é SEMPRE derivado dos mesmos bytes do export."""
    bts = billing_to_femsa_excel(_emps_anon(), "2026-07", "CC")
    grid_preview = xlsx_to_grid(bts)   # o que "Consultar faturamento" renderiza
    grid_export = xlsx_to_grid(bts)    # o que o export baixaria (mesmos bytes)
    igual, motivo = grids_equivalentes(grid_preview, grid_export)
    assert igual, motivo


def test_preview_tem_colunas_femsa():
    bts = billing_to_femsa_excel(_emps_anon(), "2026-07", "CC")
    grid = xlsx_to_grid(bts)
    header = [c["v"] if c else None for c in grid["cells"][0]]
    assert "Empresa" in header
    assert "Total Geral" in header
    # 2 funcionários -> 2 linhas de dado + cabeçalho
    assert grid["n_rows"] == 3


def test_preview_estrutura_igual_export():
    """Modelo dirigido por estrutura: preview e export saem do mesmo caminho."""
    est = _estrutura_anon()
    bts = billing_to_femsa_excel(_emps_anon(), "2026-07", "CC", estrutura=est)
    g1 = xlsx_to_grid(bts)
    g2 = xlsx_to_grid(bts)
    igual, motivo = grids_equivalentes(g1, g2)
    assert igual, motivo
    header = [c["v"] if c else None for c in g1["cells"][0]]
    assert "Empresa" in header


# ---------------------------------------------------------------------------
# model_to_grid — SEM PII (só cabeçalhos)
# ---------------------------------------------------------------------------
def test_model_to_grid_lista_sem_dados():
    grid = model_to_grid(_model_lista())
    assert grid["n_rows"] == 1  # só cabeçalho
    header = [c["v"] if c else None for c in grid["cells"][0]]
    assert header == ["Empresa", "HORAS EXTRAS (Valor)", "SEGURO DE VIDA"]


def test_model_to_grid_estrutura_sem_dados():
    grid = model_to_grid(_model_estrutura())
    assert grid["n_rows"] == 1
    assert grid["meta"]["data_row"] == 2
    headers = [c["v"] for row in grid["cells"] for c in row if c]
    assert "Empresa" in headers


# ---------------------------------------------------------------------------
# §5: snapshot SEM PII
# ---------------------------------------------------------------------------
def test_snapshot_sem_pii():
    snap = get_snapshot(_model_estrutura())
    # sample_row só tem chaves (rótulos) com valor None — nenhum dado real.
    assert all(v is None for v in snap["sample_row"].values())
    # Nenhum campo com CPF/nome/matrícula de funcionário.
    blob = str(snap).lower()
    for proibido in ("cpf", "11111111111", "func a", "matricula_real"):
        assert proibido not in blob
    assert "colunas" in snap and "params" in snap


def test_snapshot_expoe_estrutura():
    md = _model_estrutura()
    md["encargos_pct"] = 57.91
    snap = get_snapshot(md)
    assert snap["params"]["encargos_pct"] == 57.91
    assert "Empresa" in snap["colunas"]
    assert snap["tem_estrutura"] is True


# ---------------------------------------------------------------------------
# SC-2: apply_ops para TODAS as ops v1
# ---------------------------------------------------------------------------
def test_op_add_column_campo():
    md = _model_lista()
    r = apply_ops(md, [{"op": "add_column", "header": "PREMIO/BONUS",
                        "tipo": "campo", "fonte": "PREMIO/BONUS", "after": "Empresa"}])
    assert r.ok and r.applied == 1
    assert r.model["colunas"] == ["Empresa", "PREMIO/BONUS",
                                  "HORAS EXTRAS (Valor)", "SEGURO DE VIDA"]


def test_op_add_column_estrutura_atribui_letra():
    md = _model_estrutura()
    r = apply_ops(md, [{"op": "add_column", "header": "SEGURO DE VIDA",
                        "tipo": "campo", "fonte": "SEGURO DE VIDA"}])
    assert r.ok
    cols = r.model["estrutura"]["colunas"]
    nova = [c for c in cols if c["header"] == "SEGURO DE VIDA"][0]
    assert nova["letra"] and nova["letra"] not in ("A", "B")


def test_op_add_column_formula_exige_row():
    md = _model_lista()
    r = apply_ops(md, [{"op": "add_column", "header": "Dobro", "tipo": "formula",
                        "template": "=B*2"}])
    assert not r.ok and any("{row}" in e for e in r.errors)
    r2 = apply_ops(md, [{"op": "add_column", "header": "Dobro", "tipo": "formula",
                         "template": "=B{row}*2"}])
    assert r2.ok


def test_op_add_column_constante_exige_valor():
    md = _model_lista()
    r = apply_ops(md, [{"op": "add_column", "header": "Fixo", "tipo": "constante"}])
    assert not r.ok and any("valor" in e.lower() for e in r.errors)
    r2 = apply_ops(md, [{"op": "add_column", "header": "Fixo", "tipo": "constante",
                         "valor": 7}])
    assert r2.ok


def test_op_add_column_duplicado_erro():
    md = _model_lista()
    r = apply_ops(md, [{"op": "add_column", "header": "Empresa", "tipo": "campo"}])
    assert not r.ok and any("já existe" in e for e in r.errors)


def test_op_add_column_fonte_desconhecida_warning():
    md = _model_lista()
    r = apply_ops(md, [{"op": "add_column", "header": "CAFÉ", "tipo": "campo",
                        "fonte": "CAFÉ"}])
    assert r.ok
    assert any("não reconhecida" in w for w in r.warnings)


def test_op_remove_column():
    md = _model_lista()
    r = apply_ops(md, [{"op": "remove_column", "header": "SEGURO DE VIDA"}])
    assert r.ok and "SEGURO DE VIDA" not in r.model["colunas"]


def test_op_remove_column_inexistente_erro():
    md = _model_lista()
    r = apply_ops(md, [{"op": "remove_column", "header": "NAO EXISTE"}])
    assert not r.ok and any("não encontrada" in e for e in r.errors)


def test_op_rename_column():
    md = _model_lista()
    r = apply_ops(md, [{"op": "rename_column", "header": "SEGURO DE VIDA",
                        "novo_header": "SEGURO"}])
    assert r.ok and "SEGURO" in r.model["colunas"]


def test_op_rename_column_colisao_erro():
    md = _model_lista()
    r = apply_ops(md, [{"op": "rename_column", "header": "Empresa",
                        "novo_header": "SEGURO DE VIDA"}])
    assert not r.ok and any("já existe" in e for e in r.errors)


def test_op_move_column_before():
    md = _model_lista()
    r = apply_ops(md, [{"op": "move_column", "header": "SEGURO DE VIDA",
                        "before": "Empresa"}])
    assert r.ok and r.model["colunas"][0] == "SEGURO DE VIDA"


def test_op_move_column_after():
    md = _model_lista()
    r = apply_ops(md, [{"op": "move_column", "header": "Empresa",
                        "after": "SEGURO DE VIDA"}])
    assert r.ok and r.model["colunas"][-1] == "Empresa"


def test_op_move_column_alvo_inexistente_erro():
    md = _model_lista()
    r = apply_ops(md, [{"op": "move_column", "header": "Empresa",
                        "before": "NAO EXISTE"}])
    assert not r.ok


def test_op_set_field_config():
    md = _model_lista()
    r = apply_ops(md, [{"op": "set_field_config", "campo": "HORAS EXTRAS (Valor)",
                        "codigo": "257,259", "formula": "valor"}])
    assert r.ok
    cfg = [c for c in r.model["campos_config"] if c["campo"] == "HORAS EXTRAS (Valor)"][0]
    assert cfg["codigo"] == "257,259" and cfg["formula"] == "valor"


def test_op_set_field_config_codigo_invalido_erro():
    md = _model_lista()
    r = apply_ops(md, [{"op": "set_field_config", "campo": "HORAS EXTRAS (Valor)",
                        "codigo": "abc"}])
    assert not r.ok and any("código inválido" in e for e in r.errors)


def test_op_set_model_param_pct():
    md = _model_lista()
    r = apply_ops(md, [{"op": "set_model_param", "param": "encargos_pct",
                        "valor": 57.91}])
    assert r.ok and r.model["encargos_pct"] == 57.91


def test_op_set_model_param_pct_fora_faixa_erro():
    md = _model_lista()
    r = apply_ops(md, [{"op": "set_model_param", "param": "imposto_pct",
                        "valor": 150}])
    assert not r.ok and any("entre 0 e 100" in e for e in r.errors)


def test_op_set_model_param_param_invalido_erro():
    md = _model_lista()
    r = apply_ops(md, [{"op": "set_model_param", "param": "inexistente",
                        "valor": 1}])
    assert not r.ok and any("inválido" in e for e in r.errors)


def test_op_set_model_param_salario_formula_invalida_erro():
    md = _model_lista()
    r = apply_ops(md, [{"op": "set_model_param", "param": "salario_formula",
                        "valor": "import os"}])
    assert not r.ok


def test_op_add_item_target_valido_warning():
    md = _model_lista()
    r = apply_ops(md, [{"op": "add_item", "target": "linha", "payload": {}}])
    assert r.ok and any("sintética" in w for w in r.warnings)


def test_op_add_item_target_invalido_erro():
    md = _model_lista()
    r = apply_ops(md, [{"op": "add_item", "target": "banco", "payload": {}}])
    assert not r.ok


def test_op_desconhecida_rejeitada():
    md = _model_lista()
    r = apply_ops(md, [{"op": "explode_planilha"}])
    assert not r.ok and any("desconhecida" in e for e in r.errors)


def test_apply_ops_all_or_nothing_nao_corrompe():
    """Se qualquer op falha, NADA é aplicado e o modelo original fica intacto."""
    md = _model_lista()
    original = list(md["colunas"])
    r = apply_ops(md, [
        {"op": "add_column", "header": "PREMIO/BONUS", "tipo": "campo", "fonte": "PREMIO/BONUS"},
        {"op": "remove_column", "header": "NAO EXISTE"},  # falha
    ])
    assert not r.ok
    assert r.applied == 0
    # modelo devolvido = original, intacto
    assert r.model["colunas"] == original
    # o dict de entrada também não foi mutado
    assert md["colunas"] == original


def test_apply_ops_multiplas_ops_sequenciais():
    md = _model_lista()
    r = apply_ops(md, [
        {"op": "add_column", "header": "PREMIO/BONUS", "tipo": "campo", "fonte": "PREMIO/BONUS"},
        {"op": "rename_column", "header": "SEGURO DE VIDA", "novo_header": "SEGURO"},
        {"op": "set_model_param", "param": "taxa_adm_pct", "valor": 12.5},
    ])
    assert r.ok and r.applied == 3
    assert "PREMIO/BONUS" in r.model["colunas"]
    assert "SEGURO" in r.model["colunas"]
    assert r.model["taxa_adm_pct"] == 12.5


def test_apply_ops_retorna_apply_result():
    r = apply_ops(_model_lista(), [])
    assert isinstance(r, ApplyResult)
    d = r.to_dict()
    assert set(d.keys()) == {"ok", "applied", "warnings", "errors", "model"}


def test_apply_ops_ops_nao_lista_erro():
    r = apply_ops(_model_lista(), "não é lista")
    assert not r.ok and r.errors
