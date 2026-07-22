"""Abstract base class all device implementations must conform to.

The measurement loop is fully device-agnostic — it only ever calls the three
methods defined here. To support a new plug or PDU, add one module in this
package implementing BaseDevice and register it in DEVICE_TYPES (__init__.py).
"""

from abc import ABC, abstractmethod


class BaseDevice(ABC):

    @abstractmethod
    async def connect(self, ip: str, **credentials) -> bool:
        """Connect / authenticate. Returns True or raises on failure."""
        ...

    @abstractmethod
    async def get_power_mw(self) -> float:
        """Return current power draw in milliwatts (mW)."""
        ...

    @abstractmethod
    async def disconnect(self):
        """Clean up open connections / sessions."""
        ...
