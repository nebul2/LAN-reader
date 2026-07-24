"""Client for REM's field ingest API.

Stdlib-only (urllib) so the PyInstaller bundle stays lean and no extra
dependency has to be kept in sync across the CLI and the app. Blocking calls;
callers on an event loop wrap them in asyncio.to_thread.
"""

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

JOIN_CODE_PREFIX = "REM1-"
DEFAULT_REM_URL = "https://rem.greeningofstreaming.org"


class RemError(Exception):
    """Base class; str() is safe to show a volunteer."""


class RemAuthError(RemError):
    """Token invalid or revoked (HTTP 401)."""


class RemGoneError(RemError):
    """Experiment no longer exists (HTTP 410)."""


@dataclass(frozen=True)
class RemJoin:
    url: str
    experiment_id: str
    token: str


@dataclass(frozen=True)
class HelloResult:
    experiment_id: str
    experiment_name: str
    is_current: bool
    cadence_s: int
    server_time: str
    session_ttl_s: int
    max_batch_rows: int
    clock_skew_s: float


@dataclass(frozen=True)
class BatchAck:
    inserted: int
    duplicate: bool
    cadence_s: int
    is_current: bool


def parse_join_code(code: str) -> RemJoin:
    """Decode a REM1-... join code. Raises RemError with a friendly message."""
    import base64

    code = (code or "").strip()
    if not code.startswith(JOIN_CODE_PREFIX):
        raise RemError("That doesn't look like a REM join code (should start with 'REM1-').")
    try:
        blob = base64.urlsafe_b64decode(code[len(JOIN_CODE_PREFIX):].encode())
        data = json.loads(blob)
        return RemJoin(url=data["u"].rstrip("/"), experiment_id=data["e"], token=data["t"])
    except Exception:
        raise RemError("This join code is malformed. Ask your operator for a fresh one.") from None


def resolve_code(code: str, url: str = DEFAULT_REM_URL) -> RemJoin:
    """Turn either a self-contained REM1-... code OR a short code into a
    RemJoin. Short codes are resolved against `url` (the REM server)."""
    code = (code or "").strip()
    if code.startswith(JOIN_CODE_PREFIX):
        return parse_join_code(code)   # self-contained, url ignored
    if not code:
        raise RemError("Enter a join code.")
    return RemClient(url).resolve(code)


class RemClient:
    def __init__(self, url: str, token: str = "", timeout: float = 10.0):
        # token is optional — the /api/field/resolve short-code lookup is
        # unauthenticated (the short code is the shared secret).
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _headers(self) -> dict:
        h = {"User-Agent": "lem/0.2.0"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _post(self, path: str, body: dict) -> dict:
        h = self._headers()
        h["Content-Type"] = "application/json"
        req = urllib.request.Request(
            f"{self.url}{path}", data=json.dumps(body).encode(), headers=h, method="POST",
        )
        return self._send(req)

    def _get(self, path: str) -> dict:
        req = urllib.request.Request(f"{self.url}{path}", headers=self._headers(), method="GET")
        return self._send(req)

    def _send(self, req) -> dict:
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise RemAuthError("REM rejected the token (revoked or rotated). Re-join with a new code.") from None
            if e.code == 410:
                raise RemGoneError("The experiment no longer exists on REM.") from None
            detail = ""
            try:
                detail = json.loads(e.read().decode()).get("detail", "")
            except Exception:
                pass
            raise RemError(f"REM error {e.code}{': ' + detail if detail else ''}") from None
        except urllib.error.URLError as e:
            raise RemError(f"Could not reach REM at {self.url} ({e.reason}).") from None
        except Exception as e:
            raise RemError(f"Unexpected error talking to REM: {e}") from None

    def hello(self, aliases: list[str] | None = None) -> HelloResult:
        data = self._post("/api/field/hello", {"client": "lem/0.2.0", "aliases": aliases or []})
        exp = data.get("experiment", {})
        skew = 0.0
        try:
            server = datetime.fromisoformat(data["server_time"])
            skew = abs((datetime.now(timezone.utc) - server).total_seconds())
        except Exception:
            pass
        return HelloResult(
            experiment_id=exp.get("id", ""),
            experiment_name=exp.get("name", ""),
            is_current=bool(exp.get("is_current")),
            cadence_s=int(data.get("target_cadence_s", 10)),
            server_time=data.get("server_time", ""),
            session_ttl_s=int(data.get("session_ttl_s", 90)),
            max_batch_rows=int(data.get("max_batch_rows", 10000)),
            clock_skew_s=skew,
        )

    def post_batch(self, rows: list, covering: list[str], batch_id: str) -> BatchAck:
        data = self._post("/api/field/batch", {
            "batch_id": batch_id, "covering": covering, "rows": rows,
        })
        return BatchAck(
            inserted=int(data.get("inserted", 0)),
            duplicate=bool(data.get("duplicate")),
            cadence_s=int(data.get("target_cadence_s", 10)),
            is_current=bool(data.get("is_current", True)),
        )

    def status(self) -> dict:
        return self._get("/api/field/status")

    def resolve(self, short_code: str) -> RemJoin:
        data = self._get(f"/api/field/resolve/{short_code.strip()}")
        return RemJoin(url=data.get("url", self.url).rstrip("/"),
                       experiment_id=data["experiment_id"], token=data["token"])
