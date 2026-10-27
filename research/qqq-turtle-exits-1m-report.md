# QQQ Turtle System 2 — both exits on one-minute history

**The original channel exit returned more; Chandelier reduced drawdown further. Neither beat buy and hold's return.**

This is the latest **available-cash, one-position** System 2 setup, with real production-engine entries, stops and minute equity marks. It uses one cash-funded long position with no pyramiding. The production engine was not changed.

## Matched comparison

Trading window: **May 11, 2016 opening–October 23, 2026 close**. All accounts start with **$25,000**, use whole shares and 5bps slippage, and have 1,021,474 matching minute-close equity marks across 2,629 sessions. Annualized growth uses 252 sessions per year.

| Account | Total return | Annualized growth | Minute-close drawdown | Ending equity | Closed trades |
|---|---:|---:|---:|---:|---:|
| Buy and hold | 607.46% | 20.63% | 37.16% | $176,865.13 | 0; still holding |
| Turtle channel exit | 169.37% | 9.96% | 16.26% | $67,342.14 | 30 |
| Chandelier exit | 107.40% | 7.24% | 13.66% | $51,850.10 | 50 |

Channel ended $15,492.05 ahead of Chandelier. Chandelier traded more often (50 versus 30 completed trades) and had lower average invested exposure (42.45% versus 52.08%). Both gave up substantial upside in exchange for smaller drawdowns. These are historical results, not evidence of future outperformance.

This buy-and-hold figure differs from the earlier 16-rule table because Turtle starts after **55 sessions**, in May 2016, while the daily-rule comparison starts after **200 sessions**, in December 2016. The Turtle comparison also marks the actual final close. Those benchmark figures should not be mixed.

## Settings

The resolved configurations differ **only** in `strategy.exit_strategy`.

- System 2: 55 completed-session entry breakout; 20-session original opposite-channel exit.
- Alternative exit: Chandelier high window 22 sessions, ATR(22), 3× multiple, ratchet enabled, `chandelier_timeframe="session"`.
- Both retain the mandatory **2N protective stop**, with N based on ATR(20).
- Source/execution cadence: **one minute**. Entry/channel/Chandelier indicator windows remain measured in **sessions**, not minutes.
- Latest preset: `configs/system2-available-cash.toml`, overridden to one-minute input and the selected exit.
- Starting cash $25,000; `position_sizing="available_cash"`; maximum one unit; long only; no leverage or pyramiding.
- 5bps (0.05%) adverse slippage is embedded in each buy/sell fill. Explicit per-order/per-share fees are zero. Cash earns no interest.
- Historical bar volume is the configured participation-capacity scenario; no queue priority or observed spread is asserted.
- All 55 warmup sessions, February 23–May 10, 2016, update indicators before trading begins on May 11. The benchmark buys at that same eligible opening, not at Turtle's later first fill.

The benchmark buys **251 shares at 99.34965**, including entry slippage, and keeps **$63.24** residual cash. It retains those shares through the final close. No forced final sale is imposed on any account. In these actual runs both Turtle accounts finish flat; buy and hold still owns its shares.

## Year-by-year returns

First and final years are partial. Returns use the same minute-close account marks and calendar-year boundaries for all three accounts.

| Year | Buy and hold | Channel exit | Chandelier exit |
|---|---:|---:|---:|
| 2016 (partial) | 11.41% | 1.98% | -0.07% |
| 2017 | 32.55% | 18.57% | 11.50% |
| 2018 | -0.12% | -1.14% | -0.40% |
| 2019 | 38.87% | 14.93% | 13.00% |
| 2020 | 48.51% | 26.82% | 14.74% |
| 2021 | 27.36% | 1.41% | 1.93% |
| 2022 | -32.37% | -5.92% | -4.94% |
| 2023 | 54.75% | 22.53% | 25.98% |
| 2024 | 25.58% | 15.51% | 4.32% |
| 2025 | 20.76% | 10.81% | 3.12% |
| 2026 (partial) | 15.05% | 3.32% | 9.76% |

## Data, halts and interpretation

Cleaned input: `runs/qqq-1m-20160223-20261023/`, 1,042,924 bars; zero unknown missing/outside candles; 56 verified fully halted minute slots separately documented. Trading pauses do not receive fabricated bars. Reopening executions cannot precede the verified restart and use the next observed price including gaps.

Drawdown here is sampled at minute closes, **not at every tick**. Each OHLC candle still leaves intraminute order uncertain; the engine uses its existing conservative synchronized path selection. Reopening timestamps are simulation bounds rather than exact observed security trade timestamps.

Prices are fully vendor-adjusted research data. No separate dividend cash, actual split/share transformations, taxes, cash yield or historical bid/ask quotes are modeled. No parameters were optimized in this run, and this is not a new unseen validation sample.

## Successful artifacts and export handling

Successful output directories:

- `runs/qqq-turtle-exits-1m-streamed/channel/`
- `runs/qqq-turtle-exits-1m-streamed/chandelier/`

Each contains resolved `config.json`, source/config identity in `metadata.json`, small `engine-summary.json`, streamed `report.csv`, complete compressed `events.jsonl.gz`, `fills.json`, final audit state in `audit.json`, and final `run.json` with artifact checksums and completion status.

The initial standard CLI attempts failed with SQLite `string or blob too big` before publishing results. Those incomplete directories under `runs/qqq-turtle-exits-1m/` are **not used**. Successful runs directly invoke the unchanged production `run_backtest` and `summarize_result` APIs; only artifact storage differs. Streaming avoids a single giant SQLite record and repeated per-row metadata.

Run IDs:
- Channel: `c8ba5061-e6e2-4cb4-b015-69a95c8d087f`; fingerprint `b4af5b645b2a3241f3d7fcb95bd986690a3534e0881a5f9902f3e5caf4993af3`.
- Chandelier: `20ac0d80-ac24-457d-9e60-3e8be983df8b`; fingerprint `e3be1adf99f40ce42511f087f596a5b2bff2679c900ab3b7b05d2fa6ddbf97d7`.

## Verification and reproduction

Both production runs completed successfully. Each has 1,021,474 equity observations. Channel records 60 fills and 1,043,541 events; Chandelier records 100 fills and 1,043,581 events.

The independent streaming auditor:
- Checks that configurations differ only by exit and validates all required settings.
- Recomputes return, drawdown, turnover, fees and completed trades from exported records.
- Reconstructs cash and whole shares from ordered fills and reconciles **every minute's equity against the actual source closing price**. Maximum floating-point discrepancy is below $0.000001.
- Verifies matching source timestamps and account windows, CSV/fill consistency, nonnegative cash/long shares, final holdings and no fill timestamps inside verified halts.
- Verifies source coverage/checksum. All 14 successful output artifact checksums, the runner hash and all recorded production-source hashes were additionally checked.

Seven JavaScript audit tests and one Python streamed-export equivalence test passed. Ruff passed for the Python runner/test; source-aware mypy passed on 24 files. An independent reviewer verified the benchmark arithmetic, settings and minute ledger logic, independently ran the seven audit tests, and proved streamed CSV/event preservation on a separate fixture.

```sh
.venv/bin/python research/run_turtle_minute.py turtle_channel runs/qqq-turtle-exits-1m-streamed/channel
.venv/bin/python research/run_turtle_minute.py chandelier runs/qqq-turtle-exits-1m-streamed/chandelier
node research/turtle-minute-audit.cjs
node --test research/turtle-minute-audit.test.cjs
.venv/bin/python -m pytest tests/test_minute_runner.py -q
```

Existing run directories are refused; choose a fresh destination to rerun the engine. The auditor's default reads the successful directories above. Machine-readable comparison: `research/qqq-turtle-exits-1m-results.json`.

Two sub-agents inherited coordinator model/settings without overrides: one independently reviewed methodology, exports and benchmark; one implemented and tested the streaming ledger auditor. Coordinator ran both engines, implemented streamed packaging, checked artifact integrity and assembled this report. No production trading logic changed.

Minute CSV SHA-256: `551fd5feed7c8b230d90c4f36095a802ab095eacaa7709f1908b232dc6effe93`.
