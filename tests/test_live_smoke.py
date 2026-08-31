"""Gated live test — the ONLY test that touches the real LinkedIn API.

    RUN_LIVE=1 pytest -m live -q
    RUN_LIVE=1 LIVE_TEST_SLUG=some-public-slug pytest -m live -q

Skipped by default, and skipped with a clear message when credentials or the proxy are
absent. Exactly **one** upstream request per run: the authenticating account is a
throwaway that LinkedIn will restrict if it is hammered, so this file must never loop
and must never be parameterized over multiple profiles.
"""

from __future__ import annotations

import os

import pytest

from app.config import Settings
from app.linkedin.client import LinkedInClient
from app.linkedin.parser import parse_profile

pytestmark = pytest.mark.live

# A long-standing, very public profile. Override for a profile you control.
DEFAULT_SLUG = "williamhgates"


def _require_live_environment() -> Settings:
    if os.getenv("RUN_LIVE") != "1":
        pytest.skip("set RUN_LIVE=1 to hit the real LinkedIn API")

    settings = Settings()
    missing = [
        name
        for name, present in (
            ("LI_AT", bool(settings.LI_AT)),
            ("LI_JSESSIONID", bool(settings.LI_JSESSIONID)),
            ("PROXY_URL", settings.has_proxy),
        )
        if not present
    ]
    if missing:
        pytest.skip(f"BLOCKED: needs {', '.join(missing)} from human (see .env.example)")
    return settings


def test_live_profile_fetch_returns_real_data() -> None:
    settings = _require_live_environment()
    slug = os.getenv("LIVE_TEST_SLUG", DEFAULT_SLUG)

    raw = LinkedInClient(settings=settings).fetch_raw(slug)  # <- the one request
    profile = parse_profile(raw, slug)

    print(f"\nlive profile: {slug}")
    print(f"  name       : {profile.full_name}")
    print(f"  headline   : {profile.headline}")
    print(f"  location   : {profile.location.full}")
    print(f"  experience : {len(profile.experience)}")
    print(f"  education  : {len(profile.education)}")
    print(f"  skills     : {len(profile.skills)}")
    for warning in profile.warnings:
        print(f"  warning    : {warning}")

    assert profile.full_name, "no name parsed — the payload shape may have changed"
    assert profile.public_identifier
    assert profile.linkedin_url and profile.linkedin_url.startswith(
        "https://www.linkedin.com/in/"
    )
