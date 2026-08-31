"""Voyager client tests.

The single `_http_get` seam is monkeypatched, so this file exercises all of the
client's real logic — headers, fail-closed proxy, status mapping, retries, endpoint
fallback, secret redaction — while making **zero** network requests.

(`respx` is in requirements for httpx-level mocking, but curl_cffi bypasses httpx
entirely, so the transport seam is patched directly instead.)
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.linkedin import client as client_module
from app.linkedin.client import LinkedInClient, extract_public_id
from app.linkedin.endpoints import profile_view_url
from app.linkedin.exceptions import (
    AuthExpired,
    CredentialsMissing,
    InvalidProfileUrl,
    ProfileNotFound,
    ProxyRequired,
    RateLimited,
    UpstreamError,
    UpstreamTimeout,
)

PROXY_WITH_SECRET = "socks5h://proxyuser:sup3r-s3cret@residential.example.net:1080"


# =============================================================================
# helpers
# =============================================================================


class FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        text: str = '{"included": []}',
        url: str = "https://www.linkedin.com/voyager/api/identity/profiles/x/profileView",
    ) -> None:
        self.status_code = status_code
        self.text = text
        self.url = url


class Recorder:
    """Stands in for `_http_get`; records calls and replays scripted responses."""

    def __init__(self, *responses: Any) -> None:
        self._responses = list(responses) or [FakeResponse()]
        self.calls: list[dict[str, Any]] = []

    def __call__(self, url: str, **kwargs: Any) -> Any:
        self.calls.append({"url": url, **kwargs})
        item = self._responses[min(len(self.calls) - 1, len(self._responses) - 1)]
        if isinstance(item, BaseException) or (
            isinstance(item, type) and issubclass(item, BaseException)
        ):
            raise item
        return item

    @property
    def call_count(self) -> int:
        return len(self.calls)


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch):
    """A client whose credentials and proxy are set, with no real sleeping."""

    def _build(*responses: Any, **env: str) -> tuple[LinkedInClient, Recorder]:
        monkeypatch.setenv("LI_AT", "AQEDAT-fake-li-at-value")
        monkeypatch.setenv("LI_JSESSIONID", '"ajax:9876543210"')
        monkeypatch.setenv("PROXY_URL", PROXY_WITH_SECRET)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        recorder = Recorder(*responses)
        monkeypatch.setattr(client_module, "_http_get", recorder)
        return LinkedInClient(sleep=lambda _seconds: None), recorder

    return _build


# =============================================================================
# headers / cookies (Phase 4 Definition of Done)
# =============================================================================


def test_csrf_header_matches_jsessionid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LI_JSESSIONID", '"ajax:123"')
    c = LinkedInClient()
    assert c._headers()["csrf-token"] == "ajax:123"


def test_csrf_header_handles_unquoted_jsessionid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pasting the cookie without its quotes is the usual mistake; handle both."""
    monkeypatch.setenv("LI_JSESSIONID", "ajax:123")
    c = LinkedInClient()
    assert c._headers()["csrf-token"] == "ajax:123"
    assert c._cookies()["JSESSIONID"] == '"ajax:123"'  # cookie keeps the quotes


def test_required_headers_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LI_JSESSIONID", '"ajax:1"')
    headers = LinkedInClient()._headers()
    assert headers["accept"] == "application/vnd.linkedin.normalized+json+2.1"
    assert headers["x-restli-protocol-version"] == "2.0.0"
    assert headers["x-li-lang"] == "en_US"
    # A current Chrome UA, not a museum piece.
    assert "Chrome/1" in headers["user-agent"]
    assert "Chrome/83" not in headers["user-agent"]


def test_cookies_are_sent(configured) -> None:
    client, recorder = configured()
    client.fetch_raw("someone")
    cookies = recorder.calls[0]["cookies"]
    assert cookies["li_at"] == "AQEDAT-fake-li-at-value"
    assert cookies["JSESSIONID"] == '"ajax:9876543210"'


def test_proxy_applied_to_both_schemes_and_impersonation_set(configured) -> None:
    client, recorder = configured()
    client.fetch_raw("someone")
    call = recorder.calls[0]
    assert call["proxies"] == {"http": PROXY_WITH_SECRET, "https": PROXY_WITH_SECRET}
    assert call["impersonate"] == "chrome"
    assert call["timeout"] == 20


# =============================================================================
# fail-closed proxy
# =============================================================================


def test_proxy_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PROXY_URL", raising=False)
    with pytest.raises(ProxyRequired):
        LinkedInClient().fetch_raw("someone")


def test_proxy_check_runs_before_any_request_is_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail-closed means no packet leaves the host, not merely no useful one."""
    recorder = Recorder()
    monkeypatch.setattr(client_module, "_http_get", recorder)
    monkeypatch.setenv("LI_AT", "x")
    monkeypatch.setenv("LI_JSESSIONID", '"ajax:1"')
    monkeypatch.delenv("PROXY_URL", raising=False)

    with pytest.raises(ProxyRequired):
        LinkedInClient().fetch_raw("someone")
    assert recorder.call_count == 0


def test_whitespace_only_proxy_is_treated_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROXY_URL", "   ")
    with pytest.raises(ProxyRequired):
        LinkedInClient().fetch_raw("someone")


def test_direct_egress_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """The escape hatch must never be reachable without an explicit opt-in."""
    monkeypatch.delenv("PROXY_URL", raising=False)
    monkeypatch.delenv("ALLOW_DIRECT_EGRESS", raising=False)
    assert LinkedInClient().settings.ALLOW_DIRECT_EGRESS is False
    with pytest.raises(ProxyRequired):
        LinkedInClient().fetch_raw("someone")


@pytest.mark.parametrize("falsy", ["false", "0", "no", "False"])
def test_direct_egress_stays_closed_for_falsy_values(
    monkeypatch: pytest.MonkeyPatch, falsy: str
) -> None:
    monkeypatch.delenv("PROXY_URL", raising=False)
    monkeypatch.setenv("ALLOW_DIRECT_EGRESS", falsy)
    with pytest.raises(ProxyRequired):
        LinkedInClient().fetch_raw("someone")


def test_direct_egress_opt_in_sends_without_a_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PROXY_URL", raising=False)
    monkeypatch.setenv("ALLOW_DIRECT_EGRESS", "true")
    monkeypatch.setenv("LI_AT", "AQEDAT-fake")
    monkeypatch.setenv("LI_JSESSIONID", '"ajax:1"')
    recorder = Recorder(FakeResponse(text='{"included": []}'))
    monkeypatch.setattr(client_module, "_http_get", recorder)

    assert LinkedInClient().fetch_raw("someone") == {"included": []}
    # No proxy dict at all — not a partially-populated one.
    assert recorder.calls[0]["proxies"] == {}


def test_a_real_proxy_still_wins_over_the_escape_hatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROXY_URL", PROXY_WITH_SECRET)
    monkeypatch.setenv("ALLOW_DIRECT_EGRESS", "true")
    monkeypatch.setenv("LI_AT", "AQEDAT-fake")
    monkeypatch.setenv("LI_JSESSIONID", '"ajax:1"')
    recorder = Recorder(FakeResponse(text='{"included": []}'))
    monkeypatch.setattr(client_module, "_http_get", recorder)

    LinkedInClient().fetch_raw("someone")
    assert recorder.calls[0]["proxies"]["https"] == PROXY_WITH_SECRET
    assert LinkedInClient().settings.direct_egress_in_use is False


def test_credentials_required_once_proxy_is_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROXY_URL", PROXY_WITH_SECRET)
    monkeypatch.delenv("LI_AT", raising=False)
    with pytest.raises(CredentialsMissing):
        LinkedInClient().fetch_raw("someone")


# =============================================================================
# URL -> slug extraction
# =============================================================================


@pytest.mark.parametrize(
    "slug_in,expected",
    [
        ("https://www.linkedin.com/in/john-doe/", "john-doe"),
        ("https://linkedin.com/in/jane?utm=x", "jane"),
        ("https://www.linkedin.com/in/john-doe", "john-doe"),
        ("http://www.linkedin.com/in/john-doe/", "john-doe"),
        ("https://www.linkedin.com/in/john-doe/#about", "john-doe"),
        ("https://www.linkedin.com/in/john-doe/details/experience/", "john-doe"),
        ("https://in.linkedin.com/in/john-doe", "john-doe"),
        ("https://uk.linkedin.com/in/john-doe/", "john-doe"),
        ("https://www.linkedin.com/mwlite/in/john-doe", "john-doe"),
        ("www.linkedin.com/in/john-doe", "john-doe"),
        ("linkedin.com/in/john-doe/", "john-doe"),
        ("  https://www.linkedin.com/in/john-doe/  ", "john-doe"),
        ("https://www.linkedin.com/in/ACwAAABc123XyZ/", "ACwAAABc123XyZ"),
        ("https://www.linkedin.com/in/jos%C3%A9-garc%C3%ADa/", "josé-garcía"),
        ("https://www.linkedin.com/in/john-doe-1a2b3c4d/", "john-doe-1a2b3c4d"),
    ],
)
def test_slug_extraction(slug_in: str, expected: str) -> None:
    assert extract_public_id(slug_in) == expected


@pytest.mark.parametrize(
    "bad_url",
    [
        "https://google.com",
        "https://google.com/in/john-doe",
        "https://www.linkedin.com/company/acme",
        "https://www.linkedin.com/school/stanford-university",
        "https://www.linkedin.com/feed/",
        "https://www.linkedin.com/in/",
        "https://www.linkedin.com/in",
        # host-suffix confusion: must not be accepted as linkedin.com
        "https://linkedin.com.attacker.test/in/john-doe",
        "https://notlinkedin.com/in/john-doe",
        "",
        "   ",
        "not a url at all",
        "john-doe",  # bare slug, not allowed by default
    ],
)
def test_invalid_urls_rejected(bad_url: str) -> None:
    with pytest.raises(InvalidProfileUrl):
        extract_public_id(bad_url)


def test_bare_slug_allowed_only_when_opted_in() -> None:
    assert extract_public_id("john-doe", allow_bare_slug=True) == "john-doe"
    with pytest.raises(InvalidProfileUrl):
        extract_public_id("john-doe")


def test_absurdly_long_slug_rejected() -> None:
    with pytest.raises(InvalidProfileUrl):
        extract_public_id("https://www.linkedin.com/in/" + "a" * 500)


def test_profile_view_url_percent_encodes_the_slug() -> None:
    assert profile_view_url("josé-garcía").endswith(
        "/identity/profiles/jos%C3%A9-garc%C3%ADa/profileView"
    )


# =============================================================================
# status -> exception mapping
# =============================================================================


@pytest.mark.parametrize(
    "status,expected",
    [
        (401, AuthExpired),
        (403, AuthExpired),
        (404, ProfileNotFound),
        (410, ProfileNotFound),
        (429, RateLimited),
        (999, RateLimited),
        (400, UpstreamError),
        (418, UpstreamError),
        (500, UpstreamError),
        (503, UpstreamError),
    ],
)
def test_status_mapping(configured, status: int, expected: type[Exception]) -> None:
    client, _ = configured(FakeResponse(status_code=status), **{"MAX_RETRIES": "1"})
    with pytest.raises(expected):
        client.fetch_raw("someone")


def test_successful_json_is_returned(configured) -> None:
    payload = {"data": {}, "included": [{"entityUrn": "urn:li:fs_profile:X"}]}
    client, recorder = configured(FakeResponse(text=json.dumps(payload)))
    assert client.fetch_raw("someone") == payload
    assert recorder.calls[0]["url"] == profile_view_url("someone")


def test_html_login_wall_maps_to_auth_expired(configured) -> None:
    html = "<!DOCTYPE html><html><body>Please sign in to continue</body></html>"
    client, _ = configured(FakeResponse(text=html))
    with pytest.raises(AuthExpired):
        client.fetch_raw("someone")


def test_authwall_redirect_maps_to_auth_expired(configured) -> None:
    client, _ = configured(
        FakeResponse(text='{"ok": true}', url="https://www.linkedin.com/authwall?trk=x")
    )
    with pytest.raises(AuthExpired):
        client.fetch_raw("someone")


def test_generic_html_challenge_maps_to_upstream_error(configured) -> None:
    client, _ = configured(FakeResponse(text="<html><body>Blocked</body></html>"))
    with pytest.raises(UpstreamError) as excinfo:
        client.fetch_raw("someone")
    assert "HTML instead of JSON" in str(excinfo.value)


@pytest.mark.parametrize("body", ["", "   ", "not json at all", "[1, 2, 3]", "null"])
def test_unusable_bodies_map_to_upstream_error(configured, body: str) -> None:
    client, _ = configured(FakeResponse(text=body))
    with pytest.raises(UpstreamError):
        client.fetch_raw("someone")


# =============================================================================
# retries
# =============================================================================


def test_retries_on_rate_limit_then_succeeds(configured) -> None:
    client, recorder = configured(
        FakeResponse(status_code=429),
        FakeResponse(status_code=429),
        FakeResponse(text='{"included": []}'),
        MAX_RETRIES="3",
    )
    assert client.fetch_raw("someone") == {"included": []}
    assert recorder.call_count == 3


def test_retries_exhausted_reraises_rate_limited(configured) -> None:
    client, recorder = configured(FakeResponse(status_code=429), MAX_RETRIES="3")
    with pytest.raises(RateLimited):
        client.fetch_raw("someone")
    assert recorder.call_count == 3


def test_timeout_is_retried_then_surfaces_as_504(configured) -> None:
    client, recorder = configured(
        client_module.curl_exceptions.Timeout("timed out"), MAX_RETRIES="2"
    )
    with pytest.raises(UpstreamTimeout) as excinfo:
        client.fetch_raw("someone")
    assert excinfo.value.status_code == 504
    assert recorder.call_count == 2


def test_no_retry_on_auth_expired(configured) -> None:
    """Retrying an invalid cookie only accelerates the account restriction."""
    client, recorder = configured(FakeResponse(status_code=401), MAX_RETRIES="3")
    with pytest.raises(AuthExpired):
        client.fetch_raw("someone")
    assert recorder.call_count == 1


def test_no_retry_on_profile_not_found(configured) -> None:
    client, recorder = configured(FakeResponse(status_code=404), MAX_RETRIES="3")
    with pytest.raises(ProfileNotFound):
        client.fetch_raw("someone")
    assert recorder.call_count == 1


# =============================================================================
# endpoint strategy
# =============================================================================


def test_only_profile_view_is_called_by_default(configured) -> None:
    client, recorder = configured(FakeResponse(status_code=404), MAX_RETRIES="1")
    with pytest.raises(ProfileNotFound):
        client.fetch_raw("someone")
    assert recorder.call_count == 1
    assert "profileView" in recorder.calls[0]["url"]


def test_dash_fallback_used_when_decoration_id_configured(configured) -> None:
    client, recorder = configured(
        FakeResponse(status_code=404),
        FakeResponse(text='{"included": [{"entityUrn": "urn:li:fsd_profile:X"}]}'),
        MAX_RETRIES="1",
        DASH_DECORATION_ID="com.linkedin.voyager.dash.deco.identity.profile.FullProfile-99",
    )
    result = client.fetch_raw("someone")
    assert result["included"][0]["entityUrn"] == "urn:li:fsd_profile:X"
    assert recorder.call_count == 2
    assert "profileView" in recorder.calls[0]["url"]
    assert "dash/profiles" in recorder.calls[1]["url"]
    assert "decorationId=" in recorder.calls[1]["url"]


def test_auth_failure_short_circuits_the_fallback(configured) -> None:
    client, recorder = configured(
        FakeResponse(status_code=401),
        MAX_RETRIES="1",
        DASH_DECORATION_ID="deco-1",
    )
    with pytest.raises(AuthExpired):
        client.fetch_raw("someone")
    assert recorder.call_count == 1  # never tried dash


def test_primary_error_is_reported_when_both_endpoints_fail(configured) -> None:
    client, recorder = configured(
        FakeResponse(status_code=404),
        FakeResponse(status_code=400),
        MAX_RETRIES="1",
        DASH_DECORATION_ID="deco-1",
    )
    # The 404 from the canonical endpoint is the more useful answer than the
    # fallback's 400 (which usually just means the decorationId has drifted).
    with pytest.raises(ProfileNotFound):
        client.fetch_raw("someone")
    assert recorder.call_count == 2


def test_no_enabled_endpoint_is_a_configuration_error(configured) -> None:
    client, recorder = configured(PROFILE_VIEW_ENABLED="false")
    with pytest.raises(UpstreamError) as excinfo:
        client.fetch_raw("someone")
    assert "No Voyager endpoint is enabled" in str(excinfo.value)
    assert recorder.call_count == 0


# =============================================================================
# secret redaction — libcurl embeds the full proxy URL in its error strings
# =============================================================================


@pytest.mark.parametrize(
    "raised",
    [
        client_module.curl_exceptions.ProxyError(
            f"Failed to connect to {PROXY_WITH_SECRET}"
        ),
        client_module.curl_exceptions.CurlError(
            f"curl: (7) could not connect to {PROXY_WITH_SECRET}"
        ),
        client_module.curl_exceptions.RequestException(
            f"request failed via {PROXY_WITH_SECRET}"
        ),
    ],
)
def test_transport_errors_never_leak_proxy_credentials(configured, raised) -> None:
    client, _ = configured(raised, MAX_RETRIES="1")
    with pytest.raises(UpstreamError) as excinfo:
        client.fetch_raw("someone")

    rendered = f"{excinfo.value} {excinfo.value.detail!r} {excinfo.value.args!r}"
    for secret in ("sup3r-s3cret", "proxyuser", "residential.example.net"):
        assert secret not in rendered
    # And the cause is severed so a traceback handler cannot re-expose it either.
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__context__ is None or excinfo.value.__suppress_context__


def test_error_messages_never_leak_cookies(configured) -> None:
    client, _ = configured(FakeResponse(status_code=403), MAX_RETRIES="1")
    with pytest.raises(AuthExpired) as excinfo:
        client.fetch_raw("someone")
    rendered = str(excinfo.value)
    assert "AQEDAT-fake-li-at-value" not in rendered
    assert "ajax:9876543210" not in rendered
