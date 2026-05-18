"""Smoke test: soul.md atomic write + crash-safety.

`SoulHandler._append` runs four in-memory dry-run defences
(sanitize → empty-after-sanitize reject → immutable-fingerprint
re-parse → marker-count check). Those guarantee the candidate string is
structurally valid before it touches disk. This test covers the
remaining disk-side hazard: the actual write step.

Previously `_append` ended with `self._path.write_text(new_raw, ...)`
which is open → truncate → write → close. A power-loss or OOM-kill
between truncate and the final write would leave `soul.md` truncated /
half-written, and the next boot's `SoulHandler.__init__` would raise
`SoulProtectionError("soul.md is malformed: ...")` and refuse to start
the agent. The fix is the standard `tmp + os.replace` atomic-swap
pattern (consistent with vitals.json / tasks.yaml / tools.json /
skills.json publishers in main.py).

This script proves four properties:

  1. Happy path: append succeeds, soul.md updated, no `.tmp` left
     behind, IMMUTABLE_CORE bytes unchanged.
  2. Dry-run rejection: when the in-memory candidate fails one of the
     four defences (we force this by passing text that sanitizes to
     empty), the on-disk soul.md is BYTE-IDENTICAL to the pre-call
     state AND no `.tmp` exists.
  3. Disk crash simulation: when `Path.replace` itself raises (we
     monkey-patch it), `soul.md` is BYTE-IDENTICAL to the pre-call
     state AND the half-written `.tmp` is cleaned up.
  4. Stale tmp tolerance: a pre-existing leftover `soul.md.tmp` (from
     a hypothetical earlier crash) does NOT break the next append —
     it gets overwritten and renamed atomically.

Run:
    python scripts/smoke_soul_atomic.py
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from core.soul_handler import SoulHandler, SoulProtectionError  # noqa: E402

# Minimal but valid soul.md — mirrors the on-disk file's region structure
# without depending on the operator's actual identity content.
_TEMPLATE = """\
<!-- IMMUTABLE_CORE_START -->
# [IDENTITY]
- **Designation**: Test-Agent

# [FUNDAMENTAL_LAWS]
- Be honest.

# [BEHAVIORAL_MATRIX]
- Reply politely.
<!-- IMMUTABLE_CORE_END -->

<!-- DYNAMIC_GROWTH_START -->
# [CORE_MEMORIES]

# [LEARNED_PREFERENCES]

# [EMOTIONAL_EVOLUTION]
<!-- DYNAMIC_GROWTH_END -->
"""


def _write_template(path: Path) -> None:
    path.write_text(_TEMPLATE, encoding="utf-8")


async def test_happy_path() -> None:
    """Normal append: disk reflects the new content, no .tmp residue."""
    with tempfile.TemporaryDirectory(prefix="smoke-soul-happy-") as td:
        soul_path = Path(td) / "soul.md"
        _write_template(soul_path)
        original_immutable = SoulHandler(soul_path)._canonical_immutable

        soul = SoulHandler(soul_path)
        await soul.append_core_memory("Test fact from smoke harness.")

        # File on disk now contains the new fact.
        on_disk = soul_path.read_text(encoding="utf-8")
        assert "Test fact from smoke harness." in on_disk, \
            "Append did not land on disk."
        # No half-written tmp sibling left.
        assert not (soul_path.with_suffix(".md.tmp")).exists(), \
            "Stale .tmp sibling found after successful append."
        # IMMUTABLE_CORE byte-identical (proves dry-run + atomic swap kept it safe).
        after_immutable = SoulHandler(soul_path)._canonical_immutable
        assert after_immutable == original_immutable, \
            "IMMUTABLE_CORE bytes changed during an append."
    print("  [1] happy path: append succeeds, no .tmp residue ✓")


async def test_dry_run_rejection_leaves_disk_untouched() -> None:
    """A candidate that fails dry-run must not mutate the on-disk file."""
    with tempfile.TemporaryDirectory(prefix="smoke-soul-dryrun-") as td:
        soul_path = Path(td) / "soul.md"
        _write_template(soul_path)
        before = soul_path.read_text(encoding="utf-8")

        soul = SoulHandler(soul_path)
        # Sanitize collapses this to empty string → defence #2 raises.
        # We feed pure whitespace + a NUL + a CR; all are stripped.
        raised = False
        try:
            await soul.append_core_memory("   \x00 \r  ")
        except SoulProtectionError:
            raised = True
        assert raised, "Empty-after-sanitize must raise SoulProtectionError."

        after = soul_path.read_text(encoding="utf-8")
        assert after == before, \
            "Dry-run rejection still mutated soul.md on disk."
        assert not (soul_path.with_suffix(".md.tmp")).exists(), \
            "Dry-run rejection left a .tmp sibling on disk."
    print("  [2] dry-run rejection: disk byte-identical, no .tmp ✓")


async def test_disk_crash_simulation_cleans_up_tmp() -> None:
    """When tmp.replace raises mid-swap, soul.md untouched + tmp removed."""
    with tempfile.TemporaryDirectory(prefix="smoke-soul-crash-") as td:
        soul_path = Path(td) / "soul.md"
        _write_template(soul_path)
        before = soul_path.read_text(encoding="utf-8")

        soul = SoulHandler(soul_path)

        # Monkey-patch Path.replace just for the duration of this call.
        original_replace = Path.replace

        def _fail_replace(self: Path, target):  # type: ignore[no-untyped-def]
            # Only intercept the SoulHandler tmp → soul.md swap; let any
            # other rename succeed (defensive — not strictly needed here).
            if self.name.endswith(".md.tmp"):
                raise OSError("simulated power loss during atomic swap")
            return original_replace(self, target)

        Path.replace = _fail_replace  # type: ignore[method-assign]
        try:
            raised = False
            try:
                await soul.append_core_memory("This append must not land.")
            except OSError as exc:
                assert "simulated power loss" in str(exc)
                raised = True
            assert raised, "Crash simulation did not propagate the OSError."
        finally:
            Path.replace = original_replace  # type: ignore[method-assign]

        # Crucial: canonical soul.md untouched (atomic guarantee held).
        after = soul_path.read_text(encoding="utf-8")
        assert after == before, \
            "Crash mid-swap mutated the canonical soul.md."
        # Crucial: .tmp sibling cleaned up by the except branch.
        assert not (soul_path.with_suffix(".md.tmp")).exists(), \
            "Crash mid-swap left a half-written .tmp on disk."
    print("  [3] crash mid-swap: soul.md untouched, .tmp cleaned up ✓")


async def test_stale_tmp_tolerated() -> None:
    """A leftover .tmp from a prior crash must not block the next append."""
    with tempfile.TemporaryDirectory(prefix="smoke-soul-stale-") as td:
        soul_path = Path(td) / "soul.md"
        _write_template(soul_path)
        # Simulate a crash from a previous run that left garbage behind.
        stale_tmp = soul_path.with_suffix(".md.tmp")
        stale_tmp.write_text("GARBAGE FROM EARLIER CRASH\n", encoding="utf-8")

        soul = SoulHandler(soul_path)
        await soul.append_preference("Prefers terse replies.")

        # New append landed cleanly.
        on_disk = soul_path.read_text(encoding="utf-8")
        assert "Prefers terse replies." in on_disk, \
            "Stale .tmp blocked the next append."
        # The stale tmp was overwritten by `tmp.write_text` then consumed
        # by `tmp.replace` → no .tmp sibling should remain.
        assert not stale_tmp.exists(), \
            "Stale .tmp survived the next append (should be consumed by replace)."
    print("  [4] stale .tmp tolerated: overwritten + consumed by replace ✓")


async def test_concurrent_appends_no_tmp_residue() -> None:
    """Multiple concurrent appends serialise via _lock; no .tmp lingers."""
    with tempfile.TemporaryDirectory(prefix="smoke-soul-concurrent-") as td:
        soul_path = Path(td) / "soul.md"
        _write_template(soul_path)
        soul = SoulHandler(soul_path)

        # 10 concurrent appends. The asyncio.Lock inside SoulHandler
        # serialises them; the atomic swap ensures the file is always
        # in a fully valid state between operations.
        await asyncio.gather(*[
            soul.append_emotion_event(f"event-{i}")
            for i in range(10)
        ])

        on_disk = soul_path.read_text(encoding="utf-8")
        for i in range(10):
            assert f"event-{i}" in on_disk, f"Concurrent append #{i} lost."
        assert not (soul_path.with_suffix(".md.tmp")).exists(), \
            "Concurrent appends left a .tmp sibling behind."
    print("  [5] 10 concurrent appends: all landed, no .tmp residue ✓")


async def _main() -> None:
    await test_happy_path()
    await test_dry_run_rejection_leaves_disk_untouched()
    await test_disk_crash_simulation_cleans_up_tmp()
    await test_stale_tmp_tolerated()
    await test_concurrent_appends_no_tmp_residue()
    print("\nALL SOUL ATOMIC-WRITE SMOKE CHECKS PASSED")


if __name__ == "__main__":
    asyncio.run(_main())
