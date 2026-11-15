"""Standalone Agentic Capabilities Service package (zero direct DB / ORM access)."""

from app.agentic_service.app import (
    INTERNAL_LOOP_HEADER,
    agentic_app,
    create_agentic_app,
    set_core_asgi_app,
)

__all__ = [
    "INTERNAL_LOOP_HEADER",
    "agentic_app",
    "create_agentic_app",
    "set_core_asgi_app",
]
