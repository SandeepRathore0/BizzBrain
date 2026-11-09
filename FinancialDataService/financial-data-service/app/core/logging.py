import logging
import sys
from typing import Any, MutableMapping
import structlog
from structlog.types import EventDict, Processor


SENSITIVE_KEYS = {
    "password",
    "password_hash",
    "token",
    "access_token",
    "refresh_token",
    "authorization",
    "secret",
    "client_secret",
    "service_client_secret",
    "card",
    "cvv",
}


def redact_sensitive_data(
    logger: Any, method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    for key, value in list(event_dict.items()):
        if any(sens in key.lower() for sens in SENSITIVE_KEYS):
            event_dict[key] = "[REDACTED]"
        elif isinstance(value, dict):
            event_dict[key] = _redact_dict(value)
    return event_dict


def _redact_dict(d: dict[str, Any]) -> dict[str, Any]:
    redacted = {}
    for k, v in d.items():
        if any(sens in k.lower() for sens in SENSITIVE_KEYS):
            redacted[k] = "[REDACTED]"
        elif isinstance(v, dict):
            redacted[k] = _redact_dict(v)
        else:
            redacted[k] = v
    return redacted


def setup_logging(log_level: str = "INFO") -> None:
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, log_level.upper(), logging.INFO),
    )

    processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        redact_sensitive_data,
        structlog.processors.JSONRenderer(),
    ]

    structlog.configure(
        processors=processors,
        logger_factory=structlog.PrintLoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper(), logging.INFO)
        ),
        cache_logger_on_first_use=True,
    )


logger = structlog.get_logger()
