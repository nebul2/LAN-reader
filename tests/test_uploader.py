import asyncio
import base64
import json

import pytest

from lem.rem_client import RemError, parse_join_code, JOIN_CODE_PREFIX
from lem.uploader import (
    UploaderState,
    _parse_rows,
    _read_complete_lines,
    _read_sidecar,
    _write_sidecar,
    find_unsynced,
    init_sidecar,
    run_uploader,
)


def _make_code(url, exp, tok):
    blob = json.dumps({"u": url, "e": exp, "t": tok}).encode()
    return JOIN_CODE_PREFIX + base64.urlsafe_b64encode(blob).decode()


def test_join_code_roundtrip():
    code = _make_code("https://rem.example.org", "tv-standby", "tok123")
    join = parse_join_code(code)
    assert join.url == "https://rem.example.org"
    assert join.experiment_id == "tv-standby"
    assert join.token == "tok123"


def test_join_code_trailing_slash_stripped():
    assert parse_join_code(_make_code("https://x/", "e", "t")).url == "https://x"


@pytest.mark.parametrize("bad", ["", "hello", "REM1-not-base64!!", JOIN_CODE_PREFIX + "eyJ9"])
def test_join_code_bad(bad):
    with pytest.raises(RemError):
        parse_join_code(bad)


def test_parse_rows_maps_alias_and_skips_header():
    text = "timestamp,alias,power_w\n2026-07-23T10:00:00.100+00:00,desk,12.345\n"
    rows = _parse_rows(text, skip_header=True, alias_map={"desk": "Lyon TV"})
    assert rows == [["2026-07-23T10:00:00.100+00:00", "Lyon TV", 12.345]]


def test_parse_rows_alias_fallback():
    text = "2026-07-23T10:00:00.100+00:00,fake1,5.0\n"
    rows = _parse_rows(text, skip_header=False, alias_map={})
    assert rows[0][1] == "fake1"


def test_read_complete_lines_leaves_partial(tmp_path):
    p = tmp_path / "c.csv"
    p.write_bytes(b"a,b,c\n1,2,3\npartial-line-no-newline")
    text, new_offset = _read_complete_lines(p, 0, 10000)
    assert text == "a,b,c\n1,2,3\n"
    assert new_offset == len("a,b,c\n1,2,3\n")
    # A second read from the new offset sees nothing until the line completes
    text2, off2 = _read_complete_lines(p, new_offset, 10000)
    assert text2 == "" and off2 == new_offset


def test_read_complete_lines_caps_at_max_lines(tmp_path):
    p = tmp_path / "c.csv"
    p.write_bytes(b"l1\nl2\nl3\nl4\n")
    text, new_offset = _read_complete_lines(p, 0, 2)
    assert text == "l1\nl2\n" and new_offset == 6


def test_sidecar_roundtrip(tmp_path):
    combined = tmp_path / "run_combined.csv"
    combined.write_text("timestamp,alias,power_w\n")
    init_sidecar(combined, "exp1")
    data = _read_sidecar(combined)
    assert data["offset"] == 0 and data["experiment_id"] == "exp1"
    data["offset"] = 42
    _write_sidecar(combined, data)
    assert _read_sidecar(combined)["offset"] == 42


def test_find_unsynced(tmp_path):
    # joined + behind → listed
    a = tmp_path / "a_combined.csv"
    a.write_text("timestamp,alias,power_w\nx,y,1\n")
    _write_sidecar(a, {"offset": 0})
    # joined + caught up → not listed
    b = tmp_path / "b_combined.csv"
    b.write_text("timestamp,alias,power_w\n")
    _write_sidecar(b, {"offset": b.stat().st_size})
    # never joined (no sidecar) → not listed
    c = tmp_path / "c_combined.csv"
    c.write_text("timestamp,alias,power_w\nx,y,1\n")
    names = {p.name for p in find_unsynced(tmp_path)}
    assert names == {"a_combined.csv"}


class FakeClient:
    """Captures posted rows; acks everything."""
    def __init__(self):
        self.rows = []
        self.batch_ids = []

    def post_batch(self, rows, covering, batch_id):
        from lem.rem_client import BatchAck
        self.rows.extend(rows)
        self.batch_ids.append(batch_id)
        return BatchAck(inserted=len(rows), duplicate=False, cadence_s=10, is_current=True)


def test_run_uploader_streams_and_stops(tmp_path):
    combined = tmp_path / "run_combined.csv"
    combined.write_text(
        "timestamp,alias,power_w\n"
        "2026-07-23T10:00:00.000+00:00,desk,12.000\n"
        "2026-07-23T10:00:01.000+00:00,desk,12.500\n"
    )
    init_sidecar(combined, "exp1")
    client = FakeClient()
    state = UploaderState()

    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(run_uploader(
            combined, {"desk": "Lyon TV"}, client, state, stop, ["Lyon TV"], 10000
        ))
        await asyncio.sleep(0.1)
        stop.set()
        await task

    asyncio.run(scenario())
    assert len(client.rows) == 2
    assert client.rows[0] == ["2026-07-23T10:00:00.000+00:00", "Lyon TV", 12.000]
    assert _read_sidecar(combined)["offset"] == combined.stat().st_size
    assert state.rows_uploaded == 2
