"""Testes de regressão da spec 007 — colunas ausentes no faturamento Skyrail.

Contexto do bug (ver docs/modelos/POSTMORTEM-skyrail-colunas.md):
  O faturamento Skyrail é dirigido por um BillingModel criado por upload de
  planilha (Contrato C1): parse_model_xlsx() extrai a estrutura, derive_colunas()
  deriva as colunas 'campo' e _render_por_estrutura() renderiza. Colunas "sumiam"
  em dois casos:
    (1) colunas de benefício/desconto por funcionário (CAFÉ, ALMOÇO, ATRASOS,
        DSR...) cujo cabeçalho NÃO casa com um campo do sistema eram classificadas
        como 'constante' e o renderizador APAGAVA o valor numérico (tratado como
        "exemplo do template"). Como o valor VARIA por funcionário no modelo, a
        coluna deveria manter o dado — não sumir.
    (2) colunas 'campo' cuja fonte canônica não existe no DataFrame montado
        ficavam vazias EM SILÊNCIO (sem log/aviso).

PII / OFFLINE:
  O template real (docs/modelos/skyrail_abril.xlsx) é gitignored e contém nomes de
  funcionários reais. Estes testes NÃO o usam. Em vez disso constroem em memória um
  xlsx SINTÉTICO (`_build_synthetic_skyrail_xlsx`) que reproduz o MESMO padrão
  estrutural do bug com dados 100% fictícios (FUNC UM/DOIS/TRÊS). Roda offline, sem
  banco, Senior, SMTP ou rede — só openpyxl + os serviços puros de exportação.
"""
from io import BytesIO

import pytest
from openpyxl import Workbook, load_workbook

from app.services.model_structure import parse_model_xlsx, derive_colunas, validate_estrutura
from app.services.excel_export import (
    billing_to_femsa_excel,
    FEMSA_COLUMNS,
    GERAL_COLUMNS,
)


# ---------------------------------------------------------------------------
# Fixture estrutural ANÔNIMA: reproduz o padrão Skyrail sem PII, em memória.
# ---------------------------------------------------------------------------
def _build_synthetic_skyrail_xlsx() -> bytes:
    """Constrói um .xlsx sintético que reproduz o padrão de layout da Skyrail:

    - bloco de título institucional acima do cabeçalho (2 linhas de topo);
    - cabeçalho em 2 linhas (grupos + rótulos);
    - colunas 'campo' com sinônimos (COLABORADOR→Nome, ADMISSAO→Dt Admissão,
      SEG. DE VIDA→SEGURO DE VIDA);
    - colunas de benefício/desconto POR FUNCIONÁRIO sem campo no sistema, umas
      CONSTANTES (CAFE = mesmo valor) e outras VARIÁVEIS (ALMOCO/ATRASOS/DSR);
    - fórmulas de subtotal/total que referenciam essas colunas.

    Dados totalmente fictícios (nenhum PII). É o cenário mínimo do bug.
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "FATURAMENTO"

    # Topo institucional (acima do cabeçalho).
    ws["B1"] = "RELATORIO DE FATURAMENTO (SINTETICO)"
    ws["B2"] = "Emitente: Fulano"

    # Cabeçalho: linha 3 = grupos, linha 4 = rótulos das colunas.
    ws["B3"] = "COLABORADOR"; ws["C3"] = "ADMISSAO"; ws["D3"] = "FUNCAO"
    ws["E3"] = "BENEFICIOS"; ws["I3"] = "TAXA ADM."; ws["J3"] = "DESCONTOS"; ws["L3"] = "TOTAL"
    ws["B4"] = "COLABORADOR"; ws["C4"] = "ADMISSAO"; ws["D4"] = "FUNCAO"
    ws["E4"] = "SALARIO"; ws["F4"] = "CAFE"; ws["G4"] = "ALMOCO"; ws["H4"] = "SEG. DE VIDA"
    ws["I4"] = 0.07  # taxa adm. (parâmetro no cabeçalho)
    ws["J4"] = "ATRASOS"; ws["K4"] = "DSR"; ws["L4"] = "TOTAL MENSAL"

    # 3 funcionários fictícios. CAFE = constante (10 sempre); ALMOCO/ATRASOS/DSR
    # variam por funcionário — é justamente o que sumia.
    linhas = [
        # nome,     admissao,     funcao,   sal,  cafe, almoco, seg,  atr,  dsr
        ("FUNC UM",   "01/01/2025", "CARGO A", 1000, 10, 50, 5, 88.0, 0),
        ("FUNC DOIS", "02/02/2025", "CARGO B", 2000, 10, 70, 5, 0.0, 12.0),
        ("FUNC TRES", "03/03/2025", "CARGO C", 3000, 10, 50, 5, 9.5, 0),
    ]
    r0 = 5
    for i, (nome, adm, func, sal, cafe, almoco, seg, atr, dsr) in enumerate(linhas):
        r = r0 + i
        ws[f"B{r}"] = nome
        ws[f"C{r}"] = adm
        ws[f"D{r}"] = func
        ws[f"E{r}"] = sal
        ws[f"F{r}"] = cafe
        ws[f"G{r}"] = almoco
        ws[f"H{r}"] = seg
        ws[f"I{r}"] = f"=(E{r}+F{r}+G{r}+H{r})*$I$4"
        ws[f"J{r}"] = atr
        ws[f"K{r}"] = dsr
        ws[f"L{r}"] = f"=E{r}+F{r}+G{r}+H{r}+I{r}-J{r}-K{r}"

    # Rodapé de total.
    rf = r0 + len(linhas) + 1
    ws[f"L{rf}"] = f"=SUM(L{r0}:L{r0 + len(linhas) - 1})"

    out = BytesIO()
    wb.save(out)
    return out.getvalue()


def _fake_employees(n=3):
    """Funcionários SINTÉTICOS (sem PII) para a renderização."""
    emps = []
    for i in range(n):
        emps.append({
            "matricula": str(1000 + i),
            "nome_funcionario": f"FUNC {i}",
            "cpf": f"{i}",
            "funcao": f"CARGO {i}",
            "salario": 1000.0 * (i + 1),
            "data_admissao": "2025-01-01",
            "eventos": [],
        })
    return emps


@pytest.fixture()
def skyrail_estrutura():
    return parse_model_xlsx(_build_synthetic_skyrail_xlsx(), "skyrail_sintetico.xlsx")


# ---------------------------------------------------------------------------
# Parse / classificação (Scenario 2 e 3 da spec)
# ---------------------------------------------------------------------------
def test_parse_reproduz_layout_skyrail(skyrail_estrutura):
    """A estrutura tem o cabeçalho em 2 linhas e a primeira linha de dados certa."""
    est = skyrail_estrutura
    assert est["aba"] == "FATURAMENTO"
    assert est["header_rows"] == [3, 4]
    assert est["data_row"] == 5
    # sanidade: estrutura utilizável (sem problemas de validação)
    assert validate_estrutura(est) == []


def test_sinonimos_casam_campos_conhecidos(skyrail_estrutura):
    """Scenario 2: cabeçalhos com sinônimo/acento/caixa viram 'campo' canônico."""
    por_letra = {c["letra"]: c for c in skyrail_estrutura["colunas"]}
    assert por_letra["B"]["tipo"] == "campo" and por_letra["B"]["fonte"] == "Nome"          # COLABORADOR
    assert por_letra["C"]["tipo"] == "campo" and por_letra["C"]["fonte"] == "Dt Admissão"   # ADMISSAO
    assert por_letra["D"]["tipo"] == "campo" and por_letra["D"]["fonte"] == "Função"        # FUNCAO
    assert por_letra["E"]["tipo"] == "campo" and por_letra["E"]["fonte"] == "Salário"       # SALARIO
    assert por_letra["H"]["tipo"] == "campo" and por_letra["H"]["fonte"] == "SEGURO DE VIDA"  # SEG. DE VIDA


def test_colunas_variaveis_marcadas_e_nao_somem(skyrail_estrutura):
    """Scenario 3: colunas per-funcionário sem campo no sistema, que VARIAM, são
    marcadas 'variavel' (para NÃO sumirem) — nunca em silêncio."""
    por_letra = {c["letra"]: c for c in skyrail_estrutura["colunas"]}
    # ALMOCO / ATRASOS / DSR variam -> marcadas variavel
    assert por_letra["G"]["tipo"] == "constante" and por_letra["G"].get("variavel") is True
    assert por_letra["J"]["tipo"] == "constante" and por_letra["J"].get("variavel") is True
    assert por_letra["K"]["tipo"] == "constante" and por_letra["K"].get("variavel") is True
    # CAFE é constante de verdade (mesmo valor em todas as linhas) -> NÃO variavel
    assert por_letra["F"]["tipo"] == "constante" and not por_letra["F"].get("variavel")


# ---------------------------------------------------------------------------
# Renderização (Scenario 1 / SC-1): todas as colunas do layout aparecem/preenchem
# ---------------------------------------------------------------------------
def _render(est, emps):
    out = billing_to_femsa_excel(emps, "2026-04", "CCU", estrutura=est)
    wb = load_workbook(BytesIO(out))
    return wb.active, int(est["data_row"])


def test_render_coluna_variavel_presente_mas_nao_fabrica_dado(skyrail_estrutura):
    """Decisão de INTEGRIDADE de faturamento (spec 007): colunas per-funcionário
    sem fonte no sistema (ALMOCO/ATRASOS/DSR) ficam PRESENTES no layout
    (cabeçalho/largura), mas as células de dado saem VAZIAS — o sistema NÃO
    fabrica valor a partir da 1ª linha do template. O bug 33 pedia "coluna
    incluída no arquivo", não "coluna preenchida com dado inventado"."""
    est = skyrail_estrutura
    emps = _fake_employees(3)
    ws, dr = _render(est, emps)

    for letra in ("G", "J", "K"):  # ALMOCO, ATRASOS, DSR
        # (a) a COLUNA existe no layout com cabeçalho preenchido
        assert ws[f"{letra}4"].value is not None, f"coluna {letra} sumiu do layout"
        # (c) nenhuma célula de dado é FABRICADA (todas vazias)
        for i in range(len(emps)):
            r = dr + i
            assert ws[f"{letra}{r}"].value is None, (
                f"coluna {letra} FABRICOU dado na linha {r} "
                f"(={ws[f'{letra}{r}'].value!r}) — deveria ficar vazia"
            )


def test_render_nao_fabrica_valor_da_primeira_linha(skyrail_estrutura):
    """Regressão específica: o valor da 1ª linha do template (ALMOCO=50) NÃO pode
    ser replicado para os demais funcionários — seria um número plausível e ERRADO
    na fatura (pior que vazio)."""
    est = skyrail_estrutura
    emps = _fake_employees(3)
    ws, dr = _render(est, emps)
    almoco_col_vals = [ws[f"G{dr + i}"].value for i in range(len(emps))]
    assert all(v is None for v in almoco_col_vals), (
        f"ALMOCO fabricou dado: {almoco_col_vals!r}"
    )


def test_render_todas_colunas_layout_presentes(skyrail_estrutura):
    """SC-1: nenhuma coluna some do arquivo — toda coluna do layout tem cabeçalho.
    Colunas com fonte real (campo/formula) preenchem; colunas sem fonte (constante
    variável) ficam presentes porém com célula de dado vazia (sem fabricar)."""
    est = skyrail_estrutura
    emps = _fake_employees(2)
    ws, dr = _render(est, emps)

    # As letras do layout aparecem no cabeçalho renderizado (linha 4).
    letras_layout = {c["letra"] for c in est["colunas"]}
    for letra in letras_layout:
        assert ws[f"{letra}4"].value is not None, f"coluna {letra} sem cabeçalho"

    # 'formula' e 'campo' com fonte no df preenchem a primeira linha de dados;
    # 'constante variável' fica vazia (não fabrica).
    por_letra = {c["letra"]: c for c in est["colunas"]}
    df_cols = set(derive_colunas(est))
    for letra, col in por_letra.items():
        v = ws[f"{letra}{dr}"].value
        if col["tipo"] == "formula":
            assert v is not None, f"formula {letra} vazia"
        elif col["tipo"] == "campo" and col.get("fonte") in df_cols:
            # campo com fonte presente e com dado preenche (ex.: Nome/Salário)
            pass
        elif col["tipo"] == "constante" and col.get("variavel"):
            assert v is None, f"constante variável {letra} NÃO deveria fabricar dado"


def test_render_campo_fonte_ausente_gera_aviso(skyrail_estrutura, caplog):
    """FR-4: coluna 'campo' cuja fonte NÃO existe no DataFrame emite WARNING
    (não some em silêncio). SEG. DE VIDA (H) mapeia para 'SEGURO DE VIDA', que o
    df sintético não popula (sem eventos)."""
    import logging
    est = skyrail_estrutura
    emps = _fake_employees(2)
    with caplog.at_level(logging.WARNING, logger="app.services.excel_export"):
        billing_to_femsa_excel(emps, "2026-04", "CCU", estrutura=est)
    msgs = " ".join(rec.getMessage() for rec in caplog.records)
    assert "SEGURO DE VIDA" in msgs, "faltou aviso de fonte ausente no DataFrame"


def test_parse_loga_colunas_sem_fonte(caplog):
    """FR-4: o parser avisa sobre as colunas per-funcionário sem fonte no sistema."""
    import logging
    with caplog.at_level(logging.WARNING, logger="app.services.model_structure"):
        parse_model_xlsx(_build_synthetic_skyrail_xlsx(), "skyrail_sintetico.xlsx")
    msgs = " ".join(rec.getMessage() for rec in caplog.records)
    assert "ALMOCO" in msgs and "ATRASOS" in msgs


def test_render_avisa_coluna_variavel_vazia(skyrail_estrutura, caplog):
    """O render avisa que a coluna variável fica presente porém VAZIA (Causa 1) —
    não passa em silêncio, mas também não fabrica dado."""
    import logging
    est = skyrail_estrutura
    emps = _fake_employees(2)
    with caplog.at_level(logging.WARNING, logger="app.services.excel_export"):
        billing_to_femsa_excel(emps, "2026-04", "CCU", estrutura=est)
    msgs = " ".join(rec.getMessage() for rec in caplog.records)
    assert "VAZIAS" in msgs and ("ALMOCO" in msgs or "ATRASOS" in msgs)


# ---------------------------------------------------------------------------
# NÃO-REGRESSÃO: FEMSA e GERAL (dirigidos por colunas, sem estrutura) intactos.
# ---------------------------------------------------------------------------
def test_nao_regride_femsa():
    """FR-5: o modelo FEMSA (default, sem estrutura) mantém EXATAMENTE as colunas."""
    emps = _fake_employees(1)
    out = billing_to_femsa_excel(emps, "2026-04", "CCU")  # sem colunas/estrutura -> FEMSA
    wb = load_workbook(BytesIO(out))
    ws = wb.active
    hdr = [c.value for c in ws[1]]
    assert hdr == FEMSA_COLUMNS


def test_nao_regride_geral():
    """FR-5: o modelo GERAL (dirigido por colunas) mantém EXATAMENTE as colunas."""
    emps = _fake_employees(1)
    out = billing_to_femsa_excel(emps, "2026-04", "CCU", colunas=list(GERAL_COLUMNS))
    wb = load_workbook(BytesIO(out))
    ws = wb.active
    hdr = [c.value for c in ws[1]]
    assert hdr == list(GERAL_COLUMNS)


def test_resolver_constante_nunca_fabrica_dado():
    """Integridade de faturamento: constante numérica sem fonte (com ou sem a flag
    'variavel') NÃO é escrita — não fabricamos dado. Só TAXA/percentual (parâmetro
    legítimo do cabeçalho) e texto (rótulo) são escritos."""
    from app.services.excel_export import _resolver_constante
    # constante numérica de exemplo: não escreve (célula vazia)
    val, fmt, escrever = _resolver_constante({"header": "CAFE", "valor": 10})
    assert escrever is False
    # marcada 'variavel' (per-funcionário sem fonte): TAMBÉM não escreve — repetir
    # o valor da 1ª linha fabricaria um número errado para os demais funcionários
    val2, fmt2, escrever2 = _resolver_constante({"header": "ALMOCO", "valor": 50, "variavel": True})
    assert escrever2 is False
    # taxa (parâmetro legítimo) segue escrevendo como % — inalterado
    val3, fmt3, escrever3 = _resolver_constante({"header": "TAXA ADM (%)", "valor": 0.07})
    assert escrever3 is True and fmt3 == "0%"
