"""Kill-safe CSV sink.

Writes each sample to a per-plug CSV (timestamp,power_w) and a combined
long-format CSV (timestamp,alias,power_w), flushing after every row so a
crash or kill -9 loses at most the in-flight sample.

Power is watts at 3-decimal precision — lossless for the P110, which reports
integer milliwatts. Timestamps are UTC ISO 8601, loadable straight into REM's
gos_rem TIMESTAMPTZ column.
"""

import csv
from pathlib import Path

from measure.model import Sample
from measure.sinks.base import BaseSink


class CsvSink(BaseSink):

    def __init__(self, results_dir: Path):
        self._dir = results_dir
        self._plug_files: dict[str, tuple[object, csv.writer]] = {}
        self._combined = None
        self._combined_writer = None
        self.paths: list[Path] = []

    def _open_csv(self, path: Path, header: list[str]):
        f = open(path, "a", newline="")
        writer = csv.writer(f)
        if f.tell() == 0:
            writer.writerow(header)
            f.flush()
        return f, writer

    async def open(self, run_name: str, plug_aliases: list[str]) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        for alias in plug_aliases:
            path = self._dir / f"{run_name}_{alias}.csv"
            self._plug_files[alias] = self._open_csv(path, ["timestamp", "power_w"])
            self.paths.append(path)
        combined_path = self._dir / f"{run_name}_combined.csv"
        self._combined, self._combined_writer = self._open_csv(
            combined_path, ["timestamp", "alias", "power_w"]
        )
        self.paths.append(combined_path)

    async def write(self, sample: Sample) -> None:
        ts = sample.ts.isoformat(timespec="milliseconds")
        power = f"{sample.power_w:.3f}"
        f, writer = self._plug_files[sample.alias]
        writer.writerow([ts, power])
        f.flush()
        self._combined_writer.writerow([ts, sample.alias, power])
        self._combined.flush()

    async def close(self) -> None:
        for f, _ in self._plug_files.values():
            f.close()
        if self._combined:
            self._combined.close()
        self._plug_files.clear()
        self._combined = self._combined_writer = None
