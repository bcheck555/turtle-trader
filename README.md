# Turtle Trader

Offline equity research implementing the Turtle rules described in
[Curtis Faith's firsthand account](https://www.pipsafe.com/Forex-E-Books/turtlerules.pdf).
System 2 is the default; System 1 and Chandelier exits are selectable. Shorts are
disabled by default. Historical data acquisition is explicit; no brokerage order
connection or live execution is included.

The [packaged rule specification](src/turtle_trader/rule_spec.md) separates source
mechanics from deterministic conventions and equity adaptations. Results depend
on data quality, cash constraints and execution assumptions.

## Install and run

Requires Python 3.12 or newer.

```sh
uv sync --extra dev
uv run turtle-trader demo runs/demo
uv run turtle-trader validate runs/demo/bars.csv --manifest runs/demo/data.toml
uv run turtle-trader backtest runs/demo/bars.csv --manifest runs/demo/data.toml \
  --config configs/system2.toml --output-dir runs/system2
```

Demo data is synthetic and has no market-performance interpretation. For real
datasets, supply enough completed sessions to warm all selected indicators.

## Command-line settings and experiments

All backtest configuration fields accept repeatable `--set` overrides. Values use
TOML syntax; quote string values inside the shell argument. Precedence is built-in
defaults, configuration file, then command-line settings. Unknown settings and
duplicate override assignments are errors.

```sh
uv run turtle-trader backtest --config configs/system1.toml --print-config
uv run turtle-trader backtest runs/data/bars.csv --manifest runs/data/manifest.toml \
  --config configs/system2.toml --set 'strategy.entry_length=70' \
  --set 'risk.unit_fraction="0.005"' --output-dir runs/entry70
uv run turtle-trader backtest runs/data/bars.csv --manifest runs/data/manifest.toml \
  --set 'strategy.exit_strategy="chandelier"' \
  --set 'strategy.chandelier_multiple=3.5' --output-dir runs/chandelier35
```

Use `--trade-start` and `--trade-end` with timezone-aware timestamps for a fixed
test window. Earlier data warms indicators without executing trades. Final open
positions remain marked. Use `walk-forward` for independent chronological folds;
it does not automatically optimize on held-out periods.

Run deterministic Cartesian parameter grids with an isolated directory/database
per combination:

```sh
uv run turtle-trader sweep runs/data/bars.csv --manifest runs/data/manifest.toml \
  --config configs/system2.toml --grid configs/parameter-grid.toml \
  --workers 2 --max-runs 100 --output-dir runs/entry-experiment
uv run turtle-trader sweep runs/data/bars.csv --manifest runs/data/manifest.toml \
  --config configs/system2.toml --grid configs/parameter-grid.toml \
  --workers 2 --output-dir runs/entry-experiment --resume
```

The grid's `[parameters]` table maps quoted dotted configuration keys to nonempty
candidate arrays. The sample grid creates six runs. Defaults are one worker and
100 maximum combinations. Resume skips identical completed runs and retries
failed/incomplete runs; incompatible directory reuse is refused. Automated agents
should use distinct output directories and consume JSON summaries/statuses.
Sweep exit codes are `0` for all successful, `1` for execution failures, and `2`
for invalid input. Individual failures remain in aggregate JSON/CSV. Rankings
default to total return and describe only the requested data/test window.

Output directories include resolved configuration and report artifacts. Reports
record parameters, canonical deviations, data identity, rule-specification hash,
implementation version, execution assumptions and run fingerprint. `--output`
remains available for an explicit report filename and cannot accompany
`--output-dir`. The new default database is `runs/turtle-v2.sqlite`. Old trading
configurations and report/database formats are rejected rather than reinterpreted.

See [configuration](docs/configuration.md) for all settings and presets.

## Rules and execution

System 2 uses 55-session entry and opposite 20-session exit channels. System 1
uses 20-session entries, a winner filter, 55-session failsafe and 10-session exits.
Entries/exits trigger intraday, one tick beyond preceding completed-session
channels. Indicators never use an unfinished session to calculate its own levels.

N and whole-share unit sizes publish before the week's first exchange session.
Campaign N and intended shares freeze at initial entry. A unit sizes a 1N move to
1% of sizing capital; its initial 2N stop therefore represents approximately 2%
before costs/gaps. Units add every half N from actual fills, up to four per symbol.
Earlier unit stops move half N per addition and remain separate. Portfolio caps
are 6/10/12 units across close/loose correlation groups and each direction.

Long units must fit cash; unaffordable units are rejected, not shrunk. Default
correlation groups conservatively include the whole universe. Drawdown reductions
immediately cap new-campaign unit sizing; existing campaigns keep their frozen
shares. Sizing capital becomes zero at the geometric loss-threshold limit.
There are no daily liquidation/reentry schedules.
System 1 always tracks hypothetical long and short breakouts independently of
actual fills and alternate exits; disabling actual shorts does not change its
winner filter.

Chandelier defaults to a 22-session high/low window, ATR(22), 3× multiple and a
ratchet. It replaces channel exits while mandatory unit stops remain active.
Completed observations can update only subsequent execution levels. See
[Chandelier details](docs/chandelier-exit.md).

When OHLC leaves order sequence ambiguous, simulate synchronized high-first and
low-first paths across the candle batch, using a shared normalized segment clock.
Process reductions before increases at each shared timestamp and symbols in
stable order for ties. Choose lower aggregate closing equity, high-first on ties.
Reports disclose ambiguity. Fills inside a candle do not claim an exact observed
timestamp. Participation uses full historical candle volume as a liquidity
scenario, not queue priority or volume known at the opening.

Optional shorts require `strategy.allow_short=true`, an explicit
`execution.shortable_symbols` allowlist and `execution.borrow_rate`. Proceeds are
locked with 50% additional entry collateral; maintenance requires 130% of current
liability. Borrow fees accrue over calendar days. Observed collateral breaches
queue covers at subsequent tradable quotes. This models borrowing assumptions,
not historical stock-loan availability, actual margin calls or broker guarantees.

## Data and historical downloads

CSV columns are `symbol,timestamp,open,high,low,close,volume`. Timestamps identify
bar openings and require timezone offsets. Supply ordinary OHLC candles with
chronological per-symbol timestamps. Interleaved symbols are supported.

```toml
[data]
feed = "your-export-feed"
interval_minutes = 5
coverage = "regular"
adjustment = "split"
action_free = true
start_date = "2026-01-05"
end_date = "2026-06-30"
symbols = ["SPY", "QQQ"]
timestamp_convention = "bar_open"
```

Strategy sessions use the regular exchange calendar, including early closes and
daylight saving. Incomplete internal sessions are errors; partial boundary sessions
are excluded. [Verified market-wide pauses](docs/market-halts.md) exempt only fully
halted candle intervals, with separate coverage counts. Reopening fills use the
next observed price and cannot precede the verified restart. No candles are
forward-filled. Other missing bars remain unclassified and fail strict validation.

Explicit `price_basis="adjusted_research"` with `adjustment="all"` and
`action_free=false` accepts vendor-adjusted data without attesting action-free
status. Quantities, fees and returns then describe an adjusted-price simulation,
not a point-in-time account. Adjustments can incorporate future events; no dividend
cash or split-related share changes are booked. Custom coverage requires
`synthetic=true`. Static symbol universes can introduce survivorship bias.

Set `APCA_API_KEY_ID` and `APCA_API_SECRET_KEY` in the environment; keys are never
CLI/configuration values. Acquisition is a separate explicit command:

```sh
uv run turtle-trader download alpaca --symbols SPY QQQ \
  --start 2026-01-02 --end 2026-06-30 --output runs/alpaca-spy-qqq
uv run turtle-trader validate runs/alpaca-spy-qqq/bars.csv \
  --manifest runs/alpaca-spy-qqq/manifest.toml
uv run turtle-trader backtest runs/alpaca-spy-qqq/bars.csv \
  --manifest runs/alpaca-spy-qqq/manifest.toml --config configs/system2.toml \
  --output-dir runs/alpaca-system2
```

Defaults are five-minute consolidated SIP bars, regular hours and full vendor
adjustments. IEX and extended acquisition coverage are explicit alternatives;
strategy execution remains regular-session based. Entitlement errors never cause
automatic feed fallback. Dates are inclusive New York dates strictly before today;
the SIP endpoint must be at least 16 minutes old. No warmup is added silently.

Downloads write `bars.csv`, `manifest.toml`, `coverage.json` and `provenance.json`.
Existing output directories are refused. Exit `0` means complete acquisition;
`3` preserves incomplete coverage for inspection; `2` means acquisition/input
failure. No candles are fabricated and pagination failures cannot publish a
successful dataset. See [downloader details](docs/alpaca-downloader.md).

## Development

Run `uv run pytest`, `uv run ruff check src tests`, `uv run mypy`, and `uv build`.
See [architecture](docs/architecture.md) and [validation](docs/validation.md).
No random execution model or live brokerage connection is included.
