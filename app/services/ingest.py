from fastapi import UploadFile
import pandas as pd
import json
import re
from io import BytesIO
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session

from app.models.employee import Employee
from app.models.benefit_record import BenefitRecord
from app.models.time_record import TimeRecord
from app.models.exam_record import ExamRecord


EMPLOYEE_COLUMN_MAPPING = {
    "cpf": ["cpf", "CPF", "Cpf", "documento", "DOCUMENTO"],
    "nome": ["nome", "NOME", "Nome", "nome_funcionario", "NOME_FUNCIONARIO", "funcionario", "FUNCIONARIO", "name", "NAME"],
    "matricula": ["matricula", "MATRICULA", "Matricula", "mat", "MAT", "codigo", "CODIGO"],
    "centro_custo": ["centro_custo", "CENTRO_CUSTO", "CentroCusto", "cc", "CC", "centro custo"],
    "cargo": ["cargo", "CARGO", "Cargo", "funcao", "FUNCAO", "Funcao"],
    "departamento": ["departamento", "DEPARTAMENTO", "Departamento", "depto", "DEPTO", "setor", "SETOR"],
    "data_admissao": ["data_admissao", "DATA_ADMISSAO", "DataAdmissao", "admissao", "ADMISSAO", "dt_admissao"],
    "data_nascimento": ["data_nascimento", "DATA_NASCIMENTO", "DataNascimento", "nascimento", "NASCIMENTO", "dt_nascimento"],
    "email": ["email", "EMAIL", "Email", "e-mail", "E-MAIL"],
    "telefone": ["telefone", "TELEFONE", "Telefone", "tel", "TEL", "celular", "CELULAR", "fone", "FONE"],
    "endereco": ["endereco", "ENDERECO", "Endereco", "logradouro", "LOGRADOURO"],
    "cidade": ["cidade", "CIDADE", "Cidade", "municipio", "MUNICIPIO"],
    "estado": ["estado", "ESTADO", "Estado", "uf", "UF"],
    "cep": ["cep", "CEP", "Cep", "codigo_postal", "CODIGO_POSTAL"],
    "status": ["status", "STATUS", "Status", "situacao", "SITUACAO"],
}

BENEFIT_COLUMN_MAPPING = {
    "cpf": ["cpf", "CPF", "Cpf", "documento", "DOCUMENTO"],
    "matricula": ["matricula", "MATRICULA", "Matricula", "mat", "MAT"],
    "tipo_beneficio": ["tipo_beneficio", "TIPO_BENEFICIO", "TipoBeneficio", "beneficio", "BENEFICIO", "tipo", "TIPO"],
    "descricao": ["descricao", "DESCRICAO", "Descricao", "desc", "DESC"],
    "valor": ["valor", "VALOR", "Valor", "vlr", "VLR", "value", "VALUE"],
    "quantidade": ["quantidade", "QUANTIDADE", "Quantidade", "qtd", "QTD", "qtde", "QTDE"],
    "valor_total": ["valor_total", "VALOR_TOTAL", "ValorTotal", "total", "TOTAL"],
    "status": ["status", "STATUS", "Status"],
    "observacoes": ["observacoes", "OBSERVACOES", "Observacoes", "obs", "OBS"],
}

TIME_COLUMN_MAPPING = {
    "cpf": ["cpf", "CPF", "Cpf", "documento", "DOCUMENTO"],
    "matricula": ["matricula", "MATRICULA", "Matricula", "mat", "MAT"],
    "data": ["data", "DATA", "Data", "dt", "DT", "date", "DATE"],
    "horas_trabalhadas": ["horas_trabalhadas", "HORAS_TRABALHADAS", "HorasTrabalhadas", "horas", "HORAS", "ht", "HT"],
    "horas_extras": ["horas_extras", "HORAS_EXTRAS", "HorasExtras", "he", "HE", "extras", "EXTRAS"],
    "horas_noturnas": ["horas_noturnas", "HORAS_NOTURNAS", "HorasNoturnas", "hn", "HN", "noturno", "NOTURNO"],
    "faltas": ["faltas", "FALTAS", "Faltas", "falta", "FALTA"],
    "atrasos": ["atrasos", "ATRASOS", "Atrasos", "atraso", "ATRASO"],
    "adicional_noturno": ["adicional_noturno", "ADICIONAL_NOTURNO", "AdicionalNoturno", "ad_noturno", "AD_NOTURNO"],
    "dsr": ["dsr", "DSR", "Dsr", "descanso_semanal", "DESCANSO_SEMANAL"],
    "banco_horas": ["banco_horas", "BANCO_HORAS", "BancoHoras", "bh", "BH"],
    "observacoes": ["observacoes", "OBSERVACOES", "Observacoes", "obs", "OBS"],
}

EXAM_COLUMN_MAPPING = {
    "cpf": ["cpf", "CPF", "Cpf", "documento", "DOCUMENTO"],
    "matricula": ["matricula", "MATRICULA", "Matricula", "mat", "MAT"],
    "tipo_exame": ["tipo_exame", "TIPO_EXAME", "TipoExame", "exame", "EXAME", "tipo", "TIPO"],
    "data_exame": ["data_exame", "DATA_EXAME", "DataExame", "data", "DATA", "dt_exame", "DT_EXAME"],
    "data_validade": ["data_validade", "DATA_VALIDADE", "DataValidade", "validade", "VALIDADE", "dt_validade", "DT_VALIDADE"],
    "status": ["status", "STATUS", "Status", "situacao", "SITUACAO"],
    "resultado": ["resultado", "RESULTADO", "Resultado", "result", "RESULT"],
    "clinica": ["clinica", "CLINICA", "Clinica", "prestador", "PRESTADOR"],
    "medico": ["medico", "MEDICO", "Medico", "dr", "DR"],
    "crm": ["crm", "CRM", "Crm"],
    "observacoes": ["observacoes", "OBSERVACOES", "Observacoes", "obs", "OBS"],
}


def normalize_cpf(cpf: str) -> str:
    if not cpf:
        return ""
    cpf_str = str(cpf)
    cpf_clean = re.sub(r'\D', '', cpf_str)
    return cpf_clean.zfill(11) if len(cpf_clean) <= 11 else cpf_clean


def find_column(df_columns: List[str], mapping_list: List[str]) -> Optional[str]:
    for col in df_columns:
        col_clean = str(col).strip()
        if col_clean in mapping_list:
            return col
    return None


def map_columns(df: pd.DataFrame, column_mapping: Dict[str, List[str]]) -> Dict[str, Optional[str]]:
    df_columns = list(df.columns)
    mapped = {}
    for field, alternatives in column_mapping.items():
        mapped[field] = find_column(df_columns, alternatives)
    return mapped


def get_value(row: pd.Series, column_name: Optional[str], default: Any = None) -> Any:
    if column_name is None:
        return default
    value = row.get(column_name)
    if pd.isna(value) or value == '':
        return default
    return value


async def process_file_upload(file: UploadFile) -> List[Dict[str, Any]]:
    content = await file.read()
    
    file_extension = file.filename.split('.')[-1].lower() if file.filename else ''
    
    if file_extension in ['xlsx', 'xls']:
        return await process_excel_file(content)
    elif file_extension == 'csv':
        return await process_csv_file(content)
    elif file_extension == 'json':
        return await process_json_file(content)
    else:
        raise ValueError(f"Formato de arquivo não suportado: {file_extension}")


async def process_excel_file(content: bytes) -> List[Dict[str, Any]]:
    df = pd.read_excel(BytesIO(content))
    df = df.fillna('')
    return df.to_dict('records')


async def process_csv_file(content: bytes) -> List[Dict[str, Any]]:
    try:
        df = pd.read_csv(BytesIO(content), encoding='utf-8')
    except:
        df = pd.read_csv(BytesIO(content), encoding='latin-1')
    df = df.fillna('')
    return df.to_dict('records')


async def process_json_file(content: bytes) -> List[Dict[str, Any]]:
    data = json.loads(content.decode('utf-8'))
    
    if isinstance(data, list):
        return data
    elif isinstance(data, dict):
        return [data]
    else:
        raise ValueError("Arquivo JSON deve conter uma lista ou dicionário")


def ingest_employees(db: Session, customer_id: int, file_content: bytes, file_extension: str) -> Dict[str, Any]:
    if file_extension in ['xlsx', 'xls']:
        df = pd.read_excel(BytesIO(file_content))
    else:
        try:
            df = pd.read_csv(BytesIO(file_content), encoding='utf-8')
        except:
            df = pd.read_csv(BytesIO(file_content), encoding='latin-1')
    
    df = df.fillna('')
    column_map = map_columns(df, EMPLOYEE_COLUMN_MAPPING)
    
    inserted = 0
    updated = 0
    errors = []
    
    for idx, row in df.iterrows():
        try:
            cpf_raw = get_value(row, column_map['cpf'], '')
            cpf = normalize_cpf(cpf_raw)
            nome = get_value(row, column_map['nome'], '')
            
            if not cpf or not nome:
                errors.append(f"Linha {idx + 2}: CPF ou Nome ausente")
                continue
            
            existing = db.query(Employee).filter(
                Employee.customer_id == customer_id,
                Employee.cpf == cpf
            ).first()
            
            employee_data = {
                "customer_id": customer_id,
                "cpf": cpf,
                "nome": str(nome),
                "matricula": str(get_value(row, column_map['matricula'], '')),
                "centro_custo": str(get_value(row, column_map['centro_custo'], '')),
                "cargo": str(get_value(row, column_map['cargo'], '')),
                "departamento": str(get_value(row, column_map['departamento'], '')),
                "data_admissao": str(get_value(row, column_map['data_admissao'], '')),
                "data_nascimento": str(get_value(row, column_map['data_nascimento'], '')),
                "email": str(get_value(row, column_map['email'], '')),
                "telefone": str(get_value(row, column_map['telefone'], '')),
                "endereco": str(get_value(row, column_map['endereco'], '')),
                "cidade": str(get_value(row, column_map['cidade'], '')),
                "estado": str(get_value(row, column_map['estado'], '')),
                "cep": str(get_value(row, column_map['cep'], '')),
                "status": str(get_value(row, column_map['status'], 'ativo')),
            }
            
            if existing:
                for key, value in employee_data.items():
                    if key != 'customer_id':
                        setattr(existing, key, value)
                updated += 1
            else:
                new_employee = Employee(**employee_data)
                db.add(new_employee)
                inserted += 1
                
        except Exception as e:
            errors.append(f"Linha {idx + 2}: {str(e)}")
    
    db.commit()
    
    return {
        "total_rows": len(df),
        "inserted": inserted,
        "updated": updated,
        "errors": errors,
        "columns_mapped": {k: v for k, v in column_map.items() if v is not None}
    }


def find_employee_by_cpf_or_matricula(db: Session, customer_id: int, cpf: str, matricula: str) -> Optional[Employee]:
    cpf_normalized = normalize_cpf(cpf) if cpf else None
    
    if cpf_normalized:
        employee = db.query(Employee).filter(
            Employee.customer_id == customer_id,
            Employee.cpf == cpf_normalized
        ).first()
        if employee:
            return employee
    
    if matricula:
        employee = db.query(Employee).filter(
            Employee.customer_id == customer_id,
            Employee.matricula == str(matricula)
        ).first()
        if employee:
            return employee
    
    return None


def ingest_benefits(db: Session, customer_id: int, mes_referencia: str, file_content: bytes, file_extension: str) -> Dict[str, Any]:
    if file_extension in ['xlsx', 'xls']:
        df = pd.read_excel(BytesIO(file_content))
    else:
        try:
            df = pd.read_csv(BytesIO(file_content), encoding='utf-8')
        except:
            df = pd.read_csv(BytesIO(file_content), encoding='latin-1')
    
    df = df.fillna('')
    column_map = map_columns(df, BENEFIT_COLUMN_MAPPING)
    
    inserted = 0
    skipped = 0
    errors = []
    
    for idx, row in df.iterrows():
        try:
            cpf_raw = get_value(row, column_map['cpf'], '')
            matricula = get_value(row, column_map['matricula'], '')
            
            employee = find_employee_by_cpf_or_matricula(db, customer_id, str(cpf_raw), str(matricula))
            
            if not employee:
                errors.append(f"Linha {idx + 2}: Funcionário não encontrado (CPF: {cpf_raw}, Mat: {matricula})")
                skipped += 1
                continue
            
            tipo_beneficio = get_value(row, column_map['tipo_beneficio'], '')
            if not tipo_beneficio:
                errors.append(f"Linha {idx + 2}: Tipo de benefício ausente")
                skipped += 1
                continue
            
            valor = get_value(row, column_map['valor'], 0)
            quantidade = get_value(row, column_map['quantidade'], 1)
            valor_total = get_value(row, column_map['valor_total'], 0)
            
            try:
                valor = float(valor) if valor else 0.0
            except:
                valor = 0.0
            try:
                quantidade = float(quantidade) if quantidade else 1.0
            except:
                quantidade = 1.0
            try:
                valor_total = float(valor_total) if valor_total else valor * quantidade
            except:
                valor_total = valor * quantidade
            
            benefit = BenefitRecord(
                customer_id=customer_id,
                employee_id=employee.id,
                mes_referencia=mes_referencia,
                tipo_beneficio=str(tipo_beneficio),
                descricao=str(get_value(row, column_map['descricao'], '')),
                valor=valor,
                quantidade=quantidade,
                valor_total=valor_total,
                status=str(get_value(row, column_map['status'], 'ativo')),
                observacoes=str(get_value(row, column_map['observacoes'], '')),
            )
            db.add(benefit)
            inserted += 1
            
        except Exception as e:
            errors.append(f"Linha {idx + 2}: {str(e)}")
    
    db.commit()
    
    return {
        "total_rows": len(df),
        "inserted": inserted,
        "skipped": skipped,
        "errors": errors,
        "columns_mapped": {k: v for k, v in column_map.items() if v is not None}
    }


def ingest_time_records(db: Session, customer_id: int, mes_referencia: str, file_content: bytes, file_extension: str) -> Dict[str, Any]:
    if file_extension in ['xlsx', 'xls']:
        df = pd.read_excel(BytesIO(file_content))
    else:
        try:
            df = pd.read_csv(BytesIO(file_content), encoding='utf-8')
        except:
            df = pd.read_csv(BytesIO(file_content), encoding='latin-1')
    
    df = df.fillna('')
    column_map = map_columns(df, TIME_COLUMN_MAPPING)
    
    inserted = 0
    skipped = 0
    errors = []
    
    for idx, row in df.iterrows():
        try:
            cpf_raw = get_value(row, column_map['cpf'], '')
            matricula = get_value(row, column_map['matricula'], '')
            
            employee = find_employee_by_cpf_or_matricula(db, customer_id, str(cpf_raw), str(matricula))
            
            if not employee:
                errors.append(f"Linha {idx + 2}: Funcionário não encontrado (CPF: {cpf_raw}, Mat: {matricula})")
                skipped += 1
                continue
            
            data = get_value(row, column_map['data'], '')
            
            def to_float(val, default=0.0):
                try:
                    return float(val) if val else default
                except:
                    return default
            
            time_record = TimeRecord(
                customer_id=customer_id,
                employee_id=employee.id,
                mes_referencia=mes_referencia,
                data=str(data),
                horas_trabalhadas=to_float(get_value(row, column_map['horas_trabalhadas'], 0)),
                horas_extras=to_float(get_value(row, column_map['horas_extras'], 0)),
                horas_noturnas=to_float(get_value(row, column_map['horas_noturnas'], 0)),
                faltas=to_float(get_value(row, column_map['faltas'], 0)),
                atrasos=to_float(get_value(row, column_map['atrasos'], 0)),
                adicional_noturno=to_float(get_value(row, column_map['adicional_noturno'], 0)),
                dsr=to_float(get_value(row, column_map['dsr'], 0)),
                banco_horas=to_float(get_value(row, column_map['banco_horas'], 0)),
                observacoes=str(get_value(row, column_map['observacoes'], '')),
            )
            db.add(time_record)
            inserted += 1
            
        except Exception as e:
            errors.append(f"Linha {idx + 2}: {str(e)}")
    
    db.commit()
    
    return {
        "total_rows": len(df),
        "inserted": inserted,
        "skipped": skipped,
        "errors": errors,
        "columns_mapped": {k: v for k, v in column_map.items() if v is not None}
    }


def ingest_exam_records(db: Session, customer_id: int, file_content: bytes, file_extension: str) -> Dict[str, Any]:
    if file_extension in ['xlsx', 'xls']:
        df = pd.read_excel(BytesIO(file_content))
    else:
        try:
            df = pd.read_csv(BytesIO(file_content), encoding='utf-8')
        except:
            df = pd.read_csv(BytesIO(file_content), encoding='latin-1')
    
    df = df.fillna('')
    column_map = map_columns(df, EXAM_COLUMN_MAPPING)
    
    inserted = 0
    skipped = 0
    errors = []
    
    for idx, row in df.iterrows():
        try:
            cpf_raw = get_value(row, column_map['cpf'], '')
            matricula = get_value(row, column_map['matricula'], '')
            
            employee = find_employee_by_cpf_or_matricula(db, customer_id, str(cpf_raw), str(matricula))
            
            if not employee:
                errors.append(f"Linha {idx + 2}: Funcionário não encontrado (CPF: {cpf_raw}, Mat: {matricula})")
                skipped += 1
                continue
            
            tipo_exame = get_value(row, column_map['tipo_exame'], '')
            data_exame = get_value(row, column_map['data_exame'], '')
            
            if not tipo_exame or not data_exame:
                errors.append(f"Linha {idx + 2}: Tipo de exame ou data ausente")
                skipped += 1
                continue
            
            exam_record = ExamRecord(
                customer_id=customer_id,
                employee_id=employee.id,
                tipo_exame=str(tipo_exame),
                data_exame=str(data_exame),
                data_validade=str(get_value(row, column_map['data_validade'], '')),
                status=str(get_value(row, column_map['status'], 'pendente')),
                resultado=str(get_value(row, column_map['resultado'], '')),
                clinica=str(get_value(row, column_map['clinica'], '')),
                medico=str(get_value(row, column_map['medico'], '')),
                crm=str(get_value(row, column_map['crm'], '')),
                observacoes=str(get_value(row, column_map['observacoes'], '')),
            )
            db.add(exam_record)
            inserted += 1
            
        except Exception as e:
            errors.append(f"Linha {idx + 2}: {str(e)}")
    
    db.commit()
    
    return {
        "total_rows": len(df),
        "inserted": inserted,
        "skipped": skipped,
        "errors": errors,
        "columns_mapped": {k: v for k, v in column_map.items() if v is not None}
    }


async def fetch_from_api(api_endpoint: str, api_key: str = None, headers: Dict[str, str] = None) -> List[Dict[str, Any]]:
    import aiohttp
    
    default_headers = headers or {}
    if api_key:
        default_headers['Authorization'] = f'Bearer {api_key}'
    
    async with aiohttp.ClientSession() as session:
        async with session.get(api_endpoint, headers=default_headers) as response:
            if response.status == 200:
                data = await response.json()
                if isinstance(data, list):
                    return data
                elif isinstance(data, dict):
                    return [data]
                else:
                    return []
            else:
                raise Exception(f"Requisição API falhou com status {response.status}")
