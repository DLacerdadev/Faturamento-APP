"""Fórmula de salário por MODELO de exportação (metodologia própria do cliente).

O gestor cadastra no modelo uma expressão aritmética simples que substitui o
salário-base no campo "Salário" da exportação — ex.: Skyrail projeta o salário
com regra própria em vez de usar o cadastral.

    salario / 29 * 30
    salario * 1.36
    total_remuneracao

Variáveis disponíveis (por funcionário, na competência exportada):
    salario            — salário-base cadastral (R034FUN.VALSAL)
    total_remuneracao  — Total Remuneração calculado (eventos mapeados)
    salario_dia_qtde   — quantidade do evento "SALARIO DIA (Qtde)" (dias)
    dias_mes           — constante 30
    valor              — valor-base do CAMPO configurado na grade "Fórmulas"
                         (soma do(s) código(s) buscado(s), ou o valor padrão do
                         campo quando a linha não define código)

Segurança: NADA de eval(). A expressão é parseada com `ast` e só aceita
números, as variáveis acima, + - * / e parênteses. Qualquer outra coisa é
rejeitada na validação (e a avaliação devolve None => exportação usa o
salário-base, sem quebrar).
"""
import ast
from typing import Any, Dict, Optional

VARIAVEIS = ("salario", "total_remuneracao", "salario_dia_qtde", "dias_mes", "valor")

_OPS_BIN = (ast.Add, ast.Sub, ast.Mult, ast.Div)


def _validar_no(no: ast.AST) -> Optional[str]:
    if isinstance(no, ast.Expression):
        return _validar_no(no.body)
    if isinstance(no, ast.BinOp):
        if not isinstance(no.op, _OPS_BIN):
            return "Operador não permitido (use + - * / e parênteses)."
        return _validar_no(no.left) or _validar_no(no.right)
    if isinstance(no, ast.UnaryOp):
        if not isinstance(no.op, (ast.USub, ast.UAdd)):
            return "Operador unário não permitido."
        return _validar_no(no.operand)
    if isinstance(no, ast.Constant):
        if isinstance(no.value, (int, float)):
            return None
        return "Só números são permitidos como constantes."
    if isinstance(no, ast.Name):
        if no.id in VARIAVEIS:
            return None
        return f"Variável desconhecida: '{no.id}'. Disponíveis: {', '.join(VARIAVEIS)}."
    return "Expressão inválida (só aritmética simples com as variáveis do sistema)."


def validar_formula(expr: Optional[str]) -> Optional[str]:
    """Retorna a mensagem de erro (PT-BR), ou None se a fórmula é válida."""
    if expr is None or not str(expr).strip():
        return None  # vazio = sem fórmula (salário-base)
    expr = str(expr).strip()
    if len(expr) > 200:
        return "Fórmula longa demais (máx. 200 caracteres)."
    try:
        arvore = ast.parse(expr, mode="eval")
    except SyntaxError:
        return "Fórmula inválida (erro de sintaxe)."
    return _validar_no(arvore)


def avaliar_formula(expr: Optional[str], variaveis: Dict[str, Any]) -> Optional[float]:
    """Avalia a fórmula com as variáveis do funcionário. Retorna None em
    qualquer problema (fórmula inválida, divisão por zero) — o chamador deve
    cair no salário-base. NUNCA levanta exceção."""
    if expr is None or not str(expr).strip():
        return None
    try:
        if validar_formula(expr):
            return None
        vals = {k: float(variaveis.get(k) or 0) for k in VARIAVEIS}

        def _ev(no: ast.AST) -> float:
            if isinstance(no, ast.Expression):
                return _ev(no.body)
            if isinstance(no, ast.BinOp):
                a, b = _ev(no.left), _ev(no.right)
                if isinstance(no.op, ast.Add):
                    return a + b
                if isinstance(no.op, ast.Sub):
                    return a - b
                if isinstance(no.op, ast.Mult):
                    return a * b
                return a / b  # Div (ZeroDivisionError -> except)
            if isinstance(no, ast.UnaryOp):
                v = _ev(no.operand)
                return -v if isinstance(no.op, ast.USub) else v
            if isinstance(no, ast.Constant):
                return float(no.value)
            if isinstance(no, ast.Name):
                return vals[no.id]
            raise ValueError("nó inválido")

        return round(_ev(ast.parse(str(expr).strip(), mode="eval")), 2)
    except Exception:
        return None
