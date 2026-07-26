"""Assistente de IA do faturamento — endpoint de chat (spec 010).

Rotas:
  * POST /ia/chat  (gestor+, auditado): recebe {model_id, message, snapshot?,
    session_id?}, chama o LLM LOCAL (ai_service), devolve {reply, ops[]} com ops
    já VALIDADAS contra o schema do contrato grid-ops. Persiste o histórico
    (AiChatSession/AiChatMessage).
  * GET /ia/chat   (gestor+): página standalone de dev/test da IA (sem depender
    da planilha da 009).

Padrões do projeto: RBAC via require_role("gestor"); auditoria via audit();
snapshot SEM PII (o get_snapshot da 009 já é limpo, e o ai_service sanitiza de
novo por defesa em profundidade). Chamadas ao LLM podem ser longas: expomos
também um modo assíncrono via export_jobs (thread + poll) para fugir do timeout
do proxy — o front pode usar o síncrono (rápido) ou o job.

A IA NUNCA escreve na planilha: só devolve ops; o front (009) aplica via
window.FaturamentoGrid.applyOps.
"""
import logging
import threading
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db, SessionLocal
from app.routers.auth import get_token_from_request
from app.services.permissions import require_role, get_request_user, has_role
from app.services.audit import audit
from app.services import export_jobs
from app.services import ai_service
from app.services.ai_service import LLMUnavailableError
from app.models.billing_model import BillingModel

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


# ---------------------------------------------------------------------------
# Garantia de tabelas de chat (idempotente): create_all das tabelas novas.
# A orquestradora também registra em app/db.py (INTEGRATION-NOTES); aqui é
# defesa para o router funcionar antes/depois do merge. create_all não altera
# tabelas existentes e é no-op se já criadas.
# ---------------------------------------------------------------------------
def _ensure_tables() -> None:
    try:
        from app.db import engine
        from app.models.ai_chat_session import AiChatSession, AiChatMessage
        AiChatSession.__table__.create(bind=engine, checkfirst=True)
        AiChatMessage.__table__.create(bind=engine, checkfirst=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Não foi possível garantir tabelas de chat IA: %s", exc)


_ensure_tables()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _load_model(model_id: Optional[int], db: Session) -> Optional[BillingModel]:
    if not model_id:
        return None
    return db.query(BillingModel).filter(BillingModel.id == model_id).first()


def _persist_turn(db: Session, session_id: Optional[int], user, model_id: Optional[int],
                  message: str, reply: str, ops: List[Dict[str, Any]]) -> int:
    """Grava (ou cria) a sessão e os dois turnos (user + assistant). Nunca
    derruba o endpoint: erro de persistência vira warning."""
    from app.models.ai_chat_session import AiChatSession, AiChatMessage
    try:
        sess = None
        if session_id:
            sess = db.query(AiChatSession).filter(AiChatSession.id == session_id).first()
        if sess is None:
            sess = AiChatSession(
                user_id=getattr(user, "id", None),
                username=getattr(user, "username", None),
                model_id=model_id,
                titulo=(message or "").strip()[:80] or "Conversa",
            )
            db.add(sess)
            db.flush()
        db.add(AiChatMessage(session_id=sess.id, role="user", content=message))
        db.add(AiChatMessage(session_id=sess.id, role="assistant", content=reply, ops=ops))
        db.commit()
        return sess.id
    except Exception as exc:  # noqa: BLE001
        logger.warning("Falha ao persistir histórico de chat IA: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
        return session_id or 0


# ---------------------------------------------------------------------------
# Página standalone de dev/test
# ---------------------------------------------------------------------------
@router.get("/ia/chat", response_class=HTMLResponse)
async def ia_chat_page(request: Request, db: Session = Depends(get_db)):
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
        "ai_chat.html",
        {
            "request": request,
            "user": user,
            "token": token,
            "modelos": [m.to_dict() for m in modelos],
            "llm_configurado": ai_service.is_configured(),
        },
    )


# ---------------------------------------------------------------------------
# POST /ia/chat — síncrono
# ---------------------------------------------------------------------------
class ChatIn(BaseModel):
    model_id: Optional[int] = None
    message: str
    snapshot: Optional[Dict[str, Any]] = None
    session_id: Optional[int] = None


@router.post("/ia/chat")
async def ia_chat(payload: ChatIn, request: Request, db: Session = Depends(get_db)):
    """Chat da IA: {model_id, message, snapshot?} -> {reply, ops[]}.

    Degrada com erro claro (503) se o LLM local estiver indisponível — a tela
    trata e não quebra.
    """
    user = require_role(request, db, "gestor")
    mensagem = (payload.message or "").strip()
    if not mensagem:
        raise HTTPException(status_code=400, detail="Envie uma mensagem para a IA.")

    model = _load_model(payload.model_id, db)
    # Snapshot: usa o enviado pelo front (já sem PII, da 009) OU deriva do modelo.
    snapshot = payload.snapshot
    if snapshot is None and model is not None:
        from app.services.grid_serialization import get_snapshot
        snapshot = get_snapshot(model)

    try:
        result = ai_service.chat(payload.model_id, mensagem, snapshot, model=model)
    except LLMUnavailableError as exc:
        audit(request, "ia.chat", entidade="billing_models",
              entidade_id=payload.model_id, status="erro",
              detalhe={"erro": str(exc)[:200]}, user=user)
        raise HTTPException(
            status_code=503,
            detail=f"Assistente de IA indisponível: {exc}",
        )
    except Exception as exc:  # noqa: BLE001 — nunca vaza exceção crua pra tela
        logger.exception("Erro inesperado no chat IA")
        audit(request, "ia.chat", entidade="billing_models",
              entidade_id=payload.model_id, status="erro",
              detalhe={"erro": str(exc)[:200]}, user=user)
        raise HTTPException(status_code=500, detail="Falha ao processar o pedido da IA.")

    reply = result.get("reply") or ""
    ops = result.get("ops") or []
    rejected = result.get("rejected") or []

    session_id = _persist_turn(
        db, payload.session_id, user, payload.model_id, mensagem, reply, ops
    )

    audit(request, "ia.chat", entidade="billing_models", entidade_id=payload.model_id,
          detalhe={"n_ops": len(ops), "rejeitadas": len(rejected),
                   "session_id": session_id}, user=user)

    return {
        "success": True,
        "reply": reply,
        "ops": ops,
        "rejected": rejected,
        "session_id": session_id,
    }


# ---------------------------------------------------------------------------
# POST /ia/chat/async — modo job (chamada longa) + status/result
# ---------------------------------------------------------------------------
def _run_chat_job(job_id: str, model_id: Optional[int], message: str,
                  snapshot: Optional[Dict[str, Any]]):
    db = SessionLocal()
    try:
        export_jobs.set_running(job_id, "Consultando a IA…")
        model = _load_model(model_id, db)
        snap = snapshot
        if snap is None and model is not None:
            from app.services.grid_serialization import get_snapshot
            snap = get_snapshot(model)
        result = ai_service.chat(model_id, message, snap, model=model)
        import json as _json
        content = _json.dumps({
            "reply": result.get("reply") or "",
            "ops": result.get("ops") or [],
            "rejected": result.get("rejected") or [],
        }, ensure_ascii=False).encode("utf-8")
        export_jobs.finish_ok(job_id, content, "ia_chat.json", "application/json")
    except LLMUnavailableError as exc:
        export_jobs.finish_error(job_id, f"Assistente de IA indisponível: {exc}")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Falha no job de chat IA %s", job_id)
        export_jobs.finish_error(job_id, str(exc))
    finally:
        db.close()


@router.post("/ia/chat/async")
async def ia_chat_async(payload: ChatIn, request: Request, db: Session = Depends(get_db)):
    """Versão assíncrona (thread + poll) para chamadas longas ao LLM — devolve
    job_id; o front consulta /ia/chat/status/{job_id} e busca o resultado."""
    user = require_role(request, db, "gestor")
    mensagem = (payload.message or "").strip()
    if not mensagem:
        raise HTTPException(status_code=400, detail="Envie uma mensagem para a IA.")
    job = export_jobs.create_job(
        descricao=f"ia chat model={payload.model_id}",
        user_id=user.id, username=user.username,
    )
    audit(request, "ia.chat_async", entidade="billing_models",
          entidade_id=payload.model_id, detalhe={"job_id": job.id}, user=user)
    threading.Thread(
        target=_run_chat_job,
        args=(job.id, payload.model_id, mensagem, payload.snapshot),
        daemon=True,
    ).start()
    return {"success": True, "job_id": job.id}


@router.get("/ia/chat/status/{job_id}")
async def ia_chat_status(job_id: str, request: Request, db: Session = Depends(get_db)):
    require_role(request, db, "gestor")
    job = export_jobs.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Consulta não encontrada (pode ter expirado).")
    return job.public()


@router.get("/ia/chat/result/{job_id}")
async def ia_chat_result(job_id: str, request: Request, db: Session = Depends(get_db)):
    require_role(request, db, "gestor")
    job = export_jobs.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Resultado não encontrado (pode ter expirado).")
    if job.status == "error":
        raise HTTPException(status_code=503, detail=job.error or "Falha na IA.")
    if job.status != "done" or not job.content:
        raise HTTPException(status_code=409, detail="IA ainda processando.")
    import json as _json
    return {"success": True, **_json.loads(job.content.decode("utf-8"))}
