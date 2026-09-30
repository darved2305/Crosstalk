import time
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ClockPair:
    realtime_ns: int
    monotonic_ns: int


def clock_pair() -> ClockPair:
    return ClockPair(time.time_ns(), time.monotonic_ns())
