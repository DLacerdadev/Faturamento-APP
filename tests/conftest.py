"""Harness de testes (spec 006) — fundação para as specs 007–010.

Objetivos (ver specs/006-harness-testes/spec.md):
  * Rodar 100% OFFLINE (sem Senior/SMTP/LLM), determinístico, < 2 min.
  * NUNCA tocar o banco real (`app.db` ou o Postgres de dev). Cada sessão de
    teste usa um SQLite temporário isolado, criado via `Base.metadata`.
  * Fornecer fixtures reutilizáveis: `test_engine`, `db_session`, `client`,
    `client_as(role)`, além de helpers de dados sintéticos ANÔNIMOS.

IMPORTANTE — ORDEM DE IMPORT:
  As variáveis de ambiente abaixo são configuradas ANTES de qualquer import de
  `app.*`. `app/config.py` lê env no import (DATABASE_URL, DEV_MODE) e
  `app/db.py` cria o engine no import. `load_dotenv(..., override=False)` (default
  do python-dotenv) NÃO sobrescreve variáveis já presentes em os.environ, então
  o que setamos aqui vence o `.env` do projeto (que aponta pra Postgres e tem
  credenciais reais do Senior).
"""
import os
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# 1) Ambiente de teste: banco SQLite temporário + DEV_MODE forçado (offline).
#    Feito no topo do módulo, antes de importar `app`.
# ---------------------------------------------------------------------------
_TMP_DB_FD, _TMP_DB_PATH = tempfile.mkstemp(prefix="faturamento_test_", suffix=".db")
os.close(_TMP_DB_FD)
# SQLAlchemy quer barras normais mesmo no Windows.
_TMP_DB_URL = "sqlite:///" + Path(_TMP_DB_PATH).as_posix()

os.environ["DATABASE_URL"] = _TMP_DB_URL
# Força modo dev: nada de chamada externa (Senior/SMTP). DEV_MODE = True quando
# FORCE_DEV_MODE está ligado, independentemente das credenciais do .env.
os.environ["FORCE_DEV_MODE"] = "1"
# Zera as credenciais do Senior herdadas do .env por garantia (defense-in-depth).
for _k in ("SENIOR_SOAP_USER", "SENIOR_SOAP_PASSWORD", "SENIOR_SOAP_TOKEN"):
    os.environ[_k] = ""
# Zera as credenciais do TOTVS/WSGETDATA (feature 008): nenhum teste pode alcançar
# o Protheus real. Testes que precisam do TOTVS "configurado" setam via config
# (monkeypatch.setattr), nunca com a credencial de produção.
for _k in ("TOTVS_WSGETDATA_URL", "TOTVS_WSGETDATA_USER", "TOTVS_WSGETDATA_PASSWORD"):
    os.environ[_k] = ""
# Cookie sem `secure` para o TestClient (http://testserver).
os.environ["SESSION_COOKIE_SECURE"] = "false"
# Sessão em memória determinística.
os.environ.setdefault("SESSION_SECRET", "test-secret-key")

# ---------------------------------------------------------------------------
# 2) Imports de app (agora já enxergam o ambiente de teste).
# ---------------------------------------------------------------------------
import pytest  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from sqlalchemy import BigInteger  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402

from app import config as app_config  # noqa: E402
from app import db as app_db  # noqa: E402
from app.db import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402


# ---------------------------------------------------------------------------
# Shim de PARIDADE SQLite (apenas testes): o modelo User usa BigInteger como PK
# autoincrement. Em Postgres (dev/prod) isso funciona; em SQLite o autoincrement
# só vale para "INTEGER PRIMARY KEY", não "BIGINT". Renderizamos BigInteger como
# INTEGER no dialeto sqlite para o autoincrement do id funcionar nos testes.
# Não altera código de produção; é uma regra de compilação local do harness.
# ---------------------------------------------------------------------------
@compiles(BigInteger, "sqlite")
def _compile_biginteger_as_integer_on_sqlite(type_, compiler, **kw):  # noqa: ANN001
    return "INTEGER"
from app.models.user import User  # noqa: E402
from app.session_manager import session_manager  # noqa: E402
from app.routers.auth import SESSION_COOKIE  # noqa: E402


# Sanidade: garante que estamos mesmo apontando pro banco temporário e em DEV.
assert app_config.DATABASE_URL == _TMP_DB_URL, (
    "conftest não redirecionou DATABASE_URL para o SQLite de teste — "
    "verifique a ordem de import."
)
assert app_config.DEV_MODE is True, "DEV_MODE precisa estar ativo nos testes."
assert "app.db" not in app_config.DATABASE_URL.replace("faturamento_test_", ""), (
    "banco de teste não pode ser o app.db real."
)


# ---------------------------------------------------------------------------
# 3) Fixtures de banco.
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def test_engine():
    """Engine SQLAlchemy contra um SQLite temporário isolado.

    O schema é criado uma vez por sessão de teste via `init_db()` (que chama
    `Base.metadata.create_all` + migrações idempotentes + seeds em DEV_MODE),
    e o arquivo temporário é apagado ao fim. NUNCA usa o `app.db` real.
    """
    engine = app_db.engine
    assert str(engine.url) == _TMP_DB_URL, "engine não está no SQLite de teste."
    # Cria schema + seeds (admin ti@grupoopus.com, catálogos, modelos FEMSA/GERAL).
    app_db.init_db()
    yield engine
    engine.dispose()
    try:
        os.remove(_TMP_DB_PATH)
    except OSError:
        pass


@pytest.fixture()
def db_session(test_engine):
    """Sessão transacional por teste, com rollback ao fim.

    Usa uma conexão dedicada com transação externa; a sessão vive dentro dela e
    é revertida no teardown, então gravações de um teste não vazam para os
    demais (isolamento por teste), tudo no banco temporário.
    """
    connection = test_engine.connect()
    transaction = connection.begin()
    SessionTest = app_db.sessionmaker(
        autocommit=False, autoflush=False, bind=connection
    )
    session = SessionTest()
    try:
        yield session
    finally:
        session.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()


@pytest.fixture()
def client(test_engine):
    """`TestClient` do FastAPI com `get_db` sobrescrito para o banco de teste.

    A dependência `get_db` é substituída por uma que abre sessões no engine
    temporário. Os commits dos endpoints ficam no SQLite de teste (limpo ao fim
    da sessão), nunca no banco real.
    """
    TestingSessionLocal = app_db.sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine
    )

    def _override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    # `raise_server_exceptions=True` (default) faz o teste falhar no traceback
    # real do servidor em vez de mascarar como 500.
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# 4) Autenticação por papel — `client_as(role)`.
# ---------------------------------------------------------------------------
_ROLE_EMAIL = {
    "operador": "operador.teste@example.com",
    "gestor": "gestor.teste@example.com",
    "admin": "admin.teste@example.com",
}


def _ensure_test_user(engine, role: str) -> int:
    """Cria (idempotente) um usuário de teste com o papel dado e retorna o id.

    Grava direto no banco temporário via engine de teste (não usa a sessão do
    override, para o usuário existir independentemente da request).
    """
    if role not in _ROLE_EMAIL:
        raise ValueError(f"papel inválido: {role!r} (use operador/gestor/admin)")
    email = _ROLE_EMAIL[role]
    Session = app_db.sessionmaker(bind=engine)
    db = Session()
    try:
        user = db.query(User).filter(User.email == email).first()
        if user is None:
            user = User(
                username=email,
                email=email,
                full_name=f"Usuário de Teste ({role})",
                is_active=1,
                role=role,
            )
            user.set_password("teste@123")
            db.add(user)
            db.commit()
            db.refresh(user)
        elif (user.role or "") != role:
            user.role = role
            db.commit()
        return user.id
    finally:
        db.close()


@pytest.fixture()
def client_as(client, test_engine):
    """Helper: retorna um TestClient autenticado como 'operador'/'gestor'/'admin'.

    Como o `session_manager` é um singleton de processo, autenticamos criando a
    sessão em memória (`create_session`) e injetando o cookie httpOnly real
    (`session_token`) no TestClient — exatamente o mecanismo que o
    `permissions.require_role` / `auth.require_login` resolvem em produção.
    Nenhuma senha é verificada por HTTP; o cookie assinado basta.

        def test_x(client_as):
            resp = client_as("gestor").get("/api/...")
    """
    def _make(role: str) -> TestClient:
        user_id = _ensure_test_user(test_engine, role)
        token = session_manager.create_session(user_id, _ROLE_EMAIL[role])
        client.cookies.set(SESSION_COOKIE, token)
        return client

    return _make


# ---------------------------------------------------------------------------
# 5) Helpers de dados sintéticos ANÔNIMOS (sem PII real) — reutilizáveis 007–010.
# ---------------------------------------------------------------------------
def make_fake_employee(customer_id: int, **overrides) -> "object":
    """Cria (sem persistir) um `Employee` sintético anônimo.

    CPF/nome/matrícula são fictícios e determinísticos. Persistência fica a
    cargo do teste (via `db_session`), para o helper servir tanto a testes de
    unidade quanto de integração.
    """
    from app.models.employee import Employee

    data = dict(
        customer_id=customer_id,
        cpf="000.000.000-00",
        nome="Funcionario Teste",
        matricula="T0001",
        centro_custo="CC-TESTE",
        cargo="Cargo Teste",
        departamento="Departamento Teste",
        email="funcionario.teste@example.com",
        status="ativo",
    )
    data.update(overrides)
    return Employee(**data)


def make_fake_customer(**overrides) -> "object":
    """Cria (sem persistir) um `Customer` sintético anônimo mínimo."""
    from app.models.customer import Customer

    data = dict(name="Cliente Teste")
    data.update(overrides)
    # Só passa campos que o modelo aceita (Customer varia entre versões).
    valid = {c.name for c in Customer.__table__.columns}
    return Customer(**{k: v for k, v in data.items() if k in valid})


@pytest.fixture()
def fake_employee_factory(db_session):
    """Factory que persiste `Employee`s sintéticos no `db_session` do teste."""
    created = []

    def _make(customer_id: int, **overrides):
        emp = make_fake_employee(customer_id, **overrides)
        db_session.add(emp)
        db_session.flush()
        created.append(emp)
        return emp

    return _make
