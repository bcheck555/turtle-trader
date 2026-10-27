# QQQ velocity study — October 27, 2026

**No dependable fixed candle-count continuation or cost-resilient trading edge established.** Large intraday candles usually stop extending in the same candle-body direction within zero or one additional candle. Only roughly 8–14% of completed validation runs reach three additional same-direction candles. A sequence of red/green bodies is not necessarily a sequence of falling/rising closes.

Of 240 tested follow/fade recipes, 184 have at least 100 discovery and 30 validation trades; **0** have positive mean net returns in both periods at 5bps per side. The discovery-selected recipe returns 3.58bps/trade before 2025, then -6.17bps/trade in 2025–2026; both uncertainty intervals cross zero.

One-minute gross follow-through is tiny relative to assumed trading costs. Large daily drops show a rebound in this validation sample, but only 19 next-candle observations exist; daily impulses and longer holds need more evidence. No strategy changes or live orders.

## Data and definitions

QQQ SIP fully adjusted regular-session minute archive: 2016-02-23–2026-10-23; 1,042,924 minute bars, 2684 sessions. SHA-256: `551fd5feed7c8b230d90c4f36095a802ab095eacaa7709f1908b232dc6effe93`.

Candles: 1, 5, 15, 30 minutes and daily. Intraday candles anchored at session opening; incomplete halt buckets dropped, no invented bars. Intraday signal, entry and exit must fit in one session with no missing interval; daily trades cross sessions. Daily candle means session open-to-close, not close-to-close. Signals include green/red candle bodies; overnight gaps and multi-candle acceleration are not tested.

Large = absolute log(open-to-close return) at least 2× or 3× RMS of the previous 60 candle body returns, excluding current candle. This is a volatility scale, not a Gaussian significance score. Scale carries across sessions and does not adjust for time-of-day seasonality. The body sign defines direction. Zero-body candles stop streaks.

Discovery: before 2025; chronological validation: 2025–2026. Earlier warmup bars excluded. Validation already used in other research; not pristine out-of-sample evidence. All choices are exploratory and this grid increases selection bias.

## How long does the move continue?

2× impulse threshold. “Next same” uses next candle body sign, not a close-to-close change. Median additional run excludes right-censored session/period/archive boundary runs; boundary counts in JSON. It describes uninterrupted candle colors, not time until a price peak. Signal candle itself is excluded.

| Candle | Impulse | Discovery next same | Validation next same | Unconditional validation direction | Validation observations | Median additional candles (complete runs) | Validation run ≥3 (complete runs) |
|---|---|---:|---:|---:|---:|---:|---:|
| 1m | Up | 46.53% | 49.61% | 49.46% | 4644 | 0.00 | 11.88% |
| 1m | Down | 45.42% | 46.84% | 48.29% | 5282 | 0.00 | 10.05% |
| 5m | Up | 50.33% | 51.49% | 50.37% | 1043 | 1.00 | 13.72% |
| 5m | Down | 46.62% | 47.85% | 48.65% | 1254 | 0.00 | 9.99% |
| 15m | Up | 50.91% | 53.95% | 50.82% | 304 | 1.00 | 11.41% |
| 15m | Down | 47.78% | 47.24% | 48.62% | 398 | 0.00 | 9.90% |
| 30m | Up | 52.19% | 52.38% | 51.25% | 147 | 1.00 | 9.59% |
| 30m | Down | 50.82% | 46.86% | 48.18% | 207 | 0.00 | 7.50% |
| Daily | Up | 48.78% | 37.50% | 52.32% | 8 | 0.00 | 12.50% |
| Daily | Down | 36.90% | 42.11% | 47.68% | 19 | 0.00 | 10.53% |

## Follow-through after executable entry

2× threshold; signed gross mean return in bps, following impulse direction. Entry next candle open; exit open after H held candles. Negative means reversal. All qualifying events included here, so horizons overlap; these are diagnostics, not a portfolio. Table shows validation; full discovery/validation, sample counts, medians, win rates and unconditional directional baselines in JSON. Baseline is not matched for time of day or regime.

| Candle | Impulse | 1 candle | 2 | 3 | 5 | 10 | 20 |
|---|---|---:|---:|---:|---:|---:|---:|
| 1m | Up | 0.11 | 0.23 | 0.14 | 0.03 | 0.05 | 0.28 |
| 1m | Down | -0.19 | -0.20 | -0.33 | -0.33 | -0.36 | -0.69 |
| 5m | Up | 0.40 | 1.40 | 1.58 | 2.56 | 3.11 | 1.51 |
| 5m | Down | 0.37 | 1.14 | 1.00 | 1.15 | 2.93 | 2.72 |
| 15m | Up | 0.61 | 0.80 | -0.24 | -1.23 | -3.87 | 1.32 |
| 15m | Down | -0.81 | -3.22 | -0.39 | -0.93 | 2.94 | -4.48 |
| 30m | Up | 1.16 | 2.05 | 0.02 | -3.58 | 2.14 | — |
| 30m | Down | -1.58 | 0.60 | -0.78 | 1.57 | 3.40 | — |
| Daily | Up | -85.21 | 99.32 | 59.87 | 107.48 | 210.63 | 411.27 |
| Daily | Down | -102.92 | -60.91 | -170.59 | -136.20 | -147.54 | -272.24 |

## Can it pay after costs?

240 recipes: 5 candle sizes × 2 thresholds × 2 directions × 6 holds × follow/fade. Costs: 0/1/5/10bps per side. 5bps per side roughly requires 10bps gross per round trip. Trades do not overlap within each recipe; independent recipes are not combined. Selection uses highest discovery mean net bps/trade at 5bps/side with at least 100 discovery trades (184 eligible). This favors per-trade magnitude and longer holds, not portfolio return or risk-adjusted performance.

Selected: **30m 3× down, follow, hold 10**. Recipes positive in both periods at 5bps/side with ≥100 discovery and ≥30 validation trades: **0/240**. That count is exploratory, not statistical confirmation.

| Period | Trades | Gross mean bps | Net 1bp/side | Net 5bps/side | Net 10bps/side | Net win rate (5bps) | 95% block-bootstrap interval for mean net bps (5bps) |
|---|---:|---:|---:|---:|---:|---:|---|
| discovery | 151 | 13.57 | 11.57 | 3.58 | -6.41 | 47.02% | -9.66 to 17.67 |
| validation | 48 | 3.82 | 1.83 | -6.17 | -16.17 | 41.67% | -26.43 to 14.91 |

Intervals use 1,000 deterministic resamples of circular 20-session blocks, retaining zero-trade sessions. They do not correct for recipe selection/multiple testing. Sparse daily impulses give low power.

| Selected recipe year | Trades | Mean net bps at 5bps/side |
|---|---:|---:|
| 2016 | 16 | -4.96 |
| 2017 | 22 | -4.14 |
| 2018 | 18 | 18.74 |
| 2019 | 17 | -20.18 |
| 2020 | 11 | -36.82 |
| 2021 | 20 | 13.17 |
| 2022 | 11 | 1.16 |
| 2023 | 15 | 21.92 |
| 2024 | 21 | 24.61 |
| 2025 | 25 | -19.08 |
| 2026 | 23 | 7.85 |

## Limits and reproduction

Results describe QQQ, this history and these definitions. A positive gross mean or >50% continuation is insufficient evidence of a usable edge. Prices are vendor-adjusted, not point-in-time account reconstruction. No observed bid/ask, market impact, latency, taxes, financing, short borrow/availability, cash yield or dividend/share transformations. Short trades especially need borrow/financing analysis. Minute OHLC cannot establish obtainable next-open execution. No stop or profit target, portfolio equity, CAGR or drawdown is modeled; per-trade means do not establish outperforming buy-and-hold. Do not infer a universal X-candle trend duration.

Run `node --test research/qqq-velocity.test.cjs`, then `node research/qqq-velocity.cjs`. Outputs: `research/qqq-velocity-results.json` and this report. Existing production strategies remain unchanged.
