"""Shared test fixtures.

The whole offline suite runs with **zero network access and zero LinkedIn requests**.
Two autouse fixtures guarantee that:

* `isolated_env` hides any local `.env` and unsets every credential, so a developer
  with real cookies on disk gets the same deterministic results as CI.
* `no_network` replaces the client's single transport seam with a tripwire, so a test
  that accidentally tries to reach LinkedIn fails loudly instead of firing a request.
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
    "MAX_RETRIES",
    "PROFILE_VIEW_ENABLED",
)


def _reset_singletons() -> None:
    from app.cache import reset_profile_cache
    from app.config import reset_settings_cache

    reset_settings_cache()
    reset_profile_cache()


@pytest.fixture(autouse=True)
def isolated_env(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    """Isolate every non-live test from the developer's real environment."""
    if request.node.get_closest_marker("live"):
        yield  # live tests need the real `.env`
        return

    from app.config import DISABLE_DOTENV_ENV_VAR

    monkeypatch.setenv(DISABLE_DOTENV_ENV_VAR, "1")
    for var in _CREDENTIAL_VARS:
        monkeypatch.delenv(var, raising=False)
    _reset_singletons()
    yield
    _reset_singletons()


@pytest.fixture(autouse=True)
def no_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Tripwire on the only code path that can reach the network."""
    if request.node.get_closest_marker("live"):
        return

    from app.linkedin import client as client_module

    def _blocked(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(
            "the offline test suite attempted a real HTTP request to LinkedIn"
        )

    monkeypatch.setattr(client_module, "_http_get", _blocked)


@pytest.fixture
def set_env(monkeypatch: pytest.MonkeyPatch):
    """Set env vars and re-read the cached settings/cache singletons."""

    def _set(**values: str) -> None:
        for key, value in values.items():
            monkeypatch.setenv(key, value)
        _reset_singletons()

    return _set


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
