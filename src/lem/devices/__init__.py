"""Device registry. Factories are lazy so optional deps (aiohttp for shelly)
are only required when that device type is actually used."""

from lem.devices.base import BaseDevice


def _tapo() -> BaseDevice:
    from lem.devices.tapo import TapoDevice
    return TapoDevice()


def _shelly() -> BaseDevice:
    from lem.devices.shelly import ShellyDevice
    return ShellyDevice()


def _fake() -> BaseDevice:
    from lem.devices.fake import FakeDevice
    return FakeDevice()


DEVICE_TYPES = {
    "tapo": _tapo,
    "shelly": _shelly,
    "fake": _fake,
}


def make_device(device_type: str) -> BaseDevice:
    try:
        factory = DEVICE_TYPES[device_type]
    except KeyError:
        raise ValueError(
            f"Unknown device type '{device_type}' (known: {', '.join(DEVICE_TYPES)})"
        ) from None
    return factory()
