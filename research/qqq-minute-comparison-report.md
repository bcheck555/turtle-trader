# QQQ strategy rerun using one-minute data — October 26, 2026

**None of the 16 alternatives beats buy and hold on the longer history or in the reused 2025–2026 validation period.** The original trend-plus-dip candidate loses its combined-return advantage and has a worse sampled drawdown.

## Main results: same rules and same execution clock

One-minute archive: February 23, 2016–October 23, 2026, 1,042,924 bars across 2,684 sessions. After the unchanged 200-session warmup, the evaluated window is **2016-12-06–2026-10-23**, 2484 sessions. These figures assume 5bps per side.

| Recipe | Combined return | Annualized growth | Sampled drawdown | 2025–2026 validation return |
|---|---:|---:|---:|---:|
| Buy and hold | 546.76% | 20.85% | 36.57% | 38.07% |
| SMA200 or 1% dip held 5 sessions | 490.73% | 19.74% | 40.12% | 28.23% |
| Close above SMA200 | 369.53% | 16.99% | 23.37% | 25.59% |

Buy and hold turns $100 into approximately $646.76; trend-plus-1%-dip turns it into $590.73. The hybrid's sampled drawdown is 40.12%, exceeding buy and hold's 36.57%. SMA200 reduces sampled drawdown while sacrificing return.

The same 1%-dip hybrid remains the highest-CAGR alternative in discovery, but even its discovery return (359.30%) trails buy and hold (367.03%). Selection excludes the benchmark, so “selected alternative” does not mean “winner over buy and hold.” Even at zero costs its full-window return (514.83%) trails gross buy and hold (547.41%).

## Separating candle resolution, history and trade timing

1. **Resolution control:** use minute data only from the original input start, October 26, 2021; initialize the original 200-session warmup and signal state; execute at the original clock. All **1,254 shared input sessions** have identical daily OHLC and matching execution prices. All original result rows reproduce **exactly**, across every rule, window and 0/5/10bps cost case. Minute resolution alone changes none of these results.
2. **History extension (main table):** keep that original clock and include the full cleaned archive. The extra history removes the earlier apparent advantage. The original evaluated window remains August 12, 2022–October 23, 2026, where the hybrid returned 154.35% versus 121.83% for buy and hold; those results are unchanged.
3. **Execution-clock sensitivity:** also test the final minute's opening price, 15:59 on ordinary days or 12:59 on early closes. The original clock is **15:30 / 12:30 New York**. Clock changes alter holding periods and terminal marks; they are not a pure resolution effect. None of the alternatives wins over buy and hold over the extended history under the later clock either. On the shorter overlapping dates, both hybrids still beat combined buy-and-hold return under the later clock, but neither wins in validation.

The simulator still forms signals from **completed daily OHLC** and samples drawdown at **next-session openings plus the terminal mark**. Feeding it minute bars does not add intraday stops or minute-sampled drawdowns. This reruns the 16 exploratory daily-rule alternatives; it does not rerun the separate production Turtle/Chandelier intraday engines.

## Windows, unchanged assumptions and limitations

- Discovery: **2016-12-06–2024-12-31** (2030 sessions).
- Reused chronological validation: **2025-01-02–2026-10-23** (454 sessions). This period has been inspected repeatedly; it is not fresh unseen evidence.
- The 16 definitions, 200-session warmup, January 2025 split and cost cases are unchanged. No threshold, holding period or strategy was tuned for this rerun.
- Long/cash only, fractional adjusted-price shares, zero cash interest. Persistent holdings retain share counts. No leverage, taxes, separate dividend payments, actual share transformations or observed bid/ask execution.
- Base cost: 5bps (0.05%) per buy or sell; gross: 0bps; stress: 10bps per side. Costs are assumptions, not measured historical spreads.
- Each evaluation account starts fresh and liquidates at its last session's selected afternoon clock. Signal state uses earlier observations. Boundary gaps differ between separate and combined windows, so discovery and validation returns need not multiply to the combined result.
- CAGR uses 252 sessions/year. Drawdowns miss intraday extremes. Sharpe uses zero risk-free return. Segment occupancy is not elapsed-time exposure.
- The cleaned archive has zero unknown missing/outside bars; 56 fully halted minute slots are separately documented and no candles fabricated. These daily recipes do not submit intraday stop orders during those pauses.
- This sample excludes the 2000–2002 and 2008 episodes. No statistical significance or durable future edge is claimed.

## All 16 alternatives and benchmark

Fixed original clock. Drawdown is a positive loss magnitude. Gross/stress columns are combined returns.

| Rule | Discovery return | Validation return | Combined return | CAGR | Sampled drawdown | Orders | Gross return | Stress return |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Buy and hold | 367.03% | 38.07% | 546.76% | 20.85% | 36.57% | 2 | 547.41% | 546.11% |
| Close above SMA50 | 178.49% | 17.97% | 229.52% | 12.86% | 17.25% | 146 | 254.48% | 206.33% |
| Close above SMA100 | 133.75% | 23.08% | 188.56% | 11.35% | 25.18% | 102 | 203.66% | 174.22% |
| Close above SMA200 | 272.74% | 25.59% | 369.53% | 16.99% | 23.37% | 42 | 379.50% | 359.77% |
| Positive 63-day momentum | 172.19% | 15.42% | 215.10% | 12.35% | 21.81% | 92 | 229.94% | 200.94% |
| Positive 126-day momentum | 266.44% | 25.72% | 362.07% | 16.80% | 26.58% | 40 | 371.41% | 352.92% |
| Buy after 1% down day, hold 5 sessions | 89.53% | 10.87% | 110.76% | 7.86% | 34.91% | 356 | 151.82% | 76.40% |
| Buy after 2% down day, hold 5 sessions | 102.24% | 19.87% | 142.43% | 9.40% | 32.40% | 190 | 166.59% | 120.45% |
| SMA200 or 1% dip held 5 sessions | 359.30% | 28.23% | 490.73% | 19.74% | 40.12% | 80 | 514.83% | 467.56% |
| SMA200 or 2% dip held 5 sessions | 215.24% | 32.88% | 320.15% | 15.68% | 42.36% | 76 | 336.42% | 304.48% |
| RSI2 below 10, exit above 50 | 23.30% | 7.43% | 32.47% | 2.89% | 17.86% | 224 | 48.17% | 18.43% |
| RSI2 below 10 above SMA200, exit above 50 | 22.49% | -0.41% | 21.99% | 2.04% | 15.90% | 170 | 32.81% | 12.05% |
| Close breakout 20/10 | 126.87% | 15.90% | 162.96% | 10.31% | 18.86% | 90 | 175.06% | 151.39% |
| Close breakout 55/20 | 119.22% | 12.17% | 146.64% | 9.59% | 17.39% | 42 | 151.87% | 141.51% |
| Overnight (last bar open to next open) | -67.44% | -14.21% | -72.04% | -12.13% | 73.81% | 4966 | 234.91% | -97.67% |
| Daytime (open to last bar open) | -75.21% | -34.96% | -83.88% | -16.90% | 84.26% | 4968 | 93.31% | -98.66% |
| Daytime after 1% down day | -8.68% | 20.46% | 10.00% | 0.97% | 28.31% | 818 | 65.59% | -26.92% |

## Execution-clock sensitivity, full extended history

Difference is percentage points of combined return. Both columns use one-minute source data and 5bps per side.

| Rule | Original 15:30/12:30 clock | Final-minute 15:59/12:59 clock | Difference (pp) |
|---|---:|---:|---:|
| Buy and hold | 546.76% | 546.25% | -0.50 |
| Close above SMA50 | 229.52% | 229.52% | 0.00 |
| Close above SMA100 | 188.56% | 188.56% | 0.00 |
| Close above SMA200 | 369.53% | 369.16% | -0.37 |
| Positive 63-day momentum | 215.10% | 214.86% | -0.25 |
| Positive 126-day momentum | 362.07% | 361.71% | -0.36 |
| Buy after 1% down day, hold 5 sessions | 110.76% | 110.60% | -0.16 |
| Buy after 2% down day, hold 5 sessions | 142.43% | 142.24% | -0.19 |
| SMA200 or 1% dip held 5 sessions | 490.73% | 490.26% | -0.46 |
| SMA200 or 2% dip held 5 sessions | 320.15% | 319.82% | -0.33 |
| RSI2 below 10, exit above 50 | 32.47% | 32.47% | 0.00 |
| RSI2 below 10 above SMA200, exit above 50 | 21.99% | 21.99% | 0.00 |
| Close breakout 20/10 | 162.96% | 162.96% | 0.00 |
| Close breakout 55/20 | 146.64% | 146.64% | 0.00 |
| Overnight (last bar open to next open) | -72.04% | -69.43% | 2.60 |
| Daytime (open to last bar open) | -83.88% | -85.26% | -1.39 |
| Daytime after 1% down day | 10.00% | 12.72% | 2.72 |

## Reproduction and verification

```sh
node research/qqq-minute-comparison.cjs
node --test research/qqq-patterns.test.cjs research/qqq-minute-comparison.test.cjs
```

Full results: `research/qqq-minute-comparison-results.json`. It contains every strategy and cost case for extended fixed-clock, extended final-minute and overlapping-date final-minute scenarios. The original saved JSON supplies the exactly reproduced overlap fixed-clock case.

The driver verifies minute checksum/coverage, reproduces the saved original report, checks exact daily/clock equivalence on matching dates, and runs the extended/sensitivity scenarios. It refuses missing fixed-clock minutes. Retained minute input is unchanged.

**Twelve JavaScript regression checks passed**, covering early closes, winter UTC offsets, missing execution-minute rejection, no future-dependent signals and exact transaction arithmetic. The actual data run passed exact equality assertions against every original metric. Production Python code is unchanged by this rerun.

One independent research/review sub-agent inherited coordinator model/settings without override. It separately verified all shared daily OHLC/execution prices and identified the clock confound. Coordinator implemented the comparison harness and generated artifacts.

Minute CSV SHA-256: `551fd5feed7c8b230d90c4f36095a802ab095eacaa7709f1908b232dc6effe93`.
Original 30-minute CSV SHA-256: `718f2d349bb99eb3031532a470f5bb361590acf2208000854e1795aa57f142e1`.
