from fastapi import APIRouter, Query, Request, UploadFile, File, Depends
from fastapi.responses import StreamingResponse
from typing import Optional, List, Dict
from io import BytesIO
import pandas as pd
import zipfile
import os
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

from sqlalchemy.orm import Session, joinedload
from sqlalchemy import extract
from app.db import get_db
from app.config import DEV_MODE
from app.models.medical_exam import MedicalExam
from app.models.epi_purchase import EpiPurchasePackage, EpiPurchaseDocument
from app.models.billing import AdditionalValue, Unit
from app.routers.epi_purchases import EPI_UPLOAD_DIR
from app.services.senior_connector import (
    list_tables, 
    get_connection_info, 
    test_connection,
    fetch_cost_centers,
    fetch_all_cost_centers,
    fetch_billing_data,
    fetch_employees_telos,
    fetch_payroll,
    agrupar_por_matricula,
    execute_query,
    count_billing_data
)
from app.services.billing_analyzer import (
    analyze_billing_volume,
    get_volume_breakdown
)
from app.services.invoice_builder import (
    build_generic_invoice,
    build_invoice_by_cost_center,
    build_invoice_detailed
)
from app.services.excel_export import (
    invoice_to_excel_bytes,
    invoice_to_excel_multi_sheet,
    generate_invoice_filename,
    payroll_to_excel_bytes,
    generate_payroll_filename,
    billing_to_femsa_excel,
    generate_femsa_filename,
    payroll_to_senior_excel_bytes,
    generate_senior_filename
)

router = APIRouter(prefix="/integrations", tags=["integrations"])

# Armazenamento temporário de dados de exames (em memória)
# Chave: nome do funcionário (normalizado), Valor: total de exames
exams_data_cache: Dict[str, float] = {}

# Armazenamento temporário de dados de benefícios (em memória)
# Chave: nome do funcionário (normalizado), Valor: total de benefícios
benefits_data_cache: Dict[str, float] = {}

# Armazenamento temporário de dados Flash (em memória)
# Chave: nome do funcionário (normalizado), Valor: total Flash
flash_data_cache: Dict[str, float] = {}

# Armazenamento temporário de dados iFood (em memória)
# Chave: nome do funcionário (normalizado), Valor: total iFood
ifood_data_cache: Dict[str, float] = {}


def normalize_name(name: str) -> str:
    """Normaliza nome para comparação (uppercase, sem espaços extras)."""
    if not name:
        return ""
    return " ".join(name.upper().strip().split())


def process_exams_excel(file_bytes: bytes) -> Dict[str, float]:
    """
    Processa planilha de exames e retorna dicionário com nome -> total.
    Espera planilha com headers na linha 5 (índice 4) e dados a partir da linha 6.
    """
    df = pd.read_excel(BytesIO(file_bytes), header=4)
    
    # Renomear colunas para facilitar
    cols = list(df.columns)
    col_map = {}
    for i, col in enumerate(cols):
        col_lower = str(col).lower()
        if 'nome' in col_lower or i == 0:
            col_map['nome'] = col
        if 'total' in col_lower:
            col_map['total'] = col
    
    if 'nome' not in col_map or 'total' not in col_map:
        # Tentar encontrar TOTAL pelo índice (geralmente coluna 22)
        if len(cols) > 22:
            col_map['total'] = cols[22]
        if len(cols) > 0:
            col_map['nome'] = cols[0]
    
    result = {}
    for _, row in df.iterrows():
        nome = row.get(col_map.get('nome', cols[0]))
        total = row.get(col_map.get('total', 'TOTAL'))
        
        if pd.notna(nome) and isinstance(nome, str) and nome.strip():
            nome_norm = normalize_name(nome)
            try:
                valor = float(total) if pd.notna(total) else 0.0
            except (ValueError, TypeError):
                valor = 0.0
            
            if nome_norm in result:
                result[nome_norm] += valor
            else:
                result[nome_norm] = valor
    
    return result


def process_benefits_csv(file_bytes: bytes) -> Dict[str, float]:
    """
    Processa CSV de benefícios (Sodexo) e retorna dicionário com nome -> total.
    Formato esperado: Matrícula, Colaborador, CPF, ..., Valor Creditado
    """
    import io
    
    # Tentar diferentes encodings
    for enc in ['latin-1', 'iso-8859-1', 'cp1252', 'utf-8']:
        try:
            df = pd.read_csv(io.BytesIO(file_bytes), encoding=enc, sep=';')
            break
        except:
            continue
    else:
        raise ValueError("Não foi possível ler o arquivo CSV")
    
    # Encontrar colunas relevantes
    cols = list(df.columns)
    col_nome = None
    col_valor = None
    
    for col in cols:
        col_lower = str(col).lower()
        if 'colaborador' in col_lower or 'nome' in col_lower:
            col_nome = col
        if 'valor' in col_lower and 'credit' in col_lower:
            col_valor = col
    
    if not col_nome:
        col_nome = cols[1] if len(cols) > 1 else cols[0]
    if not col_valor:
        for col in cols:
            if 'valor' in str(col).lower():
                col_valor = col
                break
    
    result = {}
    for _, row in df.iterrows():
        nome = row.get(col_nome)
        valor_str = row.get(col_valor, "0")
        
        if pd.notna(nome) and isinstance(nome, str) and nome.strip():
            nome_norm = normalize_name(nome)
            
            # Converter valor de "R$ 238,98" para float
            try:
                if isinstance(valor_str, str):
                    valor_str = valor_str.replace('R$', '').replace('.', '').replace(',', '.').strip()
                valor = float(valor_str) if valor_str else 0.0
            except (ValueError, TypeError):
                valor = 0.0
            
            if nome_norm in result:
                result[nome_norm] += valor
            else:
                result[nome_norm] = valor
    
    return result


@router.post("/senior/benefits/upload")
async def upload_benefits_data(file: UploadFile = File(...)):
    """
    Upload de CSV de benefícios (Sodexo).
    Processa e armazena em cache para uso no export FEMSA.
    """
    global benefits_data_cache
    
    try:
        contents = await file.read()
        benefits_data_cache = process_benefits_csv(contents)
        
        total_geral = sum(benefits_data_cache.values())
        
        return {
            "status": "success",
            "message": f"CSV processado com sucesso",
            "funcionarios": len(benefits_data_cache),
            "total_beneficios": round(total_geral, 2)
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Erro ao processar CSV: {str(e)}"
        }


@router.get("/senior/benefits/status")
async def get_benefits_status():
    """Retorna status dos dados de benefícios em cache."""
    total_geral = sum(benefits_data_cache.values())
    return {
        "has_data": len(benefits_data_cache) > 0,
        "funcionarios": len(benefits_data_cache),
        "total_beneficios": round(total_geral, 2)
    }


@router.delete("/senior/benefits/clear")
async def clear_benefits_data():
    """Limpa dados de benefícios do cache."""
    global benefits_data_cache
    benefits_data_cache = {}
    return {"status": "success", "message": "Dados de benefícios removidos"}


def process_flash_csv(file_bytes: bytes) -> Dict[str, float]:
    """
    Processa CSV Flash e retorna dicionário com nome -> total.
    Formato: CPF, Info, Nome, Grupo, Status, ..., TOTAL (R$)
    """
    import io
    
    for enc in ['utf-8-sig', 'utf-8', 'latin-1', 'cp1252']:
        try:
            df = pd.read_csv(io.BytesIO(file_bytes), encoding=enc)
            break
        except:
            continue
    else:
        raise ValueError("Não foi possível ler o arquivo CSV")
    
    cols = list(df.columns)
    col_nome = None
    col_total = None
    
    for col in cols:
        col_lower = str(col).lower()
        if col_lower == 'nome':
            col_nome = col
        if 'total' in col_lower and 'r$' in col_lower:
            col_total = col
    
    if not col_nome:
        col_nome = cols[2] if len(cols) > 2 else cols[0]
    if not col_total:
        col_total = cols[-2] if len(cols) > 1 else cols[-1]
    
    result = {}
    for _, row in df.iterrows():
        nome = row.get(col_nome)
        valor_str = row.get(col_total, "0")
        
        if pd.notna(nome) and isinstance(nome, str) and nome.strip():
            nome_norm = normalize_name(nome)
            
            try:
                if isinstance(valor_str, str):
                    valor_str = valor_str.replace('.', '').replace(',', '.').strip()
                valor = float(valor_str) if valor_str else 0.0
            except (ValueError, TypeError):
                valor = 0.0
            
            if nome_norm in result:
                result[nome_norm] += valor
            else:
                result[nome_norm] = valor
    
    return result


@router.post("/senior/flash/upload")
async def upload_flash_data(file: UploadFile = File(...)):
    """
    Upload de CSV Flash.
    Processa e armazena em cache.
    """
    global flash_data_cache
    
    try:
        contents = await file.read()
        flash_data_cache = process_flash_csv(contents)
        
        total_geral = sum(flash_data_cache.values())
        
        return {
            "status": "success",
            "message": f"CSV Flash processado com sucesso",
            "funcionarios": len(flash_data_cache),
            "total_flash": round(total_geral, 2)
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Erro ao processar CSV: {str(e)}"
        }


@router.get("/senior/flash/status")
async def get_flash_status():
    """Retorna status dos dados Flash em cache."""
    total_geral = sum(flash_data_cache.values())
    return {
        "has_data": len(flash_data_cache) > 0,
        "funcionarios": len(flash_data_cache),
        "total_flash": round(total_geral, 2)
    }


@router.delete("/senior/flash/clear")
async def clear_flash_data():
    """Limpa dados Flash do cache."""
    global flash_data_cache
    flash_data_cache = {}
    return {"status": "success", "message": "Dados Flash removidos"}


def process_ifood_csv(file_bytes: bytes) -> Dict[str, float]:
    """
    Processa CSV iFood Benefícios e retorna dicionário com nome -> total.
    Soma todas as colunas de valores (Refeição, Alimentação, etc.).
    """
    import io
    
    for enc in ['utf-16', 'utf-16-le', 'utf-8-sig', 'utf-8', 'latin-1']:
        try:
            df = pd.read_csv(io.BytesIO(file_bytes), encoding=enc, sep=',')
            if len(df.columns) > 5:
                break
        except:
            continue
    else:
        raise ValueError("Não foi possível ler o arquivo CSV")
    
    cols = list(df.columns)
    col_nome = None
    
    for col in cols:
        col_lower = str(col).lower()
        if 'nome' in col_lower and 'colaborador' in col_lower:
            col_nome = col
            break
    
    if not col_nome:
        for col in cols:
            if 'nome' in str(col).lower():
                col_nome = col
                break
    
    if not col_nome:
        col_nome = cols[6] if len(cols) > 6 else cols[0]
    
    value_cols = [c for c in cols if c not in ['Nome da empresa', 'CNPJ', 'ID da recarga', 
                  'Contexto da recarga', 'Mês da recarga', 'CPF', col_nome]]
    
    result = {}
    for _, row in df.iterrows():
        nome = row.get(col_nome)
        
        if pd.notna(nome) and isinstance(nome, str) and nome.strip():
            nome_norm = normalize_name(nome)
            
            total = 0.0
            for vc in value_cols:
                try:
                    val = row.get(vc, 0)
                    if pd.notna(val):
                        total += float(val)
                except (ValueError, TypeError):
                    pass
            
            if nome_norm in result:
                result[nome_norm] += total
            else:
                result[nome_norm] = total
    
    return result


@router.post("/senior/ifood/upload")
async def upload_ifood_data(file: UploadFile = File(...)):
    """
    Upload de CSV iFood Benefícios.
    Processa e armazena em cache.
    """
    global ifood_data_cache
    
    try:
        contents = await file.read()
        ifood_data_cache = process_ifood_csv(contents)
        
        total_geral = sum(ifood_data_cache.values())
        
        return {
            "status": "success",
            "message": f"CSV iFood processado com sucesso",
            "funcionarios": len(ifood_data_cache),
            "total_ifood": round(total_geral, 2)
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Erro ao processar CSV: {str(e)}"
        }


@router.get("/senior/ifood/status")
async def get_ifood_status():
    """Retorna status dos dados iFood em cache."""
    total_geral = sum(ifood_data_cache.values())
    return {
        "has_data": len(ifood_data_cache) > 0,
        "funcionarios": len(ifood_data_cache),
        "total_ifood": round(total_geral, 2)
    }


@router.delete("/senior/ifood/clear")
async def clear_ifood_data():
    """Limpa dados iFood do cache."""
    global ifood_data_cache
    ifood_data_cache = {}
    return {"status": "success", "message": "Dados iFood removidos"}


@router.post("/senior/exams/upload")
async def upload_exams_data(file: UploadFile = File(...)):
    """
    Upload de planilha de exames médicos.
    Processa e armazena em cache para uso no export FEMSA.
    """
    global exams_data_cache
    
    try:
        contents = await file.read()
        exams_data_cache = process_exams_excel(contents)
        
        total_geral = sum(exams_data_cache.values())
        
        return {
            "status": "success",
            "message": f"Planilha processada com sucesso",
            "funcionarios": len(exams_data_cache),
            "total_exames": round(total_geral, 2)
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Erro ao processar planilha: {str(e)}"
        }


@router.get("/senior/exams/status")
async def get_exams_status():
    """Retorna status dos dados de exames em cache."""
    total_geral = sum(exams_data_cache.values())
    return {
        "has_data": len(exams_data_cache) > 0,
        "funcionarios": len(exams_data_cache),
        "total_exames": round(total_geral, 2)
    }


@router.delete("/senior/exams/clear")
async def clear_exams_data():
    """Limpa dados de exames do cache."""
    global exams_data_cache
    exams_data_cache = {}
    return {"status": "success", "message": "Dados de exames removidos"}


@router.get("/senior/status")
async def get_senior_status():
    """
    Verifica o status da configuração da API Senior (para diagnóstico).
    Inclui teste de conexão (health check).
    """
    info = get_connection_info()
    health = test_connection()
    
    return {
        "api_domain": info.get("api_domain"),
        "api_key_configured": info.get("api_key_configured", False),
        "database": info.get("database"),
        "numemp_telos": info.get("numemp_telos"),
        "health": health
    }


@router.get("/senior/test-connection")
async def test_senior_connection():
    """
    Testa a conexão com a API Senior via endpoint /health.
    """
    result = test_connection()
    return result


@router.get("/senior/tables")
async def get_senior_tables():
    """
    Lista todas as tabelas disponíveis no banco MSSQL via API Senior.
    """
    try:
        tables = list_tables()
        return {"status": "ok", "count": len(tables), "tables": tables}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def _get_local_cost_centers(db: Session) -> List[Dict]:
    """
    Retorna centros de custo do banco local (modo dev).
    Combina billing_additional_values (codccu/nome_ccu) com
    billing_units (centro_custo_femsa) para montar a mesma estrutura
    que o endpoint Senior retornaria.
    """
    centers: Dict[str, str] = {}

    for av in db.query(AdditionalValue).order_by(AdditionalValue.codccu).all():
        if av.codccu and av.codccu.strip():
            centers[av.codccu.strip()] = av.nome_ccu or av.codccu.strip()

    for unit in db.query(Unit).all():
        ccu = (unit.centro_custo_femsa or "").strip()
        if ccu and ccu not in centers:
            centers[ccu] = unit.nome_unidade or ccu

    return [{"codccu": cod, "nomccu": nome} for cod, nome in sorted(centers.items())]


@router.get("/senior/cost-centers")
async def get_cost_centers(db: Session = Depends(get_db)):
    """
    Lista centros de custo da TELOS (NUMEMP=6).
    Em DEV_MODE (sem credenciais Senior), usa dados locais do banco SQLite.
    """
    if DEV_MODE:
        centers = _get_local_cost_centers(db)
        return {
            "status": "ok",
            "count": len(centers),
            "data": centers,
            "source": "local_db",
        }
    try:
        centers = fetch_cost_centers()
        return {"status": "ok", "count": len(centers), "data": centers}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/senior/cost-centers/all")
async def get_all_cost_centers(db: Session = Depends(get_db)):
    """
    Lista TODOS os centros de custo (sem filtro de empresa).
    Em DEV_MODE (sem credenciais Senior), usa dados locais do banco SQLite.
    """
    if DEV_MODE:
        centers = _get_local_cost_centers(db)
        return {
            "status": "ok",
            "count": len(centers),
            "data": centers,
            "source": "local_db",
        }
    try:
        centers = fetch_all_cost_centers()
        return {"status": "ok", "count": len(centers), "data": centers}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/senior/employees")
async def get_employees():
    """
    Lista funcionários da empresa TELOS (NUMEMP=6).
    Retorna dados básicos: matrícula, nome, admissão, centro de custo, cargo, etc.
    """
    try:
        employees = fetch_employees_telos()
        return {"status": "ok", "count": len(employees), "data": employees}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/senior/billing/count")
async def get_billing_count(
    periodo: str = Query(..., description="Data no formato YYYY-MM-DD para filtrar competência"),
    numemp: int = Query(..., description="Número da empresa no Senior"),
    codccu: Optional[str] = Query(None, description="Código do centro de custo (opcional)"),
    codcal: Optional[int] = Query(None, description="Código do cálculo (opcional, ex: 362 para folha mensal)"),
    sitafa: Optional[int] = Query(None, description="Situação do funcionário (opcional, ex: 1=Trabalhando, 7=Demitido)")
):
    """
    Conta lançamentos e funcionários para depuração.
    Executa a mesma query com COUNT para comparar resultados.
    """
    try:
        result = count_billing_data(periodo, numemp, codccu, codcal, sitafa)
        return {"status": "ok", **result}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/senior/billing")
async def get_billing_data(
    periodo: str = Query(..., description="Data no formato YYYY-MM-DD para filtrar competência"),
    numemp: int = Query(..., description="Número da empresa no Senior"),
    codccu: Optional[str] = Query(None, description="Código do centro de custo (opcional)"),
    codcal: Optional[int] = Query(None, description="Código do cálculo (opcional, ex: 362 para folha mensal)"),
    sitafa: Optional[int] = Query(None, description="Situação do funcionário (opcional, ex: 1=Trabalhando, 7=Demitido)")
):
    """
    Busca dados de faturamento.
    
    - **periodo**: Data para filtrar competência (formato: YYYY-MM-DD)
    - **numemp**: Número da empresa no Senior
    - **codccu**: Código do centro de custo (opcional - se omitido, busca todos)
    - **codcal**: Código do cálculo (opcional, ex: 362 para folha mensal)
    - **sitafa**: Situação do funcionário (opcional, ex: 1=Trabalhando, 7=Demitido)
    
    Retorna dados completos: funcionário, eventos, valores, etc.
    """
    try:
        data = fetch_billing_data(periodo, numemp, codccu, codcal, sitafa)
        return {"status": "ok", "count": len(data), "data": data}
    except Exception as e:
        return {"status": "error", "message": str(e)}


from pydantic import BaseModel

class SQLQuery(BaseModel):
    sql_text: str

@router.post("/senior/query")
async def run_custom_query(query: SQLQuery):
    """
    Executa uma query SQL personalizada via API Senior.
    Apenas comandos SELECT são permitidos.
    Envie JSON: {"sql_text": "SELECT ..."}
    """
    result = execute_query(query.sql_text)
    return result


@router.get("/senior/billing/analyze")
async def analyze_billing_volume_endpoint(
    periodo: str = Query(..., description="Data no formato YYYY-MM-DD para filtrar competência"),
    numemp: int = Query(..., description="Número da empresa no Senior"),
    codccu: Optional[str] = Query(None, description="Código do centro de custo (opcional)"),
    codcal: Optional[int] = Query(None, description="Código do cálculo (opcional, ex: 362 para folha mensal)"),
    sitafa: Optional[int] = Query(None, description="Situação do funcionário (opcional, ex: 1=Trabalhando, 7=Demitido)")
):
    """
    Analisa volume de lançamentos e funcionários usando a query exata do usuário.
    
    Retorna:
    - total_lancamentos: Quantidade de lançamentos capturados
    - total_funcionarios: Quantidade de funcionários distintos
    - media_lancamentos_por_func: Média de lançamentos por funcionário
    
    Exemplos:
    - GET /integrations/senior/billing/analyze?periodo=2025-11-01&numemp=6&codcal=362&sitafa=1
    - GET /integrations/senior/billing/analyze?periodo=2025-11-01&numemp=6
    """
    result = analyze_billing_volume(periodo, numemp, codccu, codcal, sitafa)
    return result


@router.get("/senior/billing/breakdown")
async def get_billing_breakdown_endpoint(
    periodo: str = Query(..., description="Data no formato YYYY-MM-DD para filtrar competência"),
    numemp: int = Query(..., description="Número da empresa no Senior")
):
    """
    Quebra o volume de lançamentos por CODCAL (tipo de cálculo) e SITAFA (situação do funcionário).
    Útil para diagnóstico e planejamento de importação.
    
    Retorna:
    - breakdown: Lista com volume agrupado por cálculo e situação
    - total_lancamentos_geral: Total geral de lançamentos
    - total_funcionarios_geral: Total geral de funcionários
    
    Exemplo:
    - GET /integrations/senior/billing/breakdown?periodo=2025-11-01&numemp=6
    """
    result = get_volume_breakdown(periodo, numemp)
    return result


NUMEMP_TELOS = 6


def deduplicate_codccu(codccu: List[str]) -> List[str]:
    seen = set()
    unique = []
    for cod in codccu:
        cod_norm = cod.strip()
        if cod_norm in seen:
            logger.warning(f"Centro de custo duplicado removido do filtro: '{cod_norm}'")
        else:
            seen.add(cod_norm)
            unique.append(cod_norm)
    return unique


@router.get("/senior/billing/summary")
async def get_billing_summary(
    periodo: str = Query(..., description="Data no formato YYYY-MM-DD para filtrar competência"),
    codccu: Optional[str] = Query(None, description="Código do centro de custo (opcional)"),
    codcal: Optional[int] = Query(None, description="Código do cálculo (opcional, ex: 362 para folha mensal)"),
    sitafa: Optional[int] = Query(None, description="Situação do funcionário (opcional, ex: 1=Trabalhando)")
):
    """
    Retorna resumo do faturamento com totais.
    SEMPRE filtra por empresa TELOS (NUMEMP=6).
    
    Retorna:
    - total_lancamentos: Quantidade de lançamentos
    - total_funcionarios: Quantidade de funcionários distintos
    - periodo: Período consultado
    - codccu: Centro de custo ou "Todos"
    - codcal: Código do cálculo
    - sitafa: Situação do funcionário
    """
    try:
        result = count_billing_data(periodo, NUMEMP_TELOS, codccu, codcal, sitafa)
        return {
            "status": "ok",
            "total_lancamentos": result.get("total_lancamentos", 0),
            "total_funcionarios": result.get("total_funcionarios", 0),
            "periodo": periodo,
            "numemp": NUMEMP_TELOS,
            "codccu": codccu or "Todos",
            "codcal": codcal,
            "sitafa": sitafa
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/senior/billing/invoice")
async def get_billing_invoice(
    periodo: str = Query(..., description="Data no formato YYYY-MM-DD para filtrar competência"),
    codccu: Optional[str] = Query(None, description="Código do centro de custo (opcional)"),
    codcal: Optional[int] = Query(None, description="Código do cálculo (opcional, ex: 362 para folha mensal)"),
    sitafa: Optional[int] = Query(None, description="Situação do funcionário (opcional, ex: 1=Trabalhando)")
):
    """
    Retorna fatura agregada por funcionário.
    SEMPRE filtra por empresa TELOS (NUMEMP=6).
    Agrupa lançamentos por funcionário e centro de custo, somando valores.
    """
    try:
        billing_data = fetch_billing_data(periodo, NUMEMP_TELOS, codccu, codcal, sitafa)
        invoice = build_generic_invoice(billing_data)
        
        total_geral = sum(item.get("valor_total", 0) for item in invoice)
        
        return {
            "status": "ok",
            "periodo": periodo,
            "numemp": NUMEMP_TELOS,
            "codccu": codccu or "Todos",
            "total_funcionarios": len(invoice),
            "total_geral": round(total_geral, 2),
            "data": invoice
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/senior/billing/export")
async def export_billing_excel(
    periodo: str = Query(..., description="Data no formato YYYY-MM-DD para filtrar competência"),
    codccu: Optional[str] = Query(None, description="Código do centro de custo (opcional)"),
    codcal: Optional[int] = Query(None, description="Código do cálculo (opcional, ex: 362 para folha mensal)"),
    sitafa: Optional[int] = Query(None, description="Situação do funcionário (opcional, ex: 1=Trabalhando)"),
    format: str = Query("detailed", description="Formato: 'detailed' (cada evento) ou 'aggregated' (por funcionário)")
):
    """
    Exporta faturamento para arquivo Excel.
    SEMPRE filtra por empresa TELOS (NUMEMP=6).
    
    Formatos disponíveis:
    - detailed: Cada evento como linha separada (ideal para auditoria)
    - aggregated: Agrupado por funcionário com soma de valores
    
    Retorna arquivo .xlsx para download.
    """
    try:
        billing_data = fetch_billing_data(periodo, NUMEMP_TELOS, codccu, codcal, sitafa)
        
        if format == "aggregated":
            invoice_data = build_generic_invoice(billing_data)
        else:
            invoice_data = build_invoice_detailed(billing_data)
        
        excel_bytes = invoice_to_excel_bytes(invoice_data)
        filename = generate_invoice_filename(periodo, codccu)
        
        return StreamingResponse(
            BytesIO(excel_bytes),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"'
            }
        )
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/senior/payroll")
async def get_payroll(
    periodo: str = Query(..., description="Data no formato YYYY-MM-DD para filtrar competência"),
    codccu: List[str] = Query(..., description="Código(s) do centro de custo (um ou mais)")
):
    """
    Busca folha de pagamento de um ou mais centros de custo.
    SEMPRE filtra por empresa TELOS (NUMEMP=6).
    
    Retorna dados agrupados por matrícula, com lista de eventos para cada funcionário.
    
    Campos retornados por funcionário:
    - matricula, nome_funcionario, cargo, salario, data_admissao, data_afastamento
    - eventos: lista de {codigo_evento, descricao_evento, referencia_evento, valor_evento}
    """
    try:
        codccu = deduplicate_codccu(codccu)
        payroll_data = fetch_payroll(periodo, NUMEMP_TELOS, codccu)
        all_grouped_data = agrupar_por_matricula(payroll_data)
        
        total_eventos = sum(len(emp.get("eventos", [])) for emp in all_grouped_data)
        
        return {
            "status": "ok",
            "periodo": periodo,
            "numemp": NUMEMP_TELOS,
            "codccu": codccu if len(codccu) > 1 else codccu[0],
            "total_funcionarios": len(all_grouped_data),
            "total_eventos": total_eventos,
            "funcionarios": all_grouped_data
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/senior/payroll/export")
async def export_payroll_excel(
    periodo: str = Query(..., description="Data no formato YYYY-MM-DD para filtrar competência"),
    codccu: List[str] = Query(..., description="Código(s) do centro de custo (um ou mais)")
):
    """
    Exporta folha de pagamento para arquivo Excel.
    SEMPRE filtra por empresa TELOS (NUMEMP=6).
    
    Gera planilha com todos os eventos de cada funcionário.
    Retorna arquivo .xlsx para download.
    """
    try:
        codccu = deduplicate_codccu(codccu)
        payroll_data = fetch_payroll(periodo, NUMEMP_TELOS, codccu)
        all_grouped_data = agrupar_por_matricula(payroll_data)
        
        codccu_label = "_".join(codccu) if len(codccu) <= 3 else f"{len(codccu)}_ccus"
        excel_bytes = payroll_to_excel_bytes(all_grouped_data, periodo, codccu_label)
        filename = generate_payroll_filename(periodo, codccu_label)
        
        return StreamingResponse(
            BytesIO(excel_bytes),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"'
            }
        )
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/senior/billing/export-femsa")
async def export_billing_femsa(
    periodo: str = Query(..., description="Data no formato YYYY-MM-DD para filtrar competência"),
    codccu: List[str] = Query(..., description="Códigos dos centros de custo"),
    db: Session = Depends(get_db)
):
    """
    Exporta faturamento no formato FEMSA para arquivo Excel.
    SEMPRE filtra por empresa TELOS (NUMEMP=6).
    
    Gera planilha com todas as colunas do modelo FEMSA incluindo eventos,
    taxas e encargos. Aceita múltiplos centros de custo.
    Busca exames médicos do banco de dados filtrando pelo mês/ano do período.
    """
    try:
        codccu = deduplicate_codccu(codccu)
        payroll_data = fetch_payroll(periodo, NUMEMP_TELOS, codccu)
        all_grouped_data = agrupar_por_matricula(payroll_data)
        
        dt = datetime.strptime(periodo[:7], "%Y-%m")
        ano = dt.year
        mes = dt.month
        exams = db.query(MedicalExam).filter(
            extract('year', MedicalExam.data_exame) == ano,
            extract('month', MedicalExam.data_exame) == mes
        ).all()
        
        exams_by_numcad: Dict[int, float] = {}
        for exam in exams:
            if exam.numcad:
                exams_by_numcad[exam.numcad] = exams_by_numcad.get(exam.numcad, 0) + (exam.total or 0.0)
        
        codccu_label = "_".join(codccu) if len(codccu) <= 3 else f"{len(codccu)}_ccus"
        excel_bytes = billing_to_femsa_excel(
            all_grouped_data, 
            periodo, 
            codccu_label,
            exams_data=exams_data_cache,
            exams_by_numcad=exams_by_numcad,
            benefits_data=benefits_data_cache
        )
        filename = generate_femsa_filename(periodo, codccu_label)
        
        epi_packages = db.query(EpiPurchasePackage).options(
            joinedload(EpiPurchasePackage.documents)
        ).filter(
            extract('year', EpiPurchasePackage.mes_ano) == ano,
            extract('month', EpiPurchasePackage.mes_ano) == mes
        ).all()
        
        epi_docs = []
        for pkg in epi_packages:
            for doc in (pkg.documents or []):
                filepath = os.path.join(EPI_UPLOAD_DIR, doc.stored_filename)
                if os.path.exists(filepath):
                    epi_docs.append((doc.original_filename, filepath))
        
        if epi_docs:
            zip_buffer = BytesIO()
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(filename, excel_bytes)
                used_names = set()
                for orig_name, fpath in epi_docs:
                    arcname = f"EPIs/{orig_name}"
                    if arcname in used_names:
                        base, ext = os.path.splitext(orig_name)
                        counter = 1
                        while arcname in used_names:
                            arcname = f"EPIs/{base}_{counter}{ext}"
                            counter += 1
                    used_names.add(arcname)
                    zf.write(fpath, arcname)
            zip_buffer.seek(0)
            zip_filename = filename.replace('.xlsx', '_com_epis.zip')
            return StreamingResponse(
                zip_buffer,
                media_type="application/zip",
                headers={
                    "Content-Disposition": f'attachment; filename="{zip_filename}"'
                }
            )
        
        return StreamingResponse(
            BytesIO(excel_bytes),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"'
            }
        )
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.get("/senior/payroll/export-senior")
async def export_payroll_senior(
    periodo: str = Query(..., description="Data no formato YYYY-MM-DD para filtrar competência"),
    codccu: List[str] = Query(..., description="Códigos dos centros de custo"),
):
    """
    Exporta folha de pagamento no formato dinâmico Folha Senior.
    Colunas fixas com dados do funcionário + colunas dinâmicas por evento.
    SEMPRE filtra por empresa TELOS (NUMEMP=6).
    """
    try:
        codccu = deduplicate_codccu(codccu)
        payroll_data = fetch_payroll(periodo, NUMEMP_TELOS, codccu)
        all_grouped_data = agrupar_por_matricula(payroll_data)

        codccu_label = "_".join(codccu) if len(codccu) <= 3 else f"{len(codccu)}_ccus"
        excel_bytes = payroll_to_senior_excel_bytes(all_grouped_data, periodo)
        filename = generate_senior_filename(periodo, codccu_label)

        return StreamingResponse(
            BytesIO(excel_bytes),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"'
            }
        )
    except Exception as e:
        logger.error(f"Erro ao gerar Folha Senior: {e}")
        return {"status": "error", "message": str(e)}


@router.get("/senior/billing/export-skyrail")
async def export_billing_skyrail(
    periodo: str = Query(..., description="Data no formato YYYY-MM-DD para filtrar competência"),
    codccu: List[str] = Query(..., description="Códigos dos centros de custo"),
    db: Session = Depends(get_db)
):
    """
    Exporta faturamento no formato Skyrail para arquivo Excel.
    TODO: Implementar modelo Skyrail.
    """
    return {"status": "error", "message": "Modelo Skyrail ainda não implementado. Em breve!"}
