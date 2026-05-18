"""Smoke test — JSONL rotation + reflection skill-failure summary + identity routing.

Covers three subsystems that interact at the JSONL boundary:
  * A4 — `RotatingJsonlWriter` produces date-stamped files, sweeps old ones,
         preserves concurrent line atomicity, and never raises on writer failures.
  * A3 — `ReflectionEngine.read_recent_skills` + `summarise_skills_recent` read
         the rotated `state/skills.jsonl` family and aggregate per-skill stats.
  * A2 — `Brain._handle_identity_question` (async) routes name/creator branches
         through `IdentitySkill` via the registry and falls back cleanly.

Run with: python scripts/smoke_rotation_reflection_identity.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.jsonl_writer import _FILENAME_RE, RotatingJsonlWriter  # type: ignore
from core.reflection import ReflectionEngine  # type: ignore

# ---------------------------------------------------------------------------
# A4 — rotation utility
# ---------------------------------------------------------------------------

async def test_a4_basic_append(tmp: Path) -> None:
    base = tmp / "feed.jsonl"
    w = RotatingJsonlWriter(base, retain_days=30, tz="UTC")
    await w.append({"hello": "world", "n": 1})
    await w.append({"hello": "world", "n": 2})
    active = w.active_path()
    assert active.exists(), "active path should exist after append"
    lines = active.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2, f"expected 2 lines, got {len(lines)}"
    for line in lines:
        json.loads(line)  # must parse cleanly
    # Legacy path is left untouched (backwards-compat with old tailers).
    assert not base.exists(), "legacy path should NOT be written"
    print("[a4-basic] append → date-stamped file with 2 valid lines ✓")


async def test_a4_filename_pattern(tmp: Path) -> None:
    w = RotatingJsonlWriter(tmp / "feed.jsonl", retain_days=0, tz="UTC")
    await w.append({"x": 1})
    active = w.active_path()
    m = _FILENAME_RE.match(active.name)
    assert m is not None, f"filename {active.name} should match _FILENAME_RE"
    assert m.group("base") == "feed"
    # date must be today (UTC)
    assert m.group("date") == datetime.now(tz=UTC).date().isoformat()
    print(f"[a4-filename] {active.name} matches pattern ✓")


async def test_a4_concurrent_atomic(tmp: Path) -> None:
    w = RotatingJsonlWriter(tmp / "concurrent.jsonl", retain_days=0, tz="UTC")
    N = 200
    payloads = [{"i": i, "tag": f"row-{i:03d}"} for i in range(N)]
    await asyncio.gather(*(w.append(p) for p in payloads))
    active = w.active_path()
    lines = active.read_text(encoding="utf-8").splitlines()
    assert len(lines) == N, f"expected {N} lines, got {len(lines)}"
    decoded = sorted(json.loads(line)["i"] for line in lines)
    assert decoded == list(range(N)), "every record must be present exactly once"
    print(f"[a4-concurrent] {N} concurrent appends preserved line atomicity ✓")


async def test_a4_retention_sweep(tmp: Path) -> None:
    """Manually plant old files and verify the sweeper removes them."""
    base = tmp / "retain.jsonl"
    w = RotatingJsonlWriter(base, retain_days=7, tz="UTC")
    today = date.today()
    # Plant: one fresh (within window) and two ancient (well outside).
    fresh = tmp / f"retain-{today.isoformat()}.jsonl"
    old1 = tmp / f"retain-{(today - timedelta(days=10)).isoformat()}.jsonl"
    old2 = tmp / f"retain-{(today - timedelta(days=30)).isoformat()}.jsonl"
    bystander = tmp / "retain-not-a-date.jsonl"     # must NOT be touched
    other = tmp / "different-2026-01-01.jsonl"      # different stem, untouched
    for p in (fresh, old1, old2, bystander, other):
        p.write_text('{"seed": true}\n', encoding="utf-8")
    # Direct call to the sweeper (deterministic — no time travel needed).
    w._sweep_blocking(today)  # type: ignore[attr-defined]
    assert fresh.exists(), "fresh file must survive"
    assert not old1.exists(), "10-day-old file must be removed"
    assert not old2.exists(), "30-day-old file must be removed"
    assert bystander.exists(), "non-date sibling MUST be left alone"
    assert other.exists(), "different stem MUST be left alone"
    print("[a4-retention] sweeper removed ancient siblings, preserved foreign files ✓")


async def test_a4_sibling_paths_sorted(tmp: Path) -> None:
    w = RotatingJsonlWriter(tmp / "sorted.jsonl", retain_days=0, tz="UTC")
    today = date.today()
    for offset in (5, 1, 9, 3):
        p = tmp / f"sorted-{(today - timedelta(days=offset)).isoformat()}.jsonl"
        p.write_text("{}\n", encoding="utf-8")
    siblings = w.sibling_paths()
    assert len(siblings) == 4
    # Extract dates from names and verify ascending order.
    dates = [_FILENAME_RE.match(p.name).group("date") for p in siblings]  # type: ignore[union-attr]
    assert dates == sorted(dates), f"sibling_paths must be oldest-first: {dates}"
    print("[a4-siblings] sibling_paths returns oldest-first ✓")


# ---------------------------------------------------------------------------
# A3 — reflection reads skills.jsonl
# ---------------------------------------------------------------------------

class _StubProvider:
    """Minimal stand-in for `provider.Provider`. ReflectionEngine only calls
    `.generate` during critique runs, which this test never exercises."""

    async def generate(self, system: str, msgs: list) -> str:  # noqa: ARG002
        return ""


async def test_a3_read_recent_skills_across_rotated(tmp: Path) -> None:
    skills_path = tmp / "skills.jsonl"
    # Plant rotated files with mixed timestamps.
    today = date.today()
    yesterday = today - timedelta(days=1)
    rec_today_ok = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "skill": "research", "ok": True, "latency_ms": 120,
        "tools_used": ["searxng"], "kwargs_keys": ["query"], "error": "",
    }
    rec_today_fail = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "skill": "research", "ok": False, "latency_ms": 50,
        "tools_used": [], "kwargs_keys": ["query"],
        "error": "SearXNG timeout",
    }
    rec_yest = {
        "ts": (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds"),
        "skill": "recall", "ok": True, "latency_ms": 8,
        "tools_used": ["ltm"], "kwargs_keys": ["query"], "error": "",
    }
    (tmp / f"skills-{today.isoformat()}.jsonl").write_text(
        json.dumps(rec_today_ok) + "\n" + json.dumps(rec_today_fail) + "\n",
        encoding="utf-8",
    )
    (tmp / f"skills-{yesterday.isoformat()}.jsonl").write_text(
        json.dumps(rec_yest) + "\n", encoding="utf-8",
    )
    engine = ReflectionEngine(
        provider=_StubProvider(),  # type: ignore[arg-type]
        timezone="UTC",
        feed_path=tmp / "reflection.jsonl",
        dropped_feed_path=tmp / "reflection_dropped.jsonl",
        skills_feed_path=skills_path,
    )
    # Read everything since 36h ago — should pull all three rows.
    rows_all = engine.read_recent_skills(
        since=datetime.now() - timedelta(hours=36),
    )
    assert len(rows_all) == 3, f"expected 3 rows, got {len(rows_all)}"
    # Read only the last hour — must drop yesterday's row.
    rows_recent = engine.read_recent_skills(
        since=datetime.now() - timedelta(hours=1),
    )
    assert len(rows_recent) == 2, f"expected 2 recent rows, got {len(rows_recent)}"
    print(f"[a3-read] read_recent_skills returned {len(rows_all)}/3 (all) and {len(rows_recent)}/2 (1h) ✓")


async def test_a3_summarise_skills_recent(tmp: Path) -> None:
    skills_path = tmp / "skills.jsonl"
    today = date.today()
    rows = [
        {"ts": datetime.now().isoformat(timespec="seconds"),
         "skill": "research", "ok": False, "latency_ms": 1000,
         "tools_used": [], "kwargs_keys": [], "error": "SearXNG 502"},
        {"ts": datetime.now().isoformat(timespec="seconds"),
         "skill": "research", "ok": False, "latency_ms": 1200,
         "tools_used": [], "kwargs_keys": [], "error": "SearXNG 502"},
        {"ts": datetime.now().isoformat(timespec="seconds"),
         "skill": "research", "ok": True, "latency_ms": 800,
         "tools_used": ["searxng"], "kwargs_keys": [], "error": ""},
        {"ts": datetime.now().isoformat(timespec="seconds"),
         "skill": "recall", "ok": True, "latency_ms": 12,
         "tools_used": ["ltm"], "kwargs_keys": [], "error": ""},
    ]
    (tmp / f"skills-{today.isoformat()}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8",
    )
    engine = ReflectionEngine(
        provider=_StubProvider(),  # type: ignore[arg-type]
        timezone="UTC",
        feed_path=tmp / "reflection.jsonl",
        dropped_feed_path=tmp / "reflection_dropped.jsonl",
        skills_feed_path=skills_path,
    )
    summary = engine.summarise_skills_recent(
        since=datetime.now() - timedelta(hours=1),
    )
    assert "research" in summary and "recall" in summary
    r = summary["research"]
    assert r["total"] == 3 and r["failed"] == 2 and r["ok"] == 1
    assert r["fail_rate"] == round(2 / 3, 3)
    assert r["last_error"] == "SearXNG 502"
    assert r["avg_latency_ms"] == round((1000 + 1200 + 800) / 3, 1)
    rc = summary["recall"]
    assert rc["total"] == 1 and rc["failed"] == 0 and rc["fail_rate"] == 0.0
    print(
        f"[a3-summary] research fail_rate={r['fail_rate']:.0%} "
        f"avg_latency={r['avg_latency_ms']}ms — heartbeat-ready ✓"
    )


# ---------------------------------------------------------------------------
# A2 — Brain identity question routes through IdentitySkill
# ---------------------------------------------------------------------------

async def test_a2_try_identity_skill(tmp: Path) -> None:
    """End-to-end-ish test: real SkillRegistry + IdentitySkill + Brain helper."""
    # Late imports so the A4 tests can run even if Brain has a heavy dep tree.
    from core.skills.identity import IdentitySkill
    from core.skills.registry import SkillRegistry

    audit = tmp / "skills.jsonl"
    reg = SkillRegistry(audit_feed=audit, audit_retain_days=7, audit_tz="UTC")
    reg.register(IdentitySkill())

    # Duck-typed SkillContext — IdentitySkill only reads
    # `ctx.soul.render_identity_block()` and `ctx.designation`.
    # `SkillRegistry.invoke` type-hints `ctx: SkillContext` but never
    # introspects the type at runtime; Python's duck typing accepts
    # any object that exposes the attributes a given Skill happens to
    # touch.
    class _StubSoul:
        async def render_identity_block(self) -> str:
            return (
                "**Designation**: Test-Agent\n"
                "**Codename**: TA-1\n"
                "**Creator**: The Test Harness\n"
            )

    class _StubCtx:
        soul = _StubSoul()
        designation = "Test-Agent"

    ctx = _StubCtx()
    # Direct registry call (proves IdentitySkill is wired before we
    # exercise Brain's helper).
    r_name = await reg.invoke("identity", ctx, kind="name")  # type: ignore[arg-type]
    assert r_name.ok, f"identity/name failed: {r_name.error!r}"
    assert "Test-Agent" in (r_name.summary or "")
    r_creator = await reg.invoke("identity", ctx, kind="creator")  # type: ignore[arg-type]
    assert r_creator.ok, f"identity/creator failed: {r_creator.error!r}"
    assert "Test Harness" in (r_creator.summary or "")
    print(f"[a2-registry] IdentitySkill name={r_name.summary!r} creator={r_creator.summary!r} ✓")

    # IdentityResponder indirection. The dispatch helper that used to
    # live on Brain (``_try_identity_skill``) was extracted into
    # ``core/brain/identity_responder.py`` during the v2.0 brain-package
    # split (P1.1). We instantiate the responder directly here — it
    # only needs the registry, the skill context, and the architect
    # name — and exercise the now-public skill dispatcher.
    from core.brain.identity_responder import IdentityResponder

    responder = IdentityResponder(
        skill_registry=reg,
        skill_ctx=ctx,  # type: ignore[arg-type]
        architect_name="Test Architect",
    )
    name = await responder._try_skill(kind="name")
    creator = await responder._try_skill(kind="creator")
    assert name and "Test-Agent" in name
    assert creator and "Test Harness" in creator
    print(f"[a2-helper] IdentityResponder._try_skill name={name!r} creator={creator!r} ✓")

    # Verify the audit feed was written via the rotating writer.
    rotated = list(tmp.glob("skills-*.jsonl"))
    assert rotated, "registry should have rotated skill audit writes"
    lines = rotated[0].read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 4, f"expected ≥4 audit lines (2 reg + 2 helper), got {len(lines)}"
    for line in lines:
        rec = json.loads(line)
        assert rec["skill"] == "identity" and rec["ok"] is True
    print(f"[a2-audit] {len(lines)} identity-skill invocations written to rotated audit ✓")


async def test_a2_fallback_no_registry(tmp: Path) -> None:
    """IdentityResponder helper returns None when registry/ctx are missing.

    The caller (``Brain._cycle`` via
    ``IdentityResponder.try_handle``) then falls back to the inline
    template path.
    """
    from core.brain.identity_responder import IdentityResponder

    responder = IdentityResponder(
        skill_registry=None,
        skill_ctx=None,
        architect_name="Test Architect",
    )
    assert await responder._try_skill(kind="name") is None
    assert await responder._try_skill(kind="creator") is None
    print("[a2-fallback] missing registry → helper returns None (caller falls back) ✓")


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

async def main() -> None:
    # A4 tests get isolated temp dirs to keep retention sweeps independent.
    for name, fn in [
        ("a4-basic", test_a4_basic_append),
        ("a4-filename", test_a4_filename_pattern),
        ("a4-concurrent", test_a4_concurrent_atomic),
        ("a4-retention", test_a4_retention_sweep),
        ("a4-siblings", test_a4_sibling_paths_sorted),
        ("a3-read", test_a3_read_recent_skills_across_rotated),
        ("a3-summary", test_a3_summarise_skills_recent),
        ("a2-skill", test_a2_try_identity_skill),
        ("a2-fallback", test_a2_fallback_no_registry),
    ]:
        with tempfile.TemporaryDirectory(prefix=f"smoke-p31-{name}-") as td:
            await fn(Path(td))

    print("\nALL ROTATION / REFLECTION / IDENTITY SMOKE TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
