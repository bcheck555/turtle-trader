# Architecture

The application separates prospective trading rules, campaign/unit management,
execution/accounting, and experiment orchestration. Historical acquisition is
explicit and never invokes trading endpoints.

## Data flow

1. Load CSV candles and their manifest; validate timestamps, adjustment basis,
   chronology, intervals and exchange-session coverage.
2. Assemble completed regular sessions. Update daily true range, N and channel
   histories without allowing an unfinished session into its own levels.
3. Publish weekly N/unit-size snapshots before the first session of each week.
   Entry and exit rules expose standing triggers based on known history.
4. Execute triggers through the campaign manager, reserving cash/unit capacity,
   tracking actual fills and separate lot stops, and applying portfolio limits.
5. Mark equity, accrue optional borrowing costs, and export versioned results
   with resolved configuration, source-specification/data hashes and assumptions.

## Module boundaries

- `data`, `coverage`, `alpaca`: local dataset validation, exchange-calendar
  coverage, explicit remote acquisition and provenance.
- `domain`, `config`: typed data/configuration, strict schema validation and
  configuration-file/command-line resolution.
- `strategy`, `indicators`, `chandelier`: independent entry/exit rules,
  prospective channels, streaming indicators and hypothetical System 1 outcomes.
- `backtest`, `risk`, `portfolio`, `execution`, `sessions`: event ordering,
  campaign/unit lifecycle, exposure limits, fills, cash, positions and sessions.
- `reporting`, `persistence`: versioned machine-readable results and local storage.
- `cli`, `sweep`: commands, deterministic experiments, isolated outputs and resume.

Registered entry/exit factories implement the typed rule protocols. Alternate
rules do not replace shared mandatory protective stops, unit sizing or portfolio
accounting. A selected exit rule does not alter System 1 hypothetical outcomes.

## Time and execution

Standing channel levels are available before the candle that can trigger them.
An opening gap executes at its opening price, with adverse slippage and costs.
Intrabar fills are candle-bounded simulations, not exact observed timestamps.
Completed observations can publish only subsequent levels.

When OHLC alone leaves sequence ambiguous, synchronized high-first and low-first
batch paths start from identical state, including orders, unit stops,
reservations and hypothetical campaigns. A shared normalized segment clock
orders crossings across symbols. At each shared timestamp all exposure
reductions precede increases; stable symbol ordering breaks ties. Only the lower
aggregate closing-equity outcome is committed, high-first on ties. Participation
uses observed candle volume as a scenario assumption.

Campaign N and intended shares freeze at initial entry. Each lot retains its own
fill and protective stop. Pending and partial units consume capacity; additions
wait for the preceding unit to finish. Exposure reductions precede increases.
Stops and partial fills can leave exposure pending and cannot cap gap losses.
Drawdown reductions immediately cap new-campaign sizing without resizing existing
campaigns. At the geometric loss-threshold limit, sizing capital is set to zero
instead of iterating indefinitely.

## Experiments and compatibility

Each run stores resolved settings and a reproducible fingerprint. Sweeps expand
validated parameter combinations in stable order and isolate state, databases and
artifact directories per combination. Workers share input read-only; they never
share a portfolio or SQLite writer. Atomic statuses support interrupted-run
recovery. Invalid combinations fail before scheduling; execution failures remain
visible in aggregate results.

Configuration/report version 2 is a clean break. Existing files are not silently
reinterpreted or migrated. Rule-specification hashing identifies the packaged
implementation conventions; it is not certification of historical performance.
