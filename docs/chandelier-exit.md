# Configurable Chandelier Exit

Select `exit_strategy="chandelier"` in the `[strategy]` TOML table. Existing
configuration files default to `revelio`, preserving the supplied Pine indicator's
exit behavior. Chandelier is a derived strategy; no Revelio Pine parity is claimed.

## Parameters

| Setting | Default | Meaning |
| --- | --- | --- |
| `exit_strategy` | `revelio` | `revelio` or `chandelier` |
| `chandelier_timeframe` | `session` | Completed strategy sessions or `intraday` input candles |
| `chandelier_length` | `22` | Rolling highest-high window, integer 2–400 |
| `chandelier_atr_length` | `22` | Independent Wilder ATR length, integer 2–400 |
| `chandelier_multiple` | `3.0` | Finite positive ATR multiplier |
| `chandelier_ratchet` | `true` | Prevent the selected Chandelier stop from decreasing |

Raw level = rolling highest high − multiplier × Chandelier ATR.
Ratcheted level = highest raw Chandelier level reached during the underlying
signal. Active stop = higher of the fixed initial stop and selected Chandelier
level. The raw variant may decrease, while the fixed initial-stop floor remains.
Neither variant uses the 10-session low band to decide exits or entry readiness.

The high window is rolling across all supplied observations, including history
before a trade. It is not a highest-high-since-entry calculation. The initial stop
still uses the original breakout/open formula and initial-stop ATR settings.
Chandelier ATR does not replace that risk anchor or re-anchor it to actual fills.

## Periods and availability

Session mode uses 17:00–17:00 New York identities, discards the first loaded
session including its close, and finalizes the previous observed session when
the first candle of the next session confirms. No empty sessions are fabricated
and the final observed session is not flushed into its own indicators. Session
Chandelier ATR seeds from retained session true ranges independently of the
initial-stop ATR.

Intraday mode uses completed input candles at `interval_minutes`. Its first true
range is high minus low; subsequent true ranges include the previous candle close.
After seeding with the configured arithmetic mean, ATR follows Wilder smoothing.
All supplied observations participate, including hours when ownership is forbidden.
Unconfirmed previews never update indicators, trade state or stops.

Both modes require the breakout window, initial-stop ATR, Chandelier high window
and Chandelier ATR to be ready before entry. There is no fallback to Revelio while
Chandelier warms up. On five-minute input, 22 intraday observations represent 110
minutes of supplied candles, whereas 22 sessions represent a substantially longer
history.

## Causal exit ordering

1. Snapshot the stop published before the candle opened.
2. For an established signal, test `low < incoming stop`. Equality does not exit.
   A new breakout candle keeps its initial-stop-only low test.
3. At confirmation, update the Chandelier indicator and form the replacement stop.
   Never retest the candle's low against a line containing that candle's high/TR.
4. If `close < replacement stop`, end the signal with
   `new_stop_close_breach`, recording the close and proposed threshold. Actual
   ownership exits through the existing next-eligible-opening execution path,
   including gaps, fees, slippage and partial fills.
5. Otherwise publish the replacement for subsequent candles.

An entry/reentry and close breach can coexist. A flat-to-flat signal round trip
creates no actual entry; an existing partial holding exits and its remaining buy
quantity is canceled. Pending entries opening at or below their latest published
protection remain rejected by the existing conservative guard.

Ratchet state survives scheduled daily liquidation and updates while the signal
remains active, even when ownership is flat. Reentry tests the incoming stop.
Ending the underlying signal clears the ratchet; a new breakout starts a new one.
The default Revelio path retains its original ordering and same-bar exceptions.

## Usage and diagnostics

`configs/chandelier-swing.toml` selects regular-hours execution with overnight
holdings and default Chandelier parameters. Use it with the same dataset as
`configs/swing.toml` to compare exits:

```sh
uv run turtle-trader backtest runs/alpaca-spy-qqq/bars.csv \
  --manifest runs/alpaca-spy-qqq/manifest.toml \
  --config configs/chandelier-swing.toml --output runs/chandelier.json
```

Events distinguish raw Chandelier level, selected Chandelier stop, incoming tested
stop, proposed stop, Chandelier ATR and exit reason. Existing `atr` remains the
initial-stop ATR stream and `exit_level` remains the unused 10-session band
diagnostic in Chandelier mode. `active_stop` is the published future threshold.

Saved runs and JSON/CSV reports retain exit identity, strategy settings and the
derived-strategy disclosure, alongside any adjusted-price research warnings.
Per-equity strategy profiles are not part of this change; all symbols in a run
still receive the same configured settings with independent state.

The default 22/3 rolling formula is described in
[StockCharts' Chandelier documentation](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/chandelier-exit).
Ratcheting and the causal close-breach behavior above are explicit application
policies. No improvement in trading results is assumed.
See [validation evidence](chandelier-validation.md) for checks, review and boundaries.
