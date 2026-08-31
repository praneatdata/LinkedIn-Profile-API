"""Voyager endpoint map.

`profileView` is the primary path: a plain, stable URL with no `decorationId` to drift.
`dash/profiles` returns richer data but requires a `decorationId` captured from a live
browser session, which LinkedIn rotates — so it is an opt-in fallback, not the default.
"""

from __future__ import annotations

from urllib.parse import quote, urlencode

BASE = "https://www.linkedin.com/voyager/api"

PROFILE_VIEW = BASE + "/identity/profiles/{public_id}/profileView"
DASH_PROFILES = (
    BASE + "/identity/dash/profiles?q=memberIdentity&memberIdentity={public_id}"
    "&decorationId={deco}"
)


def profile_view_url(public_id: str) -> str:
    """Primary endpoint. The slug is percent-encoded; LinkedIn slugs may be non-ASCII."""
    return PROFILE_VIEW.format(public_id=quote(public_id, safe=""))


def dash_profiles_url(public_id: str, decoration_id: str) -> str:
    """Optional fallback; only usable with a currently-valid `decorationId`."""
    query = urlencode(
        {
            "q": "memberIdentity",
            "memberIdentity": public_id,
            "decorationId": decoration_id,
        }
    )
    return f"{BASE}/identity/dash/profiles?{query}"
