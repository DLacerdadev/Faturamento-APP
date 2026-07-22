import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")

# Use DATABASE_URL from environment if available, otherwise fallback to SQLite
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR}/app.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

SQLALCHEMY_DATABASE_URL = DATABASE_URL

EXCEL_TEMPLATES_DIR = BASE_DIR / "app" / "excel_templates"
GENERATED_REPORTS_DIR = BASE_DIR / "app" / "generated_reports"
TEMPLATES_DIR = BASE_DIR / "app" / "templates"

EXCEL_TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
GENERATED_REPORTS_DIR.mkdir(parents=True, exist_ok=True)

MSSQL_HOST = os.getenv("MSSQL_HOST", "")
MSSQL_PORT = os.getenv("MSSQL_PORT", "1433")
MSSQL_DB = os.getenv("MSSQL_DB", "")
MSSQL_USER = os.getenv("MSSQL_USER", "")
MSSQL_PASS = os.getenv("MSSQL_PASS", "")
MSSQL_DRIVER = os.getenv("MSSQL_DRIVER", "")

SENIOR_API_DOMAIN = os.getenv("DOMAIN_API", "")
SENIOR_API_KEY = os.getenv("API_KEY", "")

SENIOR_SOAP_URL = os.getenv("SENIOR_SOAP_URL", "https://webp33.seniorcloud.com.br:30721/g5-senior-services/rubi_Synccom_opus_fopag?wsdl")
SENIOR_SOAP_NEXTI_URL = os.getenv("SENIOR_SOAP_NEXTI_URL", "https://webp33.seniorcloud.com.br:30721/g5-senior-services/rubi_Synccom_opus_nexti")
SENIOR_SOAP_USER = os.getenv("SENIOR_SOAP_USER", "")
SENIOR_SOAP_PASSWORD = os.getenv("SENIOR_SOAP_PASSWORD", "")
SENIOR_SOAP_TOKEN = os.getenv("SENIOR_SOAP_TOKEN", "")
SENIOR_SOAP_ENCRYPTION = int(os.getenv("SENIOR_SOAP_ENCRYPTION", "0"))

# Modo desenvolvimento: ativo quando credenciais Senior não estão configuradas.
# Neste modo, endpoints que dependem do Senior usam dados locais do banco SQLite.
#
# FORCE_DEV_MODE=1 força o modo dev MESMO com credenciais preenchidas no .env —
# útil para testar as telas com dados locais/sintéticos sem depender do Senior
# (ou quando as credenciais estão temporariamente inválidas/bloqueadas).
# Volte para 0 (ou remova a linha) quando as credenciais forem renovadas.
FORCE_DEV_MODE = os.getenv("FORCE_DEV_MODE", "").strip().lower() in {"1", "true", "yes", "on"}
DEV_MODE = FORCE_DEV_MODE or not bool(SENIOR_SOAP_USER and SENIOR_SOAP_PASSWORD)

# Feature 002: SMTP para envio de solicitação de compra por email (opcional).
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587") or "587")
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "")
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "1") not in ("0", "false", "False", "")
EPI_PURCHASE_EMAIL = os.getenv("EPI_PURCHASE_EMAIL", "")


def is_smtp_configured() -> bool:
    """Retorna True se as variáveis essenciais de SMTP estão preenchidas no .env."""
    return bool(SMTP_HOST and SMTP_FROM)

# Feature 003: cache e throttle das chamadas Senior.
SENIOR_CACHE_CCU_TTL = int(os.getenv("SENIOR_CACHE_CCU_TTL", "21600"))  # 6h default
SENIOR_CACHE_EMPLOYEES_TTL = int(os.getenv("SENIOR_CACHE_EMPLOYEES_TTL", "3600"))  # 1h default
SENIOR_SOAP_MAX_CONCURRENCY = max(1, int(os.getenv("SENIOR_SOAP_MAX_CONCURRENCY", "3")))
# Delay entre chamadas SOAP consecutivas no loop multi-CCU (ms). Mitigar 503/RST
# do F5 quando o servidor da Senior recebe muitas requisições em sequência.
SENIOR_SOAP_DELAY_BETWEEN_CCUS_MS = max(0, int(os.getenv("SENIOR_SOAP_DELAY_BETWEEN_CCUS_MS", "2000")))
# Só aplica o delay quando há MAIS de N CCUs na fila — exports pequenos rodam
# rápido como antes, exports grandes ganham a proteção contra rate-limit.
SENIOR_SOAP_DELAY_THRESHOLD_CCUS = max(1, int(os.getenv("SENIOR_SOAP_DELAY_THRESHOLD_CCUS", "10")))
# Página /monitor de inspeção de chamadas SOAP — APENAS dev. Em produção, setar
# MONITOR_PAGE_ENABLED=false no .env pra desabilitar o endpoint (volta 404).
# APP_ENV define o "modo" do servidor. Controla defaults seguros de outras flags:
# "development" → defaults voltados pra dev local (cookie sem secure, /monitor ON)
# "production"  → defaults seguros automáticos (cookie secure, /monitor OFF)
# Cada flag específica AINDA pode ser sobrescrita via env var individual.
APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
IS_PRODUCTION = APP_ENV == "production"

# /monitor — em production default OFF (404). Em dev default ON.
_monitor_default = "false" if IS_PRODUCTION else "true"
MONITOR_PAGE_ENABLED = os.getenv("MONITOR_PAGE_ENABLED", _monitor_default).strip().lower() in {"1", "true", "yes", "on"}

# Cookie secure — em production default TRUE (exige HTTPS). Em dev default FALSE.
_secure_default = "true" if IS_PRODUCTION else "false"
SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", _secure_default).strip().lower() in {"1", "true", "yes", "on"}
# SameSite do cookie. "lax" (default) protege contra a maioria de CSRF e ainda
# permite navegação top-level cross-site. "strict" é mais seguro mas quebra
# links externos pro app. "none" exige HTTPS+secure=true.
SESSION_COOKIE_SAMESITE = os.getenv("SESSION_COOKIE_SAMESITE", "lax").strip().lower()
