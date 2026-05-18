"""Unit tests for the P3.3 schema_version stamp in RotatingJsonlWriter.

The stamp is forward-defensive plumbing: every record gets a
``schema_version`` field as the first key of the serialised JSON object
unless the caller explicitly provided one. Downstream readers can branch
on it when a future producer bumps the on-disk format. Default version
is ``1`` for every feed that existed at the time of the P3.3 rollout.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.jsonl_writer import RotatingJsonlWriter


def _read_lines(p: Path) -> list[str]:
    return [
        line for line in p.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _read_records(p: Path) -> list[dict]:
    return [json.loads(line) for line in _read_lines(p)]


async def test_default_schema_version_is_one(tmp_path: Path) -> None:
    """Default constructor stamps every record with schema_version=1 \u2014
    the implicit baseline for every feed that existed at P3.3 rollout."""
    w = RotatingJsonlWriter(tmp_path / "feed.jsonl", tz="UTC")
    assert w.schema_version == 1
    await w.append({"event": "alpha"})
    records = _read_records(w.active_path())
    assert records == [{"schema_version": 1, "event": "alpha"}]


async def test_schema_version_is_first_field_on_disk(tmp_path: Path) -> None:
    """The stamp must appear as the FIRST key in the serialised line so
    operators tailing the feed see it immediately when they grep / read.
    This is contract: the field's position is part of the on-disk layout.
    """
    w = RotatingJsonlWriter(tmp_path / "feed.jsonl", tz="UTC")
    await w.append({"ts": "now", "event": "beta", "payload": {"x": 1}})
    line = _read_lines(w.active_path())[0]
    # JSON's object-key order in Python's json module follows insertion
    # order \u2014 schema_version was spliced first, so the serialised line
    # must start with the field.
    assert line.startswith('{"schema_version": 1,'), line


async def test_custom_schema_version(tmp_path: Path) -> None:
    """A feed that's already migrated past v1 stamps its configured version."""
    w = RotatingJsonlWriter(tmp_path / "feed.jsonl", tz="UTC", schema_version=7)
    assert w.schema_version == 7
    await w.append({"event": "gamma"})
    records = _read_records(w.active_path())
    assert records == [{"schema_version": 7, "event": "gamma"}]


async def test_caller_supplied_schema_version_wins(tmp_path: Path) -> None:
    """During a migration window the producer may want to emit records
    at a NEWER version than the writer's default \u2014 caller-set values
    must be preserved unchanged.
    """
    w = RotatingJsonlWriter(tmp_path / "feed.jsonl", tz="UTC", schema_version=1)
    await w.append({"schema_version": 2, "event": "future", "new_field": True})
    records = _read_records(w.active_path())
    assert records == [
        {"schema_version": 2, "event": "future", "new_field": True}
    ]


async def test_append_many_stamps_each_record(tmp_path: Path) -> None:
    """The batched path must stamp every record, not just the first.
    Used by STM.flush_journal \u2014 a regression here would silently
    produce a mix of stamped and unstamped lines.
    """
    w = RotatingJsonlWriter(tmp_path / "feed.jsonl", tz="UTC", schema_version=3)
    n = await w.append_many([
        {"event": "a"},
        {"event": "b", "schema_version": 99},  # caller override mid-batch
        {"event": "c"},
    ])
    assert n == 3
    records = _read_records(w.active_path())
    assert records == [
        {"schema_version": 3, "event": "a"},
        {"schema_version": 99, "event": "b"},
        {"schema_version": 3, "event": "c"},
    ]


async def test_stamp_does_not_mutate_caller_dict(tmp_path: Path) -> None:
    """The writer must never mutate the caller's record dict \u2014 the caller
    may keep a reference and reuse it. Stamping is done on a fresh dict.
    """
    w = RotatingJsonlWriter(tmp_path / "feed.jsonl", tz="UTC")
    rec = {"event": "delta"}
    await w.append(rec)
    assert rec == {"event": "delta"}, (
        "writer leaked schema_version back into caller's dict"
    )


@pytest.mark.parametrize("version", [1, 2, 42])
async def test_schema_version_round_trips(tmp_path: Path, version: int) -> None:
    """Round-trip through write + read preserves the stamped integer."""
    w = RotatingJsonlWriter(
        tmp_path / "feed.jsonl", tz="UTC", schema_version=version,
    )
    await w.append({"event": "round-trip"})
    records = _read_records(w.active_path())
    assert records[0]["schema_version"] == version
    assert isinstance(records[0]["schema_version"], int)
