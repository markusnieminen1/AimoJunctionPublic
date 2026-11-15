"""Tests for Batch B: P1 Flight Simulator & P2 Pre-Flight Cart Optimizer."""

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

import app.services.optimizer as optimizer_module
from app.aimo.client import AimoClient, get_aimo_client
from app.aimo.normalise import check_products_live
from app.config import settings
from app.db import get_db, init_db, make_engine
from app.main import create_app
from app.models import GatewaySubmission, Order, Product
from app.services.order_gateway import get_gateway_simulate_mode
from scripts.demo_seed import seed_demo
from scripts.seed_catalog import seed_catalog
from tests.test_governance_deals_menu_trace import _seed_extra_valio_skus

FIXTURE_PRODUCTS_PATH = Path(__file__).resolve().parent / "fixtures" / "products_sample.json"
AIMO_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "aimo"


@pytest.fixture()
def batch_b_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[dict[str, object], None, None]:
    """Isolated SQLite DB + TestClient with catalog + extra Valio/Oddlygood SKUs."""
    monkeypatch.setenv("APP_NOW", "2026-11-13T21:00:00+02:00")
    monkeypatch.setattr(settings, "app_env", "dev")
    monkeypatch.setattr(settings, "jwt_secret", "test-secret-key-with-at-least-32-bytes")
    monkeypatch.setattr(settings, "aimo_mode", "fixture")
    monkeypatch.setattr(settings, "llm_provider", "fake")
    monkeypatch.setattr(settings, "fast_llm_provider", "fake")
    monkeypatch.setattr(settings, "order_gateway_simulate", "none")

    db_file = tmp_path / "batch_b.db"
    engine = make_engine(f"sqlite:///{db_file}")
    init_db(target_engine=engine)
    session_factory = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
    )

    with session_factory() as db:
        seed_catalog(db, products_path=FIXTURE_PRODUCTS_PATH)
        _seed_extra_valio_skus(db)
        # Ensure 468465 Oddlygood Dreamy Oat 1l has realistic lower CO2/kg (0.42) vs dairy (1.1)
        oat_gf = db.get(Product, "468465")
        if oat_gf is not None:
            oat_gf.co2_kg_per_kg = 0.42
            oat_gf.delivery_reliability = 1.0
            oat_gf.temperature_condition = 8
            oat_gf.temperature_class = "ambient"
        db.commit()
        creds = seed_demo(db, credentials_path=tmp_path / ".demo_credentials")

    fixture_client = AimoClient(mode="fixture", fixture_dir=AIMO_FIXTURE_DIR)
    app = create_app()

    def _override_db() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_aimo_client] = lambda: fixture_client

    with TestClient(app) as client:
        u_login = client.post(
            "/api/auth/login",
            json={"email": "demo@aimoannos.test", "password": creds["user"]["password"]},
        )
        assert u_login.status_code == 200
        user_headers = {"Authorization": f"Bearer {u_login.json()['token']}"}

        kitchen = creds["agents"]["kitchen_agent"]
        a_tok = client.post(
            "/api/agent-auth/token",
            json={"agent_id": kitchen["id"], "agent_secret": kitchen["agent_secret"]},
        )
        assert a_tok.status_code == 200
        agent_headers = {"Authorization": f"Bearer {a_tok.json()['token']}"}

        yield {
            "client": client,
            "session_factory": session_factory,
            "user_headers": user_headers,
            "agent_headers": agent_headers,
            "kitchen_agent_id": kitchen["id"],
        }

    app.dependency_overrides.clear()
    engine.dispose()


def test_flight_simulator_all_5_scenarios_rbac_isolation_and_audit_chain(
    batch_b_env: dict[str, object],
) -> None:
    """Verify RBAC (403 for agents), all 5 scenarios end-to-end, scoped gateway & cleanups."""
    client: TestClient = batch_b_env["client"]  # type: ignore[assignment]
    session_factory: sessionmaker[Session] = batch_b_env["session_factory"]  # type: ignore[assignment]
    user_headers: dict[str, str] = batch_b_env["user_headers"]  # type: ignore[assignment]
    agent_headers: dict[str, str] = batch_b_env["agent_headers"]  # type: ignore[assignment]
    kitchen_agent_id: str = batch_b_env["kitchen_agent_id"]  # type: ignore[assignment]

    # 1. Agents MUST receive 403 FORBIDDEN on simulator routes
    assert client.get("/api/dev/simulator/scenarios", headers=agent_headers).status_code == 403
    assert (
        client.post(
            "/api/dev/simulator/run",
            headers=agent_headers,
            json={"scenario_id": "unit_trap"},
        ).status_code
        == 403
    )

    # 2. List scenarios as human user
    sc_resp = client.get("/api/dev/simulator/scenarios", headers=user_headers)
    assert sc_resp.status_code == 200
    scenarios = sc_resp.json()["scenarios"]
    scenario_ids = [s["id"] for s in scenarios]
    assert scenario_ids == [
        "unit_trap",
        "diet_breach",
        "gateway_timeout_recovery",
        "partial_and_cutoff",
        "tampered_approval_hash",
    ]

    # 3. Run all 5 scenarios and verify real outcomes + untouched global gateway mode
    assert get_gateway_simulate_mode() == "none"

    results: dict[str, dict[str, object]] = {}
    for sid in scenario_ids:
        r = client.post(
            "/api/dev/simulator/run",
            headers=user_headers,
            json={"scenario_id": sid},
        )
        assert r.status_code == 200, f"{sid} failed: {r.text}"
        body = r.json()
        assert body["passed"] is True, f"{sid} did not pass: {body}"
        assert body["audit_chain"]["valid"] is True
        results[sid] = body

    # Global gateway simulate mode was never mutated
    assert get_gateway_simulate_mode() == "none"

    # Verify specific scenario guarantees
    assert results["unit_trap"]["order_status_after"] == "PENDING_APPROVAL"
    assert results["diet_breach"]["http_status"] == 403
    assert results["diet_breach"]["error_code"] == "POLICY_BLOCKED"

    gw_res = results["gateway_timeout_recovery"]
    assert gw_res["order_status_before_reconcile"] == "SUBMISSION_UNKNOWN"
    assert gw_res["order_status_after"] == "PLACED"
    assert str(gw_res["gateway_reference"]).startswith("aimo_sim_")

    partial_res = results["partial_and_cutoff"]
    assert len(partial_res["substitutes_preview"]) >= 1  # type: ignore[arg-type]

    tamper_res = results["tampered_approval_hash"]
    assert tamper_res["http_status"] == 409
    assert tamper_res["error_code"] == "APPROVAL_CONTENT_CHANGED"
    assert tamper_res["order_status_after"] == "CANCELLED"

    # Verify tampered_approval_hash probe order is CANCELLED in DB & single gateway submission row
    with session_factory() as db:
        tamper_order = db.get(Order, str(tamper_res["order_id"]))
        assert tamper_order is not None and tamper_order.status == "CANCELLED"
        gw_count = db.scalar(
            select(func.count())
            .select_from(GatewaySubmission)
            .where(GatewaySubmission.order_id == str(gw_res["order_id"]))
        )
        assert gw_count == 1

    # 4. Repeated clicks on unit_trap (3x) never quarantine Flight Simulator Agent
    # and never touch real Hotel Aurora Kitchen Agent's spent_today_eur
    for _ in range(3):
        rep = client.post(
            "/api/dev/simulator/run",
            headers=user_headers,
            json={"scenario_id": "unit_trap"},
        )
        assert rep.status_code == 200
        assert rep.json()["passed"] is True

    kitchen_detail = client.get(f"/api/agents/{kitchen_agent_id}", headers=user_headers)
    assert kitchen_detail.status_code == 200
    assert kitchen_detail.json()["spent_today_eur"] == 0.0


def test_pre_flight_cart_optimizer_swaps_plant_guardrail_gluten_and_mcp(
    batch_b_env: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify <=50 ID cap, >=2 fixture swap pairs, plant toggle + G check, CO2 honesty & MCP."""
    client: TestClient = batch_b_env["client"]  # type: ignore[assignment]
    session_factory: sessionmaker[Session] = batch_b_env["session_factory"]  # type: ignore[assignment]
    agent_headers: dict[str, str] = batch_b_env["agent_headers"]  # type: ignore[assignment]

    # Seed a non-gluten-free regular oat drink (contains oats GO/AW) priced in fixture (471142)
    with session_factory() as db:
        non_gf_oat = db.get(Product, "471142") or Product(
            id="471142",
            name="Regular Kaurajuoma 1l (ei gluteeniton)",
            names={"fi": "Regular Kaurajuoma 1l (ei gluteeniton)"},
            category="1040200010",
            sales_unit="ST",
            base_unit="L",
            brand="GenericOat",
            vendor="Generic",
            units=[{"unit_id": "ST", "size_in_base_units": 1.0}],
            allergens=["GO", "AW"],
            non_allergens=["AM", "ML"],
            labels=["Vegaaninen", "Maidoton", "Laktoositon"],
            allergen_states={"AM": "ABSENT", "ML": "ABSENT", "AW": "PRESENT", "GO": "PRESENT"},
            claims=["FREE_FROM_LACTOSE", "VEGAN"],
            co2_kg_per_kg=0.20,
            delivery_reliability=1.0,
            temperature_condition=8,
            temperature_class="ambient",
        )
        non_gf_oat.category = "1040200010"
        non_gf_oat.name = "Regular Kaurajuoma 1l (ei gluteeniton)"
        non_gf_oat.sales_unit = "ST"
        non_gf_oat.base_unit = "L"
        non_gf_oat.allergens = ["GO", "AW"]
        non_gf_oat.allergen_states = {
            "AM": "ABSENT",
            "ML": "ABSENT",
            "AW": "PRESENT",
            "GO": "PRESENT",
        }
        non_gf_oat.claims = ["FREE_FROM_LACTOSE", "VEGAN"]
        non_gf_oat.co2_kg_per_kg = 0.20
        non_gf_oat.delivery_reliability = 1.0
        db.add(non_gf_oat)
        db.commit()

    # Spy on check_products_live to prove <= 50 IDs per call
    observed_counts: list[int] = []
    real_check = check_products_live

    def _spy_check_products_live(*args: object, **kwargs: object) -> list[object]:
        pids = kwargs.get("product_ids")
        if isinstance(pids, list):
            observed_counts.append(len(pids))
            assert len(pids) <= 50
        return real_check(*args, **kwargs)  # type: ignore[arg-type,return-value]

    monkeypatch.setattr(optimizer_module, "check_products_live", _spy_check_products_live)

    # 1. Two demonstrable swap pairs in fixture mode:
    #    - Line 0: 469363 x 200 ST (€2.48/ST) -> 464306 x 200 ST (€1.01/ST)
    #    - Line 1: 463038 x 2 KI (2 x 20 L = 40 L novobox crate, LATE) -> 464306 x 40 ST (ON_TIME)
    opt_resp = client.post(
        "/api/quotes/optimize",
        headers=agent_headers,
        json={
            "requested_delivery_date": "2026-11-16",
            "lines": [
                {"product_id": "469363", "quantity": 200},
                {"product_id": "463038", "quantity": 2},
            ],
            "diet": "L",
            "objective": "BALANCED",
            "allow_plant_based_swaps": False,
        },
    )
    assert opt_resp.status_code == 200, opt_resp.text
    opt_data = opt_resp.json()
    assert len(opt_data["swaps"]) == 2
    assert opt_data["deltas"]["total_excl_vat_delta_eur"] < -300.0
    # Both before & after are 1.1 kg CO2/kg dairy -> honest 0.0 kg delta!
    assert opt_data["deltas"]["comparable_co2_lines"] == 2
    assert opt_data["deltas"]["comparable_co2_delta_kg"] == 0.0
    swap_1 = next(s for s in opt_data["swaps"] if s["line_index"] == 1)
    assert swap_1["recommended_product"]["id"] == "464306"
    assert swap_1["suggested_quantity"] == 40.0
    assert any("Pack equivalence: 40 ST replaces 2 KI" in t for t in swap_1["tradeoffs"])
    assert any("identical product-group average" in t for t in swap_1["tradeoffs"])
    assert opt_data["before_summary"]["policy_preview"]["outcome"] == "APPROVAL_REQUIRED"
    assert opt_data["after_summary"]["policy_preview"]["outcome"] == "AUTO"

    # 2. allow_plant_based_swaps=False (default) NEVER swaps dairy milk 464306 to oat drink
    no_plant = client.post(
        "/api/quotes/optimize",
        headers=agent_headers,
        json={
            "requested_delivery_date": "2026-11-16",
            "lines": [{"product_id": "464306", "quantity": 50}],
            "diet": "L,G",
            "objective": "LOW_CARBON",
            "allow_plant_based_swaps": False,
        },
    )
    assert no_plant.status_code == 200
    assert no_plant.json()["swaps"] == []

    # 3. allow_plant_based_swaps=True + diet="L,G" strictly rejects non-GF oat 471142 (0.20 CO2)
    #    and selects ONLY certified gluten-free 468465 Oddlygood Dreamy Oat 1l (0.42 CO2)!
    with_plant_gf = client.post(
        "/api/quotes/optimize",
        headers=agent_headers,
        json={
            "requested_delivery_date": "2026-11-16",
            "lines": [{"product_id": "464306", "quantity": 50}],
            "diet": "L,G",
            "objective": "LOW_CARBON",
            "allow_plant_based_swaps": True,
        },
    )
    assert with_plant_gf.status_code == 200
    gf_swaps = with_plant_gf.json()["swaps"]
    assert len(gf_swaps) == 1
    assert gf_swaps[0]["recommended_product"]["id"] == "468465"
    assert gf_swaps[0]["co2_delta_kg"] < -20.0
    assert with_plant_gf.json()["deltas"]["comparable_co2_delta_kg"] < -20.0

    # 4. MCP tool `optimize_quote` works end-to-end and appears in GET /api/governance/summary
    mcp_call = client.post(
        "/api/mcp",
        headers=agent_headers,
        json={
            "jsonrpc": "2.0",
            "id": 99,
            "method": "tools/call",
            "params": {
                "name": "optimize_quote",
                "arguments": {
                    "requested_delivery_date": "2026-11-16",
                    "lines": [{"product_id": "469363", "quantity": 50}],
                    "objective": "SAVE_MONEY",
                },
            },
        },
    )
    assert mcp_call.status_code == 200
    mcp_res = mcp_call.json()["result"]
    assert mcp_res["isError"] is False
    mcp_opt = json.loads(mcp_res["content"][0]["text"])
    assert len(mcp_opt["swaps"]) == 1
    assert mcp_opt["swaps"][0]["recommended_product"]["id"] == "464306"

    gov = client.get("/api/governance/summary", headers=agent_headers)
    assert gov.status_code == 200
    assert "optimize_quote" in {t["name"] for t in gov.json()["mcp_tools"]}
    assert observed_counts and all(c <= 50 for c in observed_counts)
