# QQQ pattern research — October 26, 2026

A trend-plus-dip candidate beats buy and hold over the combined window, but fails to beat it in the chronological holdout. No tested rule beats buy and hold's total return in the holdout. These results support further research, not a claim of durable outperformance.

## Strongest candidates

- **SMA200 or 1% dip held five sessions:** combined return **154.35%** versus buy and hold **121.83%**; sampled drawdown **20.73%** versus **24.17%**. Discovery return **97.76%** versus **60.19%**. Holdout return **28.23%** versus **38.07%**. Its combined advantage comes from discovery.
- **SMA200 alone:** combined return **114.82%**, sampled drawdown **15.58%**; holdout **25.59%**, sampled drawdown **12.25%** versus buy and hold **24.17%**. Useful drawdown candidate, weaker absolute return.
- Dip-only and RSI candidates give up substantial exposure and return. Overnight/daytime strategies are highly sensitive to daily transaction costs.

## Data and comparison windows

Source: `runs/turtle-trader-equity-5y-20261026/qqq/bars.csv`; Alpaca SIP, 30-minute regular-session bars, fully adjusted. Data spans 2021-10-26–2026-10-23, 1254 sessions and 16,242 bars. Coverage file reports complete with zero missing bars. Input SHA-256: `718f2d349bb99eb3031532a470f5bb361590acf2208000854e1795aa57f142e1`.

Every strategy uses 200 sessions of initial indicator warmup. Discovery: **2022-08-12–2024-12-31** (600 sessions). Holdout: **2025-01-02–2026-10-23** (454 sessions). Combined: **2022-08-12–2026-10-23**. Results differ from earlier reports' five-year numbers because comparison begins after common warmup.

There are **16 alternatives**, plus buy and hold. Initial pass included 14 alternatives; two trend-plus-dip variants were added following independent hypothesis review after inspecting initial results. This sequence is disclosed because it increases selection bias. Selection rule: highest discovery CAGR at 5bps per side, excluding buy and hold. Selected: **SMA200 or 1% dip held 5 sessions**. The holdout is a retrospective chronological check; this dataset has already appeared in earlier research and is not a pristine unseen sample. Do not tune further on it and call the result out of sample.

## Execution and assumptions

All positions are unlevered long/cash, using fractional adjusted-price shares. Cash earns zero. Buy-and-hold and other persistent positions preserve share counts until exit; no daily rebalancing. Base cost is **5 basis points per side** on traded notional, combining assumed spread/slippage/fees. Also evaluated 0 and 10bps per side. No taxes, cash yield, actual share transformations, separate dividend payments, market impact or historical financing are modeled. Adjusted prices can change with future vendor adjustments; this is not a point-in-time account reconstruction.

Signals use only completed prior sessions. Ordinary entry/exit orders fill at the next session's first bar open. Daytime and overnight rules trade at the **last 30-minute bar's open**, normally 15:30 New York (12:30 on early closes), rather than claiming a fill at a close known after the fact. Thus executable overnight includes the final half-hour; daytime excludes it. Full allocation requires small-order liquidity and the assumed cost; neither is established by these data.

Each evaluation window starts with a fresh cash account and ends liquidated at its final session's last-bar open. Historical signal state carries into each window, account holdings do not. Boundary gaps are excluded from the two separate windows but included in the combined account, so discovery and holdout returns need not multiply exactly to combined return.

Drawdown and Sharpe use session-opening equity marks, plus the terminal last-bar-open mark. They do not measure worst intraday drawdown. CAGR uses 252 sessions/year. Sharpe uses zero cash/risk-free return and is descriptive. JSON's `segmentOccupancyPct` weights daytime and overnight segments equally; it is not elapsed-time exposure.

Rules:
- SMA: previous close greater than trailing mean of 50/100/200 completed closes.
- Momentum: previous close greater than close 63/126 sessions earlier.
- Dip: previous close-to-close return below −1%/−2%; enter next open, hold five opening-to-opening session intervals. A new dip while a dip position is active does not reset its expiry; a fresh signal can renew at expiry.
- Hybrid: hold whenever SMA200 condition or the independently tracked five-session dip campaign is active. **Above SMA200 the position stays invested; below SMA200 only active dip campaigns permit exposure.**
- RSI2: Wilder recursive two-session gain/loss averages, enter below 10, exit above 50 at next open. Trend variant requires SMA200 at entry only.
- Breakout: prior close above preceding 20/55-session highs; exit below preceding 10/20-session lows, next open. These are close-confirmed research rules, distinct from the repository's original intraday Turtle engine.
- Overnight/daytime: fixed segments described above. Conditional daytime requires previous daily loss greater than 1%.

## All trials

Base case 5bps per side. Drawdown shown as positive loss magnitude. Gross and stress are combined returns at 0 and 10bps per side.

| Rule | Discovery return | Holdout return | Combined return | CAGR | Sampled drawdown | Orders | Gross return | Stress return |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Buy and hold | 60.19% | 38.07% | 121.83% | 20.99% | 24.17% | 2 | 122.06% | 121.61% |
| Close above SMA50 | 34.87% | 17.97% | 59.58% | 11.82% | 15.30% | 72 | 65.43% | 53.94% |
| Close above SMA100 | 28.48% | 23.08% | 58.62% | 11.66% | 17.94% | 42 | 61.98% | 55.32% |
| Close above SMA200 | 70.53% | 25.59% | 114.82% | 20.06% | 15.58% | 12 | 116.11% | 113.53% |
| Positive 63-day momentum | 37.45% | 15.42% | 59.13% | 11.75% | 16.89% | 42 | 62.50% | 55.82% |
| Positive 126-day momentum | 54.12% | 25.72% | 94.35% | 17.22% | 15.58% | 16 | 95.91% | 92.80% |
| Buy after 1% down day, hold 5 sessions | 61.05% | 10.87% | 79.09% | 14.95% | 14.65% | 176 | 95.57% | 64.01% |
| Buy after 2% down day, hold 5 sessions | 25.61% | 19.87% | 50.57% | 10.28% | 18.39% | 72 | 56.09% | 45.24% |
| SMA200 or 1% dip held 5 sessions | 97.76% | 28.23% | 154.35% | 25.01% | 20.73% | 34 | 158.71% | 150.06% |
| SMA200 or 2% dip held 5 sessions | 74.35% | 32.88% | 132.37% | 22.33% | 23.15% | 30 | 135.88% | 128.91% |
| RSI2 below 10, exit above 50 | 25.82% | 7.43% | 35.17% | 7.47% | 14.07% | 112 | 42.96% | 27.81% |
| RSI2 below 10 above SMA200, exit above 50 | 19.32% | -0.41% | 18.83% | 4.21% | 8.02% | 82 | 23.81% | 14.06% |
| Close breakout 20/10 | 24.08% | 15.90% | 43.81% | 9.08% | 14.99% | 40 | 46.72% | 40.96% |
| Close breakout 55/20 | 38.30% | 12.17% | 55.60% | 11.15% | 11.40% | 18 | 57.00% | 54.20% |
| Overnight (last bar open to next open) | -28.16% | -14.21% | -38.30% | -10.90% | 41.39% | 2106 | 76.84% | -78.47% |
| Daytime (open to last bar open) | -32.71% | -34.96% | -56.23% | -17.93% | 57.41% | 2108 | 25.57% | -84.75% |
| Daytime after 1% down day | -7.41% | 20.46% | 11.53% | 2.64% | 12.09% | 370 | 34.19% | -7.31% |

## Pattern diagnostics

After a daily fall greater than 1%, next session's open-to-close mean return was **−3.76bps** in discovery (108 observations), versus **+32.81bps** in holdout (77). The combined **+11.46bps** average conceals a sign change. This diagnostic uses observed closes and is not itself the executable daytime backtest.

After a fall greater than 2%, corresponding means were **−0.21bps** (30 observations) and **+52.22bps** (25). Both diagnostics have small samples and unstable regimes. Unconditional next-session open-to-close means were **+3.54bps** and **+2.33bps**.

No multiple-testing correction, confidence interval or significance claim is supplied. Sixteen alternatives and several earlier repository experiments create selection bias. Five years omit major older regimes including 2000–2002, 2008 and 2020. Preserve these rules, acquire a longer independent dataset, then run chronological walk-forward validation without selecting on each test fold. Test hybrids across neighboring dip thresholds/hold lengths, costs, cash interest and broader market regimes. A smooth parameter neighborhood would be stronger evidence than one best setting.

Backtest-selection risk is supported by [Novy-Marx, Backtesting Strategies Based on Multiple Signals](https://www.nber.org/papers/w21329), which shows how multiple signal selection can produce impressive results without predictive power.

## Reproduce and validation

```sh
node research/qqq-patterns.cjs
node --test research/qqq-patterns.test.cjs
```

Machine-readable full results: `research/qqq-patterns-results.json`. Script is an independent exploratory simulator; it does not alter the Turtle engine.

Eight regression checks passed: exact share/cost arithmetic, overnight execution and terminal behavior, daytime isolation, no future-dependent signals for every rule, dip expiry, fresh holdout account, and wrong-symbol rejection. Saved JSON reproduces exactly; source checksum matches acquisition provenance, and coverage is complete with zero missing bars.

Existing Python suite: **262 passed**, 128 dependency deprecation warnings, 135.10 seconds. First run stopped at a 60-second timeout; complete rerun passed. Staged diff whitespace check passed.

One research sub-agent, using inherited coordinator model/settings with no override, reviewed existing evidence and independently checked signal timing, window isolation and metric definitions. Coordinator implemented the simulator, regression tests and artifacts. Review identified terminal-cost and occupancy-label issues; both were corrected. No disagreement remains on execution mechanics; durability of any edge remains unproven.
