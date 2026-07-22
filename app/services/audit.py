"""Helper de auditoria — grava linhas em audit_logs sem NUNCA derrubar o endpoint.

Contrato:
- Sessão PRÓPRIA (SessionLocal) com commit próprio: o registro sobrevive a
  rollback da sessão do endpoint e funciona mesmo sem usuário (ex.: login falho).
- Resolução do usuário: se `user` veio, snapshot de id/username/role; senão
  tenta get_request_user(request, db) quando `db` veio; senão registra anônimo.
- IP: primeiro valor de X-Forwarded-For; fallback request.client.host.
- Try/except global: qualquer erro vira logger.warning — auditoria jamais
  levanta exceção para o chamador.
- NUNCA registrar senhas/hashes/tokens no `detalhe`. Dados pessoais: referencie
  por id/matrícula, não despeje nome/CPF.

Exemplos de uso:

    # 1) Endpoint autenticado (já tem o User do require_role) — passe user=...
    from app.services.audit import audit

    @router.put("/api/pedidos/{pid}")
    def editar_pedido(pid: int, request: Request, db: Session = Depends(get_db)):
        user = require_role(request, db, "gestor")
        ...
        audit(request, "pedido.editar", entidade="pedido", entidade_id=str(pid),
              detalhe={"alteracoes": {"status": {"de": "rascunho", "para": "confirmado"}}},
              user=user)

    # 2) Login falho — não há usuário; registre a tentativa como anônima
    audit(request, "auth.login_falha", entidade="usuario",
          detalhe={"username_tentado": email}, status="negado")
"""
import logging

from app.db import SessionLocal
from app.models.audit_log import AuditLog

logger = logging.getLogger(__name__)


def audit(request, acao, *, entidade=None, entidade_id=None, detalhe=None,
          user=None, db=None, status="ok") -> None:
    """Registra uma ação na trilha de auditoria (audit_logs).

    Nunca levanta exceção: falha de auditoria vira logger.warning.
    Ver docstring do módulo para o contrato completo e exemplos.
    """
    try:
        # Resolve o usuário (snapshot — nunca FK viva)
        if user is None and db is not None and request is not None:
            try:
                from app.services.permissions import get_request_user
                user = get_request_user(request, db)
            except Exception:
                user = None

        user_id = getattr(user, "id", None) if user is not None else None
        username = getattr(user, "username", None) if user is not None else None
        role = getattr(user, "role", None) if user is not None else None

        # IP: primeiro valor de X-Forwarded-For, senão request.client.host
        ip = None
        if request is not None:
            try:
                fwd = request.headers.get("x-forwarded-for") if request.headers else None
                if fwd:
                    ip = fwd.split(",")[0].strip()
                elif getattr(request, "client", None) is not None:
                    ip = getattr(request.client, "host", None)
            except Exception:
                ip = None

        session = SessionLocal()
        try:
            session.add(AuditLog(
                user_id=user_id,
                username=username,
                role=role,
                acao=acao,
                entidade=entidade,
                entidade_id=str(entidade_id) if entidade_id is not None else None,
                detalhe=detalhe,
                ip=ip,
                status=status or "ok",
            ))
            session.commit()
        finally:
            session.close()
    except Exception as exc:  # auditoria jamais derruba o endpoint
        logger.warning("Falha ao registrar auditoria (acao=%s): %s", acao, exc)
