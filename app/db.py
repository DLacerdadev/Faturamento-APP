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
    from app.models import customer, employee, integrations, report, user, billing, medical_exam, epi_purchase, exam_catalog, benefit_event, product_catalog, training_catalog, training_record, cc_item_price, billing_model, audit_log
    from app.models.billing import PayrollItemType, PayrollDirection
    from app.models.exam_catalog import ExamCatalog
    from app.models.benefit_event import BenefitEvent
    from app.models.product_catalog import ProductCatalog
    from app.models.billing_model import BillingModel
    Base.metadata.create_all(bind=engine)

    # Migração idempotente: novas colunas em medical_exams (create_all não altera
    # tabela já existente). Ver RUNBOOK — colunas NULLable + ALTER documentado.
    from sqlalchemy import text as _sql_text
    _medexam_new_cols = [
        ("cpf", "VARCHAR(14)"),
        ("codccu", "VARCHAR(50)"),
        ("nome_ccu", "VARCHAR(255)"),
        ("origem", "VARCHAR(30) DEFAULT 'manual'"),
        ("status", "VARCHAR(20) DEFAULT 'confirmado'"),
    ]
    _is_pg = engine.dialect.name == "postgresql"
    _migrations = [
        ("medical_exams", _medexam_new_cols),
        ("billing_companies", [
            ("encargos_pct", "DOUBLE PRECISION"),
            ("taxa_adm_pct", "DOUBLE PRECISION"),
            ("imposto_pct", "DOUBLE PRECISION"),
            ("billing_model_id", "INTEGER"),
        ]),
        ("epi_catalog", [
            ("produto_codigo", "VARCHAR(40)"),
        ]),
        # Generalização Compras/Entregas (Fase 1)
        ("epi_purchase_packages", [
            ("categoria", "VARCHAR(20) DEFAULT 'epi'"),
            ("valor_total_pago", "DOUBLE PRECISION"),
            ("status", "VARCHAR(20) DEFAULT 'rascunho'"),
        ]),
        ("epi_purchase_items", [
            ("produto_codigo", "VARCHAR(40)"),
            # Pedido misto: categoria por ITEM (NULL = vale a categoria do pacote)
            ("categoria", "VARCHAR(20)"),
        ]),
        # Modelos de exportação por upload + regras (%) por modelo (contrato C3)
        ("billing_models", [
            ("estrutura", "JSONB" if _is_pg else "JSON"),
            ("arquivo_origem", "VARCHAR(500)"),
            ("encargos_pct", "DOUBLE PRECISION"),
            ("taxa_adm_pct", "DOUBLE PRECISION"),
            ("imposto_pct", "DOUBLE PRECISION"),
            # Fórmula do salário por modelo (metodologia do cliente, ex.: Skyrail)
            ("salario_formula", "VARCHAR(200)"),
            # Grade "Fórmulas": configuração por campo (código buscado + fórmula)
            ("campos_config", "JSONB" if _is_pg else "JSON"),
        ]),
        # Papéis de usuário: 'operador' < 'gestor' < 'admin'
        ("users", [
            ("role", "VARCHAR(20) NOT NULL DEFAULT 'operador'"),
        ]),
    ]
    with engine.begin() as _conn:
        for _tbl, _cols in _migrations:
            for _col, _ddl in _cols:
                try:
                    if _is_pg:
                        _conn.execute(_sql_text(f"ALTER TABLE {_tbl} ADD COLUMN IF NOT EXISTS {_col} {_ddl}"))
                    else:
                        _conn.execute(_sql_text(f"ALTER TABLE {_tbl} ADD COLUMN {_col} {_ddl}"))
                except Exception:
                    pass  # coluna já existe (SQLite sem IF NOT EXISTS) ou dialeto sem suporte
    
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
                is_active=1,
                role="admin",
            )
            admin.set_password("telos@2026")
            db.add(admin)
            db.commit()
        elif (existing_admin.role or "") != "admin":
            # Seed idempotente do papel: ti@grupoopus.com é sempre admin
            existing_admin.role = "admin"
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

        # Seed do catálogo de exames (21 tipos da página de Exames + sinônimos p/ identificação)
        if not db.query(ExamCatalog).first():
            _catalogo = [
                ("clinic", "Avaliação Clínica", ["clinico", "exameclinico", "avaliacaoclinica", "clinicomedico", "consultaclinica", "examedeclinico"]),
                ("audio", "Audiometria", ["audiometria", "audiometriatonal", "audio", "exameaudiometrico"]),
                ("acuid", "Acuidade Visual", ["acuidadevisual", "acuidade", "av", "examevisual"]),
                ("hemo", "Hemograma", ["hemograma", "hemogramacompleto", "hemo"]),
                ("lipidograma", "Lipidograma", ["lipidograma", "perfillipidico", "colesterol"]),
                ("rx_coluna", "RX Coluna", ["rxcoluna", "raioxcoluna", "rxdecoluna"]),
                ("met_e_cet", "Met e Cet", ["metecet", "metilhipurico"]),
                ("acet_u", "Acetona Urinária", ["acetu", "acetonaurinaria", "acetonau"]),
                ("hg", "Mercúrio (HG)", ["mercurio", "hgurinario"]),
                ("retic", "Reticulócitos", ["reticulocitos", "retic"]),
                ("ac_trans", "Ácido Trans-hipúrico", ["acidotranshipurico", "actrans", "transhipurico"]),
                ("eeg", "Eletroencefalograma", ["eletroencefalograma", "eeg"]),
                ("ecg", "Eletrocardiograma", ["eletrocardiograma", "ecg"]),
                ("etanol", "Etanol", ["etanol", "alcoolemia"]),
                ("glice", "Glicemia", ["glicemia", "glicose", "glice"]),
                ("gama_gt", "Gama GT", ["gamagt", "gamaglutamil", "ggt"]),
                ("tgp", "TGP", ["tgp", "transaminase", "alt"]),
                ("rx_torax", "RX Tórax", ["rxtorax", "raioxtorax", "rxdetorax", "toraxpa"]),
                ("espiro", "Espirometria", ["espirometria", "espiro"]),
                ("rx_lomb", "RX Lombar", ["rxlombar", "raioxlombar", "rxlomb"]),
                ("aval_psicossocial", "Avaliação Psicossocial", ["psicossocial", "avaliacaopsicossocial", "psicologico", "psicossocialocupacional"]),
            ]
            db.add_all([ExamCatalog(coluna=c, nome=n, sinonimos=s, ativo=True) for c, n, s in _catalogo])
            db.commit()

        # Seed dos eventos de benefício encontrados na Senior (TELOS).
        # ativo=True: confirmados. ativo=False: pendentes de confirmação de valor.
        if not db.query(BenefitEvent).first():
            _benef = [
                # (codeve, descricao, coluna_femsa, grupo, ativo, obs)
                (3180, "BONUS", "PREMIO/BONUS", "premio", True, "Confirmado"),
                (3158, "Desc VT não utilizado", "VALE TRANSPORTE NAO UTILIZADO", "vt", True, "Confirmado"),
                (3611, "VALE REFEICAO", "PAGTO. VALE REFEICAO (Valor)", "vr", False, "Pendente: confirmar 3611 (provento) vs 3267+3268 (split colaborador/empresa)"),
                (3268, "Desc VR Empresa-TELOS", "PAGTO. VALE REFEICAO (Valor)", "vr", False, "Pendente: parte empresa do VR"),
                (3267, "Desc VR Colaborador-TELOS", "PAGTO. VALE REFEICAO (Valor)", "vr", False, "Pendente: parte colaborador do VR"),
                (2450, "Seguro de Vida", "SEGURO DE VIDA", "seguro", False, "Pendente: hoje a coluna é fixa R$5 no código — decidir usar evento"),
                (3149, "REEMBOLSO VALE REFEICAO", "REEMB. VALE REFEICAO INDEVIDO/DEVOLVIDO", "vr", False, "Pendente: reembolso VR"),
                (3034, "Reembolso Vale Transporte", "REEMB. DESPESAS KM/ESTAC/PEDAGIO", "vt", False, "Pendente: reembolso VT"),
            ]
            db.add_all([
                BenefitEvent(codeve=c, descricao=d, coluna_femsa=col, grupo=g, ativo=a, observacao=o)
                for c, d, col, g, a, o in _benef
            ])
            db.commit()

        # Seed dos modelos de faturamento configuráveis (idempotente por nome).
        # FEMSA = 79 colunas atuais (regressão zero). GERAL = base (superconjunto):
        # FEMSA + as 4 novas colunas de custo (Uniformes/EPIs/Equip/Treinamentos).
        from app.services.excel_export import FEMSA_COLUMNS, GERAL_COLUMNS
        if not db.query(BillingModel).filter(BillingModel.nome == "FEMSA").first():
            db.add(BillingModel(
                nome="FEMSA",
                descricao="Modelo padrão FEMSA (79 colunas atuais).",
                is_base=False,
                ativo=True,
                colunas=list(FEMSA_COLUMNS),
            ))
            db.commit()
        if not db.query(BillingModel).filter(BillingModel.nome == "GERAL").first():
            db.add(BillingModel(
                nome="GERAL",
                descricao="Modelo base (superconjunto): FEMSA + Uniformes/EPIs/Equipamentos/Treinamentos.",
                is_base=True,
                ativo=True,
                colunas=list(GERAL_COLUMNS),
            ))
            db.commit()

        # Backfill (migração 004 — pedido misto): linhas antigas de compra sem
        # categoria ganham a categoria derivada do catálogo (epi_id => 'epi';
        # produto_codigo => categoria do product_catalog), e a categoria do pacote
        # é re-derivada (única categoria dos itens, ou 'misto'). Idempotente: só
        # mexe em linha com categoria NULL.
        from app.models.epi_purchase import EpiPurchaseItem, EpiPurchasePackage
        from app.models.product_catalog import ProductCatalog
        _sem_cat = (
            db.query(EpiPurchaseItem)
            .filter(EpiPurchaseItem.categoria.is_(None))
            .filter((EpiPurchaseItem.epi_id.isnot(None)) | (EpiPurchaseItem.produto_codigo.isnot(None)))
            .all()
        )
        if _sem_cat:
            _cat_por_codigo = dict(
                db.query(ProductCatalog.codigo, ProductCatalog.categoria).all()
            )
            _pkgs_afetados = set()
            for _it in _sem_cat:
                if _it.epi_id is not None:
                    _it.categoria = "epi"
                else:
                    _c = (_cat_por_codigo.get(_it.produto_codigo) or "").strip().lower()
                    _it.categoria = _c if _c in ("epi", "uniforme", "equipamento") else "equipamento"
                _pkgs_afetados.add(_it.package_id)
            db.commit()
            for _pid in _pkgs_afetados:
                _pkg = db.query(EpiPurchasePackage).filter(EpiPurchasePackage.id == _pid).first()
                if not _pkg:
                    continue
                _cats = {(_i.categoria or "").lower() for _i in (_pkg.items or []) if _i.categoria}
                if _cats:
                    _pkg.categoria = _cats.pop() if len(_cats) == 1 else "misto"
            db.commit()
            logger.info("Backfill categoria por item: %s linha(s) atualizadas.", len(_sem_cat))

        # Normaliza a ordem das colunas dos modelos dirigidos por colunas para a
        # ordem canônica do GERAL (itens após SEGURO DE VIDA). Idempotente; não
        # toca modelos por upload (estrutura manda) nem perde colunas fora do GERAL.
        _mudou = False
        for _m in db.query(BillingModel).all():
            if _m.estrutura:
                continue
            _cols = _m.colunas or []
            _canon = [c for c in GERAL_COLUMNS if c in _cols] + [c for c in _cols if c not in GERAL_COLUMNS]
            if _cols != _canon:
                _m.colunas = _canon
                _mudou = True
        if _mudou:
            db.commit()
    finally:
        db.close()


def seed_dev_data():
    """
    Carrega dados de dump.sql quando em DEV_MODE e o banco está vazio.
    Detecta o dialect (SQLite vs Postgres) e aplica a sintaxe correta
    de "insert ignore on conflict".
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

    # Dialect detection: SQLite usa `INSERT OR IGNORE`, Postgres usa `INSERT ... ON CONFLICT DO NOTHING`
    is_postgres = engine.dialect.name == "postgresql"

    def make_safe(stmt: str) -> str:
        if is_postgres:
            # Append ON CONFLICT DO NOTHING antes do `;` final (se houver)
            stmt = stmt.rstrip(";").rstrip()
            return f"{stmt} ON CONFLICT DO NOTHING;"
        # SQLite
        return stmt.replace("INSERT INTO", "INSERT OR IGNORE INTO", 1)

    conn = engine.raw_connection()
    loaded = 0
    skipped = 0
    try:
        cursor = conn.cursor()
        for stmt in inserts:
            try:
                cursor.execute(make_safe(stmt))
                loaded += 1
            except Exception:
                skipped += 1
        conn.commit()
        logger.info(
            "[DEV_MODE] seed_dev_data concluído (%s): %d INSERTs carregados, %d ignorados.",
            engine.dialect.name, loaded, skipped,
        )
    finally:
        conn.close()
