"""Rotina de sincronização automática de preços de EPI a partir do TOTVS.

Feature 008 (itens 30/31/32 do Plano). Consulta o TOTVS via `totvs_connector`
com a query OFICIAL (`contracts/totvs-wsgetdata-epi-precos.sql`) — o último
`C7_PRECO` por produto × centro de custo — e faz UPSERT em `CCItemPrice`
por `(codccu, produto_codigo)`, PULANDO as linhas travadas manualmente
(`is_manual_price=True`).

Garantias:
  * Isolamento por CCU: a trava é por linha (codccu, produto). Manual no CCU A
    não impede a atualização automática do mesmo produto no CCU B.
  * Falha segura: se o TOTVS estiver indisponível/erro, a rotina LOGA o erro e
    NÃO zera/apaga preços existentes (retorna com `ok=False`).
  * `last_auto_sync` é gravado nas linhas automáticas atualizadas.

O `SELECT` é fixo aqui (constante do módulo), nunca vem de input do usuário.
"""
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.cc_item_price import CCItemPrice
from app.services import totvs_connector

logger = logging.getLogger(__name__)

# Tamanho canônico das linhas de EPI vindas do TOTVS: sem variante ("").
# O valor de EPI é por (produto, CCU); a fonte não distingue tamanho.
_EPI_TAMANHO = ""

# Consulta OFICIAL validada em produção (mesmo texto do arquivo .sql do contrato).
# Fixa no código — NUNCA montada a partir de entrada do usuário.
EPI_PRECOS_SQL = """SELECT COD, CC, DESCR, PRECO, EMISSAO FROM (
    SELECT
        LTRIM(RTRIM(C7_PRODUTO)) AS COD,
        LTRIM(RTRIM(C7_CC))      AS CC,
        LTRIM(RTRIM(C7_DESCRI))  AS DESCR,
        C7_PRECO                 AS PRECO,
        C7_EMISSAO               AS EMISSAO,
        ROW_NUMBER() OVER (PARTITION BY C7_PRODUTO, C7_CC
                           ORDER BY C7_EMISSAO DESC, SC7.R_E_C_N_O_ DESC) AS RN
    FROM SC7010 SC7
    JOIN SB1010 SB1 ON B1_COD = C7_PRODUTO AND SB1.D_E_L_E_T_ <> '*'
         AND LTRIM(RTRIM(B1_GRUPO)) = '0003'
    WHERE SC7.D_E_L_E_T_ <> '*' AND C7_PRECO > 0
) t
WHERE RN = 1"""


def _parse_valor(raw: Any) -> Optional[float]:
    """Converte o PRECO do TOTVS para float. Aceita número ou string
    (com vírgula ou ponto decimal). Retorna None se não parsear ou <= 0."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        v = float(raw)
    else:
        s = str(raw).strip()
        if not s:
            return None
        # normaliza "1.234,56" e "45,61" -> ponto decimal
        if "," in s and "." in s:
            s = s.replace(".", "").replace(",", ".")
        elif "," in s:
            s = s.replace(",", ".")
        try:
            v = float(s)
        except ValueError:
            return None
    return v if v > 0 else None


def _upsert_cc_price(db: Session, codccu: str, produto_codigo: str,
                     valor: float, now: datetime) -> str:
    """Upsert de uma linha automática de CCItemPrice.

    Retorna: 'skipped_manual' | 'inserted' | 'updated' | 'unchanged'.
    Respeita a trava manual — se a linha existir com is_manual_price=True, NÃO
    altera o valor (isolamento por CCU garantido: só esta linha é pulada).
    """
    row = (
        db.query(CCItemPrice)
        .filter(
            CCItemPrice.codccu == str(codccu),
            CCItemPrice.produto_codigo == str(produto_codigo),
            CCItemPrice.tamanho == _EPI_TAMANHO,
        )
        .first()
    )
    if row is None:
        db.add(CCItemPrice(
            codccu=str(codccu),
            produto_codigo=str(produto_codigo),
            tamanho=_EPI_TAMANHO,
            valor=float(valor),
            is_manual_price=False,
            last_auto_sync=now,
        ))
        return "inserted"

    if row.is_manual_price:
        # Trava manual: NÃO sobrescreve o valor. (Não mexe em last_auto_sync
        # para não sugerir que o automático tocou o valor manual.)
        return "skipped_manual"

    changed = float(row.valor or 0.0) != float(valor)
    row.valor = float(valor)
    row.last_auto_sync = now
    return "updated" if changed else "unchanged"


def run_epi_price_autosync(db: Optional[Session] = None) -> Dict[str, Any]:
    """Executa uma rodada de sincronização de preços de EPI do TOTVS.

    - Se `db` não for passado, abre uma sessão própria (uso pelo scheduler).
    - Falha de TOTVS => `{"ok": False, "erro": ...}` e NADA é apagado.
    - Sucesso => contadores por resultado + `last_auto_sync`.
    """
    own_session = db is None
    if own_session:
        db = SessionLocal()

    now = datetime.utcnow()
    result: Dict[str, Any] = {
        "ok": False, "inserted": 0, "updated": 0, "unchanged": 0,
        "skipped_manual": 0, "sem_valor": 0, "registros": 0, "erro": None,
    }
    try:
        registros = totvs_connector.run_query(EPI_PRECOS_SQL)
        result["registros"] = len(registros)
        for reg in registros:
            cod = (reg.get("COD") or reg.get("cod") or "").strip() if isinstance(reg, dict) else ""
            cc = (reg.get("CC") or reg.get("cc") or "").strip() if isinstance(reg, dict) else ""
            valor = _parse_valor(reg.get("PRECO") if isinstance(reg, dict) else None)
            if not cod or not cc or valor is None:
                result["sem_valor"] += 1
                continue
            outcome = _upsert_cc_price(db, cc, cod, valor, now)
            result[outcome] = result.get(outcome, 0) + 1
        db.commit()
        result["ok"] = True
        logger.info(
            "price_autosync EPI: %s registros (novos=%s atualizados=%s "
            "inalterados=%s travados=%s sem_valor=%s)",
            result["registros"], result["inserted"], result["updated"],
            result["unchanged"], result["skipped_manual"], result["sem_valor"],
        )
    except Exception as exc:
        # Falha segura: registra o erro e NÃO destrói preços existentes.
        db.rollback()
        result["ok"] = False
        result["erro"] = str(exc)
        logger.error("price_autosync EPI falhou (preços preservados): %s", exc)
    finally:
        if own_session:
            db.close()
    return result


def resync_one_cc_item(db: Session, codccu: str, produto_codigo: str) -> Dict[str, Any]:
    """Ressincroniza UMA linha (codccu, produto) do TOTVS — usado no "voltar ao
    automático". Consulta a fonte e aplica o último preço àquela linha,
    marcando-a como automática. A trava já deve ter sido removida pelo chamador.

    Falha de TOTVS não destrói o valor atual (retorna ok=False).
    """
    now = datetime.utcnow()
    out: Dict[str, Any] = {"ok": False, "aplicado": False, "valor": None, "erro": None}
    try:
        registros = totvs_connector.run_query(EPI_PRECOS_SQL)
    except Exception as exc:
        out["erro"] = str(exc)
        logger.warning("resync_one_cc_item falhou p/ %s/%s: %s", codccu, produto_codigo, exc)
        return out

    alvo = None
    for reg in registros:
        if not isinstance(reg, dict):
            continue
        cod = (reg.get("COD") or reg.get("cod") or "").strip()
        cc = (reg.get("CC") or reg.get("cc") or "").strip()
        if cod == str(produto_codigo).strip() and cc == str(codccu).strip():
            alvo = reg
            break

    out["ok"] = True
    if alvo is None:
        # Sem pedido de compra p/ esse (produto, CCU): fica sem valor automático.
        return out

    valor = _parse_valor(alvo.get("PRECO"))
    if valor is None:
        return out

    outcome = _upsert_cc_price(db, str(codccu), str(produto_codigo), valor, now)
    db.commit()
    out["aplicado"] = outcome in ("inserted", "updated", "unchanged")
    out["valor"] = valor
    return out
