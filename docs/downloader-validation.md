# Alpaca downloader validation

## Automated checks

- Full suite: **232 passed** on Python 3.12 and Python 3.13.
- Coverage on Python 3.13: **93% overall**, 94% downloader, 100% shared coverage
  and research-label helpers.
- Ruff and strict mypy: passed for all source modules.
- Source distribution and wheel: built successfully.
- Isolated wheel smoke test: `download alpaca --help` exposes the expected options
  without accessing credentials or a data service.

All HTTP behavior uses `httpx.MockTransport`. An autouse fixture rejects real
socket connections. Credential tests replace the entire environment mapping with
synthetic values; no fixture preserves host credential entries. No live Alpaca
request or brokerage connection was used for validation. The calendar dependency
emits pandas deprecation warnings; checks still pass.

## Independent review

GPT-6.1 Sol High agents implemented downloader and coverage in separate assignments.
GPT-6 Luna Medium implemented research-label propagation. Independent GPT-6.1 Sol
High agents reviewed acquisition integrity/security and coverage/research labels;
the coordinator integrated CLI and documentation.

Review findings resolved with regression tests:

- Saved-run config-only research labels were lost by CSV/CLI report regeneration.
  Exporters now resolve metadata before unwrapping; conflicting labels fail.
- Decimal context rounding and float conversion could conceal exact OHLC errors.
  Canonical export avoids context rounding and validates exact Decimal bounds.
- Tiny positive exponents could expand into huge strings before float underflow
  was rejected. Underflow is rejected before formatting.
- Credential fixtures could preserve host values through environment helpers.
  Tests now use isolated synthetic mappings.

Additional regressions cover short/empty/multi-symbol pages, absent terminal
tokens, token loops, duplicates/conflicts, bounded retries and elapsed-time
HTTP-date retry handling, DST/holidays/early closes, incomplete/absent coverage,
publication failure, and research labels through SQLite and JSON/CSV exports.

## Remaining boundaries

Vendor API behavior has not been live-tested. Corporate-action discovery, dividend
cash and split/share transformations are not implemented. Adjusted datasets are
explicitly research-only; strict backtesting still rejects unclassified gaps.

Publication reserves an empty destination immediately before the directory rename.
An abrupt process death can leave that empty reservation; it is never treated as
a completed dataset and later commands refuse to overwrite it. Interrupted staging
directories likewise contain no published successful dataset.
