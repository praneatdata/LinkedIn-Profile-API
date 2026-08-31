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
    def has_credentials(self) -> bool:
        return bool(self.LI_AT and self.LI_JSESSIONID)

    @property
    def has_proxy(self) -> bool:
        return bool(self.PROXY_URL.strip())

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
