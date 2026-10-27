"""Trim a local archive, validate retained data, and publish new provenance.

Source data is never deleted by this utility. A caller may purge it separately
after verifying the published destination.
"""

import argparse
import csv
import hashlib
import json
import re
import shutil
import tempfile
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from .coverage import coverage_report
from .data import NY, load_dataset, read_manifest
from .halts import MARKET_HALTS, POLICY_VERSION, SOURCE_URL
from .reporting import jsonable


def _checksum(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(jsonable(value), indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def prepare_archive(source: Path, destination: Path, cutoff: date) -> dict[str, Any]:
    """Publish a verified derivative without modifying source or existing output."""
    source, destination = source.resolve(), destination.resolve()
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError("source and destination must be separate, non-nested directories")
    if destination.exists():
        raise FileExistsError("archive destination already exists")
    manifest = read_manifest(source / "manifest.toml")
    if not manifest.start_date < cutoff <= manifest.end_date:
        raise ValueError("cutoff must follow archive start and not exceed archive end")
    source_provenance = json.loads((source / "provenance.json").read_text(encoding="utf-8"))
    for name in ("bars.csv", "manifest.toml", "coverage.json"):
        actual = _checksum(source / name)
        if actual != source_provenance.get("artifact_checksums", {}).get(name):
            raise ValueError(f"source checksum mismatch: {name}")
    source_provenance_hash = _checksum(source / "provenance.json")
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{destination.name}.staging-",
                                 dir=destination.parent))
    try:
        retained = removed = 0
        with (source / "bars.csv").open(newline="", encoding="utf-8") as incoming, (
            stage / "bars.csv"
        ).open("w", newline="", encoding="utf-8") as outgoing:
            reader = csv.DictReader(incoming)
            columns = ["symbol", "timestamp", "open", "high", "low", "close", "volume"]
            if reader.fieldnames != columns:
                raise ValueError("unexpected source CSV columns")
            writer = csv.DictWriter(outgoing, fieldnames=columns, lineterminator="\n")
            writer.writeheader()
            for row in reader:
                timestamp = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
                if timestamp.utcoffset() is None:
                    raise ValueError("source timestamps must be timezone-aware")
                if timestamp.astimezone(NY).date() < cutoff:
                    removed += 1
                else:
                    writer.writerow(row)
                    retained += 1
        text, replacements = re.subn(
            r"(?m)^start_date\s*=.*$", f"start_date = {cutoff.isoformat()}",
            (source / "manifest.toml").read_text(encoding="utf-8"),
        )
        if replacements != 1:
            raise ValueError("source manifest must have exactly one start_date assignment")
        (stage / "manifest.toml").write_text(text, encoding="utf-8")
        dataset = load_dataset(stage / "bars.csv", stage / "manifest.toml")
        report = coverage_report(dataset.bars, dataset.manifest)
        if not report.complete or retained != len(dataset.bars):
            raise ValueError("retained archive failed coverage/count validation")
        _write_json(stage / "coverage.json", asdict(report) | {"complete": report.complete})
        checksums = {name: _checksum(stage / name)
                     for name in ("bars.csv", "manifest.toml", "coverage.json")}
        _write_json(stage / "provenance.json", {
            "schema_version": 1,
            "kind": "derived_archive",
            "status": "ready_for_adjusted_research",
            "derived_at": datetime.now(UTC),
            "source_acquisition": source_provenance,
            "source_provenance_sha256": source_provenance_hash,
            "transformation": {
                "operation": "discard_before_new_york_date",
                "cutoff": cutoff,
                "rows_removed": removed,
                "rows_retained": retained,
                "retained_price_volume_fields": "unchanged source strings",
                "fabricated_bars": 0,
            },
            "retained_start": dataset.bars[0].time,
            "retained_end": dataset.bars[-1].time,
            "data_hash": dataset.data_hash,
            "market_halt_policy": {
                "version": POLICY_VERSION,
                "source": SOURCE_URL,
                "intervals": [asdict(halt) for halt in MARKET_HALTS],
                "excluded_whole_bar_slots": report.halted_count,
                "opening_execution": "next observed price, no earlier than verified reopening",
            },
            "price_basis": manifest.price_basis,
            "warnings": dataset.warnings,
            "artifact_checksums": checksums,
        })
        checksums["provenance.json"] = _checksum(stage / "provenance.json")
        # Claim the destination exclusively before the atomic publication.
        destination.mkdir(exist_ok=False)
        try:
            stage.replace(destination)
        except OSError:
            destination.rmdir()
            raise
        return {"output": str(destination), "rows": retained, "removed": removed,
                "halted_count": report.halted_count, "data_hash": dataset.data_hash,
                "checksums": checksums}
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--cutoff", required=True, type=date.fromisoformat)
    args = parser.parse_args()
    print(json.dumps(prepare_archive(args.source, args.destination, args.cutoff), sort_keys=True))


if __name__ == "__main__":
    main()
