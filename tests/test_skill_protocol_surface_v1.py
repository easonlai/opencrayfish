"""Frozen-snapshot test of the published Skill + Tool Protocol surface.

CONTRACT
--------
Every name in the ``EXPECTED_*`` reference sets below is part of the
**published v1 protocol surface** that third-party Skill/Tool authors
write against. Any addition, removal, or rename of one of these names
will fail this test \u2014 forcing the developer making the change to
either:

  (a) restore the surface (most accidental drift is caught this way),
   or
  (b) update both this test AND ``SUPPORTED_PROTOCOL_VERSIONS`` /
      ``SUPPORTED_TOOL_PROTOCOL_VERSIONS`` (a v2 bump), AND add a
      migration note to README \u00a712 explaining the break.

This is the **only** test that may be modified to add a field; every
other test in the suite is shape-agnostic. Treat changes here as
public-API changes.

PAIRS WITH
----------
* ``tests/test_example_echo_integration.py`` \u2014 semantic regression
  guard (the reference plugin must keep working).
* ``tests/test_skill_context.py`` \u2014 dataclass-fields shape guard
  for SkillContext specifically.

This test is intentionally redundant with those: belt + braces. The
surface test catches *additions* the other tests can't see; the
integration tests catch *behavioral* breaks the surface test can't see.
"""
from __future__ import annotations

import dataclasses

from core.skills import (
    DEFAULT_PROTOCOL_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
    Skill,
    SkillContext,
    SkillManifest,
    SkillResult,
)
from tools import (
    DEFAULT_TOOL_PROTOCOL_VERSION,
    SUPPORTED_TOOL_PROTOCOL_VERSIONS,
    ToolManifest,
)

# ---------------------------------------------------------------------------
# Skill Protocol v1 \u2014 frozen reference
# ---------------------------------------------------------------------------


# Every dataclass field of ``SkillResult`` that plugins write to or
# read from. Renaming/removing one breaks every plugin in the wild.
EXPECTED_SKILL_RESULT_FIELDS: frozenset[str] = frozenset({
    "ok",
    "summary",
    "evidence",
    "tools_used",
    "latency_ms",
    "meta",
    "error",
})


# Every dataclass field of ``SkillContext`` that plugins read from in
# ``execute(ctx, **kwargs)``. Adding a new field is always safe
# (backward-compatible); renaming/removing requires a v2 bump.
EXPECTED_SKILL_CONTEXT_FIELDS: frozenset[str] = frozenset({
    "tools",
    "soul",
    "stm",
    "monitor",
    "provider",
    "archive_path",
    "designation",
    "architect_name",
    "architect_honorific",
    "extras",
    "plugins_config",
})


# Every dataclass field of ``SkillManifest`` that plugins set. This
# is the largest surface and the one most likely to drift; freeze
# it explicitly so a careless field addition forces a deliberate
# decision (v2 bump or back-compat synthesis in ``resolve_manifest``).
EXPECTED_SKILL_MANIFEST_FIELDS: frozenset[str] = frozenset({
    "name",
    "description",
    "trigger_hints",
    "args_schema",
    "cost_tier",
    "requires_network",
    "requires_tools",
    "requires_caps",
    "side_effects",
    "requires_confirmation",
    "plan_verb",
    "plan_arg_hint",
    "plan_guidance",
    "plan_example",
    "compat_version",
    "extras",
})


# The ``Skill`` Protocol's annotated members \u2014 names a third-party
# class MUST expose for ``isinstance(obj, Skill)`` to succeed (the
# Protocol is ``@runtime_checkable``). ``execute`` is intentionally
# NOT in this set because Protocol.runtime_checkable only inspects
# annotations, not methods \u2014 we test the method shape separately
# below via ``hasattr``.
EXPECTED_SKILL_PROTOCOL_ANNOTATIONS: frozenset[str] = frozenset({
    "name",
    "description",
    "trigger_hints",
    "args_schema",
    "cost_tier",
    "requires_network",
    "side_effects",
    "requires_confirmation",
})


# ---------------------------------------------------------------------------
# Tool Protocol v1 \u2014 frozen reference
# ---------------------------------------------------------------------------


EXPECTED_TOOL_MANIFEST_FIELDS: frozenset[str] = frozenset({
    "name",
    "description",
    "compat_version",
    "args_schema",
    "side_effects",
    "requires_confirmation",
    "requires_caps",
    "config_key",
    "extras",
})


# ---------------------------------------------------------------------------
# The tests
# ---------------------------------------------------------------------------


def _field_names(cls: type) -> frozenset[str]:
    return frozenset(f.name for f in dataclasses.fields(cls))


def test_skill_result_fields_frozen() -> None:
    assert _field_names(SkillResult) == EXPECTED_SKILL_RESULT_FIELDS, (
        "SkillResult fields drifted. If this is intentional, update "
        "EXPECTED_SKILL_RESULT_FIELDS in this file AND bump "
        "SUPPORTED_PROTOCOL_VERSIONS in core/skills/manifest.py AND "
        "document the migration in README \u00a712."
    )


def test_skill_context_fields_frozen() -> None:
    assert _field_names(SkillContext) == EXPECTED_SKILL_CONTEXT_FIELDS, (
        "SkillContext fields drifted. Adding a new field IS allowed "
        "(backward-compatible) \u2014 just append it to "
        "EXPECTED_SKILL_CONTEXT_FIELDS. Renaming/removing requires a "
        "v2 protocol bump."
    )


def test_skill_manifest_fields_frozen() -> None:
    assert _field_names(SkillManifest) == EXPECTED_SKILL_MANIFEST_FIELDS, (
        "SkillManifest fields drifted. If this is intentional, update "
        "EXPECTED_SKILL_MANIFEST_FIELDS in this file AND bump "
        "SUPPORTED_PROTOCOL_VERSIONS in core/skills/manifest.py. "
        "Back-compat synthesis in resolve_manifest() can hide a removed "
        "field for one release; document the deprecation."
    )


def test_skill_protocol_annotations_frozen() -> None:
    # Filter to public names only (skip dunders / private helpers).
    annotated = frozenset(
        name for name in Skill.__annotations__
        if not name.startswith("_")
    )
    assert annotated == EXPECTED_SKILL_PROTOCOL_ANNOTATIONS, (
        "Skill Protocol annotated members drifted. This breaks "
        "isinstance(obj, Skill) for every third-party plugin. If "
        "intentional, bump SUPPORTED_PROTOCOL_VERSIONS."
    )


def test_skill_protocol_execute_method_present() -> None:
    # Protocol.__call__ checks annotations; we additionally guard
    # the method-shape surface here so ``execute`` can't be
    # accidentally renamed to ``call`` (the Tool-side verb).
    assert hasattr(Skill, "execute"), (
        "Skill Protocol must expose ``execute(ctx, **kwargs)``. "
        "If this is renamed, it's a v2 protocol break."
    )


def test_tool_manifest_fields_frozen() -> None:
    assert _field_names(ToolManifest) == EXPECTED_TOOL_MANIFEST_FIELDS, (
        "ToolManifest fields drifted. If intentional, update "
        "EXPECTED_TOOL_MANIFEST_FIELDS AND bump "
        "SUPPORTED_TOOL_PROTOCOL_VERSIONS in tools/manifest.py."
    )


def test_default_protocol_versions_present_in_supported() -> None:
    # Sanity: the default MUST be in the supported set, or
    # bootstrap_validate will reject every freshly-stamped manifest.
    assert DEFAULT_PROTOCOL_VERSION in SUPPORTED_PROTOCOL_VERSIONS
    assert DEFAULT_TOOL_PROTOCOL_VERSION in SUPPORTED_TOOL_PROTOCOL_VERSIONS


def test_supported_protocol_versions_v1_present() -> None:
    # The current published version is v1; this test fails the day
    # someone removes v1 from the supported set without a deprecation
    # cycle.
    assert "skill-protocol/1" in SUPPORTED_PROTOCOL_VERSIONS
    assert "tool-protocol/1" in SUPPORTED_TOOL_PROTOCOL_VERSIONS
