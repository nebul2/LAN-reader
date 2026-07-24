"""LAN discovery: find Tapo plugs on a subnet and interactively add them
to config.toml.

Two stages: a fast concurrent TCP probe of port 80 across the subnet, then a
real Tapo handshake (needs cloud credentials) on each responsive host — which
both confirms the device is a Tapo and fetches its model and nickname.

Config updates are text-based so the rest of the file (comments, defaults,
credentials, fake plugs) is preserved. "Replace" only removes tapo-type plug
sections; other device types are kept.
"""

import asyncio
import contextlib
import getpass
import ipaddress
import json
import os
import re
import socket
import tomllib
from pathlib import Path

from rich.console import Console
from rich.table import Table

from lem.config import DEFAULT_PATHS, ConfigError, load_config

PORT = 80
PORT_TIMEOUT = 0.75
PORT_CONCURRENCY = 128
IDENTIFY_TIMEOUT = 6
IDENTIFY_CONCURRENCY = 8
ENERGY_MODELS = ("P110", "P115")


# ---------------------------------------------------------------------------
# Network scanning
# ---------------------------------------------------------------------------

def default_network() -> ipaddress.IPv4Network:
    """Assume the primary interface's /24."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # no packets sent; just picks a route
        local_ip = s.getsockname()[0]
    finally:
        s.close()
    return ipaddress.ip_network(f"{local_ip}/24", strict=False)


def resolve_network(subnet_arg: str | None) -> ipaddress.IPv4Network:
    """Turn a --scan argument ('auto', None, or a CIDR) into a network.
    Raises ValueError with a user-facing message."""
    if subnet_arg and subnet_arg != "auto":
        # A bare address means "its /24" — scanning exactly one host is
        # never what a lab user typing 192.168.1.0 intends.
        if "/" not in subnet_arg:
            subnet_arg += "/24"
        network = ipaddress.ip_network(subnet_arg, strict=False)
    else:
        try:
            network = default_network()
        except OSError as e:
            raise ValueError(
                f"could not determine local subnet ({e}); "
                "pass one explicitly, e.g. 10.0.0.0/24"
            ) from None
    if network.num_addresses > 4096:
        raise ValueError(f"{network} is too large to scan (max /20)")
    return network


async def _port_open(ip: str, sem: asyncio.Semaphore) -> bool:
    async with sem:
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(ip, PORT), timeout=PORT_TIMEOUT
            )
        except Exception:
            return False
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True


def _decode_tapo_nickname(raw: str) -> str:
    """Tapo's LOCAL API returns the nickname base64-encoded (e.g. 'TGFiLUE='),
    but the TP-Link CLOUD API that REM's collector uses returns it decoded
    ('Lab-A'). Decode here so LEM's alias matches REM's exactly. Fall back to
    the raw value if it isn't valid base64/UTF-8 (already-decoded firmware)."""
    if not raw:
        return ""
    try:
        import base64
        return base64.b64decode(raw, validate=True).decode("utf-8")
    except Exception:
        return raw


async def _identify_tapo(ip: str, username: str, password: str) -> dict | None:
    from tapo import ApiClient
    try:
        client = ApiClient(username, password)
        device = await asyncio.wait_for(client.p110(ip), timeout=IDENTIFY_TIMEOUT)
        info = await asyncio.wait_for(device.get_device_info_json(), timeout=IDENTIFY_TIMEOUT)
    except Exception:
        return None
    return {
        "ip": ip,
        "type": "tapo",
        "model": info.get("model") or "?",
        "nickname": _decode_tapo_nickname(info.get("nickname") or ""),
    }


def _http_get_json(url: str, timeout: float = 4.0):
    """Blocking GET returning parsed JSON or None. Run via asyncio.to_thread —
    keeps the scan free of an aiohttp import (measurement uses aiohttp)."""
    import json
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


async def _identify_shelly(ip: str) -> dict | None:
    """Every Shelly (Gen1 & Gen2/3+) answers the unauthenticated GET /shelly.
    Gen2+: {name,id,model,gen,...}. Gen1: {type,mac,num_meters,...} with the
    user name in GET /settings."""
    info = await asyncio.to_thread(_http_get_json, f"http://{ip}/shelly")
    if not isinstance(info, dict):
        return None
    gen = info.get("gen", 1)
    if gen and gen >= 2:  # Gen2/Gen3+
        name = info.get("name") or info.get("id") or ""
        model = info.get("model") or info.get("app") or "Shelly"
    elif "type" in info:  # Gen1
        model = info.get("type") or "Shelly"
        settings = await asyncio.to_thread(_http_get_json, f"http://{ip}/settings")
        name = (settings or {}).get("name") or model
    else:
        return None  # answered /shelly but not a recognisable Shelly
    return {"ip": ip, "type": "shelly", "model": model, "nickname": name, "gen": gen}


async def scan_network(
    network: ipaddress.IPv4Network,
    username: str = "",
    password: str = "",
    console: Console | None = None,
) -> list[dict]:
    """Discover Tapo and Shelly plugs. Shelly needs no credentials; Tapo is
    only probed when username/password are supplied."""
    def status(msg):
        return console.status(msg) if console else contextlib.nullcontext()

    hosts = [str(h) for h in network.hosts()]
    with status(f"Probing {len(hosts)} hosts on {network} (port {PORT})..."):
        sem = asyncio.Semaphore(PORT_CONCURRENCY)
        flags = await asyncio.gather(*(_port_open(h, sem) for h in hosts))
    candidates = [h for h, ok in zip(hosts, flags) if ok]
    if not candidates:
        return []
    if console:
        console.print(f"{len(candidates)} host(s) answered on port {PORT}.")
    with status(f"Identifying {len(candidates)} host(s) (Shelly + Tapo)..."):
        sem = asyncio.Semaphore(IDENTIFY_CONCURRENCY)

        async def ident(ip):
            async with sem:
                # Shelly first — cheap, unauthenticated. Tapo only if creds given.
                d = await _identify_shelly(ip)
                if d is None and username and password:
                    d = await _identify_tapo(ip, username, password)
                return d

        results = await asyncio.gather(*(ident(ip) for ip in candidates))
    return [r for r in results if r]


# ---------------------------------------------------------------------------
# Config file text manipulation
# ---------------------------------------------------------------------------

def sanitize_alias(name: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-")
    return slug or "plug"


def unique_alias(base: str, taken: set[str]) -> str:
    alias, n = base, 2
    while alias in taken:
        alias = f"{base}-{n}"
        n += 1
    return alias


def remove_plug_sections(text: str, aliases: set[str]) -> str:
    """Drop [plugs.<alias>] sections (header + body) for the given aliases."""
    out, skipping = [], False
    for line in text.splitlines(keepends=True):
        m = re.match(r"\s*\[plugs\.([^\]]+)\]", line)
        if m:
            skipping = m.group(1).strip().strip('"') in aliases
        elif re.match(r"\s*\[", line):
            skipping = False
        if not skipping:
            out.append(line)
    return "".join(out)


def rename_plug_section(text: str, old: str, new: str) -> str:
    """Rewrite a [plugs.old] header to [plugs.new], leaving its body intact."""
    return re.sub(
        rf"(?m)^(\s*\[plugs\.){re.escape(old)}(\]\s*)$",
        rf"\g<1>{new}\g<2>",
        text,
    )


def plug_section(alias: str, ip: str, name: str | None = None, dtype: str = "tapo") -> str:
    section = f'\n[plugs.{alias}]\ntype = "{dtype}"\nip   = "{ip}"\n'
    if name:
        # The device's own name — REM's identity for it. For Tapo it must match
        # the cloud nickname (tapo_name); other devices use device_name. JSON
        # escaping is valid TOML basic-string escaping (quotes, unicode, \).
        key = "tapo_name" if dtype == "tapo" else "device_name"
        section += f"{key} = {json.dumps(name)}\n"
    return section


def remove_rem_section(text: str) -> str:
    """Drop the [rem] table (header + body), leaving everything else intact."""
    out, skipping = [], False
    for line in text.splitlines(keepends=True):
        if re.match(r"\s*\[rem\]\s*$", line):
            skipping = True
            continue
        if re.match(r"\s*\[", line):
            skipping = False
        if not skipping:
            out.append(line)
    return "".join(out)


def write_rem_section(
    config_path: Path, url: str, token: str, experiment_id: str, experiment_name: str = ""
) -> None:
    """Write/replace the [rem] connection in the config, preserving the rest."""
    text = remove_rem_section(config_path.read_text()) if config_path.exists() else ""
    text += (
        "\n[rem]\n"
        f"url = {json.dumps(url.rstrip('/'))}\n"
        f"token = {json.dumps(token)}\n"
        f"experiment_id = {json.dumps(experiment_id)}\n"
        f"experiment_name = {json.dumps(experiment_name)}\n"
    )
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(text)


def ensure_credentials_saved(
    config_path: Path, raw: dict, username: str, password: str
) -> str | None:
    """Make sure the config file exists and holds tapo credentials.
    Returns a human-readable note about what was done, or None."""
    if not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(new_config_text(username, password))
        return f"Created {config_path} with Tapo credentials."
    file_creds = raw.get("credentials", {}).get("tapo")
    if file_creds is None:
        config_path.write_text(
            config_path.read_text()
            + f'\n[credentials.tapo]\nusername = "{username}"\npassword = "{password}"\n'
        )
        return f"Saved Tapo credentials to {config_path}."
    if not (file_creds.get("username") and file_creds.get("password")):
        return ("Note: [credentials.tapo] in the config is incomplete — "
                "fill it in (or set TAPO_USERNAME/TAPO_PASSWORD) before measuring.")
    return None


def _entry_parts(entry):
    """(alias, ip[, name[, dtype]]) -> (alias, ip, name, dtype)."""
    alias, ip, *rest = entry
    name = rest[0] if len(rest) >= 1 else None
    dtype = rest[1] if len(rest) >= 2 else "tapo"
    return alias, ip, name, dtype


def write_plugs(
    config_path: Path, accepted: list[tuple], remove_aliases: set[str] = frozenset()
) -> None:
    """Append accepted (alias, ip[, name[, dtype]]) plugs, optionally removing
    old sections first."""
    text = config_path.read_text()
    if remove_aliases:
        text = remove_plug_sections(text, set(remove_aliases))
    for entry in accepted:
        alias, ip, name, dtype = _entry_parts(entry)
        text += plug_section(alias, ip, name, dtype)
    config_path.write_text(text)


def _plugs_by_ip(config_path: Path) -> dict[str, str]:
    """Map ip -> existing alias for any real (non-fake) plug in the config."""
    if not config_path.exists():
        return {}
    try:
        with open(config_path, "rb") as f:
            raw = tomllib.load(f)
    except Exception:
        return {}
    out = {}
    for alias, p in (raw.get("plugs") or {}).items():
        if isinstance(p, dict) and p.get("type") != "fake" and p.get("ip"):
            out[p["ip"]] = alias
    return out


def upsert_plugs(config_path: Path, entries: list[tuple]) -> tuple[int, int]:
    """Add or refresh plugs, matched by IP — non-destructive to plugs not in
    `entries`, and to credentials/[rem]/comments. A plug already configured at
    the same IP is replaced in place (refreshing its name, type, and alias); a
    new IP is appended. entries: (alias, ip[, name[, dtype]]). Returns
    (added, refreshed)."""
    existing_by_ip = _plugs_by_ip(config_path)
    entry_ips = {e[1] for e in entries}
    # Drop the old sections for any IP we're about to (re)write.
    stale_aliases = {existing_by_ip[ip] for ip in entry_ips if ip in existing_by_ip}
    text = config_path.read_text() if config_path.exists() else ""
    if stale_aliases:
        text = remove_plug_sections(text, stale_aliases)
    added = refreshed = 0
    for entry in entries:
        alias, ip, name, dtype = _entry_parts(entry)
        text += plug_section(alias, ip, name, dtype)
        if ip in existing_by_ip:
            refreshed += 1
        else:
            added += 1
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(text)
    return added, refreshed


def new_config_text(username: str, password: str) -> str:
    return (
        "[defaults]\n"
        'interval    = 2.0\n'
        'duration    = "10m"\n'
        'results_dir = "results"\n'
        "\n"
        "[credentials.tapo]\n"
        f'username = "{username}"\n'
        f'password = "{password}"\n'
    )


# ---------------------------------------------------------------------------
# Interactive flow
# ---------------------------------------------------------------------------

def _ask(prompt: str, default: str = "") -> str:
    try:
        answer = input(prompt).strip()
    except EOFError:
        return default
    return answer or default


def run_scan(subnet_arg: str, config_arg: Path | None, console: Console) -> int:
    # Resolve subnet
    try:
        network = resolve_network(subnet_arg)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        return 1

    # Resolve config path and load what exists
    config_path = config_arg or next((p for p in DEFAULT_PATHS if p.exists()), DEFAULT_PATHS[0])
    config = None
    raw = {}
    if config_path.exists():
        try:
            config = load_config(config_path)
        except ConfigError as e:
            console.print(f"[red]Error:[/red] {e}")
            return 1
        with open(config_path, "rb") as f:
            raw = tomllib.load(f)

    # Tapo credentials: env > config. Shelly needs none, so they're optional —
    # prompt but allow skipping (blank) to do a Shelly-only scan.
    creds = raw.get("credentials", {}).get("tapo", {})
    username = os.environ.get("TAPO_USERNAME") or creds.get("username") or ""
    password = os.environ.get("TAPO_PASSWORD") or creds.get("password") or ""
    if not username or not password:
        console.print("Tapo cloud credentials identify Tapo plugs (Shelly needs none).")
        console.print("Press Enter at both prompts to scan for Shelly only.")
        username = _ask(f"Tapo username{f' [{username}]' if username else ''}: ", username)
        password = getpass.getpass("Tapo password: ") or password

    existing_plugs = config.plugs if config else {}
    ip_to_alias = {p.ip: a for a, p in existing_plugs.items() if p.type != "fake"}

    # Scan
    found = asyncio.run(scan_network(network, username, password, console))
    if not found:
        hint = "check the Tapo username/password" if (username and password) else \
               "no credentials given, so only Shelly plugs were sought"
        console.print(f"[yellow]No plugs found.[/yellow] If plugs are definitely on this "
                      f"subnet, {hint}.")
        return 1

    # Tapo answered, so the credentials work — persist them (only if Tapo used).
    if username and password and any(d["type"] == "tapo" for d in found):
        note = ensure_credentials_saved(config_path, raw, username, password)
        if note:
            console.print(note)

    table = Table(box=None, pad_edge=False)
    table.add_column("IP")
    table.add_column("TYPE")
    table.add_column("MODEL")
    table.add_column("NAME")
    table.add_column("")
    for d in found:
        note = "" if d["type"] != "tapo" or d["model"].startswith(ENERGY_MODELS) \
            else "[yellow]no energy monitoring?[/yellow]"
        table.add_row(d["ip"], d["type"], d["model"], d["nickname"] or "—", note)
    console.print()
    console.print(f"Found {len(found)} plug(s):")
    console.print(table)
    console.print()

    # Choose which to use / refresh. Plugs already configured at the same IP
    # are refreshed in place (nickname re-read from the device); others are
    # added. Nothing else in the config is touched (removal is a separate step).
    taken = set(existing_plugs)
    accepted: list[tuple] = []  # (alias, ip, name, dtype)
    for d in found:
        known = ip_to_alias.get(d["ip"])
        verb = "Refresh" if known else "Add"
        label = f"{d['ip']} ({d['type']} {d['model']}" + (f", \"{d['nickname']}\"" if d["nickname"] else "") + ")"
        if known:
            label += f" [currently '{known}']"
        if _ask(f"{verb} {label}? [Y/n] ", "y").lower() not in ("y", "yes"):
            continue
        # Auto-name the local handle from the device's own name (the source of
        # truth). The name is stored verbatim (tapo_name / device_name) and is
        # what REM keys on; this alias is only a filename-safe slug. Reusing a
        # refreshed plug's own current alias avoids a spurious rename.
        pool = taken - ({known} if known else set())
        alias = unique_alias(
            sanitize_alias(d["nickname"]) if d["nickname"] else f"plug{d['ip'].rsplit('.', 1)[1]}",
            pool,
        )
        taken.add(alias)
        console.print(f"  named '{alias}'  ({d['type']} name: \"{d['nickname'] or '—'}\")")
        accepted.append((alias, d["ip"], d.get("nickname") or None, d["type"]))

    if not accepted:
        console.print("No changes made.")
        return 0

    added, refreshed = upsert_plugs(config_path, accepted)
    console.print(f"\nWrote {config_path}: {added} added, {refreshed} refreshed.")
    for alias, ip, _name, _dtype in accepted:
        console.print(f"  {alias:<16} {ip}")
    return 0
