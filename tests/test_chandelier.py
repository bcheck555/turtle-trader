import pytest

from turtle_trader.chandelier import ChandelierExit


def test_hand_calculated_high_window_and_independent_wilder_atr() -> None:
    indicator = ChandelierExit(length=2, atr_length=3, multiple=2)
    assert indicator.update(10, 8, 9) is None  # First TR = 2, no prior close.
    assert indicator.update(13, 10, 12) is None  # Gap TR = 4.
    assert indicator.update(12, 11, 11.5) == pytest.approx(13 - 2 * (7 / 3))
    assert indicator.atr == pytest.approx(7 / 3)
    # The old 13 high expires; gap down from previous close gives TR = 1.5.
    assert indicator.update(11, 10, 10.5) == pytest.approx(12 - 2 * (37 / 18))
    assert indicator.atr == pytest.approx(37 / 18)
    assert list(indicator.highs) == [12, 11]


def test_high_window_and_atr_window_both_required() -> None:
    for length, atr_length in ((4, 2), (2, 4)):
        indicator = ChandelierExit(length, atr_length, 3)
        for _ in range(3):
            assert indicator.update(10, 8, 9) is None
        assert indicator.update(10, 8, 9) == 4


def test_raw_indicator_can_fall_and_is_not_trade_ratcheted() -> None:
    indicator = ChandelierExit(2, 2, 1)
    indicator.update(10, 8, 9)
    assert indicator.update(10, 8, 9) == 8
    assert indicator.update(20, 9, 19) == 13.5
    assert indicator.update(19, 17, 18) == 15.75
    assert indicator.update(18, 16, 17) == 15.875
    assert indicator.update(17, 16, 16.5) == 15.9375
    assert indicator.update(16.8, 16, 16.2) == pytest.approx(15.56875)


@pytest.mark.parametrize(("length", "atr_length", "multiple"), [
    (1, 2, 3), (2, 401, 3), (True, 2, 3), (2, 2, 0),
    (2, 2, -1), (2, 2, float("inf")), (2, 2, float("nan")),
])
def test_invalid_indicator_parameters(length: int, atr_length: int, multiple: float) -> None:
    with pytest.raises(ValueError):
        ChandelierExit(length, atr_length, multiple)
