"""Command-line entry point.

Examples:
    lem --plugs desk,rack --duration 10m
    lem --all
    lem --plugs desk --interval 0.5 --duration unlimited
    lem --list
    lem --scan                            # discover plugs on the local /24
    lem --plugs fake1 --duration 30s      # dry run, no hardware
    lem rem join <code>                   # connect to a REM experiment
    lem rem status                        # REM connection + upload status
"""

import argparse
import asyncio
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console

from lem.config import Config, ConfigError, PlugConfig, load_config, upload_alias
from lem.display import make_display, print_summary
from lem.model import PlugState
from lem.runner import run
from lem.sinks.csv_sink import CsvSink


def _rem_upload_alias(config: Config, plug: PlugConfig) -> str:
    return upload_alias(plug)


def _combined_path(sink: CsvSink) -> Path:
    return sink.combined_path

_DURATION_RE = re.compile(r"^(\d+(?:\.\d+)?)([smh]?)$")
_DURATION_MULT = {"": 1, "s": 1, "m": 60, "h": 3600}


def parse_duration(text: str) -> float | None:
    """'90', '90s', '10m', '2h' -> seconds; 'unlimited' or '0' -> None."""
    text = text.strip().lower()
    if text in ("unlimited", "0"):
        return None
    m = _DURATION_RE.match(text)
    if not m:
        raise ValueError(
            f"Invalid duration '{text}' (expected e.g. 90, 90s, 10m, 2h, or unlimited)"
        )
    seconds = float(m.group(1)) * _DURATION_MULT[m.group(2)]
    if seconds <= 0:
        return None
    return seconds


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lem",
        description="LEM (Local Energy Measurement) — measure power from smart plugs on the LAN.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--plugs", help="comma-separated plug aliases from the config")
    group.add_argument("--all", action="store_true", help="measure every configured plug")
    group.add_argument("--list", action="store_true", help="list configured plugs and exit")
    group.add_argument(
        "--scan", nargs="?", const="auto", metavar="SUBNET",
        help="scan the LAN for Tapo plugs and interactively add them to the config "
             "(default: local /24; e.g. --scan 10.0.0.0/24)",
    )
    parser.add_argument("--duration", help="e.g. 90s, 10m, 2h, or 'unlimited' (default from config)")
    parser.add_argument("--interval", type=float, help="seconds between samples (default from config)")
    parser.add_argument("--config", type=Path, help="path to config.toml")
    parser.add_argument("--results-dir", type=Path, help="output folder (default from config)")
    parser.add_argument("--run-name", help="basename for CSV files (default: UTC timestamp)")
    return parser


def _list_plugs(config: Config, console: Console) -> None:
    if not config.plugs:
        console.print("No plugs configured.")
        return
    for plug in config.plugs.values():
        console.print(f"  {plug.alias:<16} {plug.type:<8} {plug.ip}")


def main(argv: list[str] | None = None) -> int:
    console = Console()

    # `lem rem ...` is a separate command group (keeps the flat flag UX above).
    argv_list = list(sys.argv[1:] if argv is None else argv)
    if argv_list and argv_list[0] == "rem":
        from lem import rem_cli
        return rem_cli.main(argv_list[1:])

    args = build_parser().parse_args(argv)

    if args.scan:
        from lem.scan import run_scan
        try:
            return run_scan(args.scan, args.config, console)
        except KeyboardInterrupt:
            console.print("\nScan aborted — no changes made.")
            return 130

    try:
        config = load_config(args.config)
    except ConfigError as e:
        console.print(f"[red]Error:[/red] {e}")
        return 1

    if args.list:
        _list_plugs(config, console)
        return 0

    if not args.plugs and not args.all:
        console.print("[red]Error:[/red] specify --plugs A,B,... or --all (see --list)")
        return 1

    if args.all:
        plugs = list(config.plugs.values())
        if not plugs:
            console.print("[red]Error:[/red] no plugs configured")
            return 1
    else:
        plugs = []
        for alias in [a.strip() for a in args.plugs.split(",") if a.strip()]:
            if alias not in config.plugs:
                console.print(
                    f"[red]Error:[/red] unknown plug '{alias}' "
                    f"(configured: {', '.join(config.plugs) or 'none'})"
                )
                return 1
            plugs.append(config.plugs[alias])

    try:
        duration = parse_duration(args.duration or config.duration)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        return 1

    interval = args.interval if args.interval is not None else config.interval
    if interval <= 0:
        console.print("[red]Error:[/red] interval must be positive")
        return 1
    results_dir = args.results_dir or config.results_dir
    run_name = args.run_name or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    # If joined to REM, adopt its cadence as the interval unless the user set
    # one explicitly. Best-effort — never block measurement on REM.
    rem_client = None
    rem_state = None
    if config.rem:
        from lem.config import nickname_warnings
        for w in nickname_warnings(plugs):
            console.print(f"[yellow]⚠ {w}[/yellow]")
        from lem.rem_client import RemClient, RemError
        from lem.uploader import UploaderState
        rem_client = RemClient(config.rem.url, config.rem.token)
        rem_state = UploaderState()
        if args.interval is None:
            try:
                hello = rem_client.hello([_rem_upload_alias(config, p) for p in plugs])
                interval = float(hello.cadence_s)
                console.print(f"[green]REM:[/green] cadence {hello.cadence_s}s "
                              f"(experiment '{config.rem.experiment_name}')")
            except RemError as e:
                console.print(f"[yellow]REM not reachable ({e}); measuring anyway.[/yellow]")

    states = {p.alias: PlugState(alias=p.alias, ip=p.ip) for p in plugs}
    sink = CsvSink(results_dir)

    async def _main() -> bool:
        await sink.open(run_name, [p.alias for p in plugs])
        stop_event = asyncio.Event()
        tasks = []
        if rem_client is not None:
            from lem.uploader import init_sidecar, run_uploader
            combined = _combined_path(sink)
            init_sidecar(combined, config.rem.experiment_id)
            alias_map = {p.alias: _rem_upload_alias(config, p) for p in plugs}
            covering = list(alias_map.values())
            tasks.append(asyncio.create_task(run_uploader(
                combined, alias_map, rem_client, rem_state, stop_event, covering,
            )))
        try:
            return await run(
                plugs, states, [sink], interval, duration,
                display_coro=make_display(list(states.values()), duration, console, rem_state),
                stop_event=stop_event,
            )
        finally:
            for t in tasks:
                await t

    start = time.monotonic()
    try:
        interrupted = asyncio.run(_main())
    except KeyboardInterrupt:
        console.print("\n[red]Hard stop.[/red] Data up to this point is on disk.")
        return 130

    print_summary(list(states.values()), sink.paths, time.monotonic() - start, interrupted, console)
    return 130 if interrupted else 0


if __name__ == "__main__":
    sys.exit(main())
