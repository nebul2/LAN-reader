"""Backfill: measure while REM is unreachable, then `lem rem sync` uploads it."""
import json, sys, tempfile, threading, time
from pathlib import Path
sys.path.insert(0, "/Users/nebul2/dev/rem/admin")
sys.path.insert(0, "/Users/nebul2/dev/LAN-reader/src")
import field_api, uvicorn, urllib.request
from fastapi import FastAPI

tmp = Path(tempfile.mkdtemp(prefix="backfill_"))
EXP = {"tv": {"id": "tv", "name": "TV", "is_current": True, "linked_groups": [], "target_cadence_s": 10}}
GROUPS, DB = {}, []
class C:
    def cursor(self): return self
    def close(self): pass
    def commit(self): pass
field_api.configure(data_dir=tmp, get_db_connection=lambda: C(),
    load_groups=lambda: GROUPS, save_groups=lambda g: GROUPS.update(g),
    load_experiments=lambda: EXP, save_experiments=lambda e: EXP.update(e),
    public_url="http://127.0.0.1:8291")
field_api.execute_values = lambda cur, sql, values: DB.extend(values)

# Pre-mint a token WITHOUT the server running (write file directly)
import secrets, base64
tok = secrets.token_urlsafe(24)
(tmp / "field_tokens.json").write_text(json.dumps({"tv": {"token": tok, "created_at": "x"}}))
code = "REM1-" + base64.urlsafe_b64encode(json.dumps({"u": "http://127.0.0.1:8291", "e": "tv", "t": tok}).encode()).decode()

cfg = tmp / "config.toml"
cfg.write_text(f'[defaults]\ninterval=0.3\nduration="2s"\nresults_dir={json.dumps(str(tmp/"results"))}\n\n'
               '[plugs.desk]\ntype="fake"\ntapo_name="Lyon TV"\n')
from lem.cli import main

# Join by writing [rem] directly (server is down, so hello would fail — write section manually)
from lem.scan import write_rem_section
write_rem_section(cfg, "http://127.0.0.1:8291", tok, "tv", "TV")

# Measure while REM is DOWN — uploader can't reach it, data stays local
rc = main(["--config", str(cfg), "--plugs", "desk", "--duration", "2s", "--interval", "0.3"])
assert rc == 0
assert len(DB) == 0, f"REM was down but DB has {len(DB)} rows"
combined = next((tmp / "results").glob("*_combined.csv"))
local_rows = len(combined.read_text().strip().splitlines()) - 1
print(f"measured offline: {local_rows} local rows, DB still empty (good)")

# Now bring REM up and backfill
server = uvicorn.Server(uvicorn.Config(FastAPI(), host="127.0.0.1", port=8291, log_level="error"))
app_api = FastAPI(); app_api.include_router(field_api.router)
server = uvicorn.Server(uvicorn.Config(app_api, host="127.0.0.1", port=8291, log_level="error"))
threading.Thread(target=server.run, daemon=True).start()
for _ in range(50):
    if server.started: break
    time.sleep(0.1)
assert server.started

rc = main(["rem", "--config", str(cfg), "sync"])
assert rc == 0
time.sleep(0.3)
print(f"after sync: DB has {len(DB)} rows, aliases={ {r[1] for r in DB} }")
assert len(DB) == local_rows, (len(DB), local_rows)
assert all(r[1] == "Lyon TV" for r in DB)
Path("/tmp/backfill_verdict.txt").write_text(f"BACKFILL PASSED: {local_rows} offline rows backfilled under Tapo nickname")
import os; os._exit(0)
