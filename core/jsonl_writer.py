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
    """

    def __init__(
        self,
        base_path: Path | str,
        *,
        retain_days: int = 30,
        tz: str = "UTC",
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
        # Reused per-instance — serialises Python-side writers so two
        # awaiters can't both hand a write to the executor pool at the
        # exact same instant on the same file descriptor path.
        self._lock = asyncio.Lock()
        self._last_sweep_date: date | None = None

    # ---------- public surface ------------------------------------------------

    @property
    def base_path(self) -> Path:
        return self._base_path

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

    async def append(self, record: dict[str, Any]) -> None:
        """Append one record. Never raises — failures log and degrade.

        The caller's coroutine is suspended only while the executor
        runs the actual write (a few µs for a small JSON line). The
        lock guarantees serial dispatch from the asyncio side so we
        don't oversaturate the executor pool with rotation work.
        """
        async with self._lock:
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self._append_blocking, record)
            except Exception:
                log.exception(
                    "JSONL writer append failed base=%s", self._base_path,
                )
            # Retention sweep is opportunistic, fire-and-forget on
            # the executor — failure here must NEVER fail the write.
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

    # ---------- internals -----------------------------------------------------

    def _append_blocking(self, record: dict[str, Any]) -> None:
        path = self.active_path()
        line = json.dumps(record, ensure_ascii=False) + "\n"
        # POSIX O_APPEND keeps the byte-level write atomic when multiple
        # processes write to the same file — the asyncio lock above
        # handles intra-process races. `os.open` + `os.write` avoids
        # Python's buffering layer; one syscall per record.
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        fd = os.open(path, flags, 0o644)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)

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
