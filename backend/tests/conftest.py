"""Pytest configuration for offline public repository execution."""

from __future__ import annotations

from pathlib import Path

import pytest

CATALOG_SKIP_REASON = (
    "Catalog fixture JSON omitted from public repo; supply catalog JSON per backend/README.md"
)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Auto-skip test modules that depend on omitted recorded catalog fixture JSON files."""
    del config
    skip_marker = pytest.mark.skip(reason=CATALOG_SKIP_REASON)
    for item in items:
        module = getattr(item, "module", None)
        if module is None:
            continue
        for attr_name in ("FIXTURE_PRODUCTS_PATH", "SAMPLE_JSON"):
            fixture_path = getattr(module, attr_name, None)
            if fixture_path is not None and not Path(fixture_path).is_file():
                item.add_marker(skip_marker)
                break
