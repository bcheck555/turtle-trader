"""Strict TOML configuration: no live execution mode exists."""

import tomllib
from dataclasses import dataclass, fields
from decimal import Decimal
from pathlib import Path
from typing import Any

from turtle_trader.domain import ExecutionConfig, RiskConfig, StrategyConfig


@dataclass(frozen=True)
class AppConfig:
    strategy: StrategyConfig = StrategyConfig()
    risk: RiskConfig = RiskConfig()
    execution: ExecutionConfig = ExecutionConfig()


def load_config(path: Path | None = None) -> AppConfig:
    if path is None:
        return AppConfig()
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    if set(raw) - {"strategy", "risk", "execution"}:
        raise ValueError("unknown config tables; live trading is unsupported")

    def build(cls: type[Any], name: str) -> Any:
        values = raw.get(name, {})
        if not isinstance(values, dict):
            raise ValueError(f"{name} must be a table")
        defaults = cls()
        known = {f.name for f in fields(defaults)}
        if set(values) - known:
            raise ValueError(f"unknown {name} settings")
        for key, value in values.items():
            default = getattr(defaults, key)
            if isinstance(default, Decimal):
                if isinstance(value, bool):
                    raise ValueError(f"{key} must be numeric")
                values[key] = Decimal(str(value))
            elif type(default) is bool and type(value) is not bool:
                raise ValueError(f"{key} must be boolean")
            elif type(default) is int and type(value) is not int:
                raise ValueError(f"{key} must be integer")
            elif type(default) is float and (type(value) not in (int, float)):
                raise ValueError(f"{key} must be numeric")
        return cls(**values)

    return AppConfig(build(StrategyConfig, "strategy"), build(RiskConfig, "risk"),
                     build(ExecutionConfig, "execution"))
