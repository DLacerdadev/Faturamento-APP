"""
Serviço especializado para análise de lançamentos de faturamento.
Adapta a query do usuário para capturar volume de lançamentos e funcionários.
"""
from typing import Dict, Any, Optional
from app.services.senior_connector import execute_query


def analyze_billing_volume(
    periodo: str,
    numemp: int,
    codccu: Optional[str] = None,
    codcal: Optional[int] = None,
    sitafa: Optional[int] = None
) -> Dict[str, Any]:
    """
    Analisa a quantidade de lançamentos e funcionários para um período.
    
    Executa a query adaptada do usuário com COUNT para fazer diagnóstico de volume:
    - total_lancamentos: Quantidade de registros na tabela R046VER
    - total_funcionarios: Quantidade de funcionários distintos (NUMCAD)
    
    Args:
        periodo: Data no formato 'YYYY-MM-DD' para filtrar competência
        numemp: Número da empresa no Senior
        codccu: Código do centro de custo (opcional)
        codcal: Código do cálculo (opcional, ex: 362 para folha mensal)
        sitafa: Situação do funcionário (opcional, ex: 1=Trabalhando, 7=Demitido)
    
    Returns:
        Dict com:
        - total_lancamentos: Quantidade de lançamentos capturados
        - total_funcionarios: Quantidade de funcionários distintos
        - details: Breakdown adicional se disponível
        - query_executed: SQL executada (para auditoria)
    """
    
    # Monta filtros opcionais
    codccu_filter = f"AND R034FUN.CODCCU = '{codccu}'" if codccu else ""
    codcal_filter = f"AND R046VER.CODCAL = {codcal}" if codcal else ""
    sitafa_filter = f"AND R034FUN.SITAFA = {sitafa}" if sitafa else ""
    
    # Query exata do usuário, adaptada para análise
    sql = f"""
        SELECT
            COUNT(*)                   AS total_lancamentos,
            COUNT(DISTINCT Q.NUMCAD)     AS total_funcionarios
        FROM (
            SELECT
                R034FUN.NUMCAD,
                R034FUN.NOMFUN,
                R034FUN.DATADM,
                R034FUN.CODCCU,
                R034FUN.DATAFA,
                R034FUN.VALSAL,
                R034FUN.SITAFA,
                R010SIT.DESSIT,
                R024CAR.TITRED,
                R044CAL.PERREF,
                R046VER.CODCAL,
                R046VER.CODEVE,
                R008EVC.DESEVE,
                R046VER.REFEVE,
                R046VER.VALEVE
            FROM
                R034FUN
            JOIN
                R046VER ON
                    R034FUN.NUMEMP = R046VER.NUMEMP AND
                    R034FUN.TIPCOL = R046VER.TIPCOL AND
                    R034FUN.NUMCAD = R046VER.NUMCAD
            JOIN
                R024CAR ON
                    R034FUN.ESTCAR = R024CAR.ESTCAR AND
                    R034FUN.CODCAR = R024CAR.CODCAR
            JOIN
                R010SIT ON
                    R034FUN.SITAFA = R010SIT.CODSIT
            JOIN
                R008EVC ON
                    R046VER.TABEVE = R008EVC.CODTAB AND
                    R046VER.CODEVE = R008EVC.CODEVE
            JOIN
                R044CAL ON
                    R046VER.CODCAL = R044CAL.CODCAL
            WHERE
                '{periodo}' BETWEEN R044CAL.INICMP AND R044CAL.FIMCMP
                AND R034FUN.NUMEMP = {numemp}
                {codccu_filter}
                {codcal_filter}
                {sitafa_filter}
        ) AS Q
    """
    
    result = execute_query(sql)
    
    if result["status"] != "ok":
        return {
            "status": "error",
            "message": result.get("message", "Erro desconhecido"),
            "query_executed": sql.strip()
        }
    
    data = result.get("data", [])
    if data and len(data) > 0:
        row = data[0]
        return {
            "status": "ok",
            "periodo": periodo,
            "numemp": numemp,
            "filtros": {
                "codccu": codccu,
                "codcal": codcal,
                "sitafa": sitafa,
                "sitafa_descricao": _get_sitafa_description(sitafa) if sitafa else None
            },
            "total_lancamentos": row.get("total_lancamentos", 0),
            "total_funcionarios": row.get("total_funcionarios", 0),
            "media_lancamentos_por_func": round(
                row.get("total_lancamentos", 0) / max(row.get("total_funcionarios", 1), 1), 2
            ),
            "query_executed": sql.strip()
        }
    
    return {
        "status": "ok",
        "total_lancamentos": 0,
        "total_funcionarios": 0,
        "media_lancamentos_por_func": 0,
        "query_executed": sql.strip()
    }


def _get_sitafa_description(sitafa: int) -> str:
    """Mapeia código SITAFA para descrição legível."""
    sitafa_map = {
        1: "Trabalhando",
        3: "Auxílio Doença",
        7: "Demitido",
        8: "Licença sem Remuneração",
        14: "Certificado Diário"
    }
    return sitafa_map.get(sitafa, f"Código {sitafa}")


def get_volume_breakdown(periodo: str, numemp: int) -> Dict[str, Any]:
    """
    Quebra o volume por CODCAL e SITAFA para entender a composição dos dados.
    Útil para diagnóstico e planejamento de importação.
    
    Args:
        periodo: Data no formato 'YYYY-MM-DD'
        numemp: Número da empresa
    
    Returns:
        Dict com breakdown de lançamentos por cálculo e situação
    """
    sql = f"""
        SELECT
            R046VER.CODCAL,
            R034FUN.SITAFA,
            R010SIT.DESSIT,
            COUNT(*) AS qtd_lancamentos,
            COUNT(DISTINCT R034FUN.NUMCAD) AS qtd_funcionarios
        FROM
            R034FUN
        JOIN
            R046VER ON
                R034FUN.NUMEMP = R046VER.NUMEMP AND
                R034FUN.TIPCOL = R046VER.TIPCOL AND
                R034FUN.NUMCAD = R046VER.NUMCAD
        JOIN
            R024CAR ON
                R034FUN.ESTCAR = R024CAR.ESTCAR AND
                R034FUN.CODCAR = R024CAR.CODCAR
        JOIN
            R010SIT ON
                R034FUN.SITAFA = R010SIT.CODSIT
        JOIN
            R008EVC ON
                R046VER.TABEVE = R008EVC.CODTAB AND
                R046VER.CODEVE = R008EVC.CODEVE
        JOIN
            R044CAL ON
                R046VER.CODCAL = R044CAL.CODCAL
        WHERE
            '{periodo}' BETWEEN R044CAL.INICMP AND R044CAL.FIMCMP
            AND R034FUN.NUMEMP = {numemp}
        GROUP BY
            R046VER.CODCAL,
            R034FUN.SITAFA,
            R010SIT.DESSIT
        ORDER BY
            R046VER.CODCAL,
            R034FUN.SITAFA
    """
    
    result = execute_query(sql)
    
    if result["status"] != "ok":
        return {
            "status": "error",
            "message": result.get("message", "Erro desconhecido")
        }
    
    data = result.get("data", [])
    breakdown = []
    total_lancamentos = 0
    total_funcionarios = 0
    
    for row in data:
        lancamentos = row.get("qtd_lancamentos", 0)
        funcionarios = row.get("qtd_funcionarios", 0)
        total_lancamentos += lancamentos
        total_funcionarios += funcionarios
        
        breakdown.append({
            "codcal": row.get("CODCAL"),
            "sitafa": row.get("SITAFA"),
            "situacao_descricao": row.get("DESSIT"),
            "lancamentos": lancamentos,
            "funcionarios": funcionarios,
            "media_lancamentos_por_func": round(lancamentos / max(funcionarios, 1), 2)
        })
    
    return {
        "status": "ok",
        "periodo": periodo,
        "numemp": numemp,
        "total_lancamentos_geral": total_lancamentos,
        "total_funcionarios_geral": total_funcionarios,
        "breakdown": breakdown
    }
