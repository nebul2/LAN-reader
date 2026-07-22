# LAN-reader

A lab CLI to measure power consumption from one or more smart plugs on the LAN
(TP-Link Tapo P110 today; other plugs or PDUs are one module away). Successor to
[tapo_measure_tool](https://github.com/Quanteec/tapo_measure_tool) and
`dual_measure_tool` — no GUI, no web server, one command.

- **Multiple plugs at once** — each polled concurrently at its own drift-free interval.
- **Crash-safe** — every sample is flushed to disk as it arrives. Ctrl-C stops
  gracefully with a summary; even `kill -9` loses at most the in-flight sample.
- **Live terminal display** — per-plug power, sample count, and status, plus a
  progress bar with time remaining.
- **REM-ready data** — watts at 3-decimal precision (lossless: the P110 reports
  integer mW), UTC ISO 8601 timestamps, and a combined CSV whose columns
  (`timestamp,alias,power_w`) map 1:1 to REM's `gos_rem(time, alias, power_watts)`
  table for future export.

## Install

```sh
python3 -m venv venv && source venv/bin/activate
pip install -e .            # add '.[shelly]' for Shelly plug support, '.[dev]' for pytest
cp config.example.toml config.toml   # then edit: credentials + plug IPs
```

Requires Python ≥ 3.11.

## Use

```sh
measure --scan                                # discover Tapo plugs on the local /24
measure --scan 10.0.0.0/24                    # ...or an explicit subnet
measure --list                                # show configured plugs
measure --plugs desk,rack --duration 10m      # measure two plugs for 10 minutes
measure --all                                 # every configured plug
measure --plugs desk --interval 0.5 --duration unlimited   # until Ctrl-C
measure --plugs fake1 --duration 30s          # dry run, no hardware
```

Durations: bare seconds, `90s`, `10m`, `2h`, or `unlimited`. First Ctrl-C stops
gracefully (data is already on disk); a second one hard-exits.

`--scan` probes the subnet for hosts answering on port 80, then confirms each
one with a real Tapo handshake (so it needs your Tapo cloud credentials — from
the config, `TAPO_USERNAME`/`TAPO_PASSWORD`, or an interactive prompt). Each
device found is offered interactively: accept or refuse it, and name it (the
plug's Tapo nickname is the suggested alias). If the config already has Tapo
plugs you choose up front whether to add to them or replace them; everything
else in the file (credentials, defaults, fake plugs, comments) is preserved,
and a missing config file is created from scratch.

Each run writes to `results/` (override with `--results-dir` / `--run-name`):

- `<run>_<alias>.csv` per plug — `timestamp,power_w`
- `<run>_combined.csv` — `timestamp,alias,power_w` (REM-shaped)

## Configuration

`config.toml` (gitignored — it holds credentials; see `config.example.toml`):

```toml
[defaults]
interval    = 2.0
duration    = "10m"
results_dir = "results"

[credentials.tapo]           # TAPO_USERNAME / TAPO_PASSWORD env vars override
username = "you@example.com"
password = "changeme"

[plugs.desk]
type = "tapo"
ip   = "192.168.1.41"
```

Any per-plug key besides `type`/`ip` is passed to the device driver — credential
overrides, the fake device's `fail_rate`, or (one day) a PDU's outlet number.

## Extending

- **New device type** (plug, PDU): add one module in `src/measure/devices/`
  implementing `BaseDevice` (`connect` / `get_power_mw` / `disconnect`) and
  register it in `DEVICE_TYPES` in `devices/__init__.py`. Nothing else changes.
- **New output** (e.g. pushing to REM): add a module in `src/measure/sinks/`
  implementing `BaseSink` (`open` / `write` / `close`) and append it to the sink
  list in `cli.py`. The measurement loop is sink-agnostic.

## Test

```sh
pytest                                   # parsing/config unit tests
measure --plugs fake1 --duration 10s     # end-to-end without hardware
```

The `fake` device type generates ~40 W synthetic data; give it `fail_rate = 0.5`
in config to exercise the retry/reconnect path.
