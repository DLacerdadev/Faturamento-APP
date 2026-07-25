"""Testes do conector TOTVS WSGETDATA (feature 008) — SEMPRE MOCKADO (offline).

Nada de rede: `httpx.post` é substituído por um fake que devolve uma
`httpx.Response` construída em memória. Cobre: envelope 201 ok, 500 erro,
encoding cp1252, header Basic correto, env vazio => erro claro.
"""
import base64

import httpx
import pytest

from app.services import totvs_connector as tc


class _FakeResponse:
    """Mímica mínima de httpx.Response para o que run_query usa."""
    def __init__(self, status_code, content: bytes):
        self.status_code = status_code
        self.content = content


@pytest.fixture()
def totvs_env(monkeypatch):
    """Configura env TOTVS (via os.getenv que o connector lê como fallback)."""
    monkeypatch.setenv("TOTVS_WSGETDATA_URL", "http://totvs.example:8080")
    monkeypatch.setenv("TOTVS_WSGETDATA_USER", "powerbi")
    monkeypatch.setenv("TOTVS_WSGETDATA_PASSWORD", "s3nh4")
    # Garante que config (se importável) não tenha esses atributos preenchidos.
    return None


def test_run_query_sucesso_201(totvs_env, monkeypatch):
    captured = {}

    def fake_post(url, headers=None, content=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["content"] = content
        body = b'{"registros":[{"COD":"030315","CC":"640021","PRECO":22.95}],"qtde":1,"dicionario":[]}'
        return _FakeResponse(201, body)

    monkeypatch.setattr(httpx, "post", fake_post)

    regs = tc.run_query("SELECT 1")
    assert regs == [{"COD": "030315", "CC": "640021", "PRECO": 22.95}]
    # URL monta o path do WSGETDATA
    assert captured["url"] == "http://totvs.example:8080/rest/WSGETDATA/v1"
    # Header Basic correto (base64 de user:senha)
    expected = "Basic " + base64.b64encode(b"powerbi:s3nh4").decode()
    assert captured["headers"]["Authorization"] == expected
    assert captured["headers"]["Content-Type"] == "text/plain"


def test_run_query_erro_500_levanta(totvs_env, monkeypatch):
    def fake_post(url, headers=None, content=None, timeout=None):
        return _FakeResponse(500, b'{"code":500,"message":"ODBC error"}')

    monkeypatch.setattr(httpx, "post", fake_post)
    with pytest.raises(RuntimeError) as exc:
        tc.run_query("SELECT bad")
    assert "500" in str(exc.value)


def test_run_query_decodifica_cp1252(totvs_env, monkeypatch):
    # "N\xe3o" (Não) em cp1252 — deve decodificar sem estourar.
    body = '{"registros":[{"DESCR":"N\xe3o encontrado"}]}'.encode("cp1252")

    def fake_post(url, headers=None, content=None, timeout=None):
        return _FakeResponse(201, body)

    monkeypatch.setattr(httpx, "post", fake_post)
    regs = tc.run_query("SELECT 1")
    assert regs[0]["DESCR"] == "Não encontrado"


def test_run_query_env_vazio_erro_claro(monkeypatch):
    # Sem env => não configurado => erro claro, sem tocar a rede.
    monkeypatch.setenv("TOTVS_WSGETDATA_URL", "")
    monkeypatch.setenv("TOTVS_WSGETDATA_USER", "")
    monkeypatch.setenv("TOTVS_WSGETDATA_PASSWORD", "")

    def fake_post(*a, **k):  # não deve ser chamado
        raise AssertionError("run_query não deveria chamar a rede sem env")

    monkeypatch.setattr(httpx, "post", fake_post)
    assert tc.is_totvs_configured() is False
    with pytest.raises(RuntimeError) as exc:
        tc.run_query("SELECT 1")
    assert "não configurado" in str(exc.value).lower() or "nao configurado" in str(exc.value).lower()


def test_run_query_envelope_invalido(totvs_env, monkeypatch):
    def fake_post(url, headers=None, content=None, timeout=None):
        return _FakeResponse(201, b'{"algo":"errado"}')

    monkeypatch.setattr(httpx, "post", fake_post)
    with pytest.raises(RuntimeError):
        tc.run_query("SELECT 1")
