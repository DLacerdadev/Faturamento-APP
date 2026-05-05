import logging
import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
from app.config import DATABASE_URL

logger = logging.getLogger(__name__)

engine = create_engine(
    DATABASE_URL
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    from app.models import customer, employee, integrations, report, user, billing, medical_exam, epi_purchase
    from app.models.billing import PayrollItemType, PayrollDirection
    Base.metadata.create_all(bind=engine)
    
    db = SessionLocal()
    try:
        from app.models.user import User
        admin_email = "ti@grupoopus.com"
        existing_admin = db.query(User).filter(User.email == admin_email).first()
        if not existing_admin:
            admin = User(
                username=admin_email,
                email=admin_email,
                full_name="Administrador",
                is_active=1
            )
            admin.set_password("telos@2026")
            db.add(admin)
            db.commit()

        existing = db.query(PayrollItemType).first()
        if not existing:
            default_types = [
                PayrollItemType(code="SALARIO_DIA", description="Salário Dia", group="REMUNERACAO", direction=PayrollDirection.CREDIT),
                PayrollItemType(code="HORA_EXTRA", description="Horas Extras", group="REMUNERACAO", direction=PayrollDirection.CREDIT),
                PayrollItemType(code="VALE_TRANSPORTE", description="Vale Transporte", group="BENEFICIOS", direction=PayrollDirection.DEBIT),
                PayrollItemType(code="VALE_REFEICAO", description="Vale Refeição", group="BENEFICIOS", direction=PayrollDirection.DEBIT),
                PayrollItemType(code="PREMIO_BONUS", description="Prêmio/Bônus", group="REMUNERACAO", direction=PayrollDirection.CREDIT),
                PayrollItemType(code="TRIBUTO_VALOR", description="Tributos", group="ENCARGOS", direction=PayrollDirection.DEBIT),
                PayrollItemType(code="ENCARGO_VALOR", description="Encargos", group="ENCARGOS", direction=PayrollDirection.DEBIT),
                PayrollItemType(code="TAXA_FATURAMENTO", description="Taxa de Faturamento", group="TAXAS", direction=PayrollDirection.DEBIT),
                PayrollItemType(code="EXAME_MEDICO", description="Exame Médico", group="SAUDE", direction=PayrollDirection.DEBIT),
            ]
            db.add_all(default_types)
            db.commit()
    finally:
        db.close()


def seed_dev_data():
    """
    Carrega dados de dump.sql no banco SQLite quando em DEV_MODE e o banco está vazio.
    Usa INSERT OR IGNORE para não conflitar com dados já existentes.
    """
    from app.config import DEV_MODE
    from app.models.billing import BillingPeriod

    if not DEV_MODE:
        return

    db = SessionLocal()
    try:
        if db.query(BillingPeriod).first():
            logger.info("[DEV_MODE] Banco já possui dados de período. seed_dev_data ignorado.")
            return
    finally:
        db.close()

    dump_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dump.sql"
    )
    if not os.path.exists(dump_path):
        logger.warning("[DEV_MODE] dump.sql não encontrado em %s. Banco ficará sem dados de teste.", dump_path)
        return

    with open(dump_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    inserts = [
        line.strip()
        for line in lines
        if line.upper().strip().startswith("INSERT INTO")
    ]

    if not inserts:
        logger.warning("[DEV_MODE] Nenhum INSERT encontrado em dump.sql.")
        return

    conn = engine.raw_connection()
    loaded = 0
    skipped = 0
    try:
        cursor = conn.cursor()
        for stmt in inserts:
            # Converte para INSERT OR IGNORE para evitar conflito com dados já existentes
            safe_stmt = stmt.replace("INSERT INTO", "INSERT OR IGNORE INTO", 1)
            try:
                cursor.execute(safe_stmt)
                loaded += 1
            except Exception:
                skipped += 1
        conn.commit()
        logger.info(
            "[DEV_MODE] seed_dev_data concluído: %d INSERTs carregados, %d ignorados.",
            loaded, skipped,
        )
    finally:
        conn.close()
