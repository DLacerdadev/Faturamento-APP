from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse, RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload
from pydantic import BaseModel
from datetime import datetime, date
from io import BytesIO
from typing import Optional, List
from urllib.parse import quote
from app.db import get_db
from app.models.report import Report
from app.services.report_generator import generate_customer_report, generate_custom_report
from app.config import TEMPLATES_DIR

from app.routers.auth import require_login

# Todas as rotas exigem login (dependency no nível do router).
router = APIRouter(prefix="/api/reports", tags=["reports"],
                   dependencies=[Depends(require_login)])

# Router SEM prefixo para servir a PÁGINA HTML do relatório de compras
# (/relatorio-compras). Registrado em app/main.py junto com os demais.
page_router = APIRouter(tags=["reports_pages"])
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

class ReportRequest(BaseModel):
    name: str
    report_type: str
    file_format: str = "xlsx"
    description: str | None = None
    parameters: dict | None = None

@router.get("/")
async def list_reports(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    reports = db.query(Report).order_by(Report.created_at.desc()).offset(skip).limit(limit).all()
    return [report.to_dict() for report in reports]

@router.get("/{report_id}")
async def get_report(report_id: int, db: Session = Depends(get_db)):
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return report.to_dict()

@router.post("/generate")
async def generate_report(request: ReportRequest, db: Session = Depends(get_db)):
    report = None
    try:
        report = Report(
            name=request.name,
            report_type=request.report_type,
            file_format=request.file_format,
            description=request.description,
            status="processing",
            parameters=str(request.parameters) if request.parameters else None
        )
        db.add(report)
        db.commit()
        db.refresh(report)
        
        if request.report_type == "customers":
            file_path = await generate_customer_report(
                db=db,
                file_format=request.file_format,
                parameters=request.parameters or {}
            )
        else:
            file_path = await generate_custom_report(
                db=db,
                report_type=request.report_type,
                file_format=request.file_format,
                parameters=request.parameters or {}
            )
        
        report.file_path = file_path
        report.status = "completed"
        report.completed_at = datetime.utcnow()
        db.commit()
        db.refresh(report)
        
        return report.to_dict()
        
    except Exception as e:
        if report is not None:
            report.status = "failed"
            db.commit()
        raise HTTPException(status_code=500, detail=f"Error generating report: {str(e)}")

@router.get("/{report_id}/download")
async def download_report(report_id: int, db: Session = Depends(get_db)):
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    
    if not report.file_path or report.status != "completed":
        raise HTTPException(status_code=400, detail="Report not available for download")
    
    return FileResponse(
        path=report.file_path,
        filename=f"{report.name}.{report.file_format}",
        media_type="application/octet-stream"
    )

@router.delete("/{report_id}")
async def delete_report(report_id: int, db: Session = Depends(get_db)):
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    db.delete(report)
    db.commit()
    return {"message": "Report deleted successfully"}


# ============================================================================
# RELATÓRIO DE COMPRAS/ENTREGAS POR FUNCIONÁRIO (layout "zip")
# Fontes: EpiPurchasePackage/Item (epi|uniforme|equipamento) + MedicalExam
# (exames) + TrainingRecord (treinamentos), casados por período/CC.
# ============================================================================

# Mapa do filtro de status da tela -> status persistidos no pacote.
# 'solicitado' = pedido ainda não confirmado (rascunho/validado).
# 'confirmado' = pedido confirmado (entra no faturamento).
_STATUS_SOLICITADO = ("rascunho", "validado")
_STATUS_CONFIRMADO = ("confirmado",)


def _parse_date(s: Optional[str]) -> Optional[date]:
    """Converte 'YYYY-MM-DD' (ou 'YYYY-MM') em date; None se vazio/ inválido."""
    if not s:
        return None
    s = str(s).strip()
    # Tenta 'YYYY-MM-DD' (10 chars) e depois 'YYYY-MM' (7 chars).
    for fmt, n in (("%Y-%m-%d", 10), ("%Y-%m", 7)):
        try:
            return datetime.strptime(s[:n], fmt).date()
        except ValueError:
            continue
    return None


def _competencia_no_periodo(competencia: Optional[str], di: Optional[date], dfim: Optional[date]) -> bool:
    """Testa se uma competência 'YYYY-MM' (TrainingRecord) cai no período [di, dfim].
    Compara pelo primeiro dia do mês da competência."""
    if not competencia:
        return True
    try:
        comp = datetime.strptime(str(competencia)[:7], "%Y-%m").date()
    except ValueError:
        return True
    if di and comp < di.replace(day=1):
        return False
    if dfim and comp > dfim:
        return False
    return True


def _query_packages(db: Session, codccu, di: Optional[date], dfim: Optional[date], status: str):
    """Aplica os filtros da tela sobre EpiPurchasePackage e devolve a query
    (com items pré-carregados). Período compara o mês do pacote (mes_ano) contra
    [data_ini, data_fim]."""
    from app.models.epi_purchase import EpiPurchasePackage
    q = db.query(EpiPurchasePackage).options(joinedload(EpiPurchasePackage.items))
    if codccu:
        q = q.filter(EpiPurchasePackage.codccu == str(codccu))
    if di:
        q = q.filter(EpiPurchasePackage.mes_ano >= di.replace(day=1))
    if dfim:
        q = q.filter(EpiPurchasePackage.mes_ano <= dfim)
    st = (status or "todos").lower()
    if st == "solicitado":
        q = q.filter(EpiPurchasePackage.status.in_(_STATUS_SOLICITADO))
    elif st == "confirmado":
        q = q.filter(EpiPurchasePackage.status.in_(_STATUS_CONFIRMADO))
    return q.order_by(EpiPurchasePackage.mes_ano.desc(), EpiPurchasePackage.id.desc())


def _pacote_resumo(pkg) -> dict:
    """Resumo de um pedido para a listagem da tela (Buscar)."""
    items = list(pkg.items or [])
    numcads = {it.employee_numcad for it in items if it.employee_numcad is not None}
    valor = round(sum((it.valor_total or 0.0) for it in items), 2)
    return {
        "id": pkg.id,
        "competencia": pkg.mes_ano.strftime("%m/%Y") if pkg.mes_ano else "",
        "codccu": pkg.codccu or "",
        "categoria": pkg.categoria or "epi",
        "n_funcionarios": len(numcads),
        "n_itens": len(items),
        "valor_total": valor,
        "status": pkg.status or "rascunho",
    }


@router.get("/compras/buscar")
async def buscar_pedidos_compra(
    codccu: Optional[str] = None,
    data_ini: Optional[str] = None,
    data_fim: Optional[str] = None,
    status: str = "todos",
    db: Session = Depends(get_db),
):
    """Lista os PEDIDOS de compra (EpiPurchasePackage) do período/CC/status
    escolhidos na tela. Cada item traz id, competência, CC, categoria, nº de
    funcionários, nº de itens, valor total e status — para render com checkbox."""
    di = _parse_date(data_ini)
    dfim = _parse_date(data_fim)
    pkgs = _query_packages(db, codccu, di, dfim, status).all()
    return {"status": "ok", "data": [_pacote_resumo(p) for p in pkgs]}


class EmitirComprasInput(BaseModel):
    """Payload da emissão do Excel. `package_ids` são os pedidos selecionados;
    os demais campos ecoam os filtros (usados p/ casar exames/treinamentos do
    MESMO período/CC e p/ nomear o arquivo)."""
    package_ids: List[int]
    codccu: Optional[str] = None
    data_ini: Optional[str] = None
    data_fim: Optional[str] = None
    status: str = "todos"


def _coletar_itens_compra(pkgs) -> List[dict]:
    """Achata os itens dos pedidos selecionados no formato esperado pelo serviço
    de montagem (categoria vem do pacote)."""
    saida = []
    for pkg in pkgs:
        categoria = pkg.categoria or "epi"
        codccu = pkg.codccu or ""
        for it in (pkg.items or []):
            saida.append({
                "categoria": categoria,
                "numcad": it.employee_numcad,
                "nome": it.employee_nome,
                "codccu": codccu,
                "nome_ccu": "",
                "descricao": it.descricao,
                "quantidade": it.quantidade,
            })
    return saida


def _coletar_exames(db: Session, codccu, di: Optional[date], dfim: Optional[date]) -> List[dict]:
    """Busca MedicalExam do mesmo período/CC e transforma cada registro numa
    entrada por exame do catálogo com valor > 0 (nome amigável + qtd=1).
    Ignora rascunhos (só exames confirmados entram no relatório)."""
    from app.models.medical_exam import MedicalExam
    from app.services.relatorio_compras import EXAM_FIELD_LABELS
    q = db.query(MedicalExam).filter(MedicalExam.status != "rascunho")
    if codccu:
        q = q.filter(MedicalExam.codccu == str(codccu))
    if di:
        q = q.filter(MedicalExam.data_exame >= di)
    if dfim:
        q = q.filter(MedicalExam.data_exame <= dfim)
    saida = []
    for ex in q.all():
        entradas = []
        for campo, rotulo in EXAM_FIELD_LABELS:
            val = getattr(ex, campo, 0) or 0
            if val and val > 0:
                entradas.append({"nome": rotulo, "quantidade": 1})
        if not entradas:
            continue
        saida.append({
            "numcad": ex.numcad,
            "nome": ex.nome_funcionario,
            "codccu": ex.codccu or "",
            "nome_ccu": ex.nome_ccu or "",
            "exames": entradas,
        })
    return saida


def _coletar_treinamentos(db: Session, codccu, di: Optional[date], dfim: Optional[date]) -> List[dict]:
    """Busca TrainingRecord do mesmo período/CC. Filtra por data_treinamento
    quando existir; senão, pela competência 'YYYY-MM'."""
    from app.models.training_record import TrainingRecord
    q = db.query(TrainingRecord)
    if codccu:
        q = q.filter(TrainingRecord.codccu == str(codccu))
    saida = []
    for tr in q.all():
        # Filtro de período: prioriza data_treinamento; cai p/ competência.
        if tr.data_treinamento is not None:
            if di and tr.data_treinamento < di:
                continue
            if dfim and tr.data_treinamento > dfim:
                continue
        else:
            if not _competencia_no_periodo(tr.competencia, di, dfim):
                continue
        saida.append({
            "numcad": tr.employee_numcad,
            "nome": tr.employee_nome,
            "codccu": tr.codccu or "",
            "nome_ccu": tr.nome_ccu or "",
            "treinamento_nome": tr.treinamento_nome,
            "quantidade": tr.quantidade or 1,
        })
    return saida


@router.post("/compras/emitir")
async def emitir_relatorio_compras(data: EmitirComprasInput, db: Session = Depends(get_db)):
    """Gera o Excel (layout zip) dos pedidos selecionados. Junta os itens das
    compras (por categoria) com exames e treinamentos do MESMO período/CC e
    devolve o .xlsx como StreamingResponse."""
    from app.models.epi_purchase import EpiPurchasePackage
    from app.services.relatorio_compras import (
        montar_relatorio, relatorio_to_excel_bytes, gerar_nome_arquivo,
    )

    if not data.package_ids:
        raise HTTPException(status_code=400, detail="Nenhum pedido selecionado.")

    pkgs = (
        db.query(EpiPurchasePackage)
        .options(joinedload(EpiPurchasePackage.items))
        .filter(EpiPurchasePackage.id.in_(data.package_ids))
        .all()
    )
    if not pkgs:
        raise HTTPException(status_code=404, detail="Pedidos não encontrados.")

    di = _parse_date(data.data_ini)
    dfim = _parse_date(data.data_fim)

    itens_compra = _coletar_itens_compra(pkgs)
    exames = _coletar_exames(db, data.codccu, di, dfim)
    treinamentos = _coletar_treinamentos(db, data.codccu, di, dfim)

    linhas = montar_relatorio(itens_compra, exames, treinamentos)
    xlsx = relatorio_to_excel_bytes(linhas)
    filename = gerar_nome_arquivo(data.data_ini, data.data_fim, data.codccu)

    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"
    }
    return StreamingResponse(
        BytesIO(xlsx),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )


# ---------------------------------------------------------------------------
# PÁGINA HTML — /relatorio-compras (protegida por login, tema institucional)
# ---------------------------------------------------------------------------
@page_router.get("/relatorio-compras", response_class=HTMLResponse)
async def relatorio_compras_page(request: Request, db: Session = Depends(get_db)):
    # Reusa o mesmo mecanismo de auth por cookie das demais páginas.
    from app.routers.auth import get_token_from_request
    from app.session_manager import session_manager
    from app.models.user import User

    token = get_token_from_request(request)
    session = session_manager.get_session(token) if token else None
    if not session:
        return RedirectResponse(url="/login", status_code=303)
    user = db.query(User).filter(User.id == session["user_id"]).first()
    return templates.TemplateResponse(
        "relatorio_compras.html",
        {"request": request, "user": user, "token": token},
    )
