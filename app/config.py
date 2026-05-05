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
