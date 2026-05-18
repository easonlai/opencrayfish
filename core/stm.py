"""core.stm — Short-Term Memory with deferred journaling.

Two-tier durability per the Architect's biological design:

  * **RAM deque** holds the active conversation window passed to the SLM.
    Bounded by `memory.stm_max_turns` from config.yaml. Stays warm across
    idle pauses — never released except by the nightly Sleep Metabolism.
  * **In-RAM pending buffer** accumulates new turns since the last disk flush.
    No disk I/O on the hot path — keeps Pi5 SD card wear minimal during a
    conversation.
  * **Disk journal** is the durable backstop. Rotated per local day via
    ``core.jsonl_writer.RotatingJsonlWriter`` so a long-running deployment
    can't pin a single growing file open across days (the pre-P3.2 design
    had no rotation — a crash before Sleep Metabolism's nightly purge
    would leave the file growing indefinitely). Active filename pattern:
    ``state/stm_journal-YYYY-MM-DD.jsonl``. By default writes are not
    fsync'd; ``fsync_on_flush`` enables strict durability per-write, and
    ``shutdown()`` always fsyncs as a hard guarantee.

Lifecycle:

  * `append()`         → push into deque AND into `_pending`. No disk I/O.
  * `flush_journal()`  → drain `_pending` to the active rotated file in a
                         single open/write/close. Heartbeat calls this on
                         idle (configurable cadence).
  * `recover()`        → boot-time replay of ALL rotated siblings so a
                         crashed agent wakes up with prior conversation
                         context regardless of when it crashed.
  * `purge()`          → called by Sleep Metabolism at 02:00. Wipes deque,
                         pending, AND every rotated sibling — the day's
                         facts have been consolidated into archive.md and
                         soul.md.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from .jsonl_writer import RotatingJsonlWriter

log = logging.getLogger(__name__)

Role = Literal["architect", "agent", "system"]


@dataclass(frozen=True)
class Turn:
    role: Role
    content: str


@dataclass(frozen=True)
class _PendingRecord:
    ts: str
    role: Role
    content: str


class ShortTermMemory:
    def __init__(
        self,
        max_turns: int,
        *,
        journal_path: Path | str | None = None,
        fsync_on_flush: bool = False,
        retain_days: int = 30,
        tz: str = "UTC",
    ) -> None:
        self._buf: deque[Turn] = deque(maxlen=max_turns)
        self._lock = asyncio.Lock()
        self._max_turns = max_turns
        # RotatingJsonlWriter owns the on-disk concerns now: daily file
        # rotation by ``tz``, retention sweeping, line-atomic O_APPEND,
        # and an internal lock that prevents concurrent writers from
        # interleaving bytes. STM keeps only the in-RAM concerns.
        self._writer: RotatingJsonlWriter | None = None
        self._journal_base: Path | None = None
        if journal_path is not None:
            base = Path(journal_path)
            base.parent.mkdir(parents=True, exist_ok=True)
            self._journal_base = base
            self._writer = RotatingJsonlWriter(
                base, retain_days=retain_days, tz=tz,
            )
        self._fsync_on_flush = bool(fsync_on_flush)
        # Pending writes since the last disk flush. Drained by flush_journal().
        self._pending: list[_PendingRecord] = []

    # ---------- public properties --------------------------------------------

    @property
    def journal_path(self) -> Path | None:
        """BASE path of the journal series (e.g. ``state/stm_journal.jsonl``).

        NOTE: this is no longer a single file we write to — the active
        rotated sibling is at ``self.active_journal_path()`` and the full
        set is at ``self.journal_siblings()``. The base is kept for
        operator-facing messages and back-compat with older code paths
        that just want a stable identifier for the journal series.
        """
        return self._journal_base

    def active_journal_path(self) -> Path | None:
        """The rotated file the NEXT flush will target (today's sibling)."""
        if self._writer is None:
            return None
        return self._writer.active_path()

    def journal_siblings(self) -> list[Path]:
        """Every rotated sibling we've ever written, oldest first.

        Used by ``recover()`` and by ``Heartbeat._extract_conversation_for_consolidation``
        which needs to read the day's full transcript across the rotation
        boundary (a flush at 23:59 lands in yesterday's file; the next
        one at 00:01 lands in today's).
        """
        if self._writer is None:
            return []
        return self._writer.sibling_paths()

    @property
    def pending_writes(self) -> int:
        """How many turns are buffered in RAM but not yet on disk."""
        return len(self._pending)

    def size_estimate(self) -> int:
        """Non-blocking turn count for observability. Returns the deque length."""
        return len(self._buf)

    # ---------- public API ----------------------------------------------------

    async def append(self, role: Role, content: str) -> None:
        """Hot path. RAM only — no disk I/O."""
        async with self._lock:
            turn = Turn(role=role, content=content)
            self._buf.append(turn)
            if self._writer is not None:
                self._pending.append(_PendingRecord(
                    ts=datetime.now(tz=UTC).isoformat(),
                    role=role,
                    content=content,
                ))
            log.debug(
                "STM append: role=%s len=%d (buf=%d, pending=%d)",
                role,
                len(content),
                len(self._buf),
                len(self._pending),
            )

    async def render(self) -> list[Turn]:
        async with self._lock:
            return list(self._buf)

    async def flush_journal(self, *, fsync: bool | None = None) -> int:
        """Drain pending records to the active rotated sibling.

        Args:
          fsync: override the constructor default. When True, calls
                 os.fsync() after the write — strict durability at the cost
                 of an SD-card sync on the Pi.

        Returns the number of records flushed (0 if nothing pending or no
        journal is configured). Errors are logged but never raised — the
        caller's loop must keep running.
        """
        if self._writer is None or not self._pending:
            return 0
        async with self._lock:
            if not self._pending:
                return 0
            # Snapshot + clear under the lock; if the executor write fails
            # the records are lost (matches pre-P3.2 behaviour), but the
            # writer's own error handling logs the exception so the
            # operator can see it.
            snapshot = list(self._pending)
            self._pending.clear()
            do_fsync = self._fsync_on_flush if fsync is None else bool(fsync)
        records = [
            {"ts": r.ts, "role": r.role, "content": r.content}
            for r in snapshot
        ]
        n = await self._writer.append_many(records, fsync=do_fsync)
        if n:
            log.info(
                "STM flush_journal: %d turn(s) -> %s (fsync=%s).",
                n,
                self._writer.active_path(),
                do_fsync,
            )
        return n

    async def recover(self) -> int:
        """Replay every rotated journal sibling into the deque on startup.

        Tail-loaded: the deque keeps only the most recent ``max_turns``
        entries across ALL siblings. Pending in-RAM writes are also
        considered — normally empty at boot, but kept for symmetry.
        """
        async with self._lock:
            n = self._load_from_journal_locked()
            if n:
                log.info(
                    "STM recovered %d turn(s) from journal at startup.", n,
                )
            return n

    async def purge(self) -> int:
        """Sleep-Metabolism wipe: clear deque, pending, AND every sibling.

        Returns the number of RAM turns dropped. The day's facts have
        been consolidated into archive.md and soul.md by metabolism —
        retaining the raw turns would just duplicate state and burn SD
        write cycles on the next nightly purge.
        """
        # Snapshot RAM under the lock, but call writer.purge_all OUTSIDE
        # the STM lock so we don't hold it across executor I/O — the
        # writer has its own lock that already serialises file ops.
        async with self._lock:
            n = len(self._buf)
            pending_dropped = len(self._pending)
            self._buf.clear()
            self._pending.clear()
        removed = 0
        if self._writer is not None:
            removed = await self._writer.purge_all()
        log.info(
            "STM purge: dropped %d turn(s) from RAM, %d pending; "
            "%d journal sibling(s) removed.",
            n, pending_dropped, removed,
        )
        return n

    async def shutdown(self) -> int:
        """Final flush before process exit. Always uses fsync to guarantee
        durability across power loss. Safe to call multiple times."""
        n = await self.flush_journal(fsync=True)
        log.info(
            "STM shutdown: %d pending turn(s) flushed and fsync'd.", n,
        )
        return n

    # ---------- internals -----------------------------------------------------

    def _load_from_journal_locked(self) -> int:
        """Load the most recent ``max_turns`` entries from every rotated
        sibling + pending. Caller MUST hold ``self._lock``.
        """
        self._buf.clear()
        records: list[Turn] = []
        if self._writer is not None:
            # Siblings are oldest-first; reading them in order means
            # the deque tail-load below ends up with the chronologically
            # newest turns. Bad lines are skipped, not raised, so a
            # half-written record from a previous crash can't block boot.
            for sibling in self._writer.sibling_paths():
                try:
                    lines = sibling.read_text(encoding="utf-8").splitlines()
                except OSError:
                    log.exception(
                        "Failed to read STM journal sibling %s.", sibling,
                    )
                    continue
                for raw in lines:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        rec = json.loads(raw)
                        records.append(
                            Turn(role=rec["role"], content=rec["content"]),
                        )
                    except (json.JSONDecodeError, KeyError, TypeError):
                        log.warning(
                            "Skipping malformed STM journal line: %r",
                            raw[:80],
                        )
        # Pending writes (in-RAM) are most recent.
        for p in self._pending:
            records.append(Turn(role=p.role, content=p.content))
        # Tail-load: deque(maxlen=N) keeps only the most recent N entries.
        for t in records[-self._max_turns:]:
            self._buf.append(t)
        return len(self._buf)
