"""Shared test fixtures.

The whole offline suite runs with **zero network access and zero LinkedIn requests**.
The autouse env fixture below is what guarantees that: it hides any local `.env` and
unsets every credential, so a developer who has real cookies on disk still gets the
same deterministic results as CI.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"

_CREDENTIAL_VARS = (
    "LI_AT",
    "LI_JSESSIONID",
    "PROXY_URL",
    "API_KEY",
    "DASH_DECORATION_ID",
    "IMPERSONATE",
    "CACHE_TTL_SECONDS",
)


@pytest.fixture(autouse=True)
def isolated_env(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    """Isolate every non-live test from the developer's real environment."""
    if request.node.get_closest_marker("live"):
        yield  # live tests need the real `.env`
        return

    from app.config import DISABLE_DOTENV_ENV_VAR, reset_settings_cache

    monkeypatch.setenv(DISABLE_DOTENV_ENV_VAR, "1")
    for var in _CREDENTIAL_VARS:
        monkeypatch.delenv(var, raising=False)
    reset_settings_cache()
    yield
    reset_settings_cache()


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


@pytest.fixture
def sample_raw() -> dict[str, Any]:
    """Normalized `profileView` payload: `data` + flat `included[]`, fs_* URNs."""
    return load_fixture("sample_profile.json")


@pytest.fixture
def nested_raw() -> dict[str, Any]:
    """Legacy non-normalized `profileView`: inline entities under `*View.elements`."""
    return load_fixture("sample_profile_nested.json")


@pytest.fixture
def dash_raw() -> dict[str, Any]:
    """Modern `dash/profiles` payload: fsd_* URNs and `dateRange{start,end}`."""
    return load_fixture("sample_profile_dash.json")
