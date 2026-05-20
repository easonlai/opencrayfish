"""Integration test for examples/opencrayfish-skill-echo.

This is the **regression guard** for the reference plugin. It
imports the plugin directly (no ``pip install`` in CI \u2014 the example
lives in the workspace and is on sys.path via the path injection
below), resolves its manifest, dry-registers it through
``SkillRegistry``, and runs ``bootstrap_validate``. Any change to
the published Skill Protocol that breaks the published example
fails here at PR-review time, NOT after a third-party author has
already shipped a release built against the broken example.

Pair this with ``tests/test_skill_protocol_surface_v1.py`` (which
freezes the protocol's *shape*): together they catch both
syntactic surface drift (surface test) AND semantic drift in how
the example was written to use that surface (this test).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Inject the example package onto sys.path so we can import it
# without requiring a pip install in CI. The path is workspace-
# anchored \u2014 conftest.py at the repo root already adds the repo
# itself, we add just the example package directory here.
_EXAMPLE_ROOT = (
    Path(__file__).resolve().parents[1]
    / "examples"
    / "opencrayfish-skill-echo"
)


@pytest.fixture(autouse=True)
def _add_example_to_syspath():
    path_str = str(_EXAMPLE_ROOT)
    inserted = False
    if path_str not in sys.path:
        sys.path.insert(0, path_str)
        inserted = True
    try:
        yield
    finally:
        if inserted:
            sys.path.remove(path_str)
        # Forget the import so a re-run starts clean.
        sys.modules.pop("opencrayfish_skill_echo", None)


def test_example_echo_manifest_resolves() -> None:
    from opencrayfish_skill_echo import EchoSkill

    from core.skills import resolve_manifest

    m = resolve_manifest(EchoSkill())
    assert m.name == "echo"
    assert m.compat_version == "skill-protocol/1"
    assert m.plan_verb == "ECHO"


def test_example_echo_registers_and_validates() -> None:
    from opencrayfish_skill_echo import EchoSkill

    from core.skills import SkillRegistry

    reg = SkillRegistry()
    reg.register(EchoSkill())
    assert "echo" in reg.names()
    # tool_registry=None \u2014 echo declares no required tools so the
    # tool-presence check is moot; bootstrap_validate still cross-
    # checks protocol version + verb uniqueness.
    problems = reg.bootstrap_validate(tool_registry=None, strict=False)
    assert problems == [], f"echo plugin failed bootstrap_validate: {problems}"


async def test_example_echo_executes() -> None:
    """Smoke test the actual Skill verb so the reference plugin can't
    silently rot into a non-functional stub."""
    from opencrayfish_skill_echo import EchoSkill

    skill = EchoSkill()
    # SkillContext is intentionally NOT constructed here \u2014 the echo
    # skill never reads from it. Pass an object that fails loud if
    # touched so the reference example provably never depends on
    # ctx fields (drift-prevention).
    result_ok = await skill.execute(ctx=object(), query="hello")
    assert result_ok.ok is True
    assert result_ok.summary == "echo: hello"
    assert result_ok.evidence == []

    result_missing = await skill.execute(ctx=object(), query="")
    assert result_missing.ok is False
    assert "query" in (result_missing.error or "")
