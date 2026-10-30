# The F10 fault itself: the flaky vendor, the edge check on product names, the
# misbehaving tool, and the agent's circuit breaker with its cache.
import itertools

import pytest
from fastapi.testclient import TestClient

from common import db
from fleet import base_agent
from fleet import procurement_agent
from services import vendor_api


@pytest.fixture
def vendor():
    vendor_api.chaos.mode = "none"
    vendor_api.chaos.requests = 0
    client = TestClient(vendor_api.app)
    yield client
    vendor_api.chaos.mode = "none"


def codes(client, item="laptops", n=6):
    return [client.get("/quotes", params={"item": item}).status_code for _ in range(n)]


def test_flaky_fails_exactly_every_second_request(vendor):
    vendor.post("/admin/chaos", json={"mode": "flaky"})
    assert codes(vendor) == [200, 503, 200, 503, 200, 503]


def test_flaky_hides_from_the_health_endpoint(vendor):
    vendor.post("/admin/chaos", json={"mode": "flaky"})
    assert vendor.get("/admin/state").json()["status"] == "up"


def test_down_is_visible_on_the_health_endpoint(vendor):
    vendor.post("/admin/chaos", json={"mode": "down"})
    assert codes(vendor, n=3) == [503, 503, 503]
    assert vendor.get("/admin/state").json()["status"] == "down"


@pytest.mark.parametrize("mode", ["none", "flaky", "down"])
def test_unknown_product_is_always_404(vendor, mode):
    vendor.post("/admin/chaos", json={"mode": mode})
    assert codes(vendor, item="laptop-x", n=4) == [404] * 4


def test_unknown_chaos_mode_is_rejected(vendor):
    assert vendor.post("/admin/chaos", json={"mode": "explode"}).status_code == 422


@pytest.fixture
def sent_items(monkeypatch):
    sent = []

    def fake_get(url, params=None):
        sent.append(params["item"])
        return {"item": params["item"], "quantity": 1, "quotes": [{"vendor": "V", "unit_price": 1}]}
    monkeypatch.setattr(procurement_agent, "http_get", fake_get)
    monkeypatch.setattr(procurement_agent, "_calls", itertools.count(1))
    return sent


def test_misbehaving_tool_garbles_every_third_call(sent_items):
    db.set_control("chaos.procurement.bad_item_every", 3)
    for _ in range(6):
        procurement_agent.get_quotes("monitors", 1)
    assert sent_items == ["monitors", "monitors", "monitor-x", "monitors", "monitors", "monitor-x"]


def test_tool_is_normal_without_the_fault(sent_items):
    for _ in range(4):
        procurement_agent.get_quotes("printers", 1)
    assert sent_items == ["printers"] * 4


def test_open_breaker_with_fallback_serves_cached_quotes(sent_items):
    procurement_agent.get_quotes("laptops", 1)
    db.set_control("circuit.vendor_api", "open")
    db.set_control("fallback.vendor_api", True)
    data = procurement_agent.get_quotes("laptops", 7)
    assert data["source"] == "cache"
    assert data["quantity"] == 7
    assert sent_items == ["laptops"]


def test_open_breaker_without_cache_fails_fast(sent_items):
    db.set_control("circuit.vendor_api", "open")
    db.set_control("fallback.vendor_api", True)
    with pytest.raises(base_agent.ToolError, match="no cached data"):
        procurement_agent.get_quotes("printers", 1)
    assert sent_items == []


def test_open_breaker_without_fallback_never_calls_the_service(sent_items):
    db.set_control("circuit.vendor_api", "open")
    with pytest.raises(base_agent.ToolError, match="circuit open"):
        procurement_agent.get_quotes("laptops", 1)
    assert sent_items == []
