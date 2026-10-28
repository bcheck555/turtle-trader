# Squeeze Momentum: first QQQ test

The four fixed Squeeze variants were flat or negative over the full evaluation
window. Adding both video filters did not improve this implementation's result.
The held-out portion was positive for three variants, but all trailed
buy-and-hold. These are descriptive historical results, not an exact replication
of the video's strategy or performance.

## Data and assumptions

- Cached QQQ adjusted minute data: 2016-02-23 through 2026-10-23.
- 1,042,924 observed minutes became 34,766 complete 30-minute candles across
  2,684 exchange sessions. Fifty-six fully halted minute slots were excluded;
  no ordinary minutes were missing and no boundary sessions were dropped.
- All variants share a 120-session burn-in. Full evaluation starts 2016-08-12;
  held-out evaluation starts 2023-08-09. Split is chronological 70/30 by complete
  sessions before burn-in. Each period starts flat with fresh $10,000; preceding
  data warms indicators without executing trades. No parameters were optimized.
- BB(20,2), KC(20,1.5), 20-bar regression momentum, ADX(14,14)>20, WMA(120),
  ATR(14), stop 2× signal ATR and target 3× signal ATR. Indicators use completed
  30-minute candles; market entry is at the next eligible candle's opening.
- Long-only, one position per symbol, no pyramiding. A qualified opposite signal
  closes an existing long. Short entries are rejected and recorded. No extra
  exits are triggered by the filters subsequently becoming false.
- Planned initial stop-risk limit is 3% of sizing capital, capped by available
  cash and whole shares. The cash cap reduces actual stop risk below that limit
  when intraday ATR sizing would otherwise require leverage. Five basis points
  slippage per fill, zero commissions. Positions can stay open overnight.
- Advanced partial-target/breakeven/trailing-runner exits remain deferred.

## Results

Full period: **2016-08-12–2026-10-23**. Held-out: **2023-08-09–2026-10-23**.

| Strategy | Full return | Full max drawdown | Full PF | Held-out return | Held-out max drawdown | Held-out PF |
|---|---:|---:|---:|---:|---:|---:|
| Raw Squeeze | -0.97% | 19.13% | 1.00 | +2.06% | 7.71% | 1.04 |
| Squeeze + ADX | -4.74% | 19.59% | 0.95 | +7.23% | 7.24% | 1.23 |
| Squeeze + WMA | -1.52% | 21.31% | 0.99 | -3.71% | 11.55% | 0.92 |
| Squeeze + ADX + WMA | -12.83% | 20.45% | 0.80 | +3.56% | 4.88% | 1.17 |
| Turtle System 2 reference | +58.62% | 14.33% | 4.57 | +28.84% | 8.95% | 5.10 |
| Buy-and-hold reference | +548.05% | 36.43% | — | +91.45% | 22.92% | — |

The both-filter variant closed 144 full-period campaigns, with a 42.36% win
rate; its held-out period closed 45 campaigns, with a 48.89% win rate. Raw
Squeeze closed 408 full-period campaigns. The both-filter variant averaged only
5.56% gross exposure relative to equity over the full period, versus almost
100% for buy-and-hold. Absolute returns therefore do not represent equal time
in the market. Profit factors and win rates use fee-inclusive flat-to-flat
campaigns; buy-and-hold remains open and has no closed-campaign statistic.

The Turtle reference retains daily ATR(20), weekly sizing, and drawdown sizing
capital adjustments. It shares starting cash, a 0.015 ATR sizing fraction and
one-unit cap, but preserves its original insufficient-cash rejection behavior:
2,148 full-period insufficient-cash rejections. Its allocation mechanics differ
from the Squeeze cash cap. All Squeeze variants finished the full and held-out
periods flat; buy-and-hold finished invested. Full-period raw and WMA-only each
recorded one ambiguous OHLC execution, resolved using the engine's conservative
path comparison.

## Interpretation and reproducibility

Gemini captured the video’s main indicator stack correctly. This test uses
corrected LazyBear formulas, cash-funded longs and one fixed target. The video
also enables shorts and flips, experiments with settings, and later splits
entries into a fixed-target leg and a trailing runner. Its modified indicator
source, BB bug toggle state for the final AAPL test, effective test dates, and
execution assumptions remain unverified. Its results cannot be inferred from
these QQQ results, or vice versa.

Artifacts live in [runs/squeeze-qqq-30m-first](../runs/squeeze-qqq-30m-first/):

- [Generated report](../runs/squeeze-qqq-30m-first/report.md): all three periods,
  including in-sample figures.
- [Comparison JSON](../runs/squeeze-qqq-30m-first/comparison.json): return,
  drawdown, campaign statistics, fees, exposure, open positions and rejections.
- [Provenance](../runs/squeeze-qqq-30m-first/provenance.json): source/derived data,
  resolved configurations, runner and engine source hashes. The recorded Git
  commit is the pre-change base; working-tree source hashes identify the actual
  implementation tested. Those hashes were checked against current sources
  after the run and matched.
- Each period/variant directory contains its summary, fills, equity and audit
  JSON/CSV files. Generated run artifacts are local, not committed source data.

Run fingerprint:
`e9dd1315460a30fa6cd7ae2d08487a8854565ca7c6c79e9653b0965109724ee2`.

Reproduce with a new output directory:

```sh
uv run python research/run_squeeze_comparison.py \
  --output-dir runs/squeeze-qqq-30m-repeat
```

Runner verification: nine focused tests pass, including a real-calendar
end-to-end fixture exercising all periods, variants, artifact output and
existing-output refusal. Aggregation tests cover early closes, missing/duplicate
minutes, incomplete boundaries, zero-volume candles and the March 2020 halt.
The engine's independent execution and indicator tests are reported with the
implementation change.
