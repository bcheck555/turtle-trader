# AAPL Squeeze Momentum: maximum history and video comparison

The AAPL test gets closer to the video under the predeclared legacy-BB
hypothesis and 2022 date range, but does not reproduce its profitability.
That scenario produced exactly **219 closed campaigns**, matching the video's
displayed trade count, with **+41.39%** return rather than **+133.29%**. With
zero slippage, return was **+52.71%**, with a **47.27%** win rate versus the
video's **47.49%**. Matching a count or win rate does not establish identical
signals, fills, position sizing, or source code.

## Acquisition and coverage

- The provider-history probe began in 1990. Earliest regular-session AAPL bars
  available from the configured Alpaca SIP source were **2016-01-04**. The
  downloader's last permitted completed New York date was **2026-10-26**.
- [Original minute archive](../runs/aapl-1m-20160104-20261026/) preserves all
  **1,056,515 regular-session bars**. Acquisition received 1,954,173 rows across
  196 pages and excluded 897,658 out-of-hours rows. Credentials are not archived.
- Its strict coverage audit found 59 unknown missing minute slots: nine on
  2016-02-02 and 50 on 2016-02-22. Three targeted provider rechecks returned HTTP
  200 with zero bars for those ranges; see
  [gap-rechecks.json](../runs/aapl-1m-20160104-20261026/gap-rechecks.json).
- No candles were fabricated or filled forward. A separately verified
  [continuous derivative](../runs/aapl-1m-20160223-20261026/) starts 2016-02-23,
  retains **1,043,314 minute bars**, and removes the 13,201 earlier observed rows.
  The original archive remains intact. Neither video date window is affected.
- The derivative has zero unknown gaps and 56 separately classified verified
  market-halt minute slots. Aggregation yielded **34,779 complete 30-minute
  candles across 2,685 sessions**, through 2026-10-26. Early closes and verified
  pauses use actual observations; no boundary sessions were dropped.

## Fixed scenarios

The runner tested 30 scenarios, declared before seeing AAPL results. No
parameters were optimized. All begin flat with $10,000 and use earlier history
only to warm indicators. Full-history trading begins 2016-03-08, the first
session after at least 120 completed intraday warmup bars.

Three main windows are full history (2016-03-08–2026-10-26), the video tester
header range (2020-01-02–2026-10-16), and its conflicting input range
(2022-01-03–2026-10-15), with inclusive final exchange dates. Each has four
long-only variants (raw, ADX, WMA, both), both-filter long/short variants with
5 bps or zero slippage, the same two long/short variants under the legacy-BB
hypothesis, and buy-and-hold. A fourth proxy window, 2025-10-01–2026-10-15,
tests corrected-BB both-filter long-only and long/short, plus buy-and-hold.
The video's displayed out-of-sample cutoff is 2025-09-30 23:00 with unverified
timezone, so this proxy is not claimed to be the identical split or unseen data.

Common rules: BB(20), KC(20,1.5) using SMA true range, 20-bar regression
momentum, ADX(14,14)>20, WMA(120), Wilder ATR(14), fixed 2× signal-ATR stop and
3× signal-ATR target. Signals use completed regular-session 30-minute candles;
entries execute at the next eligible open. Opposite signals can close or reverse
positions. No pyramiding or two-leg/trailing-runner exits are modeled.

Corrected BB width uses multiplier 2. The legacy hypothesis uses 1.5, emulating
the original documented LazyBear multiplier bug, with KC still at 1.5. This
comparison was added before the run because the video shows an unchecked
"Fix BB multiplier bug" option during its Bitcoin demonstration. Its final AAPL
toggle state and modified Pine code remain unverified.

The stop-risk limit is 3% of sizing capital before costs/gaps; whole shares and
available cash can reduce actual risk. Existing annual and drawdown adjustments
to sizing capital remain active. Longs are cash-funded. Short scenarios assume
unlimited AAPL borrowing availability at 3% annually, initial collateral of
150% of short value and 130% maintenance. Those explicit research assumptions
can permit approximately 2× equity gross short exposure; they are not historical
stock-loan or broker facts. Costs are zero commissions and 5 bps per fill unless
the variant explicitly removes slippage. The zero-slippage variants still pay
assumed borrow costs.

## Closest video-window comparison

All implementation rows below cover **2022-01-03–2026-10-15** and use both
ADX and WMA filters with fixed ATR brackets.

| Scenario | Return | Max drawdown | Win rate | PF | Closed campaigns | Borrow costs |
|---|---:|---:|---:|---:|---:|---:|
| Video's displayed single-target result | +133.29% | 17.88% | 47.49% | 1.355 | 219 trades | Unknown |
| Corrected BB, long-only, 5 bps | -13.31% | 26.22% | 41.03% | 0.788 | 78 | $0.00 |
| Corrected BB, long/short, 5 bps | -40.45% | 53.16% | 36.69% | 0.756 | 139 | $122.41 |
| Corrected BB, long/short, zero slippage | -28.93% | 44.44% | 39.57% | 0.835 | 139 | $136.96 |
| Legacy BB(1.5), long/short, 5 bps | +41.39% | 24.53% | 44.75% | 1.164 | 219 | $267.36 |
| Legacy BB(1.5), long/short, zero slippage | +52.71% | 26.14% | 47.27% | 1.196 | 220 | $276.35 |
| Buy-and-hold | +95.85% | 33.04% | — | — | 0 | $0.00 |

The legacy 5 bps scenario had no rejected entries and one forced cover. Its
average gross exposure was 22.11% of equity versus 99.18% for buy-and-hold, so
absolute returns are not exposure matched. The zero-slippage legacy scenario
also had one forced cover. Remaining differences include capital/leverage,
risk sizing, source formula and release transitions, ATR/ADX seeding, data
adjustments, costs, and intrabar order handling. The count match supports
investigating the legacy setting and 2022 range; it does not prove them.

## Longer windows and proxy

| Window | Scenario | Return | Max drawdown | PF | Closed campaigns |
|---|---|---:|---:|---:|---:|
| Full 2016–2026 | Corrected BB, both filters, long-only | -29.95% | 38.73% | 0.772 | 187 |
| Full 2016–2026 | Corrected BB, both filters, long/short | -49.37% | 58.59% | 0.859 | 323 |
| Full 2016–2026 | Legacy BB, both filters, long/short | -39.41% | 61.48% | 0.908 | 507 |
| Full 2016–2026 | Legacy BB, both filters, long/short, zero slippage | +16.58% | 53.38% | 1.042 | 505 |
| Full 2016–2026 | Buy-and-hold | +1406.81% | 38.80% | — | 0 |
| Video 2020–2026 | Corrected BB, both filters, long/short | -45.27% | 56.45% | 0.791 | 189 |
| Video 2020–2026 | Legacy BB, both filters, long/short | -34.15% | 53.45% | 0.852 | 307 |
| Video 2020–2026 | Legacy BB, both filters, long/short, zero slippage | -9.37% | 45.30% | 0.971 | 309 |
| Video 2020–2026 | Buy-and-hold | +380.93% | 34.23% | — | 0 |
| 2025–2026 proxy | Corrected BB, both filters, long-only | -8.54% | 12.30% | 0.506 | 20 |
| 2025–2026 proxy | Corrected BB, both filters, long/short | -25.71% | 28.57% | 0.290 | 30 |
| 2025–2026 proxy | Buy-and-hold | +34.22% | 14.62% | — | 0 |

Net returns and drawdowns include slippage, commissions, and accrued borrow
costs. PF and win rate use flat-to-flat campaign PnL after commissions and
slippage but **exclude separately accrued borrow fees**. Open positions remain
marked in equity and excluded from closed-campaign statistics. Full-history
legacy variants each finished with one open position; all strategy variants
finished flat in both video windows. Buy-and-hold remains invested.

The video's later +145.31% result uses two entry legs and a trailing runner.
Its 384 reported trades are not equivalent to this runner's closed campaigns.
Neither video return is independently verified by this comparison.

## Reproduction and validation

The [generated report](../runs/squeeze-aapl-30m-first/report.md) and
[comparison JSON](../runs/squeeze-aapl-30m-first/comparison.json) contain all
30 scenarios. Each run saves summary, fills, equity and audit artifacts.
[Provenance](../runs/squeeze-aapl-30m-first/provenance.json) records source and
derived data hashes, resolved configurations, date windows, shared/new runner
hashes, engine hashes, and the pre-change Git base. All current engine and runner
hashes were checked after the experiment and matched. Adjusted research prices
do not reproduce actual dividend cash or share transformations.

Run fingerprint:
`0eb03dc51d79ef14d624f8060c8ccfd9cf8a9ac1278099bd6d94941fe79106ad`.

```sh
uv run python -m research.run_aapl_squeeze \
  --source-csv runs/aapl-1m-20160223-20261026/bars.csv \
  --source-manifest runs/aapl-1m-20160223-20261026/manifest.toml \
  --output-dir runs/squeeze-aapl-30m-repeat
```

Eight runner tests pass, covering inclusive end dates, refusal of truncated
windows, intraday warmup/session boundaries, explicit short/cost/legacy presets,
fresh-capital execution, artifact identity, existing-output refusal and symbol
validation. Ruff passes. The main implementation retains its existing engine
tests and separate downloader validation.
