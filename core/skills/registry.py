"""core.skills.registry — Lookup + lifecycle + audit for `Skill` plugins.

The SkillRegistry mirrors `tools.registry.ToolRegistry` one level up:
where the ToolRegistry owns mechanisms, the SkillRegistry owns
capabilities. CognitiveLoop / Heartbeat / TaskScheduler dispatch every
capability call through `registry.invoke(name, ctx, **kwargs)` instead
of holding typed `searxng=` / `archive=` constructor kwargs to each
mechanism.

Design notes
------------
* `invoke(name, ctx, **kwargs)` is the ONLY way callers should run a
  skill. It guarantees:
    - unknown-skill returns a uniform `SkillResult(ok=False, error=...)`
    - latency_ms is measured by the registry, not by each skill
    - one structured log line per call (`SKILL invoke ...`) for the
      dashboard's combined activity feed
    - one append-only JSONL line per call to `state/skills.jsonl` for
      permanent audit (rotated by local date with bounded retention)
    - any unexpected exception inside `skill.execute(...)` is caught
      and converted to a failure `SkillResult` so a buggy plugin can
      never crash the calling subsystem

* `available_for_prompt(cost_tier_cap, exclude_network)` produces the
  compact skill catalogue rendered into the generic Skill prompt.
  `plan_menu(...)` produces the structured verb menu for the PLAN
  stage; both surfaces share the same cost-tier and offline filters
  so the dashboard catalogue, generic prompt, and PLAN menu agree on
  what's executable right now.

* `set_change_listener(cb)` lets `main.py` re-publish
  `state/skills.json` whenever skills are dynamically registered or
  unregistered (e.g. a future `McpBridgeSkill` that pulls tools from
  an external MCP server). Static-at-boot registration calls the
  listener once after each `register()`.

* `aclose_all()` is fire-and-forget per skill — one bad shutdown does
  not block the others. main.py calls this once at SIGINT/SIGTERM.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .base import CostTier, Skill, SkillContext, SkillResult


@dataclass(frozen=True)
class PlanMenuEntry:
    """One row of the PLAN-stage menu surface.

    Built by `SkillRegistry.plan_menu(...)` from every registered skill
    whose `plan_verb` is set. The CognitiveLoop uses this list to
    render the menu in the PLAN prompt AND to build the verb→skill
    parser table at ACT time, so the prompt and the dispatch table are
    guaranteed to stay in sync (no chance of the SLM picking a verb
    we can't actually execute).

    Fields
    ------
    verb         SLM-facing token (ALL CAPS), e.g. "SEARCH".
    skill_name   Registry key the verb dispatches to, e.g. "research".
    arg_hint     Placeholder rendered next to the verb in the menu
                 (e.g. '"<3-8 keywords>"' for SEARCH, "" for RECALL).
                 Also signals to the PLAN parser whether to expect a
                 quoted query arg after the verb.
    description  One-line purpose copied from `Skill.description`.
    cost_tier    Same as the Skill — surfaced so the menu hints at the
                 cost (the CognitiveLoop also filters by this).
    requires_network  Same as the Skill.
    """

    verb: str
    skill_name: str
    arg_hint: str
    description: str
    cost_tier: CostTier
    requires_network: bool

    @property
    def has_query_arg(self) -> bool:
        """True when the menu entry advertises a `"<...>"` placeholder.

        Drives the PLAN-stage regex: verbs with `has_query_arg=True`
        capture the quoted text following the verb; verbs without it
        match the verb token alone.
        """
        return bool(self.arg_hint)

log = logging.getLogger(__name__)

# Append-only audit feed: one JSONL line per invoke(). Read by the
# dashboard's Skills activity panel and by ReflectionEngine's
# `summarise_skills_recent(...)` for the Sleep Metabolism
# skill-failure flagger.
SKILLS_AUDIT_FEED: Path = Path("state/skills.jsonl")

# Ordering for cost_tier rendering — `free` first, `expensive` last —
# so the PLAN-stage prompt encourages the SLM to pick the cheapest
# adequate skill rather than always reaching for the network.
_COST_ORDER: dict[CostTier, int] = {"free": 0, "cheap": 1, "expensive": 2}


class SkillRegistry:
    """Owns the live set of `Skill` instances for one agent process."""

    def __init__(
        self,
        *,
        audit_feed: Path | str = SKILLS_AUDIT_FEED,
        audit_retain_days: int = 30,
        audit_tz: str = "UTC",
    ) -> None:
        from ..jsonl_writer import RotatingJsonlWriter  # local import to avoid cycle

        self._skills: dict[str, Skill] = {}
        self._audit_feed = Path(audit_feed)
        self._audit_feed.parent.mkdir(parents=True, exist_ok=True)
        # Rotate the audit feed by local date with bounded retention so
        # a long-running Pi never fills the SD card.
        self._audit_writer = RotatingJsonlWriter(
            self._audit_feed,
            retain_days=audit_retain_days,
            tz=audit_tz,
        )
        self._change_listener: Callable[[], None] | None = None

    # ---------- registration --------------------------------------------------

    def register(self, skill: Skill) -> None:
        """Add a skill. Raises ValueError on duplicate name (we'd rather
        fail loudly at boot than silently shadow a previously-registered
        skill)."""
        name = getattr(skill, "name", None)
        if not name or not isinstance(name, str):
            raise ValueError(
                f"Skill {skill!r} must expose a non-empty string `name` attribute."
            )
        if name in self._skills:
            raise ValueError(
                f"Skill name {name!r} is already registered. "
                "Pick a unique name or unregister first."
            )
        self._skills[name] = skill
        log.info(
            "SKILL registered name=%s cost_tier=%s requires_network=%s "
            "side_effects=%s requires_confirmation=%s",
            name,
            getattr(skill, "cost_tier", "?"),
            getattr(skill, "requires_network", False),
            getattr(skill, "side_effects", False),
            getattr(skill, "requires_confirmation", False),
        )
        self._notify_change()

    def unregister(self, name: str) -> Skill | None:
        """Remove and return the skill with this name, or None if absent.
        Does NOT call `aclose()` — caller decides what to do with it."""
        skill = self._skills.pop(name, None)
        if skill is not None:
            self._notify_change()
        return skill

    def set_change_listener(self, cb: Callable[[], None] | None) -> None:
        """Register a callback fired after every register / unregister.
        Used by main.py to re-publish `state/skills.json` so the
        dashboard (separate process) sees dynamic additions without
        sharing live state."""
        self._change_listener = cb

    def _notify_change(self) -> None:
        cb = self._change_listener
        if cb is None:
            return
        try:
            cb()
        except Exception:
            log.exception("Skill change listener raised — ignoring")

    # ---------- lookup --------------------------------------------------------

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def has(self, name: str) -> bool:
        return name in self._skills

    def names(self) -> list[str]:
        return sorted(self._skills.keys())

    def all(self) -> list[Skill]:
        """Return all registered skills sorted by (cost_tier, name).

        Stable ordering matters because the PLAN-stage prompt is built
        from this list — a non-deterministic order would make the SLM's
        verb choice flap between boots even with identical input.
        """
        return sorted(
            self._skills.values(),
            key=lambda s: (
                _COST_ORDER.get(getattr(s, "cost_tier", "expensive"), 99),
                getattr(s, "name", "~"),
            ),
        )

    # ---------- invocation ----------------------------------------------------

    async def invoke(
        self,
        name: str,
        ctx: SkillContext,
        **kwargs: Any,
    ) -> SkillResult:
        """Execute a skill by name. ALWAYS returns a SkillResult — never
        raises. Use this from subsystems instead of `skill.execute(...)`
        so we get uniform timing + logging + audit + crash isolation
        for free.
        """
        skill = self._skills.get(name)
        if skill is None:
            log.warning("SKILL invoke name=%s status=unknown", name)
            result = SkillResult(ok=False, error=f"unknown skill: {name!r}")
            await self._audit(name, result, kwargs_keys=sorted(kwargs.keys()))
            return result

        t0 = time.perf_counter()
        try:
            result = await skill.execute(ctx, **kwargs)
        except Exception as exc:  # belt-and-braces: misbehaving plugin
            ms = int((time.perf_counter() - t0) * 1000)
            log.exception(
                "SKILL invoke name=%s status=raised latency_ms=%d kwargs_keys=%s",
                name,
                ms,
                sorted(kwargs.keys()),
            )
            result = SkillResult(
                ok=False,
                error=f"{exc.__class__.__name__}: {exc}",
                latency_ms=ms,
            )
            await self._audit(name, result, kwargs_keys=sorted(kwargs.keys()))
            return result

        ms = int((time.perf_counter() - t0) * 1000)
        # Normalize: a plugin that returns the wrong type gets coerced
        # to a failure result rather than poisoning the caller.
        if not isinstance(result, SkillResult):
            log.error(
                "SKILL invoke name=%s status=bad_return_type type=%s latency_ms=%d",
                name,
                type(result).__name__,
                ms,
            )
            result = SkillResult(
                ok=False,
                error=f"skill {name!r} returned {type(result).__name__}, "
                      "expected SkillResult",
                latency_ms=ms,
            )
        elif result.latency_ms == 0:
            # Stamp registry-measured latency unless the skill set one.
            result = SkillResult(
                ok=result.ok,
                summary=result.summary,
                evidence=result.evidence,
                tools_used=result.tools_used,
                latency_ms=ms,
                meta=result.meta,
                error=result.error,
            )
        log.info(
            "SKILL invoke name=%s status=%s latency_ms=%d tools_used=%s"
            "%s",
            name,
            "ok" if result.ok else "fail",
            result.latency_ms,
            ",".join(result.tools_used) or "-",
            f" error={result.error[:80]!r}" if not result.ok else "",
        )
        await self._audit(name, result, kwargs_keys=sorted(kwargs.keys()))
        return result

    # ---------- prompt surface ------------------------------------------------

    def available_for_prompt(
        self,
        *,
        cost_tier_cap: CostTier = "expensive",
        exclude_network: bool = False,
    ) -> str:
        """Render the skill catalogue for inclusion in an SLM PLAN prompt.

        Filters
        -------
        cost_tier_cap   Drop skills whose tier is more expensive than
                        this cap. e.g. cap="cheap" keeps only `free`
                        and `cheap` skills — used when vitals are
                        stressed and the CognitiveLoop wants to stay
                        local.
        exclude_network When True, drop skills with requires_network=True.
                        Used when Provider's circuit breaker reports
                        offline so the SLM doesn't waste a turn picking
                        something we can't actually execute.
        """
        cap = _COST_ORDER.get(cost_tier_cap, 99)
        chosen: list[Skill] = []
        for s in self.all():
            tier = _COST_ORDER.get(getattr(s, "cost_tier", "expensive"), 99)
            if tier > cap:
                continue
            if exclude_network and getattr(s, "requires_network", False):
                continue
            chosen.append(s)
        if not chosen:
            return "(no skills available under current filters)"
        lines: list[str] = []
        for s in chosen:
            chips: list[str] = [getattr(s, "cost_tier", "?")]
            if getattr(s, "requires_network", False):
                chips.append("network")
            if getattr(s, "side_effects", False):
                chips.append("side-effects")
            if getattr(s, "requires_confirmation", False):
                chips.append("needs-confirmation")
            lines.append(f"- {s.name} [{','.join(chips)}]: {s.description}")
            for hint in getattr(s, "trigger_hints", []) or []:
                lines.append(f"    when: {hint}")
            for arg_name, arg_meta in (getattr(s, "args_schema", {}) or {}).items():
                req = "required" if arg_meta.get("required", False) else "optional"
                arg_type = arg_meta.get("type", "any")
                desc = arg_meta.get("desc", "")
                tail = f" — {desc}" if desc else ""
                lines.append(f"    arg {arg_name} ({arg_type}, {req}){tail}")
        return "\n".join(lines)

    # ---------- PLAN-stage surface --------------------------------------------

    def plan_menu(
        self,
        *,
        cost_tier_cap: CostTier = "expensive",
        exclude_network: bool = False,
    ) -> list[PlanMenuEntry]:
        """Return the dynamic verb menu the CognitiveLoop PLAN stage offers.

        Only skills with a non-None `plan_verb` class attribute appear;
        background-only skills (`self_reflect`, `proactive_learning`,
        `recurring_research`) and orchestrator-only skills (`identity`,
        which Brain wires via its own shortcut) stay hidden.

        The filters mirror `available_for_prompt(...)` so all three
        registry surfaces (dashboard catalogue, generic skill prompt,
        PLAN menu) agree on what's executable right now.

        Sort order: cheapest tier first (so the SLM's eye lands on the
        local-only options before it scrolls to the expensive web call),
        then alphabetical by verb for stability across boots.
        """
        cap = _COST_ORDER.get(cost_tier_cap, 99)
        out: list[PlanMenuEntry] = []
        for s in self._skills.values():
            verb = getattr(s, "plan_verb", None)
            if not verb or not isinstance(verb, str):
                continue
            tier: CostTier = getattr(s, "cost_tier", "expensive")
            if _COST_ORDER.get(tier, 99) > cap:
                continue
            requires_net = bool(getattr(s, "requires_network", False))
            if exclude_network and requires_net:
                continue
            out.append(PlanMenuEntry(
                verb=verb,
                skill_name=s.name,
                arg_hint=str(getattr(s, "plan_arg_hint", "") or ""),
                description=str(getattr(s, "description", "") or "").strip(),
                cost_tier=tier,
                requires_network=requires_net,
            ))
        out.sort(key=lambda e: (_COST_ORDER.get(e.cost_tier, 99), e.verb))
        return out

    # ---------- lifecycle -----------------------------------------------------

    async def aclose_all(self) -> None:
        """Close every registered skill, isolating failures so one bad
        shutdown can't block the rest. Called once by main.py at exit."""
        for name, skill in list(self._skills.items()):
            try:
                await skill.aclose()
            except Exception:
                log.exception("SKILL aclose name=%s failed (continuing)", name)

    # ---------- audit ---------------------------------------------------------

    async def _audit(
        self,
        name: str,
        result: SkillResult,
        *,
        kwargs_keys: list[str],
    ) -> None:
        """Append one JSONL line per invocation to `state/skills.jsonl`.

        The path is rotated by local date and retention-pruned by
        `RotatingJsonlWriter`; consumers should not assume a single
        live file (see `RotatingJsonlWriter.sibling_paths()` for the
        recommended read pattern). We intentionally do NOT inline
        kwargs values (they may carry user text or secrets) — only
        the KEY names. The dashboard joins this against the boot-time
        `state/skills.json` catalogue.
        """
        entry = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "skill": name,
            "ok": bool(result.ok),
            "latency_ms": int(result.latency_ms),
            "tools_used": list(result.tools_used or []),
            "kwargs_keys": list(kwargs_keys),
            "error": (result.error or "")[:200],
        }
        # RotatingJsonlWriter handles per-record O_APPEND atomicity AND
        # date-based file rotation + retention sweep. It never raises
        # — failures are logged inside the writer so audit can never
        # take a turn down.
        await self._audit_writer.append(entry)
