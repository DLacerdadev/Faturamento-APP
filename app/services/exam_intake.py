"""Lançamento de exames por arquivo (Fase 1 — input manual).

Lê Excel/CSV/PDF (texto e, quando habilitado, escaneado via OCR), AUTO-DETECTA as
colunas por nomes comuns (CPF, nome, centro de custo/CNPJ, valor, data do exame,
exame) e lança na folha de pagamento ligando pela COMPETÊNCIA derivada da data
do exame (mês/ano), por funcionário (CPF).

Sem modelos salvos: a detecção é automática. O que não for reconhecido é
reportado para o usuário conferir antes de importar.
"""
import re
import unicodedata
from io import BytesIO
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from sqlalchemy.orm import Session

from app.models.billing import Company, Unit, BillingEmployee, BillingExamRecord, PayrollItem
from app.models.medical_exam import MedicalExam
from app.models.exam_catalog import ExamCatalog, PriceModelItem
from app.services.billing_processor import (
    normalize_cpf, normalize_cnpj, parse_date, safe_float,
    get_or_create_employee, get_or_create_billing_period, get_payroll_item_type,
)

# Colunas de valor de exame na página de Exames (MedicalExam).
MEDEXAM_VALUE_COLUMNS = [
    "clinic", "audio", "acuid", "hemo", "lipidograma", "rx_coluna", "met_e_cet",
    "acet_u", "hg", "retic", "ac_trans", "eeg", "ecg", "etanol", "glice",
    "gama_gt", "tgp", "rx_torax", "espiro", "rx_lomb", "aval_psicossocial",
]

# Identificação do exame por nome (normalizado: minúsculas, sem acento, só alnum).
EXAM_NAME_TO_COLUMN = {
    "clinic": ["clinico", "exameclinico", "avaliacaoclinica", "clinicomedico", "consultaclinica", "examedeclinico"],
    "audio": ["audiometria", "audiometriatonal", "audio", "exameaudiometrico"],
    "acuid": ["acuidadevisual", "acuidade", "av", "examevisual"],
    "hemo": ["hemograma", "hemogramacompleto", "hemo"],
    "lipidograma": ["lipidograma", "perfillipidico", "colesterol"],
    "rx_coluna": ["rxcoluna", "raioxcoluna", "rxdecoluna"],
    "met_e_cet": ["metecet", "metilhipurico"],
    "acet_u": ["acetu", "acetonaurinaria", "acetonau"],
    "hg": ["mercurio", "hgurinario"],
    "retic": ["reticulocitos", "retic"],
    "ac_trans": ["acidotranshipurico", "actrans", "transhipurico"],
    "eeg": ["eletroencefalograma", "eeg"],
    "ecg": ["eletrocardiograma", "ecg"],
    "etanol": ["etanol", "alcoolemia"],
    "glice": ["glicemia", "glicose", "glice"],
    "gama_gt": ["gamagt", "gamaglutamil", "ggt"],
    "tgp": ["tgp", "transaminase", "alt"],
    "rx_torax": ["rxtorax", "raioxtorax", "rxdetorax", "toraxpa"],
    "espiro": ["espirometria", "espiro"],
    "rx_lomb": ["rxlombar", "raioxlombar", "rxlomb"],
    "aval_psicossocial": ["psicossocial", "avaliacaopsicossocial", "psicologico", "psicossocialocupacional"],
}


def _exam_name_to_column(nome: Any, name_map: Optional[Dict[str, List[str]]] = None) -> Optional[str]:
    name_map = name_map or EXAM_NAME_TO_COLUMN
    n = _norm(nome)
    if not n:
        return None
    for col, syns in name_map.items():
        for s in (syns or []):
            if s == n or (len(s) >= 4 and s in n):
                return col
    return None


def _load_identification(db: Session) -> Dict[str, List[str]]:
    """Mapa coluna -> sinônimos vindo do catálogo (editável na UI). Cai para o
    mapa embutido se o catálogo ainda não estiver populado."""
    try:
        cats = db.query(ExamCatalog).filter(ExamCatalog.ativo.is_(True)).all()
        m = {c.coluna: list(c.sinonimos or []) for c in cats if c.coluna}
        return m or EXAM_NAME_TO_COLUMN
    except Exception:
        return EXAM_NAME_TO_COLUMN


def _load_price_model(db: Session, price_model_id: Optional[int]) -> Dict[str, float]:
    """Mapa coluna -> preço do modelo escolhido. Vazio se nenhum modelo."""
    if not price_model_id:
        return {}
    out: Dict[str, float] = {}
    items = db.query(PriceModelItem).filter(PriceModelItem.price_model_id == price_model_id).all()
    for it in items:
        if it.exam_catalog and it.exam_catalog.coluna:
            out[it.exam_catalog.coluna] = it.preco or 0.0
    return out

# Campos canônicos e seus apelidos (normalizados: minúsculas, sem acento, só alnum).
FIELD_ALIASES: Dict[str, List[str]] = {
    "cpf": ["cpf", "documento", "doc", "cpffuncionario"],
    "nome": ["nome", "funcionario", "colaborador", "nomefuncionario", "name", "trabalhador", "nomecolaborador"],
    "centro_custo": ["centrodecusto", "centrocusto", "ccusto", "cc", "codccu", "centrocustofemsa", "centrodecustos"],
    "cnpj": ["cnpj", "cnpjunidade", "cnpjempresa", "cnpjdaunidade", "cnpjfemsa"],
    "valor": ["valor", "vlcobrar", "vlcobrarr", "valorcobrar", "vlrcobrar", "valortotal", "total",
              "vlexame", "valorexame", "preco", "vlrexame"],
    "data_exame": ["dataexame", "dtexame", "datadoexame", "dataaso", "dtaso", "data", "dt"],
    "exame": ["exame", "tipoexame", "tipo", "descricaoexame", "nomeexame", "procedimento", "exames"],
}

# Campos que, se ausentes, impedem o lançamento.
REQUIRED_FIELDS = ["cpf", "valor", "data_exame"]


def _norm(s: Any) -> str:
    s = str(s or "").strip().lower()
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", s)


def _norm_name(s: Any) -> str:
    s = str(s or "").strip().upper()
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s)


def _parse_money(raw: Any) -> float:
    """Converte valor monetário tolerando ruído (espaços, R$, OCR): '1.234,56',
    '80, 00', 'R$ 45,90' -> float."""
    if raw is None:
        return 0.0
    if isinstance(raw, (int, float)):
        try:
            return float(raw)
        except (TypeError, ValueError):
            return 0.0
    s = re.sub(r"[^\d,.\-]", "", str(raw))  # remove espaços, R$, letras
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")   # 1.234,56 -> 1234.56
    elif "," in s:
        s = s.replace(",", ".")                    # 80,00 -> 80.00
    try:
        return float(s) if s not in ("", "-", ".") else 0.0
    except ValueError:
        return 0.0


# ------------------------- leitura crua (grid de células) -------------------------

class IntakeError(Exception):
    pass


def _grid_from_excel_csv(content: bytes, ext: str) -> List[List[Any]]:
    if ext in ("xlsx", "xls"):
        df = pd.read_excel(BytesIO(content), header=None, dtype=object)
    else:
        last_err = None
        df = None
        for enc in ("utf-8", "latin-1", "iso-8859-1", "cp1252"):
            try:
                df = pd.read_csv(BytesIO(content), header=None, dtype=object, sep=None, engine="python", encoding=enc)
                break
            except Exception as e:
                last_err = e
        if df is None:
            raise IntakeError(f"Não foi possível ler o CSV: {last_err}")
    return df.fillna("").values.tolist()


def _grid_from_pdf(content: bytes) -> Tuple[List[List[Any]], str]:
    """Extrai tabela de PDF com TEXTO. Retorna (grid, modo). Se não houver texto
    (PDF escaneado), tenta OCR; se OCR indisponível, levanta IntakeError clara."""
    import pdfplumber
    rows: List[List[Any]] = []
    text_chars = 0
    with pdfplumber.open(BytesIO(content)) as pdf:
        for page in pdf.pages:
            text_chars += len((page.extract_text() or ""))
            for table in (page.extract_tables() or []):
                for r in table:
                    rows.append([("" if c is None else c) for c in r])
    if rows:
        return rows, "pdf-texto"
    if text_chars > 30:
        # Há texto solto mas sem tabela detectável.
        raise IntakeError("PDF tem texto, mas não foi possível detectar uma tabela. "
                          "Verifique se o arquivo tem colunas claras ou exporte em Excel.")
    # Sem texto => provavelmente escaneado => OCR
    grid = _ocr_pdf_to_grid(content)
    return grid, "pdf-ocr"


import os

# Caminhos comuns do binário no Windows; sobrescrevível por env TESSERACT_CMD.
_TESSERACT_CANDIDATES = [
    os.environ.get("TESSERACT_CMD", ""),
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
]
# tessdata local do projeto (para idioma 'por' sem precisar de admin em Program Files).
_LOCAL_TESSDATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tessdata")


def _configure_tesseract():
    """Configura o binário e escolhe idioma/tessdata. Levanta IntakeError se o
    OCR não estiver disponível. Retorna (lang, config_str)."""
    try:
        import pytesseract
        import fitz  # PyMuPDF  # noqa: F401
        from PIL import Image  # noqa: F401
    except Exception:
        raise IntakeError(
            "PDF escaneado (imagem) exige OCR, que não está habilitado neste servidor "
            "(faltam pytesseract/PyMuPDF). Envie em Excel/CSV ou PDF com texto."
        )
    cmd = next((c for c in _TESSERACT_CANDIDATES if c and os.path.exists(c)), None)
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd
    # idioma: prefere 'por' (local ou instalado), senão cai para 'eng'
    config = ""
    if os.path.exists(os.path.join(_LOCAL_TESSDATA, "por.traineddata")):
        return "por", f'--tessdata-dir "{_LOCAL_TESSDATA}"'
    try:
        langs = set(pytesseract.get_languages(config=""))
    except Exception:
        langs = set()
    if "por" in langs:
        return "por", config
    if "eng" in langs or not langs:
        return "eng", config
    raise IntakeError("Tesseract instalado, mas sem pacote de idioma (por/eng). Instale o idioma.")


def _words_to_cells(words: List[tuple]) -> List[str]:
    """Agrupa palavras (left, right, texto) de uma linha em CÉLULAS por colunas.
    O limiar de 'quebra de coluna' é baseado na LARGURA DE CARACTERE (um gap entre
    colunas vale vários caracteres; o espaço entre palavras vale ~1). Isso separa
    colunas mesmo quando há poucas palavras na linha (ex.: o cabeçalho)."""
    import statistics
    words = sorted(words, key=lambda w: w[0])
    if not words:
        return []
    char_ws = [(w[1] - w[0]) / max(len(w[2]), 1) for w in words if len(w[2]) >= 2]
    char_w = statistics.median(char_ws) if char_ws else 12
    thr = max(char_w * 2.5, 18)  # >= ~2.5 caracteres de gap = nova coluna
    cells = [words[0][2]]
    for k in range(1, len(words)):
        gap = words[k][0] - words[k - 1][1]
        if gap > thr:
            cells.append(words[k][2])
        else:
            cells[-1] += " " + words[k][2]
    return cells


def _ocr_pdf_to_grid(content: bytes) -> List[List[Any]]:
    """OCR de PDF escaneado. Requer Tesseract + pytesseract + PyMuPDF.
    Usa as coordenadas das palavras (image_to_data) para reconstruir colunas."""
    lang, config = _configure_tesseract()
    import fitz
    import pytesseract
    from PIL import Image
    doc = fitz.open(stream=content, filetype="pdf")
    grid: List[List[Any]] = []
    for page in doc:
        pix = page.get_pixmap(dpi=300)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        data = pytesseract.image_to_data(img, lang=lang, config=config,
                                         output_type=pytesseract.Output.DICT)
        linhas: Dict[tuple, List[tuple]] = {}
        for i in range(len(data["text"])):
            txt = (data["text"][i] or "").strip()
            if not txt:
                continue
            try:
                if float(data["conf"][i]) < 30:
                    continue
            except (ValueError, TypeError):
                pass
            key = (data["page_num"][i], data["block_num"][i], data["par_num"][i], data["line_num"][i])
            left = data["left"][i]
            right = left + data["width"][i]
            linhas.setdefault(key, []).append((left, right, txt))
        for key in sorted(linhas):
            cells = _words_to_cells(linhas[key])
            if cells:
                grid.append(cells)
    if not grid:
        raise IntakeError("OCR não conseguiu extrair texto do PDF.")
    return grid


# ------------------------- detecção de cabeçalho + auto-map -------------------------

def _alias_hits(cells: List[Any]) -> int:
    normed = [_norm(c) for c in cells]
    hits = 0
    for field, aliases in FIELD_ALIASES.items():
        if any(_match_alias(nc, aliases) for nc in normed):
            hits += 1
    return hits


def _match_alias(normed_cell: str, aliases: List[str]) -> bool:
    if not normed_cell:
        return False
    for a in aliases:
        if normed_cell == a:
            return True
    # fallback por conteúdo (apelidos mais longos primeiro p/ evitar falso positivo)
    for a in sorted(aliases, key=len, reverse=True):
        if len(a) >= 4 and a in normed_cell:
            return True
    return False


def _detect_header(grid: List[List[Any]], scan: int = 15) -> int:
    best_idx, best_hits = 0, -1
    for i in range(min(scan, len(grid))):
        h = _alias_hits(grid[i])
        if h > best_hits:
            best_hits, best_idx = h, i
    return best_idx


def auto_map(columns: List[Any]) -> Dict[str, Optional[str]]:
    normed = [(_norm(c), str(c)) for c in columns]
    mapping: Dict[str, Optional[str]] = {}
    used = set()
    for field, aliases in FIELD_ALIASES.items():
        chosen = None
        # 1) match exato
        for nc, raw in normed:
            if raw in used:
                continue
            if nc in aliases:
                chosen = raw
                break
        # 2) match por conteúdo
        if chosen is None:
            for nc, raw in normed:
                if raw in used:
                    continue
                if _match_alias(nc, aliases):
                    chosen = raw
                    break
        if chosen is not None:
            used.add(chosen)
        mapping[field] = chosen
    return mapping


def read_and_map(content: bytes, ext: str, filename: str = "") -> Dict[str, Any]:
    """Lê o arquivo, detecta cabeçalho e auto-mapeia. Devolve linhas (dicts) +
    o de-para + diagnóstico, sem tocar no banco."""
    if ext == "pdf":
        grid, modo = _grid_from_pdf(content)
    elif ext in ("xlsx", "xls", "csv"):
        grid, modo = _grid_from_excel_csv(content, ext), ("excel" if ext != "csv" else "csv")
    else:
        raise IntakeError("Formato não suportado. Use Excel, CSV ou PDF.")

    grid = [r for r in grid if any(str(c).strip() for c in r)]
    if not grid:
        raise IntakeError("Arquivo vazio ou ilegível.")

    header_idx = _detect_header(grid)
    columns = [str(c).strip() for c in grid[header_idx]]
    data_rows = grid[header_idx + 1:]
    mapping = auto_map(columns)

    rows: List[Dict[str, Any]] = []
    for i, r in enumerate(data_rows):
        d = {}
        for j, col in enumerate(columns):
            d[col] = r[j] if j < len(r) else ""
        d["__row__"] = header_idx + i + 2
        rows.append(d)

    missing_required = [f for f in REQUIRED_FIELDS if not mapping.get(f)]
    result = {
        "modo": modo,
        "header_row": header_idx,
        "columns": columns,
        "mapping": mapping,
        "rows": rows,
        "missing_required": missing_required,
    }

    # Modo ASO/documento: PDF escaneado (sem tabela real) OU PDF sem coluna de CPF,
    # mas com um CPF no texto -> lê por palavras-chave (CPF + exames + data).
    # (auto_map às vezes casa "cpf" falsamente numa frase via "documento"; por isso
    #  para PDF escaneado o modo ASO tem prioridade.)
    if ext == "pdf":
        full_text = "\n".join(" ".join(str(c) for c in row) for row in grid)
        is_scanned = (modo == "pdf-ocr")
        if _find_cpf(full_text) and (is_scanned or not mapping.get("cpf")):
            result["aso_text"] = full_text
            result["modo"] = "pdf-aso"
            result["missing_required"] = []
    return result


# ------------------------- modo ASO (documento, não tabela) -------------------------

def _find_cpf(text: str) -> str:
    """Acha um CPF no texto (formato XXX.XXX.XXX-XX), evitando CNPJ."""
    m = re.search(r'(?<!\d)(\d{3}\.\d{3}\.\d{3}-\d{2})(?!\d)', text)
    if m:
        return normalize_cpf(m.group(1))
    # fallback: 11 dígitos isolados, removendo CNPJs antes
    sem_cnpj = re.sub(r'\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}', ' ', text)
    m2 = re.search(r'(?<!\d)(\d{11})(?!\d)', sem_cnpj)
    return normalize_cpf(m2.group(1)) if m2 else ""


def _extract_aso_records(text: str) -> List[Dict[str, Any]]:
    """Extrai os exames de um ASO (1 funcionário): acha o CPF, a data e os exames
    citados (via sinônimos do mapa embutido). Nome/CC vêm da varredura por CPF."""
    from collections import Counter
    cpf = _find_cpf(text)
    if not cpf:
        return []
    todas_datas = re.findall(r'\d{2}/\d{2}/\d{4}', text)
    data_padrao = parse_date(Counter(todas_datas).most_common(1)[0][0]) if todas_datas else None

    recs: List[Dict[str, Any]] = []
    vistos = set()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        col = _exam_name_to_column(line)   # usa o mapa embutido p/ detectar exame
        if not col or col in vistos:
            continue
        vistos.add(col)
        ld = re.search(r'\d{2}/\d{2}/\d{4}', line)
        dt = parse_date(ld.group(0)) if ld else data_padrao
        nome_exame = re.sub(r'[\|\d/]+', ' ', line).strip()[:100] or col
        recs.append({
            "linha": 0, "cpf": cpf, "nome": "", "centro_custo": "", "cnpj": "",
            "exame": nome_exame, "data_exame": dt,
            "competencia": dt.strftime("%Y-%m") if dt else None, "valor": 0.0,
        })
    return recs


# ------------------------- extração canônica -------------------------

def extract_records(parsed: Dict[str, Any]) -> List[Dict[str, Any]]:
    if parsed.get("aso_text"):
        return _extract_aso_records(parsed["aso_text"])
    m = parsed["mapping"]
    recs = []
    for row in parsed["rows"]:
        def g(field):
            col = m.get(field)
            return row.get(col) if col else None

        cpf = normalize_cpf(str(g("cpf"))) if g("cpf") not in (None, "") else ""
        dt = parse_date(g("data_exame"))
        rec = {
            "linha": row["__row__"],
            "cpf": cpf,
            "nome": str(g("nome") or "").strip(),
            "centro_custo": str(g("centro_custo") or "").strip(),
            "cnpj": str(g("cnpj") or "").strip(),
            "exame": str(g("exame") or "").strip() or None,
            "data_exame": dt,
            "competencia": dt.strftime("%Y-%m") if dt else None,
            "valor": round(_parse_money(g("valor")), 2),
        }
        if not rec["cpf"] and not rec["nome"] and rec["valor"] == 0:
            continue
        recs.append(rec)
    return recs


def _row_status(rec: Dict[str, Any]) -> Optional[str]:
    if not rec["cpf"]:
        return "sem CPF"
    if not rec["competencia"]:
        return "sem data do exame (competência)"
    if rec["valor"] <= 0:
        return "sem valor"
    return None


# ------------------------- preview (dry-run) -------------------------

def preview(db: Session, content: bytes, ext: str, filename: str = "", limit: int = 15) -> Dict[str, Any]:
    parsed = read_and_map(content, ext, filename)
    recs = extract_records(parsed)
    ok, problemas = 0, 0
    by_comp: Dict[str, float] = {}
    sample = []
    for rec in recs:
        status = _row_status(rec)
        if status:
            problemas += 1
        else:
            ok += 1
            by_comp[rec["competencia"]] = round(by_comp.get(rec["competencia"], 0.0) + rec["valor"], 2)
        if len(sample) < limit:
            sample.append({
                "linha": rec["linha"], "cpf": rec["cpf"], "nome": rec["nome"],
                "exame": rec["exame"], "competencia": rec["competencia"],
                "centro_custo": rec["centro_custo"] or rec["cnpj"],
                "valor": rec["valor"], "status": status or "ok",
            })
    return {
        "success": True,
        "modo_leitura": parsed["modo"],
        "header_row": parsed["header_row"],
        "columns": parsed["columns"],
        "mapping": parsed["mapping"],
        "missing_required": parsed["missing_required"],
        "total_linhas": len(recs),
        "ok": ok,
        "problemas": problemas,
        "por_competencia": by_comp,
        "total_valor": round(sum(r["valor"] for r in recs if not _row_status(r)), 2),
        "sample": sample,
    }


# ------------------------- importação (commit) -------------------------

def _resolve_unit(db: Session, rec: Dict[str, Any], cache: Dict[str, Any], counters: Dict[str, int]):
    cnpj = rec.get("cnpj")
    cc = rec.get("centro_custo")
    if cnpj:
        key = normalize_cnpj(cnpj)
        if key in cache:
            return cache[key]
        unit = db.query(Unit).filter(Unit.cnpj_unidade == key).first()
        if not unit:
            company = _default_company(db)
            unit = Unit(company_id=company.id, cnpj_unidade=key,
                        nome_unidade=f"Unidade {key}", centro_custo_femsa=cc or None)
            db.add(unit); db.flush()
            counters["units_created"] += 1
        cache[key] = unit
        return unit
    if cc:
        unit = db.query(Unit).filter(Unit.centro_custo_femsa == cc).first()
        if unit:
            return unit
    return None


def _default_company(db: Session) -> Company:
    company = db.query(Company).order_by(Company.id).first()
    if not company:
        company = Company(cnpj_femsa="00000000000000", name="Empresa Padrão")
        db.add(company); db.flush()
    return company


def import_to_payroll(db: Session, content: bytes, ext: str, filename: str = "") -> Dict[str, Any]:
    result = {
        "success": True, "modo_leitura": None, "total_linhas": 0,
        "lancados": 0, "ignorados": 0, "employees_created": 0, "units_created": 0,
        "por_competencia": {}, "erros": [], "mapping": {},
    }
    counters = {"units_created": 0}
    try:
        parsed = read_and_map(content, ext, filename)
        result["modo_leitura"] = parsed["modo"]
        result["mapping"] = parsed["mapping"]
        if parsed["missing_required"]:
            result["success"] = False
            result["erros"].append("Colunas obrigatórias não encontradas: " + ", ".join(parsed["missing_required"]))
            return result

        recs = extract_records(parsed)
        result["total_linhas"] = len(recs)
        exame_type = get_payroll_item_type(db, "EXAME_MEDICO")
        if not exame_type:
            result["success"] = False
            result["erros"].append("Tipo EXAME_MEDICO não encontrado no cadastro.")
            return result

        units_cache: Dict[str, Any] = {}
        periods_cache: Dict[str, Any] = {}
        for rec in recs:
            status = _row_status(rec)
            if status:
                result["ignorados"] += 1
                result["erros"].append(f"Linha {rec['linha']}: {status}")
                continue
            try:
                existed = db.query(BillingEmployee).filter(BillingEmployee.cpf == rec["cpf"]).first() is not None
                emp = get_or_create_employee(db, rec["cpf"], rec["nome"])
                if not existed:
                    result["employees_created"] += 1

                unit = _resolve_unit(db, rec, units_cache, counters)
                company_id = unit.company_id if unit else _default_company(db).id

                mes = rec["competencia"]
                pkey = f"{company_id}_{mes}"
                if pkey not in periods_cache:
                    periods_cache[pkey] = get_or_create_billing_period(db, company_id, mes)
                period = periods_cache[pkey]

                db.add(BillingExamRecord(
                    billing_period_id=period.id, unit_id=unit.id if unit else None,
                    employee_id=emp.id, tipo=None, exame=rec["exame"],
                    data_exame=rec["data_exame"], valor_cobrar=rec["valor"],
                ))
                db.add(PayrollItem(
                    billing_period_id=period.id, employee_id=emp.id,
                    unit_id=unit.id if unit else None,
                    payroll_item_type_id=exame_type.id, quantity=1, amount=rec["valor"],
                    source_column="lancamento_exames",
                    notes=f"Exame: {rec['exame'] or 'exame'} | competência {mes}",
                ))
                result["lancados"] += 1
                result["por_competencia"][mes] = round(result["por_competencia"].get(mes, 0.0) + rec["valor"], 2)
            except Exception as e:
                result["ignorados"] += 1
                result["erros"].append(f"Linha {rec['linha']}: {str(e)}")

        result["units_created"] = counters["units_created"]
        db.commit()
    except IntakeError as e:
        result["success"] = False
        result["erros"].append(str(e))
        db.rollback()
    except Exception as e:
        result["success"] = False
        result["erros"].append(f"Erro geral: {str(e)}")
        db.rollback()
    return result


# ============================================================================
# Caminho UNIFICADO: upload -> página de Exames (MedicalExam) como RASCUNHO.
# Vincula por CPF (a exportação FEMSA casa por CPF/matrícula). Identifica os
# exames feitos e distribui nos campos da página; valor vem do arquivo agora e
# poderá vir da tabela de preço por "modelo" no próximo passo.
# ============================================================================

def _group_by_funcionario(recs: List[Dict[str, Any]]):
    """Agrupa registros por (cpf, data do exame) — um ASO = um funcionário, uma
    data, vários exames -> um rascunho com várias colunas."""
    grupos: Dict[tuple, List[Dict[str, Any]]] = {}
    sem_cpf = 0
    for r in recs:
        if not r["cpf"]:
            sem_cpf += 1
            continue
        data_key = r["data_exame"].date().isoformat() if r["data_exame"] else None
        grupos.setdefault((r["cpf"], data_key), []).append(r)
    return grupos, sem_cpf


def _build_cpf_resolver(cc_hints: Optional[List[str]] = None):
    """Devolve uma função cpf -> {numcad,nome,codccu,nomccu} usando a VARREDURA
    (diretório cacheado por CPF) e, como fallback, os CCs indicados pelo usuário."""
    from app.services.senior_connector import peek_employee_directory, fetch_active_employees
    try:
        directory = peek_employee_directory()   # só cache — não dispara varredura pesada no upload
    except Exception:
        directory = {}
    hint_index: Dict[str, Dict[str, Any]] = {}
    for cc in (cc_hints or []):
        try:
            for e in fetch_active_employees(str(cc)):
                cpf = normalize_cpf(str(e.get("cpf") or "")) if e.get("cpf") else ""
                if cpf and cpf not in hint_index:
                    hint_index[cpf] = {"numcad": e.get("numcad"), "nome": e.get("nomfun"),
                                       "codccu": e.get("codccu"), "nomccu": e.get("nomccu")}
        except Exception:
            continue

    def resolve(cpf: str):
        return directory.get(cpf) or hint_index.get(cpf)
    return resolve


def _build_draft(cpf: str, data_key: Optional[str], items: List[Dict[str, Any]],
                 name_map: Optional[Dict[str, List[str]]] = None,
                 price_by_column: Optional[Dict[str, float]] = None,
                 funcionario: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    name_map = name_map or EXAM_NAME_TO_COLUMN
    price_by_column = price_by_column or {}
    nome = next((i["nome"] for i in items if i["nome"]), "")
    codccu = next((i["centro_custo"] for i in items if i["centro_custo"]), None)
    cols = {c: 0.0 for c in MEDEXAM_VALUE_COLUMNS}
    identificados, nao_identificados = [], []
    extra = 0.0  # valores de exames não identificados (entram só no total)
    for it in items:
        v = it["valor"] or 0.0
        col = _exam_name_to_column(it["exame"], name_map)
        if col:
            if col in price_by_column:
                cols[col] = round(price_by_column[col], 2)   # preço do modelo manda
            else:
                cols[col] = round(cols[col] + v, 2)          # valor do arquivo (fallback)
            identificados.append(it["exame"])
        else:
            extra += v
            if it["exame"]:
                nao_identificados.append(it["exame"])
    total = round(sum(cols.values()) + extra, 2)
    # Varredura: dados do funcionário vindos do sistema (têm prioridade sobre o arquivo)
    if funcionario:
        nome_final = funcionario.get("nome") or nome
        numcad = funcionario.get("numcad")
        codccu_final = funcionario.get("codccu") or codccu
        nome_ccu = funcionario.get("nomccu")
        localizado = True
    else:
        nome_final, numcad, codccu_final, nome_ccu, localizado = nome, None, codccu, None, False
    return {
        "cpf": cpf, "nome": nome_final, "numcad": numcad,
        "codccu": codccu_final, "nome_ccu": nome_ccu, "localizado": localizado,
        "data_exame": data_key, "cols": cols, "total": total,
        "identificados": identificados, "nao_identificados": nao_identificados,
    }


def preview_medical_exams(db: Session, content: bytes, ext: str, filename: str = "",
                          price_model_id: Optional[int] = None,
                          cc_hints: Optional[List[str]] = None, limit: int = 50) -> Dict[str, Any]:
    parsed = read_and_map(content, ext, filename)
    recs = extract_records(parsed)
    grupos, sem_cpf = _group_by_funcionario(recs)
    name_map = _load_identification(db)
    price_by_column = _load_price_model(db, price_model_id)
    resolve = _build_cpf_resolver(cc_hints)

    drafts = [_build_draft(cpf, data_key, items, name_map, price_by_column, resolve(cpf))
              for (cpf, data_key), items in grupos.items()]
    nao_localizados = sorted({d["cpf"] for d in drafts if not d["localizado"]})
    sample = [{
        "cpf": d["cpf"], "nome": d["nome"], "numcad": d["numcad"],
        "data_exame": d["data_exame"], "centro_custo": d["codccu"], "nome_ccu": d["nome_ccu"],
        "localizado": d["localizado"], "total": d["total"],
        "exames_identificados": d["identificados"],
        "exames_nao_identificados": d["nao_identificados"],
    } for d in drafts[:limit]]
    return {
        "success": True,
        "destino": "pagina_exames_rascunho",
        "modo_leitura": parsed["modo"],
        "mapping": parsed["mapping"],
        "cpf_mapeado": bool(parsed["mapping"].get("cpf")) or bool(parsed.get("aso_text")),
        "modelo_aplicado": bool(price_by_column),
        "funcionarios": len(grupos),
        "localizados": sum(1 for d in drafts if d["localizado"]),
        "nao_localizados": nao_localizados,
        "linhas_sem_cpf": sem_cpf,
        "total_valor": round(sum(d["total"] for d in drafts), 2),
        "sample": sample,
    }


def import_to_medical_exams(db: Session, content: bytes, ext: str, filename: str = "",
                            price_model_id: Optional[int] = None,
                            cc_hints: Optional[List[str]] = None) -> Dict[str, Any]:
    """Cria RASCUNHOS na página de Exames (MedicalExam), um por funcionário (CPF),
    com funcionário e centro de custo resolvidos pela varredura (CPF)."""
    result = {
        "success": True, "destino": "pagina_exames_rascunho", "modo_leitura": None,
        "rascunhos_criados": 0, "linhas_sem_cpf": 0, "funcionarios": 0,
        "localizados": 0, "nao_localizados": [], "por_funcionario": [], "erros": [], "mapping": {},
    }
    try:
        parsed = read_and_map(content, ext, filename)
        result["modo_leitura"] = parsed["modo"]
        result["mapping"] = parsed["mapping"]
        if not parsed["mapping"].get("cpf") and not parsed.get("aso_text"):
            result["success"] = False
            result["erros"].append("CPF não encontrado — o vínculo do exame é por CPF.")
            return result

        recs = extract_records(parsed)
        grupos, sem_cpf = _group_by_funcionario(recs)
        result["linhas_sem_cpf"] = sem_cpf
        result["funcionarios"] = len(grupos)
        name_map = _load_identification(db)
        price_by_column = _load_price_model(db, price_model_id)
        result["modelo_aplicado"] = bool(price_by_column)
        resolve = _build_cpf_resolver(cc_hints)

        for (cpf, data_key), items in grupos.items():
            try:
                d = _build_draft(cpf, data_key, items, name_map, price_by_column, resolve(cpf))
                if not d["localizado"]:
                    result["nao_localizados"].append(cpf)
                else:
                    result["localizados"] += 1
                from datetime import date as _date
                data_val = _date.fromisoformat(data_key) if data_key else None
                exam = MedicalExam(
                    nome_funcionario=d["nome"] or cpf,
                    cpf=cpf,
                    numcad=d["numcad"],
                    codccu=d["codccu"],
                    nome_ccu=d["nome_ccu"],
                    data_exame=data_val,
                    origem="upload",
                    status="rascunho",
                    total=d["total"],
                    **d["cols"],
                )
                db.add(exam)
                result["rascunhos_criados"] += 1
                result["por_funcionario"].append({
                    "cpf": cpf, "nome": d["nome"], "numcad": d["numcad"],
                    "centro_custo": d["codccu"], "nome_ccu": d["nome_ccu"], "localizado": d["localizado"],
                    "total": d["total"], "exames": d["identificados"], "nao_identificados": d["nao_identificados"],
                })
            except Exception as e:
                result["erros"].append(f"CPF {cpf}: {str(e)}")
        db.commit()
    except IntakeError as e:
        result["success"] = False
        result["erros"].append(str(e))
        db.rollback()
    except Exception as e:
        result["success"] = False
        result["erros"].append(f"Erro geral: {str(e)}")
        db.rollback()
    return result
