"""Relatório de Conciliação Contábil — Etapa 3 do Plano de Execução.

Demonstra a ponte entre a competência inteira (todos os CODCAL da folha) e o
recorte mensal que a contabilidade confere no relatório da Senior. A geração
consulta o WS ao vivo em segundo plano (mesmo padrão das exportações) e o
resultado NÃO é persistido — a planilha exportada é o registro da conferência.

Acesso: gestor+ (tela, geração, classificação e export). Tudo auditado.
"""
import json
import logging
import threading
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.db import get_db, SessionLocal
from app.services.permissions import require_role, get_request_user, has_role
from app.services.audit import audit
from app.services import export_jobs
from app.services import conciliacao as conc
from app.routers.auth import get_token_from_request
from app.models.codcal_classification import CodcalClassification

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

TELOS_NUMEMP = 6


# ----------------------------------------------------------------------------
# Tela
# ----------------------------------------------------------------------------
@router.get("/conciliacao", response_class=HTMLResponse)
async def conciliacao_page(request: Request, db: Session = Depends(get_db)):
    user = get_request_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    if not has_role(user, "gestor"):
        # Operador não tem acesso — volta ao dashboard (o menu já é gated).
        return RedirectResponse(url="/dashboard", status_code=303)

    # Lista de CCUs para o filtro — degrada com aviso se o WS/cache não responder.
    ccus = []
    ccu_erro = None
    try:
        from app.services.senior_connector import fetch_all_cost_centers
        ccus = fetch_all_cost_centers()
    except Exception as exc:  # nunca quebra o carregamento da tela por causa da lista
        logger.warning("Conciliação: falha ao carregar CCUs: %s", exc)
        ccu_erro = "Não foi possível carregar a lista de centros de custo agora. Informe o código manualmente ou tente de novo."

    token = get_token_from_request(request)
    return templates.TemplateResponse(
        "conciliacao.html",
        {"request": request, "user": user, "token": token, "ccus": ccus, "ccu_erro": ccu_erro},
    )


# ----------------------------------------------------------------------------
# Geração (job assíncrono)
# ----------------------------------------------------------------------------
class ConciliacaoGerarIn(BaseModel):
    periodo: str                       # 'YYYY-MM-DD' ou 'YYYY-MM'
    codccu: Optional[str] = None       # ausente = todos os CCUs

    @field_validator("periodo")
    @classmethod
    def _periodo_ok(cls, v: str) -> str:
        v = (v or "").strip()
        if len(v) == 7:  # 'YYYY-MM' -> primeiro dia
            v = v + "-01"
        try:
            datetime.strptime(v[:10], "%Y-%m-%d")
        except ValueError:
            raise ValueError("periodo deve ser 'YYYY-MM' ou 'YYYY-MM-DD'")
        return v[:10]


def _carregar_classificacoes() -> dict:
    """Mapa codcal -> {recorte_mensal, descricao, origem} (sessão própria)."""
    db = SessionLocal()
    try:
        out = {}
        for c in db.query(CodcalClassification).all():
            out[c.codcal] = {
                "recorte_mensal": bool(c.recorte_mensal),
                "descricao": c.descricao,
                "origem": c.origem or "manual",
            }
        return out
    finally:
        db.close()


def _run_conciliacao_job(job_id: str, periodo: str, codccu: Optional[str],
                         username: Optional[str]):
    from app.services.senior_connector import fetch_payroll, fetch_all_cost_centers
    try:
        export_jobs.set_running(job_id, "Buscando folha na Senior…")

        if codccu:
            ccu_param = [codccu]
        else:
            centers = fetch_all_cost_centers()
            ccu_param = [c.get("codccu") for c in centers if c.get("codccu")]

        def _cb(done, total):
            export_jobs.set_progress(job_id, done, total, f"CCU {done}/{total}")

        rows = fetch_payroll(periodo, numemp=TELOS_NUMEMP, codccu=ccu_param, progress_cb=_cb)

        classificacoes = _carregar_classificacoes()
        resultado = conc.montar_conciliacao(rows, classificacoes)

        # Metadados (snapshot do CCU quando filtrado)
        nomccu = None
        if codccu:
            for r in rows:
                if str(r.get("codccu")) == str(codccu):
                    nomccu = r.get("nomccu")
                    break
        resultado.update({
            "periodo": periodo,
            "codccu": codccu,
            "nomccu": nomccu,
            "gerado_em": datetime.utcnow().isoformat() + "Z",
            "gerado_por": username,
        })

        content = json.dumps(resultado, ensure_ascii=False).encode("utf-8")
        fname = f"conciliacao_{periodo}" + (f"_{codccu}" if codccu else "") + ".json"
        export_jobs.finish_ok(job_id, content, fname, "application/json")
    except Exception as exc:
        logger.exception("Falha no job de conciliação %s", job_id)
        export_jobs.finish_error(job_id, str(exc))


@router.post("/api/conciliacao/gerar")
async def gerar_conciliacao(payload: ConciliacaoGerarIn, request: Request,
                            db: Session = Depends(get_db)):
    user = require_role(request, db, "gestor")
    job = export_jobs.create_job(
        descricao=f"conciliacao {payload.periodo}" + (f" ccu={payload.codccu}" if payload.codccu else ""),
        user_id=user.id,
        username=user.username,
    )
    audit(request, "conciliacao.gerar", entidade="conciliacao", entidade_id=job.id,
          detalhe={"periodo": payload.periodo, "codccu": payload.codccu}, user=user)
    threading.Thread(
        target=_run_conciliacao_job,
        args=(job.id, payload.periodo, payload.codccu, user.username),
        daemon=True,
    ).start()
    return {"success": True, "job_id": job.id}


@router.get("/api/conciliacao/status/{job_id}")
async def status_conciliacao(job_id: str, request: Request, db: Session = Depends(get_db)):
    require_role(request, db, "gestor")
    job = export_jobs.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job não encontrado (pode ter expirado). Gere novamente.")
    return job.public()


@router.get("/api/conciliacao/resultado/{job_id}")
async def resultado_conciliacao(job_id: str, request: Request, db: Session = Depends(get_db)):
    require_role(request, db, "gestor")
    job = export_jobs.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Resultado não encontrado (pode ter expirado). Gere novamente.")
    if job.status == "error":
        raise HTTPException(status_code=400, detail=job.error or "Falha ao gerar a conciliação.")
    if job.status != "done" or not job.content:
        raise HTTPException(status_code=409, detail="Conciliação ainda em processamento.")
    return json.loads(job.content)


@router.get("/api/conciliacao/export/{job_id}")
async def export_conciliacao(job_id: str, request: Request, db: Session = Depends(get_db)):
    user = require_role(request, db, "gestor")
    job = export_jobs.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Resultado não encontrado (pode ter expirado). Gere novamente.")
    if job.status != "done" or not job.content:
        raise HTTPException(status_code=409, detail="Conciliação ainda em processamento.")

    resultado = json.loads(job.content)
    xlsx = conc.conciliacao_para_xlsx(resultado)
    periodo = resultado.get("periodo", "")
    codccu = resultado.get("codccu")
    fname = f"Conciliacao_{periodo}" + (f"_{codccu}" if codccu else "") + ".xlsx"

    audit(request, "conciliacao.export", entidade="conciliacao", entidade_id=job_id,
          detalhe={"arquivo": fname, "periodo": periodo, "codccu": codccu}, user=user)

    from io import BytesIO
    return StreamingResponse(
        BytesIO(xlsx),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# ----------------------------------------------------------------------------
# Classificação de CODCAL (global, gestor+)
# ----------------------------------------------------------------------------
class ClassificacaoIn(BaseModel):
    descricao: Optional[str] = None
    recorte_mensal: bool
    observacao: Optional[str] = None
    origem: str = "manual"

    @field_validator("origem")
    @classmethod
    def _origem_ok(cls, v: str) -> str:
        v = (v or "manual").strip().lower()
        if v not in ("manual", "heuristica"):
            raise ValueError("origem deve ser 'manual' ou 'heuristica'")
        return v


@router.get("/api/conciliacao/classificacoes")
async def list_classificacoes(request: Request, db: Session = Depends(get_db)):
    require_role(request, db, "gestor")
    items = db.query(CodcalClassification).order_by(CodcalClassification.codcal).all()
    return {"success": True, "items": [c.to_dict() for c in items]}


@router.put("/api/conciliacao/classificacoes/{codcal}")
async def upsert_classificacao(codcal: int, payload: ClassificacaoIn, request: Request,
                               db: Session = Depends(get_db)):
    user = require_role(request, db, "gestor")
    c = db.query(CodcalClassification).filter(CodcalClassification.codcal == codcal).first()
    antes = c.to_dict() if c else None
    if not c:
        c = CodcalClassification(codcal=codcal)
        db.add(c)
    c.descricao = payload.descricao
    c.recorte_mensal = payload.recorte_mensal
    c.observacao = payload.observacao
    c.origem = payload.origem
    db.commit()
    db.refresh(c)
    audit(request, "conciliacao.classificar", entidade="codcal_classification", entidade_id=codcal,
          detalhe={"antes": antes, "depois": c.to_dict()}, user=user)
    return {"success": True, "item": c.to_dict()}


@router.delete("/api/conciliacao/classificacoes/{codcal}")
async def delete_classificacao(codcal: int, request: Request, db: Session = Depends(get_db)):
    user = require_role(request, db, "gestor")
    c = db.query(CodcalClassification).filter(CodcalClassification.codcal == codcal).first()
    if not c:
        raise HTTPException(status_code=404, detail="Classificação não encontrada")
    antes = c.to_dict()
    db.delete(c)
    db.commit()
    audit(request, "conciliacao.classificar", entidade="codcal_classification", entidade_id=codcal,
          detalhe={"antes": antes, "depois": None}, user=user)
    return {"success": True, "message": "Classificação removida (codcal volta a não classificado)."}
