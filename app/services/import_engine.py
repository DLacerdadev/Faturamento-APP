"""Motor genérico de importação dirigido por ImportTemplate.

Lê uma planilha de qualquer fonte conforme o mapeamento do template e grava no
fluxo ÚNICO de faturamento (BillingExamRecord + PayrollItem do tipo
EXAME_MEDICO no mesmo BillingPeriod da folha), permitindo padronizar a
extração mesmo quando os layouts são diferentes.

Hoje cobre a categoria 'exames'; o motor já nasce genérico para estender a
benefícios/uniformes/EPIs/folha reaproveitando read/detect/extract.
"""
import re
import unicodedata
from io import BytesIO
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd
from sqlalchemy.orm import Session

from app.models.billing import (
    Company, Unit, BillingEmployee, BillingPeriod, BillingExamRecord, PayrollItem,
)
from app.services.billing_processor import (
    normalize_cpf, normalize_cnpj, parse_date, safe_float,
    get_or_create_employee, get_or_create_billing_period, get_payroll_item_type,
)

DEFAULT_DATE_FORMATS = ["%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y"]

# Campos canônicos do mapeamento para a categoria 'exames'.
EXAM_CANONICAL_FIELDS = [
    "cpf", "nome", "matricula", "cnpj_unidade", "tipo", "exame",
    "data_pedido", "data_exame", "data_inativacao", "valor",
]


def normalize_name(name: Any) -> str:
    """Normaliza nome para casamento: maiúsculas, sem acento, espaços colapsados."""
    if name is None:
        return ""
    s = str(name).strip().upper()
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    s = re.sub(r"\s+", " ", s)
    return s


# ------------------------- leitura do arquivo -------------------------

def get_sheet_names(content: bytes, ext: str) -> List[str]:
    if ext in ("xlsx", "xls"):
        try:
            xls = pd.ExcelFile(BytesIO(content))
            return list(xls.sheet_names)
        except Exception:
            return []
    return []


def read_dataframe(
    content: bytes, ext: str,
    sheet_mode: str = "index", sheet_index: int = 0,
    sheet_name: Optional[str] = None, header_row: int = 0,
) -> pd.DataFrame:
    header = int(header_row or 0)
    if ext in ("xlsx", "xls"):
        if sheet_mode == "name" and sheet_name:
            sheet: Any = sheet_name
        else:
            sheet = int(sheet_index or 0)
        return pd.read_excel(BytesIO(content), sheet_name=sheet, header=header)
    # CSV — tenta encodings comuns e detecção automática de separador
    for enc in ("utf-8", "latin-1", "iso-8859-1", "cp1252"):
        try:
            return pd.read_csv(BytesIO(content), encoding=enc, header=header, sep=None, engine="python")
        except Exception:
            continue
    raise ValueError("Não foi possível ler o arquivo CSV")


def _df_for_template(content: bytes, ext: str, template) -> pd.DataFrame:
    return read_dataframe(
        content, ext,
        sheet_mode=template.sheet_mode or "index",
        sheet_index=template.sheet_index or 0,
        sheet_name=template.sheet_name,
        header_row=template.header_row or 0,
    )


# ------------------------- detecção de colunas (apoio à UI) -------------------------

def detect_structure(
    content: bytes, ext: str,
    sheet_mode: str = "index", sheet_index: int = 0,
    sheet_name: Optional[str] = None, header_row: int = 0,
    sample_size: int = 5,
) -> Dict[str, Any]:
    sheets = get_sheet_names(content, ext)
    df = read_dataframe(content, ext, sheet_mode, sheet_index, sheet_name, header_row)
    df = df.fillna("")
    columns = [str(c) for c in df.columns]
    sample = df.head(sample_size).astype(str).to_dict("records")
    return {
        "sheets": sheets,
        "columns": columns,
        "sample_rows": sample,
        "total_rows": int(len(df)),
    }


# ------------------------- resolução e parsing -------------------------

def _resolve_column(df_columns: List[Any], spec: Any) -> Optional[Any]:
    """Acha a coluna real a partir do spec do mapeamento (nome exato,
    case-insensitive ou índice inteiro)."""
    if spec is None or spec == "":
        return None
    if isinstance(spec, int):
        return df_columns[spec] if 0 <= spec < len(df_columns) else None
    s = str(spec).strip()
    if s.isdigit() and not any(str(c).strip() == s for c in df_columns):
        idx = int(s)
        return df_columns[idx] if 0 <= idx < len(df_columns) else None
    for c in df_columns:
        if str(c).strip() == s:
            return c
    low = s.lower()
    for c in df_columns:
        if str(c).strip().lower() == low:
            return c
    return None


def _parse_value(raw: Any, decimal_separator: str = ",") -> float:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)) or raw == "":
        return 0.0
    if isinstance(raw, (int, float)):
        try:
            return float(raw)
        except (TypeError, ValueError):
            return 0.0
    s = str(raw).replace("R$", "").strip()
    if decimal_separator == ",":
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", "")
    s = re.sub(r"[^\d.\-]", "", s)
    try:
        return float(s) if s not in ("", "-", ".") else 0.0
    except ValueError:
        return 0.0


def _parse_date(raw: Any, date_formats: List[str]):
    if raw is None or raw == "" or (isinstance(raw, float) and pd.isna(raw)):
        return None
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, pd.Timestamp):
        return raw.to_pydatetime()
    s = str(raw).strip()
    for fmt in (date_formats or DEFAULT_DATE_FORMATS):
        try:
            return datetime.strptime(s, fmt)
        except (ValueError, TypeError):
            continue
    # fallback: deixa o pandas tentar
    try:
        return pd.to_datetime(s, dayfirst=True).to_pydatetime()
    except Exception:
        return None


# ------------------------- extração canônica -------------------------

def extract_records(df: pd.DataFrame, template) -> Dict[str, Any]:
    """Aplica o mapeamento e devolve registros canônicos + diagnóstico de colunas."""
    df = df.fillna("")
    cols = list(df.columns)
    mapping = template.mapping or {}
    date_formats = template.date_formats or DEFAULT_DATE_FORMATS
    dec = template.decimal_separator or ","

    resolved = {field: _resolve_column(cols, mapping.get(field)) for field in EXAM_CANONICAL_FIELDS}
    value_cols_resolved = []
    if (template.layout or "long") == "wide":
        for spec in (template.value_columns or []):
            rc = _resolve_column(cols, spec)
            if rc is not None:
                value_cols_resolved.append(rc)

    records: List[Dict[str, Any]] = []
    for idx, row in df.iterrows():
        def cell(field):
            c = resolved.get(field)
            return row.get(c) if c is not None else None

        cpf_raw = cell("cpf")
        nome_raw = cell("nome")
        cpf = normalize_cpf(str(cpf_raw)) if cpf_raw not in (None, "") else ""

        if (template.layout or "long") == "wide":
            valor = sum(_parse_value(row.get(c), dec) for c in value_cols_resolved)
            tipo = "CONSOLIDADO"
            exame = template.nome
        else:
            valor = _parse_value(cell("valor"), dec)
            tipo = str(cell("tipo") or "").strip() or None
            exame = str(cell("exame") or "").strip() or None

        rec = {
            "_row": int(idx) + int(template.header_row or 0) + 2,
            "cpf": cpf,
            "nome": str(nome_raw or "").strip(),
            "matricula": str(cell("matricula") or "").strip(),
            "cnpj_unidade": str(cell("cnpj_unidade") or "").strip(),
            "tipo": tipo,
            "exame": exame,
            "data_pedido": _parse_date(cell("data_pedido"), date_formats),
            "data_exame": _parse_date(cell("data_exame"), date_formats),
            "data_inativacao": _parse_date(cell("data_inativacao"), date_formats),
            "valor": round(valor, 2),
        }
        # ignora linhas totalmente vazias (sem chave nem valor)
        if not rec["cpf"] and not rec["nome"] and rec["valor"] == 0:
            continue
        records.append(rec)

    columns_mapped = {f: (str(resolved[f]) if resolved[f] is not None else None) for f in EXAM_CANONICAL_FIELDS}
    if (template.layout or "long") == "wide":
        columns_mapped["value_columns"] = [str(c) for c in value_cols_resolved]

    return {"records": records, "columns_mapped": columns_mapped}


def _resolve_employee(db: Session, template, rec: Dict[str, Any], create: bool):
    """Resolve o BillingEmployee conforme a chave do template.
    - cpf: get_or_create (cria se não existir, quando create=True)
    - nome: casa com funcionário já existente (não cria sem CPF)
    Retorna (employee | None, motivo_se_nao_achou | None)."""
    match_key = template.match_key or "cpf"

    if rec["cpf"]:
        if create:
            return get_or_create_employee(db, rec["cpf"], rec["nome"]), None
        emp = db.query(BillingEmployee).filter(BillingEmployee.cpf == rec["cpf"]).first()
        return emp, (None if emp else "CPF não cadastrado")

    if match_key == "nome" and rec["nome"]:
        alvo = normalize_name(rec["nome"])
        for emp in db.query(BillingEmployee).all():
            if normalize_name(emp.nome) == alvo:
                return emp, None
        return None, "Funcionário não encontrado pelo nome (sem CPF para criar)"

    return None, "Sem CPF para vincular (defina a coluna CPF no modelo)"


def _resolve_unit(db: Session, rec: Dict[str, Any], units_cache: Dict[str, Any], counters: Dict[str, int]):
    cnpj = rec.get("cnpj_unidade")
    if not cnpj or cnpj == "nan":
        return None
    key = normalize_cnpj(cnpj)
    if key in units_cache:
        return units_cache[key]
    unit = db.query(Unit).filter(Unit.cnpj_unidade == key).first()
    if not unit:
        default_company = db.query(Company).order_by(Company.id).first()
        if not default_company:
            default_company = Company(cnpj_femsa="00000000000000", name="Empresa Padrão")
            db.add(default_company)
            db.flush()
        unit = Unit(company_id=default_company.id, cnpj_unidade=key, nome_unidade=f"Unidade {key}")
        db.add(unit)
        db.flush()
        counters["units_created"] += 1
    units_cache[key] = unit
    return unit


def _default_company_id(db: Session) -> int:
    company = db.query(Company).order_by(Company.id).first()
    if not company:
        company = Company(cnpj_femsa="00000000000000", name="Empresa Padrão")
        db.add(company)
        db.flush()
    return company.id


# ------------------------- preview (dry-run) -------------------------

def preview(db: Session, template, content: bytes, ext: str, limit: int = 10) -> Dict[str, Any]:
    df = _df_for_template(content, ext, template)
    extracted = extract_records(df, template)
    records = extracted["records"]

    matched, unmatched = 0, 0
    sample = []
    for rec in records:
        emp, motivo = _resolve_employee(db, template, rec, create=False)
        ok = emp is not None or bool(rec["cpf"])  # com CPF, será criado na importação
        if ok:
            matched += 1
        else:
            unmatched += 1
        if len(sample) < limit:
            sample.append({
                "linha": rec["_row"],
                "cpf": rec["cpf"],
                "nome": rec["nome"],
                "exame": rec["exame"],
                "data_exame": rec["data_exame"].strftime("%d/%m/%Y") if rec["data_exame"] else None,
                "valor": rec["valor"],
                "vinculo": "ok" if ok else (motivo or "não encontrado"),
            })

    return {
        "success": True,
        "layout": template.layout,
        "columns_mapped": extracted["columns_mapped"],
        "total_rows": len(records),
        "matched": matched,
        "unmatched": unmatched,
        "total_valor": round(sum(r["valor"] for r in records), 2),
        "sample": sample,
    }


# ------------------------- importação (commit) -------------------------

def import_exams(db: Session, template, content: bytes, ext: str) -> Dict[str, Any]:
    result = {
        "success": True,
        "template": template.nome,
        "rows_processed": 0,
        "employees_created": 0,
        "units_created": 0,
        "exam_records_created": 0,
        "payroll_items_created": 0,
        "skipped": 0,
        "errors": [],
        "columns_mapped": {},
    }
    counters = {"units_created": 0}

    try:
        df = _df_for_template(content, ext, template)
        extracted = extract_records(df, template)
        result["columns_mapped"] = extracted["columns_mapped"]
        records = extracted["records"]
        result["rows_processed"] = len(records)

        exame_type = get_payroll_item_type(db, "EXAME_MEDICO")
        if not exame_type:
            result["success"] = False
            result["errors"].append("Tipo EXAME_MEDICO não encontrado no cadastro")
            return result

        units_cache: Dict[str, Any] = {}
        periods_cache: Dict[str, Any] = {}

        for rec in records:
            try:
                existed = bool(rec["cpf"]) and db.query(BillingEmployee).filter(
                    BillingEmployee.cpf == rec["cpf"]).first() is not None
                emp, motivo = _resolve_employee(db, template, rec, create=True)
                if not emp:
                    result["skipped"] += 1
                    result["errors"].append(f"Linha {rec['_row']}: {motivo}")
                    continue
                if rec["cpf"] and not existed:
                    result["employees_created"] += 1

                unit = _resolve_unit(db, rec, units_cache, counters)
                company_id = unit.company_id if unit else _default_company_id(db)

                mes_ref = rec["data_exame"].strftime("%Y-%m") if rec["data_exame"] else datetime.now().strftime("%Y-%m")
                pkey = f"{company_id}_{mes_ref}"
                if pkey not in periods_cache:
                    periods_cache[pkey] = get_or_create_billing_period(db, company_id, mes_ref)
                billing_period = periods_cache[pkey]

                exam_record = BillingExamRecord(
                    billing_period_id=billing_period.id,
                    unit_id=unit.id if unit else None,
                    employee_id=emp.id,
                    tipo=rec["tipo"],
                    exame=rec["exame"],
                    data_pedido=rec["data_pedido"],
                    data_exame=rec["data_exame"],
                    data_inativacao=rec["data_inativacao"],
                    valor_cobrar=rec["valor"],
                )
                db.add(exam_record)
                result["exam_records_created"] += 1

                if rec["valor"] > 0:
                    db.add(PayrollItem(
                        billing_period_id=billing_period.id,
                        employee_id=emp.id,
                        unit_id=unit.id if unit else None,
                        payroll_item_type_id=exame_type.id,
                        quantity=1,
                        amount=rec["valor"],
                        source_column="import_template",
                        notes=f"Exame: {rec['exame'] or rec['tipo']} (modelo: {template.nome})",
                    ))
                    result["payroll_items_created"] += 1

            except Exception as e:  # erro isolado por linha
                result["errors"].append(f"Linha {rec['_row']}: {str(e)}")
                result["skipped"] += 1

        result["units_created"] = counters["units_created"]
        db.commit()

    except Exception as e:
        result["success"] = False
        result["errors"].append(f"Erro geral: {str(e)}")
        db.rollback()

    return result
