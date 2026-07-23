"""`lem rem ...` subcommands: join / status / sync / leave.

Kept separate from the flat measurement CLI so the everyday `lem --plugs ...`
UX is untouched.
"""

import argparse
import asyncio
from pathlib import Path

from rich.console import Console

from lem.config import ConfigError, load_config, upload_alias
from lem.rem_client import RemClient, RemError, parse_join_code
from lem.scan import remove_rem_section, write_rem_section
from lem.uploader import UploaderState, find_unsynced, sync_run


def _config_path(args) -> Path:
    from lem.config import DEFAULT_PATHS
    return args.config or next((p for p in DEFAULT_PATHS if p.exists()), DEFAULT_PATHS[0])


def _cmd_join(args, console: Console) -> int:
    try:
        join = parse_join_code(args.code)
    except RemError as e:
        console.print(f"[red]{e}[/red]")
        return 1
    client = RemClient(join.url, join.token)
    try:
        hello = client.hello()
    except RemError as e:
        console.print(f"[red]Could not join:[/red] {e}")
        return 1

    path = _config_path(args)
    write_rem_section(path, join.url, join.token, hello.experiment_id, hello.experiment_name)
    console.print(f"[green]Joined[/green] experiment '{hello.experiment_name}' at {join.url}")
    console.print(f"  Measurement cadence set by REM: {hello.cadence_s}s")
    console.print("  LEM measures locally and never contacts the TP-Link cloud — "
                  "data goes only to your REM server.")
    if hello.clock_skew_s > 30:
        console.print(f"  [yellow]Warning:[/yellow] this machine's clock differs from REM by "
                      f"~{hello.clock_skew_s:.0f}s. Fix the clock so data lands at the right time.")
    console.print(f"  Config updated: {path}")
    return 0


def _cmd_leave(args, console: Console) -> int:
    path = _config_path(args)
    if not path.exists():
        console.print("No config file; nothing to leave.")
        return 0
    path.write_text(remove_rem_section(path.read_text()))
    console.print(f"Left the REM experiment. [rem] removed from {path}.")
    return 0


def _cmd_status(args, console: Console) -> int:
    path = _config_path(args)
    try:
        config = load_config(path)
    except ConfigError as e:
        console.print(f"[red]Error:[/red] {e}")
        return 1
    if not config.rem:
        console.print("Not joined to any REM experiment. Use 'lem rem join <code>'.")
        return 0
    console.print(f"Joined: '{config.rem.experiment_name}' at {config.rem.url}")
    client = RemClient(config.rem.url, config.rem.token)
    try:
        hello = client.hello()
        console.print(f"  REM reachable. Cadence {hello.cadence_s}s.")
        st = client.status()
        for alias, info in (st.get("aliases") or {}).items():
            console.print(f"    {alias}: {info.get('rows_total', 0)} rows, "
                          f"last {info.get('last_upload_at', 'never')}")
    except RemError as e:
        console.print(f"  [yellow]REM not reachable:[/yellow] {e}")
    unsynced = find_unsynced(config.results_dir)
    if unsynced:
        console.print(f"  {len(unsynced)} local run(s) not fully uploaded — "
                      f"run 'lem rem sync' to backfill.")
    return 0


def _cmd_sync(args, console: Console) -> int:
    path = _config_path(args)
    try:
        config = load_config(path)
    except ConfigError as e:
        console.print(f"[red]Error:[/red] {e}")
        return 1
    if not config.rem:
        console.print("[red]Not joined.[/red] Use 'lem rem join <code>' first.")
        return 1
    alias_map = {p.alias: upload_alias(p) for p in config.plugs.values()}
    client = RemClient(config.rem.url, config.rem.token)

    if args.file:
        targets = [Path(args.file)]
    else:
        targets = find_unsynced(config.results_dir)
    if not targets:
        console.print("Nothing to sync — all runs are uploaded.")
        return 0

    async def _run():
        for combined in targets:
            state = UploaderState()
            console.print(f"Uploading {combined.name} …")
            await sync_run(combined, alias_map, client, state, hello_max_rows())
            console.print(f"  done: {state.rows_uploaded} rows.")

    def hello_max_rows():
        try:
            return client.hello().max_batch_rows
        except RemError:
            return 10000

    try:
        asyncio.run(_run())
    except RemError as e:
        console.print(f"[red]Sync failed:[/red] {e}")
        return 1
    return 0


def _cmd_export(args, console: Console) -> int:
    """Write a REM-ready CSV (timestamp,alias,power_w with the Tapo nickname)
    so a run can be hand-imported at REM even without this machine's config."""
    import csv
    path = _config_path(args)
    try:
        config = load_config(path)
    except ConfigError as e:
        console.print(f"[red]Error:[/red] {e}")
        return 1
    alias_map = {p.alias: upload_alias(p) for p in config.plugs.values()}
    targets = [Path(args.file)] if args.file else sorted(config.results_dir.glob("*_combined.csv"))
    if not targets:
        console.print("No combined CSVs found.")
        return 1
    for combined in targets:
        out = combined.with_name(combined.name.replace("_combined.csv", "_rem.csv"))
        with open(combined) as fin, open(out, "w", newline="") as fout:
            r = csv.reader(fin)
            w = csv.writer(fout)
            header = next(r, None)
            w.writerow(["timestamp", "alias", "power_w"])
            for row in r:
                if len(row) == 3:
                    ts, alias, power = row
                    w.writerow([ts, alias_map.get(alias, alias), power])
        console.print(f"  wrote {out.name}  (REM identity / Tapo nicknames)")
    return 0


def main(argv: list[str]) -> int:
    console = Console()
    parser = argparse.ArgumentParser(prog="lem rem", description="Connect LEM to a REM experiment.")
    parser.add_argument("--config", type=Path, help="path to config.toml")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_join = sub.add_parser("join", help="join an experiment with a REM join code")
    p_join.add_argument("code", help="the REM1-... join code from your operator")
    sub.add_parser("status", help="show REM connection and upload status")
    p_sync = sub.add_parser("sync", help="upload any local runs not yet in REM")
    p_sync.add_argument("--file", help="upload a specific combined CSV")
    p_export = sub.add_parser("export", help="write REM-ready CSV(s) (Tapo nicknames) for hand-import")
    p_export.add_argument("--file", help="export a specific combined CSV")
    sub.add_parser("leave", help="disconnect from the REM experiment")

    args = parser.parse_args(argv)
    return {
        "join": _cmd_join, "leave": _cmd_leave,
        "status": _cmd_status, "sync": _cmd_sync, "export": _cmd_export,
    }[args.cmd](args, console)
