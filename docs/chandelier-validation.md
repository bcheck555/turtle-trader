# Chandelier validation scenarios

Chandelier uses the same campaign, execution and portfolio machinery as channel
exits. Verify the following cases against hand-calculated levels and ordered fills:

- Highest-high/lowest-low windows and Wilder ATR initialization/update.
- Independent lookback lengths, multiple, session/intraday sampling and warmup.
- Long ratchets rise only; short ratchets fall only. Raw lines may move either way.
- Mandatory unit stops remain active when the alternate exit is selected.
- Current-candle highs/ranges update only subsequent execution levels.
- Gaps through a previously published line fill at the opening price plus costs.
- A newly published line beyond the close cannot cause a retrospective fill.
- Input candle ambiguity, partial exits and fee/slippage symmetry.
- System 1 hypothetical original-rule outcomes remain independent of Chandelier.
- Standalone and sweep results agree for identical resolved settings and data.

Run `uv run pytest`, `uv run ruff check src tests`, and `uv run mypy` for integration
validation. Actual results belong in the implementation's validation report; these
scenarios are requirements, not claims of already passing tests.
