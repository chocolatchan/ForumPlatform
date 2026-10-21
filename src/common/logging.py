"""Logging utilities with sensitive field redaction."""

import logging
import re

SENSITIVE_PATTERNS = [
    re.compile(
        r'("(?:password|otp|token|secret|access_token|csrf_token|forum_session)"\s*:\s*")[^"]+(")',
        re.IGNORECASE,
    ),
    re.compile(r"(authorization:\s*bearer\s+)[^\s,]+", re.IGNORECASE),
    re.compile(r"(cookie:\s*)[^\r\n]+", re.IGNORECASE),
]


class SensitiveDataFilter(logging.Filter):
    """Redacts sensitive credentials and tokens from log messages."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            for pattern in SENSITIVE_PATTERNS:
                record.msg = pattern.sub(
                    r"\1[REDACTED]\2" if r"\2" in pattern.pattern else r"\1[REDACTED]", record.msg
                )
        return True


def configure_logging(service_name: str, level: str = "INFO") -> None:
    """Configure standard service logging with redaction filters."""
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        f"[%(asctime)s] [%(levelname)s] [{service_name}] [%(name)s]: %(message)s"
    )
    handler.setFormatter(formatter)
    handler.addFilter(SensitiveDataFilter())

    # Avoid duplicate handlers
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
