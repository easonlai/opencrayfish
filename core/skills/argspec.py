"""core.skills.argspec \u2014 lightweight runtime validation of args_schema.

PURPOSE
-------
Every Skill and Tool declares an ``args_schema`` in its manifest:

    {"query": {"type": "string", "required": True, "desc": "..."},
     "limit": {"type": "int",    "required": False, "default": 5}}

Without runtime validation, a SLM passing ``"42"`` (string) for an
``int`` argument crashes the Skill body \u2014 the failure surfaces as
``SkillResult(ok=False, error="TypeError: ...")`` which the SLM
treats as a silent fail and re-picks the same verb next turn. Three
hours into a deployment this looks like a regression but it's just
a schema mismatch.

This module is the boundary check: ``validate_args(schema, kwargs)``
returns ``(normalized_kwargs, errors)`` where:

  * ``normalized_kwargs`` has defaults injected and coerced types where
    a SAFE coercion exists (``"42"`` \u2192 ``42``; ``"true"`` \u2192 ``True``).
  * ``errors`` is a list of human-readable strings \u2014 caller turns each
    into ``SkillResult(ok=False, error=...)`` BEFORE dispatch.

DESIGN CHOICES
--------------
* **Zero dependencies.** ~150 LOC of stdlib. Keeps OpenCrayFish
  edge-native. Adding ``pydantic`` would double the install size on
  the Pi 5 for almost no win at this validation depth.
* **Coerce when safe, fail loud when not.** The SLM emits text \u2014
  string-to-int / string-to-bool coercion catches 90% of the
  upstream flakiness. Anything ambiguous (``"yes"`` for bool? we
  don't try) is rejected with a clear error.
* **Unknown kwargs PASS but are logged.** Third-party Skills may
  accept ``**kwargs`` and read extra hints we don't know about \u2014
  killing the call on extras would break that. We surface unknowns
  in the ``meta`` field of the Result so the operator can audit.
* **Per-arg ``required`` defaults to False** unless explicitly set.
  Matches the existing schema convention in
  ``core/skills/research.py``.

INTEGRATION
-----------
The hook points are:
  * ``SkillRegistry.invoke()`` \u2014 BEFORE the ``await skill.execute()``
    call. On validation failure, return ``SkillResult(ok=False, ...)``
    without ever calling the Skill body.
  * ``ToolRegistry.call()`` \u2014 same pattern, returning
    ``ToolResult(ok=False, ...)`` before the Tool body runs.

Both registries already have try/except wrappers around dispatch;
the argspec check sits OUTSIDE that wrapper so the failure is a
clean ``ok=False`` rather than an exception trace.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


# Mapping from schema ``type`` token to the Python types accepted
# WITHOUT coercion. Coercion (e.g. str \u2192 int) is handled separately
# below so we can log when it happens (useful for catching upstream
# bugs in SLM PLAN output).
_TYPE_NATIVE: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "str": (str,),
    "int": (int,),                    # NB: bool is a subclass of int;
    "integer": (int,),                # we exclude it explicitly below
    "float": (float, int),            # int auto-promotes to float, OK
    "number": (float, int),
    "bool": (bool,),
    "boolean": (bool,),
    "list": (list, tuple),
    "array": (list, tuple),
    "dict": (dict,),
    "object": (dict,),
    "any": (object,),                 # ``"any"`` opts out of typing
}


def _coerce(token: str, value: Any) -> tuple[Any, bool, str | None]:
    """Try to coerce ``value`` into the requested type.

    Returns ``(coerced_value, was_coerced, error)``. When coercion
    succeeds, ``error`` is None. When the value is already a native
    instance, ``was_coerced`` is False.
    """
    accepted = _TYPE_NATIVE.get(token)
    if accepted is None:
        # Unknown type token \u2014 don't fail, just accept the value as-is
        # so third-party schemas with custom tokens still work.
        return value, False, None

    # Reject bool when an int was requested (bool subclasses int).
    if token in ("int", "integer") and isinstance(value, bool):
        return value, False, (
            f"expected {token}, got bool ({value!r})"
        )

    if isinstance(value, accepted):
        return value, False, None

    # Coercion attempts \u2014 only when it's UNAMBIGUOUS.
    if token in ("int", "integer") and isinstance(value, str):
        try:
            return int(value.strip()), True, None
        except ValueError:
            return value, False, f"cannot coerce {value!r} to int"
    if token in ("float", "number") and isinstance(value, str):
        try:
            return float(value.strip()), True, None
        except ValueError:
            return value, False, f"cannot coerce {value!r} to float"
    if token in ("bool", "boolean") and isinstance(value, str):
        v = value.strip().lower()
        if v in ("true", "1", "yes"):
            return True, True, None
        if v in ("false", "0", "no"):
            return False, True, None
        return value, False, (
            f"cannot coerce {value!r} to bool "
            "(accepted: true/false/1/0/yes/no)"
        )
    if token in ("string", "str") and isinstance(value, (int, float)):
        # Only coerce numerics to strings, NOT bool/None/list/dict \u2014
        # those would silently mask programming errors.
        return str(value), True, None

    return value, False, (
        f"expected {token}, got {type(value).__name__} ({value!r})"
    )


def validate_args(
    schema: dict[str, dict[str, Any]] | None,
    kwargs: dict[str, Any],
) -> tuple[dict[str, Any], list[str], list[str]]:
    """Validate ``kwargs`` against ``schema``.

    Args:
        schema: The ``args_schema`` dict from a Skill/Tool manifest.
            ``None`` / empty dict is treated as "no schema" \u2014 kwargs
            pass through unchanged.
        kwargs: The keyword arguments the registry is about to pass
            to ``execute()`` / ``call()``.

    Returns:
        Tuple ``(normalized_kwargs, errors, unknowns)``:
          * ``normalized_kwargs``: ``kwargs`` with defaults injected
            and SAFE type coercions applied.
          * ``errors``: list of human-readable validation failures.
            Empty list means the dispatch can proceed.
          * ``unknowns``: list of kwarg keys NOT in the schema.
            Pass-through (no error) but surfaced for audit.
    """
    if not schema:
        return dict(kwargs), [], []

    normalized: dict[str, Any] = {}
    errors: list[str] = []
    # Track which schema keys we actually processed so a malformed
    # entry (skipped below) lets the caller's kwarg pass through
    # instead of being silently dropped.
    handled_keys: set[str] = set()

    for arg_name, spec in schema.items():
        if not isinstance(spec, dict):
            # Defensive — a malformed schema shouldn't crash dispatch;
            # log and skip the constraint. Leave the kwarg (if any)
            # to be carried through by the unknowns pass below.
            log.warning(
                "argspec: schema entry %r is not a dict (%r); skipping",
                arg_name, type(spec).__name__,
            )
            continue

        handled_keys.add(arg_name)
        type_token = str(spec.get("type", "any")).lower()
        required = bool(spec.get("required", False))
        has_default = "default" in spec

        if arg_name in kwargs:
            value = kwargs[arg_name]
            coerced, was_coerced, err = _coerce(type_token, value)
            if err:
                errors.append(f"arg {arg_name!r}: {err}")
                continue
            if was_coerced:
                log.debug(
                    "argspec: coerced %r %s->%s for arg=%r",
                    value, type(value).__name__,
                    type(coerced).__name__, arg_name,
                )
            normalized[arg_name] = coerced
        elif has_default:
            normalized[arg_name] = spec["default"]
        elif required:
            errors.append(f"arg {arg_name!r}: missing required value")
        # else: optional, no default — not present in normalized output

    # Carry through any kwarg the validator didn't actually handle —
    # both truly unknown kwargs (third-party Skills may accept
    # **kwargs) AND kwargs whose schema entry was malformed and
    # skipped above.
    unknowns = [k for k in kwargs if k not in handled_keys]
    for k in unknowns:
        if k not in normalized:
            normalized[k] = kwargs[k]

    return normalized, errors, unknowns
