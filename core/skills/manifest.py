"""core.skills.manifest — Declarative capability descriptor for Skills.

WHY THIS EXISTS
---------------
Until now a Skill carried its plug-in metadata as scattered class
attributes (``name``, ``description``, ``cost_tier``, ``plan_verb``,
…). That worked when there were 7 first-party skills and one author.
For OpenCrayFish to become a *framework* that third-party developers
can extend at scale, the same metadata has to be:

  * **Unified** — one canonical place to look (and to validate).
  * **Versioned** — a Skill written against the v1 protocol must be
    able to declare so, and the registry must refuse to load a Skill
    that requires a newer protocol than the running core supports.
  * **Self-describing for the SLM** — a Skill must be able to teach
    the PLAN stage *when to pick it*, without anyone editing the
    hardcoded "VERB SELECTION RULES" block in ``core/cognition.py``.
    The whole point of dynamic PLAN menus is wasted if the prompt
    still hardcodes which verbs the SLM should prefer.
  * **Dependency-aware** — a Skill that calls ``ctx.tools.call("http_get",
    …)`` must be able to *declare* that requirement at startup so the
    registry can fail-loud instead of crashing on first invocation
    three days into a deployment.

``SkillManifest`` is the dataclass that gathers all of the above.
``_synthesize_manifest`` is the back-compat bridge — it reads the
scattered class attributes that today's Skills already expose and
produces an equivalent manifest. That means existing Skills DO NOT
need to change to keep working; only Skills that want to use the new
fields (``compat_version``, ``requires_tools``, ``plan_guidance``,
``plan_example``) need to add an explicit ``manifest`` class
attribute.

DESIGN PRINCIPLES
-----------------
* **Frozen + slotted**. Manifests are immutable contracts. Mutating
  one after registration would desync the dashboard catalogue + audit
  feeds + PLAN regex cache.
* **No runtime behaviour here.** A manifest is *pure data*. All
  policy (filtering by cost tier, dispatching to ``execute``) stays
  in ``registry.py`` / ``cognition.py``. Manifests describe; they
  never decide.
* **Optional fields default to backwards-compatible values.** A
  Skill that doesn't set ``plan_guidance`` simply contributes
  nothing to the PLAN-stage rules block — the legacy behaviour.
* **Validation is split.** Static checks (well-formed name, valid
  cost tier) live in ``__post_init__``. Cross-capability checks
  (missing required tool, duplicate plan_verb) live in
  ``SkillRegistry.bootstrap_validate`` because they need the full
  registry + tool set in view.

VERSIONING POLICY
-----------------
The ``compat_version`` field declares which Skill Protocol revision
the Skill was written against. The current core supports protocol
revisions in ``SUPPORTED_PROTOCOL_VERSIONS``. A Skill whose
``compat_version`` is not in this set is refused at registration —
fail-loud so the operator can either upgrade the Skill or pin the
core. This is the contract that lets the framework evolve without
silently breaking third-party plug-ins.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .base import CostTier


# Skill Protocol revisions the running core knows how to load. Bump the
# tuple AND add a migration note in CONTRIBUTING.md whenever a
# breaking change ships in the Skill surface. The convention is:
#
#   "skill-protocol/N"   — major; N bumps on breaking changes.
#
# The framework promises to keep the most recent two majors supported
# (a one-version deprecation window) so third-party Skill packages
# have time to migrate.
SUPPORTED_PROTOCOL_VERSIONS: frozenset[str] = frozenset({
    "skill-protocol/1",
})

# Default protocol the back-compat synthesizer stamps onto a Skill that
# doesn't expose an explicit ``manifest`` — i.e. every first-party
# Skill written before manifests existed. Always equal to the LATEST
# stable major so legacy Skills track core forward by default.
DEFAULT_PROTOCOL_VERSION: str = "skill-protocol/1"


# Reserved capability tokens that the registry knows how to interpret
# when validating a Skill's ``requires_caps`` list. Unknown tokens are
# allowed at registration but logged so third-party authors can ship
# their own conventions without breaking validation. The well-known
# set is documented here so contributors have a north star.
WELL_KNOWN_CAPABILITIES: frozenset[str] = frozenset({
    "network",          # Skill makes outbound HTTP / DNS calls.
    "filesystem.read",  # Skill reads files outside its own state dir.
    "filesystem.write", # Skill writes files outside its own state dir.
    "soul.read",        # Skill reads soul.md.
    "soul.write",       # Skill mutates soul.md (gated by SoulHandler).
    "stm.read",         # Skill reads STM history.
    "stm.write",        # Skill appends to STM.
    "provider",         # Skill makes its own SLM calls via ctx.provider.
    "gpio",             # Skill controls GPIO pins (Pi-only).
    "actuator",         # Skill drives a physical actuator (motor, speaker).
})


@dataclass(frozen=True, slots=True)
class SkillManifest:
    """The declarative contract between a Skill and the core.

    Every field is part of the *plug-in surface*: adding, removing, or
    changing the semantics of any field is a Skill Protocol break and
    MUST bump ``SUPPORTED_PROTOCOL_VERSIONS``. Don't add fields here
    for convenience — extend the optional ``extras`` map instead.
    """

    # -- Identity -------------------------------------------------------------

    name: str
    """Registry key. Lowercase snake_case. Must be unique per process.
    Convention: verb-like noun (``research``, ``recall``,
    ``home_control``). The registry refuses duplicates at boot."""

    description: str
    """One-line human + SLM-readable purpose. Goes into the PLAN-stage
    prompt. Keep ≤ 60 tokens — the 1.5B SLMs we ship on have tight
    context budgets and a verbose description crowds out evidence."""

    # -- Protocol versioning --------------------------------------------------

    compat_version: str = DEFAULT_PROTOCOL_VERSION
    """Skill Protocol revision this Skill was written against. Must be
    one of ``SUPPORTED_PROTOCOL_VERSIONS`` at registration time, else
    the registry refuses to load. See module docstring for the
    versioning policy."""

    # -- SLM-facing PLAN-stage surface ---------------------------------------

    plan_verb: str | None = None
    """ALL-CAPS token the SLM picks during PLAN (e.g. ``"SEARCH"``).
    ``None`` keeps the Skill invocable via the registry but hidden
    from the PLAN menu — appropriate for orchestrator-only or
    background-only skills (``identity``, ``self_reflect``,
    ``proactive_learning``, ``recurring_research``)."""

    plan_arg_hint: str | None = None
    """Placeholder rendered next to the verb in the PLAN menu (e.g.
    ``'"<3-8 keywords>"'``). Drives both the menu text and the PLAN
    regex's optional query group. Empty / ``None`` for verbs that
    take no query argument (``RECALL``, ``ANSWER``)."""

    plan_guidance: str = ""
    """Multi-line SLM hint text the cognition loop assembles into the
    PLAN prompt's "VERB SELECTION RULES" block. Each Skill contributes
    its own ~2-4 line block describing when to pick this verb vs the
    alternatives. Empty string = no guidance contributed (the SLM
    falls back to ``description`` alone, which is fine for skills the
    SLM can pick by name-match)."""

    plan_example: str = ""
    """One example PLAN line for this verb, used in the prompt's
    "Format examples" section. Should be abstract-looking (with
    placeholder syntax like ``<noun phrase>``) so the SLM treats it
    as a *pattern* rather than a topic to copy. Example for SEARCH:
    ``'Q1: SEARCH "<noun phrase from user, 3-8 keywords>"'``."""

    trigger_hints: tuple[str, ...] = ()
    """Hints rendered under the description in the generic Skill
    catalogue (``available_for_prompt``). Distinct from
    ``plan_guidance`` which targets the PLAN stage specifically.
    Stored as a tuple so manifests stay hashable + frozen."""

    args_schema: dict[str, dict[str, Any]] = field(default_factory=dict)
    """JSON-Schema-ish descriptor of ``execute(...)`` kwargs. Same
    shape as ``Tool.args_schema``. Rendered under the trigger hints
    in the catalogue. Each value is itself a dict with keys
    ``type``, ``required``, ``desc`` (all optional, all best-effort
    rendered)."""

    # -- Cost + safety filters -----------------------------------------------

    cost_tier: CostTier = "expensive"
    """``free`` / ``cheap`` / ``expensive``. The cognition loop drops
    expensive skills when vitals are stressed; the dashboard shows
    the tier as a chip. Default ``expensive`` is the conservative
    choice — a Skill author who knows their work is local should
    explicitly downgrade."""

    requires_network: bool = False
    """If True the Skill is filtered out when the provider's circuit
    breaker reports offline. Stops the SLM from picking SEARCH when
    SearXNG is unreachable."""

    side_effects: bool = False
    """True if the Skill mutates external state (writes to soul, STM,
    filesystem, network, GPIO, …). Drives the dashboard chip and
    a future Architect-ack prompt."""

    requires_confirmation: bool = False
    """If True the Skill should not run without an explicit Architect
    ack. Reserved for actuator Skills (lock the door, send the
    email). Not enforced by the registry today — the cognition loop
    is responsible for not picking such Skills autonomously, and a
    future ack-flow will gate them at the connector layer."""

    # -- Dependencies + capability surface ------------------------------------

    requires_tools: tuple[str, ...] = ()
    """Names of Tools this Skill calls via ``ctx.tools.call(...)``.
    The registry's ``bootstrap_validate`` cross-checks every name
    against the live ToolRegistry — a Skill that requires an
    unregistered tool is refused at boot, NOT silently broken at
    first invocation. Empty tuple = pure compute / no tools."""

    requires_caps: tuple[str, ...] = ()
    """Capability tokens this Skill exercises (see
    ``WELL_KNOWN_CAPABILITIES``). Informational today; the registry
    logs unknown tokens at boot so third-party packages can ship
    their own conventions without breaking validation. A future
    sandbox layer may use this list to gate Skills against an
    operator-configured allowlist."""

    # -- Extension slot ------------------------------------------------------

    extras: dict[str, Any] = field(default_factory=dict)
    """Free-form key/value pairs for fields not yet stable enough to
    warrant their own typed slot (UI hints, telemetry tags, future
    sandboxing knobs). When a key proves useful across multiple
    Skills it should graduate to a real field + protocol bump."""

    # -- Static validation ---------------------------------------------------

    def __post_init__(self) -> None:
        # Identity must be a non-empty snake_case-ish token. We don't
        # enforce strict snake_case because third-party packages may
        # legitimately want dotted namespaces (`acme.weather`) — but
        # whitespace / control chars are always wrong.
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError(
                f"SkillManifest.name must be a non-empty string, got {self.name!r}"
            )
        if any(ch.isspace() for ch in self.name):
            raise ValueError(
                f"SkillManifest.name must not contain whitespace: {self.name!r}"
            )
        if not isinstance(self.description, str) or not self.description.strip():
            raise ValueError(
                f"SkillManifest.description must be a non-empty string "
                f"for skill {self.name!r}"
            )
        # Protocol version must be one we know how to load.
        if self.compat_version not in SUPPORTED_PROTOCOL_VERSIONS:
            raise ValueError(
                f"SkillManifest({self.name!r}): compat_version "
                f"{self.compat_version!r} is not supported by this core. "
                f"Supported: {sorted(SUPPORTED_PROTOCOL_VERSIONS)}. "
                "Upgrade the Skill package, or pin the core to a version "
                "that supports this protocol revision."
            )
        # plan_verb (when set) must be a non-empty ALL-CAPS token. We
        # don't enforce strict uppercase because the registry
        # uppercases at compare-time, but visible lowercase in
        # manifests is a smell.
        if self.plan_verb is not None:
            if not isinstance(self.plan_verb, str) or not self.plan_verb.strip():
                raise ValueError(
                    f"SkillManifest({self.name!r}): plan_verb must be a "
                    f"non-empty string or None, got {self.plan_verb!r}"
                )
        # cost_tier is typed as Literal but we sanity-check at runtime
        # because third-party Skills may import the field as a string.
        if self.cost_tier not in ("free", "cheap", "expensive"):
            raise ValueError(
                f"SkillManifest({self.name!r}): cost_tier must be one of "
                f"'free' / 'cheap' / 'expensive', got {self.cost_tier!r}"
            )


# ---------------------------------------------------------------------------
# Back-compat synthesizer
# ---------------------------------------------------------------------------
#
# Every first-party Skill (written before manifests existed) already
# exposes the scattered class attributes the manifest gathers. The
# synthesizer reads those attributes and produces an equivalent
# manifest, so the registry can treat *all* Skills uniformly without
# requiring every existing Skill to add an explicit ``manifest`` class
# attribute right now.
#
# A Skill that DOES want to use the new fields
# (``compat_version`` / ``requires_tools`` / ``plan_guidance`` /
# ``plan_example``) simply exposes a class-level ``manifest`` and the
# synthesizer is bypassed — its declared manifest wins.


def _to_str_tuple(value: Any) -> tuple[str, ...]:
    """Normalize lists/tuples/iterables of strings to a frozen tuple.

    Defensive: a third-party Skill might expose ``trigger_hints`` as
    a list (mutable) or as ``None``; we coerce so the manifest stays
    immutable and the field always has a predictable type.
    """
    if not value:
        return ()
    try:
        return tuple(str(item) for item in value)
    except TypeError:
        return ()


def resolve_manifest(skill: Any) -> SkillManifest:
    """Return the Skill's declared manifest, or synthesize one.

    Resolution order:
      1. If the Skill exposes a class-level ``manifest`` attribute
         that is already a ``SkillManifest`` instance, return it
         unchanged. This is the path a manifest-native Skill takes.
      2. If the Skill exposes a ``manifest`` attribute that is a
         dict, build a ``SkillManifest(**that_dict)``. This is a
         convenience for declarative-style Skills that don't want to
         import the dataclass.
      3. Otherwise synthesize a manifest from the legacy scattered
         attributes (``name``, ``description``, ``cost_tier``,
         ``plan_verb``, …). The manifest's ``compat_version`` is
         stamped as ``DEFAULT_PROTOCOL_VERSION`` because we have no
         way to know which protocol the legacy Skill was written
         against; the assumption is "the latest at the time the core
         was built", which is by construction supported.

    Raises:
        ValueError: if the Skill is missing a usable ``name`` or
            ``description`` (re-raised from ``SkillManifest.__post_init__``).
    """
    declared = getattr(skill, "manifest", None)
    if isinstance(declared, SkillManifest):
        return declared
    if isinstance(declared, dict):
        return SkillManifest(**declared)

    name = getattr(skill, "name", None)
    description = getattr(skill, "description", None)
    if not isinstance(name, str) or not isinstance(description, str):
        raise ValueError(
            f"Cannot resolve manifest for {skill!r}: missing `name` or "
            "`description` attribute. Either expose them as class "
            "attributes or declare a `manifest = SkillManifest(...)` "
            "directly on the class."
        )

    return SkillManifest(
        name=name,
        description=description,
        # Legacy Skills never declared compat_version; stamp the latest.
        compat_version=DEFAULT_PROTOCOL_VERSION,
        plan_verb=getattr(skill, "plan_verb", None),
        plan_arg_hint=getattr(skill, "plan_arg_hint", None),
        # plan_guidance / plan_example default to empty — legacy Skills
        # contribute NOTHING to the dynamic PLAN rules block, preserving
        # the prior behaviour where SEARCH/RECALL/ANSWER guidance was
        # hardcoded in cognition.py. Skills can opt in to dynamic
        # guidance by declaring an explicit manifest.
        trigger_hints=_to_str_tuple(getattr(skill, "trigger_hints", ())),
        args_schema=dict(getattr(skill, "args_schema", {}) or {}),
        cost_tier=getattr(skill, "cost_tier", "expensive"),
        requires_network=bool(getattr(skill, "requires_network", False)),
        side_effects=bool(getattr(skill, "side_effects", False)),
        requires_confirmation=bool(
            getattr(skill, "requires_confirmation", False)
        ),
        # Legacy Skills had no way to declare these; stay empty so
        # bootstrap_validate doesn't complain. Skills that DO want
        # validated dependencies must opt in to a declared manifest.
        requires_tools=(),
        requires_caps=(),
    )
