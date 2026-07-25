"""Testes-smoke do harness (spec 006).

Cobrem: (a) o app importa e sobe; (b) `GET /health` responde 200; (c) cada
fixture principal (`test_engine`, `db_session`, `client`, `client_as`) é
exercitada por ao menos um teste. Tudo offline, no banco SQLite temporário.
"""
from app import config as app_config
from app.models.customer import Customer
from app.models.user import User
from tests.conftest import make_fake_customer, make_fake_employee


def test_app_importa_e_sobe():
    """O app FastAPI importa e tem rotas registradas."""
    from app.main import app

    assert app.title
    paths = {r.path for r in app.routes}
    assert "/health" in paths


def test_ambiente_offline_dev_mode():
    """Garante que os testes rodam em DEV_MODE e sem apontar pro banco real."""
    assert app_config.DEV_MODE is True
    assert app_config.DATABASE_URL.startswith("sqlite:///")
    assert "faturamento_test_" in app_config.DATABASE_URL


def test_health_200(client):
    """Scenario 1 da spec: /health responde 200."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


def test_test_engine_schema_criado(test_engine):
    """A fixture `test_engine` cria o schema (tabela users existe)."""
    from sqlalchemy import inspect

    tabelas = set(inspect(test_engine).get_table_names())
    assert "users" in tabelas
    assert "customers" in tabelas
    assert "employees" in tabelas


def test_db_session_grava_e_le(db_session):
    """Scenario 2 da spec: db_session grava/lê num banco temporário isolado."""
    cliente = make_fake_customer(name="Cliente Smoke", email="smoke@example.com")
    db_session.add(cliente)
    db_session.flush()

    lido = db_session.query(Customer).filter(Customer.name == "Cliente Smoke").first()
    assert lido is not None
    assert lido.email == "smoke@example.com"


def test_db_session_isolada_entre_testes(db_session):
    """O registro do teste anterior não vaza (transação revertida por teste)."""
    existe = (
        db_session.query(Customer).filter(Customer.name == "Cliente Smoke").first()
    )
    assert existe is None


def test_fake_employee_helper(db_session):
    """Helper de dado sintético anônimo persiste via db_session."""
    cliente = make_fake_customer(name="Cliente p/ Func", email="cf@example.com")
    db_session.add(cliente)
    db_session.flush()

    emp = make_fake_employee(customer_id=cliente.id, nome="Funcionario X")
    db_session.add(emp)
    db_session.flush()

    assert emp.id is not None
    assert emp.cpf == "000.000.000-00"  # anônimo, sem PII real


def test_fake_employee_factory_fixture(fake_employee_factory, db_session):
    """A fixture factory cria funcionários sintéticos no db_session."""
    cliente = make_fake_customer(name="Cliente Factory", email="factory@example.com")
    db_session.add(cliente)
    db_session.flush()

    emp = fake_employee_factory(cliente.id, matricula="T9999")
    assert emp.id is not None
    assert emp.matricula == "T9999"


def test_client_nao_autenticado_bloqueia_protegido(client):
    """Endpoint protegido sem sessão retorna 401."""
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


def test_client_as_gestor_acessa_protegido(client_as):
    """Scenario 3 da spec: autenticado como gestor, /api/auth/me resolve 200."""
    resp = client_as("gestor").get("/api/auth/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["role"] == "gestor"
    assert body["email"].endswith("@example.com")


def test_client_as_admin_vs_operador_rbac(client_as):
    """RBAC resolvido offline: admin acessa /api/users, operador recebe 403."""
    # admin autorizado
    resp_admin = client_as("admin").get("/api/users")
    assert resp_admin.status_code == 200
    assert resp_admin.json()["success"] is True

    # operador (papel insuficiente) barrado — troca a sessão do mesmo client
    resp_op = client_as("operador").get("/api/users")
    assert resp_op.status_code == 403


def test_usuario_de_teste_existe_no_banco_de_teste(client_as, test_engine):
    """O usuário criado pelo client_as vive no banco temporário, não no real."""
    from sqlalchemy import inspect  # noqa: F401

    client_as("admin")  # dispara a criação do usuário admin de teste
    from app.db import sessionmaker

    Session = sessionmaker(bind=test_engine)
    db = Session()
    try:
        u = db.query(User).filter(User.email == "admin.teste@example.com").first()
        assert u is not None
        assert u.role == "admin"
    finally:
        db.close()
