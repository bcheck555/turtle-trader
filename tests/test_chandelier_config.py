from dataclasses import replace
from pathlib import Path

import pytest

from turtle_trader.config import load_config
from turtle_trader.domain import StrategyConfig


def test_default_exit_remains_revelio() -> None:
    config = load_config().strategy
    assert config.exit_strategy == "revelio"
    assert config.chandelier_timeframe == "session"
    assert config.chandelier_length == config.chandelier_atr_length == 22
    assert config.chandelier_multiple == 3.0
    assert config.chandelier_ratchet is True


@pytest.mark.parametrize("field,value", [
    ("exit_strategy", "unknown"),
    ("exit_strategy", True),
    ("chandelier_timeframe", "daily"),
    ("chandelier_length", 1),
    ("chandelier_length", 401),
    ("chandelier_length", True),
    ("chandelier_atr_length", 2.0),
    ("chandelier_multiple", 0),
    ("chandelier_multiple", -1),
    ("chandelier_multiple", float("nan")),
    ("chandelier_multiple", float("inf")),
    ("chandelier_multiple", True),
    ("chandelier_multiple", 10**1000),
    ("chandelier_multiple", "3"),
    ("chandelier_ratchet", "false"),
    ("chandelier_ratchet", 0),
])
def test_invalid_chandelier_settings_reject(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        replace(StrategyConfig(), **{field: value})


def test_toml_selects_intraday_raw_without_affecting_initial_atr(tmp_path: Path) -> None:
    config = tmp_path / "strategy.toml"
    config.write_text(
        '[strategy]\nexit_strategy="chandelier"\nchandelier_timeframe="intraday"\n'
        'chandelier_length=11\nchandelier_atr_length=7\nchandelier_multiple=2.5\n'
        'chandelier_ratchet=false\n', encoding="utf-8"
    )
    result = load_config(config).strategy
    assert result.exit_strategy == "chandelier"
    assert result.chandelier_timeframe == "intraday"
    assert result.chandelier_length == 11 and result.chandelier_atr_length == 7
    assert result.chandelier_multiple == 2.5 and result.chandelier_ratchet is False
    assert result.atr_length == 20 and result.atr_multiple == 2.0


def test_shipped_chandelier_preset() -> None:
    config = load_config(Path(__file__).parents[1] / "configs" / "chandelier-swing.toml")
    assert config.strategy.exit_strategy == "chandelier"
    assert not config.strategy.day_trading
    assert config.execution.market_hours == "regular"
