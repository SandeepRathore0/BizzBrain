"""Centralized alerting helper for BizBrain Payment Service.

Writes structured JSON log lines at ERROR level with alert=True, alert_code,
and context identifiers (request_id, payment_id, etc.) while scrubbing secrets.
Provides an interface and test hooks for asserting alert emissions.
"""
from typing import Any, Callable, Optional
import structlog

logger = structlog.get_logger("alerts")

_alert_listeners: list[Callable[[str, dict[str, Any]], None]] = []


class AlertNotifier:
    def notify(self, code: str, **fields: Any) -> None:
        raise NotImplementedError


class StructuredLogNotifier(AlertNotifier):
    def notify(self, code: str, **fields: Any) -> None:
        # Scrub any possible secrets
        sanitized = {
            k: v for k, v in fields.items()
            if not any(s in k.lower() for s in ("token", "secret", "password", "key", "auth", "credential"))
        }
        logger.error(
            "alert_emitted",
            alert=True,
            alert_code=code,
            **sanitized,
        )
        for listener in _alert_listeners:
            try:
                listener(code, sanitized)
            except Exception:
                pass


_default_notifier: AlertNotifier = StructuredLogNotifier()


def set_notifier(notifier: AlertNotifier) -> None:
    global _default_notifier
    _default_notifier = notifier


def register_alert_listener(listener: Callable[[str, dict[str, Any]], None]) -> None:
    _alert_listeners.append(listener)


def unregister_alert_listener(listener: Callable[[str, dict[str, Any]], None]) -> None:
    if listener in _alert_listeners:
        _alert_listeners.remove(listener)


def clear_alert_listeners() -> None:
    _alert_listeners.clear()


def alert(code: str, **fields: Any) -> None:
    _default_notifier.notify(code, **fields)
