"""Tests for the `opencrayfish` CLI (skill new + skill validate)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from core.cli import _cmd_new, _cmd_validate, _render_template, main

# ---------------------------------------------------------------------------
# `skill new` — scaffolding
# ---------------------------------------------------------------------------


def test_render_template_has_expected_files() -> None:
    files = _render_template("translate")
    keys = {str(p) for p in files.keys()}
    assert "pyproject.toml" in keys
    assert "opencrayfish_skill_translate/__init__.py" in keys
    assert "README.md" in keys
    assert "tests/test_translate.py" in keys

    # pyproject must declare the entry-point group.
    pyp = files[Path("pyproject.toml")]
    assert '[project.entry-points."opencrayfish.skills"]' in pyp
    assert "translate =" in pyp

    # __init__.py must declare a SkillManifest with the right name.
    init = files[Path("opencrayfish_skill_translate/__init__.py")]
    assert "class TranslateSkill" in init
    assert 'name="translate"' in init
    assert "plan_verb=\"TRANSLATE\"" in init


def test_cmd_new_writes_files(tmp_path: Path) -> None:
    import argparse

    args = argparse.Namespace(
        name="weather",
        dest=str(tmp_path),
        force=False,
    )
    rc = _cmd_new(args)
    assert rc == 0
    target = tmp_path / "opencrayfish-skill-weather"
    assert target.is_dir()
    assert (target / "pyproject.toml").exists()
    assert (target / "opencrayfish_skill_weather" / "__init__.py").exists()
    assert (target / "README.md").exists()
    assert (target / "tests" / "test_weather.py").exists()


def test_cmd_new_rejects_existing_without_force(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import argparse

    target = tmp_path / "opencrayfish-skill-foo"
    target.mkdir()
    args = argparse.Namespace(name="foo", dest=str(tmp_path), force=False)
    rc = _cmd_new(args)
    assert rc == 2
    captured = capsys.readouterr()
    assert "already exists" in captured.err


def test_cmd_new_rejects_bad_name(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    import argparse

    args = argparse.Namespace(name="Bad-Name", dest=str(tmp_path), force=False)
    rc = _cmd_new(args)
    assert rc == 2
    captured = capsys.readouterr()
    assert "must match" in captured.err


# ---------------------------------------------------------------------------
# `skill validate` — import + manifest checking
# ---------------------------------------------------------------------------


def test_cmd_validate_happy_path(capsys: pytest.CaptureFixture[str]) -> None:
    import argparse

    # The first-party ResearchSkill is a fully-manifest-native Skill.
    args = argparse.Namespace(target="core.skills.research:ResearchSkill")
    rc = _cmd_validate(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "resolved manifest for 'research'" in out
    assert "plan_verb      = 'SEARCH'" in out


def test_cmd_validate_rejects_bad_spec(capsys: pytest.CaptureFixture[str]) -> None:
    import argparse

    args = argparse.Namespace(target="no_colon_in_target")
    rc = _cmd_validate(args)
    assert rc == 2


def test_cmd_validate_rejects_missing_module(capsys: pytest.CaptureFixture[str]) -> None:
    import argparse

    args = argparse.Namespace(target="this_module_does_not_exist:Foo")
    rc = _cmd_validate(args)
    assert rc == 3


# ---------------------------------------------------------------------------
# End-to-end: invoke via `python -m core.cli`
# ---------------------------------------------------------------------------


def test_main_dispatches_skill_new(tmp_path: Path) -> None:
    rc = main(["skill", "new", "demo", "--dest", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / "opencrayfish-skill-demo" / "pyproject.toml").exists()


def test_invoked_as_module(tmp_path: Path) -> None:
    """`python -m core.cli skill new ...` should work end-to-end."""
    result = subprocess.run(
        [sys.executable, "-m", "core.cli", "skill", "new", "fromtest",
         "--dest", str(tmp_path)],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parent.parent),
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "opencrayfish-skill-fromtest").exists()
