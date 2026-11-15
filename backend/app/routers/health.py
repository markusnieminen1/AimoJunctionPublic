import logging
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

from app import clock, db
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"]
    db: Literal["ok", "down"]
    aimo: Literal["ok", "down"]
    aimo_mode: Literal["live", "fixture"]
    server_time: str
    version: str


def _check_db() -> Literal["ok", "down"]:
    if settings.database_url == db._engine_url:
        try:
            with db.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return "ok"
        except Exception as exc:
            logger.warning("Database health check failed: %s", exc)
            return "down"

    temp_engine = None
    try:
        temp_engine = db.make_engine(settings.database_url)
        with temp_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return "ok"
    except Exception as exc:
        logger.warning("Database health check failed: %s", exc)
        return "down"
    finally:
        if temp_engine is not None:
            temp_engine.dispose()


def _check_aimo() -> Literal["ok", "down"]:
    if settings.aimo_mode == "fixture":
        return "ok"
    return "ok" if settings.aimo_api_key else "down"


@router.get("/health", response_model=HealthResponse)
def health_check() -> HealthResponse:
    """Return service health, DB/Aimo status, and authoritative server time."""
    return HealthResponse(
        status="ok",
        db=_check_db(),
        aimo=_check_aimo(),
        aimo_mode=settings.aimo_mode,
        server_time=clock.now_iso(),
        version=settings.version,
    )
