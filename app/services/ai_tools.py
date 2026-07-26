"""Tradução + validação das operações `grid-ops` produzidas pela IA (spec 010).

A IA (ai_service) devolve uma lista bruta de operações. Este módulo:

  1. NORMALIZA cada operação para o conjunto FECHADO v1 do contrato
     (`specs/_contracts/grid-ops-protocol.md` §4), rejeitando ops desconhecidas,
     malformadas ou duplicadas.
  2. VALIDA as ops sobrando contra o SCHEMA REAL do contrato — reusando a
     validação canônica da spec 009 (`grid_serialization.apply_ops`) aplicada
     sobre uma CÓPIA de um modelo (fake se nenhum modelo real for passado).
     Assim as regras (coluna duplicada, template com {row}, faixa de %, fonte
     desconhecida etc.) são exatamente as que a 009 aplica no `apply-ops` —
     nunca divergem.

Contrato de fronteira: NÃO editamos grid_serialization; apenas o IMPORTAMOS para
validar. Uma op só é devolvida se passar por essa validação — nada inválido/
duplicado chega ao front.
"""
from __future__ import annotations

import copy
import json
from typing import Any, Dict, List, Optional, Tuple

from app.services import grid_serialization

# Conjunto fechado de operações v1 (discriminador -> campos aceitos).
_ALLOWED_OPS = {
    "add_column": {"op", "header", "tipo", "fonte", "template", "valor", "after"},
    "remove_column": {"op", "header"},
    "rename_column": {"op", "header", "novo_header"},
    "move_column": {"op", "header", "before", "after"},
    "set_field_config": {"op", "campo", "codigo", "codigo_nome", "formula"},
    "set_model_param": {"op", "param", "valor"},
    "add_item": {"op", "target", "payload"},
}


def normalize_op(op: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Normaliza UMA operação para a forma canônica v1.

    Retorna (op_normalizada, None) em sucesso, ou (None, motivo) se a op for
    desconhecida/malformada. Só mantém as chaves aceitas para o tipo (descarta
    ruído do LLM), sem alterar a semântica.
    """
    if not isinstance(op, dict):
        return None, f"operação não é objeto: {op!r}"
    nome = op.get("op")
    if nome not in _ALLOWED_OPS:
        return None, f"operação desconhecida '{nome}'"
    permitidas = _ALLOWED_OPS[nome]
    norm = {k: v for k, v in op.items() if k in permitidas}
    norm["op"] = nome
    return norm, None


def _op_signature(op: Dict[str, Any]) -> str:
    """Assinatura estável para detectar operações duplicadas exatas."""
    try:
        return json.dumps(op, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        return repr(sorted(op.items()))


def _fake_model() -> Dict[str, Any]:
    """Modelo sintético (sem PII) para validar ops isoladas quando não há modelo
    real. Usa as colunas canônicas FEMSA + parâmetros default — só estrutura."""
    return {
        "id": None,
        "nome": "__validador__",
        "colunas": list(grid_serialization.FEMSA_COLUMNS),
        "estrutura": None,
        "campos_config": [],
        "encargos_pct": None,
        "taxa_adm_pct": None,
        "imposto_pct": None,
        "salario_formula": None,
    }


def build_and_validate_ops(
    raw_ops: List[Any],
    *,
    model: Any = None,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Traduz e valida uma lista bruta de ops da IA.

    Devolve (ops_validas, rejeitadas). `ops_validas` é o subconjunto que:
      - normalizou para o schema fechado v1;
      - não é duplicata exata de outra op da mesma lista;
      - PASSA na validação canônica da 009 (apply_ops sobre uma cópia do modelo,
        aplicada incrementalmente para pegar colisões entre ops, ex.: duas
        add_column com o mesmo header).
    `rejeitadas` traz mensagens legíveis do porquê de cada descarte.

    Nunca muta o `model` de entrada: valida sempre sobre uma cópia (a própria
    apply_ops já trabalha em cópia; ainda assim partimos de um dict copiado).
    """
    validas: List[Dict[str, Any]] = []
    rejeitadas: List[str] = []
    vistos: set = set()

    # Base de validação: modelo real (como dict de trabalho) ou fake sintético.
    if model is not None:
        base = grid_serialization._as_model_dict(model)  # cópia profunda interna
    else:
        base = _fake_model()
    acumulado = copy.deepcopy(base)

    for i, raw in enumerate(raw_ops, start=1):
        norm, erro = normalize_op(raw)
        if norm is None:
            rejeitadas.append(f"op #{i}: {erro}")
            continue

        assinatura = _op_signature(norm)
        if assinatura in vistos:
            rejeitadas.append(f"op #{i}: duplicada ({norm.get('op')})")
            continue

        # Valida ESTA op no contexto das já aceitas (pega colisões incrementais).
        res = grid_serialization.apply_ops(acumulado, [norm])
        if not res.ok:
            motivo = "; ".join(res.errors) or "inválida"
            rejeitadas.append(f"op #{i} ({norm.get('op')}): {motivo}")
            continue

        # Aceita: avança o estado acumulado para a próxima validação.
        acumulado = res.model or acumulado
        vistos.add(assinatura)
        validas.append(norm)

    return validas, rejeitadas


def validate_ops(ops: List[Any], *, model: Any = None) -> Dict[str, Any]:
    """Atalho de conveniência: valida uma lista e devolve um dict resumido."""
    validas, rejeitadas = build_and_validate_ops(ops, model=model)
    return {
        "ok": len(rejeitadas) == 0,
        "ops": validas,
        "rejected": rejeitadas,
    }
