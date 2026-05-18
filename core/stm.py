"""core.stm — Short-Term Memory with deferred journaling.

Two-tier durability per the Architect's biological design:

  * **RAM deque** holds the active conversation window passed to the SLM.
    Bounded by `memory.stm_max_turns` from config.yaml. Stays warm across
    idle pauses — never released except by the nightly Sleep Metabolism.
  * **In-RAM pending buffer** accumulates new turns since the last disk flush.
    No disk I/O on the hot path — keeps Pi5 SD card wear minimal during a
    conversation.
  * **Disk journal** (`state/stm_journal.jsonl`) is the durable backstop. By
    default it is written to (not fsync'd) only when the Heartbeat detects
    idle, when Sleep Metabolism consolidates, or at shutdown. fsync can be
    enabled via config for stricter durability.

Lifecycle:

  * `append()`         → push into deque AND into `_pending`. No disk I/O.
  * `flush_journal()`  → drain `_pending` to disk. Single open/write/close.
                         Heartbeat calls this on idle (configurable cadence).
  * `recover()`        → boot-time replay of the journal so a crashed agent
                         wakes up with prior conversation context.
  * `purge()`          → called by Sleep Metabolism at 02:00. Wipes deque,
                         pending, AND journal — the day's facts have been
                         consolidated into archive.md and soul.md.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

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
    ) -> None:
        self._buf: deque[Turn] = deque(maxlen=max_turns)
        self._lock = asyncio.Lock()
        self._max_turns = max_turns
        self._journal: Path | None = (
            Path(journal_path) if journal_path is not None else None
        )
        if self._journal is not None:
            self._journal.parent.mkdir(parents=True, exist_ok=True)
        self._fsync_on_flush = bool(fsync_on_flush)
        # Pending writes since the last disk flush. Drained by flush_journal().
        self._pending: list[_PendingRecord] = []

    # ---------- public properties --------------------------------------------

    @property
    def journal_path(self) -> Path | None:
        """Disk path of the durable journal (None if not configured)."""
        return self._journal

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
            if self._journal is not None:
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
        """Drain pending records to disk in one open()/write()/close().

        Args:
          fsync: override the constructor default. When True, calls
                 os.fsync() after the write — strict durability at the cost
                 of an SD-card sync on the Pi.

        Returns the number of records flushed (0 if nothing pending or no
        journal is configured).
        """
        async with self._lock:
            n = self._flush_pending_locked(fsync=fsync)
            if n:
                log.info(
                    "STM flush_journal: %d turn(s) -> %s (fsync=%s).",
                    n,
                    self._journal,
                    self._fsync_on_flush if fsync is None else bool(fsync),
                )
            return n

    async def recover(self) -> int:
        """Replay the journal into the deque on startup."""
        async with self._lock:
            n = self._load_from_journal_locked()
            if n:
                log.info("STM recovered %d turn(s) from journal at startup.", n)
            return n

    async def purge(self) -> int:
        """Sleep-Metabolism wipe: clear deque, pending, AND journal.

        Returns the number of RAM turns dropped. The journal is unconditionally
        emptied — the day's facts have been consolidated into archive.md and
        soul.md by metabolism.
        """
        async with self._lock:
            n = len(self._buf)
            pending_dropped = len(self._pending)
            self._buf.clear()
            self._pending.clear()
            journal_existed = (
                self._journal is not None and self._journal.exists()
            )
            if journal_existed:
                # Truncate (not delete) so the file handle/path stays stable.
                self._journal.write_text("", encoding="utf-8")
            log.info(
                "STM purge: dropped %d turn(s) from RAM, %d pending; "
                "journal %s.",
                n,
                pending_dropped,
                "truncated" if journal_existed else "not present",
            )
            return n

    async def shutdown(self) -> int:
        """Final flush before process exit. Always uses fsync to guarantee
        durability across power loss. Safe to call multiple times."""
        async with self._lock:
            n = self._flush_pending_locked(fsync=True)
            log.info(
                "STM shutdown: %d pending turn(s) flushed and fsync'd.", n
            )
            return n

    # ---------- internals -----------------------------------------------------

    def _flush_pending_locked(self, *, fsync: bool | None) -> int:
        """Write all pending records to the journal in a single file open.

        Caller MUST hold `self._lock`.
        """
        if self._journal is None or not self._pending:
            return 0
        do_fsync = self._fsync_on_flush if fsync is None else fsync
        lines = [
            json.dumps(
                {"ts": r.ts, "role": r.role, "content": r.content},
                ensure_ascii=False,
            ) + "\n"
            for r in self._pending
        ]
        n = len(lines)
        try:
            with self._journal.open("a", encoding="utf-8") as fh:
                fh.writelines(lines)
                if do_fsync:
                    fh.flush()
                    os.fsync(fh.fileno())
            self._pending.clear()
            return n
        except OSError:
            log.exception(
                "Failed to flush %d STM record(s); will retry on next flush.",
                n,
            )
            return 0

    def _load_from_journal_locked(self) -> int:
        """Load the most recent `max_turns` entries from journal + pending.

        Used by `recover()` at boot. Caller MUST hold `self._lock`.
        Returns the count loaded.
        """
        self._buf.clear()
        records: list[Turn] = []
        if self._journal is not None and self._journal.exists():
            try:
                lines = self._journal.read_text(encoding="utf-8").splitlines()
            except OSError:
                log.exception("Failed to read STM journal during recover.")
                lines = []
            for raw in lines:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                    records.append(Turn(role=rec["role"], content=rec["content"]))
                except (json.JSONDecodeError, KeyError, TypeError):
                    log.warning(
                        "Skipping malformed STM journal line: %r", raw[:80]
                    )
        # Pending writes (in-RAM) are most recent.
        for p in self._pending:
            records.append(Turn(role=p.role, content=p.content))
        # Tail-load: deque(maxlen=N) keeps only the most recent N entries.
        for t in records[-self._max_turns:]:
            self._buf.append(t)
        return len(self._buf)
