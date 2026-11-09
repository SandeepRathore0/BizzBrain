import logging
import sys
from typing import Any, Dict
from contextvars import ContextVar
import structlog

# Context variable for tracing request_id
request_id_ctx: ContextVar[str] = ContextVar("request_id", default="")

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
    "pay_token",
    "card_number",
    "cvv",
}


def redact_sensitive_data(logger, method_name, event_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Redact sensitive fields from logging context recursively."""
    def _redact(val: Any) -> Any:
        if isinstance(val, dict):
            return {
                k: ("[REDACTED]" if k.lower() in SENSITIVE_KEYS else _redact(v))
                for k, v in val.items()
            }
        elif isinstance(val, list):
            return [_redact(item) for item in val]
        return val

    return _redact(event_dict)


def add_request_id(logger, method_name, event_dict: Dict[str, Any]) -> Dict[str, Any]:
    req_id = request_id_ctx.get()
    if req_id and "request_id" not in event_dict:
        event_dict["request_id"] = req_id
    return event_dict


def setup_logging(debug: bool = False) -> None:
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.DEBUG if debug else logging.INFO,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            add_request_id,
            redact_sensitive_data,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.DEBUG if debug else logging.INFO
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


logger = structlog.get_logger()
