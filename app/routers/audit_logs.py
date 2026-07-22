"""Tela e API de consulta da trilha de auditoria (/auditoria) — somente admin.

APENAS LEITURA: a tabela audit_logs é imutável por contrato — este router não
expõe (e jamais deve expor) endpoint de criação, edição ou exclusão de
registros. A escrita acontece exclusivamente via app.services.audit.audit().
"""
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import get_db
from app.session_manager import validate_token
from app.routers.auth import get_token_from_request
from app.services.permissions import require_role, has_role
from app.models.audit_log import AuditLog

router = APIRouter(tags=["auditoria"])
templates = Jinja2Templates(directory="app/templates")


def _parse_data(valor: str, campo: str) -> datetime:
    """Converte string ISO (data ou data+hora) em datetime — 400 se inválida."""
    try:
        return datetime.fromisoformat(valor.strip())
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=400,
            detail=f"Data inválida no filtro '{campo}': use o formato ISO (aaaa-mm-dd).",
        )


@router.get("/auditoria", response_class=HTMLResponse)
async def auditoria_page(request: Request, db: Session = Depends(get_db)):
    """Tela de auditoria — somente admin. Sem login vai pro /login; logado sem
    papel suficiente volta pro dashboard (padrão das telas HTML)."""
    token = get_token_from_request(request)
    user = validate_token(token, db) if token else None
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    if not has_role(user, "admin"):
        return RedirectResponse(url="/dashboard", status_code=303)
    return templates.TemplateResponse(
        "auditoria.html",
        {"request": request, "user": user, "token": token},
    )


@router.get("/api/audit-logs")
async def list_audit_logs(
    request: Request,
    username: Optional[str] = None,
    acao: Optional[str] = None,
    entidade: Optional[str] = None,
    status: Optional[str] = None,
    de: Optional[str] = None,
    ate: Optional[str] = None,
    page: int = 1,
    per_page: int = 50,
    db: Session = Depends(get_db),
):
    """Lista paginada da trilha de auditoria (ts DESC) — somente admin.

    Filtros: username (contém, case-insensitive), acao (prefixo, ex. 'pedido.'),
    entidade (exata), status (exato), de/ate (datas ISO sobre ts).
    """
    require_role(request, db, "admin")

    query = db.query(AuditLog)

    if username and username.strip():
        query = query.filter(AuditLog.username.ilike(f"%{username.strip()}%"))

    if acao and acao.strip():
        # Filtro por PREFIXO (ex.: 'pedido.' pega pedido.criar, pedido.editar...)
        prefixo = acao.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.filter(AuditLog.acao.like(f"{prefixo}%", escape="\\"))

    if entidade and entidade.strip():
        query = query.filter(AuditLog.entidade == entidade.strip())

    if status and status.strip():
        query = query.filter(AuditLog.status == status.strip())

    if de and de.strip():
        query = query.filter(AuditLog.ts >= _parse_data(de, "de"))

    if ate and ate.strip():
        fim = _parse_data(ate, "ate")
        if len(ate.strip()) <= 10:  # só a data → inclui o dia inteiro
            fim = fim + timedelta(days=1)
            query = query.filter(AuditLog.ts < fim)
        else:
            query = query.filter(AuditLog.ts <= fim)

    total = query.count()
    per_page = max(1, min(per_page, 200))
    page = max(1, page)
    items = (
        query.order_by(AuditLog.ts.desc(), AuditLog.id.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    return {
        "success": True,
        "data": [log.to_dict() for log in items],
        "total": total,
        "page": page,
        "total_pages": (total + per_page - 1) // per_page,
    }


@router.get("/api/audit-logs/acoes")
async def list_audit_acoes(request: Request, db: Session = Depends(get_db)):
    """Lista distinta de ações registradas (para popular o filtro) — somente admin."""
    require_role(request, db, "admin")
    linhas = (
        db.query(AuditLog.acao)
        .distinct()
        .order_by(AuditLog.acao)
        .all()
    )
    return {"success": True, "data": [l[0] for l in linhas if l[0]]}
