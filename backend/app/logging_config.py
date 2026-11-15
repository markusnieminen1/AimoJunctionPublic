import json
import logging
import sys
from pathlib import Path

from app import clock
from app.errors import request_id_var

_BACKEND_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = _BACKEND_DIR / "logs"
LOG_FILE = LOG_DIR / "backend.log"


class JsonFormatter(logging.Formatter):
    """Format log records as single-line JSON with request_id."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": clock.now_iso(),
            "level": record.levelname,
            "logger": record.name,
            "request_id": request_id_var.get() or None,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["traceback"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: int = logging.INFO) -> None:
    """Configure structured JSON logging to stdout and logs/backend.log."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    formatter = JsonFormatter()

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Clear existing handlers we previously added so reconfiguration is idempotent.
    root_logger.handlers.clear()

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)

    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(formatter)

    root_logger.addHandler(stream_handler)
    root_logger.addHandler(file_handler)

    # Route uvicorn loggers through root JSON handlers without duplicating.
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv_logger = logging.getLogger(logger_name)
        uv_logger.handlers.clear()
        uv_logger.propagate = True
