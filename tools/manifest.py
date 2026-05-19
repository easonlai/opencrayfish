"""tools.manifest — Declarative capability descriptor for Tools.

This module mirrors ``core.skills.manifest`` exactly, one layer down.
Where ``SkillManifest`` describes a *capability* the cognitive loop
can pick, ``ToolManifest`` describes a *mechanism* a Skill can call
via ``ctx.tools.call(...)``. The two together let the framework grow
new Skills + new Tools as independent, pip-installable artefacts.

WHY THIS EXISTS
---------------
Until now a Tool carried its plug-in metadata as scattered class
attributes (``name``, ``description``, ``args_schema``, …). That
matched the v0 era when Tools were registered by hand in ``main.py``.
For OpenCrayFish to become a *framework* — third-party developers
shipping Tools as their own pip packages — the same metadata must be:

  * **Unified** — one canonical place for the registry to read.
  * **Versioned** — a Tool written against the v1 Tool Protocol must
    declare so, and the registry must refuse one written against a
    future revision it doesn't know how to load.
  * **Dependency-aware** — a Tool that needs ``cfg.plugins.<name>``
    configuration, an outbound network capability, or filesystem
    write access should be able to declare so at boot; misconfigured
    Tools die loud before connectors spin up.

``ToolManifest`` is the dataclass that gathers all of the above.
``resolve_tool_manifest`` is the back-compat bridge — it reads the
scattered class attributes that today's Tools already expose and
produces an equivalent manifest. Existing Tools DO NOT need to change
to keep working; only Tools that want to use the new fields
(``compat_version``, ``requires_caps``, ``config_key``) need to add
an explicit ``manifest`` class attribute.

DESIGN PRINCIPLES (mirror SkillManifest)
----------------------------------------
* **Frozen + slotted.** Manifests are immutable contracts.
* **No runtime behaviour here.** A manifest is *pure data*. All
  policy (dispatch, latency stamping) stays in ``registry.py``.
* **Optional fields default to backwards-compatible values.** A Tool
  that doesn't declare ``requires_caps`` simply contributes nothing
  to the boot-time capability check — legacy behaviour preserved.
* **Validation is split.** Static checks (well-formed name, valid
  args_schema shape) live in ``__post_init__``. Cross-tool checks
  (duplicate names, capability tokens) live in
  ``ToolRegistry.bootstrap_validate`` because they need the full
  registry in view.

VERSIONING POLICY
-----------------
``compat_version`` declares which Tool Protocol revision the Tool
was written against. The current core supports revisions in
``SUPPORTED_TOOL_PROTOCOL_VERSIONS``. A Tool whose ``compat_version``
is not in this set is refused at registration — fail-loud so the
operator can either upgrade the Tool or pin the core. The framework
promises to keep the most recent two majors supported.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# Tool Protocol revisions the running core knows how to load. Bump
# the set AND add a migration note in CONTRIBUTING.md whenever a
# breaking change ships in the Tool surface. Convention mirrors
# SkillManifest: ``"tool-protocol/N"``.
SUPPORTED_TOOL_PROTOCOL_VERSIONS: frozenset[str] = frozenset({
    "tool-protocol/1",
})

# Default protocol the back-compat synthesizer stamps onto a Tool
# that doesn't expose an explicit ``manifest`` — i.e. every
# first-party Tool written before manifests existed.
DEFAULT_TOOL_PROTOCOL_VERSION: str = "tool-protocol/1"


# Reserved capability tokens that the registry knows how to interpret
# when validating a Tool's ``requires_caps`` list. Unknown tokens are
# allowed at registration but logged so third-party authors can ship
# their own conventions. The well-known set is documented here so
# contributors have a north star.
WELL_KNOWN_TOOL_CAPABILITIES: frozenset[str] = frozenset({
    "network.outbound",  # Tool makes outbound HTTP / DNS calls.
    "filesystem.read",   # Tool reads files outside its own state dir.
    "filesystem.write",  # Tool writes files outside its own state dir.
    "gpio",              # Tool drives GPIO pins (Pi-only).
    "actuator",          # Tool drives a physical actuator.
    "subprocess",        # Tool spawns subprocesses.
})


@dataclass(frozen=True, slots=True)
class ToolManifest:
    """The declarative contract between a Tool and the core.

    Every field is part of the *plug-in surface*: adding, removing,
    or changing the semantics of any field is a Tool Protocol break
    and MUST bump ``SUPPORTED_TOOL_PROTOCOL_VERSIONS``. Don't add
    fields here for convenience — extend the optional ``extras``
    map instead.
    """

    # -- Identity -------------------------------------------------------------

    name: str
    """Registry key. Lowercase snake_case. Must be unique per process.
    Convention: verb-y noun (``web_search``, ``archive_read``,
    ``http_get``, ``home_assistant_call``). The registry refuses
    duplicates at boot."""

    description: str
    """One-line human + SLM-readable purpose. Surfaced in
    ``ToolRegistry.available_for_prompt()`` and on the dashboard's
    Tool inventory panel. Keep ≤ 60 tokens."""

    # -- Protocol versioning --------------------------------------------------

    compat_version: str = DEFAULT_TOOL_PROTOCOL_VERSION
    """Tool Protocol revision this Tool was written against. Must be
    one of ``SUPPORTED_TOOL_PROTOCOL_VERSIONS`` at registration time."""

    # -- Argument surface -----------------------------------------------------

    args_schema: dict[str, dict[str, Any]] = field(default_factory=dict)
    """JSON-Schema-ish descriptor of ``call(...)`` kwargs. Each entry
    is itself a dict with optional keys ``type``, ``required``,
    ``default``, ``desc``. Rendered into the prompt surface and
    used by future arg-validation passes."""

    # -- Safety + cost --------------------------------------------------------

    side_effects: bool = False
    """True if the Tool mutates external state (sends a message,
    toggles a switch, writes a shared file). Drives the dashboard
    chip and a future Architect-ack prompt."""

    requires_confirmation: bool = False
    """True if the Tool should not run without an explicit
    Architect ack. Reserved for actuator Tools. Not enforced by
    the registry today; the future ack-flow gates here."""

    # -- Dependencies + capability surface ------------------------------------

    requires_caps: tuple[str, ...] = ()
    """Capability tokens this Tool exercises (see
    ``WELL_KNOWN_TOOL_CAPABILITIES``). Informational today; the
    registry logs unknown tokens at boot. A future sandbox layer
    may gate Tools against an operator-configured allowlist."""

    config_key: str | None = None
    """Optional ``cfg.plugins.<key>`` namespace this Tool reads at
    construct time. The registry's ``bootstrap_validate`` warns if
    a Tool declares a config_key that the live ``cfg.plugins`` map
    doesn't contain — third-party Tools shipping required config
    surface that gap at boot, not at first call."""

    # -- Extension slot -------------------------------------------------------

    extras: dict[str, Any] = field(default_factory=dict)
    """Free-form key/value pairs for fields not yet stable enough
    to warrant their own typed slot. When a key proves useful
    across multiple Tools it should graduate to a real field +
    protocol bump."""

    # -- Static validation ---------------------------------------------------

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError(
                f"ToolManifest.name must be a non-empty string, got {self.name!r}"
            )
        if any(ch.isspace() for ch in self.name):
            raise ValueError(
                f"ToolManifest.name must not contain whitespace: {self.name!r}"
            )
        if not isinstance(self.description, str) or not self.description.strip():
            raise ValueError(
                f"ToolManifest.description must be a non-empty string "
                f"for tool {self.name!r}"
            )
        if self.compat_version not in SUPPORTED_TOOL_PROTOCOL_VERSIONS:
            raise ValueError(
                f"ToolManifest({self.name!r}): compat_version "
                f"{self.compat_version!r} is not supported by this core. "
                f"Supported: {sorted(SUPPORTED_TOOL_PROTOCOL_VERSIONS)}."
            )
        if not isinstance(self.args_schema, dict):
            raise ValueError(
                f"ToolManifest({self.name!r}): args_schema must be a dict, "
                f"got {type(self.args_schema).__name__}"
            )
        if self.config_key is not None and (
            not isinstance(self.config_key, str) or not self.config_key.strip()
        ):
            raise ValueError(
                f"ToolManifest({self.name!r}): config_key must be a "
                f"non-empty string or None, got {self.config_key!r}"
            )


# ---------------------------------------------------------------------------
# Back-compat synthesizer
# ---------------------------------------------------------------------------


def _to_str_tuple(value: Any) -> tuple[str, ...]:
    if not value:
        return ()
    try:
        return tuple(str(item) for item in value)
    except TypeError:
        return ()


def resolve_tool_manifest(tool: Any) -> ToolManifest:
    """Return the Tool's declared manifest, or synthesize one.

    Resolution order mirrors ``core.skills.manifest.resolve_manifest``:
      1. If the Tool exposes a class-level ``manifest`` attribute
         that is already a ``ToolManifest`` instance, return it.
      2. If the Tool exposes a ``manifest`` attribute that is a
         dict, build a ``ToolManifest(**that_dict)``.
      3. Otherwise synthesize from the legacy scattered attributes
         (``name``, ``description``, ``args_schema``, …). The
         ``compat_version`` is stamped as ``DEFAULT_TOOL_PROTOCOL_VERSION``.

    Raises:
        ValueError: if the Tool is missing usable ``name`` or
            ``description`` (re-raised from ``__post_init__``).
    """
    declared = getattr(tool, "manifest", None)
    if isinstance(declared, ToolManifest):
        return declared
    if isinstance(declared, dict):
        return ToolManifest(**declared)

    name = getattr(tool, "name", None)
    description = getattr(tool, "description", None)
    if not isinstance(name, str) or not isinstance(description, str):
        raise ValueError(
            f"Cannot resolve manifest for {tool!r}: missing `name` or "
            "`description` attribute. Either expose them as class "
            "attributes or declare a `manifest = ToolManifest(...)` "
            "directly on the class."
        )
    args_schema = getattr(tool, "args_schema", {}) or {}
    if not isinstance(args_schema, dict):
        args_schema = {}
    return ToolManifest(
        name=name,
        description=description,
        compat_version=DEFAULT_TOOL_PROTOCOL_VERSION,
        args_schema=dict(args_schema),
        side_effects=bool(getattr(tool, "side_effects", False)),
        requires_confirmation=bool(getattr(tool, "requires_confirmation", False)),
        requires_caps=_to_str_tuple(getattr(tool, "requires_caps", ())),
        config_key=getattr(tool, "config_key", None),
        extras=dict(getattr(tool, "extras", {}) or {}),
    )
