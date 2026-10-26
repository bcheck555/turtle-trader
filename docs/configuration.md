# Configuration

Files are standalone TOML with `schema_version = 2` and optional `[strategy]`,
`[risk]`, `[execution]` tables. Omitted settings use defaults. Unknown tables,
settings and obsolete keys are rejected. Decimal settings accept decimal strings
or numbers. Fractions use units of one: `"0.01"` means 1%.

`--set 'section.key=TOMLliteral'` overrides a file setting. Repeat for distinct
keys; duplicate assignments are errors. Defaults resolve from the selected entry
system before explicit file/CLI values apply. `--print-config` emits the result
without needing a dataset. Custom periods/mechanics appear as canonical
deviations in reports.

| Preset | Entry | Exit |
| --- | --- | --- |
| [system2.toml](../configs/system2.toml) | System 2 | Opposite 20-session channel |
| [system1.toml](../configs/system1.toml) | System 1 with filter/failsafe | Opposite 10-session channel |
| [system2-chandelier.toml](../configs/system2-chandelier.toml) | System 2 | Chandelier |

## `[strategy]`

| Key | Default | Meaning |
| --- | --- | --- |
| `entry_strategy` | `"turtle_s2"` | Registered entry rule; built-ins `turtle_s1`, `turtle_s2`. |
| `exit_strategy` | `"turtle_channel"` | Registered exit rule; built-ins `turtle_channel`, `chandelier`. |
| `allow_short` | `false` | Permit actual short-opening orders. System 1 hypothetical shorts remain active regardless. |
| `entry_length` | `55` / `20` | Preceding completed-session entry window for System 2 / System 1. |
| `exit_length` | `20` / `10` | Opposite channel exit window for System 2 / System 1. |
| `failsafe_length` | `55` | System 1 skipped-entry failsafe window. |
| `atr_length` | `20` | Daily N seed/update length. |
| `atr_multiple` | `2.0` | Mandatory initial unit-stop distance in campaign N. |
| `add_multiple` | `0.5` | Addition spacing and prior-stop movement in campaign N. |
| `interval_minutes` | `5` | Input candle interval; must agree with manifest. |
| `tick_size` | `"0.01"` | Positive instrument tick for channel triggers. |
| `chandelier_timeframe` | `"session"` | `session` or completed `intraday` candles. |
| `chandelier_length` | `22` | Rolling high/low window. |
| `chandelier_atr_length` | `22` | Independent Wilder ATR window. |
| `chandelier_multiple` | `3.0` | Distance from rolling high/low in Chandelier ATR. |
| `chandelier_ratchet` | `true` | Prevent long line falling or short line rising during a campaign. |

Indicators use regular exchange sessions. N/unit-size snapshots publish weekly;
campaign N and intended shares freeze at initial entry. There are no daily
liquidation/reentry settings. A newly calculated level affects only later fills.
Lengths and multiples must pass the configuration model's numeric bounds;
nonfinite values and booleans used as numbers are rejected.

## `[risk]`

| Key | Default | Meaning |
| --- | --- | --- |
| `starting_cash` | `"25000"` | Positive starting account cash. |
| `unit_fraction` | `"0.01"` | Sizing-capital fraction represented by one 1N move. |
| `max_units` | `4` | Maximum units per symbol. |
| `close_limit` | `6` | Same-direction units per close correlation group. |
| `loose_limit` | `10` | Same-direction units per loose correlation group. |
| `direction_limit` | `12` | Maximum total units per direction. |
| `close_groups` | `[]` | Arrays of symbol arrays; empty groups conservatively group all symbols together. |
| `loose_groups` | `[]` | Arrays of symbol arrays; overrides must cover the complete universe. |

Pending/partial units consume capacity. Whole-share units must fit available
cash; there is no long borrowing or automatic downsizing to fit an allocation cap.
Drawdowns immediately cap new campaigns' weekly share snapshots without resizing
active campaigns. Sizing capital becomes zero at the geometric threshold limit.
Annual baseline resets before new fills at the first observed opening of each
year. No external cash flows are modeled.

## `[execution]`

| Key | Default | Meaning |
| --- | --- | --- |
| `slippage_bps` | `"5"` | Nonnegative adverse basis points per fill, less than 10,000. |
| `fee_per_share` | `"0"` | Nonnegative cost for each filled share. |
| `fee_per_order` | `"0"` | Nonnegative per-order cost. |
| `participation_rate` | `"1"` | Fraction of observed candle volume available for simulated fills, in `(0,1]`. |
| `market_hours` | `"regular"` | Regular-session execution only. |
| `shortable_symbols` | `[]` | Explicit symbols permitted for short opening. |
| `borrow_rate` | absent | Annual borrowing fraction; explicitly required when shorts are enabled. |
| `initial_short_collateral` | `"1.5"` | Proceeds plus additional 50% entry collateral. |
| `maintenance_short_collateral` | `"1.3"` | Required collateral/current-liability multiple. |

Short proceeds remain locked. Borrow accrues on calendar days; covers can be
forced after observed maintenance/cash breaches. Availability, fees and margin
are research assumptions, not historical broker records.

## Parameter sweeps

[parameter-grid.toml](../configs/parameter-grid.toml) demonstrates the grid format:

```toml
[parameters]
"strategy.entry_length" = [40, 55, 70]
"strategy.exit_length" = [15, 20]
```

Each array is nonempty. The Cartesian product is stable and fully validated
before execution; duplicate resolved configurations are errors. Base `--set`
overrides apply before the grid's candidate values. Use unique output directories
per experiment/agent. Resume requires matching experiment identity and completed
artifacts. Aggregate JSON/CSV retains failed runs and their errors.
