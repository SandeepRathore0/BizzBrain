"""Injectable Clock and Randomness interfaces for testability.

Ensures no real sleep or unseeded/uncontrolled time/randomness is needed in tests.
"""
import random
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Optional


class Clock(ABC):
    @abstractmethod
    def now(self) -> datetime:
        """Return current time in UTC."""
        raise NotImplementedError


class SystemClock(Clock):
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class TestClock(Clock):
    __test__ = False

    def __init__(self, initial_time: Optional[datetime] = None):
        if initial_time is None:
            self._current_time = datetime(2026, 11, 9, 12, 0, 0, tzinfo=timezone.utc)
        else:
            if initial_time.tzinfo is None:
                self._current_time = initial_time.replace(tzinfo=timezone.utc)
            else:
                self._current_time = initial_time.astimezone(timezone.utc)

    def now(self) -> datetime:
        return self._current_time

    def advance(self, seconds: float = 0, minutes: float = 0, hours: float = 0, days: float = 0) -> datetime:
        delta = timedelta(seconds=seconds, minutes=minutes, hours=hours, days=days)
        self._current_time += delta
        return self._current_time

    def set_time(self, new_time: datetime) -> None:
        if new_time.tzinfo is None:
            self._current_time = new_time.replace(tzinfo=timezone.utc)
        else:
            self._current_time = new_time.astimezone(timezone.utc)


class RandomSource(ABC):
    @abstractmethod
    def uniform(self, a: float, b: float) -> float:
        raise NotImplementedError

    @abstractmethod
    def randint(self, a: int, b: int) -> int:
        raise NotImplementedError


class SystemRandomSource(RandomSource):
    def uniform(self, a: float, b: float) -> float:
        return random.uniform(a, b)

    def randint(self, a: int, b: int) -> int:
        return random.randint(a, b)


class ControlledRandomSource(RandomSource):
    def __init__(self, fixed_value: Optional[float] = None):
        self.fixed_value = fixed_value

    def uniform(self, a: float, b: float) -> float:
        if self.fixed_value is not None:
            return min(max(self.fixed_value, a), b)
        return (a + b) / 2.0

    def randint(self, a: int, b: int) -> int:
        if self.fixed_value is not None:
            return int(min(max(self.fixed_value, a), b))
        return (a + b) // 2


_default_clock: Clock = SystemClock()
_default_random: RandomSource = SystemRandomSource()


def get_clock() -> Clock:
    return _default_clock


def set_clock(clock: Clock) -> None:
    global _default_clock
    _default_clock = clock


def get_random_source() -> RandomSource:
    return _default_random


def set_random_source(random_source: RandomSource) -> None:
    global _default_random
    _default_random = random_source
