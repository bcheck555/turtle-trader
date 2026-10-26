# Turtle Trader

Turtle Trader replays indicator signals and backtests causal equity strategies.
An explicit Alpaca command downloads historical market data. No brokerage order
connection or live execution module is included.

The supplied Revelio Pine indicator is translated into a reference state machine.
Revelio remains the default exit; a configurable Chandelier alternative is available.
Parity against exported TradingView observations is not independently verified. An
indicator signal is a research event; it is not an executable fill or a promise
of profit. Backtest results depend on supplied data and execution assumptions.

## Install

Requires Python 3.12 or newer.

```sh
uv sync --extra dev
```

## CLI

```sh
uv run turtle-trader demo runs/demo
uv run turtle-trader validate runs/demo/bars.csv --manifest runs/demo/data.toml
uv run turtle-trader import runs/demo/bars.csv --manifest runs/demo/data.toml
uv run turtle-trader replay runs/demo/bars.csv --manifest runs/demo/data.toml
uv run turtle-trader backtest runs/demo/bars.csv --manifest runs/demo/data.toml \
  --config configs/swing.toml --output runs/swing.json
uv run turtle-trader backtest runs/demo/bars.csv --manifest runs/demo/data.toml \
  --config configs/reference.toml --output runs/reference.json
```

Demo data is explicitly synthetic with sparse custom coverage; its results have
no market-performance interpretation. Outputs include a run ID, JSON report and
equity/fill CSV. Use `report RUN_ID --output runs/report.json` to regenerate a
saved report; `--database` defaults to `runs/turtle.sqlite`. All commands have help.

Use `--trade-start 2026-02-01T00:00:00-05:00` and `--trade-end` for a chronological
holdout. Earlier bars warm indicators without trading; exposure waits for a new
entry/reentry event. End positions remain marked, without invented liquidation.
`walk-forward --warmup-days 60 --fold-days 30` runs independent chronological folds;
it does not optimize parameters or tune on test folds. Supply enough history for
the configured indicator lengths.

Optional `--benchmark` CSV requires `timestamp,price`, timezone offsets, and
total-return-adjusted prices. Endpoints must match equity snapshot times exactly;
reports disclose missing matches. Static universes may introduce survivorship
bias; historical membership/delisted securities are not automatically supplied.

Runs can be saved locally with `RunStore(path)`. Each saved run records its
configuration, result and data/source hashes in a single SQLite transaction.
Results include the strategy events and any simulated orders, fills, positions,
risk decisions, rejections, and cancellations produced by the offline engine.
Metadata includes engine version and config/data/source SHA-256 hashes; the
committed lockfile captures dependency versions. No random model is used.

## Data format

CSV columns: `symbol,timestamp,open,high,low,close,volume`. Timestamps identify bar
openings and require timezone offsets. Bars must be chronological per symbol;
interleaved symbols are supported. Supply ordinary OHLC candles, not transformed
Heikin-Ashi/Renko data. No resampling is implicit.

```toml
[data]
feed = "your-export-feed"
interval_minutes = 5
coverage = "regular" # regular or extended
adjustment = "split" # split or all; consistent across OHLC
action_free = true
start_date = "2026-01-05"
end_date = "2026-01-30"
symbols = ["SPY", "QQQ"]
timestamp_convention = "bar_open"
```

Every declared symbol/date must cover all expected candles in the selected XNYS
window. Missing bars, including truncated first/last dates, fail performance runs.
Halts/no-trade periods need external review; the importer does not distinguish
them from vendor outages. `replay --allow-gaps` retains source behavior with
warnings. No candles are forward-filled. Extended hours are 04:00–20:00 New York
on exchange dates; early-close dates conservatively end at the regular close.

Legacy manifests require consistently adjusted, action-free data. An explicit
`price_basis="adjusted_research"` mode also accepts `adjustment="all"` with
`action_free=false` (action-free status is not attested). Downloader exports use
this mode. Quantities, fees, equity and returns then describe an adjusted-price
simulation, not an actual account or point-in-time performance. Vendor adjustments
may incorporate future events. No dividend cash or split-related share changes
are booked. Custom coverage requires explicit `synthetic=true`.

## Alpaca historical downloads

Set `APCA_API_KEY_ID` and `APCA_API_SECRET_KEY` in the command's environment; keys
are not CLI arguments or configuration fields. Then run:

```sh
uv run turtle-trader download alpaca --symbols SPY QQQ \
  --start 2026-01-02 --end 2026-06-30 --output runs/alpaca-spy-qqq
uv run turtle-trader validate runs/alpaca-spy-qqq/bars.csv \
  --manifest runs/alpaca-spy-qqq/manifest.toml
uv run turtle-trader backtest runs/alpaca-spy-qqq/bars.csv \
  --manifest runs/alpaca-spy-qqq/manifest.toml --config configs/swing.toml \
  --output runs/alpaca-swing.json
```

Defaults are five-minute bars, consolidated `sip`, regular hours and full vendor
adjustments. `--interval`, `--feed iex`, and `--coverage extended` are explicit
alternatives. IEX is one exchange, so its OHLCV and gaps differ from consolidated
SIP. Feed entitlement errors never cause automatic fallback. Dates are inclusive
New York dates strictly before today, with the requested SIP endpoint at least
16 minutes old. No warmup period is silently added.

Outputs are `bars.csv`, `manifest.toml`, `coverage.json` and `provenance.json`.
Coverage lists absent symbols and unclassified missing ranges by symbol/session.
A missing bar does not establish an outage or no trading; no candles are fabricated.
Out-of-hours rows are filtered and counted. Existing output directories are refused.

Exit **0** means ready for adjusted-price research; **3** means acquisition
completed but coverage is incomplete. Exit **3** keeps all files for inspection
and `replay --allow-gaps`; strict validation/backtesting still reject gaps.
Exit **2** means input/acquisition failure; acquisition failures publish an error
report without an apparently complete CSV/manifest. Pagination failures cannot
publish a successful dataset. Requests are bounded and retried only for transient
failures. Downloads never call trading endpoints.

See [downloader details](docs/alpaca-downloader.md) for acquisition assumptions and
provenance. All automated tests mock network access; no live API validation is
performed during development.

## Strategy versus execution

To use Chandelier with overnight holdings, pass `--config configs/chandelier-swing.toml`
to `replay`, `backtest` or `walk-forward`. Defaults are a 22-session highest high
minus 3 × Wilder ATR(22), ratcheted upward during the underlying signal. The fixed
initial stop remains a floor; the 10-session exit band is ignored in this mode.
Set `chandelier_timeframe="intraday"` to use completed input candles, or
`chandelier_ratchet=false` for the raw line that can rise or fall. Existing settings
for initial-stop ATR, breakout entries and portfolio risk remain separate.

A current candle is tested against its incoming stop. Its confirmed high/range
can update only the subsequent stop. If that replacement is already above the
closing price, the system emits `new_stop_close_breach` and exits any actual holding
at the next eligible opening. No same-candle fill at the newly calculated stop is
invented. Chandelier results are labeled as a derived strategy without Revelio
Pine parity. See [Chandelier details](docs/chandelier-exit.md).

Reference replay preserves confirmed-bar events and retrospective marker prices.
The indicator has no execution or PnL model. Causal simulation fills new orders at
a later eligible opening, adds adverse slippage/fees and retains the source stop.
Same-candle indicator round trips ending flat create no causal entry. Previously
active stops can execute within subsequent candles; entry-bar stop collisions use
adverse ordering and are reported as ambiguous.
Intrabar fills carry `time_precision=intrabar_unknown` with opening/closing candle
bounds; their ordering timestamp is not a claim of an exact observed fill time.

Participation uses full historical candle volume as a liquidity scenario, not
volume known at its opening or actual queue priority. Partial stop fills can leave
exposure pending. Stops do not guarantee maximum loss. Missing deadline quotes
can cause late scheduled exits and weekend exposure; lateness is recorded rather
than inventing a timely fill. Regular-hours execution cannot satisfy 16:30/18:00;
use extended coverage for the reference comparison. Swing mode holds overnight.

Defaults: $25,000 cash, 0.5% planned risk, 15% new-position allocation, five
positions, no margin. The 2% daily marked-equity loss gate blocks new entries and
cancels pending buys; it does not force liquidation. Baseline resets on the New
York date using last observed equity before that day's first opening gap.
External cash flows are unsupported in this offline milestone.

## Scope and limitations

- Historical Alpaca data is fetched only through an explicit download command;
  replay and backtesting operate on local files.
- Source-code indicator parity is an external validation task. Matching a
  displayed signal does not establish matching fills or account performance.
- Simulated prices, slippage, fees, participation, and calendar behavior are
  model assumptions, not broker quotes or execution guarantees.
- Exchange calendars cover regular sessions and early closes. For extended-hours
  coverage, data validation conservatively ends the session at the exchange
  close on early-close dates.
- Broker integrations, paper execution, live execution, scanning, and scheduled
  data services are deferred. Robinhood feasibility and documented boundaries are in
  [the feasibility note](docs/robinhood-feasibility.md).

## Development

Run `uv run pytest`, `uv run ruff check src tests`, and `uv run mypy`.
See [architecture](docs/architecture.md)
for module boundaries and data flow.
