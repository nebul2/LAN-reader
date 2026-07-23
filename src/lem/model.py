"""Data model shared by the runner, display, and sinks.

Sample is shaped to match REM's gos_rem(time, alias, power_watts) table so a
future REM sink needs no conversion beyond mW -> W.
"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Sample:
    ts: datetime          # timezone-aware UTC
    alias: str
    power_mw: float

    @property
    def power_w(self) -> float:
        return self.power_mw / 1000.0


@dataclass
class PlugState:
    """Mutable per-plug status shared between its polling task and the display.

    Safe without locks: all access happens on the single asyncio thread.
    """
    alias: str
    ip: str
    status: str = "connecting"
    last_power_mw: float | None = None
    sample_count: int = 0
    last_error: str = ""
    min_mw: float = field(default=float("inf"))
    max_mw: float = field(default=float("-inf"))
    sum_mw: float = 0.0

    def record(self, sample: Sample) -> None:
        self.last_power_mw = sample.power_mw
        self.sample_count += 1
        self.min_mw = min(self.min_mw, sample.power_mw)
        self.max_mw = max(self.max_mw, sample.power_mw)
        self.sum_mw += sample.power_mw
        self.status = "ok"
        self.last_error = ""

    @property
    def mean_mw(self) -> float | None:
        return self.sum_mw / self.sample_count if self.sample_count else None
