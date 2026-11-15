import os
from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import settings

HELSINKI_TZ = ZoneInfo("Europe/Helsinki")


def to_helsinki(dt: datetime) -> datetime:
    """Normalize a naive (assumed Europe/Helsinki) or aware datetime to Europe/Helsinki."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=HELSINKI_TZ)
    return dt.astimezone(HELSINKI_TZ)


def format_helsinki_iso(dt: datetime) -> str:
    """Format a datetime as ISO 8601 with second precision in Europe/Helsinki."""
    return to_helsinki(dt).isoformat(timespec="seconds")


def now() -> datetime:
    """Return current time in Europe/Helsinki, honoring APP_NOW override if set."""
    raw = os.environ["APP_NOW"] if "APP_NOW" in os.environ else settings.app_now
    if raw and raw.strip():
        return to_helsinki(datetime.fromisoformat(raw.strip()))
    return datetime.now(HELSINKI_TZ)


def now_iso() -> str:
    """Return current time as ISO 8601 string with second precision."""
    return now().isoformat(timespec="seconds")
