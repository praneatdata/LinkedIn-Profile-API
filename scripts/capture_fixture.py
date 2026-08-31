#!/usr/bin/env python3
"""Hit Voyager **once** and save the raw JSON as a test fixture.

    python scripts/capture_fixture.py --slug some-public-slug
    python scripts/capture_fixture.py --url https://www.linkedin.com/in/some-slug/

Requires `LI_AT`, `LI_JSESSIONID` and `PROXY_URL` (see `.env.example`). One request
per invocation, by design — never loop this against LinkedIn.

Output defaults to `tests/fixtures/live_profile.json`, which is **gitignored**: a real
capture contains a real person's data and must not be committed. The committed
`tests/fixtures/sample_profile.json` is synthetic and drives the offline suite; pass
`--out` explicitly if you really do intend to overwrite it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.config import Settings  # noqa: E402
from app.linkedin.client import LinkedInClient, extract_public_id  # noqa: E402
from app.linkedin.exceptions import LinkedInError  # noqa: E402
from app.linkedin.parser import parse_profile  # noqa: E402

DEFAULT_OUT = REPO_ROOT / "tests" / "fixtures" / "live_profile.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--slug", help="public identifier, e.g. `williamhgates`")
    source.add_argument("--url", help="full profile URL")
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"where to write the raw JSON (default: {DEFAULT_OUT.relative_to(REPO_ROOT)})",
    )
    args = parser.parse_args()

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
        print(f"\N{DOUBLE VERTICAL BAR} BLOCKED: needs {', '.join(missing)} from human")
        print()
        print("  Capture the cookies from a THROWAWAY LinkedIn account:")
        print("    DevTools -> Application -> Cookies -> https://www.linkedin.com")
        print("    copy `li_at`, and `JSESSIONID` *including* its double quotes")
        print("  PROXY_URL must be a residential/mobile proxy (socks5h://user:pass@host:port);")
        print("  datacenter IPs get HTTP 999 from LinkedIn.")
        print()
        print("  Alternatively capture the payload by hand:")
        print("    open the profile in a browser -> DevTools -> Network -> filter")
        print("    `profileView` -> copy the JSON response -> save it to --out.")
        return 2

    public_id = args.slug or extract_public_id(args.url)
    print(f"Fetching profileView for {public_id!r} through the configured proxy ...")

    try:
        raw = LinkedInClient(settings=settings).fetch_raw(public_id)
    except LinkedInError as exc:
        print(f"\N{CROSS MARK} {type(exc).__name__} (HTTP {exc.status_code}): {exc.detail}")
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")

    entities = raw.get("included")
    count = len(entities) if isinstance(entities, list) else 0
    print(f"\N{HEAVY CHECK MARK} wrote {args.out} ({count} entities in `included`)")

    profile = parse_profile(raw, public_id)
    print()
    print("Parsed summary:")
    print(f"  name        : {profile.full_name}")
    print(f"  headline    : {profile.headline}")
    print(f"  location    : {profile.location.full}")
    print(f"  experience  : {len(profile.experience)}")
    print(f"  education   : {len(profile.education)}")
    print(f"  skills      : {len(profile.skills)}")
    print(f"  certs/langs : {len(profile.certifications)}/{len(profile.languages)}")
    if profile.warnings:
        print("  warnings    :")
        for warning in profile.warnings:
            print(f"    - {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
