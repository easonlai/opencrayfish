"""Smoke test for the dashboard's rotation-aware JSONL readers.

The dashboard reads four rotated feeds (skills, deliberation, reflection,
reflections-since) that previously pointed at the legacy un-rotated
`state/<feed>.jsonl`. After the Phase 3.1 rotation cutover the writers
produce `state/<feed>-YYYY-MM-DD.jsonl` siblings instead, and the
dashboard MUST fan its reads across them (plus any legacy file an older
operator left behind) without picking up foreign files in the same
directory.

This script validates the three helpers added to `ui/dashboard.py`:
  * `_rotated_jsonl_paths(base)` — discovery + filename guard
  * `_rotated_jsonl_tail(base, limit)` — newest-N reader
  * `_rotated_jsonl_all(base)` — full scan for time-bounded slices

Run:
    python scripts/smoke_dashboard_rotation.py
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

# Import the dashboard module by file path so we don't trigger Streamlit's
# script-runner. The streamlit warning about "missing ScriptRunContext"
# is expected and harmless when we use the module purely as a library.
_spec = importlib.util.spec_from_file_location(
    "_dash_smoke_mod", _REPO_ROOT / "ui" / "dashboard.py",
)
assert _spec is not None and _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

rotated_paths = _mod._rotated_jsonl_paths
rotated_tail = _mod._rotated_jsonl_tail
rotated_all = _mod._rotated_jsonl_all


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        base = tmp / "skills.jsonl"
        # Three rotated siblings — RotatingJsonlWriter's actual filename shape.
        (tmp / "skills-2026-05-15.jsonl").write_text(
            "\n".join(
                json.dumps(
                    {"ts": f"2026-05-15T10:0{i}:00+08:00", "skill": "A", "ok": True}
                )
                for i in range(3)
            )
            + "\n",
            encoding="utf-8",
        )
        (tmp / "skills-2026-05-16.jsonl").write_text(
            "\n".join(
                json.dumps(
                    {"ts": f"2026-05-16T10:0{i}:00+08:00", "skill": "B", "ok": True}
                )
                for i in range(2)
            )
            + "\n",
            encoding="utf-8",
        )
        (tmp / "skills-2026-05-17.jsonl").write_text(
            "\n".join(
                json.dumps(
                    {"ts": f"2026-05-17T10:0{i}:00+08:00", "skill": "C", "ok": True}
                )
                for i in range(4)
            )
            + "\n",
            encoding="utf-8",
        )
        # Legacy un-rotated file — must still be picked up.
        base.write_text(
            json.dumps(
                {"ts": "2026-05-17T11:00:00+08:00", "skill": "legacy", "ok": True}
            )
            + "\n",
            encoding="utf-8",
        )
        # Foreign file we MUST NEVER pick up.
        (tmp / "skills-FOREIGN.jsonl").write_text("garbage\n", encoding="utf-8")
        # JSON inventory file with a similar stem — also must be ignored.
        (tmp / "skills.json").write_text("{}", encoding="utf-8")

        paths = rotated_paths(base)
        names = [p.name for p in paths]
        assert "skills-2026-05-15.jsonl" in names, f"missing oldest, got {names}"
        assert "skills-2026-05-16.jsonl" in names, f"missing 5/16, got {names}"
        assert "skills-2026-05-17.jsonl" in names, f"missing 5/17, got {names}"
        assert "skills.jsonl" in names, "legacy un-rotated file should be included"
        assert "skills-FOREIGN.jsonl" not in names, "foreign file leaked in"
        assert "skills.json" not in names, "JSON inventory leaked in"
        assert names[0] == "skills-2026-05-15.jsonl", (
            f"oldest rotated must come first, got {names}"
        )
        assert names[-1] == "skills.jsonl", (
            f"legacy file must come last so its rows count as newest, got {names}"
        )
        print("[rotated_paths] order + filename guard ✓")

        tail = rotated_tail(base, limit=5)
        assert len(tail) == 5, f"expected 5 tail records, got {len(tail)}"
        assert tail[-1]["skill"] == "legacy", (
            f"newest record must be the legacy line, got {tail[-1]}"
        )
        print("[rotated_tail] limit + chronological order ✓")

        # limit larger than total → returns everything in chronological order
        full = rotated_tail(base, limit=999)
        assert len(full) == 3 + 2 + 4 + 1, (
            f"expected all 10 records, got {len(full)}"
        )
        print("[rotated_tail] oversized limit returns all records ✓")

        everything = rotated_all(base)
        assert len(everything) == 3 + 2 + 4 + 1, (
            f"expected 10 across all siblings, got {len(everything)}"
        )
        print("[rotated_all] full scan across siblings + legacy ✓")

        # Empty base → empty results, no crash.
        empty_base = tmp / "doesnotexist.jsonl"
        assert rotated_paths(empty_base) == []
        assert rotated_tail(empty_base, 10) == []
        assert rotated_all(empty_base) == []
        print("[rotated_*] missing base → empty (no crash) ✓")

    print("\nALL DASHBOARD ROTATION SMOKE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
