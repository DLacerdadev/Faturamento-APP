"""
Geração do Excel da Solicitação de Compra de EPI (feature 002).

Layout (R6 da research.md):
- Cabeçalho com empresa / CCU / competência / solicitante / data-hora
- Tabela de itens (Nome EPI, Tamanho, Qtde/func., Func. atendidos, Qtde total, Valor unit., Valor total)
- Linha TOTAL GERAL em destaque
- Bloco "Funcionários atendidos" (matrícula + nome)
"""
from datetime import datetime
from io import BytesIO
from typing import Dict, List, Tuple

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from app.models.epi_purchase import EpiPurchasePackage, EpiPurchaseItem


GOLD_FILL = PatternFill(start_color="D4A84B", end_color="D4A84B", fill_type="solid")
DARK_FILL = PatternFill(start_color="1A1A1A", end_color="1A1A1A", fill_type="solid")
SUBTLE_FILL = PatternFill(start_color="F5F5F5", end_color="F5F5F5", fill_type="solid")
TOTAL_FILL = PatternFill(start_color="FFF4DC", end_color="FFF4DC", fill_type="solid")
THIN = Side(border_style="thin", color="999999")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def _group_items(items: List[EpiPurchaseItem]) -> List[Dict]:
    """
    Agrupa linhas do cartesiano por (epi_id, tamanho, qtde_por_func, valor_unitario)
    para reconstruir a visão "por item" da compra.

    `quantidade_total` por item: prefere `quantidade_total_item` (override do
    usuário, replicado em cada linha do cartesiano). Se NULL em todas as linhas,
    cai pro fallback `qpf × n_funcionários`.
    `valor_total` é sempre recalculado: `quantidade_total × valor_unitario`.
    """
    buckets: Dict[Tuple, Dict] = {}
    for it in items:
        key = (
            it.epi_id,
            it.tamanho or "",
            it.quantidade_por_funcionario if it.quantidade_por_funcionario is not None else it.quantidade,
            round(it.valor_unitario or 0.0, 6),
        )
        bucket = buckets.setdefault(key, {
            "epi_id": it.epi_id,
            "descricao": it.descricao or "",
            "tamanho": it.tamanho or "",
            "quantidade_por_funcionario": it.quantidade_por_funcionario or it.quantidade,
            "valor_unitario": it.valor_unitario or 0.0,
            "valor_unitario_catalogo": it.valor_unitario_catalogo,
            "funcionarios": [],
            "quantidade_total": None,  # override
        })
        if it.employee_numcad is not None and it.employee_numcad not in [f["numcad"] for f in bucket["funcionarios"]]:
            bucket["funcionarios"].append({"numcad": it.employee_numcad, "nome": it.employee_nome or ""})
        if bucket["quantidade_total"] is None and it.quantidade_total_item is not None:
            bucket["quantidade_total"] = it.quantidade_total_item

    # Pós-processamento: fallback + cálculo do valor_total
    out = []
    for b in buckets.values():
        if b["quantidade_total"] is None:
            b["quantidade_total"] = (b["quantidade_por_funcionario"] or 0) * len(b["funcionarios"])
        b["valor_total"] = round(b["quantidade_total"] * (b["valor_unitario"] or 0.0), 2)
        out.append(b)
    return out


def _distinct_employees(items: List[EpiPurchaseItem]) -> List[Dict]:
    seen = set()
    out = []
    for it in items:
        if it.employee_numcad is None or it.employee_numcad in seen:
            continue
        seen.add(it.employee_numcad)
        out.append({"numcad": it.employee_numcad, "nome": it.employee_nome or ""})
    out.sort(key=lambda e: (e["nome"] or "").upper())
    return out


def _format_money(value: float) -> str:
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def generate_solicitacao_xlsx(pkg: EpiPurchasePackage) -> bytes:
    """Gera o Excel da solicitação de compra como bytes."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Solicitação"

    bold = Font(name="Calibri", size=11, bold=True)
    bold_gold = Font(name="Calibri", size=11, bold=True, color="1A1A1A")
    title = Font(name="Calibri", size=14, bold=True, color="1A1A1A")
    muted = Font(name="Calibri", size=10, color="6B6B6B")

    # Título
    ws.merge_cells("A1:G1")
    ws["A1"] = "SOLICITAÇÃO DE COMPRA — EPI"
    ws["A1"].font = title
    ws["A1"].fill = GOLD_FILL
    ws["A1"].alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 28

    # Cabeçalho
    competencia = pkg.mes_ano.strftime("%m/%Y") if pkg.mes_ano else "—"
    gen_at = (pkg.solicitacao_generated_at or datetime.utcnow()).strftime("%d/%m/%Y %H:%M")

    header_rows = [
        ("Empresa solicitante", pkg.empresa or "—"),
        ("Centro de custo", pkg.codccu or "—"),
        ("Competência", competencia),
        ("Solicitante", pkg.solicitante_nome or "—"),
        ("Gerada em", gen_at),
        ("ID da compra", f"#{pkg.id}"),
    ]
    row = 3
    for label, value in header_rows:
        ws.cell(row=row, column=1, value=label).font = bold
        ws.cell(row=row, column=1).fill = SUBTLE_FILL
        ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=7)
        ws.cell(row=row, column=2, value=value).alignment = Alignment(horizontal="left")
        row += 1

    # Tabela de itens
    row += 1
    table_header_row = row
    headers = ["EPI", "Tamanho", "Qtde / func.", "Func. atendidos", "Qtde total", "Valor unit.", "Valor total"]
    for col_idx, h in enumerate(headers, start=1):
        cell = ws.cell(row=row, column=col_idx, value=h)
        cell.font = bold_gold
        cell.fill = GOLD_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = BORDER
    row += 1

    grouped = _group_items(list(pkg.items or []))
    total_qtde = 0
    total_valor = 0.0
    for item in grouped:
        ws.cell(row=row, column=1, value=item["descricao"]).border = BORDER
        ws.cell(row=row, column=2, value=item["tamanho"]).border = BORDER
        ws.cell(row=row, column=3, value=item["quantidade_por_funcionario"]).border = BORDER
        ws.cell(row=row, column=4, value=len(item["funcionarios"])).border = BORDER
        ws.cell(row=row, column=5, value=item["quantidade_total"]).border = BORDER
        ws.cell(row=row, column=6, value=_format_money(item["valor_unitario"])).border = BORDER
        ws.cell(row=row, column=7, value=_format_money(item["valor_total"])).border = BORDER

        # Aviso de override de valor
        if item.get("valor_unitario_catalogo") is not None and abs((item["valor_unitario_catalogo"] or 0) - item["valor_unitario"]) > 0.001:
            ws.cell(row=row, column=6).comment = None  # comment intentional skip
            ws.cell(row=row, column=6).font = Font(name="Calibri", size=11, italic=True, color="B8923F")

        total_qtde += item["quantidade_total"]
        total_valor += item["valor_total"]
        row += 1

    # Total geral
    for col in range(1, 8):
        ws.cell(row=row, column=col).fill = TOTAL_FILL
        ws.cell(row=row, column=col).border = BORDER
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=4)
    ws.cell(row=row, column=1, value="TOTAL GERAL").font = bold
    ws.cell(row=row, column=1).alignment = Alignment(horizontal="right")
    ws.cell(row=row, column=5, value=total_qtde).font = bold
    ws.cell(row=row, column=7, value=_format_money(total_valor)).font = bold
    row += 2

    # Bloco "Funcionários atendidos"
    funcionarios = _distinct_employees(list(pkg.items or []))
    if funcionarios:
        ws.cell(row=row, column=1, value=f"Funcionários atendidos ({len(funcionarios)})").font = bold
        ws.cell(row=row, column=1).fill = SUBTLE_FILL
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=7)
        row += 1
        ws.cell(row=row, column=1, value="Matrícula").font = bold_gold
        ws.cell(row=row, column=2, value="Nome").font = bold_gold
        ws.cell(row=row, column=1).fill = GOLD_FILL
        ws.cell(row=row, column=2).fill = GOLD_FILL
        ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=7)
        row += 1
        for f in funcionarios:
            ws.cell(row=row, column=1, value=f["numcad"]).border = BORDER
            ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=7)
            ws.cell(row=row, column=2, value=f["nome"]).border = BORDER
            row += 1

    # Larguras
    widths = [32, 12, 14, 17, 12, 16, 16]
    for idx, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = w

    # Rodapé
    row += 2
    ws.cell(row=row, column=1, value="Gerado automaticamente pelo Faturamento App — Telos Consultoria").font = muted

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_filename(pkg: EpiPurchasePackage) -> str:
    """Retorna o nome canônico do arquivo da solicitação."""
    ts = (pkg.solicitacao_generated_at or datetime.utcnow()).strftime("%Y%m%d-%H%M%S")
    return f"solicitacao_epi_{pkg.id}_{ts}.xlsx"
