"""Delivery status and on-time probability scoring."""

from __future__ import annotations

from app.schemas import DeliveryStatus, LineFlag

DEFAULT_MISSING_RELIABILITY = 0.85


def compute_delivery_status(
    flags: list[LineFlag],
    logistic_model: str | None,
    reliability: float | None,
) -> tuple[DeliveryStatus, float | None]:
    """Compute (delivery_status, on_time_probability) and append LOW_RELIABILITY if prob < 0.9."""
    prob = float(reliability) if reliability is not None else DEFAULT_MISSING_RELIABILITY
    if prob < 0.9 and "LOW_RELIABILITY" not in flags:
        flags.append("LOW_RELIABILITY")

    if "NOT_DELIVERABLE" in flags or "SALES_BLOCKED" in flags:
        return "NOT_DELIVERABLE", 0.0

    if "CUTOFF_PASSED" in flags or "LATE" in flags:
        return "LATE", 0.0

    if (
        logistic_model == "Terminal"
        or "SUPPLIER_QTY_UNKNOWN" in flags
        or "PARTIAL" in flags
        or "UNIT_MISMATCH" in flags
        or reliability is None
        or reliability < 0.95
    ):
        return "AT_RISK", round(prob, 4)

    return "ON_TIME", round(prob, 4)
