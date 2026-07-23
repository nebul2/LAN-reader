"""Shelly smart plug implementation of BaseDevice.

Uses direct async HTTP — requires the optional aiohttp dependency
(pip install 'measure[shelly]').

Supports:
  Gen1  (Shelly Plug S, Plug US)  — GET /meter/0
  Gen2  (Shelly Plus Plug S, Pro) — GET /rpc/Switch.GetStatus?id=0

Generation is auto-detected on connect().
Credentials are optional (many Shelly devices ship with no auth).
"""

import aiohttp

from lem.devices.base import BaseDevice


class ShellyDevice(BaseDevice):

    def __init__(self):
        self._ip = None
        self._auth = None   # aiohttp.BasicAuth or None
        self._gen = None    # 1 or 2
        self._session = None

    async def connect(self, ip: str, username: str = None, password: str = None) -> bool:
        self._ip = ip
        self._auth = aiohttp.BasicAuth(username, password) if username and password else None

        if self._session and not self._session.closed:
            await self._session.close()
        self._session = aiohttp.ClientSession()

        timeout = aiohttp.ClientTimeout(total=5)

        # Try Gen2 first
        try:
            async with self._session.get(
                f"http://{ip}/rpc/Shelly.GetStatus", auth=self._auth, timeout=timeout
            ) as r:
                if r.status == 200:
                    self._gen = 2
                    return True
        except Exception:
            pass

        # Fall back to Gen1
        try:
            async with self._session.get(
                f"http://{ip}/status", auth=self._auth, timeout=timeout
            ) as r:
                if r.status == 200:
                    self._gen = 1
                    return True
        except Exception:
            pass

        raise ConnectionError(f"Could not reach Shelly device at {ip}")

    async def get_power_mw(self) -> float:
        timeout = aiohttp.ClientTimeout(total=5)
        if self._gen == 2:
            url = f"http://{self._ip}/rpc/Switch.GetStatus?id=0"
        else:
            url = f"http://{self._ip}/meter/0"

        async with self._session.get(url, auth=self._auth, timeout=timeout) as r:
            data = await r.json(content_type=None)

        watts = data.get("apower" if self._gen == 2 else "power", 0.0)
        return round(float(watts) * 1000, 2)

    async def disconnect(self):
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None
