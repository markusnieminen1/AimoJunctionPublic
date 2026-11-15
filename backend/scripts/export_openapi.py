"""Export the FastAPI OpenAPI 3.1 specification to backend/openapi.json."""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Allow running directly as `uv run python scripts/export_openapi.py`
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.main import app  # noqa: E402


def export_openapi(output_path: Path | None = None) -> Path:
    """Write app.openapi() JSON to output_path (defaults to backend/openapi.json)."""
    target = output_path or (_BACKEND_DIR / "openapi.json")
    schema = app.openapi()
    target.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return target


if __name__ == "__main__":
    written = export_openapi()
    print(f"Exported OpenAPI schema to {written}")
