# QQQ and SPY exit comparison

Analysis date: 2026-10-26. Engine commit: `06e0da050d3da16e307489096304fe63f5a6cfe6`.

Chandelier-swing performs better on QQQ; swing performs better on SPY. Each wins
on return, maximum drawdown, and profit factor for its respective ETF. These
results do not establish a generally superior exit: sample sizes are small and
the aggregate difference is slight.

## Data and matched assumptions

- Local files: `runs/alpaca-qqq/{bars.csv,manifest.toml}` and
  `runs/alpaca-spy/{bars.csv,manifest.toml}`. No new market data downloaded.
- Period: 2024-01-02 through 2026-06-30, regular-session five-minute SIP bars.
  Each dataset contains 48,534 bars; saved coverage reports indicate complete
  coverage with zero missing bars. Each backtest also passed strict validation.
- Each ETF starts in a separate $25,000 account. Both configs hold overnight,
  use 5 bps adverse slippage per fill, and charge zero explicit fees.
- Default risk: 0.5% planned risk, 15% new-position allocation, no margin,
  five-position limit, and a 2% daily equity-loss gate for new entries.
- `configs/swing.toml`: default Revelio exit (10-session exit band and fixed
  initial stop). `configs/chandelier-swing.toml`: ratcheted session Chandelier,
  highest high over 22 sessions minus 3 times Wilder ATR(22), with the fixed
  initial stop as a floor. Entry and execution configuration otherwise match.
- Runs start flat with indicator warmup inside the supplied history. Exit choice
  also changes signal state, entry readiness, and later entry opportunities;
  these are full strategy runs, not paired exits applied to identical entries.

## Full-period results

Returns below are cumulative account returns, not annualized or returns on
invested capital. Drawdown uses marked account equity at bar closes.

| ETF | Config | Net P&L | Return | Max drawdown | Closed trades | Win rate | Profit factor | Average exposure |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| QQQ | swing | $152.19 | 0.61% | 2.15% | 20 | 40.0% | 1.15 | 6.60% |
| QQQ | chandelier-swing | $351.18 | 1.40% | 1.77% | 17 | 41.2% | 1.41 | 6.53% |
| SPY | swing | $766.63 | 3.07% | 1.04% | 16 | 56.3% | 3.27 | 7.72% |
| SPY | chandelier-swing | $521.15 | 2.08% | 1.21% | 15 | 46.7% | 2.24 | 7.75% |

For QQQ, Chandelier adds $199.00 and reduces drawdown by 0.37 percentage points.
For SPY, swing adds $245.48 and reduces drawdown by 0.18 percentage points.
Chandelier produces fewer trades and lower turnover for both ETFs.

Combining the two independent $25,000 accounts, swing returns 1.84%
($918.81), versus 1.74% ($872.33) for Chandelier. Their synchronized combined
equity curves have maximum drawdowns of 1.43% and 1.41%, respectively. This is a
sum of separate accounts, not a simulation of shared capital or portfolio sizing.
Swing's $46.48 advantage on $50,000 is too small to support a broad winner claim.

## Calendar-period account returns

These use consecutive year-end marked equity from the continuous runs; positions
and indicator state carry across years. 2026 covers January through June only.

| ETF | Config | 2024 | 2025 | 2026 H1 |
| --- | --- | ---: | ---: | ---: |
| QQQ | swing | -0.11% | -0.45% | 1.17% |
| QQQ | chandelier-swing | 0.39% | -0.38% | 1.40% |
| SPY | swing | 1.77% | 0.38% | 0.89% |
| SPY | chandelier-swing | 0.60% | 0.62% | 0.85% |

QQQ's Chandelier advantage appears in all three periods. SPY's swing advantage
is concentrated in 2024; Chandelier does better in 2025. Neither observation is
an independent out-of-sample test.

## Interpretation limits

Only 15–20 closed trades occur per run. No parameter search, statistical
significance test, or held-out selection test was performed. Choosing a different
exit for each ETF based on this history would be an in-sample selection requiring
fresh validation.

Average portfolio exposure is only about 6.5–7.8%; most capital sits in cash.
These account returns should not be compared directly with fully invested ETF
returns. No benchmark was supplied to the reports.

Both manifests declare `adjustment="all"` and `price_basis="adjusted_research"`.
Simulated quantities and returns use vendor-adjusted prices; actual dividend cash,
share transformations, and point-in-time adjustment histories are not modeled.
Chandelier is a derived strategy without verified Revelio Pine exit parity.
Drawdown does not measure an exact intrabar worst-case equity path.

## Outputs and reproduction

Fresh JSON reports and companion CSVs remain in ignored `runs/`:

- `runs/exit-comparison-qqq-swing.json`
- `runs/exit-comparison-qqq-chandelier-swing.json`
- `runs/exit-comparison-spy-swing.json`
- `runs/exit-comparison-spy-chandelier-swing.json`
- Run records: `runs/exit-comparison.sqlite`.

From the repository root, repeat for `symbol=qqq` and `symbol=spy`:

```bash
symbol=qqq
for config in swing chandelier-swing; do
  .venv/bin/turtle-trader backtest "runs/alpaca-$symbol/bars.csv" \
    --manifest "runs/alpaca-$symbol/manifest.toml" \
    --config "configs/$config.toml" \
    --output "runs/exit-comparison-$symbol-$config.json" \
    --database runs/exit-comparison.sqlite
done
```

Raw input CSV SHA-256:

- QQQ: `da83b9d0fcbb761d643f9d3a044bf073ec9b16a28027bb1be618fd46aa8cd843`
- SPY: `193ed38a22ee1c161a1b5398b1960305bddcb792a12a8c5da630335616ac8178`

Config file SHA-256:

- swing: `e7658b4ba88318b5f30c46f23e8c6e279b8c6aea716a1edc61c71377389f469c`
- chandelier-swing: `eb8131e6670ef6083b063a9901cf6dcf27e42ca98cf177412aa7a9b8f9e0c178`

Report metadata also records normalized config, data, and engine source hashes.
All four runs finish flat with no pending orders, rejected orders, scheduled
deadline exits, or entry-bar stop ambiguities. Independently recomputing cash
from fills and maximum drawdown from equity reconciles all four summaries.

Validation: `.venv/bin/pytest -q tests/test_chandelier*.py tests/test_reporting.py
tests/test_backtest.py tests/test_download_cli.py` — 83 passed, 44 warnings.
No strategy or engine code changed for this comparison.
