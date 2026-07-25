"""Testes da rotina de sincronização de preços de EPI (feature 008).

Conector SEMPRE MOCKADO (sem rede): substituímos
`price_autosync.totvs_connector.run_query` por um fake que devolve `registros`
sintéticos. Cobre:
  * sync por CCU (upsert do último C7_PRECO por produto × CC);
  * respeita is_manual_price (não sobrescreve linha travada);
  * ISOLAMENTO por CCU (manual no A + auto no B → B atualiza, A não);
  * reverter ao automático restaura o valor da fonte;
  * falha do TOTVS não zera preços existentes (falha segura);
  * scheduler.trigger_now dispara a rotina (sem esperar 24h).
"""
import pytest

from app.models.cc_item_price import CCItemPrice
from app.services import price_autosync
from app.services import scheduler


def _fake_registros(*rows):
    """rows = (COD, CC, PRECO[, DESCR]) → lista de dicts no formato do WSGETDATA."""
    out = []
    for r in rows:
        cod, cc, preco = r[0], r[1], r[2]
        descr = r[3] if len(r) > 3 else "EPI"
        out.append({"COD": cod, "CC": cc, "DESCR": descr, "PRECO": preco, "EMISSAO": "20260725"})
    return out


def _patch_totvs(monkeypatch, registros):
    monkeypatch.setattr(price_autosync.totvs_connector, "run_query", lambda sql: registros)


def _get(db, codccu, produto):
    return (
        db.query(CCItemPrice)
        .filter(CCItemPrice.codccu == codccu,
                CCItemPrice.produto_codigo == produto,
                CCItemPrice.tamanho == "")
        .first()
    )


def test_sync_cria_e_atualiza_automaticos(db_session, monkeypatch):
    _patch_totvs(monkeypatch, _fake_registros(
        ("030315", "640021", 22.95),
        ("030315", "420204", 45.61),
    ))
    res = price_autosync.run_epi_price_autosync(db_session)
    assert res["ok"] is True
    assert res["inserted"] == 2

    a = _get(db_session, "640021", "030315")
    b = _get(db_session, "420204", "030315")
    assert a.valor == pytest.approx(22.95)
    assert b.valor == pytest.approx(45.61)
    # automáticos: trava desligada e sync gravado
    assert a.is_manual_price is False and a.last_auto_sync is not None
    assert b.is_manual_price is False and b.last_auto_sync is not None

    # Segunda rodada com novo preço no CC 640021 atualiza a linha automática.
    _patch_totvs(monkeypatch, _fake_registros(("030315", "640021", 30.00)))
    res2 = price_autosync.run_epi_price_autosync(db_session)
    assert res2["ok"] is True
    assert _get(db_session, "640021", "030315").valor == pytest.approx(30.00)


def test_respeita_is_manual_price(db_session, monkeypatch):
    # Linha travada manualmente com valor 99.
    db_session.add(CCItemPrice(codccu="640021", produto_codigo="030315", tamanho="",
                               valor=99.0, is_manual_price=True))
    db_session.commit()

    _patch_totvs(monkeypatch, _fake_registros(("030315", "640021", 22.95)))
    res = price_autosync.run_epi_price_autosync(db_session)
    assert res["ok"] is True
    assert res["skipped_manual"] == 1

    row = _get(db_session, "640021", "030315")
    assert row.valor == pytest.approx(99.0)          # NÃO sobrescreveu
    assert row.is_manual_price is True


def test_isolamento_por_ccu(db_session, monkeypatch):
    """Produto X manual no CCU A + automático no CCU B → B atualiza, A não."""
    # A: manual, valor fixado 99. B: automático, valor antigo 10.
    db_session.add(CCItemPrice(codccu="A", produto_codigo="X", tamanho="",
                               valor=99.0, is_manual_price=True))
    db_session.add(CCItemPrice(codccu="B", produto_codigo="X", tamanho="",
                               valor=10.0, is_manual_price=False))
    db_session.commit()

    _patch_totvs(monkeypatch, _fake_registros(
        ("X", "A", 50.0),   # TOTVS traz 50 para A, mas A é manual → ignora
        ("X", "B", 77.0),   # B é automático → atualiza para 77
    ))
    res = price_autosync.run_epi_price_autosync(db_session)
    assert res["ok"] is True

    a = _get(db_session, "A", "X")
    b = _get(db_session, "B", "X")
    assert a.valor == pytest.approx(99.0)   # manual preservado
    assert a.is_manual_price is True
    assert b.valor == pytest.approx(77.0)   # automático atualizado
    assert b.is_manual_price is False


def test_reverter_para_automatico_restaura(db_session, monkeypatch):
    """resync_one_cc_item aplica o valor da fonte à linha destravada."""
    db_session.add(CCItemPrice(codccu="A", produto_codigo="X", tamanho="",
                               valor=99.0, is_manual_price=False))
    db_session.commit()

    _patch_totvs(monkeypatch, _fake_registros(("X", "A", 33.33), ("X", "B", 44.44)))
    out = price_autosync.resync_one_cc_item(db_session, "A", "X")
    assert out["ok"] is True and out["aplicado"] is True
    assert out["valor"] == pytest.approx(33.33)
    assert _get(db_session, "A", "X").valor == pytest.approx(33.33)


def test_falha_totvs_nao_zera_precos(db_session, monkeypatch):
    """TOTVS indisponível => rotina falha segura; valores existentes intactos."""
    db_session.add(CCItemPrice(codccu="A", produto_codigo="X", tamanho="",
                               valor=88.0, is_manual_price=False))
    db_session.commit()

    def boom(sql):
        raise RuntimeError("TOTVS WSGETDATA retornou HTTP 500")
    monkeypatch.setattr(price_autosync.totvs_connector, "run_query", boom)

    res = price_autosync.run_epi_price_autosync(db_session)
    assert res["ok"] is False
    assert res["erro"] and "500" in res["erro"]
    # Valor NÃO foi zerado nem apagado.
    row = _get(db_session, "A", "X")
    assert row is not None and row.valor == pytest.approx(88.0)


def test_precos_invalidos_sao_ignorados(db_session, monkeypatch):
    _patch_totvs(monkeypatch, _fake_registros(
        ("X", "A", 0),        # <= 0 ignorado
        ("", "A", 10.0),      # sem código ignorado
        ("Y", "", 10.0),      # sem CC ignorado
        ("Z", "A", 12.5),     # válido
    ))
    res = price_autosync.run_epi_price_autosync(db_session)
    assert res["ok"] is True
    assert res["inserted"] == 1
    assert res["sem_valor"] == 3
    assert _get(db_session, "A", "Z").valor == pytest.approx(12.5)


def test_scheduler_trigger_now_dispara_rotina(db_session, monkeypatch):
    """trigger_now roda a rotina imediatamente (sem esperar o cron de 24h).
    A rotina abre sessão própria; validamos apenas que executa e retorna dict."""
    _patch_totvs(monkeypatch, _fake_registros(("X", "A", 15.0)))
    # run_epi_price_autosync sem db abre SessionLocal (banco de teste via engine).
    res = scheduler.trigger_now()
    assert isinstance(res, dict)
    assert "ok" in res
