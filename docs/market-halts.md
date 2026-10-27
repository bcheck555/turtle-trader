# Verified market-wide trading pauses

Real exchange coverage exempts a candle only when its entire half-open interval
lies inside a sourced market-wide pause. Partially tradable boundary bars remain
required. The catalog currently contains these four events:

| Date | Halt start UTC | Reopening UTC | Whole one-minute bars excluded |
|---|---|---|---:|
| 2020-03-09 | 13:34:13 | 13:49:13 | 14 |
| 2020-03-12 | 13:35:44 | 13:50:44 | 14 |
| 2020-03-16 | 13:30:01 | 13:45:01 | 14 |
| 2020-03-18 | 16:56:17 | 17:11:17 | 14 |

Source: [NYSE Market-Wide Circuit Breaker Working Group report, March 31, 2021,
printed pages 4–5](https://www.nyse.com/publicdocs/nyse/markets/nyse/Report_of_the_Market-Wide_Circuit_Breaker_Working_Group.pdf).
The report supplies Eastern times; these dates are during daylight saving.
Catalog identifier: `nyse-mwcb-2020-v1`, in `src/turtle_trader/halts.py`.
This is a limited catalog. Unknown omissions still fail strict validation.

## Coverage and execution

`expected_count` counts tradable slots. `halted_count` and per-session
`halted_ranges` disclose exclusions separately with classification
`market_wide_halt`. Unknown `missing_count` remains separate. A candle supplied
wholly within a known pause is outside coverage and fails strict import.
Custom synthetic coverage does not inherit these pauses; injected calendars must
explicitly opt in with `market_halts = True`.

No flat candles are created. Positions and pending orders carry through pauses.
The engine never interpolates a price path between consecutive bars. A stop
crossed during a pause executes against the next observed bar's opening price,
subject to configured slippage, capacity and affordability.

Reopening minute labels can precede reopening by seconds. March 9's candle
labeled 13:49:00 cannot justify an execution before 13:49:13. Execution timestamps
are bounded below by the verified restart, not presented as observed security
trade times. `bar_open` precision describes use of the candle's opening price.
Original observation labels and publication times remain unchanged for indicators.
Coarser bars spanning pauses retain normal OHLC ordering uncertainty.

## Preparing the QQQ archive

`runs/qqq-1m-20160223-20261023/` retains February 23, 2016 onward. The unresolved
February gaps precede this cutoff. No retained price or volume field is changed.
New coverage/checksums accompany derivative provenance nesting the original
acquisition metadata, without keeping the discarded price rows.

Preparation verifies source checksums, writes a new directory, strictly validates
retained data and publishes atomically. Existing/nested destinations are refused.
The utility never deletes its source:

```sh
.venv/bin/python -m turtle_trader.archive SOURCE DESTINATION --cutoff 2016-02-23
```

The authorized purge of the older source and February diagnostic recheck occurs
separately after destination validation. Bars remain in Git-ignored `runs/`;
production code, tests and this policy are version controlled.

## Validation evidence

The prepared QQQ archive contains 1,042,924 bars across 2,684 sessions, with zero
unknown missing/outside bars and 56 verified halt slots reported separately.
Strict import and full-history engine preflight passed. Retained CSV rows match
the source filtered at the cutoff byte for byte; all core checksums passed.
The 13,201 pre-cutoff source rows and older diagnostic recheck were purged only
after replacement validation. Original acquisition metadata remains in provenance.

294 tests passed; Ruff, mypy and source/wheel builds passed. Tests include a
held position crossing its stop during a pause and exiting at the observed
reopening price, calendar opt-in, source checksum failures, unknown retained gaps,
and archive publication safety. The exchange calendar caches immutable session
bounds per instance to support minute history without repeated schedule lookups.
