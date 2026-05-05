import pandas as pd
from io import BytesIO
from datetime import datetime
from typing import Dict, List, Tuple, Any, Optional
from sqlalchemy.orm import Session
from app.models.billing import (
    Company, Unit, BillingEmployee, EmploymentContract, 
    BillingPeriod, PayrollItemType, PayrollItem, BillingExamRecord,
    BillingStatus
)
import re


# Colunas que compõem a base de remuneração para cálculo de encargos sociais.
# Os nomes devem corresponder exatamente às colunas do relatório FEMSA gerado.
REMUNERACAO_BASE_COLUMNS: List[str] = [
    "SALARIO DIA (Valor)",
    "HORAS EXTRAS (Valor)",
    "ADICIONAL NOTURNO (Valor)",
    "ADIC. PERICULOSIDADE",
    "REMUNERACAO VARIAVEL MENSAL",
    "PREMIO/BONUS",
    "REEMB. VALE REFEICAO INDEVIDO/DEVOLVIDO",
    "REEMB. DESPESAS KM/ESTAC/PEDAGIO",
    "REEMB. DESC.DE FALTAS/ATRASOS/D.S.R. (HS) (Valor)",
    "LICENCA PATERNIDADE (Valor)",
    "ATESTADO MEDICO DIA (Valor)",
    "AUXILIO DOENÇA (Valor)",
    "D.S.R. INTEGRACAO S/ ADICIONAL NOTURNO",
    "D.S.R. INTEGRACAO S/ HORA EXTRA",
    "D.S.R. INTEGRACAO S/ VARIAVEL/PREMIO",
    "ADICIONAL NOTURNO  - MES ANTERIOR (Valor)",  # duplo espaço conforme FEMSA_COLUMNS
    "VARIAVEL MÊS ANTERIOR",
    "HORA EXTRA - MES ANTERIOR (valor)",
    "INTERJORNADA MES ANTERIOR (Valor)",
    "DIFERENCA DE SALARIO",
    "REEMB. D.S.R. S/FALTAS (DIA) (Valor)",
    "SALDO SALARIO DIA RESCISAO (Valor)",
    "Pensão Judicial",
]

# Alíquota de encargos sociais aplicada sobre o Total Remuneração (57,91%)
ENCARGOS_SOCIAIS_RATE: float = 0.5791


def calcular_totais_remuneracao(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcula as colunas derivadas para cada linha do relatório:

    - **Total Remuneração**: somatório de REMUNERACAO_BASE_COLUMNS presentes no DataFrame.
      Colunas ausentes ou com valores nulos/vazios são tratadas como zero.
    - **Encargos Sociais**: Total Remuneração * ENCARGOS_SOCIAIS_RATE (57,91%).

    As colunas são sobrescritas caso já existam, garantindo idempotência.
    """
    cols_presentes = [col for col in REMUNERACAO_BASE_COLUMNS if col in df.columns]

    if cols_presentes:
        df["Total Remuneração"] = (
            df[cols_presentes]
            .apply(pd.to_numeric, errors="coerce")
            .fillna(0.0)
            .sum(axis=1)
        )
    else:
        df["Total Remuneração"] = 0.0

    df["Encargos Sociais"] = (df["Total Remuneração"] * ENCARGOS_SOCIAIS_RATE).round(2)

    return df


PAYROLL_COLUMN_MAPPINGS = {
    "SALARIO_DIA": {
        "qty_columns": ["SALARIO DIA (Qtde)", "SALARIO_DIA_QTDE", "salario_dia_qtde"],
        "value_columns": ["SALARIO DIA (Valor)", "SALARIO_DIA_VALOR", "salario_dia_valor"]
    },
    "HORA_EXTRA": {
        "qty_columns": ["HORAS EXTRAS (Qtde)", "HORAS_EXTRAS_QTDE", "horas_extras_qtde"],
        "value_columns": ["HORAS EXTRAS (Valor)", "HORAS_EXTRAS_VALOR", "horas_extras_valor"]
    },
    "VALE_TRANSPORTE": {
        "qty_columns": ["PAGTO. VALE-TRANSPORTE (Qtde)", "VALE_TRANSPORTE_QTDE", "vt_qtde"],
        "value_columns": ["PAGTO. VALE-TRANSPORTE (Valor)", "VALE_TRANSPORTE_VALOR", "vt_valor"]
    },
    "VALE_REFEICAO": {
        "qty_columns": ["PAGTO. VALE REFEICAO (Qtde)", "VALE_REFEICAO_QTDE", "vr_qtde"],
        "value_columns": ["PAGTO. VALE REFEICAO (Valor)", "VALE_REFEICAO_VALOR", "vr_valor"]
    },
    "PREMIO_BONUS": {
        "qty_columns": [],
        "value_columns": ["PREMIO/BONUS", "PREMIO_BONUS", "premio_bonus", "BONUS"]
    },
    "TRIBUTO_VALOR": {
        "qty_columns": [],
        "value_columns": ["TRIBUTOS (VALOR)", "TRIBUTOS_VALOR", "tributos_valor", "TRIBUTOS"]
    },
    "ENCARGO_VALOR": {
        "qty_columns": [],
        "value_columns": ["ENCARGOS (VALOR)", "ENCARGOS_VALOR", "encargos_valor", "ENCARGOS"]
    },
    "TAXA_FATURAMENTO": {
        "qty_columns": [],
        "value_columns": ["TAXA FATURAMENTO (VALOR)", "TAXA_FATURAMENTO_VALOR", "taxa_faturamento"]
    }
}

EMPLOYEE_COLUMN_MAPPINGS = {
    "cpf": ["CPF", "cpf", "CPF Funcionário", "cpf_funcionario", "DOCUMENTO"],
    "nome": ["Nome", "NOME", "Nome Funcionário", "nome_funcionario", "FUNCIONARIO"],
    "cargo": ["Cargo", "CARGO", "cargo"],
    "funcao": ["Função", "FUNCAO", "funcao", "FUNÇÃO"],
    "salario_base": ["Salário Base", "SALARIO_BASE", "salario_base", "SALARIO"],
    "data_admissao": ["Data Admissão", "DATA_ADMISSAO", "data_admissao", "Dt.Admissão"],
    "data_demissao": ["Data Demissão", "DATA_DEMISSAO", "data_demissao", "Dt.Demissão"],
}

COMPANY_COLUMN_MAPPINGS = {
    "cnpj_femsa": ["CNPJ FEMSA", "cnpj_femsa", "CNPJ_FEMSA", "CNPJ"],
    "name": ["Empresa", "EMPRESA", "empresa", "Nome Empresa", "RAZAO_SOCIAL"],
}

UNIT_COLUMN_MAPPINGS = {
    "cnpj_unidade": ["CNPJ Unidade", "cnpj_unidade", "CNPJ_UNIDADE", "CNPJ da Unidade"],
    "nome_unidade": ["Nome Unidade", "nome_unidade", "NOME_UNIDADE", "Unidade"],
    "centro_custo_femsa": ["Centro Custo FEMSA", "centro_custo_femsa", "CENTRO_CUSTO", "CC FEMSA"],
}

EXAM_COLUMN_MAPPINGS = {
    "cnpj_unidade": ["CNPJ da Unidade", "CNPJ Unidade", "cnpj_unidade", "CNPJ_UNIDADE"],
    "cpf": ["CPF", "cpf", "CPF Funcionário"],
    "nome": ["Nome", "NOME", "Nome Funcionário"],
    "tipo": ["Tipo", "TIPO", "tipo_exame"],
    "exame": ["Exame", "EXAME", "nome_exame"],
    "data_pedido": ["Dt.Pedido", "Data Pedido", "data_pedido", "DATA_PEDIDO"],
    "data_exame": ["Dt.Exame", "Data Exame", "data_exame", "DATA_EXAME"],
    "data_inativacao": ["Dt.Inativação", "Data Inativação", "data_inativacao"],
    "valor_cobrar": ["Vl.Cobrar R$", "Valor Cobrar", "valor_cobrar", "VALOR_COBRAR"],
}


def normalize_cpf(cpf: str) -> str:
    if not cpf:
        return ""
    cpf_str = str(cpf).strip()
    cpf_clean = re.sub(r'[^\d]', '', cpf_str)
    return cpf_clean.zfill(11) if len(cpf_clean) <= 11 else cpf_clean


def normalize_cnpj(cnpj: str) -> str:
    if not cnpj:
        return ""
    cnpj_str = str(cnpj).strip()
    cnpj_clean = re.sub(r'[^\d]', '', cnpj_str)
    return cnpj_clean.zfill(14) if len(cnpj_clean) <= 14 else cnpj_clean


def find_column(df: pd.DataFrame, column_options: List[str]) -> Optional[str]:
    for col in column_options:
        if col in df.columns:
            return col
    return None


def safe_float(value) -> float:
    if pd.isna(value) or value == "" or value is None:
        return 0.0
    try:
        if isinstance(value, str):
            value = value.replace(",", ".").replace("R$", "").strip()
        return float(value)
    except (ValueError, TypeError):
        return 0.0


def parse_date(value) -> Optional[datetime]:
    if pd.isna(value) or value == "" or value is None:
        return None
    try:
        if isinstance(value, datetime):
            return value
        if isinstance(value, pd.Timestamp):
            return value.to_pydatetime()
        if isinstance(value, str):
            for fmt in ["%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y"]:
                try:
                    return datetime.strptime(value.strip(), fmt)
                except ValueError:
                    continue
        return None
    except:
        return None


def get_or_create_company(db: Session, cnpj: str, name: str) -> Company:
    cnpj_normalized = normalize_cnpj(cnpj)
    company = db.query(Company).filter(Company.cnpj_femsa == cnpj_normalized).first()
    if not company:
        company = Company(cnpj_femsa=cnpj_normalized, name=name or f"Empresa {cnpj_normalized}")
        db.add(company)
        db.flush()
    return company


def get_or_create_unit(db: Session, company_id: int, cnpj: str, nome: str, centro_custo: str = None) -> Unit:
    cnpj_normalized = normalize_cnpj(cnpj)
    unit = db.query(Unit).filter(Unit.cnpj_unidade == cnpj_normalized).first()
    if not unit:
        unit = Unit(
            company_id=company_id,
            cnpj_unidade=cnpj_normalized,
            nome_unidade=nome or f"Unidade {cnpj_normalized}",
            centro_custo_femsa=centro_custo
        )
        db.add(unit)
        db.flush()
    return unit


def get_or_create_employee(db: Session, cpf: str, nome: str) -> BillingEmployee:
    cpf_normalized = normalize_cpf(cpf)
    employee = db.query(BillingEmployee).filter(BillingEmployee.cpf == cpf_normalized).first()
    if not employee:
        employee = BillingEmployee(cpf=cpf_normalized, nome=nome or f"Funcionário {cpf_normalized}")
        db.add(employee)
        db.flush()
    return employee


def get_or_create_contract(
    db: Session, 
    employee_id: int, 
    company_id: int, 
    unit_id: int,
    cargo: str = None,
    funcao: str = None,
    salario_base: float = 0.0,
    data_admissao: datetime = None
) -> EmploymentContract:
    contract = db.query(EmploymentContract).filter(
        EmploymentContract.employee_id == employee_id,
        EmploymentContract.company_id == company_id,
        EmploymentContract.unit_id == unit_id
    ).first()
    
    if not contract:
        contract = EmploymentContract(
            employee_id=employee_id,
            company_id=company_id,
            unit_id=unit_id,
            cargo=cargo,
            funcao=funcao,
            salario_base=salario_base,
            data_admissao=data_admissao
        )
        db.add(contract)
        db.flush()
    return contract


def get_or_create_billing_period(db: Session, company_id: int, mes_referencia: str) -> BillingPeriod:
    period = db.query(BillingPeriod).filter(
        BillingPeriod.company_id == company_id,
        BillingPeriod.mes_referencia == mes_referencia
    ).first()
    
    if not period:
        period = BillingPeriod(
            company_id=company_id,
            mes_referencia=mes_referencia,
            status=BillingStatus.PROCESSING
        )
        db.add(period)
        db.flush()
    return period


def get_payroll_item_type(db: Session, code: str) -> Optional[PayrollItemType]:
    return db.query(PayrollItemType).filter(PayrollItemType.code == code).first()


def read_file_to_dataframe(file_content: bytes, filename: str, sheet_index: int = 0) -> pd.DataFrame:
    if filename.lower().endswith('.csv'):
        for encoding in ['utf-8', 'latin1', 'iso-8859-1', 'cp1252']:
            try:
                return pd.read_csv(BytesIO(file_content), encoding=encoding)
            except:
                continue
        raise ValueError("Não foi possível ler o arquivo CSV")
    else:
        return pd.read_excel(BytesIO(file_content), sheet_name=sheet_index)


def process_payroll_upload(
    db: Session, 
    file_content: bytes, 
    filename: str,
    mes_referencia: str
) -> Dict[str, Any]:
    result = {
        "success": True,
        "companies_created": 0,
        "units_created": 0,
        "employees_created": 0,
        "contracts_created": 0,
        "payroll_items_created": 0,
        "rows_processed": 0,
        "errors": [],
        "mapped_columns": []
    }
    
    try:
        df = read_file_to_dataframe(file_content, filename, sheet_index=0)
        result["rows_processed"] = len(df)
        
        cpf_col = find_column(df, EMPLOYEE_COLUMN_MAPPINGS["cpf"])
        nome_col = find_column(df, EMPLOYEE_COLUMN_MAPPINGS["nome"])
        cargo_col = find_column(df, EMPLOYEE_COLUMN_MAPPINGS.get("cargo", []))
        funcao_col = find_column(df, EMPLOYEE_COLUMN_MAPPINGS.get("funcao", []))
        salario_col = find_column(df, EMPLOYEE_COLUMN_MAPPINGS.get("salario_base", []))
        admissao_col = find_column(df, EMPLOYEE_COLUMN_MAPPINGS.get("data_admissao", []))
        
        cnpj_femsa_col = find_column(df, COMPANY_COLUMN_MAPPINGS["cnpj_femsa"])
        empresa_col = find_column(df, COMPANY_COLUMN_MAPPINGS["name"])
        
        cnpj_unidade_col = find_column(df, UNIT_COLUMN_MAPPINGS["cnpj_unidade"])
        nome_unidade_col = find_column(df, UNIT_COLUMN_MAPPINGS["nome_unidade"])
        centro_custo_col = find_column(df, UNIT_COLUMN_MAPPINGS.get("centro_custo_femsa", []))
        
        if cpf_col:
            result["mapped_columns"].append(f"CPF: {cpf_col}")
        if nome_col:
            result["mapped_columns"].append(f"Nome: {nome_col}")
        if cnpj_femsa_col:
            result["mapped_columns"].append(f"CNPJ FEMSA: {cnpj_femsa_col}")
        if cnpj_unidade_col:
            result["mapped_columns"].append(f"CNPJ Unidade: {cnpj_unidade_col}")
        
        payroll_columns_found = {}
        for type_code, mapping in PAYROLL_COLUMN_MAPPINGS.items():
            qty_col = find_column(df, mapping["qty_columns"]) if mapping["qty_columns"] else None
            value_col = find_column(df, mapping["value_columns"]) if mapping["value_columns"] else None
            if qty_col or value_col:
                payroll_columns_found[type_code] = {"qty": qty_col, "value": value_col}
                result["mapped_columns"].append(f"{type_code}: qty={qty_col}, value={value_col}")
        
        if not cpf_col:
            result["errors"].append("Coluna CPF não encontrada")
            result["success"] = False
            return result
        
        companies_cache = {}
        units_cache = {}
        employees_cache = {}
        
        for idx, row in df.iterrows():
            try:
                cpf = str(row.get(cpf_col, "")).strip()
                if not cpf or cpf == "nan":
                    continue
                
                nome = str(row.get(nome_col, "")).strip() if nome_col else ""
                
                cnpj_femsa = str(row.get(cnpj_femsa_col, "")).strip() if cnpj_femsa_col else "00000000000000"
                empresa_nome = str(row.get(empresa_col, "")).strip() if empresa_col else "Empresa Padrão"
                
                if cnpj_femsa not in companies_cache:
                    company = get_or_create_company(db, cnpj_femsa, empresa_nome)
                    companies_cache[cnpj_femsa] = company
                    if company.id:
                        result["companies_created"] += 1
                else:
                    company = companies_cache[cnpj_femsa]
                
                cnpj_unidade = str(row.get(cnpj_unidade_col, "")).strip() if cnpj_unidade_col else cnpj_femsa
                nome_unidade = str(row.get(nome_unidade_col, "")).strip() if nome_unidade_col else "Unidade Padrão"
                centro_custo = str(row.get(centro_custo_col, "")).strip() if centro_custo_col else None
                
                unit_key = normalize_cnpj(cnpj_unidade)
                if unit_key not in units_cache:
                    unit = get_or_create_unit(db, company.id, cnpj_unidade, nome_unidade, centro_custo)
                    units_cache[unit_key] = unit
                    if unit.id:
                        result["units_created"] += 1
                else:
                    unit = units_cache[unit_key]
                
                cpf_normalized = normalize_cpf(cpf)
                if cpf_normalized not in employees_cache:
                    employee = get_or_create_employee(db, cpf, nome)
                    employees_cache[cpf_normalized] = employee
                    if employee.id:
                        result["employees_created"] += 1
                else:
                    employee = employees_cache[cpf_normalized]
                
                cargo = str(row.get(cargo_col, "")).strip() if cargo_col else None
                funcao = str(row.get(funcao_col, "")).strip() if funcao_col else None
                salario = safe_float(row.get(salario_col)) if salario_col else 0.0
                data_admissao = parse_date(row.get(admissao_col)) if admissao_col else None
                
                contract = get_or_create_contract(
                    db, employee.id, company.id, unit.id,
                    cargo, funcao, salario, data_admissao
                )
                if contract.id:
                    result["contracts_created"] += 1
                
                billing_period = get_or_create_billing_period(db, company.id, mes_referencia)
                
                for type_code, cols in payroll_columns_found.items():
                    qty = safe_float(row.get(cols["qty"])) if cols["qty"] else 0.0
                    value = safe_float(row.get(cols["value"])) if cols["value"] else 0.0
                    
                    if value == 0 and qty == 0:
                        continue
                    
                    item_type = get_payroll_item_type(db, type_code)
                    if not item_type:
                        continue
                    
                    payroll_item = PayrollItem(
                        billing_period_id=billing_period.id,
                        employee_id=employee.id,
                        contract_id=contract.id,
                        unit_id=unit.id,
                        payroll_item_type_id=item_type.id,
                        quantity=qty,
                        amount=value,
                        source_column=cols["value"] or cols["qty"]
                    )
                    db.add(payroll_item)
                    result["payroll_items_created"] += 1
                    
            except Exception as e:
                result["errors"].append(f"Linha {idx + 2}: {str(e)}")
        
        db.commit()
        
    except Exception as e:
        result["success"] = False
        result["errors"].append(f"Erro geral: {str(e)}")
        db.rollback()
    
    return result


def process_exams_upload(
    db: Session,
    file_content: bytes,
    filename: str
) -> Dict[str, Any]:
    result = {
        "success": True,
        "employees_created": 0,
        "units_created": 0,
        "exam_records_created": 0,
        "payroll_items_created": 0,
        "rows_processed": 0,
        "errors": [],
        "mapped_columns": []
    }
    
    try:
        df = read_file_to_dataframe(file_content, filename, sheet_index=1 if not filename.lower().endswith('.csv') else 0)
        result["rows_processed"] = len(df)
        
        cnpj_col = find_column(df, EXAM_COLUMN_MAPPINGS["cnpj_unidade"])
        cpf_col = find_column(df, EXAM_COLUMN_MAPPINGS["cpf"])
        nome_col = find_column(df, EXAM_COLUMN_MAPPINGS["nome"])
        tipo_col = find_column(df, EXAM_COLUMN_MAPPINGS["tipo"])
        exame_col = find_column(df, EXAM_COLUMN_MAPPINGS["exame"])
        data_pedido_col = find_column(df, EXAM_COLUMN_MAPPINGS["data_pedido"])
        data_exame_col = find_column(df, EXAM_COLUMN_MAPPINGS["data_exame"])
        data_inativacao_col = find_column(df, EXAM_COLUMN_MAPPINGS.get("data_inativacao", []))
        valor_col = find_column(df, EXAM_COLUMN_MAPPINGS["valor_cobrar"])
        
        for col_name, col_val in [
            ("CNPJ Unidade", cnpj_col), ("CPF", cpf_col), ("Nome", nome_col),
            ("Tipo", tipo_col), ("Exame", exame_col), ("Data Exame", data_exame_col),
            ("Valor Cobrar", valor_col)
        ]:
            if col_val:
                result["mapped_columns"].append(f"{col_name}: {col_val}")
        
        if not cpf_col:
            result["errors"].append("Coluna CPF não encontrada")
            result["success"] = False
            return result
        
        exame_type = get_payroll_item_type(db, "EXAME_MEDICO")
        if not exame_type:
            result["errors"].append("Tipo EXAME_MEDICO não encontrado")
            result["success"] = False
            return result
        
        units_cache = {}
        employees_cache = {}
        periods_cache = {}
        
        for idx, row in df.iterrows():
            try:
                cpf = str(row.get(cpf_col, "")).strip()
                if not cpf or cpf == "nan":
                    continue
                
                nome = str(row.get(nome_col, "")).strip() if nome_col else ""
                
                cpf_normalized = normalize_cpf(cpf)
                if cpf_normalized not in employees_cache:
                    employee = get_or_create_employee(db, cpf, nome)
                    employees_cache[cpf_normalized] = employee
                    if employee.id:
                        result["employees_created"] += 1
                else:
                    employee = employees_cache[cpf_normalized]
                
                unit = None
                if cnpj_col:
                    cnpj_unidade = str(row.get(cnpj_col, "")).strip()
                    if cnpj_unidade and cnpj_unidade != "nan":
                        unit_key = normalize_cnpj(cnpj_unidade)
                        if unit_key not in units_cache:
                            existing_unit = db.query(Unit).filter(Unit.cnpj_unidade == unit_key).first()
                            if existing_unit:
                                units_cache[unit_key] = existing_unit
                            else:
                                default_company = db.query(Company).first()
                                if not default_company:
                                    default_company = Company(cnpj_femsa="00000000000000", name="Empresa Padrão")
                                    db.add(default_company)
                                    db.flush()
                                unit = Unit(
                                    company_id=default_company.id,
                                    cnpj_unidade=unit_key,
                                    nome_unidade=f"Unidade {unit_key}"
                                )
                                db.add(unit)
                                db.flush()
                                units_cache[unit_key] = unit
                                result["units_created"] += 1
                        unit = units_cache.get(unit_key)
                
                data_exame = parse_date(row.get(data_exame_col)) if data_exame_col else None
                data_pedido = parse_date(row.get(data_pedido_col)) if data_pedido_col else None
                data_inativacao = parse_date(row.get(data_inativacao_col)) if data_inativacao_col else None
                
                if data_exame:
                    mes_ref = data_exame.strftime("%Y-%m")
                else:
                    mes_ref = datetime.now().strftime("%Y-%m")
                
                if unit:
                    company_id = unit.company_id
                else:
                    default_company = db.query(Company).first()
                    if not default_company:
                        default_company = Company(cnpj_femsa="00000000000000", name="Empresa Padrão")
                        db.add(default_company)
                        db.flush()
                    company_id = default_company.id
                
                period_key = f"{company_id}_{mes_ref}"
                if period_key not in periods_cache:
                    billing_period = get_or_create_billing_period(db, company_id, mes_ref)
                    periods_cache[period_key] = billing_period
                else:
                    billing_period = periods_cache[period_key]
                
                valor = safe_float(row.get(valor_col)) if valor_col else 0.0
                tipo = str(row.get(tipo_col, "")).strip() if tipo_col else None
                exame = str(row.get(exame_col, "")).strip() if exame_col else None
                
                exam_record = BillingExamRecord(
                    billing_period_id=billing_period.id,
                    unit_id=unit.id if unit else None,
                    employee_id=employee.id,
                    tipo=tipo,
                    exame=exame,
                    data_pedido=data_pedido,
                    data_exame=data_exame,
                    data_inativacao=data_inativacao,
                    valor_cobrar=valor
                )
                db.add(exam_record)
                result["exam_records_created"] += 1
                
                if valor > 0:
                    payroll_item = PayrollItem(
                        billing_period_id=billing_period.id,
                        employee_id=employee.id,
                        unit_id=unit.id if unit else None,
                        payroll_item_type_id=exame_type.id,
                        quantity=1,
                        amount=valor,
                        source_column="Vl.Cobrar R$",
                        notes=f"Exame: {exame or tipo}"
                    )
                    db.add(payroll_item)
                    result["payroll_items_created"] += 1
                
            except Exception as e:
                result["errors"].append(f"Linha {idx + 2}: {str(e)}")
        
        db.commit()
        
    except Exception as e:
        result["success"] = False
        result["errors"].append(f"Erro geral: {str(e)}")
        db.rollback()
    
    return result


def get_billing_period_summary(db: Session, billing_period_id: int) -> Dict[str, Any]:
    period = db.query(BillingPeriod).filter(BillingPeriod.id == billing_period_id).first()
    if not period:
        return {"error": "Período não encontrado"}
    
    items = db.query(PayrollItem).filter(PayrollItem.billing_period_id == billing_period_id).all()
    
    employees_summary = {}
    
    for item in items:
        emp_id = item.employee_id
        if emp_id not in employees_summary:
            employee = db.query(BillingEmployee).filter(BillingEmployee.id == emp_id).first()
            employees_summary[emp_id] = {
                "employee_id": emp_id,
                "cpf": employee.cpf if employee else "",
                "nome": employee.nome if employee else "",
                "items_by_type": {},
                "total": 0.0
            }
        
        type_code = item.payroll_item_type.code if item.payroll_item_type else "UNKNOWN"
        type_desc = item.payroll_item_type.description if item.payroll_item_type else "Desconhecido"
        
        if type_code not in employees_summary[emp_id]["items_by_type"]:
            employees_summary[emp_id]["items_by_type"][type_code] = {
                "code": type_code,
                "description": type_desc,
                "quantity": 0.0,
                "amount": 0.0
            }
        
        employees_summary[emp_id]["items_by_type"][type_code]["quantity"] += item.quantity or 0
        employees_summary[emp_id]["items_by_type"][type_code]["amount"] += item.amount or 0
        employees_summary[emp_id]["total"] += item.amount or 0
    
    grand_total = sum(emp["total"] for emp in employees_summary.values())
    
    return {
        "billing_period_id": billing_period_id,
        "mes_referencia": period.mes_referencia,
        "status": period.status.value if period.status else "unknown",
        "employees": list(employees_summary.values()),
        "total_employees": len(employees_summary),
        "grand_total": grand_total
    }
