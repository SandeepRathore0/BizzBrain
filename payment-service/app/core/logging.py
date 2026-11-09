import logging
import sys
from typing import Any
import structlog
from structlog.types import EventDict

SENSITIVE_KEYS = {
    "password",
    "secret",
    "token",
    "authorization",
    "access_token",
    "client_secret",
    "card_number",
    "cvv",
    "cvc",
    "pay_token",
}


def mask_sensitive_data(_, __, event_dict: EventDict) -> EventDict:
    """Recursively scrub sensitive keys and tokens from log event dictionaries."""
    def _scrub(val: Any) -> Any:
        if isinstance(val, dict):
            scrubbed = {}
            for k, v in val.items():
                if any(s in k.lower() for s in SENSITIVE_KEYS):
                    scrubbed[k] = "[REDACTED]"
                else:
                    scrubbed[k] = _scrub(v)
            return scrubbed
        elif isinstance(val, list):
            return [_scrub(item) for item in val]
        return val

    return _scrub(event_dict)


def setup_logging(log_level: str = "INFO") -> None:
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, log_level.upper(), logging.INFO),
    )

    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        mask_sensitive_data,
        structlog.processors.JSONRenderer(),
    ]

    structlog.configure(
        processors=processors,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "payment_service") -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
