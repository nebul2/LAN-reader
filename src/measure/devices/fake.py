"""Synthetic device for dry runs and kill-safety testing — no hardware needed.

Produces ~40 W with a slow sine swing plus noise. Set fail_rate > 0 in the
plug's credentials to exercise the runner's retry path.
"""

import math
import random
import time

from measure.devices.base import BaseDevice


class FakeDevice(BaseDevice):

    def __init__(self):
        self._t0 = None
        self._fail_rate = 0.0

    async def connect(self, ip: str, fail_rate: float = 0.0, **_ignored) -> bool:
        self._t0 = time.monotonic()
        self._fail_rate = float(fail_rate)
        return True

    async def get_power_mw(self) -> float:
        if self._fail_rate and random.random() < self._fail_rate:
            raise TimeoutError("simulated read failure")
        t = time.monotonic() - self._t0
        return 40_000 + 5_000 * math.sin(t / 10) + random.uniform(-500, 500)

    async def disconnect(self):
        self._t0 = None
