import re
import unicodedata
import requests
import json
import logging
from typing import List, Dict, Any, Optional, Union, Set
from lxml import etree
from app.config import (
    SENIOR_API_DOMAIN, SENIOR_API_KEY, MSSQL_DB,
    SENIOR_SOAP_URL, SENIOR_SOAP_NEXTI_URL, SENIOR_SOAP_USER,
    SENIOR_SOAP_PASSWORD, SENIOR_SOAP_TOKEN, SENIOR_SOAP_ENCRYPTION,
    DEV_MODE, SENIOR_SOAP_DELAY_BETWEEN_CCUS_MS,
    SENIOR_SOAP_DELAY_THRESHOLD_CCUS,
)
from app.services.billing_processor import REMUNERACAO_BASE_COLUMNS, ENCARGOS_SOCIAIS_RATE

logger = logging.getLogger(__name__)

# Mapeia billing_payroll_item_types.code → código de evento Senior usado no FEMSA
# (alinhado a EVENT_TO_FEMSA_MAPPING em excel_export; 93xx = reservado para DEV/local)
_DEV_PAYROLL_TYPE_TO_SENIOR_COD: Dict[str, int] = {
    "SALARIO_DIA": 200,
    "HORA_EXTRA": 257,
    "VALE_TRANSPORTE": 9330,
    "VALE_REFEICAO": 3031,
    "PREMIO_BONUS": 9331,
    "TRIBUTO_VALOR": 9332,
    "ENCARGO_VALOR": 9333,
    "TAXA_FATURAMENTO": 9334,
    "EXAME_MEDICO": 9335,
}


def _dev_senior_event_code_for_type(itype: Any) -> Optional[int]:
    if itype is None:
        return None
    code = getattr(itype, "code", None)
    if code and code in _DEV_PAYROLL_TYPE_TO_SENIOR_COD:
        return _DEV_PAYROLL_TYPE_TO_SENIOR_COD[code]
    logger.warning(
        "[DEV_MODE] PayrollItemType sem mapeamento Senior: code=%s id=%s — "
        "amplie _DEV_PAYROLL_TYPE_TO_SENIOR_COD se precisar do evento no FEMSA",
        code,
        getattr(itype, "id", None),
    )
    return None


def _dev_tipo_evento_for_type(itype: Any) -> int:
    """Convenção Senior: 1/2 = provento, 3 = desconto (FEMSA soma totais com tipeve 1,2 vs 3)."""
    if itype is None:
        return 1
    direction = getattr(itype, "direction", None)
    if direction is None:
        return 1
    name = getattr(direction, "name", None) or str(direction)
    if name == "DEBIT":
        return 3
    return 1


# ---------------------------------------------------------------------------
# Helpers para cálculo de Total Remuneração e Encargos Sociais por funcionário
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    """Uppercase, remove acentos, normaliza espaços e strip de sufixos comuns."""
    nfkd = unicodedata.normalize("NFKD", str(s).upper().strip())
    sem_acento = "".join(c for c in nfkd if not unicodedata.combining(c))
    resultado = " ".join(sem_acento.split())
    for sufixo in (" (VALOR)", " (QTDE)", " (QTD)", " (HS)"):
        if resultado.endswith(sufixo):
            resultado = resultado[: -len(sufixo)].strip()
    return resultado


# Set de descrições normalizadas que identificam eventos de remuneração.
# Construído a partir de REMUNERACAO_BASE_COLUMNS — sem sufixos de valor.
_REMUNERACAO_DESC: frozenset = frozenset(_norm(c) for c in REMUNERACAO_BASE_COLUMNS)


def _remuneracao_event_codes() -> frozenset:
    """
    Retorna o conjunto de códigos de evento Senior que mapeiam para uma
    coluna de remuneração.  Importação lazy para evitar circular import.
    """
    try:
        from app.services.excel_export import EVENT_TO_FEMSA_MAPPING  # noqa: PLC0415
        cols_rem = frozenset(REMUNERACAO_BASE_COLUMNS)
        return frozenset(
            cod
            for cod, mapping in EVENT_TO_FEMSA_MAPPING.items()
            if mapping is not None and mapping[1] in cols_rem
        )
    except Exception:
        return frozenset()


_CACHED_REM_CODES: Optional[frozenset] = None


def _get_rem_codes() -> frozenset:
    global _CACHED_REM_CODES
    if _CACHED_REM_CODES is None:
        _CACHED_REM_CODES = _remuneracao_event_codes()
    return _CACHED_REM_CODES


def _contribui_remuneracao(evento: Dict[str, Any]) -> bool:
    """
    Retorna True se o evento deve ser somado no Total Remuneração.

    Prioridade:
    1. Código de evento encontrado no mapeamento Senior → EVENT_TO_FEMSA_MAPPING
    2. Fallback por descrição normalizada → _REMUNERACAO_DESC
    """
    cod = evento.get("codigo_evento")
    if cod is not None and cod != "":
        try:
            if int(cod) in _get_rem_codes():
                return True
        except (ValueError, TypeError):
            pass

    desc_norm = _norm(evento.get("descricao_evento") or "")
    return desc_norm in _REMUNERACAO_DESC


def enriquecer_com_totais_remuneracao(
    funcionarios: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Adiciona ``total_remuneracao`` e ``encargos_sociais`` a cada dict de
    funcionário, somando apenas os eventos que compõem a remuneração base.

    Regras:
    - Valores null / ausentes são tratados como 0.
    - O mesmo código de evento não é somado duas vezes por funcionário.
    - ``encargos_sociais = total_remuneracao * ENCARGOS_SOCIAIS_RATE`` (57,91 %).
    """
    for emp in funcionarios:
        total = 0.0
        codigos_vistos: set = set()

        for ev in emp.get("eventos", []):
            cod = ev.get("codigo_evento")
            # Evita dupla contagem do mesmo código dentro do mesmo funcionário
            cod_key = cod if (cod is not None and cod != "") else None
            if cod_key is not None and cod_key in codigos_vistos:
                continue

            if _contribui_remuneracao(ev):
                try:
                    total += float(ev.get("valor_evento") or 0)
                except (ValueError, TypeError):
                    pass
                if cod_key is not None:
                    codigos_vistos.add(cod_key)

        emp["total_remuneracao"] = round(total, 2)
        emp["encargos_sociais"] = round(total * ENCARGOS_SOCIAIS_RATE, 2)

    return funcionarios

TELOS_NUMEMP = 6

SOAP_NAMESPACE = "http://services.senior.com.br"


def _build_soap_envelope(
    dat_ini: str,
    dat_fim: str,
    num_emp: str = "6",
    cod_ccu_list: Optional[List[str]] = None,
) -> str:
    codccu_xml = ""
    if cod_ccu_list:
        for ccu in cod_ccu_list:
            codccu_xml += f"<codCcu>{ccu.strip()}</codCcu>\n"

    envelope = f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:ser="http://services.senior.com.br">
<soapenv:Body>
<ser:consultaRegistros>
<user>{SENIOR_SOAP_USER}</user>
<password>{SENIOR_SOAP_PASSWORD}</password>
<encryption>{SENIOR_SOAP_ENCRYPTION}</encryption>
<parameters>
<token>{SENIOR_SOAP_TOKEN}</token>
<datIni>{dat_ini}</datIni>
<datFim>{dat_fim}</datFim>
<numEmp>{num_emp}</numEmp>
{codccu_xml}</parameters>
</ser:consultaRegistros>
</soapenv:Body>
</soapenv:Envelope>"""
    return envelope


def _parse_soap_registros(xml_bytes: bytes) -> List[Dict[str, Any]]:
    root = etree.fromstring(xml_bytes)

    ns = {"soapenv": "http://schemas.xmlsoap.org/soap/envelope/", "ser": SOAP_NAMESPACE}
    registros = root.xpath("//registros") or root.xpath("//ser:registros", namespaces=ns)

    erro_nodes = root.xpath("//erroExecucao") or root.xpath("//ser:erroExecucao", namespaces=ns)
    if erro_nodes:
        erro_text = erro_nodes[0].text
        if erro_text and erro_text.strip():
            if _is_credential_error(erro_text):
                _trip_auth_block(erro_text)
            raise Exception(f"Erro na execução SOAP Senior: {erro_text.strip()}")

    results: List[Dict[str, Any]] = []
    for reg in registros:
        row: Dict[str, Any] = {}
        for child in reg:
            tag = etree.QName(child.tag).localname if "}" in child.tag else child.tag
            row[tag] = child.text
        results.append(row)

    return results


def _normalize_codccu_param(codccu: Optional[Union[str, List[str]]]) -> Optional[List[str]]:
    if not codccu:
        return None
    if isinstance(codccu, list):
        return [c.strip() for c in codccu if c and c.strip()]
    return [codccu.strip()] if codccu.strip() else None


# ── Circuit-breaker de credencial ────────────────────────────────────────────
# Quando a Senior recusa por credencial inválida/expirada/desabilitada (ou
# bloqueia "por recorrência"), suspendemos as chamadas SOAP por um tempo para
# NÃO reenviar logins falhos e reforçar o bloqueio do F5. Reseta ao reiniciar.
import time as _time_cb

_AUTH_BLOCK = {"until": 0.0, "msg": ""}
_AUTH_BLOCK_COOLDOWN_S = 600  # 10 min

_CRED_ERROR_MARKERS = (
    "credenciais inválidas", "credenciais invalidas",
    "credencial inválida", "credencial invalida",
    "desabilitad", "expirad",
    "recorrência do erro", "recorrencia do erro",
    "bloqueada devido", "invalid credentials", "unauthorized", "não autorizad",
)


def _is_credential_error(msg) -> bool:
    """True se a mensagem indica credencial inválida/desabilitada/expirada ou
    bloqueio por recorrência (erro de autenticação da Senior)."""
    if not msg:
        return False
    m = str(msg).lower()
    return any(k in m for k in _CRED_ERROR_MARKERS)


def _trip_auth_block(msg: str) -> None:
    """Ativa o circuit-breaker: suspende chamadas SOAP por _AUTH_BLOCK_COOLDOWN_S."""
    _AUTH_BLOCK["until"] = _time_cb.time() + _AUTH_BLOCK_COOLDOWN_S
    _AUTH_BLOCK["msg"] = str(msg)[:300]
    logger.error(
        "Circuit-breaker de credencial Senior ATIVADO por %ds (parando de reenviar logins). Motivo: %s",
        _AUTH_BLOCK_COOLDOWN_S, _AUTH_BLOCK["msg"],
    )


def _auth_block_reason() -> Optional[str]:
    """Se o circuit-breaker está ativo, retorna a mensagem a propagar; senão None."""
    restante = _AUTH_BLOCK["until"] - _time_cb.time()
    if restante > 0:
        return (
            f"Chamadas à Senior suspensas por ~{int(restante)}s para não reforçar o bloqueio "
            f"da conta (erro de credencial detectado). Renove as credenciais no .env e reinicie "
            f"o servidor. Detalhe: {_AUTH_BLOCK['msg']}"
        )
    return None


def _post_soap_with_retry(
    url: str,
    envelope: str,
    headers: Dict[str, str],
    label: str = "SOAP",
    timeout: int = 120,
    max_attempts: int = 1,  # mantido na assinatura por compat; retry desativado
) -> requests.Response:
    """
    POST SOAP — sem retry (desativado intencionalmente).

    Razão: retries automáticos sob 503/timeout podem viver em loop quando o F5
    ASM ou a Senior estão estressados, agravando o problema. Decisão: falhar
    rápido e deixar o usuário decidir clicar de novo.

    Feature 003: limita chamadas SOAP concorrentes via `_SOAP_SEMAPHORE`
    (default 3). Aguardo > 100ms é logado para depurar gargalos.

    Em caso de 503, ainda extrai o support ID do F5 e loga para abertura de chamado.
    """
    # Circuit-breaker: se um erro de credencial foi detectado recentemente, falha
    # rápido SEM tocar na Senior (evita reenviar logins falhos e reforçar o bloqueio).
    _blk = _auth_block_reason()
    if _blk:
        raise Exception(_blk)
    import re as _re
    import time as _time
    from app.services.senior_cache import _SOAP_SEMAPHORE
    from app.services import monitor_buffer as _mon

    t0 = _time.time()
    _SOAP_SEMAPHORE.acquire()
    wait_ms = (_time.time() - t0) * 1000
    if wait_ms > 100:
        logger.info("%s wait_ms=%.0f (semáforo SOAP)", label, wait_ms)
    t_req = _time.time()
    _mon.record_start(label, "iniciando POST")
    try:
        try:
            resp = requests.post(url, data=envelope.encode("utf-8"), headers=headers, timeout=timeout, verify=True)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            elapsed = _time.time() - t_req
            logger.error("%s falha de rede: %s", label, e)
            _mon.record_error(label, str(e), elapsed_s=elapsed)
            raise
    finally:
        _SOAP_SEMAPHORE.release()

    elapsed = _time.time() - t_req

    if resp.status_code == 200:
        _mon.record_end(label, f"200 OK ({len(resp.content)} bytes)", elapsed_s=elapsed, ok=True)
        return resp

    if resp.status_code == 503:
        support_id = ""
        m = _re.search(r"support ID is ([a-f0-9-]+)", resp.text or "", _re.IGNORECASE)
        if m:
            support_id = m.group(1)
        logger.error("%s: F5 503 (support=%s) body=%s", label, support_id, resp.text[:500])
        _mon.record_error(label, f"503 F5 bloqueando (support={support_id or '—'})", elapsed_s=elapsed)
        raise Exception(f"Senior F5 bloqueando (HTTP 503). Tente novamente em alguns minutos. Support ID: {support_id}")

    logger.error("%s HTTP %s: %s", label, resp.status_code, resp.text[:500])
    _mon.record_error(label, f"HTTP {resp.status_code}: {resp.text[:200]}", elapsed_s=elapsed)
    raise Exception(f"Erro HTTP {resp.status_code} na chamada {label}: {resp.text[:300]}")


def _call_soap_consulta_single(
    dat_ini: str,
    dat_fim: str,
    num_emp: str = "6",
    cod_ccu_list: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    if DEV_MODE:
        logger.warning(
            "[DEV_MODE] Credenciais Senior não configuradas. "
            "Retornando lista vazia para consultaRegistros (datIni=%s datFim=%s numEmp=%s codCcu=%s). "
            "Configure SENIOR_SOAP_USER e SENIOR_SOAP_PASSWORD no .env para usar dados reais.",
            dat_ini, dat_fim, num_emp, cod_ccu_list,
        )
        return []
    if not SENIOR_SOAP_USER or not SENIOR_SOAP_PASSWORD:
        raise Exception("Credenciais SOAP Senior não configuradas (SENIOR_SOAP_USER / SENIOR_SOAP_PASSWORD)")

    soap_url = SENIOR_SOAP_URL
    if soap_url.endswith("?wsdl"):
        soap_url = soap_url.replace("?wsdl", "")

    envelope = _build_soap_envelope(dat_ini, dat_fim, num_emp, cod_ccu_list)

    headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": "",
    }

    logger.info("SOAP Senior request: url=%s datIni=%s datFim=%s numEmp=%s codCcu=%s",
                soap_url, dat_ini, dat_fim, num_emp, cod_ccu_list)

    response = _post_soap_with_retry(soap_url, envelope, headers, label="consultaRegistros")

    registros = _parse_soap_registros(response.content)
    logger.info("SOAP Senior retornou %d registros", len(registros))
    return registros


def _call_soap_consulta(
    dat_ini: str,
    dat_fim: str,
    num_emp: str = "6",
    cod_ccu_list: Optional[List[str]] = None,
    progress_cb=None,
) -> List[Dict[str, Any]]:
    def _report(done, total):
        if progress_cb:
            try:
                progress_cb(done, total)
            except Exception:
                pass

    if not cod_ccu_list or len(cod_ccu_list) <= 1:
        r = _call_soap_consulta_single(dat_ini, dat_fim, num_emp, cod_ccu_list)
        _report(1, 1)
        return r

    import time as _time
    # Delay só entra em ação quando passa do threshold (default 10 CCUs).
    n = len(cod_ccu_list)
    if n > SENIOR_SOAP_DELAY_THRESHOLD_CCUS:
        delay_s = SENIOR_SOAP_DELAY_BETWEEN_CCUS_MS / 1000.0
    else:
        delay_s = 0.0

    def _run_pass(label: str, ccus: List[str], report: bool = False) -> tuple:
        """Itera ccus, retorna (registros_acumulados, lista_falhos). Log INFO por
        CCU (START + OK/FAIL com tempo) pra dar visibilidade real do progresso.
        Com report=True, chama progress_cb(i+1, n) a cada CCU processado."""
        regs: List[Dict[str, Any]] = []
        falhos: List[str] = []
        total = len(ccus)
        for i, ccu in enumerate(ccus):
            if i > 0 and delay_s > 0:
                _time.sleep(delay_s)
            t0 = _time.perf_counter()
            logger.info("[%s] CCU %d/%d -> %s START", label, i + 1, total, ccu)
            try:
                r = _call_soap_consulta_single(dat_ini, dat_fim, num_emp, [ccu])
                regs.extend(r)
                elapsed = _time.perf_counter() - t0
                logger.info(
                    "[%s] CCU %d/%d -> %s OK %.2fs (+%d registros, acum=%d)",
                    label, i + 1, total, ccu, elapsed, len(r), len(regs),
                )
            except Exception as e:
                elapsed = _time.perf_counter() - t0
                logger.warning(
                    "[%s] CCU %d/%d -> %s FAIL %.2fs: %s",
                    label, i + 1, total, ccu, elapsed, str(e)[:200],
                )
                if _is_credential_error(str(e)):
                    # Erro de credencial: aborta TUDO (não tenta os demais CCUs
                    # nem o passe de retry) para não reenviar N logins falhos.
                    raise
                falhos.append(ccu)
            finally:
                if report:
                    _report(i + 1, n)
        return regs, falhos

    all_registros: List[Dict[str, Any]] = []
    logger.info(
        "Buscando dados para %d centros de custo individualmente (delay=%.2fs entre chamadas%s)",
        n, delay_s, "" if delay_s > 0 else f" — abaixo do threshold de {SENIOR_SOAP_DELAY_THRESHOLD_CCUS}",
    )

    # PASS 1: todos os CCUs sequencialmente
    regs1, falhos = _run_pass("pass1", list(cod_ccu_list), report=True)
    all_registros.extend(regs1)

    # PASS 2 (RETRY): só os que falharam no pass1
    if falhos:
        logger.info(
            "[retry] %d CCUs falharam no pass1 — retentando ao final: %s",
            len(falhos), falhos[:20] + (["..."] if len(falhos) > 20 else []),
        )
        regs2, falhos_final = _run_pass("retry", list(falhos))
        all_registros.extend(regs2)
    else:
        falhos_final = []

    if falhos_final:
        logger.error(
            "Falha definitiva ao buscar %d de %d CCUs (mesmo após retry): %s",
            len(falhos_final), n, falhos_final,
        )
    if not all_registros and falhos_final:
        raise Exception(f"Falha ao buscar todos os centros de custo: {falhos_final}")
    logger.info(
        "Total de registros após buscar todos os CCUs: %d (falhas definitivas: %d/%d)",
        len(all_registros), len(falhos_final), n,
    )
    return all_registros


def _safe_float(val: Any) -> float:
    if val is None:
        return 0.0
    try:
        s = str(val).strip()
        if "." in s and "," in s:
            s = s.replace(".", "").replace(",", ".")
        elif "," in s:
            s = s.replace(",", ".")
        elif re.match(r"^\d{1,3}(\.\d{3})+$", s):
            s = s.replace(".", "")
        return float(s)
    except (ValueError, TypeError):
        return 0.0


def _safe_int(val: Any) -> Optional[int]:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _impute_salario_across_lancamentos(rows: List[Dict[str, Any]]) -> None:
    """
    Por matrícula: replica o maior ``salario`` (valSal) entre lançamentos; se tudo 0,
    usa o valor do evento 200 (Salário Dia) como aproximação de base, como no Senior.
    """
    by_m: Dict[Any, List[Dict[str, Any]]] = {}
    for r in rows:
        m = r.get("matricula")
        by_m.setdefault(m, []).append(r)
    for _m, rlist in by_m.items():
        if not rlist:
            continue
        best = max(_safe_float(x.get("salario")) for x in rlist)
        if best > 0:
            for x in rlist:
                x["salario"] = best
            continue
        fallback = 0.0
        for x in rlist:
            if _safe_int(x.get("codigo_evento")) == 200:
                v = _safe_float(x.get("valor_evento"))
                if v > fallback:
                    fallback = v
        if fallback > 0:
            for x in rlist:
                x["salario"] = fallback


def _dev_salario_por_employee_id(
    items: List[Any], db: Any
) -> Dict[int, float]:
    """
    DEV: base salarial preferindo EmploymentContract.salario_base; se zero/ausente,
    usa o valor (ou ref.) do lançamento SALARIO_DIA do período.
    """
    from app.models.billing import EmploymentContract

    out: Dict[int, float] = {}
    for item in items:
        eid = item.employee_id
        if not eid:
            continue
        c = item.contract
        if c is None:
            c = (
                db.query(EmploymentContract)
                .filter(EmploymentContract.employee_id == eid)
                .order_by(EmploymentContract.id.desc())
                .first()
            )
        if c is not None and c.salario_base is not None and float(c.salario_base) > 0:
            out[eid] = max(out.get(eid, 0.0), float(c.salario_base))
    for item in items:
        eid = item.employee_id
        it = item.payroll_item_type
        if not eid or not it or getattr(it, "code", None) != "SALARIO_DIA":
            continue
        a = _safe_float(item.amount)
        v = a if a else _safe_float(item.quantity)
        if v <= 0:
            continue
        if out.get(eid, 0.0) <= 0:
            out[eid] = max(out.get(eid, 0.0), v)
    return out


def _sanitize_codccu(value: str) -> str:
    clean = value.strip().replace("'", "").replace(";", "").replace("--", "")
    return clean


def _build_codccu_filter(codccu: Optional[Union[str, List[str]]]) -> str:
    if not codccu:
        return ""
    if isinstance(codccu, list):
        codes = [_sanitize_codccu(c) for c in codccu if c and c.strip()]
        if not codes:
            return ""
        if len(codes) == 1:
            return f"AND R034FUN.CODCCU = '{codes[0]}'"
        quoted = ", ".join(f"'{c}'" for c in codes)
        return f"AND R034FUN.CODCCU IN ({quoted})"
    return f"AND R034FUN.CODCCU = '{_sanitize_codccu(codccu)}'"


def get_api_headers() -> Dict[str, str]:
    return {"x-api-key": SENIOR_API_KEY, "Content-Type": "application/json"}


def get_connection_info() -> Dict[str, Any]:
    return {
        "api_domain": SENIOR_API_DOMAIN,
        "api_key_configured": bool(SENIOR_API_KEY),
        "database": MSSQL_DB,
        "numemp_telos": TELOS_NUMEMP,
        "soap_url": SENIOR_SOAP_URL,
        "soap_user_configured": bool(SENIOR_SOAP_USER),
        "soap_token_configured": bool(SENIOR_SOAP_TOKEN),
    }


def test_connection() -> Dict[str, Any]:
    if not SENIOR_SOAP_USER:
        return {"status": "error", "message": "SENIOR_SOAP_USER não configurado"}

    from app.services import monitor_buffer as _mon
    import time as _time
    try:
        wsdl_url = SENIOR_SOAP_URL if SENIOR_SOAP_URL.endswith("?wsdl") else SENIOR_SOAP_URL + "?wsdl"
        _t0 = _time.time()
        _mon.record_start("WSDL", "GET healthcheck")
        try:
            response = requests.get(wsdl_url, timeout=15, verify=True)
        except Exception as _e:
            _mon.record_error("WSDL", str(_e), elapsed_s=_time.time() - _t0)
            raise
        _elapsed = _time.time() - _t0
        if response.status_code == 200:
            _mon.record_end("WSDL", "200 OK", elapsed_s=_elapsed, ok=True)
            return {
                "status": "ok",
                "message": "WSDL Senior acessível",
                "soap_url": SENIOR_SOAP_URL,
            }
        else:
            _mon.record_error("WSDL", f"HTTP {response.status_code}", elapsed_s=_elapsed)
            return {
                "status": "error",
                "message": f"HTTP {response.status_code} ao acessar WSDL",
            }
    except requests.exceptions.ConnectionError as e:
        return {"status": "error", "message": f"Erro de conexão: {str(e)}"}
    except requests.exceptions.Timeout:
        return {"status": "error", "message": "Timeout ao conectar"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def execute_query(sql_text: str) -> Dict[str, Any]:
    if not SENIOR_API_DOMAIN:
        return {"status": "error", "message": "DOMAIN_API não configurado"}

    if not SENIOR_API_KEY:
        return {"status": "error", "message": "API_KEY não configurado"}

    try:
        url = f"{SENIOR_API_DOMAIN.rstrip('/')}/query"
        payload = {"sqlText": sql_text}

        response = requests.post(
            url,
            json=payload,
            headers=get_api_headers(),
            timeout=60,
        )

        if response.status_code == 200:
            return {"status": "ok", "data": response.json()}
        elif response.status_code == 401:
            return {"status": "error", "message": "API Key inválida ou não autorizada"}
        elif response.status_code == 400:
            return {"status": "error", "message": f"Query inválida: {response.text}"}
        else:
            return {"status": "error", "message": f"HTTP {response.status_code}: {response.text}"}
    except requests.exceptions.ConnectionError as e:
        return {"status": "error", "message": f"Erro de conexão: {str(e)}"}
    except requests.exceptions.Timeout:
        return {"status": "error", "message": "Timeout ao executar query"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def list_tables() -> List[Dict[str, str]]:
    if not SENIOR_API_DOMAIN:
        raise Exception("DOMAIN_API não configurado")
    try:
        url = f"{SENIOR_API_DOMAIN.rstrip('/')}/tables"
        response = requests.get(url, headers=get_api_headers(), timeout=30)
        if response.status_code == 200:
            return response.json()
        else:
            raise Exception(f"HTTP {response.status_code}: {response.text}")
    except requests.exceptions.RequestException as e:
        raise Exception(f"Erro ao listar tabelas: {str(e)}")


def _build_soap_t018ccu_envelope(numemp: int = 6) -> str:
    envelope = f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:ser="http://services.senior.com.br">
<soapenv:Header/>
<soapenv:Body>
<ser:T018CCU>
<user>{SENIOR_SOAP_USER}</user>
<password>{SENIOR_SOAP_PASSWORD}</password>
<encryption>{SENIOR_SOAP_ENCRYPTION}</encryption>
<parameters>
<numEmp>{numemp}</numEmp>
<token>{SENIOR_SOAP_TOKEN}</token>
</parameters>
</ser:T018CCU>
</soapenv:Body>
</soapenv:Envelope>"""
    return envelope


def _fetch_cost_centers_local() -> List[Dict[str, Any]]:
    """DEV_MODE: deriva os centros de custo das unidades cadastradas no banco local
    (Unit.centro_custo_femsa / nome_unidade). Espelha a MESMA fonte que
    _fetch_payroll_local usa como codccu/nomccu, então os CCs listados aqui têm
    funcionários correspondentes ao selecionar."""
    from app.db import SessionLocal
    from app.models.billing import Unit
    db = SessionLocal()
    try:
        seen: Dict[str, str] = {}
        for u in db.query(Unit).all():
            cod = (u.centro_custo_femsa or "").strip()
            if not cod or cod in seen:
                continue
            seen[cod] = (u.nome_unidade or cod).strip()
        return [{"codccu": c, "nomccu": n} for c, n in seen.items()]
    finally:
        db.close()


def _call_soap_cost_centers(numemp: int = 6) -> List[Dict[str, Any]]:
    if DEV_MODE:
        local = _fetch_cost_centers_local()
        logger.info(
            "[DEV_MODE] T018CCU: retornando %d centros de custo do banco local "
            "(numemp=%s). Popule via seed_test_data.py ou upload de folha.",
            len(local), numemp,
        )
        return local
    if not SENIOR_SOAP_USER or not SENIOR_SOAP_PASSWORD:
        raise Exception("Credenciais SOAP Senior não configuradas (SENIOR_SOAP_USER / SENIOR_SOAP_PASSWORD)")

    soap_url = SENIOR_SOAP_NEXTI_URL
    if soap_url.endswith("?wsdl"):
        soap_url = soap_url.replace("?wsdl", "")

    envelope = _build_soap_t018ccu_envelope(numemp)

    headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": "",
    }

    logger.info("SOAP Senior T018CCU request: url=%s numEmp=%s", soap_url, numemp)

    from app.services import monitor_buffer as _mon
    import time as _time
    _t0 = _time.time()
    _mon.record_start("T018CCU", f"numEmp={numemp}")
    try:
        # timeout=120: a Senior tem respondido T018CCU perto de ~55-60s; 60s falhava
        # na borda. 120s dá folga (o resultado é cacheado por 6h depois da 1ª carga).
        response = requests.post(soap_url, data=envelope.encode("utf-8"), headers=headers, timeout=120, verify=True)
    except Exception as _e:
        _mon.record_error("T018CCU", str(_e), elapsed_s=_time.time() - _t0)
        raise
    _elapsed = _time.time() - _t0

    if response.status_code != 200:
        logger.error("SOAP Senior T018CCU HTTP %s: %s", response.status_code, response.text[:500])
        _mon.record_error("T018CCU", f"HTTP {response.status_code}: {response.text[:200]}", elapsed_s=_elapsed)
        raise Exception(f"Erro HTTP {response.status_code} na chamada SOAP T018CCU: {response.text[:300]}")
    _mon.record_end("T018CCU", f"200 OK ({len(response.content)} bytes)", elapsed_s=_elapsed, ok=True)

    root = etree.fromstring(response.content)

    erro_nodes = root.xpath("//*[local-name()='erroExecucao']")
    if erro_nodes:
        erro_text = erro_nodes[0].text
        if erro_text and erro_text.strip():
            if _is_credential_error(erro_text):
                _trip_auth_block(erro_text)
            raise Exception(f"Erro na execução SOAP T018CCU: {erro_text.strip()}")

    ccu_nodes = root.xpath("//*[local-name()='centrosCustos']")

    centers: List[Dict[str, Any]] = []
    for node in ccu_nodes:
        cod_el = node.find("{http://services.senior.com.br}codCcu")
        if cod_el is None:
            cod_el = node.find("codCcu")
        nom_el = node.find("{http://services.senior.com.br}nomCcu")
        if nom_el is None:
            nom_el = node.find("nomCcu")

        codccu = cod_el.text if cod_el is not None and cod_el.text else ""
        nomccu = nom_el.text if nom_el is not None and nom_el.text else ""

        if codccu:
            centers.append({"codccu": codccu, "nomccu": nomccu})

    logger.info("SOAP Senior T018CCU retornou %d centros de custo", len(centers))
    return centers


def fetch_cost_centers(numemp: int = 6) -> List[Dict[str, Any]]:
    """Lista de CCUs com cache (feature 003). Key = numemp."""
    from app.services.senior_cache import ccu_cache

    cached = ccu_cache.get(numemp)
    if cached is not None:
        return cached

    centers = _call_soap_cost_centers(numemp)
    centers.sort(key=lambda c: c.get("codccu", ""))
    ccu_cache.set(numemp, centers)
    return centers


def fetch_all_cost_centers() -> List[Dict[str, Any]]:
    """Idem fetch_cost_centers, fixo em TELOS_NUMEMP, com cache."""
    from app.services.senior_cache import ccu_cache

    cached = ccu_cache.get(TELOS_NUMEMP)
    if cached is not None:
        return cached

    centers = _call_soap_cost_centers(TELOS_NUMEMP)
    centers.sort(key=lambda c: c.get("codccu", ""))
    ccu_cache.set(TELOS_NUMEMP, centers)
    return centers


def _parse_senior_date(raw: Any) -> Optional["date"]:
    """Aceita 'YYYY-MM-DD', 'DD/MM/YYYY' ou objetos date/datetime. Retorna date ou None."""
    from datetime import date, datetime
    if raw is None or raw == "":
        return None
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    s = str(raw).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    return None


def is_employee_active(emp: Dict[str, Any], today: Optional["date"] = None) -> bool:
    """
    Critério de funcionário ativo para fluxo de compra de EPI (spec 001-epi-purchase-flow, FR-3).
    Ativo = sem data_afastamento, sentinel 31/12/1900, ou data_afastamento estritamente futura.
    Pega data_afastamento de `datafa` (formato Senior REST) ou `data_afastamento` (formato Folha).
    """
    from datetime import date
    today = today or date.today()
    datafa_raw = emp.get("datafa") or emp.get("data_afastamento")
    if not datafa_raw:
        return True
    s = str(datafa_raw)
    # sentinel Senior comum: "31/12/1900" ou "1900-12-31"
    if s.startswith("1900") or s.startswith("31/12/1900") or s.startswith("31-12-1900"):
        return True
    parsed = _parse_senior_date(datafa_raw)
    if parsed is None:
        # data ilegível: tolerância — considera ativo
        return True
    return parsed > today


def fetch_active_employees(codccu: str) -> List[Dict[str, Any]]:
    """
    Retorna funcionários ATIVOS (regra is_employee_active) do centro de custo informado,
    usando SOAP Senior `consultaRegistros` para o mês corrente.

    Em DEV_MODE com app.db populado, cai pra _fetch_payroll_local (mesmo connector já cuida).
    Feature 003: usa employees_cache (TTL configurável, default 1h). Key = (codccu, mês corrente).

    Retorno: lista de dicts no formato esperado pelo front:
    [{numcad, nomfun, codccu, datadm, datafa, cargo, sitafa, dessit, valsal}, ...]
    """
    from datetime import date
    from app.services.senior_cache import employees_cache, current_month_key

    if not codccu:
        return []

    today = date.today()
    periodo = f"{today.year}-{today.month:02d}-01"
    codccu_s = str(codccu).strip()
    cache_key = (codccu_s, current_month_key())

    cached = employees_cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        registros = fetch_payroll(periodo=periodo, codccu=codccu_s)
    except Exception as e:
        # Em vez de propagar (=> erro 500 na tela), loga e segue pro fallback.
        # Útil quando Senior corta conexão (ConnectionResetError 10054), timeout
        # ou 503: o fallback mês anterior pode ter os mesmos funcionários ativos
        # e a UX fica MUITO melhor do que tela quebrada.
        logger.warning("fetch_active_employees: %s falhou (%s) — tentará fallback mês anterior",
                       periodo, e)
        registros = []

    # Fallback: se mês corrente vier vazio (caso típico no começo do mês, antes
    # do Senior processar a folha) ou se a chamada acima falhou por rede,
    # tenta o mês anterior para não devolver tela vazia ao usuário.
    if not registros:
        if today.month == 1:
            prev_year, prev_month = today.year - 1, 12
        else:
            prev_year, prev_month = today.year, today.month - 1
        periodo_prev = f"{prev_year}-{prev_month:02d}-01"
        logger.info(
            "fetch_active_employees: %s vazio para codccu=%s, tentando fallback %s",
            periodo, codccu_s, periodo_prev,
        )
        try:
            registros = fetch_payroll(periodo=periodo_prev, codccu=codccu_s)
        except Exception as e:
            logger.error("fetch_active_employees: fallback %s falhou: %s", periodo_prev, e)
            registros = []

    # dedup por matricula, ficando com o registro mais "rico" (com nome preenchido)
    by_numcad: Dict[Any, Dict[str, Any]] = {}
    for r in registros:
        numcad = r.get("matricula")
        if not numcad:
            continue
        cur = by_numcad.get(numcad)
        if cur is None or (not cur.get("nome_funcionario") and r.get("nome_funcionario")):
            by_numcad[numcad] = r

    out: List[Dict[str, Any]] = []
    for numcad, r in by_numcad.items():
        if not is_employee_active(r, today):
            continue
        out.append({
            "numcad": numcad,
            "nomfun": r.get("nome_funcionario"),
            "cpf": r.get("cpf"),
            "codccu": r.get("codccu"),
            "nomccu": r.get("nomccu"),
            "datadm": r.get("data_admissao"),
            "datafa": r.get("data_afastamento"),
            "sitafa": r.get("sitafa"),
            "dessit": r.get("situacao"),
            "cargo": r.get("cargo"),
            "valsal": r.get("salario"),
        })
    out.sort(key=lambda e: (e.get("nomfun") or "").upper())
    employees_cache.set(cache_key, out)
    return out


def fetch_employee_directory(force: bool = False) -> Dict[str, Dict[str, Any]]:
    """Índice CPF -> {numcad, nome, codccu, nomccu} de TODA a TELOS (varredura da
    folha do mês inteiro, sem filtro de CC). Pesado; cacheado por mês. Fallback p/
    mês anterior se o mês corrente vier vazio."""
    import re as _re
    from datetime import date
    from app.services.senior_cache import employees_cache, current_month_key

    key = ("__directory__", current_month_key())
    if not force:
        cached = employees_cache.get(key)
        if cached is not None:
            return cached

    today = date.today()
    periodo = f"{today.year}-{today.month:02d}-01"
    try:
        registros = fetch_payroll(periodo=periodo, codccu=None)
    except Exception as e:
        logger.warning("fetch_employee_directory: %s falhou (%s) — tentando mês anterior", periodo, e)
        registros = []
    if not registros:
        py, pm = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
        try:
            registros = fetch_payroll(periodo=f"{py}-{pm:02d}-01", codccu=None)
        except Exception as e:
            logger.error("fetch_employee_directory: fallback falhou (%s)", e)
            registros = []

    directory: Dict[str, Dict[str, Any]] = {}
    for r in registros:
        cpf = _re.sub(r"\D", "", str(r.get("cpf") or ""))
        if not cpf:
            continue
        if cpf not in directory:
            directory[cpf] = {
                "numcad": r.get("matricula"),
                "nome": r.get("nome_funcionario"),
                "codccu": r.get("codccu"),
                "nomccu": r.get("nomccu"),
            }
    employees_cache.set(key, directory)
    logger.info("fetch_employee_directory: %d CPFs indexados", len(directory))
    return directory


def peek_employee_directory() -> Dict[str, Dict[str, Any]]:
    """Retorna o diretório CPF→funcionário SOMENTE se já estiver em cache (não
    dispara a varredura pesada). Usado no upload para não bater na Senior."""
    from app.services.senior_cache import employees_cache, current_month_key
    return employees_cache.get(("__directory__", current_month_key())) or {}


def lookup_employee_by_cpf(cpf: str) -> Optional[Dict[str, Any]]:
    """Acha funcionário (numcad, nome, codccu, nomccu) pelo CPF no diretório cacheado."""
    import re as _re
    digits = _re.sub(r"\D", "", str(cpf or ""))
    if not digits:
        return None
    digits = digits.zfill(11) if len(digits) <= 11 else digits
    return fetch_employee_directory().get(digits)


def employee_directory_status() -> Dict[str, Any]:
    """Status do diretório cacheado (sem expor dados)."""
    from app.services.senior_cache import employees_cache, current_month_key
    cached = employees_cache.get(("__directory__", current_month_key()))
    return {"carregado": cached is not None, "total_cpfs": len(cached) if cached else 0}


def _fetch_payroll_local(
    periodo: str,
    codccu: Optional[Union[str, List[str]]] = None,
) -> List[Dict[str, Any]]:
    """
    Busca dados de folha no banco SQLite local (DEV_MODE).
    Retorna o mesmo formato de fetch_payroll para compatibilidade total.
    O filtro de codccu é ignorado — retorna todos os itens do período.
    """
    from app.db import SessionLocal
    from app.models.billing import PayrollItem, BillingPeriod, EmploymentContract
    from sqlalchemy.orm import joinedload

    mes_ref = periodo[:7]  # '2025-10-01' -> '2025-10'
    ccu_filter = _normalize_codccu_param(codccu)
    ccu_filter_set: Optional[Set[str]] = {str(c).strip() for c in ccu_filter} if ccu_filter else None

    db = SessionLocal()
    try:
        period = db.query(BillingPeriod).filter(
            BillingPeriod.mes_referencia == mes_ref
        ).first()

        if not period:
            logger.warning(
                "[DEV_MODE] Nenhum período '%s' encontrado no banco local. "
                "Períodos disponíveis: use dump.sql ou crie um período via upload.",
                mes_ref,
            )
            return []

        items = (
            db.query(PayrollItem)
            .options(
                joinedload(PayrollItem.employee),
                joinedload(PayrollItem.contract),
                joinedload(PayrollItem.unit),
                joinedload(PayrollItem.payroll_item_type),
            )
            .filter(PayrollItem.billing_period_id == period.id)
            .all()
        )

        salario_por_emp = _dev_salario_por_employee_id(items, db)

        payroll_data: List[Dict[str, Any]] = []
        for item in items:
            emp = item.employee
            contract = item.contract
            if contract is None and item.employee_id:
                contract = (
                    db.query(EmploymentContract)
                    .filter(EmploymentContract.employee_id == item.employee_id)
                    .order_by(EmploymentContract.id.desc())
                    .first()
                )
            unit = item.unit
            itype = item.payroll_item_type

            # Usa o codccu do filtro se a unidade não tiver centro definido
            requested_codccu = (
                codccu[0] if isinstance(codccu, list) and codccu else codccu or "LOCAL"
            )
            resolved_codccu = (
                (unit.centro_custo_femsa.strip() if unit and unit.centro_custo_femsa else None)
                or requested_codccu
            )
            resolved_nomccu = unit.nome_unidade if unit else resolved_codccu

            if ccu_filter_set is not None:
                if str(resolved_codccu or "").strip() not in ccu_filter_set:
                    continue

            senior_cod = _dev_senior_event_code_for_type(itype)
            codigo_evento = (
                senior_cod
                if senior_cod is not None
                else (itype.id if itype else None)
            )

            payroll_data.append({
                "matricula": emp.id if emp else None,
                "nome_funcionario": emp.nome if emp else None,
                "cpf": emp.cpf if emp else "",
                "data_admissao": str(contract.data_admissao) if contract and contract.data_admissao else None,
                "codccu": resolved_codccu,
                "nomccu": resolved_nomccu,
                "data_afastamento": None,
                "salario": float(salario_por_emp.get(emp.id, 0.0)) if emp else 0.0,
                "sitafa": 1,
                "situacao": "Trabalhando",
                "cargo": (contract.cargo or contract.funcao) if contract else None,
                "periodo_referencia": periodo[:10] if len(periodo) >= 10 else mes_ref,
                "codcal": 362,
                "codigo_evento": codigo_evento,
                "descricao_evento": itype.description if itype else (item.source_column or "Evento"),
                "natureza_evento": None,
                "tipo_evento": _dev_tipo_evento_for_type(itype),
                "referencia_evento": item.quantity or 0.0,
                "valor_evento": item.amount or 0.0,
            })

        _impute_salario_across_lancamentos(payroll_data)

        logger.info(
            "[DEV_MODE] fetch_payroll local: %d itens para período '%s' (codccu=%s)",
            len(payroll_data), mes_ref, codccu,
        )
        return payroll_data
    finally:
        db.close()


def fetch_payroll(
    periodo: str,
    numemp: int = 6,
    codccu: Optional[Union[str, List[str]]] = None,
    dat_ini: Optional[str] = None,
    dat_fim: Optional[str] = None,
    progress_cb=None,
) -> List[Dict[str, Any]]:
    """
    Busca folha de pagamento via SOAP Senior (consultaRegistros).
    Em DEV_MODE usa o banco SQLite local como fallback.

    progress_cb: callback opcional (done:int, total:int) chamado a cada CCU
    processado — usado pela exportação em segundo plano para reportar progresso.

    Args:
        periodo: Data no formato 'YYYY-MM-DD' (usado para calcular datIni/datFim se não informados)
        numemp: Número da empresa (padrão 6 = TELOS)
        codccu: Código(s) do centro de custo (string ou lista de strings)
        dat_ini: Data início no formato 'DD/MM/YYYY' (opcional, calculado de periodo)
        dat_fim: Data fim no formato 'DD/MM/YYYY' (opcional, calculado de periodo)
    """
    if DEV_MODE:
        return _fetch_payroll_local(periodo, codccu)

    import calendar
    from datetime import datetime

    if not dat_ini or not dat_fim:
        dt = datetime.strptime(periodo[:10], "%Y-%m-%d")
        last_day = calendar.monthrange(dt.year, dt.month)[1]
        dat_ini = f"01/{dt.month:02d}/{dt.year}"
        dat_fim = f"{last_day}/{dt.month:02d}/{dt.year}"

    cod_ccu_list = _normalize_codccu_param(codccu)

    registros = _call_soap_consulta(dat_ini, dat_fim, str(numemp), cod_ccu_list, progress_cb=progress_cb)

    payroll_data: List[Dict[str, Any]] = []
    for row in registros:
        cpf_raw = row.get("numCpf") or ""
        cpf_clean = str(cpf_raw).strip().replace(".", "").replace("-", "").replace("/", "")
        cpf = cpf_clean.zfill(11) if cpf_clean else ""

        payroll_data.append({
            "matricula": _safe_int(row.get("numCad")),
            "nome_funcionario": row.get("nomFun"),
            "cpf": cpf,
            "data_admissao": row.get("datAdm"),
            "codccu": row.get("codCcu"),
            "nomccu": row.get("nomCcu") or row.get("codCcu") or "",
            "data_afastamento": row.get("datAfa"),
            "salario": _safe_float(row.get("valSal")),
            "sitafa": _safe_int(row.get("sitAfa")),
            "situacao": row.get("desSit"),
            "cargo": row.get("titRed"),
            "periodo_referencia": row.get("perRef"),
            "codcal": _safe_int(row.get("codCal")),
            "codigo_evento": _safe_int(row.get("codEve")),
            "descricao_evento": row.get("desEve"),
            "natureza_evento": _safe_int(row.get("natEve")),
            "tipo_evento": _safe_int(row.get("tipEve")) or 0,
            "referencia_evento": _safe_float(row.get("refEve")),
            "valor_evento": _safe_float(row.get("valEve")),
        })
    _impute_salario_across_lancamentos(payroll_data)
    return payroll_data


def agrupar_por_matricula(payload: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    mapa: Dict[Any, Dict[str, Any]] = {}

    for item in payload:
        matricula = item.get("matricula")

        if matricula not in mapa:
            mapa[matricula] = {
                "matricula": matricula,
                "nome_funcionario": item.get("nome_funcionario"),
                "cpf": item.get("cpf"),
                "data_admissao": item.get("data_admissao"),
                "codccu": item.get("codccu"),
                "nomccu": item.get("nomccu"),
                "data_afastamento": item.get("data_afastamento"),
                "salario": item.get("salario"),
                "sitafa": item.get("sitafa"),
                "situacao": item.get("situacao"),
                "cargo": item.get("cargo"),
                "periodo_referencia": item.get("periodo_referencia"),
                "codcal": item.get("codcal"),
                "eventos": [],
            }
        else:
            cur = _safe_float(mapa[matricula].get("salario"))
            nxt = _safe_float(item.get("salario"))
            if nxt > cur:
                mapa[matricula]["salario"] = nxt

        mapa[matricula]["eventos"].append({
            "codigo_evento": item.get("codigo_evento"),
            "descricao_evento": item.get("descricao_evento"),
            "natureza_evento": item.get("natureza_evento", ""),
            "tipo_evento": item.get("tipo_evento", 0),
            "referencia_evento": item.get("referencia_evento"),
            "valor_evento": item.get("valor_evento"),
        })

    return enriquecer_com_totais_remuneracao(list(mapa.values()))


def count_billing_data(
    periodo: str,
    numemp: int,
    codccu: Optional[Union[str, List[str]]] = None,
    codcal: Optional[int] = None,
    sitafa: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Conta lançamentos e funcionários via SOAP Senior.
    Filtros opcionais de codcal e sitafa são aplicados em memória.
    """
    payroll = fetch_payroll(periodo, numemp, codccu)

    if codcal:
        payroll = [r for r in payroll if r.get("codcal") == codcal]
    if sitafa:
        payroll = [r for r in payroll if r.get("sitafa") == sitafa]

    numcads = set(r.get("matricula") for r in payroll if r.get("matricula"))

    return {
        "total_lancamentos": len(payroll),
        "total_funcionarios": len(numcads),
    }


def fetch_billing_data(
    periodo: str,
    numemp: int,
    codccu: Optional[Union[str, List[str]]] = None,
    codcal: Optional[int] = None,
    sitafa: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Busca dados de faturamento via SOAP Senior.
    Retorna no formato esperado pelos consumidores (chaves minúsculas do Senior).
    """
    payroll = fetch_payroll(periodo, numemp, codccu)

    if codcal:
        payroll = [r for r in payroll if r.get("codcal") == codcal]
    if sitafa:
        payroll = [r for r in payroll if r.get("sitafa") == sitafa]

    billing_data: List[Dict[str, Any]] = []
    for r in payroll:
        billing_data.append({
            "numcad": r.get("matricula"),
            "nomfun": r.get("nome_funcionario"),
            "datadm": r.get("data_admissao"),
            "codccu": r.get("codccu"),
            "datafa": r.get("data_afastamento"),
            "valsal": r.get("salario", 0.0),
            "sitafa": r.get("sitafa"),
            "dessit": r.get("situacao"),
            "titred": r.get("cargo"),
            "perref": r.get("periodo_referencia"),
            "codcal": r.get("codcal"),
            "codeve": r.get("codigo_evento"),
            "deseve": r.get("descricao_evento"),
            "refeve": r.get("referencia_evento", 0.0),
            "valeve": r.get("valor_evento", 0.0),
        })
    return billing_data


def fetch_employees_telos() -> List[Dict[str, Any]]:
    if DEV_MODE:
        logger.warning(
            "[DEV_MODE] Credenciais Senior não configuradas. "
            "Retornando lista vazia para fetch_employees_telos."
        )
        return []
    db = MSSQL_DB or "opus_hcm_221123"
    sql = f"""
        SELECT DISTINCT
            R034FUN.NUMCAD,
            R034FUN.NOMFUN,
            R034FUN.DATADM,
            R034FUN.CODCCU,
            R018CCU.NOMCCU,
            R034FUN.DATAFA,
            R034FUN.VALSAL,
            R034FUN.SITAFA,
            R010SIT.DESSIT,
            R024CAR.TITRED
        FROM
            [{db}].dbo.R034FUN
        LEFT JOIN
            [{db}].dbo.R024CAR ON
                R034FUN.ESTCAR = R024CAR.ESTCAR AND
                R034FUN.CODCAR = R024CAR.CODCAR
        LEFT JOIN
            [{db}].dbo.R010SIT ON
                R034FUN.SITAFA = R010SIT.CODSIT
        LEFT JOIN
            [{db}].dbo.R018CCU ON
                R034FUN.CODCCU = R018CCU.CODCCU
        WHERE
            R034FUN.NUMEMP = {TELOS_NUMEMP}
        ORDER BY
            R034FUN.NOMFUN
    """
    result = execute_query(sql)
    if result["status"] != "ok":
        raise Exception(result.get("message", "Erro desconhecido"))
    data = result.get("data", [])
    employees: List[Dict[str, Any]] = []
    for row in data:
        employees.append({
            "numcad": row.get("NUMCAD"),
            "nomfun": row.get("NOMFUN"),
            "datadm": row.get("DATADM"),
            "codccu": row.get("CODCCU"),
            "nomccu": row.get("NOMCCU"),
            "datafa": row.get("DATAFA"),
            "valsal": float(row.get("VALSAL", 0)) if row.get("VALSAL") else 0.0,
            "sitafa": row.get("SITAFA"),
            "dessit": row.get("DESSIT"),
            "cargo": row.get("TITRED"),
        })
    return employees


def fetch_payroll_items_telos(
    periodo: str,
    numemp: int,
    codccu: str,
) -> List[Dict[str, Any]]:
    return fetch_billing_data(periodo, numemp, codccu)
