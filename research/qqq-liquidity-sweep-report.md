# QQQ liquidity-sweep experiment — October 28, 2026

**This predefined version provides no evidence of an edge after costs.** The full setup returns **-3.33%** across **53 trades** at 5bps per side; reused validation returns **-1.98%** across **7 trades**, with 0.00% wins. Gross combined return is 1.85%; at 10bps it is -7.73%. Costs erase the small gross gain, and the validation sample is too small for a strong conclusion.

Video: [TradeIQ liquidity sweep](https://www.youtube.com/watch?v=l27wF-EvDkw&t=335s).

Rules frozen before first data run in `qqq-liquidity-sweep-rules.json`. This is one explicit interpretation of discretionary examples, implemented as an isolated research simulator.

Source: runs/qqq-1m-20160223-20261023/bars.csv; SHA-256 `551fd5feed7c8b230d90c4f36095a802ab095eacaa7709f1908b232dc6effe93`. 1,042,924 SIP one-minute bars, 2016-02-23–2026-10-23. Adjusted research prices, regular sessions; zero unknown missing bars, 56 documented halted minutes.

Trading starts 2016-05-11, matching the cached Turtle comparison. Discovery ends 2024-12-31. Reused validation starts 2025-01-02; this period has been examined in earlier experiments and is not unseen evidence. Each window starts with $25,000 cash. No ranking, selection, or parameter tuning.

## Frozen model

- **level:** Previous completed regular session low; target previous session high.
- **sweep:** Completed 5m candle low strictly below level, close strictly above level.
- **pivot:** Most recent same-session high strictly greater than two highs on each side; right-side candles must finish before sweep candle begins.
- **atr:** Mean true range of previous 14 completed 5m candles, including overnight gaps; never current candle.
- **confirmation:** Within 30 wall-clock minutes after sweep close: bullish body >= body_atr_multiple * prior ATR, close above frozen pre-sweep confirmed pivot high.
- **gap:** Exactly next completed candle after confirmation: its low > high two candles earlier by at least minimum_gap; three candles must be contiguous and confirmation must follow sweep.
- **entry:** Sweep/confirmed use next observed minute open; full uses later buy limit at gap low + gap_fraction * gap width, active only after third candle closes.
- **expiry:** Market entry valid one minute; limit valid retest_bars * 5 wall-clock minutes; orders expire at scheduled final-minute open.
- **invalidation:** Before fill: cancel if price trades below original sweep low, or stop/target passed at minute open. Incomplete 5m candle cancels unfilled setup/order.
- **execution:** Buy limit touch fills at limit or improved opening price, with bps costs separately debited. Stop gaps sell at worse open. Stop wins if both stop and target touched. No target credited during an intraminute limit-fill candle unless filled at open; stop still wins. Reopening events timestamped no earlier than verified halt end.
- **sizing:** Whole shares; planned risk including entry/stop costs <= 0.5% current equity; gross purchase plus costs <= cash; no leverage, no pyramiding, at most one filled trade per day.
- **exit:** Protective stop, previous-day high target, or market at scheduled final-minute open; no overnight position.
- **comparison:** Same fresh-cash combined/discovery/reused-validation windows and costs for variants and buy-and-hold. Cached production Turtle statistics are contextual, with different risk, sizing and terminal policy.
- **selection:** No best-strategy selection or tuning; report all predefined sensitivity cases. Reused validation is not unseen data.

The full rule requires a same-session 2-left/2-right swing high confirmed before the sweep. Five-minute ATR uses only prior completed candles. First 14 aggregate candles warm ATR. Incomplete five-minute buckets are omitted; setup state is canceled at gaps. Halt reopening prices are available no earlier than the verified reopening time.

## Base results at 5bps per side

| Rule | Combined return | CAGR | Minute-sampled drawdown | Trades | Win rate | Net R/trade | Discovery return | Reused validation return |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| sweep | -64.20% | -9.38% | 64.23% | 872 | 19.38% | -0.417 | -53.87% | -22.78% |
| confirmed | -5.06% | -0.50% | 7.23% | 108 | 40.74% | -0.121 | -6.20% | 1.20% |
| full | -3.33% | -0.32% | 4.39% | 53 | 35.85% | -0.214 | -1.39% | -1.98% |
| buy_hold | 607.00% | 20.62% | 37.16% | 1 | 100.00% | — | 408.79% | 37.19% |

## Predefined sensitivity and cost cases

All rows below use the full rule except the separately named ablations and benchmark. Sensitivity changes one parameter at a time; the base is body/ATR 1, gap fraction 0.5, retest expiry six five-minute bars.

| Rule | Combined return 0bps | 5bps | 10bps | Reused validation trades at 5bps | Reused validation net R/trade |
|---|---:|---:|---:|---:|---:|
| sweep | -18.60% | -64.20% | -83.36% | 142 | -0.619 |
| confirmed | 3.90% | -5.06% | -12.71% | 18 | 0.142 |
| full | 1.85% | -3.33% | -7.73% | 7 | -0.753 |
| body_0.75 | 0.64% | -7.59% | -14.61% | 14 | -0.480 |
| body_1.25 | 2.94% | 0.23% | -2.08% | 5 | -0.175 |
| gap_0.25 | -1.40% | -5.68% | -9.42% | 7 | -0.731 |
| gap_0.75 | 1.34% | -4.48% | -9.28% | 8 | -0.570 |
| retest_3 | 2.51% | -2.02% | -5.92% | 6 | -0.712 |
| retest_12 | 2.40% | -3.72% | -8.91% | 8 | -0.705 |
| buy_hold | 607.40% | 607.00% | 606.59% | 1 | — |

## Contextual production Turtle comparison

Same archive, trading start and 5bps setting. These cached audited results use available-cash Turtle sizing, overnight holdings, and final-close marks. They are not matched-risk or identical-terminal-execution comparisons.

| Rule | Combined return | CAGR | Minute-sampled drawdown | Closed trades |
|---|---:|---:|---:|---:|
| Turtle channel | 169.37% | 9.96% | 16.26% | 30 |
| Turtle chandelier | 107.40% | 7.24% | 13.66% | 50 |

## Interpretation and limits

Return and drawdown must be read alongside exposure and sample size. Sweep positions risk at most 0.5% planned capital per trade, capped by cash; buy-and-hold invests all available cash. No leverage, no shorting, no reinvested cash yield, no taxes, no volume participation model or actual bid/ask history. Cost cases debit 0/5/10bps of notional per side, combining assumed spread/slippage/fees; costs do not move a limit fill above its price. Stop gaps can exceed planned risk.

Limit fills assume sufficient liquidity when touched. If a limit is filled during a minute, a same-minute target is ignored unless entry occurred at open; any stop touch wins. Stop/target ambiguity counts and minute-close exposure/drawdown are in JSON. Execution inside a minute remains uncertain. Daily positions liquidate at the final scheduled minute open, including early closes. Buy-and-hold liquidates at the same clock on the window’s final day.

Prior-day levels are an objective subset of the video: no equal-level clustering, higher-timeframe trend filter, Asian-session levels or discretionary setup selection. Price patterns cannot identify institutional intent. This retrospective experiment cannot establish a durable edge or statistical significance. One independent code-review agent using inherited model/settings reviewed timing, execution and accounting. Review found an opening-below-sweep cancellation defect; it was fixed with regression coverage before the first historical run.

## Reproduce

```sh
node --test research/qqq-liquidity-sweep.test.cjs
node research/qqq-liquidity-sweep.cjs
```

Full window/year/cost metrics, frozen rules, source/rules/runner hashes and combined 5bps trade ledgers: `qqq-liquidity-sweep-results.json`.
