"""Log-redaction tests.

The acceptance criterion is that no cookie or proxy credential appears in an error
body *or a log*. Bodies are covered in test_api/test_client; this file covers logs.
"""

from __future__ import annotations

import logging

from app.config import Settings
from app.redaction import REDACTED, SecretRedactingFilter, secrets_from_settings

PROXY = "socks5h://proxyuser:sup3r-s3cret@residential.example.net:1080"
LI_AT = "AQEDATfakeCookieValue1234567890"
JSESSIONID = '"ajax:9876543210"'
API_KEY = "api-key-abcdef123456"

SECRET_SUBSTRINGS = (
    "sup3r-s3cret",
    "proxyuser",
    LI_AT,
    "ajax:9876543210",
    API_KEY,
)


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        PROXY_URL=PROXY,
        LI_AT=LI_AT,
        LI_JSESSIONID=JSESSIONID,
        API_KEY=API_KEY,
    )


def _render(record: logging.LogRecord) -> str:
    return logging.Formatter("%(message)s").format(record)


def _record(msg: str, *args: object, exc_info: object = None) -> logging.LogRecord:
    return logging.LogRecord(
        name="app.test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=exc_info,  # type: ignore[arg-type]
    )


def test_secrets_are_collected_from_settings() -> None:
    collected = secrets_from_settings(_settings())
    assert PROXY in collected
    assert LI_AT in collected
    assert "ajax:9876543210" in collected  # unquoted variant too
    assert "proxyuser:sup3r-s3cret" in collected  # userinfo section
    assert "sup3r-s3cret" in collected  # and the password alone
    assert API_KEY in collected


def test_message_is_scrubbed() -> None:
    log_filter = SecretRedactingFilter(secrets_from_settings(_settings()))
    record = _record(f"connect failed via {PROXY}")
    assert log_filter.filter(record) is True

    rendered = _render(record)
    assert REDACTED in rendered
    for secret in SECRET_SUBSTRINGS:
        assert secret not in rendered


def test_interpolated_args_are_scrubbed() -> None:
    log_filter = SecretRedactingFilter(secrets_from_settings(_settings()))
    record = _record("proxy=%s cookie=%s", PROXY, LI_AT)
    log_filter.filter(record)

    rendered = _render(record)
    for secret in SECRET_SUBSTRINGS:
        assert secret not in rendered


def test_traceback_is_scrubbed() -> None:
    log_filter = SecretRedactingFilter(secrets_from_settings(_settings()))
    try:
        raise RuntimeError(f"curl: (7) could not connect to {PROXY}")
    except RuntimeError:
        import sys

        record = _record("boom", exc_info=sys.exc_info())

    log_filter.filter(record)
    rendered = logging.Formatter("%(message)s").format(record)
    assert "Traceback" in rendered  # the traceback is preserved...
    for secret in SECRET_SUBSTRINGS:
        assert secret not in rendered  # ...but scrubbed
    assert REDACTED in rendered


def test_nothing_is_touched_when_no_secrets_are_configured() -> None:
    log_filter = SecretRedactingFilter(secrets_from_settings(Settings(_env_file=None)))
    record = _record("ordinary message with no secrets")
    log_filter.filter(record)
    assert _render(record) == "ordinary message with no secrets"


def test_short_values_are_not_used_as_redaction_patterns() -> None:
    """Redacting a 2-char 'secret' would corrupt every log line it appears in."""
    log_filter = SecretRedactingFilter(["ab", "x"])
    record = _record("a table of absolute values")
    log_filter.filter(record)
    assert _render(record) == "a table of absolute values"


def test_longest_secret_wins_so_the_proxy_url_is_replaced_whole() -> None:
    log_filter = SecretRedactingFilter(secrets_from_settings(_settings()))
    record = _record(f"using {PROXY} now")
    log_filter.filter(record)
    assert _render(record) == f"using {REDACTED} now"


def test_filter_installation_is_idempotent() -> None:
    from app.redaction import install_secret_redaction

    logger = logging.getLogger("app")
    before = len(logger.filters)
    install_secret_redaction(_settings())
    install_secret_redaction(_settings())
    after = len(logger.filters)
    assert after - before <= 1
