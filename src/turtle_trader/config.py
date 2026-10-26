"""Strict versioned configuration and typed command-line overrides."""

import json
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, fields
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from turtle_trader.domain import ExecutionConfig, RiskConfig, StrategyConfig

SCHEMA_VERSION = 2


@dataclass(frozen=True)
class AppConfig:
    strategy: StrategyConfig = StrategyConfig()
    risk: RiskConfig = RiskConfig()
    execution: ExecutionConfig = ExecutionConfig()
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != SCHEMA_VERSION:
            raise ValueError("configuration schema_version must be 2")
        if self.strategy.allow_short and (
            not self.execution.shortable_symbols or self.execution.borrow_rate is None
        ):
            raise ValueError("shorts require shortable_symbols and an explicit borrow_rate")


def _convert(value: object, default: object, name: str) -> object:
    if isinstance(default, Decimal) or name == "borrow_rate":
        if value is None and name == "borrow_rate":
            return None
        if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
            raise ValueError(f"{name} must be numeric")
        try:
            number = Decimal(str(value))
        except InvalidOperation as exc:
            raise ValueError(f"{name} must be numeric") from exc
        if not number.is_finite():
            raise ValueError(f"{name} must be finite")
        return number
    if type(default) is bool:
        if type(value) is not bool:
            raise ValueError(f"{name} must be boolean")
    elif type(default) is int:
        if type(value) is not int:
            raise ValueError(f"{name} must be integer")
        if name.endswith("_length") and value < 2:
            raise ValueError(f"{name} must be at least two")
    elif type(default) is float:
        if type(value) not in (int, float):
            raise ValueError(f"{name} must be numeric")
    elif isinstance(default, str):
        if not isinstance(value, str):
            raise ValueError(f"{name} must be text")
    elif isinstance(default, tuple):
        if not isinstance(value, (list, tuple)):
            raise ValueError(f"{name} must be an array")
        if name in ("close_groups", "loose_groups"):
            if any(not isinstance(g, (list, tuple)) for g in value):
                raise ValueError(f"{name} must be arrays of symbol arrays")
            return tuple(tuple(g) for g in value)
        return tuple(value)
    return value


def config_from_mapping(raw: Mapping[str, object]) -> AppConfig:
    """Build one configuration; missing version is an obsolete configuration."""
    if set(raw) - {"schema_version", "strategy", "risk", "execution"}:
        raise ValueError("unknown configuration tables")
    if type(raw.get("schema_version")) is not int or raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("configuration schema_version=2 is required; old configs are unsupported")

    def build(cls: type[Any], table: str) -> Any:
        values = raw.get(table, {})
        if not isinstance(values, Mapping):
            raise ValueError(f"{table} must be a table")
        defaults = cls()
        known = {f.name for f in fields(defaults)}
        if set(values) - known:
            raise ValueError(f"unknown {table} settings: {', '.join(sorted(set(values) - known))}")
        converted = {key: _convert(value, getattr(defaults, key), key)
                     for key, value in values.items()}
        return cls(**converted)

    result = AppConfig(build(StrategyConfig, "strategy"), build(RiskConfig, "risk"),
                       build(ExecutionConfig, "execution"))
    from turtle_trader.strategy import ENTRY_RULES, EXIT_RULES
    if result.strategy.entry_strategy not in ENTRY_RULES:
        raise ValueError(f"unknown entry_strategy: {result.strategy.entry_strategy}")
    if result.strategy.exit_strategy not in EXIT_RULES:
        raise ValueError(f"unknown exit_strategy: {result.strategy.exit_strategy}")
    return result


def load_config(path: Path | None = None, overrides: Sequence[str] = (), *,
                values: Mapping[str, object] | None = None) -> AppConfig:
    raw: dict[str, Any] = {"schema_version": SCHEMA_VERSION}
    if path is not None:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
        if raw.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("configuration schema_version=2 is required")
    assigned: set[str] = set()
    for assignment in overrides:
        key, separator, text = assignment.partition("=")
        if not separator or key.count(".") != 1:
            raise ValueError("overrides must be section.key=TOML_VALUE")
        if key in assigned:
            raise ValueError(f"duplicate override: {key}")
        assigned.add(key)
        section, name = key.split(".")
        if section not in ("strategy", "risk", "execution") or not name.isidentifier():
            raise ValueError(f"unknown override: {key}")
        try:
            parsed = tomllib.loads(f"value = {text}")
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(f"invalid TOML override for {key}") from exc
        if set(parsed) != {"value"}:
            raise ValueError(f"invalid TOML override for {key}")
        table = raw.setdefault(section, {})
        if not isinstance(table, dict):
            raise ValueError(f"{section} must be a table")
        table[name] = parsed["value"]
    for key, value in (values or {}).items():
        if key.count(".") != 1:
            raise ValueError("grid parameter paths must be section.key")
        section, name = key.split(".")
        if section not in ("strategy", "risk", "execution") or not name.isidentifier():
            raise ValueError(f"unknown grid parameter: {key}")
        table = raw.setdefault(section, {})
        if not isinstance(table, dict):
            raise ValueError(f"{section} must be a table")
        table[name] = value
    return config_from_mapping(raw)


def resolved_config(config: AppConfig) -> dict[str, Any]:
    """Return JSON-compatible settings, including resolved system defaults."""
    return dict(json.loads(json.dumps(asdict(config), default=str)))


def deviations(config: AppConfig) -> list[str]:
    """Describe parameter differences from the selected original-rule preset."""
    if config.strategy.entry_strategy not in ("turtle_s1", "turtle_s2"):
        return [f"alternate entry strategy: {config.strategy.entry_strategy}"]
    baseline = AppConfig(strategy=StrategyConfig(entry_strategy=config.strategy.entry_strategy))
    current = resolved_config(config)
    original = resolved_config(baseline)
    changes: list[str] = []
    for section in ("strategy", "risk"):
        for key, value in current[section].items():
            if (key in ("interval_minutes", "tick_size", "allow_short")
                    or key.startswith("chandelier_")):
                continue
            if value != original[section][key]:
                changes.append(f"{section}.{key}={value!r} (canonical {original[section][key]!r})")
    if config.strategy.exit_strategy == "chandelier":
        changes.append("Chandelier replaces the original channel exit")
    return changes
