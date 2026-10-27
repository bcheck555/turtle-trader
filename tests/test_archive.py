import hashlib
import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from turtle_trader.archive import prepare_archive
from turtle_trader.data import load_dataset, read_manifest

CUTOFF = date(2016, 2, 23)


def checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(directory: Path) -> dict[str, bytes]:
    return {file.name: file.read_bytes() for file in directory.iterdir() if file.is_file()}


def source_archive(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    rows = ["symbol,timestamp,open,high,low,close,volume"]
    for day in (22, 23):
        opening = datetime(2016, 2, day, 14, 30, tzinfo=UTC)
        for i in range(13):
            timestamp = (opening + timedelta(minutes=30 * i)).isoformat().replace("+00:00", "Z")
            # Deliberate trailing/leading zeroes catch float/int rewriting.
            rows.append(f"QQQ,{timestamp},100.0000,101.2500,99.7500,100.125000,00012345")
    (source / "bars.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    (source / "manifest.toml").write_text(
        '[data]\nfeed = "sip"\ninterval_minutes = 30\ncoverage = "regular"\n'
        'adjustment = "all"\naction_free = false\nprice_basis = "adjusted_research"\n'
        'start_date = 2016-02-22\nend_date = 2016-02-23\nsymbols = ["QQQ"]\n'
        'timestamp_convention = "bar_open"\nsynthetic = false\n',
        encoding="utf-8",
    )
    (source / "coverage.json").write_text('{"original_acquisition": true}\n', encoding="utf-8")
    refresh_source_checksums(source)
    return source


def refresh_source_checksums(source: Path) -> None:
    provenance = {
        "status": "coverage_incomplete",
        "artifact_checksums": {
            name: checksum(source / name)
            for name in ("bars.csv", "manifest.toml", "coverage.json")
        },
    }
    (source / "provenance.json").write_text(json.dumps(provenance), encoding="utf-8")


def test_prepared_archive_preserves_retained_values_and_source_with_verified_metadata(
    tmp_path: Path,
) -> None:
    source = source_archive(tmp_path)
    original = snapshot(source)
    destination = tmp_path / "derived"

    result = prepare_archive(source, destination, CUTOFF)

    assert snapshot(source) == original
    expected_rows = original["bars.csv"].decode().splitlines()
    assert (destination / "bars.csv").read_text().splitlines() == [
        expected_rows[0], *expected_rows[14:],
    ]
    assert result["rows"] == result["removed"] == 13
    assert result["halted_count"] == 0
    assert read_manifest(destination / "manifest.toml") == replace(
        read_manifest(source / "manifest.toml"), start_date=CUTOFF,
    )
    dataset = load_dataset(destination / "bars.csv", destination / "manifest.toml")
    assert len(dataset.bars) == 13
    assert result["data_hash"] == dataset.data_hash
    coverage = json.loads((destination / "coverage.json").read_text())
    assert coverage["complete"]
    assert coverage["expected_count"] == coverage["observed_count"] == 13
    assert coverage["missing_count"] == coverage["outside_count"] == 0
    provenance = json.loads((destination / "provenance.json").read_text())
    assert provenance["source_acquisition"] == json.loads(original["provenance.json"])
    assert provenance["source_provenance_sha256"] == hashlib.sha256(
        original["provenance.json"],
    ).hexdigest()
    assert provenance["transformation"]["cutoff"] == "2016-02-23"
    assert provenance["transformation"]["fabricated_bars"] == 0
    for name, digest in result["checksums"].items():
        assert checksum(destination / name) == digest
    for name, digest in provenance["artifact_checksums"].items():
        assert checksum(destination / name) == digest


def test_existing_archive_destination_is_never_overwritten(tmp_path: Path) -> None:
    source = source_archive(tmp_path)
    original = snapshot(source)
    destination = tmp_path / "derived"
    destination.mkdir()
    (destination / "keep.txt").write_text("existing archive", encoding="utf-8")

    with pytest.raises(FileExistsError, match="already exists"):
        prepare_archive(source, destination, CUTOFF)

    assert snapshot(destination) == {"keep.txt": b"existing archive"}
    assert snapshot(source) == original


@pytest.mark.parametrize("relationship", ["same", "child", "ancestor"])
def test_nested_archive_destinations_are_refused(tmp_path: Path, relationship: str) -> None:
    source = source_archive(tmp_path)
    original = snapshot(source)
    destination = {"same": source, "child": source / "derived", "ancestor": tmp_path}[relationship]

    with pytest.raises(ValueError, match="non-nested"):
        prepare_archive(source, destination, CUTOFF)

    assert snapshot(source) == original
    assert not (source / "derived").exists()


@pytest.mark.parametrize("name", ["bars.csv", "manifest.toml", "coverage.json"])
def test_checksum_corruption_is_refused_before_publication(tmp_path: Path, name: str) -> None:
    source = source_archive(tmp_path)
    (source / name).write_bytes((source / name).read_bytes() + b"\n")
    original = snapshot(source)
    destination = tmp_path / "derived"

    with pytest.raises(ValueError, match=f"source checksum mismatch: {name}"):
        prepare_archive(source, destination, CUTOFF)

    assert snapshot(source) == original
    assert not destination.exists()
    assert not list(tmp_path.glob(".derived.staging-*"))


def test_unexplained_retained_gap_leaves_source_and_destination_untouched(tmp_path: Path) -> None:
    source = source_archive(tmp_path)
    rows = (source / "bars.csv").read_text().splitlines()
    del rows[18]  # A tradable bar interval inside the retained February 23 session.
    (source / "bars.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    refresh_source_checksums(source)
    original = snapshot(source)
    destination = tmp_path / "derived"

    with pytest.raises(ValueError, match="1 missing candles"):
        prepare_archive(source, destination, CUTOFF)

    assert snapshot(source) == original
    assert not destination.exists()
    assert not list(tmp_path.glob(".derived.staging-*"))


def test_source_gap_before_cutoff_does_not_prevent_verified_derivative(tmp_path: Path) -> None:
    source = source_archive(tmp_path)
    rows = (source / "bars.csv").read_text().splitlines()
    del rows[5]  # An unresolved gap in the session explicitly being discarded.
    (source / "bars.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    refresh_source_checksums(source)
    original = snapshot(source)
    destination = tmp_path / "derived"

    result = prepare_archive(source, destination, CUTOFF)

    assert result["removed"] == 12
    assert result["rows"] == 13
    assert snapshot(source) == original
    assert len(load_dataset(destination / "bars.csv", destination / "manifest.toml").bars) == 13
