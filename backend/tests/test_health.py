import json
from datetime import datetime

from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.config import Settings
from app.errors import AppError
from app.logging_config import LOG_FILE
from app.main import app, create_app


def test_health_returns_expected_fields_and_headers(tmp_path, monkeypatch) -> None:
    db_file = tmp_path / "health_test.db"
    monkeypatch.setattr("app.routers.health.settings.database_url", f"sqlite:///{db_file}")
    monkeypatch.setattr("app.routers.health.settings.aimo_mode", "fixture")

    client = TestClient(app)
    resp = client.get("/api/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"
    assert body["aimo"] == "ok"
    assert body["aimo_mode"] == "fixture"
    assert body["version"] == "0.1.0"
    assert datetime.fromisoformat(body["server_time"]).tzinfo is not None

    req_id = resp.headers.get("X-Request-ID")
    server_time = resp.headers.get("X-Server-Time")
    assert req_id is not None and req_id.startswith("req_")
    assert server_time is not None
    assert datetime.fromisoformat(server_time).tzinfo is not None


def test_incoming_request_id_is_echoed_back() -> None:
    client = TestClient(app)
    resp = client.get("/api/health", headers={"X-Request-ID": "req_custom_trace_123"})

    assert resp.status_code == 200
    assert resp.headers.get("X-Request-ID") == "req_custom_trace_123"


def test_app_now_override_reflects_in_health_and_headers(monkeypatch) -> None:
    # 12:00 UTC in winter (Nov) is 14:00+02:00 in Europe/Helsinki
    monkeypatch.setenv("APP_NOW", "2026-11-15T12:00:00+00:00")

    client = TestClient(app)
    resp = client.get("/api/health")

    assert resp.status_code == 200
    assert resp.json()["server_time"] == "2026-11-15T14:00:00+02:00"
    assert resp.headers.get("X-Server-Time") == "2026-11-15T14:00:00+02:00"


def test_error_envelopes_and_headers_for_all_error_types(monkeypatch) -> None:
    monkeypatch.setenv("APP_NOW", "2026-11-15T14:00:00+02:00")

    test_app = create_app()

    class _ItemBody(BaseModel):
        quantity: int

    @test_app.get("/api/_test/app-error")
    def _raise_app_error() -> None:
        raise AppError(
            code="QUOTE_EXPIRED",
            status_code=409,
            message="Quote has expired",
            details={"quote_id": "qte_01TEST"},
        )

    @test_app.post("/api/_test/validate")
    def _validate(body: _ItemBody) -> dict[str, int]:
        return {"quantity": body.quantity}

    @test_app.get("/api/_test/http-error")
    def _raise_http_error() -> None:
        raise HTTPException(status_code=404, detail="Item missing")

    @test_app.get("/api/_test/boom")
    def _raise_unhandled() -> None:
        raise RuntimeError("unexpected failure")

    client = TestClient(test_app, raise_server_exceptions=False)

    # 1. AppError
    r_app = client.get("/api/_test/app-error", headers={"X-Request-ID": "req_app_err_1"})
    assert r_app.status_code == 409
    assert r_app.headers.get("X-Request-ID") == "req_app_err_1"
    assert r_app.headers.get("X-Server-Time") == "2026-11-15T14:00:00+02:00"
    assert r_app.json() == {
        "error": {
            "code": "QUOTE_EXPIRED",
            "message": "Quote has expired",
            "details": {"quote_id": "qte_01TEST"},
        },
        "request_id": "req_app_err_1",
    }

    # 2. RequestValidationError -> 422 VALIDATION_ERROR
    r_val = client.post(
        "/api/_test/validate",
        json={"quantity": "not-an-int"},
        headers={"X-Request-ID": "req_val_err_2"},
    )
    assert r_val.status_code == 422
    assert r_val.headers.get("X-Request-ID") == "req_val_err_2"
    assert r_val.headers.get("X-Server-Time") == "2026-11-15T14:00:00+02:00"
    val_body = r_val.json()
    assert val_body["request_id"] == "req_val_err_2"
    assert val_body["error"]["code"] == "VALIDATION_ERROR"
    assert isinstance(val_body["error"]["details"]["errors"], list)

    # 3. HTTPException -> mapped contract code
    r_http = client.get("/api/_test/http-error")
    assert r_http.status_code == 404
    http_req_id = r_http.headers.get("X-Request-ID")
    assert http_req_id and http_req_id.startswith("req_")
    assert r_http.headers.get("X-Server-Time") == "2026-11-15T14:00:00+02:00"
    assert r_http.json() == {
        "error": {
            "code": "NOT_FOUND",
            "message": "Item missing",
            "details": {},
        },
        "request_id": http_req_id,
    }

    # 4. Unhandled Exception -> 500 INTERNAL + JSON log with traceback & request_id
    r_boom = client.get("/api/_test/boom", headers={"X-Request-ID": "req_boom_500"})
    assert r_boom.status_code == 500
    assert r_boom.headers.get("X-Request-ID") == "req_boom_500"
    assert r_boom.headers.get("X-Server-Time") == "2026-11-15T14:00:00+02:00"
    assert r_boom.json() == {
        "error": {
            "code": "INTERNAL",
            "message": "Internal server error",
            "details": {},
        },
        "request_id": "req_boom_500",
    }

    log_lines = LOG_FILE.read_text(encoding="utf-8").strip().splitlines()
    matching = [
        json.loads(line)
        for line in log_lines
        if json.loads(line).get("request_id") == "req_boom_500"
    ]
    assert any(
        entry["level"] == "ERROR"
        and "RuntimeError: unexpected failure" in entry.get("traceback", "")
        for entry in matching
    )


def test_openapi_and_docs_under_api_prefix() -> None:
    client = TestClient(app)
    assert client.get("/api/openapi.json").status_code == 200
    assert client.get("/api/docs").status_code == 200
    assert client.get("/redoc").status_code == 404
    assert client.get("/docs").status_code == 404


def test_settings_aliases_for_lowercase_env_keys(monkeypatch) -> None:
    monkeypatch.setenv("database_url", "sqlite:///./custom.db")
    monkeypatch.setenv("jwt_secret", "test-secret")
    monkeypatch.setenv("AimoAPI_key", "aimo-key")
    monkeypatch.setenv("anthropic_api_key", "ant-key")
    monkeypatch.setenv("featherless_api_key", "fl-key")

    loaded = Settings(_env_file=None)
    assert loaded.database_url == "sqlite:///./custom.db"
    assert loaded.jwt_secret == "test-secret"
    assert loaded.aimo_api_key == "aimo-key"
    assert loaded.anthropic_api_key == "ant-key"
    assert loaded.featherless_api_key == "fl-key"
