"""`X-API-KEY` enforcement tests."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app, get_linkedin_client

API_KEY = "test-key-4f3a9c2e8b1d"
VALID_URL = "https://www.linkedin.com/in/jane-doe-example/"


class StubClient:
    def __init__(self, raw: dict[str, Any]) -> None:
        self.raw = raw
        self.calls: list[str] = []

    def fetch_raw(self, public_id: str) -> dict[str, Any]:
        self.calls.append(public_id)
        return self.raw


@pytest.fixture
def api(sample_raw: dict[str, Any]):
    client = TestClient(app, raise_server_exceptions=False)
    stub = StubClient(sample_raw)
    app.dependency_overrides[get_linkedin_client] = lambda: stub
    client.stub = stub  # type: ignore[attr-defined]
    yield client
    app.dependency_overrides.clear()


# =============================================================================
# auth disabled (local dev)
# =============================================================================


def test_no_api_key_configured_allows_requests(api: TestClient) -> None:
    assert api.get("/profile", params={"url": VALID_URL}).status_code == 200


def test_blank_api_key_is_treated_as_disabled(api: TestClient, set_env) -> None:
    set_env(API_KEY="   ")
    assert api.get("/profile", params={"url": VALID_URL}).status_code == 200


# =============================================================================
# auth enabled
# =============================================================================


def test_correct_api_key_allows_request(api: TestClient, set_env) -> None:
    set_env(API_KEY=API_KEY)
    response = api.get(
        "/profile", params={"url": VALID_URL}, headers={"X-API-KEY": API_KEY}
    )
    assert response.status_code == 200


def test_missing_api_key_401(api: TestClient, set_env) -> None:
    set_env(API_KEY=API_KEY)
    response = api.get("/profile", params={"url": VALID_URL})
    assert response.status_code == 401
    assert "X-API-KEY" in response.json()["detail"]


@pytest.mark.parametrize(
    "supplied",
    ["wrong-key", "", " ", API_KEY + "x", API_KEY[:-1], API_KEY.upper()],
)
def test_wrong_api_key_401(api: TestClient, set_env, supplied: str) -> None:
    set_env(API_KEY=API_KEY)
    response = api.get(
        "/profile", params={"url": VALID_URL}, headers={"X-API-KEY": supplied}
    )
    assert response.status_code == 401


def test_header_name_is_case_insensitive(api: TestClient, set_env) -> None:
    set_env(API_KEY=API_KEY)
    response = api.get(
        "/profile", params={"url": VALID_URL}, headers={"x-api-key": API_KEY}
    )
    assert response.status_code == 200


def test_unauthorized_request_never_reaches_linkedin(api: TestClient, set_env) -> None:
    set_env(API_KEY=API_KEY)
    api.get("/profile", params={"url": VALID_URL})
    assert api.stub.calls == []  # type: ignore[attr-defined]


def test_401_body_does_not_echo_the_expected_or_supplied_key(
    api: TestClient, set_env
) -> None:
    set_env(API_KEY=API_KEY)
    response = api.get(
        "/profile", params={"url": VALID_URL}, headers={"X-API-KEY": "guess-attempt"}
    )
    assert API_KEY not in response.text
    assert "guess-attempt" not in response.text


# =============================================================================
# probes stay open so a deploy can be health-checked without the key
# =============================================================================


@pytest.mark.parametrize("path", ["/health", "/readiness", "/docs", "/openapi.json"])
def test_meta_endpoints_do_not_require_the_api_key(
    api: TestClient, set_env, path: str
) -> None:
    set_env(API_KEY=API_KEY)
    assert api.get(path).status_code == 200


def test_api_key_scheme_is_documented_in_openapi(api: TestClient, set_env) -> None:
    set_env(API_KEY=API_KEY)
    schema = api.get("/openapi.json").json()
    schemes = schema["components"]["securitySchemes"]
    assert any(
        scheme.get("in") == "header" and scheme.get("name") == "X-API-KEY"
        for scheme in schemes.values()
    )
