"""
Envio de email com anexo (feature 002, FR-16).

Usa stdlib smtplib + email.mime. Configuração lida de app.config (SMTP_HOST etc.).
"""
import logging
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from typing import Optional

from app import config

logger = logging.getLogger(__name__)


def send_solicitacao_email(
    to: str,
    subject: str,
    body_text: str,
    attachment_bytes: bytes,
    attachment_filename: str,
    cc: Optional[str] = None,
) -> None:
    """
    Envia email com 1 anexo via SMTP. Lança Exception em falha; chamador trata.
    """
    if not config.is_smtp_configured():
        raise RuntimeError("SMTP não configurado no servidor (.env: SMTP_HOST/SMTP_FROM).")

    msg = MIMEMultipart()
    msg["From"] = formataddr(("Faturamento App", config.SMTP_FROM))
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg["Subject"] = subject

    msg.attach(MIMEText(body_text or "", "plain", "utf-8"))

    attach = MIMEApplication(attachment_bytes, _subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    attach.add_header("Content-Disposition", "attachment", filename=attachment_filename)
    msg.attach(attach)

    recipients = [to] + ([cc] if cc else [])

    server: Optional[smtplib.SMTP] = None
    try:
        if config.SMTP_USE_TLS:
            server = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30)
            server.starttls()
        else:
            server = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30)
        if config.SMTP_USER and config.SMTP_PASSWORD:
            server.login(config.SMTP_USER, config.SMTP_PASSWORD)
        server.sendmail(config.SMTP_FROM, recipients, msg.as_string())
        logger.info("Email enviado para %s (cc=%s) via %s", to, cc, config.SMTP_HOST)
    finally:
        if server is not None:
            try:
                server.quit()
            except Exception:
                pass
