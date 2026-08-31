"""Scrub secrets out of log records.

The requirement is that cookies and proxy credentials never appear in an error body
*or a log*. Error bodies are handled by construction (typed exceptions carry fixed,
caller-safe messages). Logs need a belt-and-braces filter, because libcurl embeds the
full proxy URL — password included — in its error strings, and any unexpected
traceback could carry one into the log stream.

Installing this as a *handler* filter means it also covers records that merely
propagate to the handler, including uvicorn's.
"""

from __future__ import annotations

import logging

REDACTED = "***REDACTED***"

# Below this length a "secret" is too generic to substitute safely.
_MIN_SECRET_LENGTH = 6


class SecretRedactingFilter(logging.Filter):
    """Replaces every configured secret with `***REDACTED***` in message and traceback."""

    def __init__(self, secrets: list[str]) -> None:
        super().__init__()
        # Longest first, so a proxy URL is replaced before its embedded password.
        self._secrets = sorted(
            {s for s in secrets if s and len(s) >= _MIN_SECRET_LENGTH},
            key=len,
            reverse=True,
        )

    def _scrub(self, text: str) -> str:
        for secret in self._secrets:
            if secret in text:
                text = text.replace(secret, REDACTED)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._secrets:
            return True

        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - malformed log call
            message = str(record.msg)

        scrubbed = self._scrub(message)
        if scrubbed != message or record.args:
            # Collapse args into the message so no unscrubbed arg is re-interpolated.
            record.msg = scrubbed
            record.args = ()

        if record.exc_info:
            formatter = logging.Formatter()
            record.exc_text = self._scrub(formatter.formatException(record.exc_info))
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = self._scrub(record.exc_text)

        if record.stack_info:
            record.stack_info = self._scrub(record.stack_info)

        return True


def secrets_from_settings(settings: object) -> list[str]:
    """Every value that must never be logged, plus the variants we derive from them."""
    values: list[str] = []
    for attr in ("PROXY_URL", "LI_AT", "LI_JSESSIONID", "API_KEY"):
        raw = getattr(settings, attr, "") or ""
        raw = raw.strip()
        if raw:
            values.append(raw)
            unquoted = raw.strip('"')
            if unquoted != raw:
                values.append(unquoted)

    # The proxy's userinfo section on its own — it can surface without the scheme.
    proxy = (getattr(settings, "PROXY_URL", "") or "").strip()
    if "@" in proxy:
        userinfo = proxy.split("://", 1)[-1].split("@", 1)[0]
        values.append(userinfo)
        for part in userinfo.split(":"):
            values.append(part)

    return values


_LOGGER_NAMES = ("", "uvicorn", "uvicorn.error", "uvicorn.access", "app")


def install_secret_redaction(settings: object) -> SecretRedactingFilter:
    """Attach the filter to every handler that could emit application log records."""
    log_filter = SecretRedactingFilter(secrets_from_settings(settings))

    for name in _LOGGER_NAMES:
        logger = logging.getLogger(name)
        # A filter on the logger catches records it emits directly...
        if not any(isinstance(f, SecretRedactingFilter) for f in logger.filters):
            logger.addFilter(log_filter)
        # ...and one on each handler catches everything that propagates to it.
        for handler in logger.handlers:
            if not any(isinstance(f, SecretRedactingFilter) for f in handler.filters):
                handler.addFilter(log_filter)

    return log_filter
