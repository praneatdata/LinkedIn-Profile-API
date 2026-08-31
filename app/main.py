"""FastAPI application: routes, error mapping, and wiring."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.requests import Request

from app import __version__
from app.cache import ProfileCache, get_profile_cache
from app.config import Settings, get_settings
from app.linkedin.client import LinkedInClient, extract_public_id
from app.linkedin.exceptions import InvalidProfileUrl, LinkedInError
from app.linkedin.parser import parse_profile
from app.models import ErrorResponse, HealthResponse, Profile
from app.redaction import install_secret_redaction
from app.security import require_api_key

logger = logging.getLogger("app.api")

DESCRIPTION = """
Fetches a public LinkedIn profile and returns clean, structured JSON.

Requests go **directly to LinkedIn's internal Voyager API** over HTTP — there is no
browser, no Selenium, no Playwright, no Puppeteer anywhere in the stack. TLS
fingerprint impersonation (`curl_cffi`) plus browser-consistent headers are what make
a plain HTTP client acceptable to LinkedIn.

All LinkedIn traffic egresses through a residential proxy. That is enforced
*fail-closed*: if the proxy is not configured the server refuses the upstream call
rather than exposing its own IP address.

Responses are cached for 24 hours by default, because the authenticating account is
rate-limited and every avoidable request is a risk to it.
""".strip()

# Documented on the endpoint so /docs shows the whole error contract.
ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse, "description": "Missing or invalid `X-API-KEY`."},
    404: {"model": ErrorResponse, "description": "Profile not found or not visible."},
    422: {"model": ErrorResponse, "description": "`url` is not a LinkedIn `/in/` URL."},
    429: {"model": ErrorResponse, "description": "LinkedIn is rate-limiting the session."},
    500: {"model": ErrorResponse, "description": "Server misconfigured (no proxy or no cookies)."},
    502: {"model": ErrorResponse, "description": "LinkedIn session expired, or an unexpected upstream response."},
    504: {"model": ErrorResponse, "description": "Upstream LinkedIn request timed out."},
}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    install_secret_redaction(settings)
    logger.info(
        "startup: credentials=%s proxy=%s auth=%s cache_ttl=%ss",
        settings.has_credentials,
        settings.has_proxy,
        settings.auth_enforced,
        settings.CACHE_TTL_SECONDS,
    )
    if settings.direct_egress_in_use:
        logger.warning(
            "ALLOW_DIRECT_EGRESS is enabled and PROXY_URL is unset: LinkedIn traffic "
            "will leave from THIS HOST'S IP. That is fine on a residential connection "
            "and wrong on a cloud host, where LinkedIn answers datacenter IPs with "
            "HTTP 999. Set PROXY_URL before deploying."
        )
    elif not settings.has_proxy:
        logger.warning(
            "PROXY_URL is not set — /profile will fail closed rather than send "
            "LinkedIn traffic from this host's IP address."
        )
    if not settings.has_credentials:
        logger.warning("LI_AT / LI_JSESSIONID are not set — /profile cannot fetch.")
    yield


app = FastAPI(
    title="LinkedIn Profile API",
    version=__version__,
    description=DESCRIPTION,
    lifespan=lifespan,
)


# =============================================================================
# dependencies (overridden in tests via app.dependency_overrides)
# =============================================================================


def get_linkedin_client(settings: Settings = Depends(get_settings)) -> LinkedInClient:
    return LinkedInClient(settings=settings)


# =============================================================================
# routes
# =============================================================================


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")


@app.get("/health", response_model=HealthResponse, tags=["meta"], summary="Liveness probe")
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.get("/readiness", tags=["meta"], summary="Configuration report (no secret values)")
def readiness(
    settings: Settings = Depends(get_settings),
    cache: ProfileCache = Depends(get_profile_cache),
) -> dict[str, object]:
    """Booleans only — useful for confirming a deploy's env without exposing it."""
    return {
        "status": "ok",
        "version": __version__,
        "credentials_configured": settings.has_credentials,
        "proxy_configured": settings.has_proxy,
        # True only in the unsafe local-development mode; must be false in production.
        "direct_egress_in_use": settings.direct_egress_in_use,
        "api_key_enforced": settings.auth_enforced,
        "impersonate": settings.IMPERSONATE,
        "dash_fallback_enabled": settings.dash_enabled,
        "ready_for_live_requests": settings.has_credentials and settings.egress_allowed,
        "cache": cache.stats(),
    }


@app.get(
    "/profile",
    response_model=Profile,
    tags=["profile"],
    summary="Fetch and parse a LinkedIn profile",
    responses=ERROR_RESPONSES,
    dependencies=[Depends(require_api_key)],
)
def get_profile(
    url: str = Query(
        ...,
        description="A LinkedIn member profile URL, e.g. `https://www.linkedin.com/in/some-slug/`",
        examples=["https://www.linkedin.com/in/williamhgates/"],
    ),
    client: LinkedInClient = Depends(get_linkedin_client),
    cache: ProfileCache = Depends(get_profile_cache),
) -> Profile:
    # Reject non-profile URLs before spending an upstream request on them.
    try:
        public_id = extract_public_id(url)
    except InvalidProfileUrl as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None

    cached = cache.get(public_id)
    if cached is not None:
        logger.info("profile cache hit public_id=%s", public_id)
        return cached

    try:
        raw = client.fetch_raw(public_id)
    except LinkedInError as exc:
        # Typed exceptions carry a fixed, caller-safe message; nothing from the
        # upstream error string (which can contain proxy credentials) is reused.
        logger.warning(
            "profile fetch failed public_id=%s error=%s status=%s",
            public_id,
            type(exc).__name__,
            exc.status_code,
        )
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None

    # `parse_profile` never raises: a partial profile becomes `warnings[]`, not a 500.
    profile = parse_profile(raw, public_id)
    cache.set(public_id, profile)
    logger.info(
        "profile fetched public_id=%s experience=%d education=%d skills=%d warnings=%d",
        public_id,
        len(profile.experience),
        len(profile.education),
        len(profile.skills),
        len(profile.warnings),
    )
    return profile


# =============================================================================
# catch-all: never let an unexpected error render a stack trace to the caller
# =============================================================================


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # The redaction filter scrubs secrets out of this traceback before it is written.
    logger.exception("unhandled error path=%s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error."},
    )
