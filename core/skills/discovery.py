"""core.skills.discovery — Entry-points-based third-party Skill discovery.

THE PROMISE
-----------
A third-party developer should be able to publish an OpenCrayFish
Skill as a pip-installable package, and that package should be
auto-registered the next time the agent boots. No code changes to
``main.py``. No manual registration. Just::

    pip install opencrayfish-skill-translate

…and on the next boot the operator sees::

    SKILL registered name=translate protocol=skill-protocol/1 ...
    SKILL Registered 8 skill(s): direct_answer, identity, …, translate

HOW IT WORKS
------------
Python's standard ``importlib.metadata.entry_points`` API lets any
installed package declare named hooks under a group. Third-party
packages opt in by adding to their ``pyproject.toml``::

    [project.entry-points."opencrayfish.skills"]
    translate = "opencrayfish_skill_translate:TranslateSkill"

…where the value is a ``module:attr`` import path that resolves to
either a Skill *class* (we instantiate it with zero args) or a
*factory callable* (we call it with zero args, expect a Skill back).

At boot, ``main.py`` calls ``discover_external_skills(registry)``
after the first-party Skills are registered. Each entry-point is:

  1. Loaded (``ep.load()`` imports the module + attribute).
  2. Instantiated if it's a class, called if it's a callable, or
     accepted as-is if it's already a Skill instance.
  3. Passed through the same ``registry.register(skill)`` call the
     first-party Skills use — same manifest resolution, same
     duplicate-name detection, same fail-loud on protocol mismatch.

ISOLATION
---------
A misbehaving third-party Skill must NEVER prevent the agent from
starting. ``discover_external_skills`` wraps each entry-point in a
try/except so a single broken package only loses that one Skill, not
the whole boot. The exception is logged with the entry-point's
``module:attr`` location so the operator can diagnose without
guessing which package broke.

The one exception: a Skill that *successfully loads* but then fails
``bootstrap_validate`` (e.g. declares a missing tool) will trigger
the fail-loud RuntimeError from the registry — that's intentional.
Discovery isolates load-time failures (import errors, missing
dependencies) but trusts the post-load validation to catch
configuration errors that an operator can actually fix.

SECURITY NOTE
-------------
Entry-points discovery executes arbitrary code from any installed
package that registers itself in the ``opencrayfish.skills`` group.
This is the standard Python plug-in security model (the same one
pytest, mypy, black use) — if you don't trust a package, don't
``pip install`` it. The agent does NOT sandbox third-party Skills
today; the ``requires_caps`` manifest field is informational only.
A future hardening pass may add an operator allowlist gating which
package names may register Skills.
"""
from __future__ import annotations

import logging
from importlib.metadata import EntryPoint, entry_points
from typing import Any, Callable

from .base import Skill
from .registry import SkillRegistry

log = logging.getLogger(__name__)

# The entry-point group third-party packages register Skills under.
# Documented in CONTRIBUTING.md / README as the canonical contract.
# Renaming this is a Skill Protocol break — bump SUPPORTED_PROTOCOL_VERSIONS.
SKILL_ENTRY_POINT_GROUP: str = "opencrayfish.skills"


def _instantiate(loaded: Any, ep_name: str) -> Skill | None:
    """Turn whatever ``ep.load()`` returned into a Skill instance.

    Accepts three shapes:
      * Skill instance already — returned as-is.
      * Class — instantiated with zero args.
      * Callable (factory) — called with zero args; result must be
        a Skill instance.

    Returns ``None`` (with a logged warning) if the loaded object is
    none of the above. We don't raise — discovery isolation lives at
    the caller and we want one bad entry-point to just be skipped,
    not interrupt the loop.
    """
    # Class first because classes are also callable; we want
    # zero-arg instantiation over "treat it as a factory".
    if isinstance(loaded, type):
        try:
            return loaded()  # type: ignore[return-value]
        except Exception:
            log.exception(
                "SKILL discovery ep=%s class instantiation failed",
                ep_name,
            )
            return None
    # If the loaded object already looks like a Skill (duck-typed —
    # has name + execute), pass it through.
    if hasattr(loaded, "name") and hasattr(loaded, "execute"):
        return loaded
    # Factory function fallback.
    if callable(loaded):
        try:
            result = loaded()
        except Exception:
            log.exception(
                "SKILL discovery ep=%s factory call failed",
                ep_name,
            )
            return None
        if hasattr(result, "name") and hasattr(result, "execute"):
            return result
        log.warning(
            "SKILL discovery ep=%s factory returned %s, not a Skill",
            ep_name, type(result).__name__,
        )
        return None
    log.warning(
        "SKILL discovery ep=%s loaded object %s is not a Skill / class / "
        "callable — skipping",
        ep_name, type(loaded).__name__,
    )
    return None


def discover_external_skills(
    registry: SkillRegistry,
    *,
    group: str = SKILL_ENTRY_POINT_GROUP,
    filter_fn: Callable[[EntryPoint], bool] | None = None,
) -> list[str]:
    """Walk installed packages for Skills and register them.

    Should be called from ``main.py`` AFTER the first-party Skills
    are registered, so a third-party package can't accidentally
    shadow a built-in by registering the same name first (the
    registry's duplicate-name check will fail loud at the third-
    party registration, which is the right blast radius).

    Args:
        registry: The live SkillRegistry instance to register into.
        group: Entry-point group name. Override only in tests.
        filter_fn: Optional predicate ``(entry_point) -> bool`` for
            operator-side filtering (e.g. an allowlist of trusted
            package names). When ``None`` every entry-point in the
            group is loaded.

    Returns:
        List of registered Skill names. Useful for logging /
        dashboard summaries. Skills that failed to load are NOT in
        this list (the failure is logged at WARNING level).
    """
    registered: list[str] = []

    # importlib.metadata.entry_points returns an EntryPoints view in
    # 3.10+; on older Pythons it returned a dict. We're on 3.13 so
    # the modern API is safe.
    try:
        eps = entry_points(group=group)
    except Exception:
        log.exception(
            "SKILL discovery: entry_points(group=%r) lookup failed",
            group,
        )
        return registered

    for ep in eps:
        if filter_fn is not None and not filter_fn(ep):
            log.info(
                "SKILL discovery ep=%s skipped by filter",
                ep.name,
            )
            continue
        # Each entry-point is isolated — a broken package only loses
        # ITS skill, not the whole boot.
        try:
            loaded = ep.load()
        except Exception:
            log.exception(
                "SKILL discovery ep=%s (%s) load failed — skipping",
                ep.name, ep.value,
            )
            continue
        skill = _instantiate(loaded, ep.name)
        if skill is None:
            continue
        try:
            registry.register(skill)
        except ValueError as exc:
            # Duplicate name, malformed manifest, unsupported protocol.
            # Log and skip — the operator sees the message and can
            # ``pip uninstall`` the offending package.
            log.warning(
                "SKILL discovery ep=%s rejected by registry: %s",
                ep.name, exc,
            )
            continue
        registered.append(getattr(skill, "name", ep.name))

    if registered:
        log.info(
            "SKILL discovery group=%s registered %d external skill(s): %s",
            group, len(registered), ", ".join(registered),
        )
    else:
        log.info(
            "SKILL discovery group=%s found 0 external skills (this is "
            "fine — first-party Skills are still registered)",
            group,
        )
    return registered
