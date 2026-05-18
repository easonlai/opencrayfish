"""core.jsonl_writer — Append-only JSONL writer with date rotation + retention.

Centralises three concerns that previously lived inline in
`skills/registry.py::_audit`, `cognition.py::_persist`, and
`reflection.py::_append_blocking`:

  1. **Per-day rotation** — the active file's name embeds the local date
     (`<base>-YYYY-MM-DD.jsonl`). A fresh date → a fresh file. We never
     rename or truncate; readers parsing a file in flight never see a
     partial record.
  2. **Line-atomic appends** — `O_APPEND` plus a single `fh.write()` per
     record so concurrent invokes (registered Skills running under
     `asyncio.gather()`, plus Brain's fire-and-forget reflections) can't
     interleave bytes mid-line. An internal `asyncio.Lock` further
     serialises Python-side writers so the executor pool can't race on
     the same path.
  3. **Bounded retention** — opportunistic cleanup of sibling files
     older than `retain_days` runs at most once per process-day. Cheap
     scandir + unlink, no compression — operators who want long-term
     archival should move files off the device, not bloat the SD card.
  4. **Forward-defensive ``schema_version``** — every record is stamped
     with the writer's configured schema version (default ``1``) as the
     FIRST field of the line. Future producers that bump their on-disk
     format only need to pass ``schema_version=N`` at construction and
     downstream readers can branch on the field. A record that already
     carries an explicit ``schema_version`` is passed through unchanged
     so producers can emit mixed versions during a migration window.

Synchronous writers (e.g. heartbeat's own log) keep their existing path;
this module is only for the high-frequency feeds.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

# Filenames produced by this module always match this pattern. The same
# regex is used by the retention sweeper to identify our own files (and
# nothing else — we will NEVER delete a file we didn't write).
_FILENAME_RE = re.compile(r"^(?P<base>.+)-(?P<date>\d{4}-\d{2}-\d{2})\.jsonl$")


class RotatingJsonlWriter:
    """Date-rotating JSONL append writer.

    Construct ONCE per feed at boot and reuse — the instance carries the
    asyncio.Lock and the last retention-sweep date. Re-instantiating per
    write would defeat both.

    Parameters
    ----------
    base_path
        Path to the LEGACY file (e.g. `state/skills.jsonl`). The active
        filename derives from this:
            state/skills.jsonl  →  state/skills-2026-05-17.jsonl
        We do NOT write to the legacy path — keeping it untouched means
        the rotation cutover is fully backwards-compatible for any
        operator who'd been tailing the old name (they'll just see no
        new lines and notice the date-stamped sibling).
    retain_days
        Sibling files older than this many days are unlinked on the
        first write of each new local day. 0 disables retention.
    tz
        Timezone for both the date stamp and the retention window. Match
        whatever the rest of the agent uses (cfg.system.timezone).
    schema_version
        Stamped into every appended record (as the FIRST field of the
        JSON object) unless the caller already provided one. Bump when
        the on-disk record shape changes incompatibly so downstream
        readers can branch on it. Defaults to ``1`` — the baseline
        version for every feed that existed at the time of the P3.3
        forward-defensive rollout.
    """

    def __init__(
        self,
        base_path: Path | str,
        *,
        retain_days: int = 30,
        tz: str = "UTC",
        schema_version: int = 1,
    ) -> None:
        self._base_path = Path(base_path)
        self._base_path.parent.mkdir(parents=True, exist_ok=True)
        # Strip a single trailing `.jsonl` so we can splice the date in.
        stem = self._base_path.name
        if stem.endswith(".jsonl"):
            stem = stem[: -len(".jsonl")]
        self._stem = stem
        self._dir = self._base_path.parent
        self._retain_days = max(0, int(retain_days))
        self._tz = ZoneInfo(tz)
        self._schema_version = int(schema_version)
        # Reused per-instance — serialises Python-side writers so two
        # awaiters can't both hand a write to the executor pool at the
        # exact same instant on the same file descriptor path.
        self._lock = asyncio.Lock()
        self._last_sweep_date: date | None = None

    # ---------- public surface ------------------------------------------------

    @property
    def base_path(self) -> Path:
        return self._base_path

    @property
    def schema_version(self) -> int:
        """The schema_version stamped into every record produced by this
        writer (overridable per-record by the caller)."""
        return self._schema_version

    def active_path(self, *, now: datetime | None = None) -> Path:
        """Return the file the NEXT write will target."""
        d = (now or datetime.now(tz=self._tz)).date()
        return self._dir / f"{self._stem}-{d.isoformat()}.jsonl"

    def sibling_paths(self) -> list[Path]:
        """Return every rotated file we've ever produced, oldest first."""
        out: list[tuple[date, Path]] = []
        if not self._dir.exists():
            return []
        for p in self._dir.iterdir():
            if not p.is_file():
                continue
            m = _FILENAME_RE.match(p.name)
            if not m or m.group("base") != self._stem:
                continue
            try:
                d = date.fromisoformat(m.group("date"))
            except ValueError:
                continue
            out.append((d, p))
        out.sort(key=lambda t: t[0])
        return [p for _, p in out]

    async def append(
        self,
        record: dict[str, Any],
        *,
        fsync: bool = False,
    ) -> None:
        """Append one record. Never raises — failures log and degrade.

        Parameters
        ----------
        record
            A JSON-serialisable dict.
        fsync
            When True, ``os.fsync()`` the file before close. Strict power-
            loss durability at the cost of an SD-card sync (Pi). Default
            False: rely on the kernel to flush on its own schedule, which
            is the right trade-off for high-frequency telemetry feeds.

        The caller's coroutine is suspended only while the executor
        runs the actual write (a few µs for a small JSON line). The
        lock guarantees serial dispatch from the asyncio side so we
        don't oversaturate the executor pool with rotation work.
        """
        async with self._lock:
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(
                    None, self._append_blocking, record, fsync,
                )
            except Exception:
                log.exception(
                    "JSONL writer append failed base=%s", self._base_path,
                )
            await self._maybe_sweep_locked()

    async def append_many(
        self,
        records: list[dict[str, Any]],
        *,
        fsync: bool = False,
    ) -> int:
        """Append a batch of records in a SINGLE open()/write()/close().

        Used by callers (e.g. ``ShortTermMemory.flush_journal``) that
        accumulate records in RAM and flush them as one durable unit —
        the batched write keeps total syscall count to one per flush
        instead of one per record, which matters on the Pi's SD card
        where every open() costs ~ms-class latency.

        Returns the number of records actually written (0 on empty input
        or on error — errors are logged, not raised, so the caller's
        hot path stays alive).
        """
        if not records:
            return 0
        async with self._lock:
            try:
                loop = asyncio.get_running_loop()
                n = await loop.run_in_executor(
                    None, self._append_many_blocking, list(records), fsync,
                )
            except Exception:
                log.exception(
                    "JSONL writer append_many failed base=%s", self._base_path,
                )
                n = 0
            await self._maybe_sweep_locked()
            return n

    async def purge_all(self) -> int:
        """Unlink EVERY rotated sibling under this base path.

        Used by callers (e.g. ``ShortTermMemory.purge`` during Sleep
        Metabolism) that need a complete wipe — the agent's STM journal
        is consolidated into ``archive.md`` nightly, so retaining the
        raw turn-level files past metabolism would just duplicate state.

        Foreign files in the same directory are NEVER touched (the
        filename guard in ``sibling_paths()`` ensures we only see files
        we ourselves wrote). Returns the number of files unlinked.
        """
        async with self._lock:
            removed = 0
            for p in self.sibling_paths():
                try:
                    p.unlink()
                    removed += 1
                except OSError:
                    log.exception(
                        "JSONL writer purge_all: unlink failed path=%s", p,
                    )
            if removed:
                log.info(
                    "JSONL writer purge_all base=%s removed=%d",
                    self._base_path, removed,
                )
            return removed

    # ---------- internals -----------------------------------------------------

    async def _maybe_sweep_locked(self) -> None:
        """Run the daily retention sweep at most once per local day.

        Caller MUST hold ``self._lock``. Failures are logged but never
        raised — a flaky filesystem must never fail the actual write.
        """
        try:
            today = datetime.now(tz=self._tz).date()
            if self._retain_days > 0 and self._last_sweep_date != today:
                self._last_sweep_date = today
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(
                    None, self._sweep_blocking, today,
                )
        except Exception:
            log.exception(
                "JSONL writer retention sweep scheduling failed base=%s",
                self._base_path,
            )

    def _append_blocking(
        self,
        record: dict[str, Any],
        fsync: bool = False,
    ) -> None:
        path = self.active_path()
        line = json.dumps(self._stamp(record), ensure_ascii=False) + "\n"
        # POSIX O_APPEND keeps the byte-level write atomic when multiple
        # processes write to the same file — the asyncio lock above
        # handles intra-process races. `os.open` + `os.write` avoids
        # Python's buffering layer; one syscall per record.
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        fd = os.open(path, flags, 0o644)
        try:
            os.write(fd, line.encode("utf-8"))
            if fsync:
                os.fsync(fd)
        finally:
            os.close(fd)

    def _append_many_blocking(
        self,
        records: list[dict[str, Any]],
        fsync: bool = False,
    ) -> int:
        """Batched sibling of ``_append_blocking`` — one open() for N records.

        The byte payload is built in RAM first so the os.write() is one
        contiguous syscall — line-atomic under POSIX O_APPEND just like
        the single-record path.
        """
        path = self.active_path()
        payload = b"".join(
            (json.dumps(self._stamp(r), ensure_ascii=False) + "\n").encode("utf-8")
            for r in records
        )
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        fd = os.open(path, flags, 0o644)
        try:
            os.write(fd, payload)
            if fsync:
                os.fsync(fd)
        finally:
            os.close(fd)
        return len(records)

    def _stamp(self, record: dict[str, Any]) -> dict[str, Any]:
        """Return ``record`` with ``schema_version`` stamped FIRST.

        Pass-through if the caller already set the field — preserves
        their value and position so a migration-window producer can
        emit mixed versions intentionally. Otherwise we splice the
        writer's configured version at the front of a fresh dict so
        the field is the first key in the serialised JSON object
        (cheap to grep / eyeball when tailing a feed).
        """
        if "schema_version" in record:
            return record
        return {"schema_version": self._schema_version, **record}

    def _sweep_blocking(self, today: date) -> None:
        cutoff = today - timedelta(days=self._retain_days)
        kept = 0
        removed = 0
        for p in self.sibling_paths():
            m = _FILENAME_RE.match(p.name)
            if not m:
                continue
            try:
                d = date.fromisoformat(m.group("date"))
            except ValueError:
                continue
            if d < cutoff:
                try:
                    p.unlink()
                    removed += 1
                    log.info(
                        "JSONL writer retention: removed %s (age=%dd)",
                        p, (today - d).days,
                    )
                except OSError:
                    log.exception(
                        "JSONL writer retention: unlink failed path=%s", p,
                    )
            else:
                kept += 1
        if removed:
            log.info(
                "JSONL writer retention sweep base=%s kept=%d removed=%d",
                self._base_path, kept, removed,
            )
