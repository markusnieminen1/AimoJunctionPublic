"""HTTP client for Valio Aimo API with retries, chunking, TTL cache, and fixture mode."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import httpx

from app import clock
from app.config import settings
from app.errors import AppError, request_id_var

logger = logging.getLogger(__name__)

MAX_IDS_PER_CALL = 1000
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_RETRY_DELAYS: tuple[float, ...] = (0.5, 1.0, 2.0)
DEFAULT_CACHE_TTL_SECONDS = 60.0
DEFAULT_FIXTURE_DIR = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "aimo"


def _validate_requested_date(requested_date: str) -> str:
    if not isinstance(requested_date, str) or len(requested_date) != 10:
        raise AppError(
            "VALIDATION_ERROR",
            422,
            "requested_delivery_date must be formatted as YYYY-MM-DD",
        )
    try:
        date.fromisoformat(requested_date)
    except ValueError as exc:
        raise AppError(
            "VALIDATION_ERROR",
            422,
            "requested_delivery_date must be a valid YYYY-MM-DD date",
        ) from exc
    return requested_date


def _validate_product_ids(product_ids: Sequence[str]) -> list[str]:
    if not isinstance(product_ids, (list, tuple)):
        raise AppError("VALIDATION_ERROR", 422, "product_ids must be a list of strings")
    if not product_ids:
        raise AppError("VALIDATION_ERROR", 422, "product_ids must not be empty")
    cleaned: list[str] = []
    for pid in product_ids:
        if not isinstance(pid, str) or not pid.strip():
            raise AppError(
                "VALIDATION_ERROR",
                422,
                "All product_ids must be non-empty strings",
            )
        cleaned.append(pid)
    return list(dict.fromkeys(cleaned))


class AimoClient:
    """Valio Aimo API client supporting live httpx calls and local fixture mode."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        mode: Literal["live", "fixture"] | None = None,
        fixture_dir: Path | str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        retry_delays: tuple[float, ...] = DEFAULT_RETRY_DELAYS,
        cache_ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
        transport: httpx.BaseTransport | None = None,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._mode = mode
        self._fixture_dir = Path(fixture_dir) if fixture_dir is not None else DEFAULT_FIXTURE_DIR
        self.timeout = timeout
        self.retry_delays = retry_delays
        self.cache_ttl_seconds = cache_ttl_seconds
        self._transport = transport
        self._sleep = sleep_fn or time.sleep
        self._cache: dict[tuple[str, tuple[str, ...], str], tuple[float, dict[str, Any]]] = {}

    @property
    def base_url(self) -> str:
        raw = self._base_url if self._base_url is not None else settings.aimo_base_url
        return raw.rstrip("/")

    @property
    def api_key(self) -> str:
        return self._api_key if self._api_key is not None else settings.aimo_api_key

    @property
    def mode(self) -> Literal["live", "fixture"]:
        return self._mode if self._mode is not None else settings.aimo_mode

    @property
    def fixture_dir(self) -> Path:
        return self._fixture_dir

    def clear_cache(self) -> None:
        """Clear the in-memory response cache."""
        self._cache.clear()

    def _log_call(
        self,
        *,
        endpoint: str,
        count: int,
        latency_ms: float,
        status: int | str,
    ) -> None:
        req_id = request_id_var.get() or "-"
        logger.info(
            "aimo_upstream request_id=%s endpoint=%s count=%d latency_ms=%.1f status=%s",
            req_id,
            endpoint,
            count,
            latency_ms,
            status,
        )

    def _get_cached(self, cache_key: tuple[str, tuple[str, ...], str]) -> dict[str, Any] | None:
        entry = self._cache.get(cache_key)
        if entry is None:
            return None
        expires_at, payload = entry
        if time.monotonic() >= expires_at:
            self._cache.pop(cache_key, None)
            return None
        return payload

    def _set_cached(
        self, cache_key: tuple[str, tuple[str, ...], str], payload: dict[str, Any]
    ) -> None:
        if self.cache_ttl_seconds <= 0:
            return
        self._cache[cache_key] = (time.monotonic() + self.cache_ttl_seconds, payload)

    def _make_httpx_client(self) -> httpx.Client:
        if self._transport is not None:
            return httpx.Client(timeout=self.timeout, transport=self._transport)
        return httpx.Client(timeout=self.timeout)

    def ping(self) -> bool:
        """Check upstream availability (fixture/synthetic in fixture mode, /api/ping in live)."""
        if self.mode == "fixture":
            has_files = (self.fixture_dir / "price.json").is_file() and (
                self.fixture_dir / "availability.json"
            ).is_file()
            self._log_call(
                endpoint="/api/ping",
                count=0,
                latency_ms=0.0,
                status="fixture_ok" if has_files else "fixture_synthetic",
            )
            return True

        url = f"{self.base_url}/api/ping"
        t0 = time.perf_counter()
        try:
            with self._make_httpx_client() as client:
                resp = client.get(url, headers={"X-API-Key": self.api_key})
            latency_ms = (time.perf_counter() - t0) * 1000.0
            self._log_call(
                endpoint="/api/ping",
                count=0,
                latency_ms=latency_ms,
                status=resp.status_code,
            )
            if resp.status_code != 200:
                return False
            data = resp.json()
            return isinstance(data, dict) and data.get("status") == "ok"
        except (httpx.HTTPError, ValueError) as exc:
            latency_ms = (time.perf_counter() - t0) * 1000.0
            self._log_call(
                endpoint="/api/ping",
                count=0,
                latency_ms=latency_ms,
                status=type(exc).__name__,
            )
            return False

    def _synthesize_fallback_fixture_response(
        self,
        endpoint: Literal["/api/price", "/api/availability"],
        unique_ids: list[str],
        requested_date: str,
        t0: float,
    ) -> dict[str, Any]:
        if endpoint == "/api/price":
            products = [
                {
                    "productId": pid,
                    "quantity": 1.0,
                    "netPriceFound": True,
                    "error": False,
                    "catalogPrice": {"value": 2.50, "valueWithTax": 2.84},
                    "listPrice": {"value": 2.50, "valueWithTax": 2.84},
                    "tax": {"taxRate": 13.5},
                }
                for pid in unique_ids
            ]
            result: dict[str, Any] = {
                "customerId": "123",
                "currency": "EUR",
                "pricingDate": f"{requested_date}T00:00:00+0200",
                "syntheticFallback": True,
                "products": products,
            }
        else:
            now_dt = clock.now()
            req_d = date.fromisoformat(requested_date)
            req_anchor = datetime(
                req_d.year,
                req_d.month,
                req_d.day,
                now_dt.hour,
                now_dt.minute,
                now_dt.second,
                tzinfo=now_dt.tzinfo,
            )
            anchor_dt = req_anchor if req_anchor > now_dt else now_dt
            closing_time_iso = clock.format_helsinki_iso(anchor_dt + timedelta(days=2))
            products = [
                {
                    "productId": pid,
                    "logisticModel": "Stock",
                    "deliveryModel": "Stock",
                    "closingTime": closing_time_iso,
                    "salesBlock": False,
                    "scheduleLines": [
                        {
                            "availableDate": requested_date,
                            "availableAmount": 500.0,
                            "salesBlock": False,
                        }
                    ],
                    "deliverySchedules": [
                        {
                            "deliveryModel": "Stock",
                            "availableDate": requested_date,
                            "availableQuantity": 500.0,
                            "closingTime": closing_time_iso,
                        }
                    ],
                }
                for pid in unique_ids
            ]
            result = {
                "customerId": "123",
                "requestedDate": f"{requested_date}T00:00:00+0200",
                "syntheticFallback": True,
                "products": products,
            }

        latency_ms = (time.perf_counter() - t0) * 1000.0
        self._log_call(
            endpoint=endpoint,
            count=len(unique_ids),
            latency_ms=latency_ms,
            status="fixture_synthetic",
        )
        return result

    def _load_fixture_response(
        self,
        endpoint: Literal["/api/price", "/api/availability"],
        unique_ids: list[str],
        requested_date: str,
    ) -> dict[str, Any]:
        t0 = time.perf_counter()
        filename = "price.json" if endpoint == "/api/price" else "availability.json"
        path = self.fixture_dir / filename
        if not path.is_file():
            return self._synthesize_fallback_fixture_response(
                endpoint, unique_ids, requested_date, t0
            )

        raw = json.loads(path.read_text(encoding="utf-8"))
        by_id = {
            str(item["productId"]): item
            for item in (raw.get("products") or [])
            if isinstance(item, dict) and "productId" in item
        }

        products: list[dict[str, Any]] = []
        for pid in unique_ids:
            if pid in by_id:
                products.append(dict(by_id[pid]))
            elif endpoint == "/api/price":
                products.append(
                    {
                        "productId": pid,
                        "quantity": 1.0,
                        "netPriceFound": False,
                        "error": True,
                    }
                )
            else:
                products.append(
                    {
                        "productId": pid,
                        "salesBlock": False,
                        "scheduleLines": [],
                    }
                )

        result: dict[str, Any] = {k: v for k, v in raw.items() if k != "products"}
        result["products"] = products
        if endpoint == "/api/availability":
            result.setdefault("requestedDate", f"{requested_date}T00:00:00+0200")
        elif endpoint == "/api/price":
            result.setdefault("pricingDate", f"{requested_date}T00:00:00+0200")

        latency_ms = (time.perf_counter() - t0) * 1000.0
        self._log_call(
            endpoint=endpoint,
            count=len(unique_ids),
            latency_ms=latency_ms,
            status="fixture",
        )
        return result

    def _post_with_retries(
        self,
        endpoint: str,
        payload: dict[str, Any],
        *,
        count: int,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{endpoint}"
        headers = {"X-API-Key": self.api_key}
        max_attempts = len(self.retry_delays) + 1

        with self._make_httpx_client() as client:
            for attempt in range(max_attempts):
                t0 = time.perf_counter()
                try:
                    resp = client.post(url, json=payload, headers=headers)
                    latency_ms = (time.perf_counter() - t0) * 1000.0
                    self._log_call(
                        endpoint=endpoint,
                        count=count,
                        latency_ms=latency_ms,
                        status=resp.status_code,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        if not isinstance(data, dict):
                            raise AppError(
                                "UPSTREAM_UNAVAILABLE",
                                503,
                                "Valio Aimo upstream API unavailable",
                            )
                        return data

                    retryable = resp.status_code == 429 or 500 <= resp.status_code < 600
                    if retryable and attempt < len(self.retry_delays):
                        self._sleep(self.retry_delays[attempt])
                        continue
                    raise AppError(
                        "UPSTREAM_UNAVAILABLE",
                        503,
                        "Valio Aimo upstream API unavailable",
                        details={"upstream_status": resp.status_code},
                    )
                except (httpx.TimeoutException, httpx.RequestError) as exc:
                    latency_ms = (time.perf_counter() - t0) * 1000.0
                    self._log_call(
                        endpoint=endpoint,
                        count=count,
                        latency_ms=latency_ms,
                        status=type(exc).__name__,
                    )
                    if attempt < len(self.retry_delays):
                        self._sleep(self.retry_delays[attempt])
                        continue
                    raise AppError(
                        "UPSTREAM_UNAVAILABLE",
                        503,
                        "Valio Aimo upstream API unavailable",
                    ) from exc

        raise AppError("UPSTREAM_UNAVAILABLE", 503, "Valio Aimo upstream API unavailable")

    def _call_endpoint(
        self,
        endpoint: Literal["/api/price", "/api/availability"],
        product_ids: list[str],
        requested_date: str,
        customer: str,
    ) -> dict[str, Any]:
        _validate_requested_date(requested_date)
        unique_ids = _validate_product_ids(product_ids)
        cache_key = (endpoint, tuple(sorted(unique_ids)), requested_date)

        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        if self.mode == "fixture":
            result = self._load_fixture_response(endpoint, unique_ids, requested_date)
            self._set_cached(cache_key, result)
            return result

        merged: dict[str, Any] | None = None
        merged_products: list[dict[str, Any]] = []
        for idx in range(0, len(unique_ids), MAX_IDS_PER_CALL):
            chunk = unique_ids[idx : idx + MAX_IDS_PER_CALL]
            payload = {
                "customer": str(customer),
                "products": chunk,
                "requestedDate": requested_date,
            }
            chunk_resp = self._post_with_retries(endpoint, payload, count=len(chunk))
            if merged is None:
                merged = {k: v for k, v in chunk_resp.items() if k != "products"}
            merged_products.extend(chunk_resp.get("products") or [])

        final_resp = merged or {}
        final_resp["products"] = merged_products
        self._set_cached(cache_key, final_resp)
        return final_resp

    def price(
        self,
        product_ids: list[str],
        requested_date: str,
        customer: str = "123",
    ) -> dict[str, Any]:
        """Fetch customer pricing from Valio Aimo (or recorded fixtures)."""
        return self._call_endpoint("/api/price", product_ids, requested_date, customer)

    def availability(
        self,
        product_ids: list[str],
        requested_date: str,
        customer: str = "123",
    ) -> dict[str, Any]:
        """Fetch delivery availability from Valio Aimo (or recorded fixtures)."""
        return self._call_endpoint("/api/availability", product_ids, requested_date, customer)


aimo_client = AimoClient()


def get_aimo_client() -> AimoClient:
    """FastAPI dependency returning the shared AimoClient instance."""
    return aimo_client
