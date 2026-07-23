"""TP-Link Tapo P110 implementation of BaseDevice.

current_power from get_energy_usage() is an integer number of milliwatts.
"""

import asyncio

from tapo import ApiClient

from lem.devices.base import BaseDevice


class TapoDevice(BaseDevice):

    def __init__(self):
        self._device = None
        self._ip = None

    async def connect(self, ip: str, username: str = "", password: str = "") -> bool:
        if not username or not password:
            raise ValueError("Tapo requires username and password.")
        self._ip = ip
        client = ApiClient(username, password)
        self._device = await asyncio.wait_for(client.p110(ip), timeout=5)
        # Verify connection with a live call
        await asyncio.wait_for(self._device.get_device_info_json(), timeout=5)
        return True

    async def get_power_mw(self) -> float:
        energy = await asyncio.wait_for(self._device.get_energy_usage(), timeout=5)
        return float(energy.current_power)

    async def disconnect(self):
        self._device = None
