"""Sanitização SEM PII do snapshot enviado ao LLM (spec 010, §5 do contrato).

Política da organização (NÃO-NEGOCIÁVEL): o snapshot que vai ao LLM — mesmo
sendo o modelo self-hosted — contém APENAS metadados de ESTRUTURA (nomes de
colunas, tipos, fórmulas, parâmetros do modelo e, no máximo, uma linha de
exemplo SINTÉTICA com valores nulos). NUNCA CPF, nome de funcionário, matrícula
real, e-mail pessoal ou valores individuais reais.

O `get_snapshot` da spec 009 (`app/services/grid_serialization.py`) já é sem PII
por construção. Esta camada é DEFESA EM PROFUNDIDADE: qualquer snapshot que
transite no caminho até o LLM passa por `sanitize_snapshot`, que:

  1. mantém só as chaves permitidas (allowlist de estrutura);
  2. varre recursivamente strings/valores e REDIGE qualquer coisa que "cheire" a
     PII (CPF, e-mail, telefone, chaves longas de dígitos);
  3. zera qualquer `sample_row`/linha de exemplo para valores nulos.

`assert_no_pii` levanta `PiiLeakError` se, após sanitizar, ainda houver algo
suspeito — usado pelos testes (prova de que o snapshot vai limpo).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

# Chaves de topo permitidas no snapshot que vai ao LLM (só estrutura).
_ALLOWED_TOP_KEYS = {
    "model_id",
    "nome",
    "colunas",
    "colunas_meta",
    "campos_config",
    "params",
    "tem_estrutura",
    "sample_row",
}

# Chaves que NUNCA podem existir num snapshot enviado ao LLM (PII direta).
_FORBIDDEN_KEYS = {
    "cpf", "nome_funcionario", "funcionario", "funcionarios", "matricula",
    "numcad", "email", "e_mail", "telefone", "endereco", "rg", "salario_real",
    "colaborador", "colaboradores", "pessoa", "pessoas", "linhas", "rows",
    "dados", "data_rows", "valores_reais", "holerite", "conta_bancaria",
}

# Padrões de PII textual (defense-in-depth em qualquer string).
_CPF_RE = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
_CNPJ_RE = re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(r"\b\(?\d{2}\)?[\s\-]?9?\d{4}[\s\-]?\d{4}\b")
# Sequência longa de dígitos (>=8) — matrícula/conta/telefone sem máscara.
_LONGNUM_RE = re.compile(r"\b\d{8,}\b")

_REDACTED = "«redigido»"


class PiiLeakError(Exception):
    """Levantada quando PII é detectada num payload destinado ao LLM."""


def _redact_str(s: str) -> str:
    out = _CPF_RE.sub(_REDACTED, s)
    out = _CNPJ_RE.sub(_REDACTED, out)
    out = _EMAIL_RE.sub(_REDACTED, out)
    out = _PHONE_RE.sub(_REDACTED, out)
    out = _LONGNUM_RE.sub(_REDACTED, out)
    return out


def _scrub(value: Any) -> Any:
    """Varre recursivamente um valor removendo PII textual e chaves proibidas."""
    if isinstance(value, str):
        return _redact_str(value)
    if isinstance(value, dict):
        clean: Dict[str, Any] = {}
        for k, v in value.items():
            if str(k).strip().lower() in _FORBIDDEN_KEYS:
                continue  # descarta a chave inteira
            clean[k] = _scrub(v)
        return clean
    if isinstance(value, (list, tuple)):
        return [_scrub(v) for v in value]
    return value


def sanitize_snapshot(snapshot: Any) -> Dict[str, Any]:
    """Devolve uma cópia do snapshot contendo SÓ metadados de estrutura, sem PII.

    - Mantém apenas as chaves de estrutura permitidas (`_ALLOWED_TOP_KEYS`).
    - Remove recursivamente chaves proibidas e redige PII textual.
    - Neutraliza qualquer linha de exemplo (`sample_row`) para valores nulos:
      preservamos os RÓTULOS das colunas, nunca valores reais.
    """
    if not isinstance(snapshot, dict):
        return {}

    filtered = {k: v for k, v in snapshot.items() if k in _ALLOWED_TOP_KEYS}
    clean = _scrub(filtered)

    # A linha de exemplo, se existir, vira {coluna: None} — só estrutura.
    sample = clean.get("sample_row")
    if isinstance(sample, dict):
        clean["sample_row"] = {str(k): None for k in sample.keys()}
    elif sample is not None:
        clean.pop("sample_row", None)

    return clean


def _iter_strings(value: Any) -> List[str]:
    acc: List[str] = []
    if isinstance(value, str):
        acc.append(value)
    elif isinstance(value, dict):
        for k, v in value.items():
            acc.append(str(k))
            acc.extend(_iter_strings(v))
    elif isinstance(value, (list, tuple)):
        for v in value:
            acc.extend(_iter_strings(v))
    return acc


def assert_no_pii(payload: Any) -> None:
    """Levanta `PiiLeakError` se `payload` ainda contiver PII aparente.

    Verifica chaves proibidas e padrões textuais de PII. Usado nos testes como
    prova de que o snapshot vai ao LLM sem PII, e como guarda em runtime.
    """
    if isinstance(payload, dict):
        for k in payload.keys():
            if str(k).strip().lower() in _FORBIDDEN_KEYS:
                raise PiiLeakError(f"Chave proibida no payload do LLM: {k!r}")
    for s in _iter_strings(payload):
        for rx, nome in (
            (_CPF_RE, "CPF"),
            (_CNPJ_RE, "CNPJ"),
            (_EMAIL_RE, "e-mail"),
            (_LONGNUM_RE, "sequência longa de dígitos"),
        ):
            if rx.search(s):
                raise PiiLeakError(f"PII detectada ({nome}) no payload do LLM: {s!r}")
