"""Runtime configuration, read from the environment (or a local `.env`).

Every field is optional so the app imports and boots on a machine with no secrets
at all — that is what makes Phases 0-5 verifiable entirely offline. Anything that
genuinely needs a secret fails at call time with a precise error, not at import.
"""

from __future__ import annotations

import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# A *current* Chrome UA. LinkedIn scores stale UAs (e.g. Chrome 83) as bot traffic,
# and it must stay consistent with the curl_cffi impersonation target below.
CHROME_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Set by tests so a developer's local `.env` can never bleed into the offline suite.
DISABLE_DOTENV_ENV_VAR = "LINKEDIN_DISABLE_DOTENV"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- LinkedIn session (throwaway account) ---
    LI_AT: str = ""
    LI_JSESSIONID: str = ""

    # --- Egress ---
    # Residential/mobile proxy. Required for live calls; the client fails closed.
    PROXY_URL: str = ""

    # LOCAL DEVELOPMENT ONLY. Sends LinkedIn traffic from this host's own IP.
    # Safe on a home/residential connection; on a cloud host it exposes a datacenter
    # IP, which LinkedIn answers with HTTP 999 and which risks the account. Default
    # false so a deployment that forgets PROXY_URL fails closed instead of leaking.
    ALLOW_DIRECT_EGRESS: bool = False

    # --- Auth for this API ---
    API_KEY: str = ""

    # --- Tuning ---
    CACHE_TTL_SECONDS: int = 86_400
    IMPERSONATE: str = "chrome"
    REQUEST_TIMEOUT_SECONDS: int = 20
    MAX_RETRIES: int = 3
    USER_AGENT: str = CHROME_USER_AGENT

    # --- Endpoint strategy ---
    PROFILE_VIEW_ENABLED: bool = True
    # Captured from a live browser request; enables the dash/profiles fallback.
    DASH_DECORATION_ID: str = ""

    def __init__(self, **kwargs: object) -> None:
        if os.getenv(DISABLE_DOTENV_ENV_VAR) == "1":
            kwargs.setdefault("_env_file", None)
        super().__init__(**kwargs)  # type: ignore[arg-type]

    # --- Derived helpers ---

    @property
    def csrf_token(self) -> str:
        """LinkedIn's csrf-token header is the JSESSIONID value without its quotes."""
        return self.LI_JSESSIONID.strip().strip('"')

    @property
    def jsessionid_cookie(self) -> str:
        """The JSESSIONID *cookie* keeps its quotes, unlike the csrf-token header.

        Re-adds them if the value was pasted unquoted — the single most common
        copy-paste mistake when lifting the cookie out of DevTools.
        """
        value = self.LI_JSESSIONID.strip()
        if value and not value.startswith('"'):
            value = f'"{value}"'
        return value

    @property
    def has_credentials(self) -> bool:
        return bool(self.LI_AT and self.LI_JSESSIONID)

    @property
    def has_proxy(self) -> bool:
        return bool(self.PROXY_URL.strip())

    @property
    def egress_allowed(self) -> bool:
        """Whether outbound LinkedIn traffic is permitted at all.

        A proxy is the supported path; `ALLOW_DIRECT_EGRESS` is a deliberate,
        explicitly-opted-into escape hatch for local development.
        """
        return self.has_proxy or self.ALLOW_DIRECT_EGRESS

    @property
    def direct_egress_in_use(self) -> bool:
        return self.ALLOW_DIRECT_EGRESS and not self.has_proxy

    @property
    def auth_enforced(self) -> bool:
        """API-key auth is only enforced when an API_KEY is configured."""
        return bool(self.API_KEY.strip())

    @property
    def dash_enabled(self) -> bool:
        return bool(self.DASH_DECORATION_ID.strip())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton (used by the app; tests build their own)."""
    return Settings()


def reset_settings_cache() -> None:
    """Drop the singleton so a test can change the environment and re-read it."""
    get_settings.cache_clear()
