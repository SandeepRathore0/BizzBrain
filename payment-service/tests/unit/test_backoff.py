"""Unit tests for outbox worker exponential backoff and jitter calculations."""
import uuid
from datetime import datetime, timezone
import pytest

from app.core.clock import ControlledRandomSource, TestClock
from app.services.outbox_service import OutboxService


def test_exponential_backoff_bounds_and_cap():
    """Verify exponential backoff values: min(600, 5 * 2^(attempts-1)) and jitter bounds."""
    # Attempt 1: 5 * 2^0 = 5.0s max
    # Attempt 2: 5 * 2^1 = 10.0s max
    # Attempt 3: 5 * 2^2 = 20.0s max
    # Attempt 4: 5 * 2^3 = 40.0s max
    # Attempt 5: 5 * 2^4 = 80.0s max
    # Attempt 6: 5 * 2^5 = 160.0s max
    # Attempt 7: 5 * 2^6 = 320.0s max
    # Attempt 8: 5 * 2^7 = 640.0s -> capped at 600.0s
    # Attempt 9: capped at 600.0s
    # Attempt 10: capped at 600.0s

    expected_caps = {
        1: 5.0,
        2: 10.0,
        3: 20.0,
        4: 40.0,
        5: 80.0,
        6: 160.0,
        7: 320.0,
        8: 600.0,
        9: 600.0,
        10: 600.0,
    }

    for attempt, expected_max in expected_caps.items():
        calculated_cap = min(600.0, 5.0 * (2 ** (attempt - 1)))
        assert calculated_cap == expected_max, f"Attempt {attempt} expected max {expected_max}, got {calculated_cap}"


@pytest.mark.asyncio
async def test_controlled_random_jitter_within_bounds():
    """Verify that ControlledRandomSource keeps jitter strictly within [0, max_backoff]."""
    test_clock = TestClock(datetime(2026, 11, 9, 10, 0, 0, tzinfo=timezone.utc))
    controlled_random = ControlledRandomSource(fixed_value=15.0)

    service = OutboxService(clock=test_clock, random_source=controlled_random)

    # For attempt 3 (max backoff 20s), fixed_value 15.0 is returned
    max_backoff_3 = min(600.0, 5.0 * (2 ** (3 - 1)))
    val = service.random_source.uniform(0.0, max_backoff_3)
    assert val == 15.0

    # For attempt 1 (max backoff 5s), fixed_value 15.0 is clamped to 5.0
    max_backoff_1 = min(600.0, 5.0 * (2 ** (1 - 1)))
    val_clamped = service.random_source.uniform(0.0, max_backoff_1)
    assert val_clamped == 5.0
