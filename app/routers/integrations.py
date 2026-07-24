from fastapi import APIRouter, Query, Request, UploadFile, File, Depends
from fastapi.responses import StreamingResponse, JSONResponse
from typing import Optional, List, Dict
from io import BytesIO
import pandas as pd
import zipfile
import os
import logging
import threading
from datetime import datetime

logger = logging.getLogger(__name__)

from sqlalchemy.orm import Session, joinedload
from sqlalchemy import extract
from app.db import get_db
from app.services import export_jobs
from app.services.audit import audit
from app.config import DEV_MODE
from app.models.medical_exam import MedicalExam
from app.models.epi_purchase import EpiPurchasePackage, EpiPurchaseDocument
from app.models.billing import AdditionalValue, Unit, Company
from app.routers.epi_purchases import EPI_UPLOAD_DIR
from app.services.senior_connector import (
    list_tables, 
    get_connection_info, 
    test_connection,
    fetch_cost_centers,
    fetch_all_cost_centers,
    fetch_billing_data,
    fetch_employees_telos,
    fetch_active_employees,
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

from app.routers.auth import require_login
from app.services.permissions import require_role

# Todas as rotas exigem login (dependency no nível do router); endpoints
# sensíveis (SQL cru, listagem de tabelas) elevam para admin no próprio handler.
router = APIRouter(prefix="/integrations", tags=["integrations"],
                   dependencies=[Depends(require_login)])

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
async def upload_benefits_data(request: Request = None, file: UploadFile = File(...),
                               db: Session = Depends(get_db)):
    """
    Upload de CSV de benefícios (Sodexo).
    Processa e armazena em cache para uso no export FEMSA.
    """
    global benefits_data_cache

    try:
        contents = await file.read()
        benefits_data_cache = process_benefits_csv(contents)

        total_geral = sum(benefits_data_cache.values())

        audit(request, "importacao.dados", entidade="importacao",
              detalhe={"tipo": "beneficios_cache", "arquivo": file.filename,
                       "n_registros": len(benefits_data_cache)}, db=db)
        return {
            "status": "success",
            "message": f"CSV processado com sucesso",
            "funcionarios": len(benefits_data_cache),
            "total_beneficios": round(total_geral, 2)
        }
    except Exception as e:
        audit(request, "importacao.dados", entidade="importacao",
              detalhe={"tipo": "beneficios_cache", "arquivo": file.filename,
                       "erro": str(e)[:200]}, db=db, status="erro")
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
async def upload_flash_data(request: Request = None, file: UploadFile = File(...),
                            db: Session = Depends(get_db)):
    """
    Upload de CSV Flash.
    Processa e armazena em cache.
    """
    global flash_data_cache

    try:
        contents = await file.read()
        flash_data_cache = process_flash_csv(contents)

        total_geral = sum(flash_data_cache.values())

        audit(request, "importacao.dados", entidade="importacao",
              detalhe={"tipo": "flash_cache", "arquivo": file.filename,
                       "n_registros": len(flash_data_cache)}, db=db)
        return {
            "status": "success",
            "message": f"CSV Flash processado com sucesso",
            "funcionarios": len(flash_data_cache),
            "total_flash": round(total_geral, 2)
        }
    except Exception as e:
        audit(request, "importacao.dados", entidade="importacao",
              detalhe={"tipo": "flash_cache", "arquivo": file.filename,
                       "erro": str(e)[:200]}, db=db, status="erro")
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
async def upload_ifood_data(request: Request = None, file: UploadFile = File(...),
                            db: Session = Depends(get_db)):
    """
    Upload de CSV iFood Benefícios.
    Processa e armazena em cache.
    """
    global ifood_data_cache

    try:
        contents = await file.read()
        ifood_data_cache = process_ifood_csv(contents)

        total_geral = sum(ifood_data_cache.values())

        audit(request, "importacao.dados", entidade="importacao",
              detalhe={"tipo": "ifood_cache", "arquivo": file.filename,
                       "n_registros": len(ifood_data_cache)}, db=db)
        return {
            "status": "success",
            "message": f"CSV iFood processado com sucesso",
            "funcionarios": len(ifood_data_cache),
            "total_ifood": round(total_geral, 2)
        }
    except Exception as e:
        audit(request, "importacao.dados", entidade="importacao",
              detalhe={"tipo": "ifood_cache", "arquivo": file.filename,
                       "erro": str(e)[:200]}, db=db, status="erro")
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
async def upload_exams_data(request: Request = None, file: UploadFile = File(...),
                            db: Session = Depends(get_db)):
    """
    Upload de planilha de exames médicos.
    Processa e armazena em cache para uso no export FEMSA.
    """
    global exams_data_cache

    try:
        contents = await file.read()
        exams_data_cache = process_exams_excel(contents)

        total_geral = sum(exams_data_cache.values())

        audit(request, "importacao.dados", entidade="importacao",
              detalhe={"tipo": "exames_cache", "arquivo": file.filename,
                       "n_registros": len(exams_data_cache)}, db=db)
        return {
            "status": "success",
            "message": f"Planilha processada com sucesso",
            "funcionarios": len(exams_data_cache),
            "total_exames": round(total_geral, 2)
        }
    except Exception as e:
        audit(request, "importacao.dados", entidade="importacao",
              detalhe={"tipo": "exames_cache", "arquivo": file.filename,
                       "erro": str(e)[:200]}, db=db, status="erro")
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
async def get_senior_tables(request: Request, db: Session = Depends(get_db)):
    """
    Lista todas as tabelas disponíveis no banco MSSQL via API Senior.
    Restrito a ADMIN (expõe o schema do ERP).
    """
    require_role(request, db, "admin")
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
async def get_employees(
    codccu: Optional[str] = Query(None, description="Filtra funcionários por centro de custo"),
    active_only: bool = Query(False, description="Se True, retorna apenas ativos (sem afastamento ou afastamento futuro)"),
):
    """
    Lista funcionários da empresa TELOS (NUMEMP=6).
    Retorna dados básicos: matrícula, nome, admissão, centro de custo, cargo, etc.

    Query params (opcionais; retro-compatível):
    - `codccu`: filtra por centro de custo.
    - `active_only`: aplica regra de ativo (FR-3 da spec 001-epi-purchase-flow).

    Quando `codccu` é informado, usa SOAP `consultaRegistros` (mês corrente) — mais rápido e
    funciona sem MSSQL. Sem `codccu`, mantém o caminho histórico via `fetch_employees_telos`.
    """
    try:
        if codccu:
            # Usa o caminho SOAP que respeita o filtro por CCU e o critério de ativo.
            employees = fetch_active_employees(codccu)
            if not active_only:
                # active_only=False com codccu: já temos só ativos do mês corrente; vamos manter.
                # (sem codccu o caminho legado abaixo retorna tudo)
                pass
            return {"status": "ok", "count": len(employees), "data": employees}
        employees = fetch_employees_telos()
        if active_only:
            from app.services.senior_connector import is_employee_active
            employees = [e for e in employees if is_employee_active(e)]
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


class ExportBatchInput(BaseModel):
    """Payload para os endpoints POST de export quando há muitos CCUs.

    Usado quando a query string GET ficaria grande demais (mais de algumas
    dezenas de CCUs) e o navegador trunca/bloqueia silenciosamente.
    """
    periodo: str
    codccu: List[str]
    modelo: Optional[str] = None  # usado só pelo /billing/export-batch
    # Override dos parâmetros do contrato na hora do faturamento (opcional)
    encargos_pct: Optional[float] = None
    taxa_adm_pct: Optional[float] = None
    imposto_pct: Optional[float] = None

@router.post("/senior/query")
async def run_custom_query(query: SQLQuery, request: Request, db: Session = Depends(get_db)):
    """
    Executa uma query SQL personalizada via API Senior.
    Apenas comandos SELECT são permitidos.
    Restrito a ADMIN e auditado (execução de SQL cru no ERP).
    Envie JSON: {"sql_text": "SELECT ..."}
    """
    user = require_role(request, db, "admin")
    audit(request, "senior.query", entidade="senior",
          detalhe={"sql": (query.sql_text or "")[:500]}, user=user)
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


def _codccu_detalhe(codccu):
    """Resumo do(s) centro(s) de custo pro `detalhe` da auditoria (JSON pequeno).

    String passa direto; lista pequena vai inteira; lista grande vira contagem
    pra não inchar a coluna JSON de audit_logs.
    """
    if codccu is None:
        return None
    if isinstance(codccu, str):
        return codccu
    lista = list(codccu)
    if len(lista) <= 20:
        return lista
    return f"{len(lista)} centros de custo"


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
    request: Request = None,
    periodo: str = Query(..., description="Data no formato YYYY-MM-DD para filtrar competência"),
    codccu: Optional[str] = Query(None, description="Código do centro de custo (opcional)"),
    codcal: Optional[int] = Query(None, description="Código do cálculo (opcional, ex: 362 para folha mensal)"),
    sitafa: Optional[int] = Query(None, description="Situação do funcionário (opcional, ex: 1=Trabalhando)"),
    format: str = Query("detailed", description="Formato: 'detailed' (cada evento) ou 'aggregated' (por funcionário)"),
    db: Session = Depends(get_db)
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

        audit(request, "exportacao.faturamento", entidade="exportacao",
              detalhe={"modelo": f"generico_{format}", "periodo": periodo,
                       "codccu": _codccu_detalhe(codccu), "job": False}, db=db)
        return StreamingResponse(
            BytesIO(excel_bytes),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"'
            }
        )
    except Exception as e:
        audit(request, "exportacao.faturamento", entidade="exportacao",
              detalhe={"modelo": f"generico_{format}", "periodo": periodo,
                       "codccu": _codccu_detalhe(codccu), "job": False,
                       "erro": str(e)[:200]}, db=db, status="erro")
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
    request: Request = None,
    periodo: str = Query(..., description="Data no formato YYYY-MM-DD para filtrar competência"),
    codccu: List[str] = Query(..., description="Código(s) do centro de custo (um ou mais)"),
    db: Session = Depends(get_db)
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

        audit(request, "exportacao.folha", entidade="exportacao",
              detalhe={"modelo": "payroll", "periodo": periodo,
                       "codccu": _codccu_detalhe(codccu), "job": False}, db=db)
        return StreamingResponse(
            BytesIO(excel_bytes),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"'
            }
        )
    except Exception as e:
        audit(request, "exportacao.folha", entidade="exportacao",
              detalhe={"modelo": "payroll", "periodo": periodo,
                       "codccu": _codccu_detalhe(codccu), "job": False,
                       "erro": str(e)[:200]}, db=db, status="erro")
        return {"status": "error", "message": str(e)}


@router.get("/senior/billing/export-femsa")
async def export_billing_femsa(
    request: Request = None,
    periodo: str = Query(..., description="Data no formato YYYY-MM-DD para filtrar competência"),
    codccu: List[str] = Query(..., description="Códigos dos centros de custo"),
    encargos_pct: Optional[float] = None,
    taxa_adm_pct: Optional[float] = None,
    imposto_pct: Optional[float] = None,
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
        # Override de percentuais: só gestor+ (operador usa modelo -> contrato).
        encargos_pct, taxa_adm_pct, imposto_pct = _pct_se_gestor(
            request, db, encargos_pct, taxa_adm_pct, imposto_pct)
        content, filename, media_type = _build_billing_export(
            db, "femsa", periodo, codccu,
            encargos_pct=encargos_pct, taxa_adm_pct=taxa_adm_pct, imposto_pct=imposto_pct,
        )
        audit(request, "exportacao.faturamento", entidade="exportacao",
              detalhe={"modelo": "femsa", "periodo": periodo,
                       "codccu": _codccu_detalhe(codccu), "job": False}, db=db)
        return StreamingResponse(
            BytesIO(content),
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        audit(request, "exportacao.faturamento", entidade="exportacao",
              detalhe={"modelo": "femsa", "periodo": periodo,
                       "codccu": _codccu_detalhe(codccu), "job": False,
                       "erro": str(e)[:200]}, db=db, status="erro")
        return {"status": "error", "message": str(e)}

@router.get("/senior/payroll/export-senior")
async def export_payroll_senior(
    request: Request = None,
    periodo: str = Query(..., description="Data no formato YYYY-MM-DD para filtrar competência"),
    codccu: List[str] = Query(..., description="Códigos dos centros de custo"),
    db: Session = Depends(get_db)
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

        audit(request, "exportacao.folha", entidade="exportacao",
              detalhe={"modelo": "senior", "periodo": periodo,
                       "codccu": _codccu_detalhe(codccu), "job": False}, db=db)
        return StreamingResponse(
            BytesIO(excel_bytes),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"'
            }
        )
    except Exception as e:
        logger.error(f"Erro ao gerar Folha Senior: {e}")
        audit(request, "exportacao.folha", entidade="exportacao",
              detalhe={"modelo": "senior", "periodo": periodo,
                       "codccu": _codccu_detalhe(codccu), "job": False,
                       "erro": str(e)[:200]}, db=db, status="erro")
        return {"status": "error", "message": str(e)}


@router.get("/senior/billing/export-skyrail")
async def export_billing_skyrail(
    request: Request = None,
    periodo: str = Query(..., description="Data no formato YYYY-MM-DD para filtrar competência"),
    codccu: List[str] = Query(..., description="Códigos dos centros de custo"),
    db: Session = Depends(get_db)
):
    """
    Exporta faturamento no formato Skyrail (modelo configurável 'SKYRAIL',
    importado por upload da planilha oficial — renderização por estrutura).
    """
    try:
        content, filename, media_type = _build_billing_export(db, "skyrail", periodo, codccu)
        audit(request, "exportacao.faturamento", entidade="exportacao",
              detalhe={"modelo": "skyrail", "periodo": periodo,
                       "codccu": _codccu_detalhe(codccu), "job": False}, db=db)
        return StreamingResponse(
            BytesIO(content),
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        audit(request, "exportacao.faturamento", entidade="exportacao",
              detalhe={"modelo": "skyrail", "periodo": periodo,
                       "codccu": _codccu_detalhe(codccu), "job": False,
                       "erro": str(e)[:200]}, db=db, status="erro")
        return {"status": "error", "message": str(e)}


# ─── Variantes POST (batch) — para muitos CCUs onde a query string GET ficaria
# grande demais (~32KB limite prático do browser). Mesma lógica, só muda o
# transporte de query string → JSON body.

@router.post("/senior/payroll/export-batch")
async def export_payroll_excel_batch(payload: ExportBatchInput, request: Request = None,
                                     db: Session = Depends(get_db)):
    """POST equivalente a GET /senior/payroll/export. JSON body: {periodo, codccu: [...]}"""
    # request/db são repassados pro handler GET, onde a auditoria registra a exportação.
    return await export_payroll_excel(request=request, periodo=payload.periodo,
                                      codccu=payload.codccu, db=db)


@router.post("/senior/payroll/export-senior-batch")
async def export_payroll_senior_batch(payload: ExportBatchInput, request: Request = None,
                                      db: Session = Depends(get_db)):
    """POST equivalente a GET /senior/payroll/export-senior."""
    return await export_payroll_senior(request=request, periodo=payload.periodo,
                                       codccu=payload.codccu, db=db)


@router.post("/senior/billing/export-batch")
async def export_billing_batch(payload: ExportBatchInput, request: Request = None,
                               db: Session = Depends(get_db)):
    """POST equivalente aos GET /senior/billing/export-{modelo}.
    Body deve incluir `modelo` (femsa | senior | skyrail)."""
    modelo = (payload.modelo or "femsa").lower()
    if modelo == "femsa":
        return await export_billing_femsa(
            request=request,
            periodo=payload.periodo, codccu=payload.codccu,
            encargos_pct=payload.encargos_pct, taxa_adm_pct=payload.taxa_adm_pct,
            imposto_pct=payload.imposto_pct, db=db
        )
    if modelo == "senior":
        return await export_payroll_senior(request=request, periodo=payload.periodo,
                                           codccu=payload.codccu, db=db)
    if modelo == "skyrail":
        return await export_billing_skyrail(request=request, periodo=payload.periodo,
                                            codccu=payload.codccu, db=db)
    return JSONResponse(status_code=400, content={"status": "error", "message": f"modelo desconhecido: {modelo}"})


# ─── Exportação em SEGUNDO PLANO (job) ───────────────────────────────────────
# A exportação faz muitas chamadas SOAP sequenciais à Senior e pode passar de
# 100s, estourando o timeout do proxy (Cloudflare 524). Aqui a requisição volta
# na hora com um job_id; o front consulta o status e baixa quando pronto.

def _pct_se_gestor(request, db, enc, adm, imp):
    """Percentuais digitados na tela só valem para GESTOR ou acima. Para operador
    (ou requisição sem usuário resolvível) o override é ignorado e vale a cadeia
    modelo -> contrato — a fonte da verdade configurada pelo gestor."""
    try:
        from app.services.permissions import get_request_user, has_role
        user = get_request_user(request, db) if (request is not None and db is not None) else None
        if has_role(user, "gestor"):
            return enc, adm, imp
    except Exception:
        pass
    return None, None, None


def _codcals_mensais(db) -> set:
    """Códigos de cálculo (codcal) que compõem o CÁLCULO MENSAL — o recorte
    faturado. Fonte primária: classificação da Conciliação (recorte_mensal=True);
    fallback: env SENIOR_CODCAL_MENSAL. Vazio = sem filtro (comportamento antigo)."""
    try:
        from app.models.codcal_classification import CodcalClassification
        mensais = {
            c.codcal for c in db.query(CodcalClassification)
            .filter(CodcalClassification.recorte_mensal.is_(True)).all()
            if c.codcal is not None
        }
        if mensais:
            return mensais
    except Exception:
        logger.warning("Não foi possível ler codcal_classifications; usando fallback de env.")
    from app.config import SENIOR_CODCAL_MENSAL
    return set(SENIOR_CODCAL_MENSAL or [])


def _build_billing_export(db, modelo, periodo, codccu, encargos_pct=None,
                          taxa_adm_pct=None, imposto_pct=None, progress_cb=None):
    """Monta os bytes da exportação de faturamento. Retorna (content, filename,
    media_type). Suporta modelos femsa (padrão) e senior. Levanta exceção em erro.
    progress_cb(done, total) é repassado ao fetch_payroll para reportar progresso."""
    import re as _re
    from sqlalchemy import or_ as _or
    from app.models.benefit_event import BenefitEvent

    modelo = (modelo or "femsa").lower()
    codccu = deduplicate_codccu(codccu)
    payroll_data = fetch_payroll(periodo, NUMEMP_TELOS, codccu, progress_cb=progress_cb)
    # FATURAMENTO = só o CÁLCULO MENSAL. A integração devolve todos os cálculos da
    # competência (mensal + rescisões/férias/13º/complementares), o que inflava o
    # nº de funcionários. Filtra por codcal do mensal (não afeta as folhas cruas
    # 'senior'/'payroll', que devem mostrar tudo). Sem codcal definido => sem filtro.
    if modelo not in ("senior", "payroll"):
        _mensais = _codcals_mensais(db)
        if _mensais:
            _n0 = len(payroll_data)
            payroll_data = [r for r in payroll_data if r.get("codcal") in _mensais]
            logger.info("Faturamento só-mensal: %d/%d lançamentos mantidos (codcals mensais=%s).",
                        len(payroll_data), _n0, sorted(_mensais))
        else:
            logger.warning("Faturamento: nenhum codcal do mensal definido — export NÃO filtrado. "
                           "Classifique o cálculo mensal em /conciliacao (recorte mensal) ou "
                           "defina SENIOR_CODCAL_MENSAL no .env.")
    all_grouped_data = agrupar_por_matricula(payroll_data)
    codccu_label = "_".join(codccu) if len(codccu) <= 3 else f"{len(codccu)}_ccus"
    xlsx_mt = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    if modelo == "senior":
        excel_bytes = payroll_to_senior_excel_bytes(all_grouped_data, periodo)
        return excel_bytes, generate_senior_filename(periodo, codccu_label), xlsx_mt
    if modelo == "payroll":
        excel_bytes = payroll_to_excel_bytes(all_grouped_data, periodo, codccu_label)
        return excel_bytes, generate_payroll_filename(periodo, codccu_label), xlsx_mt
    # 'skyrail' e demais nomes caem no caminho dos modelos CONFIGURÁVEIS abaixo
    # (resolvidos por nome em billing_models — o SKYRAIL foi importado por upload
    # da planilha oficial e renderiza pela estrutura/fórmulas do layout).
    # Demais modelos (femsa, geral, ou qualquer modelo configurável cadastrado em
    # /modelos-faturamento) usam o formato FEMSA; o que muda são as COLUNAS, resolvidas
    # pelo NOME do modelo escolhido no dropdown da tela (ver colunas_modelo abaixo).

    # ── FEMSA ──
    dt = datetime.strptime(periodo[:7], "%Y-%m")
    ano, mes = dt.year, dt.month
    # Somente exames CONFIRMADOS entram (rascunhos ficam de fora). status NULL = legado.
    exams = db.query(MedicalExam).filter(
        extract('year', MedicalExam.data_exame) == ano,
        extract('month', MedicalExam.data_exame) == mes,
        _or(MedicalExam.status != 'rascunho', MedicalExam.status.is_(None)),
    ).all()

    def _norm_cpf(v):
        d = _re.sub(r"\D", "", str(v or ""))
        return d.zfill(11) if 0 < len(d) <= 11 else d

    exams_by_numcad, exams_by_cpf = {}, {}
    for exam in exams:
        if exam.numcad:
            exams_by_numcad[exam.numcad] = exams_by_numcad.get(exam.numcad, 0) + (exam.total or 0.0)
        if exam.cpf:
            k = _norm_cpf(exam.cpf)
            if k:
                exams_by_cpf[k] = exams_by_cpf.get(k, 0) + (exam.total or 0.0)

    extra_event_map = {
        be.codeve: be.coluna_femsa
        for be in db.query(BenefitEvent).filter(BenefitEvent.ativo.is_(True)).all()
    }

    # MODELO escolhido no dropdown "Modelo de Exportação" da tela, resolvido
    # pelo NOME (femsa | geral | modelos configuráveis). O usuário decide o
    # modelo na hora de exportar — NÃO depende de associação com o contrato.
    from sqlalchemy import func as _func
    from app.models.billing_model import BillingModel
    _bm = (
        db.query(BillingModel)
        .filter(_func.lower(BillingModel.nome) == modelo, BillingModel.ativo.is_(True))
        .first()
    )

    # Contrato = registro de parâmetros (percentuais). order_by(id) garante que
    # sempre pegamos o MESMO contrato que a tela "Salvar padrão" grava, mesmo que
    # exista mais de uma empresa no banco (evita ambiguidade do .first() sem ordem).
    # Percentuais: cadeia de fallback payload -> modelo (padrões do BillingModel,
    # quando não-null) -> contrato. Modelo sem padrão = comportamento de sempre.
    contrato = db.query(Company).order_by(Company.id).first()

    def _resolve_pct(valor_payload, valor_modelo, valor_contrato):
        if valor_payload is not None:
            return valor_payload
        if valor_modelo is not None:
            return valor_modelo
        return valor_contrato

    enc = _resolve_pct(encargos_pct, _bm.encargos_pct if _bm else None,
                       contrato.encargos_pct if contrato else None)
    adm = _resolve_pct(taxa_adm_pct, _bm.taxa_adm_pct if _bm else None,
                       contrato.taxa_adm_pct if contrato else None)
    imp = _resolve_pct(imposto_pct, _bm.imposto_pct if _bm else None,
                       contrato.imposto_pct if contrato else None)

    # Colunas do modelo: nome não encontrado (ou 'femsa') → colunas_modelo=None →
    # billing_to_femsa_excel usa FEMSA_COLUMNS (regressão zero para o FEMSA de hoje).
    import json as _json
    colunas_modelo = None
    if _bm and _bm.colunas:
        colunas_modelo = _bm.colunas
        if isinstance(colunas_modelo, str):
            try:
                colunas_modelo = _json.loads(colunas_modelo)
            except Exception:
                colunas_modelo = None

    # ESTRUTURA do modelo (modelos criados por UPLOAD de planilha): quando o
    # BillingModel tem 'estrutura' (contrato C1), billing_to_femsa_excel usa o
    # renderizador por estrutura (cabeçalhos/fórmulas/constantes da planilha).
    # estrutura NULL => fluxo dirigido por colunas de sempre (regressão zero).
    estrutura_modelo = None
    if _bm and _bm.estrutura:
        estrutura_modelo = _bm.estrutura
        if isinstance(estrutura_modelo, str):
            try:
                estrutura_modelo = _json.loads(estrutura_modelo)
            except Exception:
                estrutura_modelo = None
        if not isinstance(estrutura_modelo, dict):
            estrutura_modelo = None
    # Template SEM PII (opção 2): se o modelo tem o arquivo salvo, a exportação
    # preenche o próprio template do cliente (logo/bordas/formatação perfeitos).
    template_bytes_modelo = getattr(_bm, "arquivo_template", None) if _bm else None

    # Itens CONFIRMADOS (uniformes/EPIs/equipamentos) do período viram custo por
    # funcionário. Agrega valor_total por employee_numcad, separado por categoria.
    # Só pacotes status='confirmado' entram no faturamento (rascunho/validado ficam de fora).
    uniformes_by_numcad: Dict[int, float] = {}
    epis_by_numcad: Dict[int, float] = {}
    equipamentos_by_numcad: Dict[int, float] = {}
    _mapa_por_categoria = {
        "uniforme": uniformes_by_numcad,
        "epi": epis_by_numcad,
        "equipamento": equipamentos_by_numcad,
    }
    confirmed_pkgs = db.query(EpiPurchasePackage).options(
        joinedload(EpiPurchasePackage.items)
    ).filter(
        EpiPurchasePackage.status == 'confirmado',
        extract('year', EpiPurchasePackage.mes_ano) == ano,
        extract('month', EpiPurchasePackage.mes_ano) == mes,
    ).all()
    for pkg in confirmed_pkgs:
        for item in (pkg.items or []):
            if item.employee_numcad is None:
                continue
            # Categoria POR ITEM (pedido misto); linhas antigas sem categoria
            # caem na categoria do pacote.
            cat = (item.categoria or pkg.categoria or "").strip().lower()
            alvo = _mapa_por_categoria.get(cat)
            if alvo is None:
                continue
            alvo[item.employee_numcad] = alvo.get(item.employee_numcad, 0.0) + (item.valor_total or 0.0)

    # TREINAMENTOS (Valor): lançamentos de treinamento do período viram custo por
    # funcionário (valor × quantidade), casando por matrícula (numcad). Período casa
    # por data_treinamento quando existir; senão pela competência 'YYYY-MM'. Sem
    # filtro de CC (igual às demais categorias — o casamento por numcad já restringe).
    treinamentos_by_numcad: Dict[int, float] = {}
    from app.models.training_record import TrainingRecord
    _comp_str = f"{ano}-{mes:02d}"
    for tr in db.query(TrainingRecord).all():
        if tr.employee_numcad is None:
            continue
        if tr.data_treinamento is not None:
            if tr.data_treinamento.year != ano or tr.data_treinamento.month != mes:
                continue
        elif (tr.competencia or "")[:7] != _comp_str:
            continue
        valor = (tr.valor or 0.0) * (tr.quantidade or 1)
        treinamentos_by_numcad[tr.employee_numcad] = (
            treinamentos_by_numcad.get(tr.employee_numcad, 0.0) + valor
        )

    excel_bytes = billing_to_femsa_excel(
        all_grouped_data, periodo, codccu_label,
        exams_data=exams_data_cache,
        exams_by_numcad=exams_by_numcad, exams_by_cpf=exams_by_cpf,
        benefits_data=benefits_data_cache, extra_event_map=extra_event_map,
        encargos_pct=enc, taxa_adm_pct=adm, imposto_pct=imp,
        uniformes_by_numcad=uniformes_by_numcad,
        epis_by_numcad=epis_by_numcad,
        equipamentos_by_numcad=equipamentos_by_numcad,
        treinamentos_by_numcad=treinamentos_by_numcad,
        colunas=colunas_modelo,
        estrutura=estrutura_modelo,
        salario_formula=(_bm.salario_formula if _bm else None),
        campos_config=(_bm.campos_config if _bm else None),
        template_bytes=template_bytes_modelo,
    )
    filename = generate_femsa_filename(periodo, codccu_label)

    epi_packages = db.query(EpiPurchasePackage).options(
        joinedload(EpiPurchasePackage.documents)
    ).filter(
        extract('year', EpiPurchasePackage.mes_ano) == ano,
        extract('month', EpiPurchasePackage.mes_ano) == mes,
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
        return zip_buffer.getvalue(), filename.replace('.xlsx', '_com_epis.zip'), "application/zip"

    return excel_bytes, filename, xlsx_mt


def _run_billing_export_job(job_id, modelo, periodo, codccu, enc, adm, imp):
    """Roda a exportação numa thread separada, com sua própria sessão de banco."""
    from app.db import SessionLocal
    export_jobs.set_running(job_id, "Buscando folha na Senior…")
    db = SessionLocal()
    try:
        n = len(codccu or [])
        export_jobs.set_progress(job_id, 0, n, f"Buscando folha na Senior (0/{n} centros de custo)…")

        def _cb(done, total):
            export_jobs.set_progress(
                job_id, done, total,
                f"Buscando folha na Senior ({done}/{total} centros de custo)…")

        content, filename, media_type = _build_billing_export(
            db, modelo, periodo, codccu, enc, adm, imp, progress_cb=_cb)
        export_jobs.set_progress(job_id, n, n, "Gerando planilha…")
        export_jobs.finish_ok(job_id, content, filename, media_type)
    except Exception as e:
        logger.exception("Erro no job de exportação %s", job_id)
        export_jobs.finish_error(job_id, str(e))
    finally:
        db.close()


@router.post("/senior/billing/export-async")
async def export_billing_async(payload: ExportBatchInput, request: Request = None,
                               db: Session = Depends(get_db)):
    """Inicia a exportação em segundo plano e devolve um job_id na hora."""
    modelo = (payload.modelo or "femsa").lower()
    if not payload.codccu:
        return JSONResponse(status_code=400, content={"success": False, "message": "Selecione pelo menos um centro de custo."})
    # Snapshot de quem enfileirou fica no job — usado na auditoria do download.
    from app.services.permissions import get_request_user as _get_req_user
    _job_user = _get_req_user(request, db) if request is not None else None
    job = export_jobs.create_job(
        descricao=f"{modelo} {payload.periodo}",
        user_id=getattr(_job_user, "id", None),
        username=getattr(_job_user, "username", None) or getattr(_job_user, "email", None),
    )
    # Auditoria no ENFILEIRAMENTO (aqui ainda existem request e usuário; a thread
    # do job roda sem contexto de request). Folha = modelos senior/payroll.
    acao = "exportacao.folha" if modelo in ("senior", "payroll") else "exportacao.faturamento"
    audit(request, acao, entidade="exportacao", entidade_id=job.id,
          detalhe={"modelo": modelo, "periodo": payload.periodo,
                   "codccu": _codccu_detalhe(payload.codccu), "job": True}, db=db)
    # Override de percentuais: só gestor+ (operador usa modelo -> contrato).
    _enc, _adm, _imp = _pct_se_gestor(request, db, payload.encargos_pct,
                                      payload.taxa_adm_pct, payload.imposto_pct)
    threading.Thread(
        target=_run_billing_export_job,
        args=(job.id, modelo, payload.periodo, payload.codccu, _enc, _adm, _imp),
        daemon=True,
    ).start()
    return {"success": True, "job_id": job.id}


@router.get("/senior/billing/export-status/{job_id}")
async def export_billing_status(job_id: str):
    job = export_jobs.get_job(job_id)
    if not job:
        return JSONResponse(status_code=404, content={"success": False, "message": "Job não encontrado (pode ter expirado)."})
    return job.public()


@router.get("/senior/billing/export-download/{job_id}")
async def export_billing_download(job_id: str, request: Request = None,
                                  db: Session = Depends(get_db)):
    job = export_jobs.get_job(job_id)
    if not job:
        return JSONResponse(status_code=404, content={"success": False, "message": "Job não encontrado (pode ter expirado)."})
    if job.status != "done" or job.content is None:
        return JSONResponse(status_code=409, content={"success": False, "message": f"Exportação ainda não concluída (status={job.status}).", "error": job.error})
    # Auditoria do DOWNLOAD em si (quem baixou pode não ser quem enfileirou).
    audit(request, "exportacao.download", entidade="exportacao", entidade_id=job.id,
          detalhe={"arquivo": job.filename, "descricao": job.descricao,
                   "enfileirado_por": job.username}, db=db)
    return StreamingResponse(
        BytesIO(job.content),
        media_type=job.media_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{job.filename}"'},
    )


# ─── Feature 003: administração de cache Senior ──────────────────────────────

from typing import Literal as _Literal
from fastapi.responses import JSONResponse as _JSONResponse
from app.services.senior_cache import (
    ccu_cache as _ccu_cache,
    employees_cache as _employees_cache,
    current_month_key as _current_month_key,
    soap_concurrency_snapshot as _soap_concurrency_snapshot,
)
from app.routers.auth import get_current_user as _get_current_user


class CacheActionInput(BaseModel):
    scope: _Literal["ccu", "employees", "all"] = "all"
    key: Optional[str] = None


def _employees_cache_key(raw_key: str):
    """Compõe a chave do employees_cache. raw_key é o codccu como string."""
    return (str(raw_key).strip(), _current_month_key())


@router.post("/senior/cache/invalidate")
async def invalidate_cache(data: CacheActionInput, user=Depends(_get_current_user)):
    """Limpa entradas do cache sem buscar novos dados. Feature 003."""
    if user is None:
        return _JSONResponse(status_code=401, content={"status": "error", "message": "Não autenticado"})

    removed = {"ccu": 0, "employees": 0}
    if data.scope in ("ccu", "all"):
        if data.key is not None:
            try:
                key_int = int(data.key)
                removed["ccu"] = _ccu_cache.invalidate(key_int)
            except ValueError:
                return _JSONResponse(status_code=400, content={"status": "error", "message": "key inválida para scope=ccu (esperado inteiro = numEmp)"})
        else:
            removed["ccu"] = _ccu_cache.invalidate()
    if data.scope in ("employees", "all"):
        if data.key is not None:
            removed["employees"] = _employees_cache.invalidate(_employees_cache_key(data.key))
        else:
            removed["employees"] = _employees_cache.invalidate()
    return {"status": "ok", "scope": data.scope, "removed": removed}


@router.post("/senior/cache/refresh")
async def refresh_cache(data: CacheActionInput, user=Depends(_get_current_user)):
    """Limpa e re-popula buscando dados frescos da Senior. Feature 003."""
    if user is None:
        return _JSONResponse(status_code=401, content={"status": "error", "message": "Não autenticado"})

    refreshed = {"ccu": None, "employees": None}

    try:
        if data.scope in ("ccu", "all"):
            if data.key is not None:
                try:
                    numemp = int(data.key)
                except ValueError:
                    return _JSONResponse(status_code=400, content={"status": "error", "message": "key inválida para scope=ccu (esperado inteiro)"})
                _ccu_cache.invalidate(numemp)
                centers = fetch_cost_centers(numemp)
            else:
                _ccu_cache.invalidate()
                centers = fetch_all_cost_centers()
            refreshed["ccu"] = {"count": len(centers), "sample": centers[:3]}

        if data.scope == "employees":
            if not data.key:
                return _JSONResponse(
                    status_code=400,
                    content={"status": "error", "message": "scope=employees requer key=codccu (revalidar todos os CCUs é caro)"},
                )
            cache_key = _employees_cache_key(data.key)
            _employees_cache.invalidate(cache_key)
            from app.services.senior_connector import fetch_active_employees
            emps = fetch_active_employees(data.key)
            refreshed["employees"] = {"count": len(emps), "codccu": data.key, "sample": emps[:3]}
    except Exception as e:
        return _JSONResponse(
            status_code=503,
            content={"status": "error", "message": f"Falha ao buscar Senior: {e}"},
        )

    return {"status": "ok", "scope": data.scope, "refreshed": refreshed}


@router.get("/senior/cache/stats")
async def cache_stats(user=Depends(_get_current_user)):
    """Snapshot informativo dos caches. Feature 003."""
    if user is None:
        return _JSONResponse(status_code=401, content={"status": "error", "message": "Não autenticado"})
    return {
        "status": "ok",
        "ccu": _ccu_cache.stats(),
        "employees": _employees_cache.stats(),
        "soap_concurrency": _soap_concurrency_snapshot(),
    }
