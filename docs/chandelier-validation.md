# Chandelier validation

## Checks

- Full pytest suite: **305 passed** on Python 3.12 and Python 3.13.
- Python 3.13 coverage: **93% overall**, 100% strategy state-machine coverage.
- Ruff and strict mypy: passed for all source modules.
- Source distribution and wheel: built successfully.
- CLI tests exercise both Chandelier timeframes, raw/ratcheted selection, replay,
  backtest, walk-forward, SQLite persistence and JSON/CSV regeneration.
- Existing Revelio strategy fixtures pass; source reference bytes are unchanged.

Calendar dependency pandas deprecation warnings remain visible and did not fail
checks. Tests block live socket connections and use synthetic data/mock transports.
No brokerage connection or live data request was made for this milestone.

## Independent review

Initial implementation assignments used GPT-6.1 Sol High for strategy and execution
and GPT-6 Luna Medium for report metadata. Those workers saved changes but stopped
on a usage limit before final handoffs; the coordinator completed integration and
validation. The requested Sol reviewer was at capacity. A GPT-6 Astra High reviewer
then independently reviewed strategy, indicator, execution and metadata behavior.

The independent reviewer ran 135 focused/existing tests and found no actionable
defects. Reviewed cases include incoming versus proposed stop timing, independent
ATR streams, session first discard/publication delay, raw/ratcheted behavior,
scheduled-flat state, next-open close-breach exits, partial entry cancellation,
fixed initial risk anchor and stored-run identity conflicts.

## Integration corrections

The new multi-session execution test exposed a pre-existing swing-mode problem:
the backtester honored a strategy deadline even when daily liquidation was disabled.
Deadline registration now requires `day_trading=true`. A regression checks both
enabled daily closing and overnight swing holding with Revelio; Chandelier's
next-session causal test also covers the corrected behavior.

Two unfinished test fixtures were corrected to isolate their intended cases:
one gap-open candle had already crossed its fixed initial stop, and another case
accidentally created a second legitimate breakout/close-breach round trip.

## Boundaries

Chandelier is a derived exit, not exact Revelio Pine behavior. Raw lines may fall;
ratchets apply only to the underlying signal's lifetime. Adjusted research prices
retain their existing warnings and are not actual-account or point-in-time claims.
No live protection acceptance, trading improvement, or external platform parity
has been established. Per-equity strategy profiles remain a future feature.
