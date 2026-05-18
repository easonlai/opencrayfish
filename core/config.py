"""core.config — Typed loader for `config.yaml`."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class SystemCfg:
    individual_designation: str
    timezone: str
    duty_start: str   # "HH:MM"
    sleep_start: str  # "HH:MM"
    # Display name of THIS deployment's human operator (the Architect).
    # Eason Lai is the project Creator (immutable in soul.md); the Architect
    # is whoever runs this instance and is named here so the agent can
    # address them properly in every reply.
    architect_name: str = "Architect"
    # How the agent verbally addresses the operator. Combined with
    # `architect_name` to form salutations like "Boss Eason". Default is
    # "Boss" — the agent will say "Boss <name>" instead of "Architect <name>"
    # in every reply. Set to empty string to use just the name.
    architect_honorific: str = "Boss"
    # Heartbeat tuning (per HEARTBEAT_LOGIC.md §1). Defaults match the spec.
    # NOTE the dataclass default of 15 minutes is the *fallback* used only
    # when `system.idle_proactive_minutes` is missing from config.yaml. The
    # shipped config.yaml sets this to 5 — see config_sample.yaml.
    pulse_interval_seconds: int = 30
    idle_proactive_minutes: int = 15
    # Deferred journal write: STM holds new turns in a RAM pending buffer
    # and only writes to disk when the agent has been idle this many seconds.
    # Avoids per-turn SD-card thrashing on the Pi5 during active conversation.
    # Set to 0 to flush on every pulse (effectively per-turn). Pending data
    # is also auto-flushed on metabolism / shutdown so no idle wait can
    # cost data through the normal lifecycle.
    idle_journal_flush_seconds: int = 30
    # When True, every disk flush (idle, metabolism) is followed by
    # os.fsync() — strict durability across power loss but at the cost
    # of an SD-card sync on each flush. Default False: rely on OS write-back.
    # Shutdown flush always uses fsync regardless of this setting.
    journal_fsync_on_flush: bool = False


@dataclass(frozen=True)
class HardwareCfg:
    npu_acceleration: bool
    hailo_ollama_url: str       # Hailo-Ollama REST endpoint, e.g. http://localhost:8000
    hailo_model: str            # NPU model tag, e.g. "qwen2.5-instruct:1.5b"
    cpu_fallback_url: str       # Ollama REST endpoint, e.g. http://localhost:11434
    cpu_fallback_model: str     # CPU model tag, e.g. "qwen2:1.5b"
    thermal_limit_celsius: float
    # ---- v2 vitals tuning ---------------------------------------------------
    # Stress ENTER thresholds — defaults match the legacy single-threshold
    # behaviour. The RELEASE values implement hysteresis: once we enter
    # stress, we stay in stress until BOTH temp AND ram drop below their
    # release thresholds. Prevents the persona from oscillating turn-by-turn
    # when readings hover at the limit.
    ram_limit_pct: float = 85.0
    thermal_release_celsius: float = 0.0   # 0.0 → defaults to (limit - 5°C) in Monitor
    ram_release_pct: float = 0.0           # 0.0 → defaults to (limit - 5%) in Monitor
    # Brain re-uses the heartbeat's vitals reading if it's fresher than this
    # many seconds, avoiding a per-turn 100 ms `psutil.cpu_percent` block.
    vitals_cache_ttl_seconds: float = 5.0


@dataclass(frozen=True)
class ApiKeysCfg:
    telegram_token: str
    telegram_user_id: str  # Telegram numeric user id authorized to talk to the agent.


@dataclass(frozen=True)
class ToolsCfg:
    searxng_url: str
    # When True, every user message is triaged by the SLM: if the agent isn't
    # confident it has first-hand knowledge to answer, SearXNG is queried
    # before the main reply is generated. Adds one short LLM call per turn.
    web_search_triage_enabled: bool = True
    # LTM short-circuit: if archive.md already contains a strong match for the
    # user's query, skip the SLM triage call AND the SearXNG round-trip and
    # answer from memory. Saves latency + bandwidth + tokens; the trade-off is
    # that stale archive entries may be preferred over fresh web data. Set
    # `ltm_short_circuit_min_score` higher to be more conservative (require
    # more keyword overlap before trusting LTM). Explicit user search requests
    # ("search for ...", "搜尋 ...") always bypass the short-circuit.
    ltm_short_circuit_enabled: bool = True
    # Minimum keyword-overlap score (number of >3-char query terms appearing
    # in the top archive line) required before the short-circuit fires.
    # 1 = trigger-happy, 2 = balanced default, 3+ = only on very strong hits.
    ltm_short_circuit_min_score: int = 2


@dataclass(frozen=True)
class MemoryCfg:
    stm_max_turns: int
    archive_path: str
    log_path: str


@dataclass(frozen=True)
class ReflectionCfg:
    """Self-reflection / self-learning loop tuning."""
    enabled: bool = True
    # Reflect on user-driven turns (after every reply).
    reflect_on_user_turn: bool = True
    # Reflect on autonomous proactive thoughts.
    reflect_on_proactive: bool = True


@dataclass(frozen=True)
class ProactiveCfg:
    """Autonomous-research (idle Proactive Thought) tuning.

    The agent's idle research source is chosen in this order:
      1. STM-gap extraction — read recent conversation, ask the SLM to name
         concepts mentioned that it does NOT confidently know, then research
         the first true gap (cross-checked against archive.md / soul.md so it
         doesn't relearn what's already in LTM).
      2. LEARNED_PREFERENCES fallback — if STM produced no genuine gap, fall
         back to the operator's stored interests (legacy behavior).
      3. Skip — if both sources are empty, the cycle stays silent.
    """
    # Master switch for the cognitive-gap extraction step. When False, the
    # agent always uses LEARNED_PREFERENCES (legacy behavior).
    stm_gap_extraction_enabled: bool = True
    # How many candidate concepts to extract per cycle. Each candidate costs
    # one extra SLM triage call ("do I know this?"). Keep small on Pi5.
    max_candidates_per_cycle: int = 3
    # Fall back to LEARNED_PREFERENCES when STM produced no actionable gap.
    # Set False on quiet deployments — the agent will then stay silent on
    # idle days where there's nothing genuinely new to learn.
    fallback_to_preferences: bool = True
    # When the SLM emits this single token (case-insensitive), treat the
    # candidate as KNOWN (no research needed). Anything else — including
    # ambiguous output — is treated as UNKNOWN to err on the side of learning.
    triage_known_token: str = "YES"
    # When True, run a single REFINE pass on the synthesized reflection
    # BEFORE it gets persisted to proactive.jsonl / promoted into Core
    # Memories. The refiner re-reads the SearXNG snippets and the draft
    # reflection and either returns "OK" or rewrites the reflection to
    # remove unsupported claims (hallucinated dates/numbers/names).
    # Costs ONE extra SLM call per idle cycle. Pi 5 budget: ~600-900 ms.
    refine_enabled: bool = True


@dataclass(frozen=True)
class CognitionCfg:
    """Cognitive Loop tuning (THINK → PLAN → ACT → REFINE).

    The loop runs in front of the final synthesize call on engaged user
    turns, decomposing the request into atomic sub-questions, planning a
    verb per sub-question (RECALL/SEARCH/ANSWER), executing them
    concurrently, and — if `refine_enabled` — doing one bounded refine
    round when the gathered evidence has a concrete gap. Bypassed for
    chitchat / explicit search / stressed vitals / LTM short-circuit so
    edge latency stays bounded.
    """
    enabled: bool = True
    # Max sub-questions emitted by THINK. Larger = more thorough but more
    # ACT calls (linear cost). Pi 5 sweet spot is 3.
    max_subquestions: int = 3
    # Total ACT rounds permitted (1 = no refine; 2 = one refine round
    # allowed after the first ACT). Capped to 3 in code regardless.
    max_act_rounds: int = 2
    # When False, REFINE stage is skipped even if max_act_rounds >= 2.
    # Useful for first-day rollout: prove THINK + PLAN + ACT are stable,
    # then flip this to true to enable refinement.
    refine_enabled: bool = True
    # When True, the ANSWER verb actually invokes the `direct_answer`
    # Skill (one extra SLM call per ANSWER step) and surfaces the reply
    # as evidence in the synth KNOWLEDGE block. When False (default,
    # byte-identical legacy behaviour), ANSWER is a no-op marker telling
    # synth "no retrieval needed; lean on SLM training data". Flip to
    # true once you've measured the latency impact and decided the extra
    # grounding is worth it.
    dispatch_answer_via_skill: bool = False


@dataclass(frozen=True)
class WebChatCfg:
    """Local Streamlit / browser chat channel.

    A tiny aiohttp server runs in-process alongside the Telegram connector
    and exposes /chat (POST) + /state (GET) + /history (GET). The
    accompanying Streamlit app `ui/web_chat.py` talks to it. Defaults bind
    to localhost only and require nothing extra; set `auth_token` for any
    deployment that exposes the port to the LAN.
    """
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8765
    # Optional shared secret. When set, every request must include
    # `X-OCF-Token: <token>` or it is rejected with 401. Leave empty for
    # purely-local dev (loopback bind already prevents external access).
    auth_token: str = ""
    # The web UI does NOT carry the same trust level as the Telegram
    # Architect channel. Sleep Metabolism still gates non-emergency
    # messages — set this True to also gate the web channel during 02-06.
    respect_sleep_metabolism: bool = True


@dataclass(frozen=True)
class SkillsCfg:
    """Skill layer tuning (capability registry above Tools).

    A Skill is the agent-facing capability the Cognitive Loop / Heartbeat /
    Scheduler dispatches to satisfy one sub-question or fire one autonomous
    cycle. Each Skill composes 0..N Tool calls plus its own policy. See
    `core/skills/base.py` for the contract and `core/skills/` for the
    shipping set.

    Skills are registered at boot, listed in `state/skills.json` for
    dashboard discoverability, and audited per-invocation in
    `state/skills.jsonl` (rotated by local date). The knobs below shape
    the dynamic PLAN-stage menu the SLM picks from each turn.
    """
    # Cost-tier cap for the SLM-driven PLAN-stage menu. Skills above
    # this tier are filtered out. Useful for clamping budget on
    # stressed vitals (when 'cheap' is enforced the agent stays local).
    #   free      — only pure-compute skills
    #   cheap     — free + local-I/O skills (default)
    #   expensive — all skills allowed
    default_cost_tier_cap: str = "expensive"
    # When the Provider's circuit breaker reports offline, auto-drop
    # skills with requires_network=true from the PLAN menu so the SLM
    # doesn't waste a turn picking something we can't execute.
    auto_offline_filter: bool = True
    # Per-skill enable/disable. Keys are skill names. Omit a key to use
    # the skill's default registration (i.e. enabled). Set to false to
    # force-hide a skill (it won't be registered at boot).
    enabled: dict[str, bool] = field(default_factory=dict)


@dataclass(frozen=True)
class TasksCfg:
    """Recurring research-task scheduler (per core/scheduler.py)."

    Operator creates tasks via plain chat ("check MSFT every hour"). The
    scheduler then fires each task on its own cadence, runs SearXNG on
    the queries, hands the brief to the SLM for analysis, and pushes the
    report back through the originating connector. Tasks pause during
    Sleep Metabolism and catch up once on wakeup.
    """
    enabled: bool = True
    state_path: str = "state/tasks.yaml"
    # Hard cap on concurrently active tasks. Each task fires sequentially
    # so the practical SLM cost is interval-bound, not count-bound — but
    # too many tasks make the /tasks listing unwieldy. 16 is plenty.
    max_active_tasks: int = 16
    # SearXNG results per query (per fire). 5 keeps the brief short
    # enough for the small SLM to digest in one synthesis call.
    results_per_query: int = 5
    # Floor on interval. Below 5 minutes the agent and SearXNG would be
    # under constant load with no real signal change. Operator can lower
    # this for testing but be aware of the cost.
    min_interval_seconds: int = 300
    # How often the scheduler wakes to check for due tasks. Lower = more
    # responsive to add_task, but more wasted ticks. 30 s matches the
    # heartbeat cadence so they're naturally aligned.
    tick_seconds: int = 30


@dataclass(frozen=True)
class Config:
    system: SystemCfg
    hardware: HardwareCfg
    api_keys: ApiKeysCfg
    tools: ToolsCfg
    memory: MemoryCfg
    reflection: ReflectionCfg = ReflectionCfg()
    proactive: ProactiveCfg = ProactiveCfg()
    cognition: CognitionCfg = CognitionCfg()
    web_chat: WebChatCfg = WebChatCfg()
    tasks: TasksCfg = TasksCfg()
    skills: SkillsCfg = SkillsCfg()

    @classmethod
    def load(cls, path: str | Path = "config.yaml") -> Config:
        raw: dict[str, Any] = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return cls(
            system=SystemCfg(**raw["system"]),
            hardware=HardwareCfg(**raw["hardware"]),
            api_keys=ApiKeysCfg(**raw["api_keys"]),
            tools=ToolsCfg(**raw["tools"]),
            memory=MemoryCfg(**raw["memory"]),
            reflection=ReflectionCfg(**raw.get("reflection", {})),
            proactive=ProactiveCfg(**raw.get("proactive", {})),
            cognition=CognitionCfg(**raw.get("cognition", {})),
            web_chat=WebChatCfg(**raw.get("web_chat", {})),
            tasks=TasksCfg(**raw.get("tasks", {})),
            skills=SkillsCfg(**raw.get("skills", {})),
        )
