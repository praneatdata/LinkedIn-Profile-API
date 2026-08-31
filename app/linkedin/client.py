"""HTTP client for LinkedIn's internal Voyager API.

No browser is involved. Voyager is a plain JSON API; the only reason a naive HTTP
client fails against it is fingerprinting, so this module leans on three things:

* **TLS/JA3 impersonation** via `curl_cffi` — a stock `requests`/`httpx` TLS handshake
  is trivially distinguishable from Chrome's and gets challenged immediately.
* **Browser-consistent headers**, including the `csrf-token` header that LinkedIn
  requires to equal the `JSESSIONID` cookie value with its quotes stripped.
* **A residential/mobile egress proxy.** This is enforced *fail-closed*: with
  `PROXY_URL` unset the client refuses to send anything rather than leak the host IP
  (a datacenter IP would get an immediate HTTP 999 and burn the account).

Every failure is translated into a typed exception carrying a caller-safe message.
Upstream exception strings are never propagated, because libcurl embeds the full
proxy URL — credentials included — in its error text.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import unquote, urlsplit

from curl_cffi import requests as curl_requests
from curl_cffi.requests import exceptions as curl_exceptions
from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.config import Settings
from app.linkedin.endpoints import dash_profiles_url, profile_view_url
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

_LINKEDIN_HOST = "linkedin.com"
_MAX_SLUG_LENGTH = 200

# Marks a response that is really the login wall / bot challenge rather than data.
_AUTHWALL_MARKERS = ("authwall", "/uas/login", "checkpoint/challenge", "csrf-error")

_TIMEOUT_ERRORS: tuple[type[BaseException], ...] = (
    curl_exceptions.Timeout,
    curl_exceptions.ConnectTimeout,
    curl_exceptions.ReadTimeout,
)
_PROXY_ERRORS: tuple[type[BaseException], ...] = (
    curl_exceptions.ProxyError,
    curl_exceptions.InvalidProxyURL,
)
_TRANSPORT_ERRORS: tuple[type[BaseException], ...] = (
    curl_exceptions.RequestException,
    curl_exceptions.CurlError,
)


# =============================================================================
# URL -> public identifier
# =============================================================================


def extract_public_id(value: str, *, allow_bare_slug: bool = False) -> str:
    """Pull the public identifier out of a LinkedIn profile URL.

    Handles trailing slashes, query strings, fragments, missing schemes, country
    subdomains (`in.linkedin.com`), the `/mwlite/` mobile prefix, and deep links such
    as `/in/<slug>/details/experience/`.

    Raises `InvalidProfileUrl` for anything that is not a LinkedIn `/in/` URL — which
    is what makes the endpoint answer 422 instead of firing a doomed upstream request.
    """
    if not isinstance(value, str) or not value.strip():
        raise InvalidProfileUrl()

    candidate = value.strip()

    if allow_bare_slug and not any(ch in candidate for ch in "/:."):
        return _validate_slug(candidate)

    if "://" not in candidate:
        candidate = "https://" + candidate.lstrip("/")

    try:
        parsed = urlsplit(candidate)
    except ValueError:
        raise InvalidProfileUrl() from None

    host = (parsed.hostname or "").lower().removeprefix("www.")
    # Exact host or a real subdomain only: `linkedin.com.attacker.test` must not pass.
    if host != _LINKEDIN_HOST and not host.endswith("." + _LINKEDIN_HOST):
        raise InvalidProfileUrl(
            "`url` must point at linkedin.com — got a different host."
        )

    segments = [segment for segment in parsed.path.split("/") if segment]
    if "in" not in segments:
        raise InvalidProfileUrl(
            "`url` must be a member profile URL containing `/in/` "
            "(company and school URLs are not supported)."
        )

    index = segments.index("in")
    if index + 1 >= len(segments):
        raise InvalidProfileUrl("`url` is missing the profile identifier after `/in/`.")

    return _validate_slug(segments[index + 1])


def _validate_slug(slug: str) -> str:
    decoded = unquote(slug).strip()
    if not decoded or decoded in {".", ".."}:
        raise InvalidProfileUrl("`url` contains an empty profile identifier.")
    if len(decoded) > _MAX_SLUG_LENGTH:
        raise InvalidProfileUrl("Profile identifier is implausibly long.")
    if any(ch in decoded for ch in "/?#\\ \t\n"):
        raise InvalidProfileUrl("Profile identifier contains illegal characters.")
    return decoded


# =============================================================================
# transport seam (monkeypatched in tests; the only place curl_cffi is called)
# =============================================================================


def _http_get(
    url: str,
    *,
    headers: dict[str, str],
    cookies: dict[str, str],
    proxies: dict[str, str],
    impersonate: str,
    timeout: int,
) -> Any:
    return curl_requests.get(
        url,
        headers=headers,
        cookies=cookies,
        proxies=proxies,
        impersonate=impersonate,
        timeout=timeout,
        allow_redirects=True,
    )


# =============================================================================
# client
# =============================================================================


class LinkedInClient:
    """Fetches raw Voyager JSON for one profile at a time."""

    def __init__(
        self,
        settings: Settings | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        # Read the environment fresh so callers (and tests) can vary it per instance.
        self.settings = settings or Settings()
        self._sleep = sleep

    # --- request construction ---

    def _headers(self) -> dict[str, str]:
        settings = self.settings
        return {
            "user-agent": settings.USER_AGENT,
            # Asks Voyager for the flat `included[]` form the parser expects.
            "accept": "application/vnd.linkedin.normalized+json+2.1",
            "accept-language": "en-US,en;q=0.9",
            "x-restli-protocol-version": "2.0.0",
            "x-li-lang": "en_US",
            # LinkedIn requires this to equal the JSESSIONID cookie without quotes.
            "csrf-token": settings.csrf_token,
            "referer": "https://www.linkedin.com/feed/",
        }

    def _cookies(self) -> dict[str, str]:
        # JSESSIONID must keep its surrounding quotes as a *cookie*; only the
        # csrf-token *header* drops them.
        return {
            "li_at": self.settings.LI_AT.strip(),
            "JSESSIONID": self.settings.jsessionid_cookie,
        }

    def _proxies(self) -> dict[str, str]:
        if not self.settings.has_proxy:
            raise ProxyRequired()
        proxy = self.settings.PROXY_URL.strip()
        return {"http": proxy, "https": proxy}

    def _require_egress_proxy(self) -> None:
        """Fail closed. Checked before anything else so no request can escape."""
        if not self.settings.has_proxy:
            raise ProxyRequired()

    def _require_credentials(self) -> None:
        if not self.settings.has_credentials:
            raise CredentialsMissing()

    # --- fetching ---

    def fetch_raw(self, public_id: str) -> dict[str, Any]:
        """Fetch the raw Voyager payload for `public_id`.

        Tries `profileView` first, then the `dash/profiles` fallback when a
        `DASH_DECORATION_ID` is configured. Auth and rate-limit failures short-circuit
        immediately — trying a second endpoint would only burn more of the quota.
        """
        self._require_egress_proxy()
        self._require_credentials()

        attempts = self._endpoints(public_id)
        if not attempts:
            raise UpstreamError(
                "No Voyager endpoint is enabled; set PROFILE_VIEW_ENABLED=true "
                "or provide a DASH_DECORATION_ID."
            )

        primary_error: UpstreamError | ProfileNotFound | None = None
        for url in attempts:
            try:
                return self._request_with_retries(url)
            except (ProfileNotFound, UpstreamError) as exc:
                # Recoverable-by-another-endpoint: remember the primary failure and
                # let the fallback have a go.
                if primary_error is None:
                    primary_error = exc
                continue

        assert primary_error is not None  # loop ran at least once
        raise primary_error

    def _endpoints(self, public_id: str) -> list[str]:
        urls: list[str] = []
        if self.settings.PROFILE_VIEW_ENABLED:
            urls.append(profile_view_url(public_id))
        if self.settings.dash_enabled:
            urls.append(
                dash_profiles_url(public_id, self.settings.DASH_DECORATION_ID.strip())
            )
        return urls

    def _request_with_retries(self, url: str) -> dict[str, Any]:
        retrying = Retrying(
            retry=retry_if_exception_type((RateLimited, UpstreamTimeout)),
            stop=stop_after_attempt(max(1, self.settings.MAX_RETRIES)),
            wait=wait_exponential_jitter(initial=2, max=30, jitter=2),
            reraise=True,
            sleep=self._sleep,
        )
        return retrying(self._request_once, url)

    def _request_once(self, url: str) -> dict[str, Any]:
        headers = self._headers()
        cookies = self._cookies()
        proxies = self._proxies()

        try:
            response = _http_get(
                url,
                headers=headers,
                cookies=cookies,
                proxies=proxies,
                impersonate=self.settings.IMPERSONATE,
                timeout=self.settings.REQUEST_TIMEOUT_SECONDS,
            )
        except _TIMEOUT_ERRORS:
            # `from None` throughout: libcurl error strings embed the full proxy URL,
            # credentials included, and must never reach a caller or a log.
            raise UpstreamTimeout() from None
        except _PROXY_ERRORS as exc:
            raise UpstreamError(
                f"Could not reach LinkedIn through the configured proxy "
                f"({type(exc).__name__})."
            ) from None
        except _TRANSPORT_ERRORS as exc:
            raise UpstreamError(
                f"Transport error while contacting LinkedIn ({type(exc).__name__})."
            ) from None

        return self._interpret(response)

    def _interpret(self, response: Any) -> dict[str, Any]:
        status = getattr(response, "status_code", None)

        if status in (401, 403):
            raise AuthExpired()
        if status in (404, 410):
            raise ProfileNotFound()
        # 999 is LinkedIn's non-standard "you look like a bot" status.
        if status in (429, 999):
            raise RateLimited()
        if not isinstance(status, int):
            raise UpstreamError("LinkedIn response had no usable status code.")
        if status >= 400:
            raise UpstreamError(f"LinkedIn returned HTTP {status}.")

        # A redirect chain that lands on the login wall is a 200 carrying no data.
        final_url = str(getattr(response, "url", "") or "").lower()
        if any(marker in final_url for marker in _AUTHWALL_MARKERS):
            raise AuthExpired(
                "LinkedIn redirected the request to its login wall — the session "
                "cookies are no longer valid."
            )

        body = getattr(response, "text", "") or ""
        stripped = body.lstrip()

        if stripped.startswith("<"):
            head = stripped[:4000].lower()
            if any(marker in head for marker in _AUTHWALL_MARKERS) or "sign in" in head:
                raise AuthExpired(
                    "LinkedIn served its login wall instead of JSON — refresh the "
                    "li_at and JSESSIONID cookies."
                )
            raise UpstreamError(
                "LinkedIn returned HTML instead of JSON — most likely a bot challenge. "
                "Try a different residential proxy or impersonation target."
            )

        if not stripped:
            raise UpstreamError("LinkedIn returned an empty response body.")

        try:
            payload = json.loads(body)
        except ValueError:
            raise UpstreamError("LinkedIn returned a body that is not valid JSON.") from None

        if not isinstance(payload, dict):
            raise UpstreamError(
                f"LinkedIn returned unexpected JSON of type {type(payload).__name__}."
            )
        return payload
