"""Independent arithmetic fixtures for completed-bar Squeeze calculations."""

from collections import deque
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from math import inf, nan

import pytest

from turtle_trader.domain import Bar, StrategyConfig
from turtle_trader.squeeze import SqueezeIndicator, WilderADX, linear_regression_endpoint


def config(**changes: object) -> StrategyConfig:
    defaults = StrategyConfig(
        entry_strategy="squeeze_momentum", exit_strategy="atr_bracket",
        squeeze_bb_length=3, squeeze_kc_length=3, squeeze_momentum_length=3,
        squeeze_atr_length=2, squeeze_wma_length=3,
        squeeze_use_adx=False, squeeze_use_wma=False,
    )
    return replace(defaults, **changes)


def bar(index: int, close: float = 100, width: float = 1) -> Bar:
    at = datetime(2026, 1, 5, 14, 30, tzinfo=UTC) + timedelta(minutes=30 * index)
    return Bar("QQQ", at, at + timedelta(minutes=30), close, close + width,
               close - width, close, 10000)


def test_regression_endpoint_is_ols_not_a_moving_average() -> None:
    assert linear_regression_endpoint(deque([1.0, 4.0, 2.0])) == pytest.approx(17 / 6)
    assert linear_regression_endpoint(deque([3.0, 5.0, 7.0])) == pytest.approx(7)


def test_wma_weights_newest_highest_and_momentum_uses_range_midpoint() -> None:
    indicator = SqueezeIndicator(config())
    for i in range(5):
        indicator.update(bar(i, i + 1, 0.5))
    # Last closes 3,4,5; WMA=(3+8+15)/6. Detrended closes are each 1.
    assert indicator.wma == pytest.approx(26 / 6)
    assert indicator.momentum == pytest.approx(1)
    # ATR seeds mean(1,1.5), then Wilder updates with 1.5 three times.
    assert indicator.atr == pytest.approx(1.46875)


def test_wilder_adx_two_by_two_hand_calculation() -> None:
    adx = WilderADX(2, 2)
    assert adx.update(10, 8, 9) is None
    assert adx.update(12, 10, 11) is None
    assert adx.update(11, 9, 10) is None
    # DXs are 100*(1-.5)/(1+.5), then 100*(2-.25)/(2+.25).
    assert adx.update(14, 12, 13) == pytest.approx(500 / 9)


def test_default_adx_warmup_and_flat_ranges() -> None:
    adx = WilderADX(14, 14)
    for _ in range(27):
        assert adx.update(100, 100, 100) is None
    assert adx.update(100, 100, 100) == 0
    assert adx.update(100, 100, 100) == 0


@pytest.mark.parametrize("direction", [1, -1])
def test_first_release_publishes_direction_and_signal_bar_atr(direction: int) -> None:
    indicator = SqueezeIndicator(config())
    for i in range(6):
        assert indicator.update(bar(i)) is None
    assert indicator.squeeze_on
    signal_bar = bar(6, 100 + 10 * direction)
    signal = indicator.update(signal_bar)
    assert indicator.squeeze_off
    assert signal is not None
    assert signal.direction == direction
    assert signal.known_at == signal_bar.time_close
    # TR gap is 11; prior Wilder ATR=2, so next ATR=(2+11)/2=6.5.
    assert signal.atr == pytest.approx(6.5)
    assert indicator.update(bar(7, 100 + 10 * direction)) is None


def test_population_deviation_and_strict_band_equality() -> None:
    # Widths chosen to make BB and KC exactly equal on a flat series.
    indicator = SqueezeIndicator(config(squeeze_bb_length=2, squeeze_kc_length=2))
    indicator.update(bar(0, 100, 0))
    indicator.update(bar(1, 100, 0))
    assert indicator.state == "neutral"
    # Close [100,102] population std=1; BB width=2, KC SMA TR=1 -> 1.5.
    indicator.update(bar(2, 102, 0))
    assert indicator.squeeze_off


def test_disabled_filters_do_not_force_wma_or_adx_warmup() -> None:
    indicator = SqueezeIndicator(config(squeeze_wma_length=120))
    for i in range(6):
        indicator.update(bar(i))
    assert indicator.adx is None
    assert indicator.wma is None
    assert indicator.update(bar(6, 110)) is not None


@pytest.mark.parametrize("filter_name", ["squeeze_use_adx", "squeeze_use_wma"])
def test_enabled_unready_filter_blocks_release(filter_name: str) -> None:
    indicator = SqueezeIndicator(config(**{filter_name: True, "squeeze_wma_length": 120}))
    for i in range(6):
        indicator.update(bar(i))
    assert indicator.update(bar(6, 110)) is None


@pytest.mark.parametrize("adx_value,allowed", [(20.0, False), (20.0001, True)])
def test_adx_threshold_is_strict(monkeypatch: pytest.MonkeyPatch,
                                 adx_value: float, allowed: bool) -> None:
    indicator = SqueezeIndicator(config(squeeze_use_adx=True))
    monkeypatch.setattr(indicator._adx, "update", lambda *_: adx_value)
    for i in range(6):
        indicator.update(bar(i))
    assert (indicator.update(bar(6, 110)) is not None) == allowed
    with pytest.raises(ValueError, match="threshold"):
        config(squeeze_adx_threshold=-1)


def test_wma_equality_blocks_release() -> None:
    indicator = SqueezeIndicator(config(squeeze_use_wma=True, squeeze_wma_length=2))
    for i in range(6):
        indicator.update(bar(i))
    # Isolate the confirmation boundary: newest completed price equals baseline.
    indicator._wma = deque([110.0], maxlen=2)
    assert indicator.update(bar(6, 110)) is None
    assert indicator.wma == 110


def test_future_bars_cannot_change_past_signal_values() -> None:
    indicator = SqueezeIndicator(config())
    prefix = [bar(i) for i in range(6)] + [bar(6, 110)]
    results = [indicator.update(item) for item in prefix]
    signal = results[-1]
    assert signal is not None
    captured = signal.direction, signal.known_at, signal.atr
    for i in range(7, 20):
        indicator.update(bar(i, 1000))
    assert (signal.direction, signal.known_at, signal.atr) == captured
    replay = SqueezeIndicator(config())
    assert [replay.update(item) for item in prefix] == results


@pytest.mark.parametrize("changes", [
    {"squeeze_bb_length": 1}, {"squeeze_adx_smoothing": True},
    {"squeeze_wma_length": 10001}, {"squeeze_bb_multiple": 0},
    {"squeeze_kc_multiple": nan}, {"squeeze_target_multiple": inf},
    {"squeeze_adx_threshold": nan}, {"squeeze_use_adx": 1},
    {"squeeze_use_wma": "true"}, {"squeeze_flip_on_opposite": 0},
])
def test_squeeze_configuration_rejects_invalid_values(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        config(**changes)


def test_squeeze_requires_bracket_pair_and_has_explicit_channel_defaults() -> None:
    selected = StrategyConfig(entry_strategy="squeeze_momentum", exit_strategy="atr_bracket")
    assert selected.entry_length == selected.exit_length == 20
    with pytest.raises(ValueError, match="atr_bracket"):
        StrategyConfig(entry_strategy="squeeze_momentum")
    with pytest.raises(ValueError, match="atr_bracket"):
        StrategyConfig(exit_strategy="atr_bracket")
