"""Load and validate config.toml.

Credential precedence for tapo: TAPO_USERNAME/TAPO_PASSWORD env vars, then
per-plug username/password keys, then [credentials.tapo].
"""

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATHS = (
    Path("config.toml"),
    Path.home() / ".config" / "lem" / "config.toml",
    Path.home() / ".config" / "measure" / "config.toml",  # pre-rename installs
)


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class PlugConfig:
    alias: str
    type: str
    ip: str
    credentials: dict  # kwargs passed to BaseDevice.connect()
    # Exact Tapo nickname, verbatim (may hold spaces/unicode). This is the
    # device's identity in REM — the cloud collector uses the same string —
    # so uploads must use it, never the sanitized local alias.
    tapo_name: str | None = None


@dataclass(frozen=True)
class RemConfig:
    url: str
    token: str
    experiment_id: str
    experiment_name: str = ""


@dataclass(frozen=True)
class Config:
    interval: float
    duration: str
    results_dir: Path
    plugs: dict[str, PlugConfig]
    rem: RemConfig | None = None


def upload_alias(plug: PlugConfig) -> str:
    """The alias to report to REM: the Tapo nickname when known (matching the
    cloud collector), else the local alias (fake plugs, hand-edited configs)."""
    return plug.tapo_name or plug.alias


def nickname_warnings(plugs) -> list[str]:
    """Identity problems that would confuse REM: duplicate or blank Tapo
    nicknames (REM keys on the nickname, so these merge or misidentify)."""
    out = []
    by_name: dict[str, list[str]] = {}
    for p in plugs:
        if p.type != "tapo":
            continue
        if not p.tapo_name:
            out.append(f"'{p.alias}' has no Tapo nickname — REM can't match it to the cloud device.")
        else:
            by_name.setdefault(p.tapo_name, []).append(p.alias)
    for name, aliases in by_name.items():
        if len(aliases) > 1:
            out.append(f"Duplicate Tapo nickname \"{name}\" on {', '.join(aliases)} "
                       "— their data will merge in REM. Rename one in the Tapo app.")
    return out


def _credentials_for(plug_type: str, plug_raw: dict, creds_raw: dict) -> dict:
    # Any per-plug key besides type/ip/tapo_name is passed to the device's
    # connect() (credential overrides, fake fail_rate, a PDU's outlet, ...).
    base = dict(creds_raw.get(plug_type, {}))
    base.update({k: v for k, v in plug_raw.items() if k not in ("type", "ip", "tapo_name")})
    if plug_type == "tapo":
        if os.environ.get("TAPO_USERNAME"):
            base["username"] = os.environ["TAPO_USERNAME"]
        if os.environ.get("TAPO_PASSWORD"):
            base["password"] = os.environ["TAPO_PASSWORD"]
    return base


def load_config(path: Path | None = None) -> Config:
    if path is not None:
        if not path.exists():
            raise ConfigError(f"Config file not found: {path}")
    else:
        path = next((p for p in DEFAULT_PATHS if p.exists()), None)
        if path is None:
            raise ConfigError(
                "No config file found. Copy config.example.toml to config.toml "
                f"(searched: {', '.join(str(p) for p in DEFAULT_PATHS)})."
            )

    with open(path, "rb") as f:
        raw = tomllib.load(f)

    defaults = raw.get("defaults", {})
    creds_raw = raw.get("credentials", {})

    plugs: dict[str, PlugConfig] = {}
    for alias, plug_raw in raw.get("plugs", {}).items():
        if not isinstance(plug_raw, dict):
            raise ConfigError(f"[plugs.{alias}] must be a table")
        plug_type = plug_raw.get("type")
        if not plug_type:
            raise ConfigError(f"[plugs.{alias}] is missing 'type'")
        ip = plug_raw.get("ip", "-")
        if plug_type != "fake" and (not ip or ip == "-"):
            raise ConfigError(f"[plugs.{alias}] is missing 'ip'")
        plugs[alias] = PlugConfig(
            alias=alias,
            type=plug_type,
            ip=ip,
            credentials=_credentials_for(plug_type, plug_raw, creds_raw),
            tapo_name=plug_raw.get("tapo_name") or None,
        )

    rem = None
    rem_raw = raw.get("rem")
    if rem_raw:
        for key in ("url", "token", "experiment_id"):
            if not rem_raw.get(key):
                raise ConfigError(f"[rem] is missing '{key}' — re-join with the join code")
        rem = RemConfig(
            url=str(rem_raw["url"]).rstrip("/"),
            token=str(rem_raw["token"]),
            experiment_id=str(rem_raw["experiment_id"]),
            experiment_name=str(rem_raw.get("experiment_name", "")),
        )

    return Config(
        interval=float(defaults.get("interval", 2.0)),
        duration=str(defaults.get("duration", "10m")),
        results_dir=Path(defaults.get("results_dir", "results")),
        plugs=plugs,
        rem=rem,
    )
