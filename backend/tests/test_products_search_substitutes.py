"""Tests for Milestone M4: Products Search, Allergen/Diet Rules, Categories, and Substitutions."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.aimo.client import AimoClient, get_aimo_client
from app.config import settings
from app.db import get_db, init_db, make_engine
from app.main import create_app
from app.models import Product
from app.services.catalog import product_passes_diets, resolve_diet_keys
from scripts.seed_catalog import (
    DEFAULT_CATEGORIES_PATH,
    DEFAULT_CO2_PATH,
    DEFAULT_RELIABILITY_PATH,
    seed_catalog,
)

FIXTURE_PRODUCTS_PATH = Path(__file__).resolve().parent / "fixtures" / "products_sample.json"
AIMO_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "aimo"


@pytest.fixture()
def m4_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[tuple[TestClient, sessionmaker[Session]], None, None]:
    """Isolated SQLite DB seeded with 50 sample products + TestClient in fixture mode."""
    monkeypatch.setenv("APP_NOW", "2026-11-13T21:00:00+02:00")
    monkeypatch.setattr(settings, "jwt_secret", "test-secret-key-with-at-least-32-bytes")
    monkeypatch.setattr(settings, "aimo_mode", "fixture")

    db_file = tmp_path / "m4_test.db"
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


def _user_auth_headers(client: TestClient) -> dict[str, str]:
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
    return {"Authorization": f"Bearer {reg.json()['token']}"}


def test_products_endpoints_require_auth(
    m4_env: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = m4_env
    for method, url, kwargs in (
        ("GET", "/api/products", {}),
        ("GET", "/api/products/464306", {}),
        ("GET", "/api/categories", {}),
        (
            "POST",
            "/api/products/substitutes",
            {
                "json": {
                    "product_id": "469363",
                    "quantity": 1000,
                    "requested_delivery_date": "2026-11-16",
                }
            },
        ),
    ):
        resp = client.request(method, url, **kwargs)
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "UNAUTHENTICATED"


def test_search_laktoositon_maito_and_multilang_pagination(
    m4_env: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = m4_env
    headers = _user_auth_headers(client)

    resp = client.get("/api/products", params={"q": "laktoositon maito"}, headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] >= 4
    top5_ids = [item["id"] for item in data["items"][:5]]
    assert "464306" in top5_ids

    first = data["items"][0]
    assert set(first.keys()) == {
        "id",
        "name",
        "names",
        "category",
        "category_name",
        "sales_unit",
        "base_unit",
        "units",
        "vendor",
        "brand",
        "country_of_origin",
        "temperature_condition",
        "allergens",
        "non_allergens",
        "labels",
        "image_url",
        "co2_kg_per_kg",
        "delivery_reliability",
    }

    # English & Swedish search + exact ID search
    en_resp = client.get("/api/products", params={"q": "semi skimmed milk"}, headers=headers)
    assert en_resp.status_code == 200
    assert "464306" in [item["id"] for item in en_resp.json()["items"]]

    sv_resp = client.get("/api/products", params={"q": "lättmjölksdryck"}, headers=headers)
    assert sv_resp.status_code == 200
    assert "464306" in [item["id"] for item in sv_resp.json()["items"]]

    id_resp = client.get("/api/products", params={"q": "464306"}, headers=headers)
    assert id_resp.status_code == 200
    assert id_resp.json()["items"][0]["id"] == "464306"

    # Pagination
    p1 = client.get(
        "/api/products", params={"q": "laktoositon maito", "limit": 2, "offset": 0}, headers=headers
    ).json()
    p2 = client.get(
        "/api/products", params={"q": "laktoositon maito", "limit": 2, "offset": 2}, headers=headers
    ).json()
    assert p1["total"] == p2["total"]
    assert len(p1["items"]) == 2
    assert len(p2["items"]) == 2
    assert {x["id"] for x in p1["items"]}.isdisjoint({x["id"] for x in p2["items"]})

    # Category prefix + exact + label filters
    cat_prefix = client.get("/api/products", params={"category": "104"}, headers=headers).json()
    cat_ids = {x["id"] for x in cat_prefix["items"]}
    assert {"464306", "460005"}.issubset(cat_ids)

    lbl_resp = client.get(
        "/api/products",
        params={"q": "laktoositon maito", "label": "coolTemperature"},
        headers=headers,
    ).json()
    assert {x["id"] for x in lbl_resp["items"]} == {"464306", "463038"}


def test_diet_m_excludes_464306_while_diet_l_includes_464306(
    m4_env: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = m4_env
    headers = _user_auth_headers(client)

    # diet=M (MILK_FREE) and allergen_free=milk MUST exclude 464306
    for params in (
        {"q": "laktoositon maito", "diet": "M"},
        {"q": "laktoositon maito", "diet": "MILK_FREE"},
        {"q": "laktoositon maito", "allergen_free": "milk"},
        {"diet": "M", "limit": 100},
        {"allergen_free": "milk", "limit": 100},
    ):
        r = client.get("/api/products", params=params, headers=headers)
        assert r.status_code == 200, r.text
        ids = {x["id"] for x in r.json()["items"]}
        assert "464306" not in ids
        assert "464398" not in ids
        assert "469363" not in ids
        assert "463038" not in ids

    # Truly milk-free items ARE returned by diet=M / allergen_free=milk
    r_milk_free = client.get(
        "/api/products", params={"allergen_free": "milk", "limit": 100}, headers=headers
    )
    assert r_milk_free.status_code == 200
    mf_ids = {x["id"] for x in r_milk_free.json()["items"]}
    assert "460005" in mf_ids  # Alpro soy product

    # diet=L (LACTOSE_FREE) and allergen_free=lactose MUST include 464306
    for params in (
        {"q": "laktoositon maito", "diet": "L"},
        {"q": "laktoositon maito", "diet": "LACTOSE_FREE"},
        {"q": "laktoositon maito", "allergen_free": "lactose"},
    ):
        r = client.get("/api/products", params=params, headers=headers)
        assert r.status_code == 200, r.text
        ids = {x["id"] for x in r.json()["items"]}
        assert "464306" in ids

    # Invalid diet / allergen_free returns 422 VALIDATION_ERROR with request_id
    bad_diet = client.get("/api/products", params={"diet": "INVALID_DIET"}, headers=headers)
    assert bad_diet.status_code == 422
    assert bad_diet.json()["error"]["code"] == "VALIDATION_ERROR"
    assert bad_diet.json()["request_id"].startswith("req_")

    bad_af = client.get("/api/products", params={"allergen_free": "unobtainium"}, headers=headers)
    assert bad_af.status_code == 422
    assert bad_af.json()["error"]["code"] == "VALIDATION_ERROR"


def test_unknown_synkka_460013_excluded_by_strict_allergen_filters(
    m4_env: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = m4_env
    headers = _user_auth_headers(client)

    # Confirm 460013 exists and shows up in unfiltered search
    unfiltered = client.get("/api/products", params={"q": "460013"}, headers=headers)
    assert unfiltered.status_code == 200
    assert [x["id"] for x in unfiltered.json()["items"]] == ["460013"]

    # Excluded by allergen_free=milk, allergen_free=gluten, diet=M, diet=G, diet=VEG
    for params in (
        {"q": "460013", "allergen_free": "milk"},
        {"q": "460013", "allergen_free": "gluten"},
        {"q": "460013", "diet": "M"},
        {"q": "460013", "diet": "G"},
        {"q": "460013", "diet": "VEG"},
        {"allergen_free": "milk", "limit": 100},
        {"allergen_free": "gluten", "limit": 100},
    ):
        r = client.get("/api/products", params=params, headers=headers)
        assert r.status_code == 200, r.text
        assert "460013" not in {x["id"] for x in r.json()["items"]}

    # Direct domain test: ABSENT_WEAK, TRACE, and non-food category must fail strict diet check
    weak_prod = Product(
        id="test_weak",
        name="Weak milk",
        names={"fi": "Weak milk"},
        category="10400",
        sales_unit="ST",
        base_unit="ST",
        allergen_states={"AM": "ABSENT_WEAK"},
    )
    trace_prod = Product(
        id="test_trace",
        name="Trace milk",
        names={"fi": "Trace milk"},
        category="10400",
        sales_unit="ST",
        base_unit="ST",
        allergen_states={"AM": "TRACE"},
    )
    non_food = Product(
        id="test_nonfood",
        name="Pot",
        names={"fi": "Kattila"},
        category="20011",
        sales_unit="ST",
        base_unit="ST",
        allergen_states={"AM": "ABSENT"},
    )
    assert product_passes_diets(weak_prod, ["MILK_FREE"])[0] is False
    assert product_passes_diets(trace_prod, ["MILK_FREE"])[0] is False
    assert product_passes_diets(non_food, ["MILK_FREE"])[0] is False

    assert resolve_diet_keys("L,M,HEART", "gluten") == [
        "LACTOSE_FREE",
        "MILK_FREE",
        "HEART_LOW_SALT",
        "GLUTEN_FREE_COELIAC",
    ]


def test_get_product_detail_and_categories(
    m4_env: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = m4_env
    headers = _user_auth_headers(client)

    detail = client.get("/api/products/464306", headers=headers)
    assert detail.status_code == 200, detail.text
    prod = detail.json()
    assert prod["id"] == "464306"
    assert prod["category"] == "10400"
    assert prod["sales_unit"] == "ST"
    assert prod["co2_kg_per_kg"] is not None
    assert prod["delivery_reliability"] == 1.0
    assert len(prod["units"]) >= 1
    assert "fi" in prod["names"] and "en" in prod["names"]

    missing = client.get("/api/products/999999", headers=headers)
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "NOT_FOUND"
    assert missing.json()["request_id"].startswith("req_")

    cats = client.get("/api/categories", headers=headers)
    assert cats.status_code == 200, cats.text
    items = cats.json()["items"]
    assert len(items) >= 1
    cat_10400 = next(c for c in items if c["code"] == "10400")
    assert cat_10400["product_count"] >= 4
    assert cat_10400["name"]
    assert [c["product_count"] for c in items] == sorted(
        (c["product_count"] for c in items), reverse=True
    )


def test_substitutes_for_469363_and_463038_and_diet_m_excludes_milk(
    m4_env: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = m4_env
    headers = _user_auth_headers(client)

    # Also verify an agent can call search and substitutes
    agent_create = client.post(
        "/api/agents",
        headers=headers,
        json={
            "name": "Kitchen Scout",
            "auto_approve_limit_eur": 500,
            "max_order_value_eur": 5000,
            "daily_spend_limit_eur": 5000,
        },
    )
    assert agent_create.status_code == 201
    agent_id = agent_create.json()["agent"]["id"]
    agent_secret = agent_create.json()["agent_secret"]
    tok_resp = client.post(
        "/api/agent-auth/token",
        json={"agent_id": agent_id, "agent_secret": agent_secret},
    )
    assert tok_resp.status_code == 200
    agent_headers = {"Authorization": f"Bearer {tok_resp.json()['token']}"}

    # 469363 (qty 1000, PARTIAL 384) -> returns 464306 and 464398 ON_TIME with trade-offs
    sub_469363 = client.post(
        "/api/products/substitutes",
        headers=agent_headers,
        json={
            "product_id": "469363",
            "quantity": 1000,
            "requested_delivery_date": "2026-11-16",
        },
    )
    assert sub_469363.status_code == 200, sub_469363.text
    body = sub_469363.json()
    assert body["requested_product_id"] == "469363"
    cand_ids = [c["product"]["id"] for c in body["items"]]
    assert "464306" in cand_ids
    assert "464398" in cand_ids
    assert "463038" not in cand_ids  # CUTOFF_PASSED / LATE excluded

    c_464306 = next(c for c in body["items"] if c["product"]["id"] == "464306")
    assert c_464306["quote_line"]["delivery_status"] == "ON_TIME"
    assert c_464306["price_delta_eur"] == -1.47
    assert any("Storage:" in t and "Chilled" in t for t in c_464306["tradeoffs"])
    assert any("Price -€1.47/ST" in t for t in c_464306["tradeoffs"])
    assert "LACTOSE_FREE" in c_464306["reason"]

    # 463038 (Terminal, CUTOFF_PASSED) -> returns 464306 and 464398 with unit & storage tradeoffs
    sub_463038 = client.post(
        "/api/products/substitutes",
        headers=headers,
        json={
            "product_id": "463038",
            "quantity": 5,
            "requested_delivery_date": "2026-11-16",
        },
    )
    assert sub_463038.status_code == 200, sub_463038.text
    ids_463038 = [c["product"]["id"] for c in sub_463038.json()["items"]]
    assert "464306" in ids_463038
    assert "464398" in ids_463038
    first_463038 = sub_463038.json()["items"][0]
    assert any("Sales unit:" in t for t in first_463038["tradeoffs"])

    # With diet=M (MILK_FREE), 464306 and 464398 MUST be excluded!
    for pid in ("469363", "463038"):
        sub_m = client.post(
            "/api/products/substitutes",
            headers=headers,
            json={
                "product_id": pid,
                "quantity": 100,
                "requested_delivery_date": "2026-11-16",
                "diet": "M",
            },
        )
        assert sub_m.status_code == 200, sub_m.text
        m_ids = [c["product"]["id"] for c in sub_m.json()["items"]]
        assert "464306" not in m_ids
        assert "464398" not in m_ids

    # Unknown product -> 404 NOT_FOUND
    sub_missing = client.post(
        "/api/products/substitutes",
        headers=headers,
        json={
            "product_id": "999999",
            "quantity": 1,
            "requested_delivery_date": "2026-11-16",
        },
    )
    assert sub_missing.status_code == 404
    assert sub_missing.json()["error"]["code"] == "NOT_FOUND"
