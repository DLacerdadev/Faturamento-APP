"""Lançamento de treinamento por funcionário (processo #7 do faturamento).

Página + API do CRUD e importação de planilha (Excel/CSV). A importação
reaproveita os helpers de leitura de grid do serviço de exames (auto-detecção
de cabeçalho) e resolve o funcionário por CPF/matrícula usando a varredura
Senior (diretório cacheado). Autenticação idêntica aos demais routers.
"""
import re
import unicodedata
from io import BytesIO
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import or_
from pydantic import BaseModel

from app.db import get_db
from app.session_manager import validate_token
from app.routers.auth import get_token_from_request
from app.models.training_record import TrainingRecord
from app.services.exam_intake import _grid_from_excel_csv, IntakeError

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

ALLOWED_EXT = ("xlsx", "xls", "csv")


def _require_user(request: Request, db: Session):
    token = get_token_from_request(request)
    user = validate_token(token, db) if token else None
    if not user:
        raise HTTPException(status_code=401, detail="Não autenticado")
    return user, token


def _ext(filename: str) -> str:
    ext = (filename or "").split(".")[-1].lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(status_code=400, detail="Formato não suportado. Use Excel ou CSV.")
    return ext


# ------------------------- normalização / parsing -------------------------

def _norm(s: Any) -> str:
    """minúsculas, sem acento, só alfanumérico — para casar cabeçalhos."""
    s = str(s or "").strip().lower()
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", s)


def _normalize_cpf(cpf: Any) -> str:
    digits = re.sub(r"\D", "", str(cpf or ""))
    if not digits:
        return ""
    return digits.zfill(11) if len(digits) <= 11 else digits


def _parse_money(raw: Any) -> Optional[float]:
    """'1.234,56', 'R$ 45,90', '80,00' -> float. Vazio -> None."""
    if raw is None or str(raw).strip() == "":
        return None
    if isinstance(raw, (int, float)):
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None
    s = re.sub(r"[^\d,.\-]", "", str(raw))
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s) if s not in ("", "-", ".") else None
    except ValueError:
        return None


def _parse_int(raw: Any, default: Optional[int] = None) -> Optional[int]:
    if raw is None or str(raw).strip() == "":
        return default
    digits = re.sub(r"\D", "", str(raw))
    return int(digits) if digits else default


def _parse_date(raw: Any):
    """Devolve datetime.date ou None. Tolera formatos comuns e datas do Excel."""
    if raw is None or str(raw).strip() == "":
        return None
    if isinstance(raw, datetime):
        return raw.date()
    try:
        import pandas as pd
        if isinstance(raw, pd.Timestamp):
            return raw.to_pydatetime().date()
    except Exception:
        pass
    s = str(raw).strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


# Campos canônicos -> apelidos (normalizados).
FIELD_ALIASES: Dict[str, List[str]] = {
    "codccu": ["codccu", "centrodecusto", "centrocusto", "ccusto", "cc", "codigocc", "codccu"],
    "nome_ccu": ["nomeccu", "nomccu", "nomecentrocusto", "descricaocc", "centrodecustonome"],
    "cpf": ["cpf", "documento", "doc", "cpffuncionario"],
    "numcad": ["matricula", "numcad", "matriculafuncionario", "chapa", "codigofuncionario", "matr"],
    "nome": ["nome", "funcionario", "colaborador", "nomefuncionario", "nomecolaborador", "trabalhador"],
    "treinamento": ["treinamento", "curso", "nometreinamento", "nomecurso", "descricaotreinamento", "capacitacao", "nr"],
    "data": ["data", "datatreinamento", "dttreinamento", "dtcurso", "datacurso", "datarealizacao", "dt"],
    "quantidade": ["quantidade", "qtd", "qtde", "qtd"],
    "valor": ["valor", "vlvalor", "vlrtreinamento", "valortreinamento", "custo", "preco", "valortotal", "total"],
}

# Sem estes campos não dá pra lançar (mínimo: identificar funcionário + treinamento).
REQUIRED_FIELDS = ["treinamento"]


def _match_alias(normed_cell: str, aliases: List[str]) -> bool:
    if not normed_cell:
        return False
    for a in aliases:
        if normed_cell == a:
            return True
    for a in sorted(aliases, key=len, reverse=True):
        if len(a) >= 4 and a in normed_cell:
            return True
    return False


def _alias_hits(cells: List[Any]) -> int:
    normed = [_norm(c) for c in cells]
    hits = 0
    for aliases in FIELD_ALIASES.values():
        if any(_match_alias(nc, aliases) for nc in normed):
            hits += 1
    return hits


def _detect_header(grid: List[List[Any]], scan: int = 15) -> int:
    best_idx, best_hits = 0, -1
    for i in range(min(scan, len(grid))):
        h = _alias_hits(grid[i])
        if h > best_hits:
            best_hits, best_idx = h, i
    return best_idx


def _auto_map(columns: List[Any]) -> Dict[str, Optional[str]]:
    normed = [(_norm(c), str(c)) for c in columns]
    mapping: Dict[str, Optional[str]] = {}
    used = set()
    for field, aliases in FIELD_ALIASES.items():
        chosen = None
        for nc, raw in normed:                     # 1) match exato
            if raw in used:
                continue
            if nc in aliases:
                chosen = raw
                break
        if chosen is None:                          # 2) match por conteúdo
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


def _read_grid(content: bytes, ext: str) -> List[List[Any]]:
    grid = _grid_from_excel_csv(content, ext)
    return [r for r in grid if any(str(c).strip() for c in r)]


def _build_cpf_resolver():
    """cpf/numcad -> {numcad, nome, codccu, nomccu} a partir do diretório Senior
    cacheado (não dispara varredura pesada no upload)."""
    try:
        from app.services.senior_connector import peek_employee_directory
        directory = peek_employee_directory() or {}
    except Exception:
        directory = {}
    # índice por matrícula, para resolver quando só houver numcad no arquivo.
    by_numcad: Dict[str, Dict[str, Any]] = {}
    for cpf, info in directory.items():
        nc = info.get("numcad")
        if nc is not None:
            by_numcad[str(nc)] = info

    def resolve(cpf: str, numcad: Optional[int]):
        if cpf and cpf in directory:
            return directory[cpf]
        if numcad is not None and str(numcad) in by_numcad:
            return by_numcad[str(numcad)]
        return None

    return resolve


# ------------------------- página -------------------------

@router.get("/lancamento-treinamentos", response_class=HTMLResponse)
async def lancamento_treinamentos_page(request: Request, db: Session = Depends(get_db)):
    token = get_token_from_request(request)
    user = validate_token(token, db) if token else None
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse(
        "lancamento_treinamentos.html",
        {"request": request, "user": user, "token": token},
    )


# ------------------------- API -------------------------

class TrainingRecordIn(BaseModel):
    competencia: str
    codccu: str
    nome_ccu: Optional[str] = None
    employee_numcad: Optional[int] = None
    employee_nome: str
    cpf: Optional[str] = None
    training_catalog_id: Optional[int] = None
    treinamento_nome: str
    data_treinamento: Optional[str] = None
    quantidade: int = 1
    valor: Optional[float] = None


@router.get("/api/training-records")
async def list_training_records(request: Request, codccu: Optional[str] = None,
                                competencia: Optional[str] = None, q: Optional[str] = None,
                                db: Session = Depends(get_db)):
    _require_user(request, db)
    query = db.query(TrainingRecord)
    if codccu:
        query = query.filter(TrainingRecord.codccu == codccu.strip())
    if competencia:
        query = query.filter(TrainingRecord.competencia == competencia.strip())
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(
            TrainingRecord.employee_nome.ilike(like),
            TrainingRecord.treinamento_nome.ilike(like),
            TrainingRecord.cpf.ilike(like),
        ))
    items = query.order_by(TrainingRecord.competencia.desc(),
                           TrainingRecord.employee_nome).all()
    return {"success": True, "data": [t.to_dict() for t in items]}


@router.post("/api/training-records")
async def create_training_record(payload: TrainingRecordIn, request: Request,
                                 db: Session = Depends(get_db)):
    _require_user(request, db)
    competencia = (payload.competencia or "").strip()
    if not re.match(r"^\d{4}-\d{2}$", competencia):
        raise HTTPException(status_code=400, detail="Competência inválida (use AAAA-MM).")
    codccu = (payload.codccu or "").strip()
    if not codccu:
        raise HTTPException(status_code=400, detail="Informe o centro de custo (codccu).")
    nome = (payload.employee_nome or "").strip()
    if not nome:
        raise HTTPException(status_code=400, detail="Informe o nome do funcionário.")
    treino = (payload.treinamento_nome or "").strip()
    if not treino:
        raise HTTPException(status_code=400, detail="Informe o nome do treinamento.")

    rec = TrainingRecord(
        competencia=competencia,
        codccu=codccu,
        nome_ccu=(payload.nome_ccu or None),
        employee_numcad=payload.employee_numcad,
        employee_nome=nome,
        cpf=(_normalize_cpf(payload.cpf) or None) if payload.cpf else None,
        training_catalog_id=payload.training_catalog_id,
        treinamento_nome=treino,
        data_treinamento=_parse_date(payload.data_treinamento),
        quantidade=payload.quantidade or 1,
        valor=payload.valor,
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return {"success": True, "data": rec.to_dict()}


@router.post("/api/training-records/import")
async def import_training_records(request: Request, file: UploadFile = File(...),
                                  competencia: Optional[str] = Form(None),
                                  db: Session = Depends(get_db)):
    _require_user(request, db)
    ext = _ext(file.filename)
    content = await file.read()

    result = {"success": True, "criados": 0, "erros": [], "resumo": {}}
    try:
        grid = _read_grid(content, ext)
    except IntakeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Erro ao ler arquivo: {str(e)}")

    if not grid:
        raise HTTPException(status_code=400, detail="Arquivo vazio ou ilegível.")

    header_idx = _detect_header(grid)
    columns = [str(c).strip() for c in grid[header_idx]]
    mapping = _auto_map(columns)

    faltando = [f for f in REQUIRED_FIELDS if not mapping.get(f)]
    if faltando:
        result["success"] = False
        result["erros"].append("Colunas obrigatórias não encontradas: " + ", ".join(faltando))
        return result

    comp_default = (competencia or "").strip() or None
    if comp_default and not re.match(r"^\d{4}-\d{2}$", comp_default):
        raise HTTPException(status_code=400, detail="Competência inválida (use AAAA-MM).")

    resolve = _build_cpf_resolver()
    localizados = 0
    por_competencia: Dict[str, int] = {}
    data_rows = grid[header_idx + 1:]

    for i, r in enumerate(data_rows):
        linha = header_idx + i + 2
        row = {col: (r[j] if j < len(r) else "") for j, col in enumerate(columns)}

        def g(field):
            col = mapping.get(field)
            return row.get(col) if col else None

        treino = str(g("treinamento") or "").strip()
        cpf = _normalize_cpf(g("cpf"))
        numcad = _parse_int(g("numcad"))
        nome = str(g("nome") or "").strip()
        codccu = str(g("codccu") or "").strip()
        nome_ccu = str(g("nome_ccu") or "").strip() or None
        data_val = _parse_date(g("data"))
        quantidade = _parse_int(g("quantidade"), default=1) or 1
        valor = _parse_money(g("valor"))

        # linha em branco -> ignora sem erro
        if not treino and not cpf and not numcad and not nome:
            continue
        if not treino:
            result["erros"].append(f"Linha {linha}: sem nome do treinamento.")
            continue

        # resolve funcionário/CC pela varredura Senior (prioridade sobre o arquivo)
        info = resolve(cpf, numcad)
        if info:
            localizados += 1
            nome = info.get("nome") or nome
            if numcad is None and info.get("numcad") is not None:
                numcad = info.get("numcad")
            codccu = codccu or (info.get("codccu") or "")
            nome_ccu = nome_ccu or info.get("nomccu")

        if not nome:
            result["erros"].append(f"Linha {linha}: sem nome do funcionário (não localizado por CPF/matrícula).")
            continue

        # competência: da coluna de data, senão o default do formulário
        comp = data_val.strftime("%Y-%m") if data_val else comp_default
        if not comp:
            result["erros"].append(f"Linha {linha}: sem competência (informe no formulário ou uma data na planilha).")
            continue

        rec = TrainingRecord(
            competencia=comp,
            codccu=codccu or None,
            nome_ccu=nome_ccu,
            employee_numcad=numcad,
            employee_nome=nome,
            cpf=cpf or None,
            treinamento_nome=treino,
            data_treinamento=data_val,
            quantidade=quantidade,
            valor=valor,
        )
        db.add(rec)
        result["criados"] += 1
        por_competencia[comp] = por_competencia.get(comp, 0) + 1

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao gravar: {str(e)}")

    result["resumo"] = {
        "linhas_lidas": len(data_rows),
        "criados": result["criados"],
        "localizados": localizados,
        "por_competencia": por_competencia,
        "mapping": mapping,
    }
    return result


@router.delete("/api/training-records/{rec_id}")
async def delete_training_record(rec_id: int, request: Request, db: Session = Depends(get_db)):
    _require_user(request, db)
    rec = db.query(TrainingRecord).filter(TrainingRecord.id == rec_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Lançamento não encontrado")
    db.delete(rec)
    db.commit()
    return {"success": True}
