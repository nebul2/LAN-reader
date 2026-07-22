"""Output sink interface.

The runner only ever calls these three methods, so new destinations (e.g. a
future REM sink pushing to TimescaleDB or a REM ingest endpoint) plug in
without touching the measurement loop.
"""

from abc import ABC, abstractmethod

from measure.model import Sample


class BaseSink(ABC):

    @abstractmethod
    async def open(self, run_name: str, plug_aliases: list[str]) -> None:
        ...

    @abstractmethod
    async def write(self, sample: Sample) -> None:
        ...

    @abstractmethod
    async def close(self) -> None:
        ...
