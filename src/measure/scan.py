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
import os
import re
import socket
import tomllib
from pathlib import Path

from rich.console import Console
from rich.table import Table

from measure.config import DEFAULT_PATHS, ConfigError, load_config

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
        "model": info.get("model") or "?",
        "nickname": info.get("nickname") or "",
    }


async def scan_network(
    network: ipaddress.IPv4Network,
    username: str,
    password: str,
    console: Console | None = None,
) -> list[dict]:
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
    with status(f"Checking {len(candidates)} host(s) for Tapo devices..."):
        sem = asyncio.Semaphore(IDENTIFY_CONCURRENCY)

        async def ident(ip):
            async with sem:
                return await _identify_tapo(ip, username, password)

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


def plug_section(alias: str, ip: str) -> str:
    return f'\n[plugs.{alias}]\ntype = "tapo"\nip   = "{ip}"\n'


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


def write_plugs(
    config_path: Path, accepted: list[tuple[str, str]], remove_aliases: set[str] = frozenset()
) -> None:
    """Append accepted (alias, ip) tapo plugs, optionally removing old sections first."""
    text = config_path.read_text()
    if remove_aliases:
        text = remove_plug_sections(text, set(remove_aliases))
    for alias, ip in accepted:
        text += plug_section(alias, ip)
    config_path.write_text(text)


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

    # Credentials: env > config; prompt if still missing
    creds = raw.get("credentials", {}).get("tapo", {})
    username = os.environ.get("TAPO_USERNAME") or creds.get("username") or ""
    password = os.environ.get("TAPO_PASSWORD") or creds.get("password") or ""
    if not username or not password:
        console.print("Tapo cloud credentials are needed to identify devices.")
        username = _ask(f"Tapo username{f' [{username}]' if username else ''}: ", username)
        password = getpass.getpass("Tapo password: ") or password
        if not username or not password:
            console.print("[red]Error:[/red] credentials are required for scanning")
            return 1

    existing_plugs = config.plugs if config else {}
    tapo_aliases = {a for a, p in existing_plugs.items() if p.type == "tapo"}
    ip_to_alias = {p.ip: a for a, p in existing_plugs.items() if p.type == "tapo"}

    # Add or replace?
    mode = "add"
    if tapo_aliases:
        console.print(
            f"{config_path} already has {len(tapo_aliases)} tapo plug(s): "
            f"{', '.join(sorted(tapo_aliases))}"
        )
        while True:
            answer = _ask("[a]dd new plugs to it, [r]eplace its tapo plugs, or [q]uit? [a/r/q] ", "a").lower()
            if answer in ("a", "r", "q"):
                break
        if answer == "q":
            console.print("No changes made.")
            return 0
        mode = "replace" if answer == "r" else "add"

    # Scan
    found = asyncio.run(scan_network(network, username, password, console))
    if not found:
        console.print(
            "[yellow]No Tapo devices found.[/yellow] If plugs are definitely on this "
            "subnet, check the credentials (a failed Tapo login looks identical to "
            "a non-Tapo device)."
        )
        return 1

    # Devices answered, so the credentials work — persist them now, regardless
    # of whether any plugs end up being added below.
    note = ensure_credentials_saved(config_path, raw, username, password)
    if note:
        console.print(note)

    table = Table(box=None, pad_edge=False)
    table.add_column("IP")
    table.add_column("MODEL")
    table.add_column("NICKNAME")
    table.add_column("")
    for d in found:
        note = "" if d["model"].startswith(ENERGY_MODELS) else "[yellow]no energy monitoring?[/yellow]"
        table.add_row(d["ip"], d["model"], d["nickname"] or "—", note)
    console.print()
    console.print(f"Found {len(found)} Tapo device(s):")
    console.print(table)
    console.print()

    # Accept / name / refuse each
    taken = set(existing_plugs) - (tapo_aliases if mode == "replace" else set())
    accepted: list[tuple[str, str]] = []  # (alias, ip)
    for d in found:
        label = f"{d['ip']} ({d['model']}" + (f", \"{d['nickname']}\"" if d["nickname"] else "") + ")"
        if mode == "add" and d["ip"] in ip_to_alias:
            console.print(f"  {label} — already configured as '{ip_to_alias[d['ip']]}', skipping")
            continue
        if _ask(f"Add {label}? [Y/n] ", "y").lower() not in ("y", "yes"):
            continue
        default = unique_alias(
            sanitize_alias(d["nickname"]) if d["nickname"] else f"plug{d['ip'].rsplit('.', 1)[1]}",
            taken,
        )
        alias = None
        while alias is None:
            candidate = sanitize_alias(_ask(f"  Alias [{default}]: ", default))
            if candidate in taken:
                console.print(f"  [yellow]'{candidate}' is already used — pick another.[/yellow]")
            else:
                alias = candidate
        taken.add(alias)
        accepted.append((alias, d["ip"]))

    if not accepted and mode == "add":
        console.print("No plugs added.")
        return 0

    # Write the plug list (the file exists by now — created above if needed)
    write_plugs(config_path, accepted, tapo_aliases if mode == "replace" else frozenset())

    replaced = f" (replaced {len(tapo_aliases)})" if mode == "replace" else ""
    console.print(f"\nWrote {len(accepted)} plug(s) to {config_path}{replaced}:")
    for alias, ip in accepted:
        console.print(f"  {alias:<16} {ip}")
    return 0
