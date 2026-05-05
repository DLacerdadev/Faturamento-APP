"""
Serviço de montagem de faturas.
Transforma dados brutos de faturamento em estruturas agregadas para clientes.
"""
from typing import List, Dict, Any
from collections import defaultdict


def build_generic_invoice(billing_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Recebe as linhas brutas de faturamento (fetch_billing_data)
    e monta uma estrutura agregada por funcionário e centro de custo.
    
    Agrupa por (NUMCAD, NOMFUN, CODCCU) e soma VALEVE (valor do evento).
    
    Args:
        billing_rows: Lista de dicts com dados brutos do faturamento
        
    Returns:
        Lista de dicts agregados com:
        - numcad: Matrícula do funcionário
        - nomfun: Nome do funcionário
        - codccu: Centro de custo
        - valor_total: Soma dos valores dos eventos
        - perref: Período de referência
        - qtd_eventos: Quantidade de eventos
        - cargo: Cargo do funcionário
        - situacao: Situação do funcionário
    """
    if not billing_rows:
        return []
    
    aggregated: Dict[tuple, Dict[str, Any]] = {}
    
    for row in billing_rows:
        key = (
            row.get("numcad"),
            row.get("nomfun", ""),
            row.get("codccu", "")
        )
        
        if key not in aggregated:
            aggregated[key] = {
                "valor_total": 0.0,
                "qtd_eventos": 0,
                "eventos": [],
                "perref": None,
                "cargo": "",
                "situacao": "",
                "datadm": None,
                "valsal": 0.0
            }
        
        aggregated[key]["valor_total"] += float(row.get("valeve", 0) or 0)
        aggregated[key]["qtd_eventos"] += 1
        aggregated[key]["perref"] = row.get("perref")
        aggregated[key]["cargo"] = row.get("titred", "")
        aggregated[key]["situacao"] = row.get("dessit", "")
        aggregated[key]["datadm"] = row.get("datadm")
        aggregated[key]["valsal"] = row.get("valsal", 0)
        
        evento = {
            "codeve": row.get("codeve"),
            "deseve": row.get("deseve"),
            "refeve": row.get("refeve"),
            "valeve": row.get("valeve")
        }
        aggregated[key]["eventos"].append(evento)
    
    result = []
    for (numcad, nomfun, codccu), data in aggregated.items():
        result.append({
            "numcad": numcad,
            "nomfun": nomfun,
            "codccu": codccu,
            "valor_total": round(data["valor_total"], 2),
            "qtd_eventos": data["qtd_eventos"],
            "perref": data["perref"],
            "cargo": data["cargo"],
            "situacao": data["situacao"],
            "datadm": data["datadm"],
            "salario_base": data["valsal"]
        })
    
    result.sort(key=lambda x: (x["codccu"] or "", x["nomfun"] or ""))
    return result


def build_invoice_by_cost_center(billing_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Agrupa faturamento por centro de custo.
    
    Args:
        billing_rows: Lista de dicts com dados brutos
        
    Returns:
        Lista de dicts com totais por centro de custo
    """
    if not billing_rows:
        return []
    
    by_ccu: Dict[str, Dict[str, Any]] = {}
    
    for row in billing_rows:
        codccu = row.get("codccu", "SEM_CCU") or "SEM_CCU"
        
        if codccu not in by_ccu:
            by_ccu[codccu] = {
                "valor_total": 0.0,
                "funcionarios": set(),
                "qtd_eventos": 0
            }
        
        by_ccu[codccu]["valor_total"] += float(row.get("valeve", 0) or 0)
        by_ccu[codccu]["funcionarios"].add(row.get("numcad"))
        by_ccu[codccu]["qtd_eventos"] += 1
    
    result = []
    for codccu, data in by_ccu.items():
        result.append({
            "codccu": codccu,
            "valor_total": round(data["valor_total"], 2),
            "qtd_funcionarios": len(data["funcionarios"]),
            "qtd_eventos": data["qtd_eventos"]
        })
    
    result.sort(key=lambda x: x["codccu"] or "")
    return result


def build_invoice_detailed(billing_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Retorna dados detalhados para exportação em Excel.
    Mantém cada evento como linha separada, mas com formatação limpa.
    
    Args:
        billing_rows: Lista de dicts com dados brutos
        
    Returns:
        Lista formatada para Excel com colunas organizadas
    """
    if not billing_rows:
        return []
    
    result = []
    for row in billing_rows:
        result.append({
            "Matricula": row.get("numcad"),
            "Nome": row.get("nomfun"),
            "Centro Custo": row.get("codccu"),
            "Cargo": row.get("titred"),
            "Situacao": row.get("dessit"),
            "Data Admissao": row.get("datadm"),
            "Salario Base": row.get("valsal"),
            "Periodo Ref": row.get("perref"),
            "Cod Evento": row.get("codeve"),
            "Descricao Evento": row.get("deseve"),
            "Referencia": row.get("refeve"),
            "Valor": row.get("valeve")
        })
    
    result.sort(key=lambda x: (x["Centro Custo"] or "", x["Nome"] or "", x["Cod Evento"] or 0))
    return result
