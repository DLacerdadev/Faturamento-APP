"""Testes de integração HTTP do router da planilha (spec 009).

O router `faturamento_grid` é registrado no `app/main.py` pela orquestradora no
merge (ver INTEGRATION-NOTES). Aqui registramos o router no app SÓ para o teste
(idempotente), para exercitar o contrato REST de ponta a ponta com o harness 006:
  - RBAC gestor+ (401 sem login);
  - /faturamento/planilha herda base.html e satisfaz o contrato de UI (div +
    ai_assistant.js + window.FaturamentoGrid);
  - apply-ops aplica ops v1 e valida (ApplyResult);
  - snapshot sem PII pela rota.
"""
import pytest

from app.main import app
from app.routers.faturamento_grid import router as grid_router
from app.models.billing_model import BillingModel


@pytest.fixture(autouse=True)
def _register_router():
    """Registra o router da planilha no app de teste (idempotente)."""
    if not any(getattr(r, "path", "") == "/api/faturamento/grid/apply-ops"
               for r in app.routes):
        app.include_router(grid_router)
    yield


def _gestor_model_id(test_engine):
    """Id de um modelo cadastrado (FEMSA/GERAL são seedados em DEV)."""
    from app import db as app_db
    Session = app_db.sessionmaker(bind=test_engine)
    s = Session()
    try:
        m = s.query(BillingModel).order_by(BillingModel.id).first()
        return m.id
    finally:
        s.close()


def test_planilha_page_exige_login(client):
    r = client.get("/faturamento/planilha", follow_redirects=False)
    assert r.status_code in (302, 303)


def test_planilha_page_contrato_ui(client_as):
    r = client_as("gestor").get("/faturamento/planilha")
    assert r.status_code == 200
    html = r.text
    # Contrato de UI (§2): div de montagem + script da IA (entregue por 010).
    assert 'id="ai-assistant-root"' in html
    assert "/static/js/ai_assistant.js" in html
    # Grid da 009 + Univer self-hosted (vendor local, não CDN).
    assert "/static/js/faturamento_planilha.js" in html
    assert "/static/vendor/univer/" in html
    # Herda o design system (base.html): sidebar institucional.
    assert 'class="sidebar"' in html


def test_apply_ops_exige_login(client):
    r = client.post("/api/faturamento/grid/apply-ops",
                    json={"model_id": 1, "ops": []})
    assert r.status_code == 401


def test_apply_ops_aplica_e_valida(client_as, test_engine):
    mid = _gestor_model_id(test_engine)
    # add_column válida
    r = client_as("gestor").post("/api/faturamento/grid/apply-ops", json={
        "model_id": mid,
        "ops": [{"op": "set_model_param", "param": "encargos_pct", "valor": 40}],
    })
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["applied"] == 1
    assert body["model"]["encargos_pct"] == 40


def test_apply_ops_op_invalida_retorna_erros(client_as, test_engine):
    mid = _gestor_model_id(test_engine)
    r = client_as("gestor").post("/api/faturamento/grid/apply-ops", json={
        "model_id": mid,
        "ops": [{"op": "set_model_param", "param": "imposto_pct", "valor": 999}],
    })
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False and body["applied"] == 0
    assert body["errors"]


def test_snapshot_sem_pii_pela_rota(client_as, test_engine):
    mid = _gestor_model_id(test_engine)
    r = client_as("gestor").get(f"/api/faturamento/grid/snapshot/{mid}")
    assert r.status_code == 200
    snap = r.json()["snapshot"]
    assert "colunas" in snap and "params" in snap
    assert all(v is None for v in snap["sample_row"].values())


def test_load_model_grid_sem_dados(client_as, test_engine):
    mid = _gestor_model_id(test_engine)
    r = client_as("gestor").get(f"/api/faturamento/grid/model/{mid}")
    assert r.status_code == 200
    grid = r.json()["grid"]
    # Só cabeçalho — nenhuma linha de dado de funcionário.
    assert grid["n_rows"] == 1
