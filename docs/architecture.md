# Architecture

Turtle Trader is organized as an offline research pipeline:

1. Download or import and validate historical bars.
2. Replay causal strategy signals using only data available at each decision
   time.
3. Apply simulated risk limits and execution assumptions to produce an audit
   result.
4. Persist the inputs, hashes, outputs, and audit records locally; summarize or
   export reports.

CLI commands are `download alpaca`, `validate`, `import`, `replay`, `backtest`,
`report`, `walk-forward`, and `demo`.

The current design keeps market data, strategy logic, risk policy, execution
simulation, persistence, and reporting separate. Shared input and output records
live in `domain.py`. The run result carries events, order intents, simulated
fills, equity snapshots, positions, risk decisions, rejections, and
cancellations. These records describe a model run, not brokerage activity.

`RunStore` stores a run's kind, config, result, data hash, and source hash
atomically in SQLite. Its JSON encoding preserves Decimal, date/time, and path
values and rejects non-finite numbers. JSON reports and CSV equity/fill rows are
offline exports. Neither persistence nor export uploads data.

Provider-independent historical-data and calendar protocols are implemented;
`CSVProvider` is the local data adapter. There is no scanner, service process, paper
execution adapter, or live broker adapter. Those require separate design and
validation before implementation. Source parity with external indicators also
remains unverified. A displayed source indicator is not an executable PnL
record: fills depend on timing, order behavior, costs, market sessions, data
quality, and the assumptions of the simulator.

Session validation uses exchange calendars for regular hours and early closes.
When extended-hours coverage is selected on an early-close date, the accepted
window ends conservatively at the exchange close.

## Pine reference contract

Sessions use New York 17:00 boundaries from opening timestamps. The first observed
session (even if complete) is discarded, including its close. First retained true
range is high minus low; ATR seeds with its configured arithmetic mean, then
Wilder smoothing. Session updates publish on the next observed session's first
confirmed candle. No empty sessions are synthesized or final session flushed.

Entry: `high > entry band`. Initial stop:
`max(entry band, trigger open) - ATR multiple * completed ATR`. Breakout candles
test only `low < initial stop`; later candles test `low < max(stop, exit band)`.
Equality does not exit. Initial stop is frozen; active exit is not ratcheted.
Actual accepted minute intervals include 2/3/6/10 as divisors of 30.

Signal survives scheduled ownership liquidation. Entry, reentry, signal exit and
scheduled close flags may coexist. A missed close occurs at the next observed
open, potentially alongside reentry. Reentry tests the previous signal state.
Display marks are reference events and never booked directly into portfolio cash.

## Execution and future broker boundary

The default exit path preserves Revelio behavior. Selecting Chandelier activates
an independent rolling-high/Wilder ATR component and per-signal ratchet. Current
candle exits use the incoming published threshold; new thresholds are published
after confirmation. A newly raised threshold above close ends the signal at
confirmation and queues causal execution for the next eligible opening. Event
diagnostics distinguish the tested and proposed stops. Initial-stop risk formulas
are shared unchanged. See [Chandelier specification](chandelier-exit.md).

Timestamp batches apply opening marks/loss gate, scheduled and protective exits,
pending sells, pending buys, intrabar stops, then closing marks/confirmed decisions.
Alphabetical symbol priority resolves simultaneous competition for capital. Bars
must share nonoverlapping boundaries. Only previously confirmed stops affect fills.
Inferred intrabar fills use `time_precision=intrabar_unknown` and `bar_end` to
disclose candle-bounded uncertainty. Direct `run_backtest` calls validate candle
shape and timing, but callers must use `load_dataset(strict=True)` for exchange
coverage checks. Synthetic unit-test calendars can intentionally allow sparse bars.

`Broker` exposes account, positions, orders, submit/cancel and reconciliation.
`OfflineBroker` maintains simulated holdings/orders in memory; SQLite saves audit
runs. This is not crash-resumable paper execution and makes no broker-side
protective-acceptance claim.

Next milestone requires independent simulator persistence, durable intents, fill
deduplication and broker-authoritative restart reconciliation. Persist before
submission; ambiguous acknowledgments freeze exposure and reconcile rather than
blindly resubmit. Protective coverage requires confirmation for actual filled
shares. Scanner filters, scheduler, Gatus health, Signal-compatible notifications,
and all live integration remain deferred. Explicit Alpaca historical acquisition
is implemented separately from the execution boundary.

## Acquisition and adjusted-price research

The Alpaca downloader uses a fixed market-data endpoint, an injected HTTP client,
bounded retries and token-based pagination. Temporary SQLite storage validates
and deduplicates rows before deterministic CSV export. A directory rename publishes
completed artifacts; failed acquisition publishes diagnostics without a usable
dataset. No trading client is instantiated.

The shared `coverage_report` function compares observed bars with the existing
exchange calendar and compresses missing intervals into unclassified ranges. It
does not create an expected timestamp/symbol Cartesian set or forward-fill bars.
The downloader reports gaps while the importer decides whether strict rejection
or reference-replay warnings apply.

Manifests default to the original `action_free` price basis. The explicit
`adjusted_research` basis requires full vendor adjustments and no action-free
attestation. CLI import/replay/backtest/walk-forward and regenerated reports carry
this basis and research warnings; `run_backtest` exposes an equivalent optional
argument. Signal and risk formulas remain unchanged. The execution model simulates
adjusted units, without dividend cash, split/share transformations or point-in-time
corporate-action accounting. Corporate-action discovery remains outside scope.
