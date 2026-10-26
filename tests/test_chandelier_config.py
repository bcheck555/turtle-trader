"""Strict file/CLI configuration shares one validation path."""

from pathlib import Path

import pytest

from turtle_trader.config import config_from_mapping, deviations, load_config, resolved_config


def test_default_config_is_system_two_and_long_only() -> None:
    config = load_config()
    assert config.schema_version == 2
    assert config.strategy.entry_strategy == "turtle_s2"
    assert config.strategy.exit_strategy == "turtle_channel"
    assert not config.strategy.allow_short
    assert deviations(config) == []


def test_file_then_cli_precedence_and_system_defaults(tmp_path: Path) -> None:
    source = tmp_path / "settings.toml"
    source.write_text('schema_version=2\n[strategy]\nentry_strategy="turtle_s1"\n'
                      'atr_multiple=3.0\n[risk]\nstarting_cash="50000"\n')
    config = load_config(source, ["strategy.atr_multiple=2.5", "risk.starting_cash=75000"])
    assert (config.strategy.entry_length, config.strategy.exit_length) == (20, 10)
    assert config.strategy.atr_multiple == 2.5
    assert resolved_config(config)["risk"]["starting_cash"] == "75000"
    assert any("atr_multiple" in value for value in deviations(config))


def test_override_selects_system_before_resolving_defaults() -> None:
    config = load_config(overrides=['strategy.entry_strategy="turtle_s1"'])
    assert (config.strategy.entry_length, config.strategy.exit_length) == (20, 10)


def test_grid_values_override_cli_and_preserve_explicit_file_periods(tmp_path: Path) -> None:
    source = tmp_path / "settings.toml"
    source.write_text('schema_version=2\n[strategy]\nentry_length=30\n')
    config = load_config(source, ['strategy.entry_strategy="turtle_s2"'],
                         values={"strategy.entry_strategy": "turtle_s1"})
    assert (config.strategy.entry_length, config.strategy.exit_length) == (30, 10)
    config = load_config(values={"strategy.entry_strategy": "turtle_s1"})
    assert (config.strategy.entry_length, config.strategy.exit_length) == (20, 10)


@pytest.mark.parametrize("overrides", [
    ["strategy.atr_length=true"], ["strategy.entry_length=0"], ["strategy.allow_short=1"],
    ["risk.unit_fraction=true"], ["strategy.day_trading=false"],
    ["strategy.entry_strategy=unknown"], ['strategy.entry_strategy="unknown"'],
    ["execution.market_hours=\"extended\""], ["strategy.atr_length=2", "strategy.atr_length=3"],
    ["strategy.atr_length=2\nother=3"], ["strategy.atr_length=nan"],
])
def test_invalid_overrides_fail_before_execution(overrides: list[str]) -> None:
    with pytest.raises(ValueError):
        load_config(overrides=overrides)


def test_old_config_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "old.toml"
    source.write_text("[strategy]\nentry_length=20\n")
    with pytest.raises(ValueError, match="schema_version"):
        load_config(source)


def test_short_requirements_and_nested_groups() -> None:
    with pytest.raises(ValueError, match="shorts require"):
        load_config(overrides=["strategy.allow_short=true"])
    config = load_config(overrides=["strategy.allow_short=true",
                                    'execution.shortable_symbols=["AAA"]',
                                    "execution.borrow_rate=0.03",
                                    'risk.close_groups=[["AAA", "BBB"]]'])
    assert config.execution.shortable_symbols == ("AAA",)
    assert config.risk.close_groups == (("AAA", "BBB"),)
    assert config_from_mapping(resolved_config(config)) == config


def test_chandelier_configuration_does_not_change_turtle_initial_stop() -> None:
    config = load_config(overrides=['strategy.exit_strategy="chandelier"',
                                    'strategy.chandelier_timeframe="intraday"',
                                    'strategy.chandelier_ratchet=false'])
    assert config.strategy.atr_multiple == 2.0
    assert config.strategy.chandelier_multiple == 3.0
    assert any("Chandelier" in value for value in deviations(config))
