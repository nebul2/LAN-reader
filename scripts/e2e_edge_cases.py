"""Field-API edge cases, in-process (stubbed DB). Covers the failure modes a
hackathon hits at the API layer: experiment paused/deleted, token revoked,
duplicate nickname collision, idempotent retry.

Stack-dependent cases (stop/restart export window, field_import round-trip,
collector cloud-skip) need the docker stack — see scripts/DOCKER_EDGE_CHECKS.md.
"""
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, "/Users/nebul2/dev/rem/admin")
import field_api
from fastapi import FastAPI
from fastapi.testclient import TestClient

tmp = Path(tempfile.mkdtemp(prefix="edge_"))
EXP = {"tv": {"id": "tv", "name": "TV", "is_current": True, "linked_groups": [], "target_cadence_s": 10}}
GROUPS, DB = {}, []


class Conn:
    def cursor(self): return self
    def close(self): pass
    def commit(self): pass


field_api.configure(data_dir=tmp, get_db_connection=lambda: Conn(),
    load_groups=lambda: GROUPS, save_groups=lambda g: GROUPS.update(g),
    load_experiments=lambda: EXP, save_experiments=lambda e: EXP.update(e),
    public_url="http://x")
field_api.execute_values = lambda cur, sql, values: DB.extend(values)
app = FastAPI(); app.include_router(field_api.router)
c = TestClient(app)


def token():
    import base64, json
    code = c.post("/api/experiments/tv/field-token").json()["join_code"]
    return json.loads(base64.urlsafe_b64decode(code[5:]))["t"]


def batch(tok, rows, bid="b", covering=None):
    return c.post("/api/field/batch", headers={"Authorization": f"Bearer {tok}"},
                  json={"batch_id": bid, "covering": covering or [], "rows": rows})


with patch.object(field_api, "execute_values", field_api.execute_values):
    tok = token()

    # 1. Experiment live -> ack is_current True
    r = batch(tok, [["2026-07-23T10:00:00+00:00", "TV", 5.0]], "b1")
    assert r.json()["is_current"] is True, "should be recording"
    print("1 ok: live experiment ack is_current=True")

    # 2. Experiment paused -> ack is_current False (drives LEM's paused banner)
    EXP["tv"]["is_current"] = False
    r = batch(tok, [["2026-07-23T10:00:01+00:00", "TV", 5.0]], "b2")
    assert r.json()["is_current"] is False, "should report paused"
    print("2 ok: paused experiment ack is_current=False (rows still stored)")
    EXP["tv"]["is_current"] = True

    # 3. Duplicate nickname collision -> two plugs' rows land under one alias
    DB.clear()
    batch(tok, [["2026-07-23T10:00:02+00:00", "TV", 5.0],
                ["2026-07-23T10:00:02+00:00", "TV", 9.0]], "b3")
    assert len({row[1] for row in DB}) == 1 and len(DB) == 2
    print("3 ok: duplicate nickname merges into one alias (why H3 warns)")

    # 4. Idempotent retry -> duplicate=true, no reinsert
    n = len(DB)
    r = batch(tok, [["2026-07-23T10:00:02+00:00", "TV", 5.0]], "b3")
    assert r.json()["duplicate"] is True and len(DB) == n
    print("4 ok: repeated batch_id is idempotent")

    # 5. Token revoked mid-stream -> 401
    c.delete("/api/experiments/tv/field-token")
    assert batch(tok, [["2026-07-23T10:00:03+00:00", "TV", 5.0]], "b5").status_code == 401
    print("5 ok: revoked token -> 401 (LEM shows reconnect)")

    # 6. Experiment deleted -> 410
    tok2 = token()
    EXP.clear()
    assert batch(tok2, [["2026-07-23T10:00:04+00:00", "TV", 5.0]], "b6").status_code == 410
    print("6 ok: deleted experiment -> 410")

print("EDGE CASES: ALL PASSED")
