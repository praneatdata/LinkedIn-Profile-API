"""Typed failures, each carrying the HTTP status and the *caller-safe* message.

Keeping the status and the public message on the exception means the API layer maps
errors by data rather than by a growing if/elif chain — and it means no message can
ever be assembled from a cookie, a proxy URL, or an upstream exception string.
"""

from __future__ import annotations


class LinkedInError(Exception):
    """Base class for every LinkedIn integration failure."""

    status_code: int = 502
    detail: str = "Upstream LinkedIn request failed."
    #: Whether retrying the same request could plausibly succeed.
    retryable: bool = False

    def __init__(self, detail: str | None = None) -> None:
        self.detail = detail or self.__class__.detail
        super().__init__(self.detail)


class InvalidProfileUrl(LinkedInError):
    status_code = 422
    detail = (
        "`url` must be a LinkedIn profile URL of the form "
        "https://www.linkedin.com/in/<public-identifier>"
    )


class ProfileNotFound(LinkedInError):
    status_code = 404
    detail = (
        "Profile not found. It may not exist, may have been renamed, or may not be "
        "visible to the authenticated session."
    )


class AuthExpired(LinkedInError):
    status_code = 502
    detail = (
        "LinkedIn session expired or was rejected — refresh the li_at and JSESSIONID "
        "cookies and redeploy."
    )


class RateLimited(LinkedInError):
    status_code = 429
    detail = (
        "LinkedIn is rate-limiting this session. Back off before retrying; the account "
        "may be temporarily restricted."
    )
    retryable = True


class ProxyRequired(LinkedInError):
    status_code = 500
    detail = (
        "Server is not configured for outbound LinkedIn traffic: PROXY_URL is unset. "
        "The request was refused rather than sent from the host IP address."
    )


class CredentialsMissing(LinkedInError):
    status_code = 500
    detail = (
        "Server is missing LinkedIn credentials: set LI_AT and LI_JSESSIONID in the "
        "environment."
    )


class UpstreamError(LinkedInError):
    status_code = 502
    detail = "LinkedIn returned an unexpected response."


class UpstreamTimeout(UpstreamError):
    status_code = 504
    detail = "The request to LinkedIn timed out."
    retryable = True
