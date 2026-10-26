# Chandelier exit

Chandelier is an alternate exit selected independently of the entry rule. Use
`configs/system2-chandelier.toml` or command-line overrides:

```sh
uv run turtle-trader backtest bars.csv --manifest manifest.toml \
  --set 'strategy.exit_strategy="chandelier"' \
  --set 'strategy.chandelier_multiple=3.0' --output-dir runs/chandelier
```

## Formula and settings

Long line: rolling highest high minus `multiple × Wilder ATR`.
Short line: rolling lowest low plus `multiple × Wilder ATR`.

Defaults are completed regular-session windows of 22 for high/low and ATR, a
multiple of 3, and a ratchet. History before entry contributes to rolling windows.
`chandelier_timeframe="intraday"` instead samples completed input candles.
`chandelier_ratchet=false` permits the raw line to move in either direction.

The ratchet permits a long line only to rise and a short line only to fall during
a campaign. The independent mandatory per-unit protective stops remain active;
whichever active exit is first reached closes the relevant exposure. Chandelier
replaces the selected channel exit, not entry selection, N, pyramiding or exposure
limits. Changing exits marks the run as a research variant.

## Causality

A candle can execute against its incoming active levels. Its completed high, low
and range can update only subsequent levels; no fill at a newly calculated line
is invented earlier in that same candle. A subsequently active level already
crossed at the next opening executes as a gap, with adverse costs.

Entry/stop collisions retain the engine's two-path ambiguity policy. Partial
fills, volume limits and gaps can leave open exposure. No exit guarantees a
maximum loss.

System 1's hypothetical winner filter continues to use its original 2N protective
stop and 10-session channel exit even when actual holdings use Chandelier.

Compare alternatives against the same dataset, execution costs and test window;
the [sample grid](../configs/parameter-grid.toml) shows deterministic sweep syntax.
Selecting whichever exit won one historical sample is not out-of-sample validation.
