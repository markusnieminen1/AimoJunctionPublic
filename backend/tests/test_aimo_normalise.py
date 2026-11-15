"""Tests for Milestone M3: AimoClient, CO2, Delivery, Normaliser, and POST /api/products/check."""

from __future__ import annotations

import json
import logging
from collections.abc import Generator
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.aimo.client import AimoClient, get_aimo_client
from app.aimo.normalise import check_products_live, normalise_product_line
from app.config import settings
from app.db import get_db, init_db, make_engine
from app.errors import AppError, request_id_var
from app.main import create_app
from app.models import Product
from app.services.co2 import compute_line_co2
from scripts.seed_catalog import (
    DEFAULT_CATEGORIES_PATH,
    DEFAULT_CO2_PATH,
    DEFAULT_RELIABILITY_PATH,
    seed_catalog,
)

FIXTURE_PRODUCTS_PATH = Path(__file__).resolve().parent / "fixtures" / "products_sample.json"
AIMO_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "aimo"


@pytest.fixture()
def m3_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[tuple[TestClient, sessionmaker[Session]], None, None]:
    """Isolated SQLite DB seeded with 50 sample products + TestClient in fixture mode."""
    monkeypatch.setenv("APP_NOW", "2026-11-13T21:00:00+02:00")
    monkeypatch.setattr(settings, "jwt_secret", "test-secret-key-with-at-least-32-bytes")
    monkeypatch.setattr(settings, "aimo_mode", "fixture")

    db_file = tmp_path / "m3_test.db"
    test_engine = make_engine(f"sqlite:///{db_file}")
    init_db(target_engine=test_engine)
    session_factory = sessionmaker(
        bind=test_engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    with session_factory() as db:
        seed_catalog(
            db,
            products_path=FIXTURE_PRODUCTS_PATH,
            co2_csv_path=DEFAULT_CO2_PATH,
            reliability_csv_path=DEFAULT_RELIABILITY_PATH,
            category_mapping_path=DEFAULT_CATEGORIES_PATH,
            batch_size=25,
        )

    test_app = create_app()
    local_client = AimoClient(mode="fixture", fixture_dir=AIMO_FIXTURE_DIR)

    def _override_get_db() -> Generator[Session, None, None]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    test_app.dependency_overrides[get_db] = _override_get_db
    test_app.dependency_overrides[get_aimo_client] = lambda: local_client

    client = TestClient(test_app)
    try:
        yield client, session_factory
    finally:
        test_app.dependency_overrides.clear()
        test_engine.dispose()


def _auth_headers(client: TestClient) -> dict[str, str]:
    reg = client.post(
        "/api/auth/register",
        json={
            "company_name": "Hotel Aurora Oy",
            "user_name": "Chef",
            "email": "chef@aurora.test",
            "password": "password123",
        },
    )
    assert reg.status_code == 201, reg.text
    token = reg.json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_products_check_requires_auth(m3_env: tuple[TestClient, sessionmaker[Session]]) -> None:
    client, _ = m3_env
    resp = client.post(
        "/api/products/check",
        json={"product_ids": ["464306"], "requested_delivery_date": "2026-11-16"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHENTICATED"


def test_products_check_validation_errors(
    m3_env: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = m3_env
    headers = _auth_headers(client)

    # Empty product_ids
    r_empty = client.post(
        "/api/products/check",
        headers=headers,
        json={"product_ids": [], "requested_delivery_date": "2026-11-16"},
    )
    assert r_empty.status_code == 422
    assert r_empty.json()["error"]["code"] == "VALIDATION_ERROR"

    # More than 50 product_ids
    r_many = client.post(
        "/api/products/check",
        headers=headers,
        json={"product_ids": ["464306"] * 51, "requested_delivery_date": "2026-11-16"},
    )
    assert r_many.status_code == 422
    assert r_many.json()["error"]["code"] == "VALIDATION_ERROR"

    # Bad date format
    r_date = client.post(
        "/api/products/check",
        headers=headers,
        json={"product_ids": ["464306"], "requested_delivery_date": "16.11.2026"},
    )
    assert r_date.status_code == 422
    assert r_date.json()["error"]["code"] == "VALIDATION_ERROR"

    # Unknown product in catalog -> 422 UNKNOWN_PRODUCT
    r_unknown = client.post(
        "/api/products/check",
        headers=headers,
        json={"product_ids": ["999999"], "requested_delivery_date": "2026-11-16"},
    )
    assert r_unknown.status_code == 422
    body = r_unknown.json()
    assert body["error"]["code"] == "UNKNOWN_PRODUCT"
    assert body["request_id"].startswith("req_")


def test_products_check_covers_recorded_fixtures_and_agent_auth(
    m3_env: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = m3_env
    headers = _auth_headers(client)

    # Create an agent and verify agent token works for POST /api/products/check
    agt = client.post(
        "/api/agents",
        headers=headers,
        json={
            "name": "Kitchen Bot",
            "auto_approve_limit_eur": 500,
            "max_order_value_eur": 5000,
            "daily_spend_limit_eur": 3000,
        },
    )
    assert agt.status_code == 201
    tok = client.post(
        "/api/agent-auth/token",
        json={
            "agent_id": agt.json()["agent"]["id"],
            "agent_secret": agt.json()["agent_secret"],
        },
    )
    assert tok.status_code == 200
    agent_headers = {"Authorization": f"Bearer {tok.json()['token']}"}

    resp = client.post(
        "/api/products/check",
        headers=agent_headers,
        json={
            "product_ids": ["464306", "469363", "463038", "460013"],
            "quantities": {"464306": 1000, "469363": 1000, "463038": 2, "460013": 5},
            "requested_delivery_date": "2026-11-16",
        },
    )
    assert resp.status_code == 200, resp.text
    items = {line["product_id"]: line for line in resp.json()["items"]}

    # 464306: Stock, UNLIMITED, ON_TIME, unit_price 1.01, total 1010.0, non-null CO2 + CO2_ESTIMATED
    p464306 = items["464306"]
    assert p464306["logistic_model"] == "Stock"
    assert p464306["available_qty_state"] == "UNLIMITED"
    assert p464306["available_amount"] is None
    assert p464306["delivery_status"] == "ON_TIME"
    assert p464306["on_time_probability"] == 1.0
    assert p464306["unit_price_eur"] == 1.01
    assert p464306["line_total_eur"] == 1010.0
    assert p464306["tax_rate"] == 13.5
    assert p464306["co2_kg"] == 1100.0
    assert p464306["co2_basis"] is not None
    assert "CO2_ESTIMATED" in p464306["flags"]

    # 469363: FINITE 384 -> PARTIAL when qty 1000, AT_RISK, LOW_RELIABILITY (missing -> 0.85)
    p469363 = items["469363"]
    assert p469363["logistic_model"] == "Stock"
    assert p469363["available_qty_state"] == "FINITE"
    assert p469363["available_amount"] == 384.0
    assert p469363["delivery_status"] == "AT_RISK"
    assert p469363["on_time_probability"] == 0.85
    assert "PARTIAL" in p469363["flags"]
    assert "LOW_RELIABILITY" in p469363["flags"]

    # 463038: Terminal, cut-off passed -> CUTOFF_PASSED, LATE, SUPPLIER_QTY_UNKNOWN, prob 0.0
    p463038 = items["463038"]
    assert p463038["logistic_model"] == "Terminal"
    assert p463038["available_qty_state"] == "TERMINAL_UNKNOWN"
    assert p463038["available_amount"] is None
    assert p463038["unit_price_eur"] == 31.31  # catalogPrice, never listPrice (36.83)
    assert p463038["line_total_eur"] == 62.62
    assert p463038["delivery_status"] == "LATE"
    assert p463038["on_time_probability"] == 0.0
    assert "CUTOFF_PASSED" in p463038["flags"]
    assert "SUPPLIER_QTY_UNKNOWN" in p463038["flags"]

    # 460013: priced, empty scheduleLines -> NOT_DELIVERABLE
    p460013 = items["460013"]
    assert p460013["unit_price_eur"] == 6.11
    assert p460013["line_total_eur"] == 30.55
    assert p460013["available_qty_state"] is None
    assert p460013["delivery_status"] == "NOT_DELIVERABLE"
    assert p460013["on_time_probability"] == 0.0
    assert "NOT_DELIVERABLE" in p460013["flags"]
    assert "CO2_ESTIMATED" not in p460013["flags"]
    assert p460013["co2_kg"] == round(1.96 * 1.0 * 5, 3)


def test_normalise_unit_mismatch_null_fields_and_pallet_unlimited(
    m3_env: tuple[TestClient, sessionmaker[Session]],
) -> None:
    _, session_factory = m3_env
    aimo = AimoClient(mode="fixture", fixture_dir=AIMO_FIXTURE_DIR)

    with session_factory() as db:
        # Insert 471142 (unit mismatch in recorded fixtures: catalog KI vs schedule ST)
        p471142 = Product(
            id="471142",
            name="Test unit mismatch product",
            names={"fi": "Test unit mismatch product"},
            category="10400",
            sales_unit="KI",
            base_unit="ST",
            units=[
                {"unit_id": "KI", "size_in_base_units": 6.0},
                {"unit_id": "ST", "size_in_base_units": 1.0},
            ],
            co2_kg_per_kg=1.5,
            delivery_reliability=0.98,
        )
        db.add(p471142)
        db.commit()

        lines = check_products_live(
            db,
            aimo,
            ["471142"],
            {"471142": 2},
            "2026-11-16",
            "cmp_test",
        )
        assert len(lines) == 1
        assert "UNIT_MISMATCH" in lines[0].flags
        assert "SUPPLIER_QTY_UNKNOWN" in lines[0].flags
        assert "CUTOFF_PASSED" in lines[0].flags
        assert lines[0].delivery_status == "LATE"

        # Synthetic Stock unit mismatch (convertible and unconvertible)
        p_conv = Product(
            id="synth_conv",
            name="Convertible Stock Product",
            names={"fi": "Convertible Stock Product"},
            category="10400",
            sales_unit="KI",
            base_unit="ST",
            units=[
                {"unit_id": "KI", "size_in_base_units": 10.0},
                {"unit_id": "ST", "size_in_base_units": 1.0},
            ],
            delivery_reliability=0.99,
        )
        p_unconv = Product(
            id="synth_unconv",
            name="Unconvertible Stock Product",
            names={"fi": "Unconvertible Stock Product"},
            category="10400",
            sales_unit="KI",
            base_unit="ST",
            units=[{"unit_id": "KI", "size_in_base_units": 10.0}],
            delivery_reliability=0.99,
        )
        from app import clock

        now = clock.now()
        line_conv = normalise_product_line(
            p_conv,
            {"productId": "synth_conv", "catalogPrice": {"value": 20.0}, "tax": {"taxRate": 13.5}},
            {
                "productId": "synth_conv",
                "closingTime": "2026-11-15T17:00:00+0200",
                "salesBlock": False,
                "logisticModel": "Stock",
                "scheduleLines": [
                    {
                        "salesBlock": False,
                        "availableAmount": 50.0,
                        "availableDate": "2026-11-16",
                        "salesUnit": "ST",
                    }
                ],
            },
            quantity=3.0,
            requested_delivery_date="2026-11-16",
            now=now,
        )
        assert "UNIT_MISMATCH" in line_conv.flags
        assert "PARTIAL" not in line_conv.flags
        assert line_conv.available_amount == 5.0  # 50 ST / 10 = 5 KI
        assert line_conv.delivery_status == "AT_RISK"

        line_unconv = normalise_product_line(
            p_unconv,
            {"productId": "synth_unconv", "catalogPrice": {"value": 20.0}},
            {
                "productId": "synth_unconv",
                "closingTime": "2026-11-15T17:00:00+0200",
                "salesBlock": False,
                "logisticModel": "Stock",
                "scheduleLines": [
                    {
                        "salesBlock": False,
                        "availableAmount": 50.0,
                        "availableDate": "2026-11-16",
                        "salesUnit": "PAK",
                    }
                ],
            },
            quantity=3.0,
            requested_delivery_date="2026-11-16",
            now=now,
        )
        assert "UNIT_MISMATCH" in line_unconv.flags
        assert "PARTIAL" in line_unconv.flags
        assert line_unconv.available_amount is None
        assert line_unconv.delivery_status == "AT_RISK"

        # Null availableDate (zero stock) and closingTime=None
        p_zero = Product(
            id="synth_zero",
            name="Zero stock product",
            names={"fi": "Zero stock product"},
            category="10400",
            sales_unit="ST",
            base_unit="ST",
            units=[{"unit_id": "ST", "size_in_base_units": 1.0}],
            delivery_reliability=0.99,
        )
        line_zero = normalise_product_line(
            p_zero,
            {"productId": "synth_zero", "netPriceFound": False, "error": True},
            {
                "productId": "synth_zero",
                "closingTime": None,
                "salesBlock": False,
                "logisticModel": "Stock",
                "scheduleLines": [
                    {
                        "salesBlock": False,
                        "availableAmount": 0.0,
                        "availableDate": None,
                        "salesUnit": "ST",
                    }
                ],
            },
            quantity=1.0,
            requested_delivery_date="2026-11-16",
            now=now,
        )
        assert line_zero.available_qty_state == "ZERO"
        assert line_zero.available_amount is None
        assert line_zero.closing_time is None
        assert line_zero.available_date is None
        assert "CUTOFF_PASSED" in line_zero.flags
        assert "LATE" in line_zero.flags
        assert "PARTIAL" in line_zero.flags
        assert "NO_PRICE" in line_zero.flags
        assert line_zero.delivery_status == "LATE"
        assert line_zero.on_time_probability == 0.0

        # Pallet UNLIMITED where raw amt < 1e8 (70,000,000 PAL * 160 >= 0.99 * 9,999,999,999)
        p_pallet = Product(
            id="synth_pallet",
            name="Pallet product",
            names={"fi": "Pallet product"},
            category="10400",
            sales_unit="PAL",
            base_unit="ST",
            units=[
                {"unit_id": "PAL", "size_in_base_units": 160.0},
                {"unit_id": "ST", "size_in_base_units": 1.0},
            ],
            delivery_reliability=0.99,
        )
        line_pallet = normalise_product_line(
            p_pallet,
            {
                "productId": "synth_pallet",
                "catalogPrice": {"value": 100.0},
                "tax": {"taxRate": 13.5},
            },
            {
                "productId": "synth_pallet",
                "closingTime": "2026-11-15T17:00:00+0200",
                "salesBlock": False,
                "logisticModel": "Stock",
                "scheduleLines": [
                    {
                        "salesBlock": False,
                        "availableAmount": 70_000_000.0,
                        "availableDate": "2026-11-16",
                        "salesUnit": "PAL",
                    }
                ],
            },
            quantity=1.0,
            requested_delivery_date="2026-11-16",
            now=now,
        )
        assert line_pallet.available_qty_state == "UNLIMITED"
        assert line_pallet.available_amount is None
        assert line_pallet.delivery_status == "ON_TIME"


def test_co2_resolution_order() -> None:
    # (1) units[] when base_unit == "KG"
    p1 = Product(
        id="c1",
        name="Butter case",
        names={},
        category="10400",
        sales_unit="KI",
        base_unit="KG",
        units=[{"unit_id": "KI", "size_in_base_units": 5.0}],
        co2_kg_per_kg=8.0,
    )
    co2_1, basis_1, est_1 = compute_line_co2(p1, None, 2.0)
    assert co2_1 == 80.0
    assert est_1 is False
    assert basis_1 is not None and "5 kg/KI" in basis_1

    # (2) comparisonPrice with comparisonUnit == "KG" vs "L"
    p2 = Product(
        id="c2",
        name="Juice",
        names={},
        category="10400",
        sales_unit="ST",
        base_unit="ST",
        units=[{"unit_id": "ST", "size_in_base_units": 1.0}],
        co2_kg_per_kg=1.2,
    )
    co2_2kg, _, est_2kg = compute_line_co2(
        p2,
        {"catalogPrice": {"value": 3.0}, "comparisonPrice": {"value": 1.5}, "comparisonUnit": "KG"},
        1.0,
    )
    assert co2_2kg == 2.4
    assert est_2kg is False

    co2_2l, basis_2l, est_2l = compute_line_co2(
        p2,
        {"catalogPrice": {"value": 3.0}, "comparisonPrice": {"value": 1.5}, "comparisonUnit": "L"},
        1.0,
    )
    assert co2_2l == 2.4
    assert est_2l is True
    assert basis_2l is not None and "1.0 kg/L (assumed)" in basis_2l

    # (3) unit_conversions netWeight (ignore volume & grossWeight)
    p3 = Product(
        id="c3",
        name="Salad 400g",
        names={},
        category="10400",
        sales_unit="ST",
        base_unit="ST",
        units=[{"unit_id": "ST", "size_in_base_units": 1.0}],
        unit_conversions=[
            {
                "synkkaPackagingType": "BASE_UNIT_OR_EACH",
                "volume": {"unit": "LTR", "value": 99.0},
                "netWeight": {"unit": "GRM", "value": 400.0},
                "grossWeight": {"unit": "GRM", "value": 450.0},
            }
        ],
        co2_kg_per_kg=2.0,
    )
    co2_3, basis_3, est_3 = compute_line_co2(p3, None, 3.0)
    assert co2_3 == 2.4  # 2.0 * 0.4 * 3
    assert est_3 is False
    assert basis_3 is not None and "Synkka netWeight" in basis_3

    # (4) Finnish/English product name fallback
    p4 = Product(
        id="c4",
        name="Kaurajuoma 1,5 l",
        names={"fi": "Kaurajuoma 1,5 l"},
        category="10400",
        sales_unit="ST",
        base_unit="ST",
        units=[],
        unit_conversions=[],
        co2_kg_per_kg=0.5,
    )
    co2_4, basis_4, est_4 = compute_line_co2(p4, None, 4.0)
    assert co2_4 == 3.0  # 0.5 * 1.5 * 4
    assert est_4 is True
    assert basis_4 is not None

    # (5) Missing co2_kg_per_kg or unresolvable weight -> None
    p5 = Product(
        id="c5",
        name="Mystery item",
        names={},
        category="10400",
        sales_unit="ST",
        base_unit="ST",
        units=[],
        unit_conversions=[],
        co2_kg_per_kg=2.0,
    )
    assert compute_line_co2(p5, None, 1.0) == (None, None, False)


def test_aimo_client_retry_backoff_503_cache_chunking_and_logging(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    sleeps: list[float] = []
    client = AimoClient(
        base_url="https://aimo.example.test",
        api_key="test-key-xyz",
        mode="live",
        retry_delays=(0.5, 1.0, 2.0),
        cache_ttl_seconds=60.0,
        sleep_fn=sleeps.append,
    )

    token = request_id_var.set("req_test_aimo_123")
    try:
        with respx.mock(assert_all_called=True) as mock:
            # 1) Retry on 429 then TimeoutException, succeed on 3rd attempt
            route = mock.post("https://aimo.example.test/api/price").mock(
                side_effect=[
                    httpx.Response(429, json={"detail": "rate limit"}),
                    httpx.TimeoutException("timed out"),
                    httpx.Response(
                        200,
                        json={
                            "customerId": "123",
                            "currency": "EUR",
                            "products": [{"productId": "464306", "error": False}],
                        },
                    ),
                ]
            )
            res = client.price(["464306", "469363"], "2026-11-16")
            assert res["products"][0]["productId"] == "464306"
            assert route.call_count == 3
            assert sleeps == [0.5, 1.0]
            assert route.calls[0].request.headers["X-API-Key"] == "test-key-xyz"

            # 2) Cache hit even with reversed ID order
            res_cached = client.price(["469363", "464306"], "2026-11-16")
            assert res_cached == res
            assert route.call_count == 3  # no extra HTTP call

        # Verify request_id, endpoint, count, latency_ms, status logged
        aimo_logs = [r.message for r in caplog.records if "aimo_upstream" in r.message]
        assert any(
            "request_id=req_test_aimo_123" in msg
            and "endpoint=/api/price" in msg
            and "count=2" in msg
            and "latency_ms=" in msg
            and "status=200" in msg
            for msg in aimo_logs
        )

        # 3) Exhaust all 3 retries (4 attempts total) -> 503 UPSTREAM_UNAVAILABLE
        sleeps.clear()
        client.clear_cache()
        with respx.mock(assert_all_called=True) as mock:
            fail_route = mock.post("https://aimo.example.test/api/availability").mock(
                side_effect=[
                    httpx.Response(502),
                    httpx.Response(503),
                    httpx.RequestError("conn reset"),
                    httpx.Response(500),
                ]
            )
            with pytest.raises(AppError) as exc_info:
                client.availability(["464306"], "2026-11-16")
            assert exc_info.value.code == "UPSTREAM_UNAVAILABLE"
            assert exc_info.value.status_code == 503
            assert fail_route.call_count == 4
            assert sleeps == [0.5, 1.0, 2.0]

        # 4) Chunking at 1000 IDs max per upstream call
        client.clear_cache()
        many_ids = [f"{400000 + i}" for i in range(1005)]
        with respx.mock(assert_all_called=True) as mock:
            chunk_route = mock.post("https://aimo.example.test/api/price").mock(
                side_effect=[
                    httpx.Response(
                        200,
                        json={
                            "customerId": "123",
                            "products": [{"productId": pid} for pid in many_ids[:1000]],
                        },
                    ),
                    httpx.Response(
                        200,
                        json={
                            "customerId": "123",
                            "products": [{"productId": pid} for pid in many_ids[1000:]],
                        },
                    ),
                ]
            )
            chunked_res = client.price(many_ids, "2026-11-16")
            assert chunk_route.call_count == 2
            assert len(chunked_res["products"]) == 1005
            first_req = json.loads(chunk_route.calls[0].request.content)
            second_req = json.loads(chunk_route.calls[1].request.content)
            assert len(first_req["products"]) == 1000
            assert len(second_req["products"]) == 5

        # 5) ping() in live mode
        with respx.mock(assert_all_called=True) as mock:
            mock.get("https://aimo.example.test/api/ping").mock(
                return_value=httpx.Response(200, json={"status": "ok"})
            )
            assert client.ping() is True
    finally:
        request_id_var.reset(token)


def test_aimo_client_fixture_unknown_id_shape_and_validation() -> None:
    client = AimoClient(mode="fixture", fixture_dir=AIMO_FIXTURE_DIR)
    assert client.ping() is True

    price = client.price(["987654321"], "2026-11-16")
    assert price["products"] == [
        {"productId": "987654321", "quantity": 1.0, "netPriceFound": False, "error": True}
    ]
    avail = client.availability(["987654321"], "2026-11-16")
    assert avail["products"] == [
        {"productId": "987654321", "salesBlock": False, "scheduleLines": []}
    ]

    with pytest.raises(AppError) as exc:
        client.price([464306], "2026-11-16")  # type: ignore[list-item]
    assert exc.value.code == "VALIDATION_ERROR"
    assert exc.value.status_code == 422
