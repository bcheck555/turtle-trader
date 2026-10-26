# Configuration reference

Pass a TOML file with `--config` to `validate`, `import`, `replay`, `backtest`, or
`walk-forward`. Paths resolve from the command's working directory:

```sh
uv run turtle-trader backtest runs/alpaca-spy-qqq/bars.csv \
  --manifest runs/alpaca-spy-qqq/manifest.toml \
  --config configs/swing.toml --output runs/swing.json
```

Without `--config`, the application uses the built-in defaults below. Missing
tables and keys also use those defaults. Each file stands alone: `swing.toml`
does not inherit from `reference.toml`, and files cannot include one another.
Unknown tables or keys are rejected. The only application tables are
`[strategy]`, `[risk]`, and `[execution]`.

Use TOML integers for integer fields, unquoted `true`/`false` for booleans, and
numbers for ATR multipliers. Money, costs, and fractions use `Decimal` internally;
quoted decimal strings (as in the presets) preserve exact decimal values. TOML
numbers are also accepted for these fields. Fractions are proportions:
`"0.005"` means 0.5%, not 0.005%.

## Supplied presets

| File | Purpose | Differences from built-in defaults |
| --- | --- | --- |
| [reference.toml](../configs/reference.toml) | Revelio reference schedule with daily liquidation/reentry | `execution.market_hours = "extended"`; other explicit values match defaults |
| [swing.toml](../configs/swing.toml) | Revelio with overnight holdings and regular-hours execution | `strategy.day_trading = false` |
| [chandelier-swing.toml](../configs/chandelier-swing.toml) | Chandelier exits with overnight holdings and regular-hours execution | `strategy.day_trading = false`, `strategy.exit_strategy = "chandelier"` |

The built-in combination is `day_trading = true` and `market_hours = "regular"`.
Regular-hours execution cannot satisfy the default 16:30 liquidation and 18:00
reentry schedule. Use extended coverage with the reference preset, or select a
swing preset for regular-hours overnight research. Missing deadline quotes can
delay liquidation and leave weekend exposure; the engine records lateness.

## `[strategy]`

| Key | Default | Meaning and accepted values |
| --- | --- | --- |
| `entry_length` | `20` | Completed strategy sessions in the highest-high breakout window; integer 2–400. |
| `exit_length` | `10` | Completed strategy sessions in Revelio's lowest-low exit band; integer 2–400. Ignored for exit decisions and entry readiness in Chandelier mode. |
| `atr_length` | `20` | Completed strategy sessions used to seed the initial-stop Wilder ATR; integer 2–400. |
| `atr_multiple` | `2.0` | Initial-stop distance in ATR units; finite number at least 0.5. Initial stop is `max(entry_level, bar.open) - atr_multiple * ATR`. |
| `interval_minutes` | `5` | Input candle duration; one of `1, 2, 3, 5, 6, 10, 15, 30`. Must match the data manifest. No implicit resampling. |
| `day_trading` | `true` | Enable the indicator's ownership schedule, scheduled liquidation and reentry. `false` permits overnight holdings until a strategy exit. |
| `close_hour` | `16` | Scheduled close hour in America/New_York; integer 0–23. |
| `close_minute` | `30` | Scheduled close minute; integer 0–55 in steps of five. |
| `open_hour` | `18` | Reentry hour in America/New_York; integer 0–23. |
| `open_minute` | `0` | Reentry minute; integer 0–55 in steps of five. |
| `exit_strategy` | `"revelio"` | `"revelio"` uses the reference exit band and initial stop; `"chandelier"` selects the derived trailing-stop alternative. |
| `chandelier_timeframe` | `"session"` | `"session"` uses completed strategy sessions; `"intraday"` uses completed input candles. Does not change entry or initial-stop ATR periods. |
| `chandelier_length` | `22` | Rolling highest-high window in the selected Chandelier timeframe; integer 2–400. Includes history before entry. |
| `chandelier_atr_length` | `22` | Independent Wilder ATR length in that timeframe; integer 2–400. |
| `chandelier_multiple` | `3.0` | Chandelier ATR multiplier; finite positive number. Raw level is highest high minus this multiple of ATR. |
| `chandelier_ratchet` | `true` | Prevent the selected Chandelier stop from decreasing during an underlying signal. `false` permits the raw line to rise or fall. The fixed initial stop remains a floor. |

Strategy sessions use 17:00–17:00 New York boundaries, including daylight-saving
changes. These are neither input candles nor exchange trading dates. Only supplied
bars contribute. The first loaded session is discarded; a prior session is
finalized when the first candle of the next observed session confirms. The final
session is not flushed into its own indicators. Supply enough warmup history for
all required windows and ATR seeds.

With `day_trading = true`, close must precede reentry on the same clock day and
both times must align to `interval_minutes`. Ownership is forbidden between those
times, throughout Saturday, before Sunday reentry, and after Friday close. These
indicator rules are separate from the execution exchange calendar. With
`day_trading = false`, the ownership schedule is disabled, but hour/minute ranges
are still validated.

Chandelier settings are validated even when `exit_strategy = "revelio"`. See
[Chandelier details](chandelier-exit.md) for warmup, stop publication, intrabar
ordering, and ratchet behavior across scheduled liquidation/reentry.

## `[risk]`

| Key | Default | Meaning and accepted values |
| --- | --- | --- |
| `starting_cash` | `"25000"` | Initial simulation cash; finite positive amount. |
| `risk_fraction` | `"0.005"` | Planned risk budget per position as a fraction of equity, including estimated exit slippage and both sides' fees; finite value in `(0, 1]`. |
| `allocation_fraction` | `"0.15"` | Position allocation cap as a fraction of equity when sizing entries; finite value in `(0, 1]`. Does not rebalance appreciated holdings. |
| `max_positions` | `5` | Maximum simultaneous position slots for new entries; positive integer. |
| `daily_loss_fraction` | `"0.02"` | Marked-equity drawdown from the day's baseline that latches the entry gate; finite value in `(0, 1]`. |

Sizing uses whole shares bounded by risk budget, allocation, available cash, and
position slots. There is no margin. Stops and planned risk do not cap losses
through gaps. The daily loss gate blocks new entries and cancels pending buys;
it does not liquidate holdings. It stays latched until the next New York date,
whose baseline uses last observed equity before that day's first opening gap.

## `[execution]`

| Key | Default | Meaning and accepted values |
| --- | --- | --- |
| `slippage_bps` | `"5"` | Adverse price adjustment on buys and sells, in basis points (`5` = 0.05%); finite value in `[0, 10000)`. |
| `fee_per_share` | `"0"` | Fee per filled share on either side; finite nonnegative amount. |
| `fee_per_order` | `"0"` | Flat fee charged on an order's first fill, once across partial fills; finite nonnegative amount. |
| `participation_rate` | `"1"` | Fraction of historical candle volume available for simulated fills; finite value in `(0, 1]`. Share capacity is rounded down and shared across fills for that symbol/candle. |
| `market_hours` | `"regular"` | `"regular"` or `"extended"`; controls eligible execution openings using the exchange calendar. |

Participation is a historical liquidity scenario, not volume known at the open
or queue priority. Market hours do not filter strategy indicator observations.
`replay` emits indicator events without simulated fills; portfolio risk and
execution assumptions apply to `backtest` and `walk-forward`.

## Data and command settings

The dataset's `[data]` manifest is a separate file passed with `--manifest`, not
an application config table. See [data format](../README.md#data-format). All five
data commands check that strategy and manifest intervals agree. Backtest and
walk-forward also require execution hours to match declared data coverage, except
for explicitly synthetic `custom` coverage.

Downloader options (`--interval`, `--feed`, `--coverage`, dates and symbols) are
CLI arguments; Alpaca credentials come from `APCA_API_KEY_ID` and
`APCA_API_SECRET_KEY`. See [downloader details](alpaca-downloader.md). Output paths,
database paths, trade windows, benchmarks, and fold/warmup days are also CLI
arguments, not TOML keys. Use each command's `--help` for those options.

The repository's `pyproject.toml` configures packaging, dependencies, pytest,
Ruff and mypy; `uv.lock` pins dependencies. Neither is a strategy preset. Loader
and validation definitions live in [config.py](../src/turtle_trader/config.py) and
[domain.py](../src/turtle_trader/domain.py).
