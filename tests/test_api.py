"""End-to-end API tests with the LinkedIn client stubbed out. No network."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.linkedin.exceptions import (
    AuthExpired,
    CredentialsMissing,
    ProfileNotFound,
    ProxyRequired,
    RateLimited,
    UpstreamError,
    UpstreamTimeout,
)
from app.main import app, get_linkedin_client
from app.models import Profile

VALID_URL = "https://www.linkedin.com/in/jane-doe-example/"


class StubClient:
    """Stands in for `LinkedInClient`: returns a payload or raises, and counts calls."""

    def __init__(self, raw: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        self.raw = raw if raw is not None else {"included": []}
        self.error = error
        self.calls: list[str] = []

    def fetch_raw(self, public_id: str) -> dict[str, Any]:
        self.calls.append(public_id)
        if self.error is not None:
            raise self.error
        return self.raw


@pytest.fixture
def api():
    # raise_server_exceptions=False so the catch-all 500 handler is observable.
    client = TestClient(app, raise_server_exceptions=False)
    yield client
    app.dependency_overrides.clear()


@pytest.fixture
def stub_upstream(api: TestClient):
    """Install a StubClient as the API's LinkedIn client and return it."""

    def _install(raw: dict[str, Any] | None = None, error: Exception | None = None) -> StubClient:
        stub = StubClient(raw=raw, error=error)
        app.dependency_overrides[get_linkedin_client] = lambda: stub
        return stub

    return _install


# =============================================================================
# meta endpoints
# =============================================================================


def test_health(api: TestClient) -> None:
    response = api.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_docs_and_openapi_render(api: TestClient) -> None:
    assert api.get("/docs").status_code == 200
    schema = api.get("/openapi.json")
    assert schema.status_code == 200
    assert "/profile" in schema.json()["paths"]


def test_root_redirects_to_docs(api: TestClient) -> None:
    response = api.get("/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/docs"


def test_readiness_reports_config_without_exposing_values(api: TestClient, set_env) -> None:
    set_env(
        LI_AT="AQEDAT-secret-cookie",
        LI_JSESSIONID='"ajax:secret-session"',
        PROXY_URL="socks5h://user:pa55word@residential.example.net:1080",
    )
    response = api.get("/readiness")
    assert response.status_code == 200
    body = response.json()

    assert body["credentials_configured"] is True
    assert body["proxy_configured"] is True
    assert body["ready_for_live_requests"] is True
    assert body["cache"]["ttl_seconds"] == 86_400

    rendered = response.text
    for secret in ("AQEDAT-secret-cookie", "ajax:secret-session", "pa55word", "residential.example.net"):
        assert secret not in rendered


def test_readiness_flags_missing_configuration(api: TestClient) -> None:
    body = api.get("/readiness").json()
    assert body["credentials_configured"] is False
    assert body["proxy_configured"] is False
    assert body["ready_for_live_requests"] is False


def test_lifespan_startup_runs_cleanly() -> None:
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200


# =============================================================================
# happy path
# =============================================================================


def test_profile_ok(api: TestClient, stub_upstream, sample_raw: dict[str, Any]) -> None:
    stub_upstream(raw=sample_raw)
    response = api.get("/profile", params={"url": "https://linkedin.com/in/jane-doe-example"})
    assert response.status_code == 200

    body = response.json()
    assert body["full_name"] and body["headline"]
    assert isinstance(body["experience"], list)
    assert len(body["experience"]) == 3
    assert len(body["education"]) == 2
    assert len(body["skills"]) == 8
    assert len(body["certifications"]) == 2
    assert len(body["languages"]) == 2
    assert body["location"]["full"] == "San Francisco, California"
    assert body["profile_picture_url"].startswith("https://media.licdn.com/")


def test_response_contains_every_schema_key(
    api: TestClient, stub_upstream, sample_raw: dict[str, Any]
) -> None:
    stub_upstream(raw=sample_raw)
    body = api.get("/profile", params={"url": VALID_URL}).json()
    assert set(body) == set(Profile.model_fields)
    assert set(body["location"]) == {"city", "country", "full"}


def test_response_validates_against_the_pydantic_schema(
    api: TestClient, stub_upstream, sample_raw: dict[str, Any]
) -> None:
    stub_upstream(raw=sample_raw)
    body = api.get("/profile", params={"url": VALID_URL}).json()
    assert isinstance(Profile.model_validate(body), Profile)


def test_slug_is_extracted_before_the_upstream_call(
    api: TestClient, stub_upstream, sample_raw: dict[str, Any]
) -> None:
    stub = stub_upstream(raw=sample_raw)
    api.get("/profile", params={"url": "https://in.linkedin.com/in/jane-doe-example/details/skills/"})
    assert stub.calls == ["jane-doe-example"]


def test_partial_profile_degrades_with_warnings_not_500(api: TestClient, stub_upstream) -> None:
    minimal = {
        "included": [
            {
                "$type": "com.linkedin.voyager.identity.profile.Profile",
                "entityUrn": "urn:li:fs_profile:ACoAAAMinimal",
                "firstName": "Min",
                "lastName": "Imal",
                "headline": "Only a headline",
            }
        ]
    }
    stub_upstream(raw=minimal)
    response = api.get("/profile", params={"url": VALID_URL})
    assert response.status_code == 200
    body = response.json()
    assert body["full_name"] == "Min Imal"
    assert body["experience"] == []
    assert body["warnings"]


def test_completely_empty_payload_is_still_a_200_with_warnings(
    api: TestClient, stub_upstream
) -> None:
    stub_upstream(raw={"included": []})
    response = api.get("/profile", params={"url": VALID_URL})
    assert response.status_code == 200
    assert response.json()["warnings"]


# =============================================================================
# input validation
# =============================================================================


def test_bad_url_422(api: TestClient, stub_upstream) -> None:
    stub_upstream()
    assert api.get("/profile", params={"url": "https://google.com"}).status_code == 422


@pytest.mark.parametrize(
    "bad_url",
    [
        "https://google.com",
        "https://www.linkedin.com/company/acme",
        "https://www.linkedin.com/school/stanford",
        "https://www.linkedin.com/feed/",
        "https://www.linkedin.com/in/",
        "https://linkedin.com.attacker.test/in/john-doe",
        "not a url",
        "",
    ],
)
def test_invalid_urls_are_rejected_without_an_upstream_call(
    api: TestClient, stub_upstream, bad_url: str
) -> None:
    stub = stub_upstream()
    response = api.get("/profile", params={"url": bad_url})
    assert response.status_code == 422
    assert stub.calls == []  # never spent a LinkedIn request on it


def test_missing_url_param_422(api: TestClient, stub_upstream) -> None:
    stub_upstream()
    assert api.get("/profile").status_code == 422


# =============================================================================
# upstream error mapping
# =============================================================================


@pytest.mark.parametrize(
    "error,expected_status",
    [
        (ProfileNotFound(), 404),
        (AuthExpired(), 502),
        (RateLimited(), 429),
        (ProxyRequired(), 500),
        (CredentialsMissing(), 500),
        (UpstreamError(), 502),
        (UpstreamTimeout(), 504),
    ],
)
def test_upstream_errors_map_to_http_statuses(
    api: TestClient, stub_upstream, error: Exception, expected_status: int
) -> None:
    stub_upstream(error=error)
    response = api.get("/profile", params={"url": VALID_URL})
    assert response.status_code == expected_status
    assert response.json()["detail"]


def test_not_found_404(api: TestClient, stub_upstream) -> None:
    stub_upstream(error=ProfileNotFound())
    response = api.get("/profile", params={"url": "https://linkedin.com/in/ghost"})
    assert response.status_code == 404


def test_expired_session_tells_the_operator_to_refresh_the_cookie(
    api: TestClient, stub_upstream
) -> None:
    stub_upstream(error=AuthExpired())
    detail = api.get("/profile", params={"url": VALID_URL}).json()["detail"].lower()
    assert "refresh" in detail
    assert "cookie" in detail


def test_missing_proxy_fails_closed_end_to_end(api: TestClient) -> None:
    """No dependency override: the real client must refuse before any egress."""
    response = api.get("/profile", params={"url": VALID_URL})
    assert response.status_code == 500
    assert "PROXY_URL" in response.json()["detail"]


def test_error_bodies_never_contain_secrets(api: TestClient, set_env) -> None:
    set_env(PROXY_URL="socks5h://user:pa55word@residential.example.net:1080")
    # Proxy present but no cookies -> CredentialsMissing, still no network.
    response = api.get("/profile", params={"url": VALID_URL})
    assert response.status_code == 500
    for secret in ("pa55word", "residential.example.net", "user:pa55word"):
        assert secret not in response.text


def test_unexpected_exception_becomes_a_generic_500(api: TestClient, stub_upstream) -> None:
    stub_upstream(error=ValueError("internal detail that must not escape"))
    response = api.get("/profile", params={"url": VALID_URL})
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error."}
    assert "internal detail" not in response.text


# =============================================================================
# caching — the main defence for the LinkedIn account
# =============================================================================


def test_cache_prevents_a_second_upstream_call(
    api: TestClient, stub_upstream, sample_raw: dict[str, Any]
) -> None:
    stub = stub_upstream(raw=sample_raw)
    first = api.get("/profile", params={"url": VALID_URL})
    second = api.get("/profile", params={"url": VALID_URL})
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(stub.calls) == 1


def test_cache_is_keyed_by_slug_not_url_spelling(
    api: TestClient, stub_upstream, sample_raw: dict[str, Any]
) -> None:
    stub = stub_upstream(raw=sample_raw)
    for url in (
        "https://www.linkedin.com/in/jane-doe-example/",
        "https://linkedin.com/in/jane-doe-example?utm_source=x",
        "https://in.linkedin.com/in/Jane-Doe-Example",
        "www.linkedin.com/in/jane-doe-example",
    ):
        assert api.get("/profile", params={"url": url}).status_code == 200
    assert len(stub.calls) == 1


def test_distinct_profiles_are_cached_separately(
    api: TestClient, stub_upstream, sample_raw: dict[str, Any]
) -> None:
    stub = stub_upstream(raw=sample_raw)
    api.get("/profile", params={"url": "https://linkedin.com/in/first-person"})
    api.get("/profile", params={"url": "https://linkedin.com/in/second-person"})
    api.get("/profile", params={"url": "https://linkedin.com/in/first-person"})
    assert stub.calls == ["first-person", "second-person"]


def test_failures_are_not_cached(api: TestClient, stub_upstream) -> None:
    """A transient 429 must not poison the cache for the whole TTL."""
    stub = stub_upstream(error=RateLimited())
    assert api.get("/profile", params={"url": VALID_URL}).status_code == 429
    assert api.get("/profile", params={"url": VALID_URL}).status_code == 429
    assert len(stub.calls) == 2


def test_cache_stats_are_reported(
    api: TestClient, stub_upstream, sample_raw: dict[str, Any]
) -> None:
    stub_upstream(raw=sample_raw)
    api.get("/profile", params={"url": VALID_URL})
    api.get("/profile", params={"url": VALID_URL})
    stats = api.get("/readiness").json()["cache"]
    assert stats["entries"] == 1
    assert stats["hits"] == 1
    assert stats["misses"] == 1
