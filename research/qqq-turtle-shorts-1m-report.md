# QQQ Turtle with shorting — matched ATR comparisons

**Shorting reduced returns and increased drawdown with both exits, including the zero-borrow-fee scenarios.** The short trades themselves lost money before borrowing costs.

## Why these controls differ from the previous cash-sized runs

The production engine deliberately supports `available_cash` only for long-only trading. Its guard remains in place. These experiments use the existing **ATR sizing mode** for both long-only controls and short-enabled cases; no production trading logic was changed.

All six cases retain the previous one-unit limit, $25,000 starting cash, one-minute QQQ data, May 11, 2016 opening start after 55 warmup sessions, October 23, 2026 final close, 5bps slippage, System 2 entries and the same two exits.

Compare short-enabled cases to their **ATR long-only controls below**. The earlier available-cash channel/Chandelier returns of 169.37%/107.40% use different sizing and are not the controls for isolating shorting.

## Results

Trading window: **May 11, 2016–October 23, 2026**, 2,629 sessions and 1,021,474 matching minute-close equity marks per case. Annualized growth uses 252 sessions/year. Account returns include borrow charges.

| Exit | Mode | Total return | Annualized growth | Minute-close drawdown | Ending equity | Borrow charged |
|---|---|---:|---:|---:|---:|---:|
| Channel | Long only | 88.56% | 6.27% | 10.32% | $47,140.73 | $0.00 |
| Channel | Long + short; 0% borrow | 37.90% | 3.13% | 17.92% | $34,473.93 | $0.00 |
| Channel | Long + short; 3% borrow | 35.62% | 2.96% | 17.89% | $33,903.76 | $626.60 |
| Chandelier | Long only | 62.30% | 4.75% | 8.65% | $40,574.83 | $0.00 |
| Chandelier | Long + short; 0% borrow | 3.94% | 0.37% | 22.71% | $25,985.54 | $0.00 |
| Chandelier | Long + short; 3% borrow | 2.83% | 0.27% | 23.17% | $25,706.51 | $274.24 |

The matched buy-and-hold account returned **607.46%**, with **37.16%** minute-close drawdown and ending equity **$176,865.13**. It uses the same opening date, whole-share cash account and entry slippage. All six Turtle accounts ended flat; buy and hold remained invested.

Enabling shorts with zero borrow fees reduces channel return by **50.67 percentage points** and Chandelier return by **58.36 percentage points**. No configuration here beats buy and hold's return.

## Actual short activity

Direction P&L below is realized P&L using executed prices, **before explicit execution fees and borrow allocation**. Executed prices already include adverse slippage. Explicit execution fees are zero. Account returns in the main table include borrowing. The long P&L within a short-enabled account is not the standalone long-only account's P&L: losses and fees alter later capital and quantities.

| Exit | Borrow APR | Short opening fills | Closed short trades | Realized short P&L before borrow | Realized long P&L before borrow | Maximum short exposure/equity | Funding rejection records |
|---|---:|---:|---:|---:|---:|---:|---:|
| Channel | 0% | 26 | 26 | $-11,097.99 | $20,571.91 | 92.39% | 844 |
| Channel | 3% | 26 | 26 | $-10,880.46 | $20,410.83 | 92.43% | 844 |
| Chandelier | 0% | 32 | 32 | $-9,822.06 | $10,807.59 | 91.62% | 16123 |
| Chandelier | 3% | 32 | 32 | $-9,803.87 | $10,784.61 | 91.65% | 16123 |

Funding counts come from each saved `audit.json`, reason `insufficient_cash`. They are rejection **records**, potentially repeated on successive candles, not unique missed trades. Unaffordable whole ATR units are rejected rather than shrunk. Long-only controls have 844 channel and 10669 Chandelier funding rejection records. Their `short_not_permitted` records do not indicate actual short positions.

No forced-cover fills/orders or margin-breach cancellations occurred. Maximum observed gross exposure stayed below 100% of equity in every case. The engine can permit larger short exposure under its collateral assumptions, but these actual results did not use gross exposure above equity.

## Fixed strategy and execution assumptions

- ATR sizing: `risk.unit_fraction="0.01"`, `risk.position_sizing="atr"`, `max_units=1`. This sizes **1% of sizing capital per 1N move**, approximately 2% at the initial 2N stop before gaps and costs; it is not a 1% stop-loss-risk target.
- The existing weekly N sizing, frozen campaign quantities/N, drawdown sizing reductions and annual sizing-capital behavior remain active.
- System 2: 55-session entry channels; original exit is the opposite 20-session channel. N uses ATR(20); the 2N protective stop remains active in both exits.
- Chandelier: session high/low window 22, ATR(22), multiple 3, ratchet enabled. These remain **daily/session indicators**, not 22-minute indicators.
- Short-enabled cases set `allow_short=true`, explicitly allowlist `QQQ`, and explicitly supply annual borrow rate 0 or 0.03.
- 150% of short entry value is locked collateral: sale proceeds plus 50% additional cash. Maintenance compares entry-based collateral with 130% of current short liability; observed breaches queue covers at later tradable quotes. This is the existing engine model, not a complete broker margin implementation.
- Borrow accrues over calendar-day gaps, including weekends, using the preceding short-liability mark and annual rate divided by 365, before the next day's opening fills.
- 5bps adverse slippage per fill, zero per-share/per-order commissions, zero cash interest. No forced final sale is imposed; final holdings are marked to the final observed close.
- Configurations differ only in exit, short permission and borrow rate. The free-borrow pair differs from its matching long-only control **only in short permission**.

Both borrow rates and uninterrupted QQQ short availability are **scenario assumptions**, not observed historical stock-loan data. [Alpaca's current documentation](https://docs.alpaca.markets/us/docs/margin-and-short-selling) describes zero fees for easy-to-borrow shares; that does not establish QQQ's loan availability or fees throughout 2016–2026. The 3% case is an additional cost scenario, not a claimed historical rate. Fee changes can also affect future sizing and fills, so the six cases were simulated separately rather than subtracting a fee afterward.

## Annual returns: zero-borrow comparison

First and last years are partial. This table separates direction permission using identical ATR sizing and 0% borrow.

| Year | Buy and hold | Channel long only | Channel long + short | Chandelier long only | Chandelier long + short |
|---|---:|---:|---:|---:|---:|
| 2016 (partial) | 11.41% | 2.78% | 0.74% | 2.45% | 0.78% |
| 2017 | 32.55% | 17.41% | 17.69% | 4.81% | 4.94% |
| 2018 | -0.12% | 0.71% | 7.16% | 1.43% | -4.39% |
| 2019 | 38.87% | 9.65% | 4.24% | 9.20% | 4.06% |
| 2020 | 48.51% | 10.50% | 0.37% | 8.84% | -4.57% |
| 2021 | 27.36% | 1.59% | -2.79% | 2.49% | -1.92% |
| 2022 | -32.37% | -2.64% | -1.12% | -2.23% | -7.45% |
| 2023 | 54.75% | 12.05% | 8.00% | 13.53% | 9.55% |
| 2024 | 25.58% | 9.84% | 3.87% | 4.20% | -1.04% |
| 2025 | 20.76% | 3.77% | 5.69% | 0.36% | 7.65% |
| 2026 (partial) | 15.05% | 1.38% | -8.98% | 5.40% | -2.29% |

## Data and verification

Input `runs/qqq-1m-20160223-20261023/` has 1,042,924 bars, zero unknown missing/outside bars and 56 documented fully halted minute slots. No candles were fabricated. All six completed runs use the unchanged production engine with streamed outputs under `runs/qqq-turtle-shorts-1m/`.

The independent auditor reconstructs signed per-unit positions, cash and collateral from fills; charges borrow before each new session using calendar gaps; then reconciles **cash, equity, gross/net exposure, short liability, collateral and cumulative borrow costs at every minute**. It also checks entry funding, proportional collateral release, direction/side consistency, no opposing campaigns or overcovers, fill timestamps outside verified halts, source/config identity, matching windows and summary metrics.

All **6,128,844** account marks passed. Maximum floating-point discrepancy is below $0.000001. All **42** saved output artifact checksums, runner hashes and recorded production-source hashes passed. All six native run commands exited successfully.

Validation: **39 focused Python shorting/risk/Chandelier/runner checks passed**, **7 JavaScript short-ledger tests passed**, Ruff passed on the runner/test, and source-aware mypy passed on 24 files. Hand cases include profitable and losing shorts, adverse covers with negative cash, weekend borrow, partial collateral release, funding/direction errors, halt timing, streamed reconciliation and deliberate corruption.

An independent reviewer confirmed the sizing constraint, actual configuration deltas and ledger formulas. Review corrected two reporting labels: forced covers are counted by their actual `forced_cover` reason, and borrow-charge sessions are labeled as sessions rather than calendar days.

## Interpretation limits

Results use vendor-adjusted research prices, without separate dividend cash, dividend-in-lieu charges or historical share transformations. These are adjusted-price simulations rather than point-in-time short-account reconstructions. Actual historical locates, borrow recalls, broker-specific restrictions and financing were not supplied.

Minute-close drawdown misses within-minute/tick extremes. OHLC path order remains uncertain; the engine retains its conservative path selection and verified-halt reopening bounds. These are full-history comparisons of preselected settings, not a new unseen sample or a significance claim.

## Artifacts and reproduction

Machine-readable comparison: `research/qqq-turtle-shorts-1m-results.json`. Each run directory contains resolved config, source metadata, complete streamed equity/fills, compressed events, audit state and checksum-bearing completion record.

```sh
.venv/bin/python research/run_turtle_minute.py turtle_channel runs/qqq-turtle-shorts-1m/channel-long --position-sizing atr --borrow-rate 0
.venv/bin/python research/run_turtle_minute.py turtle_channel runs/qqq-turtle-shorts-1m/channel-short-free --position-sizing atr --allow-short --borrow-rate 0
.venv/bin/python research/run_turtle_minute.py turtle_channel runs/qqq-turtle-shorts-1m/channel-short-3pct --position-sizing atr --allow-short --borrow-rate 0.03
.venv/bin/python research/run_turtle_minute.py chandelier runs/qqq-turtle-shorts-1m/chandelier-long --position-sizing atr --borrow-rate 0
.venv/bin/python research/run_turtle_minute.py chandelier runs/qqq-turtle-shorts-1m/chandelier-short-free --position-sizing atr --allow-short --borrow-rate 0
.venv/bin/python research/run_turtle_minute.py chandelier runs/qqq-turtle-shorts-1m/chandelier-short-3pct --position-sizing atr --allow-short --borrow-rate 0.03
node research/turtle-short-audit.cjs
node --test research/turtle-short-audit.test.cjs
```

Existing destinations are refused; choose fresh directories for new engine runs. The auditor defaults to the six successful paths above.

- channel-long: run `280e4bee-d06f-40c5-ba32-9fef488f9434`; fingerprint `e91947b349f34cb9fda08e5a8289409c6e4b0353dd9cb8b4ed9814b12ee99768`.
- channel-short-free: run `1f9fcb92-0645-444f-af67-9935fec54f67`; fingerprint `a7909f0389ac1e11aa945539778b954a883fedb155fabc8c1e35320e64cbaee4`.
- channel-short-3pct: run `ab2d40ce-939f-40c6-b4d2-7a8298ea96c4`; fingerprint `13a838f826d684c5854c497a6da41916c07b107be013933459bcb61a32caa1e5`.
- chandelier-long: run `a6343bdd-9fa7-4b74-bc20-51f725a4cf6d`; fingerprint `76bcef03a3192aa8657b8a6823608c63325a1e870e59866cdcf7c30345ff05ad`.
- chandelier-short-free: run `fb588912-e05f-4e39-bff6-e005c3a35bef`; fingerprint `e22bc804465b3490725a795c6f123a830c6f44c80ca35eac3875cc7c449d9d8f`.
- chandelier-short-3pct: run `bccf7b9c-10e5-4813-beae-46ac7b431da5`; fingerprint `5b3530278604a687e5dcfc66277b07f9223852ae343969e152335353451758e1`.

Two sub-agents inherited coordinator model/settings without overrides: one reviewed shorting mechanics and comparisons, and one implemented/tested the signed ledger auditor. Coordinator configured and ran six production experiments, verified output integrity and assembled the report. No production trading logic or short-safety guard changed.

Input CSV SHA-256: `551fd5feed7c8b230d90c4f36095a802ab095eacaa7709f1908b232dc6effe93`.
