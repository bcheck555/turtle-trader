# Squeeze Momentum research

The `squeeze_momentum` entry and `atr_bracket` exit implement an independent
interpretation of LazyBear's Squeeze Momentum plus optional ADX and WMA entry
filters. They are not a reproduction of AlgoTrade Pro's proprietary Signal
Engine or its reported performance.

Sources: [LazyBear's indicator](https://www.tradingview.com/script/nqQ1DT5a-Squeeze-Momentum-Indicator-LazyBear/)
and the [AlgoTrade Pro video](https://www.youtube.com/watch?v=RrtIKmqzO88).

Use the preset with the ordinary backtest command and an appropriate 30-minute
dataset. Every `squeeze_*` strategy setting accepts existing `--set` overrides
and parameter sweeps. The strategy uses completed intraday candles, not daily
Turtle channels or weekly N. ATR is fixed from the signal candle, entry waits
until the next eligible candle, and stop/target levels anchor to actual fills.

Defaults are BB(20, 2), KC(20, 1.5) using averaged true range, LazyBear's
20-period regression momentum, ADX(14,14)>20, WMA(120) of close, ATR(14),
2× ATR stop and 3× ATR target. Baseline/ADX only qualify new signals. Strict
squeeze-inside to squeeze-outside transitions generate signals; equality and
neutral states do not. All required observations must be available first.

The preset uses $10,000 starting capital and a 0.015 ATR sizing fraction,
equivalent to 3% planned stop risk before costs with a 2× ATR stop. Squeeze
whole-share entries are capped by available cash. With low intraday ATR this
can reduce actual risk considerably below 3%; it does not imply leveraged
purchasing power. Pyramiding is disabled. Long-only is the default; opposite
qualified signals close a long position when reversal is enabled. Actual short
entries still require the existing allowlist, borrow rate, and collateral rules.

The video confirms ATR(14), ADX(14,14), WMA(120), both directions enabled,
opposite-signal reversals, and 3% risk per trade. Its later two-leg mode takes
50% at the fixed target, moves the remaining stop to at least breakeven, then
trails at 2× ATR. This version implements one fixed target only. The modified
indicator's BB bug toggle, exact signal source, trailing reference, and broker
execution assumptions remain unverified. Corrected BB calculations are used.

## Cached comparison

```sh
uv run python research/run_squeeze_comparison.py \
  --output-dir runs/squeeze-qqq-30m-first
```

The runner streams cached QQQ minute data into complete 30-minute candles,
aligned to XNYS sessions, including early closes. Verified halt minutes are
excluded from expected observations; no synthetic quotes are inserted. Missing
ordinary minutes in internal sessions stop the experiment. Incomplete boundary
sessions are dropped. The derived CSV/manifest and source hashes are saved.

Four variants use fixed parameters: raw Squeeze, ADX only, WMA only, and both.
Buy-and-hold and Turtle System 2 are references. All use $10,000, with 5 bps
slippage per fill and zero commissions. The Turtle reference retains its weekly
daily-ATR sizing and rejects insufficient-cash entries rather than applying the
Squeeze cash-size cap; its sizing is therefore not mechanically identical.

The first 120 included sessions warm the daily Turtle reference and intraday
indicators, then all full-period evaluations share one starting timestamp. The
chronological 70/30 split is defined over the complete dataset before burn-in.
The in-sample portion ends at that boundary; held-out runs start flat with fresh
capital, retaining only preceding indicator history. There is no optimization.
Small test fixtures with fewer sessions use a shorter burn-in and are not
performance evidence.

Each new output directory contains `report.md`, `comparison.json`, provenance,
derived data, and per-period/per-variant summaries, fills, equity, and audit
records. Reruns refuse an existing output directory. Reports use existing
fee-inclusive flat-to-flat campaign statistics; open positions are marked but
excluded from closed-trade win rates and profit factors. Adjusted historical
equity data, OHLC execution ambiguity, cash constraints, and the chosen costs
limit interpretation. Held-out rankings do not establish future profitability.

## AAPL video comparison

Download the earliest available regular-session AAPL SIP minute history with:

```sh
uv run python research/download_aapl_history.py --end 2026-10-26
```

The helper discovers the first actual regular-session bar and reads only the
two Alpaca credential assignments from the repository `.env` when they are not
already set in the environment. It never evaluates that file as shell code.
Download dates follow the existing policy of excluding the current New York date.

The first acquisition retained January 4, 2016 onward, including 59 unknown
missing minute slots on February 2 and February 22. Targeted provider requests
still returned no bars in those ranges. Preserve that raw archive. The following
verified derivative starts after the last gap; it does not fabricate candles:

```sh
uv run python -m turtle_trader.archive \
  runs/aapl-1m-20160104-20261026 runs/aapl-1m-20160223-20261026 \
  --cutoff 2016-02-23
uv run python -m research.run_aapl_squeeze \
  --source-csv runs/aapl-1m-20160223-20261026/bars.csv \
  --source-manifest runs/aapl-1m-20160223-20261026/manifest.toml \
  --output-dir runs/squeeze-aapl-30m-repeat
```

This cutoff belongs to the recorded acquisition; inspect coverage before
choosing a cutoff for any new dataset. The AAPL runner compares all available
continuous history and both date ranges visible in the video, with explicit
long/short, execution-cost, and legacy BB-width sensitivities. These are fixed
comparison hypotheses, not a parameter optimization. The original modified
script's final bug-toggle state and advanced trailing implementation remain
unknown. See the [AAPL report](../research/aapl-squeeze-report.md) for results.
