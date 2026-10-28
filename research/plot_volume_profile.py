# /// script
# requires-python = ">=3.12"
# dependencies = ["matplotlib>=3.9,<4"]
# ///
"""Render the saved experiment; no source download or strategy calculation."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter


def render(results_path: Path, output_path: Path) -> None:
    result = json.loads(results_path.read_text())
    trades = result["baseTrades"]
    example = result["example"]
    if not trades or example is None:
        raise ValueError("No example trade available")
    trade = example["trade"]
    profile = example["previousProfile"]
    bars = example["sessionBars"]
    eastern = ZoneInfo("America/New_York")

    def local_time(milliseconds: int) -> datetime:
        return datetime.fromtimestamp(milliseconds / 1000, eastern)

    plt.rcParams.update(
        {
            "font.size": 10,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "svg.fonttype": "none",
        }
    )
    figure = plt.figure(figsize=(13, 10), layout="constrained")
    grid = figure.add_gridspec(2, 2, height_ratios=[1, 1.2], width_ratios=[1, 3])
    equity_ax = figure.add_subplot(grid[0, :])
    profile_ax = figure.add_subplot(grid[1, 0])
    price_ax = figure.add_subplot(grid[1, 1], sharey=profile_ax)

    for cost in result["costsBpsPerSide"]:
        equity = 1.0
        dates = [datetime.fromisoformat(result["source"]["start"])]
        values = [equity]
        for item in trades:
            equity *= 1 + item["costResults"][str(cost)]["net"]
            dates.append(datetime.fromisoformat(item["date"]))
            values.append(equity)
        equity_ax.plot(dates, values, label=f"{cost} bps / side", linewidth=1.5)
    equity_ax.axvline(
        datetime.fromisoformat(result["chronologicalSplit"]),
        color="#666666",
        linestyle=":",
        label="Chronological split",
    )
    equity_ax.axhline(1, color="#999999", linewidth=0.7)
    equity_ax.set_title("Base rule: compounded 1× notional, sampled after each trade", loc="left")
    equity_ax.set_ylabel("Starting capital multiple")
    equity_ax.grid(alpha=0.2)
    equity_ax.legend(ncols=3, fontsize=9)

    volumes = profile["volumes"]
    centers = [profile["low"] + (index + 0.5) * profile["width"] for index in range(len(volumes))]
    colors = [
        "#287f8e" if profile["val"] <= price <= profile["vah"] else "#c4d7dc" for price in centers
    ]
    profile_ax.barh(
        centers,
        [volume / profile["totalVolume"] for volume in volumes],
        height=profile["width"],
        color=colors,
        linewidth=0,
    )
    profile_ax.xaxis.set_major_formatter(PercentFormatter(1))
    profile_ax.set_xlabel("Share of session volume")
    profile_ax.set_ylabel("Adjusted QQQ price ($)")
    profile_ax.set_title(f"Previous session\n{example['previousSessionDate']}", loc="left")

    times = [local_time(bar["t"]) for bar in bars]
    # Open and close are distinct observations; wicks expose minute-level uncertainty.
    for moment, bar in zip(times, bars, strict=True):
        color = "#287f8e" if bar["c"] >= bar["o"] else "#b84956"
        price_ax.vlines(moment, bar["l"], bar["h"], color=color, linewidth=0.55)
    price_ax.plot(
        times,
        [bar["c"] for bar in bars],
        color="#263746",
        linewidth=0.6,
        alpha=0.65,
        label="Minute close",
    )
    price_ax.axhspan(profile["val"], profile["vah"], color="#287f8e", alpha=0.08)
    for level, label, color in [
        (trade["val"], "Value low", "#287f8e"),
        (trade["vah"], "Value high", "#287f8e"),
        (trade["stop"], "Stop", "#b84956"),
        (trade["target"], "Target", "#6a8c3a"),
    ]:
        price_ax.axhline(
            level, color=color, linestyle="--", linewidth=0.85, label=f"{label}: {level:.2f}"
        )
    price_ax.scatter(
        local_time(trade["entryTime"]),
        trade["entry"],
        marker="o",
        s=65,
        color="#3452a3",
        zorder=4,
        label="Next-open entry",
    )
    price_ax.scatter(
        local_time(trade["exitTime"]),
        trade["exit"],
        marker="X",
        s=65,
        color="#b84956",
        zorder=4,
        label=f"Exit: {trade['reason']}",
    )
    price_ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=eastern))
    price_ax.set_xlabel("New York time; exit marker identifies minute, not exact intraminute time")
    side = "Long" if trade["side"] == 1 else "Short"
    price_ax.set_title(
        f"First evaluation trade, without outcome selection: {trade['date']} · {side}", loc="left"
    )
    price_ax.legend(fontsize=8, ncols=2, loc="best")
    price_ax.grid(alpha=0.15)
    figure.suptitle("QQQ volume-profile re-entry · research approximation", fontsize=16)
    figure.supxlabel(
        "Uniform minute-range volume allocation; vendor-adjusted prices. "
        "Stops, targets and fills are modeled; no intraday equity drawdown shown.",
        fontsize=9,
    )
    figure.savefig(output_path, dpi=160)
    plt.close(figure)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    render(args.results, args.output)


if __name__ == "__main__":
    main()
