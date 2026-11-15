"""Simulated Valio Aimo order gateway adapter with idempotent submission ledger."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from sqlalchemy.orm import Session

from app import clock
from app.config import settings
from app.models import GatewaySubmission

GatewaySimulateMode = Literal["none", "timeout_after_accept", "error"]
_VALID_SIMULATE_MODES: tuple[GatewaySimulateMode, ...] = ("none", "timeout_after_accept", "error")

_simulate_mode_override: GatewaySimulateMode | None = None


@dataclass(frozen=True, slots=True)
class GatewayResult:
    """Outcome of submitting an order to the external/simulated order gateway."""

    status: Literal["ok", "error", "unknown"]
    reference: str | None = None
    message: str = ""


class OrderGateway(Protocol):
    """Protocol for order submission and lookup against an upstream order gateway."""

    def submit(self, db: Session, order_id: str, payload: dict[str, Any]) -> GatewayResult:
        """Submit an order idempotently keyed by order_id."""
        ...

    def lookup(self, db: Session, order_id: str) -> str | None:
        """Look up an existing gateway reference for order_id, or None if not found."""
        ...


def set_gateway_simulate_mode(mode: GatewaySimulateMode | None) -> GatewaySimulateMode:
    """Set or clear the runtime simulation mode override for MockAimoOrderGateway."""
    global _simulate_mode_override
    if mode is None:
        _simulate_mode_override = None
        return get_gateway_simulate_mode()
    if mode not in _VALID_SIMULATE_MODES:
        raise ValueError(f"Invalid gateway simulate mode: {mode!r}")
    _simulate_mode_override = mode
    return mode


def get_gateway_simulate_mode() -> GatewaySimulateMode:
    """Return the active simulation mode (runtime override or settings.order_gateway_simulate)."""
    if _simulate_mode_override is not None:
        return _simulate_mode_override
    return settings.order_gateway_simulate


class MockAimoOrderGateway:
    """Simulated Valio Aimo order gateway persisted in gateway_submissions."""

    def __init__(self, mode_override: GatewaySimulateMode | None = None) -> None:
        if mode_override is not None and mode_override not in _VALID_SIMULATE_MODES:
            raise ValueError(f"Invalid gateway simulate mode: {mode_override!r}")
        self._mode_override = mode_override

    def submit(self, db: Session, order_id: str, payload: dict[str, Any]) -> GatewayResult:
        """Persist submission keyed by order_id; strictly idempotent on resubmit."""
        existing = db.get(GatewaySubmission, order_id)
        if existing is not None:
            return GatewayResult(
                status="ok",
                reference=existing.gateway_reference,
                message="Confirmed existing submission on simulated Valio Aimo order gateway",
            )

        mode = (
            self._mode_override if self._mode_override is not None else get_gateway_simulate_mode()
        )
        if mode == "error":
            return GatewayResult(
                status="error",
                reference=None,
                message="Simulated gateway rejection",
            )

        reference = f"aimo_sim_{order_id[-12:]}"
        submission = GatewaySubmission(
            order_id=order_id,
            gateway_reference=reference,
            payload=payload,
            status="ACCEPTED",
            created_at=clock.now(),
        )
        db.add(submission)
        db.flush()

        if mode == "timeout_after_accept":
            return GatewayResult(
                status="unknown",
                reference=None,
                message="Simulated gateway timeout after accept (outcome unknown)",
            )

        return GatewayResult(
            status="ok",
            reference=reference,
            message=(
                "Accepted by simulated Valio Aimo order gateway (no real upstream order endpoint)"
            ),
        )

    def lookup(self, db: Session, order_id: str) -> str | None:
        """Return the persisted simulated gateway reference if accepted upstream, else None."""
        existing = db.get(GatewaySubmission, order_id)
        return existing.gateway_reference if existing is not None else None


def get_order_gateway() -> OrderGateway:
    """FastAPI dependency / default factory returning the simulated Valio Aimo order gateway."""
    return MockAimoOrderGateway()
