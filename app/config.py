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
DEV_MODE = not bool(SENIOR_SOAP_USER and SENIOR_SOAP_PASSWORD)

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
