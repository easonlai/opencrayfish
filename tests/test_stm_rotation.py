"""Unit tests for the P3.2 STM rotation swap.

These cover the behaviours that changed when ``ShortTermMemory`` started
delegating its on-disk concerns to ``RotatingJsonlWriter``:

  * ``flush_journal()`` lands records in the *active* dated sibling
    (``stm_journal-YYYY-MM-DD.jsonl``), not a flat single file.
  * ``recover()`` reads across every rotated sibling in chronological
    order so a crash + restart that straddles midnight rehydrates the
    full tail window, not just the current day.
  * ``purge()`` (Sleep Metabolism) unlinks every sibling instead of
    truncating a single file.
  * ``flush_journal(fsync=True)`` honours the per-call override even
    when the constructor default is ``False`` (used by ``shutdown()``).
  * Foreign files in the same directory are NEVER touched by purge.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from core.stm import ShortTermMemory


def _read_records(p: Path) -> list[dict]:
    """Parse a JSONL file into a list of dicts; skip blanks."""
    if not p.exists():
        return []
    return [
        json.loads(line)
        for line in p.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _seed_sibling(base: Path, day: date, turns: list[tuple[str, str]]) -> Path:
    """Write a synthetic rotated sibling for ``day`` containing ``turns``.

    Used to fake "yesterday's" journal contents without waiting for the
    clock to roll over. Sibling filename matches the
    ``<stem>-YYYY-MM-DD.jsonl`` pattern that ``RotatingJsonlWriter``
    discovers via ``sibling_paths()``.
    """
    sibling = base.parent / f"{base.stem}-{day.isoformat()}.jsonl"
    payload = "".join(
        json.dumps({"ts": f"{day.isoformat()}T12:00:00+00:00",
                    "role": r, "content": c}) + "\n"
        for r, c in turns
    )
    sibling.write_text(payload, encoding="utf-8")
    return sibling


@pytest.fixture
def journal_base(tmp_path: Path) -> Path:
    """Base path STM is configured with — the actual file written to is
    a dated sibling, never this path itself."""
    return tmp_path / "state" / "stm_journal.jsonl"


async def test_flush_writes_to_dated_sibling_not_base(journal_base: Path) -> None:
    """The configured base path must NEVER appear on disk — writes always
    go to the active dated sibling. This guards against regression to
    the pre-P3.2 flat-file behaviour.
    """
    stm = ShortTermMemory(
        max_turns=10, journal_path=journal_base, tz="UTC",
    )
    await stm.append("architect", "hello")
    await stm.append("agent", "hi")
    n = await stm.flush_journal()

    assert n == 2
    assert not journal_base.exists(), (
        "Base path must stay empty — writes go to dated siblings only."
    )
    active = stm.active_journal_path()
    assert active is not None
    assert active.exists()
    assert active.name.startswith("stm_journal-")
    assert active.name.endswith(".jsonl")

    records = _read_records(active)
    assert [r["role"] for r in records] == ["architect", "agent"]
    assert [r["content"] for r in records] == ["hello", "hi"]


async def test_recover_spans_multiple_siblings(journal_base: Path) -> None:
    """Boot replay must concatenate every rotated sibling oldest-first so
    a long uptime (days of rotation) rehydrates the full tail window."""
    journal_base.parent.mkdir(parents=True, exist_ok=True)
    today = date.today()
    _seed_sibling(journal_base, today - timedelta(days=2), [
        ("architect", "day-minus-2 a"),
        ("agent", "day-minus-2 b"),
    ])
    _seed_sibling(journal_base, today - timedelta(days=1), [
        ("architect", "day-minus-1 a"),
        ("agent", "day-minus-1 b"),
    ])
    _seed_sibling(journal_base, today, [
        ("architect", "today a"),
        ("agent", "today b"),
    ])

    stm = ShortTermMemory(
        max_turns=10, journal_path=journal_base, tz="UTC",
    )
    n = await stm.recover()
    assert n == 6
    turns = await stm.render()
    assert [t.content for t in turns] == [
        "day-minus-2 a", "day-minus-2 b",
        "day-minus-1 a", "day-minus-1 b",
        "today a", "today b",
    ]


async def test_recover_tail_loads_only_last_n(journal_base: Path) -> None:
    """``max_turns`` bound applies across siblings, not per-sibling."""
    journal_base.parent.mkdir(parents=True, exist_ok=True)
    today = date.today()
    _seed_sibling(journal_base, today - timedelta(days=1), [
        ("architect", "old-1"),
        ("agent", "old-2"),
        ("architect", "old-3"),
    ])
    _seed_sibling(journal_base, today, [
        ("architect", "new-1"),
        ("agent", "new-2"),
    ])

    stm = ShortTermMemory(
        max_turns=3, journal_path=journal_base, tz="UTC",
    )
    n = await stm.recover()
    # Total = 5 turns across two siblings, but deque caps at 3 newest.
    assert n == 3
    turns = await stm.render()
    assert [t.content for t in turns] == ["old-3", "new-1", "new-2"]


async def test_purge_unlinks_every_sibling(journal_base: Path) -> None:
    """Sleep Metabolism wipe must remove EVERY rotated sibling, not just
    the active one — the day's facts have been consolidated already."""
    journal_base.parent.mkdir(parents=True, exist_ok=True)
    today = date.today()
    yesterday_path = _seed_sibling(
        journal_base, today - timedelta(days=1), [("agent", "y")],
    )
    today_path = _seed_sibling(
        journal_base, today, [("agent", "t")],
    )
    assert yesterday_path.exists() and today_path.exists()

    stm = ShortTermMemory(
        max_turns=10, journal_path=journal_base, tz="UTC",
    )
    await stm.recover()
    await stm.append("architect", "after-recover")  # ensure pending is non-empty
    await stm.purge()

    assert not yesterday_path.exists()
    assert not today_path.exists()
    assert stm.journal_siblings() == []
    assert stm.size_estimate() == 0
    assert stm.pending_writes == 0


async def test_purge_leaves_foreign_files_alone(journal_base: Path) -> None:
    """Files that don't match the writer's ``<stem>-YYYY-MM-DD.jsonl``
    pattern must be ignored by purge — operators sometimes drop debug
    files in ``state/``.
    """
    journal_base.parent.mkdir(parents=True, exist_ok=True)
    foreign = journal_base.parent / "operator_notes.txt"
    foreign.write_text("do not touch", encoding="utf-8")
    _seed_sibling(journal_base, date.today(), [("agent", "x")])

    stm = ShortTermMemory(
        max_turns=10, journal_path=journal_base, tz="UTC",
    )
    await stm.purge()

    assert foreign.exists()
    assert foreign.read_text(encoding="utf-8") == "do not touch"


async def test_fsync_override_applies_on_demand(journal_base: Path) -> None:
    """``flush_journal(fsync=True)`` must override the constructor default.
    Used by ``shutdown()`` to guarantee durability across power loss
    even when ``journal_fsync_on_flush`` is disabled in config.
    """
    stm = ShortTermMemory(
        max_turns=10,
        journal_path=journal_base,
        fsync_on_flush=False,  # default off
        tz="UTC",
    )
    await stm.append("architect", "important")

    n = await stm.flush_journal(fsync=True)
    assert n == 1
    # Data made it to disk — we can't directly observe fsync, but we can
    # at least confirm the write path didn't swallow the override.
    active = stm.active_journal_path()
    assert active is not None and active.exists()
    # Compare only the semantic fields; the writer also stamps a
    # `schema_version` envelope key on every record — don't bind this
    # test to that internal envelope shape.
    records = _read_records(active)
    assert len(records) == 1
    assert records[0]["role"] == "architect"
    assert records[0]["content"] == "important"
    assert "ts" in records[0]


async def test_shutdown_always_fsyncs(journal_base: Path) -> None:
    """``shutdown()`` always flushes with fsync regardless of config.
    Idempotent: calling twice does not re-write the same record.
    """
    stm = ShortTermMemory(
        max_turns=10, journal_path=journal_base, fsync_on_flush=False, tz="UTC",
    )
    await stm.append("agent", "bye")
    n1 = await stm.shutdown()
    n2 = await stm.shutdown()
    assert n1 == 1
    assert n2 == 0  # nothing pending the second time
    active = stm.active_journal_path()
    assert active is not None
    assert len(_read_records(active)) == 1


async def test_journal_siblings_orders_oldest_first(journal_base: Path) -> None:
    """Heartbeat consolidation reads siblings in this order to preserve
    the natural conversation timeline across midnight rotation."""
    journal_base.parent.mkdir(parents=True, exist_ok=True)
    today = date.today()
    _seed_sibling(journal_base, today, [("agent", "newest")])
    _seed_sibling(journal_base, today - timedelta(days=3), [("agent", "oldest")])
    _seed_sibling(journal_base, today - timedelta(days=1), [("agent", "middle")])

    stm = ShortTermMemory(
        max_turns=10, journal_path=journal_base, tz="UTC",
    )
    siblings = stm.journal_siblings()
    assert len(siblings) == 3
    # Filename suffixes encode the date — alphabetical order == chronological.
    assert [p.name for p in siblings] == sorted(p.name for p in siblings)


async def test_no_journal_configured_is_inert(tmp_path: Path) -> None:
    """STM with ``journal_path=None`` must keep working in RAM-only mode —
    the dashboard's smoke variant and unit tests use this shape.
    """
    stm = ShortTermMemory(max_turns=3, journal_path=None)
    assert stm.journal_path is None
    assert stm.active_journal_path() is None
    assert stm.journal_siblings() == []
    # recover() on a journal-less STM clears the deque (no source to
    # rehydrate from), so call it before any append we want to keep.
    assert await stm.recover() == 0
    await stm.append("agent", "ram-only")
    assert await stm.flush_journal() == 0
    assert await stm.purge() == 1  # one turn in deque
    assert stm.size_estimate() == 0
