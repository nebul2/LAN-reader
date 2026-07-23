"""Journaled uploader: streams the combined CSV to REM and catches up after
any interruption.

The combined CSV (timestamp,alias,power_w) written per-sample by CsvSink is
the source of truth. A sidecar '<combined>.sync' records the byte offset up to
which rows have been acked by REM. Live streaming, catch-up after a network
drop, and post-run backfill are all the same loop: seek to the offset, read
whole lines, POST, advance the offset. Adaptive *batching* (back off when REM
is unhappy) — never adaptive sampling, which happens independently in the
measurement loop.
"""

import asyncio
import csv
import io
import json
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from lem.rem_client import BatchAck, RemClient, RemError

BATCH_INTERVAL_S = 15.0
BATCH_INTERVAL_MAX_S = 300.0
FINAL_DRAIN_TIMEOUT_S = 3.0


@dataclass
class UploaderState:
    """Read cross-thread by the GUI/CLI display (plain attribute reads)."""
    status: str = "idle"
    rows_uploaded: int = 0
    rows_behind: int = 0
    last_error: str = ""
    cadence_s: int | None = None
    connected: bool = False


def _sidecar_path(combined_path: Path) -> Path:
    return combined_path.with_suffix(combined_path.suffix + ".sync")


def _read_sidecar(combined_path: Path) -> dict:
    try:
        return json.loads(_sidecar_path(combined_path).read_text())
    except Exception:
        return {"version": 1, "offset": 0, "rows_acked": 0}


def _write_sidecar(combined_path: Path, data: dict) -> None:
    p = _sidecar_path(combined_path)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, p)


def init_sidecar(combined_path: Path, experiment_id: str) -> None:
    """Create the sidecar at run start so a run is recognisably 'joined'."""
    if not _sidecar_path(combined_path).exists():
        _write_sidecar(combined_path, {
            "version": 1, "offset": 0, "rows_acked": 0,
            "experiment_id": experiment_id, "pending_batch_id": None,
        })


def _parse_rows(chunk: str, skip_header: bool, alias_map: dict) -> list[list]:
    """Turn CSV text into [[iso_ts, upload_alias, watts], ...]."""
    rows = []
    reader = csv.reader(io.StringIO(chunk))
    for i, row in enumerate(reader):
        if len(row) != 3:
            continue
        if skip_header and i == 0 and row[0] == "timestamp":
            continue
        ts, alias, power = row
        rows.append([ts, alias_map.get(alias, alias), float(power)])
    return rows


def _read_complete_lines(combined_path: Path, offset: int, max_lines: int) -> tuple[str, int]:
    """Read from offset up to `max_lines` complete lines; return (text,
    new_offset). A partial trailing line (mid-write) is never included."""
    with open(combined_path, "rb") as f:
        f.seek(offset)
        data = f.read()
    consumed = 0
    lines = 0
    for i, byte in enumerate(data):
        if byte == 0x0A:  # newline
            consumed = i + 1
            lines += 1
            if lines >= max_lines:
                break
    if consumed == 0:
        return "", offset
    return data[:consumed].decode("utf-8", "replace"), offset + consumed


async def _drain_once(combined_path, alias_map, client, state, covering, max_rows) -> bool:
    """Upload all currently-complete rows on disk, one batch per iteration.
    Returns True if it reached the end cleanly, False on a REM error."""
    while True:
        sidecar = _read_sidecar(combined_path)
        offset = sidecar.get("offset", 0)
        try:
            filesize = combined_path.stat().st_size
        except FileNotFoundError:
            return True
        state.rows_behind = max(0, filesize - offset)  # bytes, approximate
        if offset >= filesize:
            # Nothing new: heartbeat (throwaway id) so field sessions stay
            # alive. Never uses the pending id, which is reserved for data.
            if covering:
                await _post(client, state, [], covering, uuid.uuid4().hex)
            return True

        # One batch's worth of complete lines, so the byte offset we advance
        # corresponds exactly to the rows we send.
        text, new_offset = _read_complete_lines(combined_path, offset, max_rows)
        if not text:
            return True  # only a partial line so far
        rows = _parse_rows(text, skip_header=(offset == 0), alias_map=alias_map)
        if not rows:
            # Header-only slice: advance past it and continue.
            sidecar["offset"] = new_offset
            _write_sidecar(combined_path, sidecar)
            continue

        # Reuse the pending id across retries of this same batch so a lost ack
        # doesn't double-insert; a fresh batch always gets a fresh id.
        batch_id = sidecar.get("pending_batch_id") or uuid.uuid4().hex
        sidecar["pending_batch_id"] = batch_id
        _write_sidecar(combined_path, sidecar)

        ack = await _post(client, state, rows, covering, batch_id)
        if ack is None:
            return False
        state.rows_uploaded += ack.inserted if not ack.duplicate else 0
        if ack.cadence_s:
            state.cadence_s = ack.cadence_s
        sidecar.update(offset=new_offset, pending_batch_id=None,
                       rows_acked=sidecar.get("rows_acked", 0) + len(rows))
        _write_sidecar(combined_path, sidecar)


async def _post(client, state, rows, covering, batch_id) -> BatchAck | None:
    try:
        ack = await asyncio.to_thread(client.post_batch, rows, covering, batch_id)
        state.connected = True
        state.last_error = ""
        state.status = "streaming"
        return ack
    except RemError as e:
        state.connected = False
        state.last_error = str(e)
        state.status = "retrying"
        return None


async def run_uploader(combined_path: Path, alias_map: dict, client: RemClient,
                       state: UploaderState, stop_event: asyncio.Event,
                       covering: list[str], max_batch_rows: int = 10000) -> None:
    """Live uploader for a running measurement. Renews field sessions via the
    'covering' aliases so REM pauses cloud polling for them."""
    interval = BATCH_INTERVAL_S
    while not stop_event.is_set():
        ok = await _drain_once(combined_path, alias_map, client, state, covering, max_batch_rows)
        interval = BATCH_INTERVAL_S if ok else min(interval * 2, BATCH_INTERVAL_MAX_S)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except TimeoutError:
            pass
    # Final bounded drain so the last samples land before we return.
    try:
        await asyncio.wait_for(
            _drain_once(combined_path, alias_map, client, state, covering, max_batch_rows),
            timeout=FINAL_DRAIN_TIMEOUT_S,
        )
    except (TimeoutError, Exception):
        pass
    state.status = "stopped"


# ---------------------------------------------------------------------------
# Backfill (post-run, or explicit `lem rem sync`)
# ---------------------------------------------------------------------------

def find_unsynced(results_dir: Path) -> list[Path]:
    """Combined CSVs with a sidecar whose offset is behind the file size —
    i.e. runs that were joined to REM but not fully uploaded."""
    out = []
    for combined in sorted(results_dir.glob("*_combined.csv")):
        sidecar = _sidecar_path(combined)
        if not sidecar.exists():
            continue  # never joined — don't upload silently
        try:
            data = json.loads(sidecar.read_text())
            if data.get("offset", 0) < combined.stat().st_size:
                out.append(combined)
        except Exception:
            continue
    return out


async def sync_run(combined_path: Path, alias_map: dict, client: RemClient,
                   state: UploaderState, max_batch_rows: int = 10000) -> None:
    """Backfill one run to completion. Empty covering: pure backfill must NOT
    renew field sessions (would wrongly pause live cloud polling)."""
    while True:
        ok = await _drain_once(combined_path, alias_map, client, state, [], max_batch_rows)
        if ok:
            break
        await asyncio.sleep(BATCH_INTERVAL_S)
    state.status = "done"
