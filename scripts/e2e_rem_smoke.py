"""End-to-end: real HTTP server running REM's field_api (stubbed DB) +
the LEM CLI measuring fake plugs and streaming to it."""
import base64
import json
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, "/Users/nebul2/dev/rem/admin")
sys.path.insert(0, "/Users/nebul2/dev/LAN-reader/src")

import field_api
import uvicorn
from fastapi import FastAPI

tmp = Path(tempfile.mkdtemp(prefix="e2e_"))
EXPERIMENTS = {"tv": {"id": "tv", "name": "TV Study", "is_current": True,
                      "linked_groups": [], "target_cadence_s": 10}}
GROUPS = {}
DB_ROWS = []


class FakeCursor:
    def close(self): pass
class FakeConn:
    def cursor(self): return FakeCursor()
    def commit(self): pass
    def close(self): pass

field_api.configure(
    data_dir=tmp, get_db_connection=lambda: FakeConn(),
    load_groups=lambda: GROUPS, save_groups=lambda g: GROUPS.update(g),
    load_experiments=lambda: EXPERIMENTS, save_experiments=lambda e: EXPERIMENTS.update(e),
    public_url="http://127.0.0.1:8199",
)

# Patch execute_values at module level so batch inserts land in DB_ROWS
field_api.execute_values = lambda cur, sql, values: DB_ROWS.extend(values)

app = FastAPI()
app.include_router(field_api.router)

server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8199, log_level="error"))
t = threading.Thread(target=server.run, daemon=True)
t.start()
for _ in range(50):
    if server.started:
        break
    time.sleep(0.1)
assert server.started, "server did not start"

# --- operator makes a join code ---
import urllib.request
req = urllib.request.Request("http://127.0.0.1:8199/api/experiments/tv/field-token", method="POST")
join_code = json.loads(urllib.request.urlopen(req).read())["join_code"]
print("join code:", join_code[:20], "...")

# --- LEM config with two fake plugs, one with a tapo_name ---
cfg = tmp / "config.toml"
results_dir = tmp / "results"
cfg.write_text(
    f'[defaults]\ninterval = 0.5\nduration = "2s"\nresults_dir = {json.dumps(str(results_dir))}\n\n'
    '[plugs.desk]\ntype = "fake"\ntapo_name = "Lyon TV"\n\n'
    '[plugs.rack]\ntype = "fake"\n'
)

from lem.cli import main

# join
assert main(["rem", "--config", str(cfg), "join", join_code]) == 0
assert '[rem]' in cfg.read_text()

# measure — uploader streams during the run
rc = main(["--config", str(cfg), "--plugs", "desk,rack", "--duration", "3s", "--interval", "0.5"])
assert rc == 0, rc

time.sleep(0.5)
aliases = {r[1] for r in DB_ROWS}
print(f"DB got {len(DB_ROWS)} rows, aliases={aliases}")
# desk uploads under its Tapo nickname 'Lyon TV'; rack (no tapo_name) under 'rack'
assert "Lyon TV" in aliases, aliases
assert "rack" in aliases, aliases
assert "desk" not in aliases, "should have used tapo_name, not local alias"

# sessions file written (collector would read this to pause cloud polling)
sessions = json.loads((tmp / "field_sessions.json").read_text())["aliases"]
assert "Lyon TV" in sessions and "rack" in sessions
print("field sessions cover:", set(sessions))

# combined CSV fully synced
combined = next((tmp / "results").glob("*_combined.csv"))
sidecar = json.loads((combined.parent / (combined.name + ".sync")).read_text())
assert sidecar["offset"] == combined.stat().st_size, "not fully uploaded"

# row-count equality: DB rows == CSV data rows
csv_rows = combined.read_text().strip().splitlines()[1:]  # minus header
print(f"CSV data rows={len(csv_rows)}, DB rows={len(DB_ROWS)}")
assert len(csv_rows) == len(DB_ROWS), (len(csv_rows), len(DB_ROWS))

server.should_exit = True
print("E2E PASSED")
