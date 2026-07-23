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


@dataclass(frozen=True)
class Config:
    interval: float
    duration: str
    results_dir: Path
    plugs: dict[str, PlugConfig]


def _credentials_for(plug_type: str, plug_raw: dict, creds_raw: dict) -> dict:
    # Any per-plug key besides type/ip is passed to the device's connect()
    # (credential overrides, fake fail_rate, a future PDU's outlet number, ...).
    base = dict(creds_raw.get(plug_type, {}))
    base.update({k: v for k, v in plug_raw.items() if k not in ("type", "ip")})
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
        )

    return Config(
        interval=float(defaults.get("interval", 2.0)),
        duration=str(defaults.get("duration", "10m")),
        results_dir=Path(defaults.get("results_dir", "results")),
        plugs=plugs,
    )
