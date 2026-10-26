# Validation

Version 2 verification targets the [packaged rule specification](../src/turtle_trader/rule_spec.md)
and deterministic equity conventions. Historical comparisons from earlier
implementations do not establish these rules' correctness or expected returns.

Run the complete checks from the repository root:

```sh
uv run pytest
uv run ruff check src tests
uv run mypy
uv build
```

Release validation also installs the built wheel into an isolated environment,
checks that the rule specification is packaged, and exercises CLI help, resolved
configuration, demo replay/backtest and a small parameter sweep. Record actual
command results with the implementation commit; this document does not claim a
particular checkout passed them.

## Rule and execution coverage

- Hand-calculated true range/N, initialization, weekly publication and frozen
  campaign inputs.
- Prior-session channels, tick boundaries, opening gaps, System 1 filtering and
  failsafe, and System 2 entry/exit behavior.
- Hypothetical short breakouts with actual shorts disabled; hypothetical outcomes
  independent of cash rejection and selected actual exits.
- Whole-share sizing, actual-fill addition spacing, individual lot-stop movement,
  drawdown reductions, annual reset and 4/6/10/12 unit limits.
- Pending reservations, partial fills, cash rejection, deterministic simultaneous
  symbols and conservative OHLC path selection.
- Regular sessions, early closes, daylight saving, incomplete data, warmup and
  final open positions.
- Mirrored optional shorts, collateral release, calendar-day borrow fees, forced
  partial covers and negative-cash behavior.
- Alternate-exit causality, including [Chandelier scenarios](chandelier-validation.md).

## Experiment and compatibility coverage

- Configuration precedence, TOML literal types, duplicates, unknown/obsolete keys
  and canonical-deviation reporting.
- Equivalent standalone and sweep runs; stable grid expansion and results across
  worker counts, combination limits, preflight validation and failure aggregation.
- Output isolation, atomic statuses, collision refusal, interrupted-run resume,
  completed-artifact identity and failed-run retries.
- CLI JSON output/errors, export/persistence round trips and refusal of legacy
  formats without modifying existing databases.
- Absence of superseded implementation artifacts from maintained files and the
  installed package.

Acquisition tests mock network access; they do not demonstrate current provider
entitlement or live data quality. See [downloader validation](downloader-validation.md).
