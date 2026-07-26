"""Planilha de faturamento interativa (Univer) — spec 009.

Entrega a tela `/faturamento/planilha`, o motor de import/export do grid e o
endpoint `POST /api/faturamento/grid/apply-ops` (contrato grid-ops v1, dono 009).

Princípios:
  - "O que você vê é o que exporta" (SC-0): o botão **Consultar faturamento**
    monta o grid a partir dos MESMOS bytes .xlsx que seriam exportados —
    `_build_billing_export(...)` produz os bytes, `xlsx_to_grid(...)` os
    renderiza. O export do grid usa exatamente o mesmo caminho de bytes.
  - RBAC gestor+ em todas as rotas; ações auditadas.
  - Import/export longos (busca da folha na Senior) rodam em job de segundo
    plano (`export_jobs`), fugindo do timeout do proxy — mesmo padrão da
    conciliação (spec 004).

O ponto de montagem da IA (`<div id="ai-assistant-root">` + `window.FaturamentoGrid`)
mora no template/JS; este router só serve dados de estrutura (sem PII no snapshot).
"""
import logging
import threading
from io import BytesIO
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db, SessionLocal
from app.routers.auth import get_token_from_request
from app.services.permissions import require_role, get_request_user, has_role
from app.services.audit import audit
from app.services import export_jobs
from app.models.billing_model import BillingModel
from app.services.grid_serialization import (
    apply_ops,
    get_snapshot,
    model_to_grid,
    xlsx_to_grid,
)

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

_XLSX_MT = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# ---------------------------------------------------------------------------
# Tela
# ---------------------------------------------------------------------------
@router.get("/faturamento/planilha", response_class=HTMLResponse)
async def planilha_page(request: Request, db: Session = Depends(get_db)):
    user = get_request_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    if not has_role(user, "gestor"):
        return RedirectResponse(url="/dashboard", status_code=303)
    token = get_token_from_request(request)
    modelos = (
        db.query(BillingModel)
        .filter(BillingModel.ativo.is_(True))
        .order_by(BillingModel.is_base.desc(), BillingModel.nome)
        .all()
    )
    return templates.TemplateResponse(
        "faturamento_planilha.html",
        {
            "request": request,
            "user": user,
            "token": token,
            "modelos": [m.to_dict() for m in modelos],
        },
    )


# ---------------------------------------------------------------------------
# Carga do grid a partir do MODELO (sem PII — só cabeçalhos/estrutura)
# ---------------------------------------------------------------------------
def _load_model(model_id: int, db: Session) -> BillingModel:
    m = db.query(BillingModel).filter(BillingModel.id == model_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Modelo não encontrado")
    return m


@router.get("/api/faturamento/grid/model/{model_id}")
async def load_grid_from_model(model_id: int, request: Request, db: Session = Depends(get_db)):
    """Grid de CABEÇALHO do modelo (sem dados de funcionário) + snapshot."""
    require_role(request, db, "gestor")
    m = _load_model(model_id, db)
    return {
        "success": True,
        "grid": model_to_grid(m),
        "snapshot": get_snapshot(m),
    }


@router.get("/api/faturamento/grid/snapshot/{model_id}")
async def grid_snapshot(model_id: int, request: Request, db: Session = Depends(get_db)):
    """Snapshot de estrutura (SEM PII) — usado pela IA (spec 010) via window.FaturamentoGrid."""
    require_role(request, db, "gestor")
    m = _load_model(model_id, db)
    return {"success": True, "snapshot": get_snapshot(m)}


# ---------------------------------------------------------------------------
# apply-ops (contrato §3/§4 — dono 009)
# ---------------------------------------------------------------------------
class ApplyOpsIn(BaseModel):
    model_id: int
    ops: List[Dict[str, Any]] = []
    persist: bool = False  # grava no BillingModel quando gestor confirma


@router.post("/api/faturamento/grid/apply-ops")
async def grid_apply_ops(payload: ApplyOpsIn, request: Request, db: Session = Depends(get_db)):
    """Recebe {model_id, ops[]}, valida e aplica sobre o BillingModel/estrutura C1.

    Retorna `ApplyResult` (contrato §6). Por padrão NÃO persiste (dry-run que a
    IA/preview usam); `persist=true` grava a estrutura/colunas/params no modelo.
    """
    user = require_role(request, db, "gestor")
    m = _load_model(payload.model_id, db)

    result = apply_ops(m, payload.ops)

    if result.ok and payload.persist:
        novo = result.model or {}
        if "estrutura" in novo:
            m.estrutura = novo.get("estrutura")
        if "colunas" in novo:
            m.colunas = novo.get("colunas") or []
        if "campos_config" in novo:
            m.campos_config = novo.get("campos_config") or None
        for campo in ("encargos_pct", "taxa_adm_pct", "imposto_pct", "salario_formula"):
            if campo in novo:
                setattr(m, campo, novo.get(campo))
        db.commit()
        db.refresh(m)
        result.model = model_to_grid(m).get("meta")  # devolve estrutura, sem PII

    audit(request, "grid.apply_ops", entidade="billing_models", entidade_id=payload.model_id,
          detalhe={"n_ops": len(payload.ops), "aplicadas": result.applied,
                   "persist": payload.persist, "ok": result.ok,
                   "erros": result.errors[:5]}, user=user)
    return result.to_dict()


# ---------------------------------------------------------------------------
# Consultar faturamento -> preview FIEL do .xlsx (mesmo caminho de bytes)
# ---------------------------------------------------------------------------
class PreviewIn(BaseModel):
    modelo: str = "femsa"       # nome do modelo (resolvido em _build_billing_export)
    periodo: str                # YYYY-MM ou YYYY-MM-DD
    codccu: Optional[str] = None
    encargos_pct: Optional[float] = None
    taxa_adm_pct: Optional[float] = None
    imposto_pct: Optional[float] = None


def _normalizar_periodo(periodo: str) -> str:
    p = (periodo or "").strip()
    if len(p) == 7:  # YYYY-MM
        p = p + "-01"
    return p


def _run_preview_job(job_id: str, payload: Dict[str, Any]):
    """Job de segundo plano: gera os bytes .xlsx do faturamento (mesmo caminho do
    export) e armazena o CONTEÚDO no job. O grid é derivado desses bytes na
    entrega — preview e export saem, byte a byte, do mesmo lugar."""
    from app.routers.integrations import _build_billing_export
    db = SessionLocal()
    try:
        export_jobs.set_running(job_id, "Montando faturamento…")

        def _cb(done, total):
            export_jobs.set_progress(job_id, done, total, f"CCU {done}/{total}")

        content, filename, media_type = _build_billing_export(
            db,
            modelo=payload["modelo"],
            periodo=payload["periodo"],
            codccu=payload.get("codccu"),
            encargos_pct=payload.get("encargos_pct"),
            taxa_adm_pct=payload.get("taxa_adm_pct"),
            imposto_pct=payload.get("imposto_pct"),
            progress_cb=_cb,
        )
        export_jobs.finish_ok(job_id, content, filename, media_type)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Falha no preview de faturamento %s", job_id)
        export_jobs.finish_error(job_id, str(exc))
    finally:
        db.close()


@router.post("/api/faturamento/grid/preview")
async def preview_faturamento(payload: PreviewIn, request: Request, db: Session = Depends(get_db)):
    """Botão **Consultar faturamento**: enfileira a montagem do .xlsx do
    faturamento em segundo plano (foge do timeout do proxy) e devolve um job_id.
    O front consulta o status e busca o grid do preview quando pronto."""
    user = require_role(request, db, "gestor")
    periodo = _normalizar_periodo(payload.periodo)
    if len(periodo) < 7:
        raise HTTPException(status_code=400, detail="Informe a competência (YYYY-MM).")
    job = export_jobs.create_job(
        descricao=f"preview faturamento {periodo}" + (f" ccu={payload.codccu}" if payload.codccu else ""),
        user_id=user.id,
        username=user.username,
    )
    audit(request, "grid.preview", entidade="faturamento", entidade_id=job.id,
          detalhe={"modelo": payload.modelo, "periodo": periodo, "codccu": payload.codccu},
          user=user)
    threading.Thread(
        target=_run_preview_job,
        args=(job.id, {
            "modelo": payload.modelo, "periodo": periodo, "codccu": payload.codccu,
            "encargos_pct": payload.encargos_pct, "taxa_adm_pct": payload.taxa_adm_pct,
            "imposto_pct": payload.imposto_pct,
        }),
        daemon=True,
    ).start()
    return {"success": True, "job_id": job.id}


@router.get("/api/faturamento/grid/status/{job_id}")
async def preview_status(job_id: str, request: Request, db: Session = Depends(get_db)):
    require_role(request, db, "gestor")
    job = export_jobs.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Consulta não encontrada (pode ter expirado). Gere novamente.")
    return job.public()


@router.get("/api/faturamento/grid/result/{job_id}")
async def preview_result(job_id: str, request: Request, db: Session = Depends(get_db)):
    """Grid do preview: converte os bytes .xlsx do job em grid (mesmo bytes que
    seriam baixados no export). Isto materializa a garantia preview == export."""
    require_role(request, db, "gestor")
    job = export_jobs.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Resultado não encontrado (pode ter expirado). Gere novamente.")
    if job.status == "error":
        raise HTTPException(status_code=400, detail=job.error or "Falha ao montar o faturamento.")
    if job.status != "done" or not job.content:
        raise HTTPException(status_code=409, detail="Faturamento ainda em processamento.")
    grid = xlsx_to_grid(job.content)
    return {"success": True, "grid": grid, "filename": job.filename}


@router.get("/api/faturamento/grid/export/{job_id}")
async def preview_export(job_id: str, request: Request, db: Session = Depends(get_db)):
    """Baixa o .xlsx do faturamento — os MESMOS bytes que o grid do preview
    renderizou (o preview é `xlsx_to_grid(bytes)`; aqui devolvemos `bytes`)."""
    user = require_role(request, db, "gestor")
    job = export_jobs.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Resultado não encontrado (pode ter expirado). Gere novamente.")
    if job.status != "done" or not job.content:
        raise HTTPException(status_code=409, detail="Faturamento ainda em processamento.")
    audit(request, "grid.export", entidade="faturamento", entidade_id=job_id,
          detalhe={"arquivo": job.filename}, user=user)
    return StreamingResponse(
        BytesIO(job.content),
        media_type=job.media_type or _XLSX_MT,
        headers={"Content-Disposition": f'attachment; filename="{job.filename or "faturamento.xlsx"}"'},
    )
