# Docker edge-case checks (run against the local REM stack)

In-process API edge cases are covered by `scripts/e2e_edge_cases.py`. These
remaining ones need the running stack (`docker compose up -d timescaledb admin`
from the `field-api` branch) and are part of P1 regression. Run them from the
`rem` repo; `field_test.sh setup` first for a join code.

1. **Experiment stop → data orphaned**
   - Join LEM, measure ~30s → `./scripts/field_test.sh verify` shows rows.
   - `curl -X POST .../api/experiments/lem-field-test/end` (stop it).
   - Measure another ~30s. Verify: new rows ARE in `gos_rem` but the experiment
     **export** (`.../export`) does NOT include the post-stop rows.
   - LEM should show the "⏸ experiment not running" banner during that window.

2. **Experiment restart → earlier data dropped from export**
   - Start it again (`.../start`). Confirm the export now covers only the newest
     segment (start time was reset) — earlier rows orphaned but still in DB.

3. **Manual import of a late file**
   - `lem rem export` → `<run>_rem.csv`.
   - `ADMIN_BASIC_USER=... python3 scripts/field_import.py <run>_rem.csv lem-field-test --extend`
   - Verify the rows land under the Tapo nicknames and (with --extend) show in export.

4. **Collector cloud-skip** (needs collector + real TP-Link creds)
   - Bring up `collector`; measure a real shared plug in LEM.
   - `docker compose logs -f collector | grep "covered by a local LEM"` appears;
     after LEM stops + >90s, cloud polling resumes.

5. **No-regression spot checks** (P1)
   - Admin UI loads; existing experiments/groups/export work; `/api/data/power`
     returns; basic-auth still challenges non-`/api/field` paths.
   - With no `field_sessions.json`, collector polls all devices normally.
