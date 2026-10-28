"""Discover and download the available AAPL SIP minute history; never trades."""

import argparse
import json
import os
import shlex
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from turtle_trader.alpaca import ENDPOINT, DownloadRequest, download_alpaca
from turtle_trader.cli import _alpaca_client
from turtle_trader.data import ExchangeCalendar

ROOT = Path(__file__).resolve().parents[1]
KEYS = ("APCA_API_KEY_ID", "APCA_API_SECRET_KEY")
NY = ZoneInfo("America/New_York")


def load_credentials(path: Path) -> None:
    """Read only Alpaca assignments; never evaluate a dotenv file as shell code."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, raw = line.removeprefix("export ").partition("=")
        key = key.strip()
        if separator and key in KEYS and not os.environ.get(key):
            try:
                values = shlex.split(raw, comments=True)
            except ValueError:
                raise ValueError("invalid Alpaca dotenv assignment") from None
            if len(values) != 1:
                raise ValueError("invalid Alpaca dotenv assignment")
            os.environ[key] = values[0]


def discover_start(client: httpx.Client, end: date) -> tuple[date, dict[str, object]]:
    """Find the first actual regular-session observation, without guessing a cutoff."""
    params = {"symbols": "AAPL", "timeframe": "1Min", "start": "1990-01-01T00:00:00Z",
              "end": (end + timedelta(days=1)).isoformat() + "T00:00:00Z", "limit": "10000",
              "sort": "asc", "adjustment": "all", "feed": "sip", "asof": end.isoformat()}
    seen: set[str] = set()
    first_provider: str | None = None
    pages = 0
    while True:
        response = client.get(ENDPOINT, params=params)
        if response.status_code != 200:
            raise ValueError(f"historical discovery HTTP {response.status_code}")
        payload = response.json()
        rows = payload.get("bars", {}).get("AAPL", [])
        pages += 1
        if rows:
            first_provider = first_provider or rows[0]["t"]
            first = datetime.fromisoformat(rows[0]["t"].replace("Z", "+00:00"))
            last = datetime.fromisoformat(rows[-1]["t"].replace("Z", "+00:00"))
            calendar = ExchangeCalendar(first.date() - timedelta(days=2), last.date())
            for row in rows:
                at = datetime.fromisoformat(row["t"].replace("Z", "+00:00"))
                bounds = calendar.bounds(at.astimezone(NY).date(), "regular")
                if bounds and bounds[0] <= at and at + timedelta(minutes=1) <= bounds[1]:
                    return at.astimezone(NY).date(), {
                        "symbol": "AAPL", "feed": "sip", "interval_minutes": 1,
                        "probe_start": params["start"], "probe_end": params["end"],
                        "first_provider_timestamp": first_provider,
                        "first_regular_timestamp": at.isoformat(), "pages_inspected": pages,
                        "endpoint": ENDPOINT,
                    }
        token = payload.get("next_page_token")
        if not isinstance(token, str) or not token or token in seen:
            raise ValueError("no first regular AAPL observation found")
        seen.add(token)
        params["page_token"] = token


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--end", type=date.fromisoformat,
                        default=datetime.now(UTC).astimezone(NY).date() - timedelta(days=1))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    load_credentials(args.env_file)
    with _alpaca_client() as client:
        start, discovery = discover_start(client, args.end)
        output = args.output or ROOT / "runs" / f"aapl-1m-{start:%Y%m%d}-{args.end:%Y%m%d}"
        print(f"Downloading AAPL 1-minute SIP regular sessions: {start} through {args.end}",
              flush=True)
        result = download_alpaca(DownloadRequest(("AAPL",), start, args.end, 1),
                                 output, client=client)
    if result.exit_code in (0, 3):
        (output / "discovery.json").write_text(json.dumps(discovery, indent=2) + "\n",
                                               encoding="utf-8")
    print(json.dumps(asdict(result), default=str), flush=True)
    raise SystemExit(result.exit_code)


if __name__ == "__main__":
    main()
