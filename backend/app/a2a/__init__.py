"""Layer 3 Agent-to-Agent (A2A) protocol service and FastAPI routers."""

from app.a2a.router import router, well_known_router
from app.a2a.service import build_agent_card, handle_rpc, list_a2a_tasks

__all__ = [
    "build_agent_card",
    "handle_rpc",
    "list_a2a_tasks",
    "router",
    "well_known_router",
]
