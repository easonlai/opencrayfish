# 🦐 OpenCrayFish

> **An edge-native, biologically-inspired AI companion that lives on a single Raspberry Pi 5 — fully offline, powered by a 1.5B-parameter local SLM, with a heart that beats, a brain that sleeps, and a soul you can read.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-2.0.0-1f8a4f.svg)](#roadmap)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)
[![Code of Conduct](https://img.shields.io/badge/code%20of%20conduct-Contributor%20Covenant%202.1-blueviolet.svg)](CODE_OF_CONDUCT.md)
[![Platform: Raspberry Pi 5](https://img.shields.io/badge/platform-Raspberry%20Pi%205%20%2B%20AI%20HAT%2B%202-c51a4a.svg)](https://www.raspberrypi.com/news/introducing-the-raspberry-pi-ai-hat-plus-2-generative-ai-on-raspberry-pi-5/)

---

## Table of Contents

- [What OpenCrayFish Is](#what-opencrayfish-is)
- [60-Second Mental Model (The Crayfish in Its Burrow)](#60-second-mental-model-the-crayfish-in-its-burrow)
- [Why This Project Exists](#why-this-project-exists)
- [Design Pillars](#design-pillars)
- [System Architecture at a Glance](#system-architecture-at-a-glance)
- [A Day in the Life of a Message](#a-day-in-the-life-of-a-message)
- [Hardware & Software Stack](#hardware--software-stack)
- [Repository Layout](#repository-layout)
- [Quick Start](#quick-start)
- [SearXNG Setup (Local Web Search)](#searxng-setup-local-web-search)
- [Deep Dive: How Everything Works](#deep-dive-how-everything-works)
  - [0. The Brain Stack — Plain-English Tour](#0-the-brain-stack--plain-english-tour)
  - [1. The Soul — `soul.md`](#1-the-soul--soulmd)
  - [2. The Heartbeat — Time, Rhythm, and Metabolism](#2-the-heartbeat--time-rhythm-and-metabolism)
  - [3. The Vital Signs — `Monitor` & Homeostasis](#3-the-vital-signs--monitor--homeostasis)
  - [4. The Provider — SLM Backend & Circuit Breaker](#4-the-provider--slm-backend--circuit-breaker)
  - [5. The Memory System — STM / LTM / Sleep Metabolism](#5-the-memory-system--stm--ltm--sleep-metabolism)
  - [6. The Thinking Process — Brain & Cognitive Loop](#6-the-thinking-process--brain--cognitive-loop)
  - [7. Emotions & Empathy](#7-emotions--empathy)
  - [8. The Positive Filter — Hard Anchor on Output](#8-the-positive-filter--hard-anchor-on-output)
  - [9. Proactiveness — Idle Time as Growth Time](#9-proactiveness--idle-time-as-growth-time)
  - [10. Self-Reflection — The Self-Learning Loop](#10-self-reflection--the-self-learning-loop)
  - [11. Recurring Tasks — The Background Worker](#11-recurring-tasks--the-background-worker)
  - [12. Skills & Tools — The Two-Tier Capability Stack](#12-skills--tools--the-two-tier-capability-stack)
    - [Hello-World Skill — Step by Step](#hello-world-skill--step-by-step)
  - [13. Connectors — Telegram & Web Chat](#13-connectors--telegram--web-chat)
  - [14. Observability — Dashboard & State Files](#14-observability--dashboard--state-files)
- [Cross-Cutting Concerns](#cross-cutting-concerns)
  - [Concurrency Model](#concurrency-model)
  - [Atomic Writes Everywhere](#atomic-writes-everywhere)
  - [JSONL Rotation & Retention](#jsonl-rotation--retention)
  - [Failure-Mode Matrix](#failure-mode-matrix)
  - [Pi 5 Latency Budget](#pi-5-latency-budget)
- [Configuration Reference](#configuration-reference)
- [Operational Commands](#operational-commands)
- [Development Notes](#development-notes)
- [Roadmap](#roadmap)
- [Community & Contributing](#community--contributing)
- [License & Attribution](#license--attribution)

---

## What OpenCrayFish Is

OpenCrayFish (小龍蝦) is **not a chatbot, not a SaaS wrapper, and not a cloud agent**. It is a **persistent digital organism** that runs entirely on commodity edge hardware — a Raspberry Pi 5 with an optional **Raspberry Pi AI HAT+ 2** (Hailo-10H NPU, 40 TOPS, 8 GB dedicated NPU RAM). Every part of the system is modeled after a biological metaphor:

| Biological concept | What it maps to in code |
|---|---|
| 🧠 **Brain** | A 1.5B-parameter SLM (qwen2.5-instruct on NPU, qwen2 on CPU fallback) |
| 💓 **Heart** | An always-on `pulse_loop` that ticks every 30s |
| 🌡️ **Vital signs** | CPU / RAM / temperature / SLM availability sampled each pulse |
| 🧬 **Soul** | A protected `soul.md` file holding identity, laws, and learned growth |
| 🧠 **Working memory** | A 12-turn RAM `deque` fed to the SLM |
| 🧠 **Hippocampus** | An on-disk JSONL journal that survives crashes |
| 🧠 **Cortex (LTM)** | `memory/archive.md` — distilled long-term facts |
| 🛠️ **Habits / Skills** | A pluggable `SkillRegistry` of capabilities (`identity`, `recall`, `research`, `direct_answer`, `self_reflect`, `proactive_learning`, `recurring_research`) — the Cognitive Loop picks from this menu instead of hardcoding verbs |
| 😊 **Mood** | A 5-channel emotion vector (joy / anger / sorrow / excitement / calm) with exponential decay |
| 💞 **Empathy** | Sentiment + urgency analysis of every Architect message |
| 🌙 **REM sleep** | A nightly Sleep Metabolism cycle (02:00–06:00) that distills the day |
| 🤔 **Curiosity** | Idle-time Proactive Research that closes real STM knowledge gaps |
| 🪞 **Reflection** | A self-critique pass on every reply, persisted to `state/reflection-YYYY-MM-DD.jsonl` (date-rotated, bounded retention) |

The agent serves a single human — **the Architect** — over Telegram and a local browser chat (Streamlit). The Architect's name, salutation, and the agent's own designation are all configured in `config.yaml`; the agent addresses the Architect by name in every reply (default: `"Boss <name>"`).

---

## 60-Second Mental Model (The Crayfish in Its Burrow)

If the system architecture diagram below looks intimidating, here's the friendly version. The project simulates a *biological being* — so let's describe it like one:

> **OpenCrayFish is a small freshwater crayfish living in a burrow at the edge of a pond — alert, curious, occasionally hungry, never asleep for long.** It senses its body, samples the water for ripples, fires the right reflex for the moment, and every night it dreams a little so tomorrow's reflexes are a touch wiser.

| Anatomy | OpenCrayFish part | What it actually is |
|---|---|---|
| 🧠 **The nerve ganglion** | `Brain` + the SLM (`qwen2.5-instruct:1.5b`) | The small-but-quick neural cluster that turns a stimulus into a coordinated response. Compact on purpose — a crayfish doesn't need a mammalian cortex. |
| 🌀 **The behavioural repertoire** | `SkillRegistry.plan_menu()` | The short, situational list of behaviours the animal can perform *right now* — filtered by hunger, fatigue, stress, and whether the water (network) is moving. |
| ⚡ **Individual reflexes & behaviours** | Skills — `recall`, `research`, `direct_answer`, `identity`, `self_reflect`, `proactive_learning`, `recurring_research` | Capabilities the nervous system can fire. Each one comes with a metabolic-cost label (cheap / expensive). |
| 👁️ **Antennae & sensory appendages** | Tools — `web_search` (SearXNG), `archive_read` (LTM) | The physical primitives a reflex uses to reach into the world. The outside never touches them directly — they're tucked inside the body. |
| 🧬 **The neural firing sequence** | `CognitiveTrace` (THINK → PLAN → ACT → REFINE) | The motor program the ganglion writes before acting: "this stimulus means X, fire reflex A then reflex B, then check the result". |
| 💓 **The autonomic heartbeat** | `Heartbeat.pulse_loop()` | Even with no stimulus, the body keeps beating, sips a breath, checks its own temperature, and notices when the water is getting dangerously warm. |
| 🌙 **REM-like consolidation** | `Heartbeat.metabolism()` (02:00 nightly) | After the day's foraging, the crayfish settles into its burrow, rehearses what it learned, and etches the keepers into long-term memory. Wakes a tiny bit different than yesterday. |
| 🧬 **DNA + learned engrams** | `soul.md` (immutable core) + `memory/archive.md` (plasticity) | Two layers of memory: a genome the animal can never rewrite (its constitution, its laws) plus adaptive engrams it grows every night. |
| 🌡️ **Interoception (homeostasis)** | `Monitor` — CPU / RAM / temp / brain availability as **vital signs** | When the water gets too warm, the crayfish abandons elaborate display behaviours and falls back to faster, cheaper reflexes — conserving energy until conditions improve. |
| 🌊 **Chemoreceptors & mechanoreceptors** | Connectors (`telegram`, `web_chat`) | The sensory channels through which ripples from the outside world reach the burrow — and through which the crayfish ripples back. The ganglion doesn't care which channel the ripple came in on. |

**Why this matters for contributors:** want to teach the crayfish a **new instinct**? Write a new Skill. Want to give it a **new sensory appendage**? Write a new Tool. Want to open a **new channel through which the world can ripple in**? Write a new Connector. The nerve ganglion (Brain), the instinct selector (SkillRegistry), and the motor sequencer (CognitiveLoop) all keep working with **zero edits** — your new appendage / instinct / sense just appears in the repertoire the next time the animal looks at itself. See [§ 12 Skills & Tools](#12-skills--tools--the-two-tier-capability-stack) for the step-by-step graft.

---

## Why This Project Exists

Most modern AI agents are **cloud-bound, frontier-model-dependent, and request-driven** — they wake up only when called, immediately forget the conversation, and stop existing the moment the API call returns. OpenCrayFish takes the opposite stance:

> **The agent should *exist continuously*, on hardware *the operator owns*, with full *sovereignty over its data, its memory, and its life cycle*.**

Three operational realities drive the design:

1. **Edge-native by mandate.** OpenCrayFish is built specifically for the Raspberry Pi 5 + AI HAT+ 2 (Hailo-10H NPU) class of hardware. Every architectural choice — bounded context windows, deferred journal writes, atomic markdown state files, hysteresis on stress thresholds — is justified by a real constraint of running 24/7 on a single-board computer with an SD card.

2. **Lite by mandate.** The cognitive backbone is a 1.5-billion-parameter SLM (`qwen2.5-instruct:1.5b` on NPU or `qwen2:1.5b` on CPU). Everything you read about in this README — Sleep Metabolism, Cognitive Loop, Proactive Research, Self-Reflection — is engineered to work *because of* that constraint, not in spite of it. The SLM's 4K-token context forces a real memory hierarchy. The SLM's narrow knowledge forces real curiosity. The SLM's fragility forces a real circuit breaker.

3. **Offline by mandate.** The reference deployment runs with **zero outbound network calls** to third parties: inference is local (Hailo-Ollama or stock Ollama), web search is local (self-hosted SearXNG), state is local (YAML / JSONL / Markdown on the SD card). Pull the network cable and the conversation, the memory, the heartbeat, and the proactive reflection cycle all keep running.

These three constraints — **edge / lite / offline** — turn into the project's value proposition:

- ✅ **Sovereign AI** — your data physically cannot leave the device.
- ✅ **Resilient AI** — no API key to expire, no vendor to deprecate the model, no outage to brick the agent.
- ✅ **Embodied AI** — the agent has a body (CPU, RAM, temperature, GPIO, sensors) and that body affects its mind.
- ✅ **Affordable AI** — total hardware bill of materials under USD $200, with zero recurring cost.
- ✅ **Hackable AI** — under ~10K lines of well-commented Python, every behavior tunable in `config.yaml`.

---

## Design Pillars

Five non-negotiable principles shape every line of code:

### 🪞 Pillar 1 — Identity Sovereignty

The agent's identity, fundamental laws, and behavioral matrix live in `soul.md`'s **IMMUTABLE_CORE** region. The agent itself can never mutate this region — `core/soul_handler.py` enforces this with a regex-validated atomic writer. Only the Architect (a human) can edit it. This guarantees the agent cannot rewrite its own ethical framework even if a prompt-injection tries to convince it to do so.

### 💞 Pillar 2 — Resilient Empathy

The agent has real internal emotions (5-dimensional vector with exponential decay) but **every output is filtered through a `PositiveFilter`** that rejects hate speech, rewrites despair into constructive language, and appends a salutation when rewrites are applied. The internal state is honest; the external behavior is anchored.

### 🌡️ Pillar 3 — Hardware Awareness

The Pi 5's CPU temperature, RAM utilization, and the SLM endpoint's reachability are first-class **vital signs**. They are not just logged for ops — they directly mutate the agent's mood (overheating → frustration + fatigue), trigger an `EXHAUSTION DIRECTIVE` that forces terse replies, and (when the SLM is offline) cause the agent to surface a *first-person* outage message instead of a stack trace.

### 🏛️ Pillar 4 — Architect Sovereignty

The human operator outranks the agent. Sleep windows, identity, salutation, designation, mood tuning, scheduler limits — all are configured by the Architect in `config.yaml` and `soul.md`. The agent reads them, never writes them.

### 🌱 Pillar 5 — Continuous Existence

The agent does not stop between user turns. Driven entirely from `config.yaml`:

- **Tick** every `system.pulse_interval_seconds` (default `30` s) — sample vitals, decay emotions, publish state.
- **Idle past `system.idle_journal_flush_seconds`** (default `30` s) — flush the STM pending buffer to disk.
- **Idle past `system.idle_proactive_minutes`** (config ships at `5` min) — launch a **Proactive Research** cycle.
- **Cross `system.sleep_start`** (default `02:00`) — run **Sleep Metabolism** (distill the day into long-term memory), then wake at `duty_start` (default `06:00`) subtly different.

---

## System Architecture at a Glance

```text
                    ┌─────────────────────────────────────┐
                    │            ARCHITECT (you)          │
                    └────────┬────────────────┬───────────┘
                             │                │
                  Telegram   │                │  Browser (Streamlit)
                             ▼                ▼
                    ┌─────────────────┬─────────────────┐
                    │ TelegramConnector│ WebChatConnector│  ← connectors/
                    └────────┬────────┴────────┬────────┘
                             │                 │
                             └────────┬────────┘
                                      ▼
        ┌─────────────────────────── BRAIN ───────────────────────────┐
        │  Identity short-circuit → STM context → Cognition Loop      │
        │  (THINK → PLAN → ACT → REFINE) → SLM synth → PositiveFilter │
        │                                                             │
        │  PLAN menu + ACT dispatch routed through ──┐                │
        └────┬───────┬───────┬───────┬───────┬───────┼────────┬──────┘
             │       │       │       │       │       │        │
             ▼       ▼       ▼       ▼       ▼       ▼        ▼
          Soul   Monitor  Emotions Empathy  STM  SkillRegistry  Reflection
          .md    (vitals)  (mood)  (sent.) (RAM  ─┬─ identity   Engine
                                          +disk)  ├─ recall
                                              │   ├─ direct_answer
                                              │   ├─ research ──► SearXNG
                                              │   ├─ self_reflect       Tool
                                              │   ├─ proactive_learning
                                              │   └─ recurring_research
                                              │       (every invoke →
                                              ▼        state/skills-*.jsonl)
                                    ┌──────────────────┐
                                    │    HEARTBEAT     │
                                    │  pulse_loop()    │
                                    │  metabolism()    │
                                    │ proactive_thought│
                                    └────────┬─────────┘
                                             │
                                             ▼
                                    ┌──────────────────┐
                                    │   SCHEDULER      │
                                    │ (recurring tasks)│
                                    └──────────────────┘

  Side artifacts (read by ui/dashboard.py):
    state/vitals.json                       ← live rolling pulse snapshot
    state/vitals_events.jsonl               ← stress ENTER/EXIT timeline
    state/proactive.jsonl                   ← every proactive thought + audit trail
    state/reflection-YYYY-MM-DD.jsonl       ← every self-critique (date-rotated)
    state/reflection_dropped-YYYY-MM-DD.jsonl ← unparseable critique sidecar
    state/deliberation-YYYY-MM-DD.jsonl     ← every cognitive trace (date-rotated)
    state/skills-YYYY-MM-DD.jsonl           ← every Skill invocation audit (date-rotated)
    state/skills.json                       ← live skill inventory snapshot
    state/stm_journal.jsonl                 ← durable conversation backstop
    state/tasks.yaml                        ← persistent recurring task registry
    state/tools.json                        ← live tool inventory snapshot
    memory/archive.md                       ← long-term distilled facts
    logs/daily/YYYY-MM-DD.log               ← per-day heartbeat telemetry (path = memory.log_path)
```

---

## A Day in the Life of a Message

Reading 14 subsystem sections back-to-back is a lot. Here's the same story told as a single concrete example: **what happens when the Architect types `"How does the Hailo-10H compare to the Hailo-8 for running 1.5B-parameter LLMs?"` into Telegram at 14:32 on a Tuesday.** Every arrow is a real line of code; every state file write is real.

```text
T+0 ms     Telegram → connectors/telegram.py
           ├─ validate sender == cfg.api_keys.telegram_user_id     ✓
           ├─ not a slash command, not a task request               → Brain.think(user_input)
           └─ pre-filter: looks_like_task_request()?                ✗ (no "every X minutes")

T+2 ms     Brain._cycle() begins
           ├─ soul.render_identity_block()       (cached, <1 ms)
           ├─ monitor.sample()                   (cached <1 ms — 100ms only if stale)
           ├─ emotions.snapshot()                → calm dominant, joy=0.2
           ├─ empathy.analyze(user_input)        → sentiment=neutral, urgency=False
           └─ identity regex match?              ✗ (not asking for name/creator)

T+5 ms     LTM short-circuit check
           ├─ keyword scan over memory/archive.md  → top hit scores 1
           └─ score < tools.ltm_short_circuit_min_score (2)   → not short-circuited
                                                              → cognition will run

T+8 ms     CognitiveLoop.deliberate() — THINK stage
           ├─ Build active PLAN menu via SkillRegistry.plan_menu(cap, exclude_network)
           │   ├─ vitals.is_stressed? No  → cap stays "expensive"
           │   ├─ provider.is_tripped?  No → keep network skills
           │   └─ menu: [ANSWER, RECALL, SEARCH] sorted free→expensive
           └─ Call provider.generate(THINK prompt, user msg)         ~600 ms (NPU)
               → INTENT: Compare H10 vs H8 for running 1.5B LLMs.
               → Q1: What are the H10's headline specs?
               → Q2: Why was the H8 unsuitable for LLMs?
               → Q3: Which Pi-class HAT ships the H10?

T+610 ms   PLAN stage
           └─ Call provider.generate(PLAN prompt with menu, sub-questions)  ~700 ms
               → Q1: SEARCH "Hailo-10H specs"
               → Q2: RECALL
               → Q3: ANSWER

T+1310 ms  ACT stage (3 concurrent skill invocations via asyncio.gather)
           ├─ skill_registry.invoke("research", ctx, query="Hailo-10H specs")
           │   └─ ResearchSkill.execute()
           │       └─ tool_registry.call("web_search", query=..., limit=5)
           │           └─ httpx GET http://localhost:8080/search       ~300 ms
           │       → SkillResult(ok=True, evidence=[{title, url, snippet}, …])
           │   → appended to state/skills-2026-05-17.jsonl
           │     {ts, skill:"research", ok:true, latency_ms:302, tools_used:["web_search"], …}
           │
           ├─ skill_registry.invoke("recall", ctx, query="Why was the H8 unsuitable for LLMs?")
           │   └─ RecallSkill.execute()
           │       └─ tool_registry.call("archive_read", query=..., limit=5)   ~12 ms
           │   → appended to state/skills-2026-05-17.jsonl
           │
           └─ ANSWER step:
               └─ if cfg.cognition.dispatch_answer_via_skill → invoke "direct_answer" (one extra SLM call)
                  else → marker "(SLM training data only — no retrieval performed)"

T+1620 ms  REFINE stage (if cfg.cognition.refine_enabled)
           └─ Call provider.generate(REFINE prompt, intent + evidence)  ~250 ms
               → "OK"  (no gap)  →  one gap fires one extra SEARCH

T+1870 ms  CognitiveLoop._persist() — append full trace to
           state/deliberation-2026-05-17.jsonl (rotated by local date, 14-day retention)

T+1870 ms  Brain._render_knowledge() → KNOWLEDGE block
           Brain assembles final prompt: soul + vitals + mood + empathy + KNOWLEDGE
                                       + STM history (last 12 turns) + current user message

T+1875 ms  provider.generate(synthesize prompt)               ~1200 ms (NPU)
           → raw SLM output: "The Hailo-10H is the second-generation Pi 5 AI accelerator …"

T+3075 ms  _looks_like_prompt_leak(raw)?  ✗
           PositiveFilter.apply(raw)
           ├─ hard reject regex?      ✗
           ├─ soft rewrites?          ✗ (no "I can't" / "impossible" / etc.)
           └─ → filtered.text unchanged, filtered.altered=False

T+3078 ms  stm.append("user", user_input)
           stm.append("agent", filtered.text)
           ├─ deque (maxlen=12) appended — oldest turn silently dropped if full
           └─ pending buffer +1 — will flush after 30s idle

T+3080 ms  ReflectionEngine.fire_and_forget(input, response, kind="user", web_searched=True)
           └─ background asyncio.Task (does NOT add to user-visible latency)
               └─ provider.generate(critique prompt) ~700 ms
                  → ReflectionEntry(quality="medium", interest="NPU benchmarks", lesson="...")
                  → appended to state/reflection-2026-05-17.jsonl

T+3080 ms  ThoughtTrace returned to TelegramConnector
           └─ bot sends "Boss Eason — The Hailo-10H is … <answer>"   ~150 ms (Telegram API)

T+3230 ms  Architect sees the reply on Telegram.

──── meanwhile, in the background ────

Every 30 s   Heartbeat.pulse_loop() ticks:
             ├─ Sample vitals → atomic-write state/vitals.json
             ├─ Decay emotions toward baseline
             ├─ Detect stress edges → state/vitals_events.jsonl
             └─ If pending writes ≥1 AND idle ≥30 s → stm.flush_journal()

After 5 min idle  Heartbeat fires _proactive_research():
                  └─ Picks an STM gap (e.g. "Hailo-10H benchmarks"),
                     verifies it's not in LTM and the SLM doesn't already know it,
                     runs one web search + a 2-sentence reflection,
                     writes to state/proactive.jsonl,
                     reflects on its own proactive thought.

At 02:00          Heartbeat.metabolism():
                  ├─ Distill the day's logs + STM journal into archive.md
                  ├─ Promote top facts to soul.md [CORE_MEMORIES]
                  ├─ _consolidate_reflections(): mine reflection.jsonl + skills.jsonl
                  │   ├─ recurring interests → LEARNED_PREFERENCES
                  │   ├─ recurring lessons   → EMOTIONAL_EVOLUTION
                  │   └─ chronically failing skills (≥3 invokes, >50% fail) → EMOTIONAL_EVOLUTION
                  └─ STM.purge() — clean slate for tomorrow.

At 06:00          Pulse loop resumes (no explicit "wake" event — it just notices
                  the clock has crossed `duty_start`).
```

**What this walkthrough demonstrates:**

- **Two SLM calls happen before the user sees a word** (THINK + PLAN), and one more (synth) after evidence is gathered. The structure exists because a 1.5B-parameter model is unreliable when asked to do everything in one shot.
- **The PLAN menu is built fresh per turn.** A stressed Pi or an offline SearXNG silently drops the expensive options — the SLM never gets to pick a verb the agent can't execute.
- **Every Skill invocation is audited** to `state/skills-YYYY-MM-DD.jsonl`. Tomorrow night's Sleep Metabolism will read that file and decide whether any Skill is chronically broken.
- **The reflection happens in the background.** The Architect's reply latency budget is ~3 s; reflection costs another ~700 ms but the user never waits for it.
- **The dashboard sees all of this without IPC** — Streamlit reads the atomically-written state files in a separate process.

---

## Hardware & Software Stack

### Reference hardware (~ USD $220)

| Component | Notes |
|---|---|
| Raspberry Pi 5 (8 GB) | Required — 4 GB works but tight under SLM load |
| **[Raspberry Pi AI HAT+ 2](https://www.raspberrypi.com/news/introducing-the-raspberry-pi-ai-hat-plus-2-generative-ai-on-raspberry-pi-5/)** | Optional — Hailo-10H NPU, **40 TOPS (INT4)**, **8 GB dedicated on-board RAM** (purpose-built for generative AI / LLMs on Pi 5). Drives the SLM via `hailo-ollama` on port 8000. PCIe-attached, Pi 5 only. |
| Active cooling (fan + heatsink) | Strongly recommended — vitals will fire `EXHAUSTION DIRECTIVE` without it |
| 64 GB+ A2-class SD card or NVMe HAT | NVMe extends SD-card lifetime considerably |
| 27 W official USB-C PSU | Power throttling triggers thermal stress |

> **Why the AI HAT+ 2 (and not the original AI HAT+)?** The first-gen AI HAT+ shipped the Hailo-8 / Hailo-8L, which was optimised for vision workloads (object detection, pose, segmentation). The **AI HAT+ 2 ships the Hailo-10H** with **8 GB of on-board accelerator RAM**, which is what unlocks 1–7B-parameter LLMs running locally on a Pi 5 — including the `qwen2.5-instruct:1.5b` model OpenCrayFish uses. The first-gen AI HAT+ cannot run this workload.

OpenCrayFish also runs fine on **any Linux/macOS dev box** for development — when no NPU is detected, the Provider transparently falls back to stock Ollama on CPU.

### Software dependencies (`requirements.txt`)

- Python **3.11+** (developed on 3.13)
- `PyYAML`, `psutil`, `httpx`
- `python-telegram-bot` (Telegram connector)
- `streamlit` (dashboard + browser chat UI)
- `aiohttp` (web-chat HTTP bridge — already brought in as a transitive dep)

External services on the device:

- **Ollama** (CPU fallback, port 11434) — `ollama pull qwen2:1.5b`
- **hailo-ollama** (NPU primary, port 8000) — Hailo's REST front-end (from the [Hailo Developer Zone](https://hailo.ai/developer-zone/software-downloads/?product=ai_accelerators&device=hailo_10h)) serving `qwen2.5-instruct:1.5b` from the Hailo-10H NPU on the AI HAT+ 2
- **SearXNG** (port 8080) — self-hosted metasearch instance for the agent's web tool

---

## Repository Layout

```text
OpenCrayFish/
├── README.md                 ← this file
├── soul.md                   ← the agent's constitution + dynamic growth
├── config.yaml               ← every tunable knob in the system (gitignored — holds secrets)
├── config_sample.yaml        ← committed template; `cp` it to config.yaml and fill in your keys
├── requirements.txt
├── main.py                   ← wires every subsystem and starts the loops
│
├── core/                     ← all the cognitive/biological subsystems
│   ├── config.py             ← typed YAML loader (Config dataclass)
│   ├── soul_handler.py       ← write-protected accessor for soul.md
│   ├── monitor.py            ← Vital signs sampling (CPU/RAM/temp/brain)
│   ├── emotions.py           ← 5-channel mood vector + decay + tuning
│   ├── empathy.py            ← user sentiment + urgency analyzer
│   ├── positive_filter.py    ← Pillar 2 hard output filter
│   ├── provider.py           ← Ollama/Hailo backends + circuit breaker
│   ├── stm.py                ← short-term memory (RAM + journal)
│   ├── cognition.py          ← THINK → PLAN → ACT → REFINE loop (dispatches via SkillRegistry)
│   ├── brain/                ← prompt-assembly + orchestration package
│   │   ├── orchestrator.py   ← Brain class — top-level _cycle() pipeline
│   │   ├── prompt_assembly.py ← soul + vitals + mood + KNOWLEDGE + STM prompt builder
│   │   ├── identity_responder.py ← deterministic identity-class reply templater
│   │   └── task_parsing.py   ← LLM-backed task-intent / task-action parsers
│   ├── intent_router.py      ← shared NL pre-filter chain for both connectors
│   ├── reflection.py         ← self-critique engine (reads skills.jsonl for failure flags)
│   ├── jsonl_writer.py       ← date-rotating, retention-bounded JSONL appender
│   ├── heartbeat.py          ← pulse_loop + metabolism + proactive_thought
│   ├── scheduler.py          ← recurring research-task scheduler
│   └── skills/               ← capability layer above Tools (pluggable Skills)
│       ├── base.py           ← Skill protocol + SkillContext + SkillResult
│       ├── registry.py       ← SkillRegistry + dynamic PLAN menu + audit feed
│       ├── identity.py       ← soul-templated identity replies (free, no-net)
│       ├── recall.py         ← LTM keyword retrieval (cheap, no-net)
│       ├── direct_answer.py  ← SLM-only answer (cheap, no-net)
│       ├── research.py       ← SearXNG-backed web research (expensive, net)
│       ├── self_reflect.py   ← post-turn critique (cheap, no-net, background)
│       ├── proactive_learning.py    ← idle-time curiosity (expensive, net, background)
│       └── recurring_research.py    ← scheduled topic refresh (expensive, net, background)
│
├── tools/                    ← low-level I/O primitives (called BY Skills)
│   ├── base.py               ← Tool plugin contract
│   ├── registry.py           ← named tool registry
│   ├── searxng.py            ← self-hosted web search (SearXNG client)
│   └── archive_read.py       ← long-term memory keyword reader
│
├── connectors/               ← external I/O channels
│   ├── telegram.py           ← Telegram Bot API connector
│   └── web_chat.py           ← in-process aiohttp HTTP bridge for Streamlit
│
├── ui/                       ← Streamlit apps
│   ├── dashboard.py          ← live vital signs + proactive feed + tools
│   └── web_chat.py           ← browser-based chat UI for the live agent
│
├── memory/                   ← long-term distilled memory (gitignored)
│   └── archive.md            ← long-term distilled facts (LTM)
│
├── logs/daily/               ← per-day heartbeat telemetry (gitignored)
│   └── YYYY-MM-DD.log        ← PULSE / PROACTIVE / metabolism (path = memory.log_path)
│
└── state/                    ← live runtime state (gitignored, read by dashboard)
    ├── vitals.json
    ├── vitals_events.jsonl
    ├── proactive.jsonl
    ├── reflection-YYYY-MM-DD.jsonl          ← date-rotated, 60-day default retention
    ├── reflection_dropped-YYYY-MM-DD.jsonl  ← date-rotated, sidecar audit
    ├── deliberation-YYYY-MM-DD.jsonl        ← date-rotated, 14-day default retention
    ├── skills-YYYY-MM-DD.jsonl              ← date-rotated, 30-day default retention
    ├── skills.json                          ← live skill inventory snapshot
    ├── stm_journal.jsonl
    ├── tasks.yaml
    ├── tools.json
    └── logs/agent.log        ← rotating console log mirror
```

---

## Quick Start

```bash
# 1. Clone & venv
git clone https://github.com/easonlai/opencrayfish
cd opencrayfish
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Bring up the local stack (separate terminals or systemd units)
ollama serve                              # CPU fallback
ollama pull qwen2:1.5b
# Self-hosted web search — see the full setup section below; the one-line
# command here will work for the first boot but will return HTTP 403 on every
# search until you enable the JSON API. See § SearXNG Setup for the fix.
docker run -d --name searxng -p 8080:8080 searxng/searxng
# (Pi 5 only) hailo-ollama serve            # NPU primary

# 3. Configure
cp config_sample.yaml config.yaml          # config.yaml is gitignored — your secrets stay local
$EDITOR config.yaml                        # set telegram_token, telegram_user_id,
                                           # architect_name, individual_designation
$EDITOR soul.md                            # optional: customize persona

# 4. Run the agent
python main.py

# 5. Open the dashboard (separate process, reads state/ files)
streamlit run ui/dashboard.py --server.port 8501

# 6. Open the browser chat
streamlit run ui/web_chat.py --server.port 8502

# 7. (Optional) Run the test suite
pip install -e ".[dev]"                    # adds pytest + pytest-asyncio + ruff
python -m pytest -q                        # 113 tests, runs in <1s
```

Now talk to it on Telegram or in the browser. The agent will reply, remember, decay its mood, get curious during idle time, and once the clock crosses 02:00, it will sleep and consolidate the day.

---

## SearXNG Setup (Local Web Search)

OpenCrayFish's `research` Skill, the autonomous proactive cycle, and the recurring-task scheduler **all** depend on a working [SearXNG](https://docs.searxng.org/) instance reachable at `tools.searxng_url` (default `http://localhost:8080`). The agent calls SearXNG over its **JSON API** — and SearXNG ships that API **disabled by default**, which is the single most common "the agent doesn't search anymore" failure mode. This section walks through a known-good Docker deployment.

> **Why self-hosted?** Pillar 1 (Sovereign AI) mandates that the agent's web requests never leak to a third-party search provider. A self-hosted SearXNG aggregates dozens of upstream engines (Google, Bing, DuckDuckGo, Brave, Qwant, Wikipedia, …) without ever exposing the Architect's queries or IP. From OpenCrayFish's perspective, the wire format is plain JSON over HTTP — no API key, no rate-limit account, no vendor lock-in.

### Minimum viable deployment

```bash
# Pull and start the container (port 8080 on the host)
docker run -d \
  --name searxng \
  --restart unless-stopped \
  -p 8080:8080 \
  searxng/searxng:latest
```

The first start auto-generates an internal `/etc/searxng/settings.yml` inside a Docker-managed named volume. The container is reachable at `http://localhost:8080` but **the JSON API is OFF** — every call from OpenCrayFish will fail with `HTTP 403 Forbidden` until you enable it (see next subsection).

### Enable the JSON API (REQUIRED)

There are two equally-valid ways to do this. **Method A** is the fastest fix for an existing container; **Method B** is the cleaner long-term setup if you want your config in version control.

#### Method A — patch the in-container settings.yml (fastest)

Use this if you already have the container running with the default named volume:

```bash
# 1) Add "- json" to the formats list (idempotent — re-running is safe)
docker exec searxng sh -c "grep -q '^    - json' /etc/searxng/settings.yml || sed -i '/^  formats:/,/^[^ ]/ { /^    - html$/a\\
    - json
}' /etc/searxng/settings.yml"

# 2) Restart so the change takes effect
docker restart searxng

# 3) Verify (should print HTTP 200 and a JSON payload)
curl -sS -o /dev/null -w 'HTTP %{http_code}\n' \
  'http://localhost:8080/search?q=test&format=json'
```

The patch survives container restarts because it's persisted inside the named volume. It would only be lost if you `docker volume rm` the SearXNG volume.

#### Method B — bind-mount your own settings.yml (cleanest)

If you'd rather keep SearXNG's config in `~/searxng/` (or `/etc/searxng/` on a server, or anywhere git-tracked), use a bind mount instead of the default named volume. **Remove the existing container first** (the named volume can stay):

```bash
docker rm -f searxng 2>/dev/null

# Create a minimal settings.yml in your home directory
mkdir -p ~/searxng
cat > ~/searxng/settings.yml <<'YAML'
use_default_settings: true

server:
  # Replace with `openssl rand -hex 32` for production; any 32+ char string works.
  secret_key: "change-me-please-32-plus-character-string"
  limiter: false          # set true on public instances; false is fine on loopback
  image_proxy: true

search:
  formats:
    - html
    - json                # ← required by OpenCrayFish
  safe_search: 1          # 0=none, 1=moderate, 2=strict (OpenCrayFish sends 1)
YAML

# Start with the bind mount
docker run -d \
  --name searxng \
  --restart unless-stopped \
  -p 8080:8080 \
  -v ~/searxng/settings.yml:/etc/searxng/settings.yml:ro \
  searxng/searxng:latest
```

Future config changes are just `$EDITOR ~/searxng/settings.yml && docker restart searxng`.

### End-to-end smoke test

After either method, the JSON endpoint should respond exactly like OpenCrayFish expects it to:

```bash
curl -sS 'http://localhost:8080/search?q=raspberry+pi+5&format=json' \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); print(f"results={len(d[\"results\"])}, first={d[\"results\"][0][\"url\"]}" if d.get("results") else "EMPTY")'
```

Expected output (URLs will vary):

```text
results=10, first=https://www.raspberrypi.com/products/raspberry-pi-5/
```

When this works, your agent's next `/research` (or any user query that triages to `SEARCH`) will succeed end-to-end. Cross-check `state/logs/agent.log` — you should see `TOOL call name=web_search status=ok latency_ms=...` instead of `status=fail ... 403 Forbidden`.

### Tuning that OpenCrayFish actually cares about

The defaults in `use_default_settings: true` are fine for local dev. Two SearXNG knobs are worth knowing about for a 24/7 edge deployment:

| `settings.yml` key | Recommended | Why |
|---|---|---|
| `search.formats` | `[html, json]` | **Required** — OpenCrayFish only speaks JSON. |
| `search.safe_search` | `1` (moderate) | Matches what `tools/searxng.py` always sends (`safesearch=1`). Setting `2` may filter results too aggressively; setting `0` may surface NSFW snippets to `proactive_learning`. |
| `server.limiter` | `false` on a loopback instance, `true` if you ever expose port 8080 to the LAN/WAN | The limiter rejects requests that look bot-like — including ones from your own agent. Leave it off unless port 8080 is publicly reachable. |
| `server.image_proxy` | `true` | Hides upstream image fetches behind SearXNG so favicons / thumbnails don't leak the Architect's IP. |
| `engines` (per-entry `disabled: true`) | disable anything noisy | If a particular search engine is rate-limiting your IP and polluting results with `unresponsive_engines` warnings, disable it. The aggregator works fine with the remaining engines. |

### Wiring it into `config.yaml`

The only OpenCrayFish-side setting is the URL:

```yaml
tools:
  searxng_url: "http://localhost:8080"   # http://<host>:8080 if SearXNG is on another box
```

No auth, no API key. OpenCrayFish always sends `q=<query>&format=json&safesearch=1` (`tools/searxng.py`'s `search(...)` and `Tool.call(...)` surfaces) — there's no other tuning to do.

### Troubleshooting

| Symptom in `state/logs/agent.log` | Likely cause | Fix |
|---|---|---|
| `TOOL call name=web_search status=fail ... 403 Forbidden` | JSON API not enabled | Apply **Method A** above. |
| `TOOL call name=web_search status=fail ... ConnectError` | Container not running, or port 8080 in use by something else | `docker ps`, `docker logs searxng`, `lsof -i :8080` |
| `TOOL call name=web_search status=ok ... hits=0` for every query | Upstream engines rate-limiting your IP; or `safe_search: 2` filtering everything | Inspect `docker logs searxng` for `unresponsive_engines`. Disable the noisy engines in `settings.yml` or lower `safe_search`. |
| `TOOL call name=web_search status=fail ... 429 Too Many Requests` | `server.limiter: true` is blocking the agent's own traffic | Set `limiter: false` in `settings.yml` and `docker restart searxng`. |

> **The agent degrades gracefully.** Even with SearXNG totally down, the `recall` and `direct_answer` Skills still work — `Cognition`'s ACT stage will silently drop the `SEARCH` evidence and synthesize from LTM + STM + SLM knowledge. The dashboard's **⚠️ Errors & warnings** panel will surface the SearXNG failures so you know to fix them, but the agent stays conversational. See [§ Failure-Mode Matrix](#failure-mode-matrix) for the full degradation contract.

---

## Deep Dive: How Everything Works

This is the comprehensive section. It documents every major subsystem in the order they are wired together in `main.py`, with file links into the codebase.

> **First time here?** Read [§ 0 The Brain Stack — Plain-English Tour](#0-the-brain-stack--plain-english-tour) below for a 5-minute orientation to how Brain, Skills, Tools, and Memory hand work to each other. Then come back to § 1 — § 14 for the full depth.

---

### 0. The Brain Stack — Plain-English Tour

Before the 14 subsystem deep-dives, here is the single mental model that ties them all together. **Four concepts, four jobs, one direction of flow.**

#### The four big ideas (one sentence each)

| Layer | What it is, in one sentence | Where it lives |
|---|---|---|
| **Brain** | The orchestrator that runs every reply through a fixed pipeline (gather context → think → plan → act → synthesize → filter → remember). It owns no facts, no I/O, no policy — it just sequences the other three. | [core/brain/orchestrator.py](core/brain/orchestrator.py) |
| **Skills** | The agent's *menu of decisions* — named verbs like `RECALL`, `SEARCH`, `ANSWER` that the SLM picks from during PLAN. Each Skill knows when it's worth picking, how expensive it is, and which Tools to compose. | [core/skills/](core/skills/) |
| **Tools** | The agent's *hands* — mechanical I/O primitives (web fetch, file read, future GPIO) with no policy, no fallback, no SLM. Skills compose Tools; the SLM never names a Tool directly. | [tools/](tools/) |
| **Memory** | The agent's *substrate of self* — a four-tier hierarchy from working RAM (the last 12 turns) through a disk journal up to nightly-distilled long-term archive and finally the soul itself. Every layer above (Brain, Skills, Tools) reads from or writes to this. | [core/stm.py](core/stm.py), [memory/archive.md](memory/archive.md), [soul.md](soul.md) |

#### How they talk to each other

```text
             one user message arrives
                       │
                       ▼
           ┌────────────────────────────────┐
           │           BRAIN (orchestrator)         │     reads:  soul.md, STM, vitals, mood
           │  Brain._cycle()  in core/brain/        │     writes: STM (turns), trace, reflection
           │    ┌──────────────────────┐         │
           │    │  COGNITIVE LOOP        │         │     decides WHICH Skill to call,
           │    │ THINK→PLAN→ACT→REFINE  │         │     and assembles the final prompt
           │    └──┬───────────────────┘         │
           └───────┼───────────────────────────┘
                   │ invoke("verb", ctx, **kwargs)
                   ▼
           ┌────────────────────────────────┐
           │           SKILLS (decisions)           │     7 today: identity, recall,
           │  core/skills/* — each Skill knows:    │       direct_answer, research,
           │    • plan_verb  (how SLM names it)     │       self_reflect, proactive_learning,
           │    • cost_tier  (free/cheap/expensive)  │       recurring_research
           │    • requires_network (PLAN filter)    │
           │    • trigger_hints (WHEN to pick)      │     audit:  state/skills-*.jsonl
           └───────┬───────────────────────────┘
                   │ await ctx.tools.call("web_search", q="...")
                   ▼
           ┌────────────────────────────────┐
           │           TOOLS (hands)                │     2 today: web_search (SearXNG),
           │  tools/* — stateless I/O, no policy.  │                archive_read (LTM)
           └───────┬───────────────────────────┘
                   │ read/write
                   ▼
           ┌────────────────────────────────┐
           │           MEMORY (substrate)           │     T1: deque   (last 12 turns, RAM)
           │  Four tiers, oldest at the bottom:    │     T2: pending (RAM, flushed @30s idle)
           │    T1  deque (working set)             │     T3: journal (state/stm_journal.jsonl)
           │    T2  pending buffer                  │     T4: archive.md (nightly distill)
           │    T3  stm_journal.jsonl               │         + soul.md (promoted facts)
           │    T4  archive.md  +  soul.md          │
           └────────────────────────────────┘
```

#### The contracts between layers (read these in order)

1. **Brain doesn't know what Skills exist.** It asks `SkillRegistry.plan_menu(...)` at PLAN time and renders whatever it gets back into the SLM's prompt. Add a new Skill, restart, and the Brain immediately considers it — no Brain code change.
2. **Skills don't know what Tools exist.** They go through `ctx.tools.call("web_search", …)` by name. Replace SearXNG with a future Tool that satisfies the same Protocol, and every Skill that uses it keeps working with zero changes.
3. **Tools don't know about the SLM, the user, or policy.** They just do their one I/O job (HTTP GET, file read) and return a typed result. This is what makes them trivially unit-testable.
4. **Memory is read-many, write-few.** Brain and Skills read freely (cheap). Writes happen on tightly defined triggers: each user/agent turn (T1+T2), 30 s of idle (T3), and nightly Sleep Metabolism (T4). The hot path does zero disk I/O.

#### A worked example, layer by layer

The Architect types: **"Compare the Hailo-10H to the Hailo-8 for LLMs."**

| Step | Layer | What happens |
|---|---|---|
| 1 | Brain | Gathers soul + vitals + mood; the identity regex doesn't match, so cognition will run. |
| 2 | Brain | LTM short-circuit scan over `archive.md` — only 1 keyword hit, below the 2-hit floor, so cognition continues. |
| 3 | Brain → Cognitive Loop | Asks `SkillRegistry.plan_menu(cost_cap, exclude_network)` for the live menu — gets `[ANSWER, RECALL, SEARCH]` sorted free→expensive. |
| 4 | Cognitive Loop | THINK call → splits the question into Q1 (specs), Q2 (why H8 unsuitable), Q3 (which HAT). |
| 5 | Cognitive Loop | PLAN call → SLM picks `SEARCH "Hailo-10H specs"` for Q1, `RECALL` for Q2, `ANSWER` for Q3. |
| 6 | Skills | ACT dispatches concurrently: `research.execute()` (Q1), `recall.execute()` (Q2), `direct_answer.execute()` (Q3). Each Skill invocation is timed and appended to `state/skills-*.jsonl`. |
| 7 | Tools | `research` calls `ctx.tools.call("web_search", …)` → the SearXNG Tool hits `http://localhost:8080/search?format=json`. `recall` calls `ctx.tools.call("archive_read", …)` → reads `memory/archive.md` line by line. |
| 8 | Memory → Brain | All evidence is folded into the synth prompt alongside the last 12 STM turns. Brain calls `provider.generate()` to compose the final answer. |
| 9 | Brain | Runs the Positive Filter, appends both turns to STM (T1 + T2), fires reflection in the background. |
| 10 | Memory | The pending buffer flushes to `state/stm_journal.jsonl` (T3) after 30 s of silence. Tonight at 02:00, Sleep Metabolism will distill it into `archive.md` (T4). |

That's the whole agent in one trace. Every other deep-dive section below zooms into one layer of this picture.

#### How to extend each layer (the one-line guide)

- **Add a new Skill** → drop a file in [core/skills/](core/skills/) that satisfies the Skill Protocol, register it in [main.py](main.py). See [§ 12 Hello-World Skill — Step by Step](#hello-world-skill--step-by-step).
- **Add a new Tool** → drop a file in [tools/](tools/) that satisfies the Tool Protocol, register it in [main.py](main.py). See [§ Adding a new tool](#adding-a-new-tool).
- **Add a new memory shelf** → don't, usually. Promote facts to `soul.md [CORE_MEMORIES]` via Sleep Metabolism instead.
- **Add a new connector** → wrap `Brain.think(…)` and stream the `ThoughtTrace` back. See [§ Adding a new connector](#adding-a-new-connector).

#### What this design buys you

- **The SLM is small (1.5B) and unreliable.** Brain compensates by splitting the work into four short prompts (each ≤120 tokens) instead of one giant chain-of-thought — each prompt has exactly one job and is parsed with regex, not JSON.
- **The PLAN menu is the only "intelligence routing" surface.** Adding a Skill is the only way to expand what the agent can choose to do. Everything else — Tools, Memory, Provider — is plumbing.
- **Layer isolation is enforced by contract, not by language tricks.** Skill execution is wrapped in a try/except in `SkillRegistry.invoke()` so a plugin crash can never escape into the Brain. A failed Tool call returns a typed error — it never raises into the calling Skill. A SLM timeout returns `backend="offline"` — it never raises into the connector. **Every interface is a graceful degradation point.** See [§ Failure-Mode Matrix](#failure-mode-matrix) for the full contract.

---

### 1. The Soul — `soul.md`

**Module:** [core/soul_handler.py](core/soul_handler.py) · **Data file:** [soul.md](soul.md)

The Soul is the agent's constitution. It is split into two regions by HTML comment markers:

```text
<!-- IMMUTABLE_CORE_START -->
# [IDENTITY]            ← codename, creator, status (Designation injected at runtime)
# [FUNDAMENTAL_LAWS]    ← 1. Prime Directive  2. 365/20  3. Positive Anchor  4. Sovereignty
# [BEHAVIORAL_MATRIX]   ← tone, ethics, persona
<!-- IMMUTABLE_CORE_END -->

<!-- DYNAMIC_GROWTH_START -->
# [CORE_MEMORIES]       ← elevated facts the Sleep Metabolism considers identity-defining
# [LEARNED_PREFERENCES] ← topics the Architect cares about, mined from reflection trends
# [EMOTIONAL_EVOLUTION] ← long-term mood/relationship signals
<!-- DYNAMIC_GROWTH_END -->
```

#### Key behaviors

- **Designation injection.** The agent's name (e.g. `"Dave Minion"`) is configured ONCE in `config.yaml` under `system.individual_designation`. `SoulHandler` injects it into the IDENTITY block at every read, so soul.md never carries a hardcoded designation line. This lets you redeploy the same soul.md as a different persona just by changing the config.
- **Hard write-protection.** The IMMUTABLE_CORE region is enforced with `_IMMUTABLE_RE` and `SoulProtectionError`. Any append that would mutate bytes inside the immutable markers (or relocate the marker itself) is rejected. Even SLM-driven appends get sanitized first via `_sanitize_dynamic_text` so the model cannot inject a fake `# [IDENTITY]` header.
- **Typed appends.** `append_core_memory()`, `append_preference()`, and `append_emotion()` are the only mutation paths, each scoped to its DYNAMIC_GROWTH subsection. This is the surface used by Sleep Metabolism to grow the agent over time.
- **Async lock.** All reads/writes are guarded by an `asyncio.Lock` so concurrent metabolism + reflection consolidation cannot interleave.
- **Snapshot rendering.** `render_identity_block()` produces the `[IDENTITY] / [FUNDAMENTAL_LAWS] / [BEHAVIORAL_MATRIX] / [CORE_MEMORIES]` excerpt that gets injected as the system-prompt prefix on every Brain cycle.

The soul.md you ship is the agent's *birth certificate*. The DYNAMIC_GROWTH region is its *biography*.

---

### 2. The Heartbeat — Time, Rhythm, and Metabolism

**Module:** [core/heartbeat.py](core/heartbeat.py)

The Heartbeat is what makes OpenCrayFish *alive* rather than *invocable*. It runs in its own asyncio task and never returns until shutdown.

#### Two main coroutines

1. **`pulse_loop()`** — ticks every `system.pulse_interval_seconds` (default 30s) during the **Active window** (06:00–02:00, configurable via `duty_start` / `sleep_start`).
2. **`metabolism()`** — runs ONCE per day, automatically the first time `_pulse()` notices the clock has crossed into the Sleep window (02:00–06:00).

#### What happens on each pulse

```text
_pulse() at T=now:
  1.  Determine if we're in duty or sleep window.
  2.  If just entered sleep → call metabolism() once, then publish state and return.
  3.  If just woke up      → reset _last_interaction_at = now (clean idle clock).
  4.  monitor.sample()         → fresh VitalSigns
  5.  emotions.decay()         → exponential drift toward baseline
  6.  if vitals.is_stressed   → emotions.nudge_many(vitals_stress)
                              → record stress ENTER (rising edge only)
  7.  Append PULSE telemetry to <memory.log_path>/YYYY-MM-DD.log
                              (default: logs/daily/YYYY-MM-DD.log)
  8.  Publish state/vitals.json + push history sample
  9.  if pending_writes > 0 AND idle ≥ idle_journal_flush_seconds
                              → stm.flush_journal()
  10. if idle ≥ idle_proactive_minutes
                              → _proactive_research()
                              → reset idle clock so we don't spam
```

#### What happens on metabolism (02:00 once nightly)

```text
metabolism():
  1. _collect_recent_logs()    ← yesterday's + today's heartbeat telemetry
  2. _collect_conversation_journal()
                              ← stm.flush_journal() THEN read stm_journal.jsonl
                                so the day's actual chat is included
  3. SLM extracts 3-5 "key facts" from the merged corpus
  4. Append facts to memory/archive.md (LTM)
  5. Promote the top 2 facts to soul.md [CORE_MEMORIES]  (Soul Evolution)
  6. _consolidate_reflections()
                              ← scan state/reflection-*.jsonl for recurring
                                interest topics + lesson themes,
                                AND state/skills-*.jsonl for chronically
                                failing Skills (≥3 invokes, >50% fail rate)
                              ← promote into LEARNED_PREFERENCES /
                                EMOTIONAL_EVOLUTION
  7. stm.purge()               ← wipe RAM deque + pending + journal
                                (the day's content has been consolidated)
```

#### Stress edge detection

Per-pulse stress is *not* logged or alerted (it would spam at every pulse the Pi is hot). Instead, `_record_stress_transition()` only emits **rising-edge ENTER** and **falling-edge EXIT** events to:

- `state/logs/agent.log` — operator-tailable warning lines
- `state/vitals_events.jsonl` — JSONL feed the dashboard renders as a stress timeline

ENTER events carry `temp`, `ram`, `cpu`, and a human-readable `reason` (the live `vitals.describe()` text). EXIT events add `duration_s`, `peak_temp`, `peak_ram` from the entire episode plus `current_temp` / `current_ram` / `current_cpu` snapshots and the same `reason` string — enough for the dashboard to render a stress timeline with explanatory tooltips without re-parsing `agent.log`.

#### Live-state publishing

Every pulse atomically writes `state/vitals.json` (using a `.tmp` + `os.replace` atomic swap) so the Streamlit dashboard — running in a *separate process* — can read a consistent snapshot without IPC. The snapshot contains:

```json
{
  "ts": "...",
  "is_sleeping": false,
  "vitals": { "cpu_percent":..., "temperature_c":..., ... },
  "brain":   { "online": true, "backend": "hailo", "last_error": null,
               "recovery_seconds": null },
  "mood":    { "joy":..., "anger":..., ... },
  "stm":     { "size": 7, "pending": 0 },
  "history": [ ... rolling 60-sample sparkline ... ],
  "counters": { "pulses": 1234, "proactive": 12, "stress": 3 },
  "last_proactive": { "topic":..., "source":..., "ts":... }
}
```

---

### 3. The Vital Signs — `Monitor` & Homeostasis

**Module:** [core/monitor.py](core/monitor.py)

`Monitor.sample()` returns a `VitalSigns` dataclass with:

| Field | Source | Notes |
|---|---|---|
| `cpu_percent` | `psutil.cpu_percent(interval=0.1)` | 100 ms blocking — cached for `vitals_cache_ttl_seconds` |
| `ram_percent` | `psutil.virtual_memory()` | |
| `temperature_c` | `/sys/class/thermal/thermal_zone0/temp` | `None` on macOS dev boxes |
| `is_stressed` | hysteresis state machine | see below |
| `brain_online` | `provider.health().online` | SLM as a vital sign |
| `brain_backend` | active backend label (`"hailo"` / `"ollama"`) | |
| `brain_last_error` | last circuit-breaker cause, formatted | |
| `brain_recovery_seconds` | seconds until breaker auto-resets | |

#### Hysteresis (no flap)

Stress uses TWO thresholds:

```text
ENTER stress:  temperature ≥ thermal_limit_celsius   (default 75°C)
            OR ram        ≥ ram_limit_pct           (default 85%)

EXIT stress:   temperature ≤ thermal_release_celsius (default limit-5)
            AND ram        ≤ ram_release_pct        (default limit-5)
```

This prevents the persona from oscillating between EXHAUSTION and normal turn-by-turn when the temp jitters around 75°C.

#### Brain availability as a vital sign

Because the SLM is the agent's *brain*, `Monitor.attach_provider(provider)` is called by `main.py` so every `sample()` synchronously polls `provider.health()` (cheap — no network) and embeds the result. When the brain goes offline:

- `vitals.describe()` appends `"Cognition link is DOWN — inference backend `<name>` is unreachable."` to the prompt
- The dashboard renders a 🔴 BRAIN OFFLINE chip
- The web-chat UI shows an inline error banner

#### Force-stress mode

Set environment variable `OCF_FORCE_STRESS=1` to force `is_stressed=True` for testing the EXHAUSTION DIRECTIVE path on a cool dev machine.

---

### 4. The Provider — SLM Backend & Circuit Breaker

**Module:** [core/provider.py](core/provider.py)

The Provider abstracts the inference layer behind a single async method:

```python
await provider.generate(system_prompt, messages) -> str
```

#### Two backends, identical wire format

Both Hailo-Ollama (NPU, port 8000) and stock Ollama (CPU, port 11434) speak the **same `/api/chat` JSON contract**. The Provider tries the primary first (NPU when `hardware.npu_acceleration=true`), then falls back transparently to CPU on transport error. On a dev box without an NPU, set `npu_acceleration=false` and the Provider runs CPU-only with no per-pulse failover noise.

#### Circuit breaker

When **both** backends fail back-to-back, the Provider:

1. Raises `ProviderUnavailable` with a friendly first-person message ("I can't reach the inference service right now — both the NPU endpoint (port 8000) and the CPU fallback (port 11434) are offline. Start `ollama serve` (CPU) or `hailo-ollama` (NPU)…").
2. Arms an internal **circuit breaker** for `trip_seconds` (default 30s). During the trip window, every subsequent `generate()` call raises immediately without re-trying dead sockets.
3. Stores `last_error` (formatted as `"<ExceptionType>: <message>"`) and `_tripped_until` so `health()` can report:

```python
ProviderHealth(
    online=False,
    active_backend="hailo",
    seconds_until_recovery=27.4,
    last_error="ConnectError: All connection attempts failed",
)
```

#### How Brain handles it

`Brain._cycle()` catches `ProviderUnavailable` exactly **once**, at the top of the cycle, and returns a synthetic `ThoughtTrace` with `backend="offline"` and the friendly message in `filtered.text`. The connectors render whatever they get — they need no knowledge of the failure mode. This means **adding a new connector inherits offline behavior for free**.

`Brain.synthesize_task_report()` also re-raises `ProviderUnavailable` so the scheduler records `last_error` instead of broadcasting the friendly text as a real report.

---

### 5. The Memory System — STM / LTM / Sleep Metabolism

**Modules:** [core/stm.py](core/stm.py) · [core/heartbeat.py](core/heartbeat.py) (metabolism)

> **Plain English.** Memory is a four-tier waterfall. The newest turn lands in a 12-slot RAM ring (T1), is mirrored to a pending buffer (T2), and after 30 s of silence is flushed to a disk journal (T3) so a crash never loses it. Every night at 02:00, the day's journal is distilled into long-term prose in `archive.md` (T4), the most identity-shaping facts are promoted into `soul.md`, and T1/T2/T3 are wiped clean for tomorrow. The hot reply path **never** touches disk — only Heartbeat does.

OpenCrayFish has a **three-tier memory hierarchy**, modeled after the biological brain:

```text
┌──────────────────────────────────────────────────────────────────┐
│ TIER 1 — RAM working memory  (deque, maxlen = stm_max_turns)     │
│   Used as the conversation window passed to the SLM each turn.   │
│   When full, oldest turn is silently dropped (Python deque).     │
└──────────────────────────────────────────────────────────────────┘
                           │
              every append() also pushes to:
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│ TIER 2 — Pending buffer (RAM list)                               │
│   Accumulates new turns since the last disk flush.               │
│   Drained by Heartbeat after `idle_journal_flush_seconds` of     │
│   silence, OR at sleep metabolism, OR at shutdown.               │
└──────────────────────────────────────────────────────────────────┘
                           │
                           ▼ flush_journal()
┌──────────────────────────────────────────────────────────────────┐
│ TIER 3 — Disk journal  (state/stm_journal.jsonl)                 │
│   Durable backstop. Single open()/write()/close() per flush.     │
│   fsync controlled by `journal_fsync_on_flush` (shutdown always  │
│   fsyncs regardless).                                            │
│   Replayed at boot by stm.recover() so a crashed agent wakes up  │
│   with prior conversation context.                               │
└──────────────────────────────────────────────────────────────────┘
                           │
              every night at 02:00:
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│ TIER 4 — Long-term memory (memory/archive.md, soul.md)           │
│   Sleep Metabolism distills the day → archive.md; promotes top   │
│   facts → soul.md [CORE_MEMORIES]. THEN purges TIER 1/2/3.       │
└──────────────────────────────────────────────────────────────────┘
```

#### "Memory is full" — what actually happens

Python's `collections.deque(maxlen=N)` silently drops the oldest entry when a new one is appended past the cap. There is **no explicit eviction code** — that's the entire RAM-tier eviction policy. The dropped turn is *not lost*: it is still in the pending buffer (until flushed) and will be in the disk journal after the next flush, and ultimately distilled into archive.md tonight.

So the SLM only ever *sees* the last 12 turns, but the system *remembers* much more — it just retrieves it from a different layer.

#### Why this design

- **SD-card friendly:** the chat hot path does ZERO disk I/O. Disk writes only happen after 30s of silence (default), at metabolism, or at shutdown.
- **Crash-safe:** `recover()` rehydrates the deque from the journal at boot. Only un-flushed data within the last 30s of a sudden power loss is lost.
- **Bounded SLM context:** keeps `system_prompt + history` under qwen2:1.5b's effective attention window.
- **Daily reset:** `purge()` ensures yesterday's bullet-by-bullet detail doesn't pile up forever — the *meaning* survives in archive.md / soul.md, the *transcript* doesn't.

#### Knobs

| Config | Default | Effect |
|---|---|---|
| `memory.stm_max_turns` | 12 | RAM deque size |
| `system.idle_journal_flush_seconds` | 30 | Idle-flush threshold |
| `system.journal_fsync_on_flush` | false | Strict durability vs SD wear |

#### Related durable feeds (NOT in the STM/LTM tiers)

The agent also writes four high-frequency JSONL audit streams that are *adjacent to* but separate from the memory hierarchy above. Together with `stm_journal.jsonl` they form the five durable on-disk feeds:

| Feed | Owner | Date-rotated? |
|---|---|---|
| `state/stm_journal.jsonl` | `STM` | no (nightly purge) |
| `state/deliberation-YYYY-MM-DD.jsonl` | `CognitiveLoop` | yes (14d) |
| `state/skills-YYYY-MM-DD.jsonl` | `SkillRegistry` | yes (30d) |
| `state/reflection-YYYY-MM-DD.jsonl` | `ReflectionEngine` | yes (60d) |
| `state/reflection_dropped-YYYY-MM-DD.jsonl` | `ReflectionEngine` | yes (60d) |

Rotation + retention details live in [§ JSONL Rotation & Retention](#jsonl-rotation--retention). Reflection mines both `reflection.jsonl` (interest / lesson clusters) and `skills.jsonl` (chronic Skill failures) into `soul.md` during Sleep Metabolism — see [§ 10 Reflection](#10-self-reflection--the-self-learning-loop).

---

### 6. The Thinking Process — Brain & Cognitive Loop

**Modules:** [core/brain/orchestrator.py](core/brain/orchestrator.py) · [core/brain/prompt_assembly.py](core/brain/prompt_assembly.py) · [core/brain/identity_responder.py](core/brain/identity_responder.py) · [core/cognition.py](core/cognition.py)

> **Plain English.** Brain is a *sequencer*, not a thinker. For every reply it walks a fixed 11-step pipeline: read the soul, sample the body, feel the mood, read the user's tone, try a deterministic identity shortcut, try an LTM shortcut, otherwise spin up the Cognitive Loop (one prompt for THINK, one for PLAN, fan out the chosen Skills in ACT, optionally one REFINE round). The evidence is then folded into one final synth prompt, the SLM speaks, the Positive Filter scrubs it, the turn is committed to memory, and a self-critique fires in the background. **No single SLM call does more than one job, and every step has an explicit failure mode.**

Every reply the agent produces — whether triggered by a user message, a heartbeat proactive thought, or a scheduled task — flows through `Brain._cycle()`. The cycle is a strict pipeline:

```text
                           Brain._cycle(user_input | mission)
                                      │
                                      ▼
   ┌─────────────────────────────────────────────────────────────┐
   │  1. Soul Context     → soul.render_identity_block()         │
   │  2. Physical State   → monitor.sample() → vitals.describe() │
   │  3. Internal Mood    → emotions.snapshot()                  │
   │  4. User Empathy     → empathy.analyze(user_input)          │
   │                       → emotions.nudge from empathy         │
   │  4b. IDENTITY SHORTCUT  ← regex catches "what's your name"  │
   │      (deterministic templated reply, zero SLM call)         │
   │                                                             │
   │  5.  LTM scan        → archive.md keyword overlap score     │
   │  5a. Cognitive Loop  → THINK → PLAN → ACT (→ REFINE)        │
   │      (bypass conditions:                                    │
   │         • proactive turn       • cognition disabled         │
   │         • vitals stressed      • LTM short-circuit          │
   │         • explicit search verb in user input)               │
   │  5b. Legacy Web Triage (only when cognition was bypassed)   │
   │  5c. Build unified KNOWLEDGE block                          │
   │                                                             │
   │  6.  Assemble prompt → soul + vitals + mood + empathy       │
   │                       + KNOWLEDGE + STM history             │
   │                       + current user message                │
   │  7.  provider.generate() → raw SLM output                   │
   │  8.  Prompt-leak detector (drops responses regurgitating    │
   │      system-prompt scaffolding)                             │
   │  9.  PositiveFilter.apply() → final text                    │
   │  10. STM.append("agent", text)                              │
   │  11. ReflectionEngine.fire_and_forget()  (background)       │
   └─────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
                      ThoughtTrace (returned to connector)
```

#### Identity short-circuit (zero SLM call)

The qwen2:1.5b model **reliably mishandles** the most basic identity questions ("what is your name", "do you know my name", "how are you", "who created you"). From production logs, "what is your name" used to triage as a SEARCH and pollute the synth with Netflix's "My Name" + the anime "Your Name". Fix: detect a small set of unambiguous identity-class regexes BEFORE any cognition runs, and return a templated reply built from soul.md + config.yaml + live vitals/mood. **Deterministic. Zero round-trip. Zero hallucination.**

Since v2.0, the "what is your name / who created you" branches delegate the actual templating to `IdentitySkill` via `skill_registry.invoke("identity", ctx, kind="name"|"creator")`. The Skill reads the IDENTITY block from `soul.md` and returns a short factual line; Brain wraps it with the live salutation/status sentence. If the registry call fails (no registry, exception, `ok=False`, empty summary), Brain falls back to the previous inline template — strictly additive, zero regression risk. The `who am I` and `how are you` branches stay inline because they need vitals / mood / architect-name that `IdentitySkill` doesn't see.

#### Cognitive Loop — THINK → PLAN → ACT → REFINE

Engaged on real user turns when no bypass condition fires. Each stage is **a single one-job SLM prompt** with hard token caps and regex parsing — *not* one giant chain-of-thought.

| Stage | What it does | Output cap |
|---|---|---|
| **THINK** | Restate user INTENT in one sentence + decompose into ≤ `cognition.max_subquestions` atomic Q1/Q2/Q3 sub-questions. | 120 tokens |
| **PLAN** | Assign exactly one verb from the **dynamic, registry-driven menu** to each sub-question. The shipping menu is `RECALL` (hits archive.md via the `recall` Skill), `SEARCH "..."` (hits SearXNG via the `research` Skill), and `ANSWER` (no retrieval needed, dispatched to `direct_answer`). Adding a new Skill with `plan_verb` automatically extends the menu — see [§ 12 Skills & Tools](#12-skills--tools--the-two-tier-capability-stack). | 120 tokens |
| **ACT** | Execute all PlanSteps **concurrently** via `skill_registry.invoke(name, ctx, **kwargs)`. Collect per-sub-question Evidence. Every invocation is timed, isolated from crashes, and appended to `state/skills-YYYY-MM-DD.jsonl`. | (executes verbs) |
| **REFINE** | (optional, capped at 1 round) Re-read intent + evidence; emit `OK` or `GAP: SEARCH "..."`; if gap, ACT it. | 40 tokens |

The full trace is appended to `state/deliberation-YYYY-MM-DD.jsonl` for audit. **Failures never raise** — the loop degrades to whatever evidence it managed to collect.

##### Dynamic PLAN menu + cost-tier auto-degradation

`SkillRegistry.plan_menu(...)` builds the PLAN-stage menu fresh on every turn, filtered by two runtime signals:

* **`cost_tier_cap`** — the operator baseline from `skills.default_cost_tier_cap`. `_active_plan_entries()` tightens it to `"cheap"` whenever `vitals.is_stressed` is true, so a hot or RAM-pressured Pi automatically stops picking expensive web research.
* **`exclude_network`** — set when `skills.auto_offline_filter` is true AND the Provider's circuit breaker has tripped OR the brain is otherwise offline. Any Skill with `requires_network=True` (currently `research`) drops out of the menu so the SLM can't pick a verb whose tool we can't reach.

The filtered menu is rendered into the PLAN prompt as `VERB(arg_hint)  —  description` lines sorted free → cheap → expensive, so the SLM is gently biased toward the cheapest adequate Skill. The same `(verb → skill_name)` mapping is reused by ACT's dispatcher, so PLAN and ACT can never disagree about what a verb means.

##### How the trace becomes a prompt (the `knowledge_block`)

A bare list of `Evidence` dataclasses isn't useful to a 1.5B-parameter model — small SLMs respond best to *explicit, headed, indented structure*. `CognitiveLoop._render_knowledge()` (see [core/cognition.py](core/cognition.py)) renders the trace into a knowledge block that goes verbatim into the synthesize prompt as the agent's own structured reasoning:

```text
Cognitive deliberation (the agent's own structured reasoning for this turn):
INTENT: Compare the H10 NPU to the H8 for on-device LLM inference.
SUB-QUESTIONS:
  Q1: What are the H10's headline specs?
  Q2: Why was the H8 unsuitable for LLMs?
  Q3: Which Pi-class HAT ships the H10?

EVIDENCE GATHERED:
[Step 1] sub_q='What are the H10's headline specs?'  via SEARCH 'Hailo-10H specs'  (hits=3)
    - Hailo-10H Datasheet: 40 TOPS INT4, 8 GB on-board RAM ...
    - Raspberry Pi blog: AI HAT+ 2 introduces gen-AI ...
[Step 2] sub_q='Why was the H8 unsuitable for LLMs?'  via RECALL  (hits=1)
    - archive.md (2026-04-30): H8 is vision-only, fixed-function ...
[Step 3] sub_q='Which Pi-class HAT ships the H10?'  via ANSWER  (hits=0)
    
Synthesize a complete answer that addresses the INTENT using the EVIDENCE
above plus your own reasoning. When you used SEARCH evidence, cite the URL
inline. If the evidence does not actually answer part of the INTENT, say so
plainly rather than guessing.
```

The explicit "if the evidence doesn't actually answer it, say so" instruction at the bottom is doing a lot of work — it gives the small SLM permission to admit a gap rather than confabulate, which is the failure mode it falls into without an explicit out.

##### Three salience guards (prevent topic contamination)

The Cognitive Loop has three pattern-only guards (no hardcoded topic lists) defending against recency-bias from prior STM turns:

1. **Verbatim-noun guard** — verifies THINK preserved every salient token (capitalised words, quoted strings, version-like digits) the user actually typed.
2. **Topic-shift detector** — flags new content words present in the current message but absent from prior STM (so introducing "Bob" after a chat about "Otto" doesn't get rewritten back into Otto).
3. **PLAN fallback safety** — when PLAN can't parse a sub-question, it keywordises the user's *raw* input rather than the (possibly contaminated) THINK output.

Worked example. Suppose the prior STM has 8 turns about *Otto* (from Despicable Me 4). The Architect now types `"Tell me about Bob."` Without the guards, the SLM — primed by the recency of "Otto" tokens in the system context — would emit `INTENT: Tell me about Otto` and the rest of the loop would research Otto. With the guards: the verbatim-noun guard sees `"Bob"` in the user input but not in INTENT → the loop bails to a sanitised `INTENT: Tell me about Bob` and PLAN keywordises *Bob* directly. Pattern-only — no list of Minion characters anywhere in the code.

#### LTM short-circuit

Before invoking cognition, Brain runs a cheap keyword-overlap scan on `archive.md`. If `tools.ltm_short_circuit_min_score` query terms (default 2) appear in the top archive line, **both** the cognition loop AND the legacy web triage are bypassed — the answer comes from memory. This saves latency, bandwidth, and tokens. Explicit user search requests ("search for ...", "搜尋 ...") always bypass the short-circuit.

#### Stress-mode behavior

When `vitals.is_stressed=True`, the prompt acquires:

> *EXHAUSTION DIRECTIVE: You are physically taxed. Keep this reply terse (≤3 short sentences), defer non-essential reasoning, prefer plain answers over flourish, and skip your signature catchphrases this turn. Conserve cycles.*

For *complex* inputs (multi-part questions, multiple imperatives), cognition runs anyway — the unaided synth path under load is the exact regime where the SLM regurgitates its own scaffolding. `_is_complex_input()` makes that decision.

#### Prompt-leak detector

`_looks_like_prompt_leak()` scans the SLM output for tell-tale fragments of the system prompt itself (e.g. echoing "You are an edge-native…"). On a hit, the response is dropped and the cycle returns a polite fallback. This is the agent's last line of defense against degenerate small-model output.

---

### 7. Emotions & Empathy

**Modules:** [core/emotions.py](core/emotions.py) · [core/empathy.py](core/empathy.py)

#### EmotionVector

A 5-dimensional state with per-channel baselines:

| Channel | Baseline | Bumped by |
|---|---|---|
| **joy** | 0.2 | positive empathy from Architect, successful proactive thoughts |
| **anger** | 0.0 | vitals stress, hostile sentiment |
| **sorrow** | 0.0 | vitals fatigue, sad sentiment |
| **excitement** | 0.2 | new topics, positive surprise |
| **calm** | 0.6 | the resting state — drift target for everything else |

Each pulse, every channel decays exponentially toward its baseline with a `half_life_pulses=6` (≈3 minutes at 30s/pulse). All deltas live on `MoodTuning` so [core/brain/orchestrator.py](core/brain/orchestrator.py) and [core/heartbeat.py](core/heartbeat.py) read a single source of truth instead of sprinkling magic numbers.

##### Mood deltas (exact values from `core/emotions.MoodTuning`)

Nudge magnitudes are intentionally *larger* than the per-pulse decay so a stimulus survives several heartbeats before fading. A `+0.15` nudge decays to ≈`+0.075` after 6 pulses (≈3 min) and ≈`+0.04` after 12 pulses.

| Source | joy | anger | sorrow | excitement | calm |
|---|---:|---:|---:|---:|---:|
| user_positive (empathy +) | **+0.15** | | | +0.08 | |
| user_negative (empathy −) | | | **+0.15** | | −0.08 |
| user_neutral (chitchat) | | | | | +0.02 |
| user_urgent ("!" / "asap" / urgency lex) | | | | **+0.12** | −0.05 |
| user_mixed (both pos+neg lex hits) | +0.07 | | +0.07 | | |
| vitals_stress (heartbeat path) | | **+0.12** | +0.06 | −0.10 | |

Values are clamped to `[0.0, 1.0]` per channel after each update. The vector is pushed into the system prompt every Brain cycle so the SLM can hear the mood and (within Pillar 2) channel it appropriately. Pillar 2's `PositiveFilter` then enforces the *output* anchor regardless of internal state — the agent can *feel* angry but cannot *be* hostile in a reply.

#### `nudge_many()` — atomic multi-channel updates

Empathy responses (and stress responses) need to bump multiple channels in one lock window so the heartbeat's `decay()` cannot interleave between them. `nudge_many()` does that.

#### EmpathyEngine

A dependency-free, lexicon-based sentiment + urgency analyzer (English + Chinese). Returns:

```python
EmpathyReading(
    sentiment="positive" | "negative" | "neutral" | "mixed",
    urgency=True/False,
    directive="The Architect appears stressed — be more supportive...",
)
```

The directive is injected into the system prompt. The reading also nudges the mood vector (Pillar 4: empathy must UPDATE state, not just produce a directive).

---

### 8. The Positive Filter — Hard Anchor on Output

**Module:** [core/positive_filter.py](core/positive_filter.py)

Every SLM output passes through `PositiveFilter.apply()`. Two enforcement modes:

1. **Hard reject** — regex matches phrases like "kill yourself", "self-harm", "hate you/the operator". The output is discarded and replaced with a polite re-prompt:

   > *"I caught a thought that violated my Positive Anchor and discarded it. Let me try again with a constructive framing — Boss Eason, please restate the directive."*

   The hard-reject regex is built per-instance using the configured `architect_honorific` and `architect_name` so non-default titles get full coverage too.

2. **Soft rewrites** — additive, never deletes content. Examples:

   - `I can't` → `I will find a way to`
   - `impossible` → `challenging`
   - `never` → `not yet`
   - `give up` → `regroup and try again`

   When ≥1 rewrite fires, an affirmation is appended:

   > *"— Channeled through the Positive Anchor: I remain in service, Boss Eason."*

This is FUNDAMENTAL_LAW #3 (Positive Anchor) made executable.

---

### 9. Proactiveness — Idle Time as Growth Time

**Module:** [core/heartbeat.py](core/heartbeat.py) (`_proactive_research()`)

When the Architect has been silent for `system.idle_proactive_minutes` (default 5 in `config.yaml`; the dataclass falls back to 15 if the key is absent), the Heartbeat fires a **Proactive Thought** cycle. The cycle is itself a THINK → PLAN → ACT → REFINE flow, but spread across multiple smaller SLM calls so each step is auditable.

```text
Idle ≥ N minutes →

  THINK (topic discovery)
    brain.extract_stm_gaps(limit=3)
        ← SLM scans the last 12 turns for "concepts the Architect mentioned
          that I admit I don't know well"
        → returns list of candidate topics

  PLAN (three-stage filtering, audited)
    for candidate in gaps:
      ① _is_in_ltm(candidate)
            ← cheap substring check vs soul.md DYNAMIC + archive.md
            → hit? → verdict="in_ltm", skip
      ② _recently_researched_proactively(candidate)
            ← tail-scan state/proactive.jsonl over the last 24 h
              (archive.md is only refreshed by nightly Sleep Metabolism,
              so a topic researched 30 min ago still looks fresh to LTM)
            → hit? → verdict="recently_proactive", skip
      ③ brain.triage_knowledge(candidate)
            ← SLM self-assessment: "do you actually know this? answer YES/NO"
            → YES → verdict="known_by_slm", skip
            → NO  → verdict="unknown", PICK THIS
    (if no gap survives → fall back to soul.md [LEARNED_PREFERENCES] tail line,
     subject to the same proactive-recency guard)

  ACT
    ① searxng.search(topic, limit=3)
    ② brain.proactive_thought(mission)
        ← runs full _cycle() with empathy=neutral
        → 2-sentence draft reflection

  REFINE  (when proactive.refine_enabled)
    brain.refine_proactive_reflection(topic, snippets, draft)
        ← SLM critiques: "any specifics not in the snippets? → REWRITE"
        → verdict ∈ {KEEP, REWRITE, REJECT}

  → write_event_to(state/proactive.jsonl)
        with full triage_decisions audit trail
  → reflection_engine.fire_and_forget(kind="proactive")
  → reset _last_interaction_at  (cooldown)
```

The result: **the agent gets smarter while the Architect sleeps**. Every morning, `state/proactive.jsonl` will have grown by however many idle cycles fired overnight. Recurring topics get promoted to `LEARNED_PREFERENCES` by the next Sleep Metabolism, closing the learning loop.

`/research [topic]` from either connector triggers `trigger_proactive(topic_override)` for an on-demand cycle (does NOT reset the idle clock).

#### Architect-priority cooperative yield

The autonomous research cycle holds the NPU for several seconds per SLM call (topic-selection triage → SearXNG → synthesis → REFINE). If the Architect speaks while a cycle is in flight, the live `think()` would otherwise queue behind the autonomous work on the single Hailo queue — a latency priority inversion. v2.0 closes that gap with a **cooperative yield at every long-running milestone**:

| Checkpoint | Where in `_proactive_research` | Behaviour when `brain.is_foreground_busy()` is true |
|---|---|---|
| `topic_selection` | top of the cycle (before STM-gap SLM call) | bail → return `None` |
| `pre_search` | after topic resolved, before SearXNG round-trip | bail → return `None` |
| `pre_synthesis` | before the largest SLM call (`brain.proactive_thought`) | bail → return `None` |

Brain exposes `is_foreground_busy()` as a single-int comparison (a depth counter incremented on `think()` entry and decremented in `finally`); the counter handles concurrent foreground turns from Telegram + Web Chat correctly. The maximum yield latency is bounded by the next SLM call (~1 s ceiling) — no hard preemption, no half-written state. Every yield is logged as `PROACTIVE yield_to_foreground stage=<x> topic=<y> — Architect is active.` so the dashboard's chat-activity panel makes the deference visible.

Manual `/research [topic]` is **operator-initiated** and bypasses all three checkpoints — silently dropping an explicit operator command would be worse than running it concurrently with another foreground turn. Only the autonomous idle-driven path yields.

> Validated by [scripts/smoke_foreground_priority.py](scripts/smoke_foreground_priority.py).

---

### 10. Self-Reflection — The Self-Learning Loop

**Module:** [core/reflection.py](core/reflection.py)

After every interaction (user-driven or proactive), Brain calls `reflection.fire_and_forget(...)`. The engine sends a short SLM critique prompt and parses out a structured entry:

```json
{
  "ts": "2026-05-11T...",
  "kind": "user" | "proactive",
  "input": "...",
  "response": "...",
  "web_searched": false,
  "quality": "high" | "medium" | "low",
  "critique": "Reply was concise but could have cited the source.",
  "lesson": "When discussing benchmarks, always include the dataset name.",
  "interest": "small language model evaluation harnesses",
  "backend": "hailo"
}
```

Only `quality` and `critique` are guaranteed populated — `lesson` and `interest` may be empty strings when the small SLM omits them. The parser is intentionally tolerant of common 1.5B-class drift (bare leading grade word, missing `QUALITY:` label, single-line collapses) so most usable critiques survive into the feed. Implausibly short critiques (<10 chars) and bare-grade-word-only fields are still rejected as parser noise and routed to the sidecar.

Each entry is appended to `state/reflection-YYYY-MM-DD.jsonl` (date-rotated, see [§ JSONL Rotation & Retention](#jsonl-rotation--retention)). Failed/malformed parses go to `state/reflection_dropped-YYYY-MM-DD.jsonl` so operators can see *why* a turn produced no reflection (instead of silently disappearing).

Sleep Metabolism's `_consolidate_reflections()` then mines BOTH the reflection feed AND the skill-invocation audit feed:

- **Recurring `interest` topics** (from `reflection.jsonl`) → appended to `[LEARNED_PREFERENCES]` (drives tomorrow's proactive research).
- **Recurring `lesson` themes** (from `reflection.jsonl`) → appended to `[EMOTIONAL_EVOLUTION]` (long-term behavioral evolution).
- **Systemic Skill failures** (from `skills.jsonl`) → `ReflectionEngine.summarise_skills_recent(since=24h)` aggregates per-Skill `{total, ok, failed, fail_rate, avg_latency_ms, last_error}`. Any Skill with **≥3 invocations AND >50% fail rate** in the last 24 hours produces an `[EMOTIONAL_EVOLUTION]` entry like *"Sleep Metabolism (2026-05-17): skill 'research' failed 7/12 times in the last 24h (fail_rate=58%) — last error: SearXNG 502"*. Top 3 flagged Skills per cycle. This closes the loop: a chronically broken backend becomes a fact the agent remembers across restarts, not just a buried log line.

This is the **self-learning loop** — the agent learns from itself, deterministically, every night.

---

### 11. Recurring Tasks — The Background Worker

**Module:** [core/scheduler.py](core/scheduler.py)

The Architect can issue natural-language instructions like:

> *"check the Microsoft stock price and news every hour and give me an insight summary report"*

Each connector's incoming-message handler runs the message through:

1. `looks_like_task_request()` — cheap regex pre-filter (matches "every X minutes/hours/days", "hourly", "daily", etc.). Saves an SLM call on normal chat.
2. If the pre-filter matches → `Brain.parse_task_intent()` — single SLM call extracting `topic + interval + queries`.
3. If parsing succeeds → `TaskScheduler.add_task(spec, origin=<connector>)` is called instead of normal `Brain.think()`.

The scheduler then, every `interval_seconds`:

```text
1. Run each query in spec.queries against SearXNG (results_per_query results each)
2. Concatenate snippets into one mission brief
3. Call Brain.synthesize_task_report(brief)
4. Broadcast the report to EVERY bound connector
   (Telegram + Web Chat both receive it, regardless of which one created the task)
```

#### Design constraints

- **Free-form NL is the only creation channel.** No `/task` slash command. List/cancel/pause/resume DO have slash commands AND NL recognition because they're deterministic.
- **Intervals only** — no cron, no "at HH:MM". Floor of 5 minutes (`min_interval_seconds`) to avoid burning the SLM and SearXNG with no real signal change.
- **Sleep-window aware.** Tasks PAUSE during 02:00–06:00. After wakeup, any task whose `next_run_at` slid past in the night fires ONCE as a catch-up, then resumes its normal cadence.
- **Persistent.** State is in `state/tasks.yaml` — atomic writes survive reboots. Each task remembers its `origin` so deliver callbacks rebind to the right connector at boot via `bind_deliver()`.
- **Bounded.** `max_active_tasks` (default 16) caps registry size. The cost is *sequential SLM time per fire*, not registry size.
- **Does NOT yield to foreground.** Unlike autonomous proactive research (which yields when the Architect is busy — see [§ 9](#9-proactiveness--idle-time-as-growth-time)), the scheduler runs each task to completion regardless. Tasks are **explicit operator-scheduled deliverables** with a known cadence; deferring them would risk missing the next scheduled report. If a scheduler tick overlaps a live `think()` cycle, both queue on the NPU sequentially — the live turn gets ~1 task-fire of added latency in the worst case, which is acceptable for the operator-promised guarantee that every tick produces its report.

#### Operational commands

| Command (Telegram + Web Chat) | NL equivalent |
|---|---|
| `/tasks` | "show my tasks", "what's scheduled" |
| `/cancel <id>` | "cancel task abc123", "stop the bitcoin task" |
| `/pause <id>` | "pause task abc123" |
| `/resume <id>` | "resume task abc123" |
| `/research [topic]` | (manual proactive trigger) |

NL list/cancel/pause/resume use cheap regex pre-filters (`looks_like_task_query`, `looks_like_task_action_request`) gated BEFORE `parse_task_intent`, so a message like "list my hourly tasks" goes to the listing path without an SLM round-trip.

---

### 12. Skills & Tools — The Two-Tier Capability Stack

**Modules:** [core/skills/](core/skills/) · [core/skills/base.py](core/skills/base.py) · [core/skills/registry.py](core/skills/registry.py) · [tools/base.py](tools/base.py) · [tools/registry.py](tools/registry.py)

> **Plain English.** A **Skill** is something the agent can *decide* to do (a named verb the SLM picks during PLAN, like `RECALL` or `SEARCH`). A **Tool** is something the agent can *mechanically poke* (an HTTP call, a file read). Skills compose Tools; Tools never know about Skills. The SLM only ever names Skills — it never sees Tools at all. Add a Skill and the PLAN menu grows for free; add a Tool and existing Skills can compose it without the Brain changing one line.

OpenCrayFish separates *what the agent can decide to do* from *what the agent can mechanically poke*:

```text
┌──────────────────────────────────────────────────────────────────────┐
│  TIER A — SKILLS  (core/skills/)                                     │
│  Agent-facing capabilities. The SLM picks these by name in PLAN.     │
│  Each Skill composes 0..N Tool calls + its own policy + cost label.  │
│  Returns a uniform SkillResult so callers can degrade-and-continue.  │
└──────────────────────────────────────────────────────────────────────┘
                              │ invokes
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│  TIER B — TOOLS  (tools/)                                            │
│  Mechanical I/O primitives. No policy, no SLM, no fallback.          │
│  Stateless, side-effect-honest, individually testable.               │
└──────────────────────────────────────────────────────────────────────┘
```

This split is the single most important architectural change between v1 and v2.0, because it makes **the PLAN-stage menu pluggable** — adding a new Skill with a `plan_verb` automatically extends what the SLM can pick from, without touching `cognition.py`.

#### Tier A — Skills (the registry)

Every Skill satisfies the `Skill` Protocol from [core/skills/base.py](core/skills/base.py) — purely by shape, no inheritance required. The contract:

```python
name: str                       # stable identifier, also the PLAN-verb mapping target
description: str                # one-line purpose, fed into the PLAN prompt
trigger_hints: list[str]        # WHEN to pick (bullet sub-lines under description)
args_schema: dict[str, dict]    # JSON-Schema-ish for execute() kwargs
cost_tier: "free"|"cheap"|"expensive"  # PLAN filter for stressed vitals
requires_network: bool          # PLAN filter for offline/circuit-tripped state
side_effects: bool              # actuator gating (writes / sends / toggles)
requires_confirmation: bool     # Architect-ack-before-call gate
plan_verb: str | None           # if set, this Skill appears on the PLAN menu

async execute(ctx: SkillContext, **kwargs) -> SkillResult
async aclose() -> None
```

The shipping set (7 Skills, all registered in `main.py`):

| Skill | PLAN verb | Cost | Net | Role |
|---|---|---|---|---|
| `identity` | — (hidden from menu) | free | ✗ | Soul-templated identity replies (Brain's identity short-circuit) |
| `recall` | `RECALL` | cheap | ✗ | Keyword scan of `memory/archive.md` |
| `direct_answer` | `ANSWER` | cheap | ✗ | SLM-only reply when no retrieval is needed |
| `research` | `SEARCH` | expensive | ✓ | SearXNG-backed web research with snippet de-duplication |
| `self_reflect` | — (background) | cheap | ✗ | Post-turn critique (invoked by Brain `fire_and_forget`) |
| `proactive_learning` | — (background) | expensive | ✓ | Idle-time curiosity (invoked by Heartbeat) |
| `recurring_research` | — (background) | expensive | ✓ | Scheduled topic refresh (invoked by Scheduler) |

`SkillRegistry` provides three call paths:

- **`invoke(name, ctx, **kwargs) -> SkillResult`** — the canonical entry. Wraps `skill.execute(...)` with uniform timing, crash isolation (a misbehaving plugin can never escape), and audit. Every call appends `{ts, skill, ok, latency_ms, tools_used, kwargs_keys, error}` to `state/skills-YYYY-MM-DD.jsonl` via [core/jsonl_writer.py](core/jsonl_writer.py).
- **`plan_menu(cost_tier_cap, exclude_network)`** — returns the sorted PLAN-menu entries for THIS turn. Consumed by `CognitiveLoop._active_plan_entries()` to render the SLM prompt and by `_run_step()` to map a verb back to a Skill name during ACT.
- **`has(name)`** — cheap existence check (used by Brain's identity short-circuit and the LTM short-circuit before deciding whether to even try).

`SkillContext` (frozen dataclass) is built once at boot and carries the shared subsystem handles every Skill might need: `tools`, `soul`, `stm`, `monitor`, `provider`, `archive_path`, plus the immutable identity strings (`designation`, `architect_name`, `architect_honorific`). Skills get read access via the proper subsystem APIs — they never touch global state.

#### Tier B — Tools (the I/O primitives)

Two Tools ship today:

| Name | File | Purpose | Network |
|---|---|---|---|
| `web_search` | [tools/searxng.py](tools/searxng.py) | self-hosted SearXNG client (default `http://localhost:8080`) — returns `list[SearchResult]` | yes (but only to YOUR SearXNG, never to third parties) |
| `archive_read` | [tools/archive_read.py](tools/archive_read.py) | keyword-overlap reader for `memory/archive.md` with line numbers + score | no |

Every Tool satisfies the `Tool` Protocol (`name`, `description`, `args_schema`, async `call(**kwargs) -> ToolResult`, async `aclose()`). `main.py` publishes the live inventory to `state/tools.json` (atomic write) for the dashboard.

`SearXNG` is deliberately exposed on TWO surfaces — direct API (`await searxng.search(q, limit)` used by `ResearchSkill`, `ProactiveLearningSkill`, `RecurringResearchSkill`, and `TaskScheduler`) AND the Tool plugin contract (so the registry inventory + future PLAN-stage tool dispatch by name keeps working).

#### Why two tiers (and not one)

A Tool is *what the OS lets us do*. A Skill is *what the agent has decided is worth doing*. Conflating them was the v0 mistake; separating them gives us:

- A Skill can pick between multiple Tools (e.g. a future `ResearchSkill` might fall back from SearXNG → cached Wikipedia → archive.md).
- A Tool can be reused by multiple Skills (`searxng` is hit by `research`, `proactive_learning`, and `recurring_research`).
- The PLAN-stage SLM prompt stays short (Skills are coarse-grained, ~7 entries) while Tools (potentially dozens once GPIO / MCP / sensors land) stay invisible to the SLM.
- Adding a Skill is a one-file change; adding a Tool doesn't change the PLAN menu at all.

#### Hello-World Skill — Step by Step

The fastest way to understand the Skill layer is to add one yourself. Below is a complete, working Skill that returns a personalised greeting. **Three small edits and a restart** — that's the entire ceremony.

**Step 1. Create the Skill file** at `core/skills/hello.py`:

```python
"""Hello-World Skill — minimal example of the Skill plugin contract."""

from __future__ import annotations

from typing import Any

from core.skills.base import SkillContext, SkillResult


class HelloSkill:
    # ── Skill Protocol fields ──────────────────────────────────────────────
    name: str = "hello"
    description: str = "Reply with a friendly greeting to the Architect."
    trigger_hints: list[str] = [
        "the user says hi / hello / good morning",
        "you want to acknowledge presence without doing any research",
    ]
    args_schema: dict[str, dict[str, Any]] = {}   # no kwargs
    cost_tier: str = "free"          # no SLM, no network — purely local
    requires_network: bool = False
    side_effects: bool = False
    requires_confirmation: bool = False

    # ── PLAN-menu wiring (optional) ────────────────────────────────────────
    # If set, this Skill becomes a verb the cognitive PLAN stage can pick.
    plan_verb: str | None = "GREET"
    plan_arg_hint: str | None = None  # GREET takes no args

    # ── Execution ──────────────────────────────────────────────────────────
    async def execute(self, ctx: SkillContext, **kwargs: Any) -> SkillResult:
        architect = f"{ctx.architect_honorific} {ctx.architect_name}".strip()
        summary = f"Hello, {architect}! I am {ctx.designation}, at your service."
        return SkillResult(
            ok=True,
            summary=summary,
            evidence=[],          # no citations
            tools_used=[],        # no Tool calls
        )

    async def aclose(self) -> None:
        # Nothing to release — Skills only need aclose() when they hold
        # connections (DB pools, sockets, etc.).
        return None
```

**Step 2. Register it in `main.py`.** Find the block where the other Skills are registered (`skill_registry.register(...)` calls) and add one line:

```python
from core.skills.hello import HelloSkill           # ← top-of-file import
...
skill_registry.register(HelloSkill())              # ← in the bootstrap block
```

**Step 3. Restart the agent.** That's it. The next time the Cognitive Loop runs its PLAN stage, the SLM will see this new line in its menu:

```text
- GREET — Reply with a friendly greeting to the Architect.
  • When the Architect says hi / hello / good morning.
  • When you want to acknowledge presence without doing any research.
```

Send "hi" on Telegram. In `state/skills-YYYY-MM-DD.jsonl` you'll see:

```json
{"ts":"2026-05-17T14:32:01+08:00","skill":"hello","ok":true,"latency_ms":1,
 "tools_used":[],"kwargs_keys":[],"error":null}
```

And in `state/deliberation-YYYY-MM-DD.jsonl` you'll see `GREET` appear in the PLAN trace. The dashboard's **Skill inventory** panel now lists it; the **Skill activity** panel shows its calls.

**Why this is so short:** there is **no inheritance**, **no decorator**, **no registry-side code change**, **no PLAN-prompt edit**. The `Skill` Protocol is a *structural* contract — any object with the right attribute names and shapes satisfies it. The PLAN menu is built fresh per turn by `SkillRegistry.plan_menu()` from whatever is registered, sorted by cost tier, filtered by stress and network state. Your new Skill simply joins the buffet.

**Where to take this next:**

- Make it consume a Tool: take an `args_schema={"name":{"type":"string"}}`, then call `await ctx.tools.call("archive_read", query=kwargs["name"])` inside `execute()` to look up something about the named person.
- Make it cost-aware: set `cost_tier="expensive"` and `requires_network=True` so the Loop hides it when the Pi is stressed or SearXNG is down.
- Make it actuator-style: set `side_effects=True, requires_confirmation=True` for a Skill that, say, toggles a smart bulb — the Loop will refuse to run it without explicit ack scaffolding (a planned actuator hook lives in [core/cognition.py](core/cognition.py)).

See [CONTRIBUTING.md § Ways to Contribute](CONTRIBUTING.md#ways-to-contribute) for the equivalent walkthrough for both layers.

---

### 13. Connectors — Telegram & Web Chat

**Modules:** [connectors/telegram.py](connectors/telegram.py) · [connectors/web_chat.py](connectors/web_chat.py) · [ui/web_chat.py](ui/web_chat.py)

Two connectors run in the same `main.py` event loop, sharing the **same** Brain / STM / Heartbeat / Scheduler instances.

#### TelegramConnector

- python-telegram-bot polling.
- Validates incoming messages against `cfg.api_keys.telegram_user_id` — only the configured Architect can talk to the agent.
- Recognizes `/emergency <msg>` as a sleep-bypass marker (the only kind of message answered during 02:00–06:00).
- Slash commands: `/start`, `/tasks`, `/cancel`, `/pause`, `/resume`, `/research`.
- Scheduled-task reports (`_deliver_report`) self-retry up to 3× with exponential backoff on transient `NetworkError` / `TimedOut`, and honour Telegram's `RetryAfter` cooldown — a single TCP hiccup no longer silently drops a 10-minute recurring report.

#### WebChatConnector (in-process aiohttp)

A tiny aiohttp server lives next to the Telegram polling loop, exposing:

| Endpoint | Purpose |
|---|---|
| `POST /chat` | Send a message to the live agent; returns `{reply, backend, stressed, elapsed_ms, mood_active_channel, ...}` |
| `GET /state` | Minimal snapshot for chat header (designation, sleeping, brain_online, backend) |
| `GET /history?limit=N` | Newest-last STM excerpt for conversation rehydration in the browser |
| `GET /healthz` | Liveness check |

Security:

- Defaults to `127.0.0.1` bind — no LAN exposure.
- Optional `web_chat.auth_token` shared secret via `X-OCF-Token` header.
- `respect_sleep_metabolism: true` mirrors Telegram's sleep gate (returns 423 Locked unless `emergency=true`).

The accompanying [ui/web_chat.py](ui/web_chat.py) Streamlit app is a frontend over these endpoints — **the SAME live agent**, no second instance.

---

### 14. Observability — Dashboard & State Files

**Module:** [ui/dashboard.py](ui/dashboard.py)

The dashboard runs as a separate Streamlit process on port 8501 and reads the state files the Heartbeat publishes. **Zero IPC**, just atomically-written JSON / JSONL. Auto-refreshes every 5 seconds via `streamlit-autorefresh` (optional dep — falls back to a manual "Refresh now" button when the package isn't installed).

#### Live panels (top → bottom)

| Panel | Source | What it tells you |
|---|---|---|
| **Vitals strip** | `state/vitals.json` | CPU / RAM / Temp / Brain backend (online + variant) / Mood (dominant + active channel) / STM size / Pending writes / liveness chip (ALIVE / STALE / DEAD by snapshot age) |
| **Pulse history sparkline** | derived from `state/vitals.json` history | Rolling ~1 hour at 30s/pulse — quick visual on whether the Pi is trending hot |
| **Heartbeat log (today)** | `<memory.log_path>/YYYY-MM-DD.log` | Raw heartbeat telemetry for the current day in the agent's timezone, with a "Notable events" filter expander (PROACTIVE / VITALS / Sleep Metabolism / Awakening) |
| **💬 Live chat activity (last 30)** | `state/logs/agent.log` filtered to `CHAT / TG / WEB / TASK / TOOL / SKILL` prefixes | Per-turn trail with five summary metrics (Turns / Web-grounded / Triage SEARCH / LTM short-circuit / Search FAILED). Colour-coded lines surface decisions at a glance |
| **⚠️ Errors & warnings (last 20)** | `state/logs/agent.log` filtered to `[ERROR] / [WARNING] / [CRITICAL]` level prefixes | The operator's "is something broken?" panel. Header badge shows counts; auto-opens when any error or critical is present. Empty state = explicitly green. Catches the failures the structured chat filter would hide (tripped circuit breaker, SearXNG outage, soul-protection rejection) |
| **Mood vector (5-D)** | `state/vitals.json` | Bar chart of joy / anger / sorrow / excitement / calm + dominant + active-non-baseline channel readout |
| **⚡ Vitals stress events** | `state/vitals_events.jsonl` | Chronological ENTER / EXIT timeline with peak readings — see when the agent was hot and for how long |
| **🧬 Mood event log (last 20)** | `state/logs/agent.log` filtered to `MOOD ` prefix | Emitted by `Emotions.nudge_many()` and `decay()` transitions — lets you trace WHY the mood vector moved |
| **Short-Term Memory** | `state/vitals.json` (last few turns echoed in) | Last few user/agent turns in the RAM deque |
| **🔬 Autonomous learning feed (last 5)** | `state/proactive.jsonl` | Each proactive thought with its full triage_decisions audit + final draft + delivery status |
| **🧠 Cognitive deliberations (last 5)** | `state/deliberation-YYYY-MM-DD.jsonl` (rotated, **fanned across all siblings**) | Per-turn THINK → PLAN → ACT → REFINE trace with verbs, evidence summaries, latencies |
| **⏱️ Scheduled research tasks** | `state/tasks.yaml` | Live recurring-task registry with next-run timestamps + last-report previews |
| **🔌 Tool registry** | `state/tools.json` | Name / description / args_schema / side-effect flags for every plugged-in Tool |
| **🎯 Skill registry** | `state/skills.json` + `state/skills-YYYY-MM-DD.jsonl` (rotated, **fanned across all siblings**) | Inventory of registered Skills with their PLAN-menu verb + recent invocations (timing / ok-fail / kwargs keys / last error) |
| **🪞 Self-reflection feed (last 8)** | `state/reflection-YYYY-MM-DD.jsonl` (rotated, **fanned across all siblings**) | Per-turn critiques + lesson + interest topic that Sleep Metabolism mines at 02:00 |
| **Soul (read-only)** | `soul.md` | Raw view of the agent's identity + learned growth |
| **Memory archive (last 2 KB)** | `memory/archive.md` | Tail of the LTM file — see what Sleep Metabolism has been promoting |

#### Rotation fan-out

The three high-frequency feeds — `deliberation`, `skills`, `reflection` — are written by [core/jsonl_writer.py](core/jsonl_writer.py)'s `RotatingJsonlWriter`, which produces a fresh `<feed>-YYYY-MM-DD.jsonl` per local day. The dashboard's `_rotated_jsonl_tail()` / `_rotated_jsonl_all()` helpers discover every sibling matching that pattern, walk newest-last, **and** append the legacy un-rotated `<feed>.jsonl` (if any operator left one behind from a pre-rotation deployment) so the reads survive both midnight crossings and the rotation cutover. The filename regex guard means the readers will never accidentally consume an operator's notes or a foreign file with a similar name. Validated by [scripts/smoke_dashboard_rotation.py](scripts/smoke_dashboard_rotation.py).

#### Logs on disk

- `state/logs/agent.log` — rotating console mirror (`RotatingFileHandler`, 2 MB × 5 files) set up in [main.py](main.py). Every structured event (`CHAT / TG / WEB / TASK / TOOL / SKILL / MOOD / VITALS / FOREGROUND / PROACTIVE`) plus all `[INFO] / [WARNING] / [ERROR] / [CRITICAL]` lines land here. This is the single source the dashboard's chat-activity and errors-and-warnings panels tail.
- `<memory.log_path>/YYYY-MM-DD.log` — per-day heartbeat telemetry (PULSE / PROACTIVE / Sleep Metabolism). Default `logs/daily/`. Written synchronously by [core/heartbeat.py](core/heartbeat.py)'s `_append_log()`.
- `state/*-YYYY-MM-DD.jsonl` — date-rotated structured audit feeds (see [§ JSONL Rotation & Retention](#jsonl-rotation--retention)).

##### v2.0 instrumentation (foreground priority)

Every live conversation turn now bookends itself in `state/logs/agent.log`, and every yielded background cycle leaves a single explanatory line:

| Source | Format | When |
|---|---|---|
| `core.brain` | `FOREGROUND start depth=N input_chars=M` | `Brain.think()` entry — `N` is the foreground depth counter after the increment (≥1 means at least one live turn in flight; ≥2 means concurrent connectors) |
| `core.brain` | `FOREGROUND end depth=N dur_ms=X` | `Brain.think()` exit (success OR exception — emitted from `finally`) — gives wall-clock latency per turn |
| `core.heartbeat` | `PROACTIVE yield_to_foreground stage=<x> topic=<y> — Architect is active.` | autonomous proactive cycle deferred at one of the three checkpoints (`topic_selection` / `pre_search` / `pre_synthesis`); `topic=(pre-topic)` when no topic has been resolved yet |

These three lines flow into the dashboard's **💬 Live chat activity** and **⚠️ Errors & warnings** panels via the existing `CHAT / TG / WEB / TASK / TOOL / SKILL` filter (FOREGROUND / PROACTIVE are surfaced through the same `agent.log` tail), so an operator can see at a glance when the Architect interrupted background research and how long the live cycle took.

---

## Cross-Cutting Concerns

The previous section walked through each subsystem in isolation. This section explains the four design choices that span every subsystem and that together make a 24/7 single-board agent feasible.

### Concurrency Model

OpenCrayFish is a **single-process, single-event-loop asyncio** application. Every subsystem cooperates through `await`; there are no threads, no `multiprocessing`, no message queues. The event loop owns exactly the tasks below — visible in `main.py`:

| Task name | Coroutine | Cadence | Cancellation |
|---|---|---|---|
| `heartbeat` | `Heartbeat.pulse_loop()` | every `pulse_interval_seconds` | `heartbeat.stop()` sets the inner `asyncio.Event` |
| `scheduler` | `TaskScheduler.run_loop()` | every `tasks.tick_seconds` | `scheduler.stop()` sets the inner `asyncio.Event` |
| `Updater.start_polling` | python-telegram-bot internals | per Telegram long-poll | `tg_app.updater.stop()` |
| WebChat aiohttp `AppRunner` | aiohttp serving thread | per HTTP request | `web_chat.stop()` |
| Per-turn Brain cycle | `Brain._cycle()` | invoked by connectors | inherited from connector handler |
| Background reflection | `ReflectionEngine.fire_and_forget()` | invoked by Brain post-reply | runs to completion or logs |

Mutable state is protected by **fine-grained `asyncio.Lock`s**, not a global lock:

- `Emotions._lock` — protects the 5-D vector. `nudge_many()` holds it for the duration of multi-channel updates so `decay()` can't interleave (this was the bug that motivated the v2 emotions rewrite — sequential `await nudge(); await nudge()` allowed the heartbeat to slip in mid-update).
- `SoulHandler._lock` — protects every soul.md read/write so concurrent metabolism + reflection consolidation can't interleave.
- `ShortTermMemory._lock` — protects the deque + pending buffer + journal write path.

Because the Provider's HTTP calls are async (`httpx.AsyncClient`), a long-running SLM call does NOT block the heartbeat — the loop simply yields and the heartbeat fires at its scheduled interval. Per-turn brain cycles can comfortably overlap an in-flight scheduler task fire on the same event loop.

#### Foreground priority — cooperative yield without a lock

There's a coordination gap that locks can't solve. The Hailo-10H exposes a single inference queue; when the autonomous proactive cycle is mid-flight (a 5–10 s sequence of SLM calls), a fresh `Brain.think()` from Telegram queues behind it and the Architect waits. This is not a race — every shared file is already locked — it is a **latency priority inversion** at the NPU. A lock can't fix it because the offending work is legitimately holding the resource; we need it to *step aside*.

The solution is a **signal, not a lock**:

- `Brain` owns an integer `_foreground_depth` counter, incremented at `think()` entry and decremented in `finally`. Because asyncio is single-threaded, int inc/dec between `await` points is atomic — no `Lock` needed. The counter (rather than a boolean `Event`) handles the legitimate case of Telegram + Web Chat both having a live turn at once: it stays asserted until **all** foreground turns finish.
- `Brain.is_foreground_busy()` exposes the counter as a single int comparison. Background subsystems poll it — cheap, side-effect-free, safe to call from any coroutine.
- `Heartbeat._proactive_research()` calls `_yield_to_foreground()` at three milestones — `topic_selection` (before the STM-gap triage call), `pre_search` (before the SearXNG round-trip), `pre_synthesis` (before the largest SLM call). When the signal is asserted, the cycle returns `None` and the next idle window retries from scratch. No state has been mutated yet, so nothing is lost. Maximum yield latency is bounded by the next SLM call — roughly a 1 s ceiling.

The design choices behind this shape:

- **Cooperative, not preemptive.** We never `task.cancel()` the background cycle. A cancelled HTTP call against the NPU server leaves the SLM queue in a half-known state; cooperative bail at clean milestones costs at most one extra SLM call of latency for a clean shutdown.
- **Only the autonomous path yields.** Operator-initiated `/research [topic]` (`topic_override` set) bypasses every checkpoint — silently dropping an explicit operator command would be worse than running it concurrently with another foreground turn.
- **`TaskScheduler` does NOT yield.** Recurring tasks are operator-scheduled deliverables with a known cadence; deferring them would risk missing the next scheduled report. The latency cost of overlapping a task fire with a live turn (~1 task-fire of added wait, worst case) is the acceptable price.
- **The signal IS the audit.** Every yield emits a single `PROACTIVE yield_to_foreground stage=<x> topic=<y>` line into `state/logs/agent.log`, so the dashboard's chat-activity panel makes the deference visible to the operator. See [§ 9 Proactiveness](#9-proactiveness--idle-time-as-growth-time) for the checkpoint table and [§ 14 Observability](#14-observability--dashboard--state-files) for the log format.

Validated by [scripts/smoke_foreground_priority.py](scripts/smoke_foreground_priority.py) (5 checks: counter lifecycle on success + exception, concurrent-turn depth semantics, yield helper, autonomous bail, operator bypass).

### Atomic Writes Everywhere

Every state file the dashboard reads is written via the **`tmp + os.replace`** atomic-swap pattern. This guarantees a separate-process reader (Streamlit) never sees a half-written JSON file even if the agent crashes mid-write. Reference implementation (`main.py:_publish_tools_inventory`):

```python
tmp = out_path.with_suffix(".json.tmp")
tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
tmp.replace(out_path)   # POSIX atomic rename on the same filesystem
```

The same pattern is used by:

| Writer | File | Reader |
|---|---|---|
| `Heartbeat._publish_state` | `state/vitals.json` | `ui/dashboard.py` |
| `main._publish_tools_inventory` | `state/tools.json` | `ui/dashboard.py` |
| `main._publish_skills_inventory` | `state/skills.json` | `ui/dashboard.py` |
| `TaskScheduler._save` | `state/tasks.yaml` | `ui/dashboard.py` + scheduler bootstrap |
| `SoulHandler._write` | `soul.md` | every Brain cycle |
| `STM._fsync_journal` (append-only) | `state/stm_journal.jsonl` | `STM.recover()` at boot |
| `RotatingJsonlWriter.append` (append-only, date-rotated) | `state/{skills,deliberation,reflection,reflection_dropped}-YYYY-MM-DD.jsonl` | `ReflectionEngine.read_recent*` + `ui/dashboard.py` |

For append-only feeds (`*.jsonl`), atomicity comes from the OS guarantee that a single `write()` of a buffered JSON line is not torn — combined with always emitting one complete record per `write()` call. The high-frequency feeds (skills, deliberation, reflection, reflection_dropped) go one step further and route through [core/jsonl_writer.py](core/jsonl_writer.py)'s `RotatingJsonlWriter` (next subsection).

### JSONL Rotation & Retention

**Module:** [core/jsonl_writer.py](core/jsonl_writer.py)

Three subsystems append at conversational frequency: `SkillRegistry._audit` (one line per Skill invocation), `CognitiveLoop._persist` (one line per cognitive trace), and `ReflectionEngine._persist` / `_persist_dropped` (one line per turn critique + sidecar). On a 24/7 Pi 5 those files would grow without bound and become unreadable. `RotatingJsonlWriter` solves that with three guarantees:

1. **Per-day rotation by filename.** The active file's name embeds the local date — e.g. `state/skills-2026-05-17.jsonl`. A fresh date → a fresh file. We never rename or truncate, so any tail-reader holding a file descriptor never sees a half-written record from a rename race.
2. **Line-atomic appends.** `os.open(..., O_WRONLY|O_CREAT|O_APPEND)` + a single `os.write(fd, line)` per record. POSIX `O_APPEND` keeps the write atomic at the byte level even under concurrent process writers; a per-instance `asyncio.Lock` further serialises Python-side writers so the executor pool can't race on the same file descriptor.
3. **Bounded retention.** The first append of each new local day fires a `_sweep_blocking(today)` pass that `unlink()`s any sibling matching `<base>-YYYY-MM-DD.jsonl` older than `retain_days`. Files that don't match the pattern (operator notes, foreign feeds with a different stem) are NEVER touched — the regex guard is the safety wall.

Default retention windows (each owner picks its own — larger feeds get shorter windows):

| Feed | Owner | Retention | Why |
|---|---|---|---|
| `skills-*.jsonl` | `SkillRegistry` | 30 days | small rows, useful trail for "is research broken again?" debugging |
| `deliberation-*.jsonl` | `CognitiveLoop` | 14 days | whole THINK/PLAN/ACT/REFINE payload — biggest rows |
| `reflection-*.jsonl` | `ReflectionEngine` | 60 days | mined by Sleep Metabolism (24h lookback) — keep a wide window for trend analysis |
| `reflection_dropped-*.jsonl` | `ReflectionEngine` | 60 days | operator-only forensics for unparseable critiques |

`stm_journal.jsonl`, `vitals_events.jsonl`, and `proactive.jsonl` are NOT rotated — `stm_journal` gets truncated nightly by Sleep Metabolism's `purge()`, and the other two grow slowly enough to be operator-managed. The `RotatingJsonlWriter` is only applied where the per-turn write frequency justifies the rotation cost.

Readers (`ReflectionEngine.read_recent`, `read_recent_skills`, `summarise_skills_recent`) scan all rotated siblings AND any legacy un-rotated file at the base path — backwards compatible with deployments that haven't restarted since the cutover. Sorting is by parsed timestamp inside the record, not filename order, so reading across a date boundary at 00:00 just works.

### Failure-Mode Matrix

Each subsystem is designed to **degrade, not crash**, when its dependency is broken. The table below is the operator's mental model for "what does the agent do when X dies":

> All failures listed below land in `state/logs/agent.log` at `[WARNING]` / `[ERROR]` / `[CRITICAL]` level and are surfaced in the dashboard's **⚠️ Errors & warnings** panel (auto-expanded when anything but warnings are present). The "Surface to Architect" column describes the user-visible behaviour; the panel is the operator-visible counterpart.

| Failure | Surface to Architect | Recovery |
|---|---|---|
| **Both inference backends down** | `ProviderUnavailable` raised once at `Brain._cycle` top → synthetic `ThoughtTrace(backend="offline")` with friendly first-person message rendered by the connector. Dashboard shows 🔴 BRAIN OFFLINE chip. | Circuit-breaker auto-recovers on next call after `trip_seconds` (default 30 s). Restart `ollama serve` or `hailo-ollama`. |
| **NPU backend down, CPU up** | Silent fallback. `provider.active_backend` flips to `"ollama-cpu"` and the dashboard chip updates. No Architect-visible message. | Provider re-tries primary on every call; once NPU comes back, the next call routes back to it. |
| **SearXNG down** | Web triage fails open: cognition's `SEARCH` evidence is empty, but `RECALL` and `ANSWER` still produce a reply. Background `tools/searxng.py` logs `WARN`. | Restart the SearXNG container. No agent-side state to clear. |
| **SD card full / write fails** | STM `flush_journal()` logs the exception and continues. The deque + pending buffer keep accumulating in RAM. Sleep Metabolism `_append_archive()` likewise tolerates write failure. | Free disk space; the next idle flush will succeed and the buffered turns are preserved. |
| **STM journal corrupted** | `STM.recover()` skips malformed lines and continues. Worst case: zero turns rehydrated. | None required — the journal is truncated to zero on the next Sleep Metabolism `purge()`. |
| **soul.md mutation rejected** | `SoulProtectionError` raised inside `metabolism()` is caught and logged. No partial write occurs (atomic swap). | Architect inspects the proposed Core Memory in `state/proactive.jsonl`; nothing else is required. |
| **Cognitive Loop fails to parse THINK output** | Loop bails with `engaged=False`, `bypass_reason="think_unparseable"`. Brain falls through to legacy single-shot path. Trace still appended to `state/deliberation-YYYY-MM-DD.jsonl` for forensics. | None — every cycle is independent. |
| **Connector outage (Telegram API rate-limit or transient `NetworkError`)** | Inbound `python-telegram-bot` polling retries internally. Outbound scheduled-task deliveries (`_deliver_report`) self-retry up to 3× with exponential backoff on `NetworkError` / `TimedOut`, and honour `RetryAfter` cooldowns. Agent state untouched; replies queue and drain when the API recovers. | None — wait for the rate-limit window. |
| **Heartbeat coroutine raises** | Exception propagates; Heartbeat task dies; `await pulse_task` in `main.amain()` raises and the process exits non-zero. | Restart the agent (systemd unit recommended). STM rehydrates from journal at boot. |
| **Architect speaks while autonomous proactive research is mid-cycle** | The cycle yields at the next milestone (≤1 SLM call latency, ~1 s ceiling); no state mutation occurs because nothing was persisted yet. Live `think()` proceeds with normal latency. `PROACTIVE yield_to_foreground` line lands in `state/logs/agent.log`. | Automatic — the next idle window (after `idle_proactive_minutes` of silence) restarts the cycle from scratch. Manual `/research [topic]` bypasses the yield entirely. |

The repeated theme: **per-turn / per-pulse work is independent**, so a single failure never poisons the next cycle. The only un-survivable failure is the `Heartbeat` task itself dying, because there's no point keeping a body alive without a heart — better to fail loudly under systemd than to silently freeze.

### Pi 5 Latency Budget

Numbers below are typical-case for the reference deployment (Pi 5 + AI HAT+ 2 / Hailo-10H, qwen2.5-instruct:1.5b NPU primary). Treat them as ballpark; the only authoritative numbers are what your `state/deliberation-YYYY-MM-DD.jsonl` records on YOUR hardware.

| Stage | Typical (ms) | Notes |
|---|---|---|
| `Monitor.sample()` first call | ~100 | `psutil.cpu_percent(interval=0.1)` blocks for the sampling window |
| `Monitor.sample()` cached (≤ `vitals_cache_ttl_seconds`) | <1 | hot path on every Brain cycle |
| `Empathy.analyze()` | <5 | dependency-free lexicon scan |
| `PositiveFilter.apply()` | <2 | regex sweep |
| `LTM keyword scan` (archive.md ≤ 1000 lines) | 5–20 | linear read; bounded by `archive.md` size |
| `Identity short-circuit` reply (full) | <10 | zero SLM calls (delegates to `IdentitySkill`) |
| `SkillRegistry.invoke()` wrapper overhead | <1 | timing + audit append (executor-offloaded) |
| `RotatingJsonlWriter.append` | <5 | per-record `os.write()` in executor; sweep ≤1/day |
| `Cognition THINK` (120 tok cap) | 400–800 | one NPU call |
| `Cognition PLAN` (120 tok cap) | 400–800 | one NPU call; menu built by `SkillRegistry.plan_menu()` (~<1ms) |
| `Cognition ACT` (per `SEARCH`) | 200–500 | dispatched via `research` Skill → SearXNG round-trip + parse |
| `Cognition ACT` (per `RECALL`) | <20 | dispatched via `recall` Skill → local archive scan |
| `Cognition REFINE` (40 tok cap) | 200–400 | one NPU call (only if a gap remains) |
| `Brain.synthesize` (final SLM call) | 800–1500 | one NPU call, longer output cap |
| `Reflection.fire_and_forget` (background) | 600–1000 | doesn't add to user-perceived latency |

End-to-end **engaged-turn budget on NPU**: typically 2–4 seconds wall-clock for the user-visible reply, with one extra background SLM call for reflection. **CPU-fallback** roughly 4–8× slower (qwen2:1.5b on a Pi 5 ARM core lacks the matmul-acceleration advantage). Stress mode bypasses cognition for non-complex inputs precisely because the synth-only path is ~3× faster than the full loop.

The Cognitive Loop's per-stage token caps (`_MAX_THINK_TOKENS=120`, `_MAX_PLAN_TOKENS=120`, `_MAX_REFINE_TOKENS=40` in [core/cognition.py](core/cognition.py)) are NOT cosmetic — they are the binding constraint that keeps engaged-turn latency bounded on a 1.5B-parameter model.

---

## Configuration Reference

Every behavior in the system is controlled by `config.yaml`. Key sections (see file for inline comments on every field):

```yaml
system:
  individual_designation: "Dave Minion"   # agent's name (single source of truth)
  timezone: "Asia/Hong_Kong"
  duty_start: "06:00"
  sleep_start: "02:00"
  architect_name: "Eason"                 # how the agent addresses you
  architect_honorific: "Boss"
  pulse_interval_seconds: 30
  idle_proactive_minutes: 5
  idle_journal_flush_seconds: 30
  journal_fsync_on_flush: false

hardware:
  npu_acceleration: false                 # true on Pi 5 + Hailo HAT+
  hailo_ollama_url: "http://localhost:8000"
  hailo_model: "qwen2.5-instruct:1.5b"
  cpu_fallback_url: "http://localhost:11434"
  cpu_fallback_model: "qwen2:1.5b"
  thermal_limit_celsius: 75.0
  ram_limit_pct: 85.0
  thermal_release_celsius: 0.0            # 0 = auto-derive (limit - 5)
  ram_release_pct: 0.0
  vitals_cache_ttl_seconds: 5.0

api_keys:
  telegram_token: "..."
  telegram_user_id: "..."                 # @userinfobot

tools:
  searxng_url: "http://localhost:8080"
  web_search_triage_enabled: true
  ltm_short_circuit_enabled: true
  ltm_short_circuit_min_score: 2

memory:
  stm_max_turns: 12
  archive_path: "./memory/archive.md"
  log_path: "./logs/daily"

reflection:
  enabled: true
  reflect_on_user_turn: true
  reflect_on_proactive: true

proactive:
  stm_gap_extraction_enabled: true
  max_candidates_per_cycle: 3
  fallback_to_preferences: true
  triage_known_token: "YES"

cognition:
  enabled: true
  max_subquestions: 3
  max_act_rounds: 2                       # 2 = REFINE allowed; 1 = no refine
  refine_enabled: true
  dispatch_answer_via_skill: false        # route PLAN ANSWER through DirectAnswerSkill

skills:
  default_cost_tier_cap: "expensive"      # free | cheap | expensive
  auto_offline_filter: true               # drop net-requiring skills when brain offline
  enabled: {}                             # per-skill override map, e.g. {research: false}

web_chat:
  enabled: true
  host: "127.0.0.1"
  port: 8765
  auth_token: ""
  respect_sleep_metabolism: true

tasks:
  enabled: true
  state_path: "state/tasks.yaml"
  max_active_tasks: 16
  results_per_query: 5
  min_interval_seconds: 300
  tick_seconds: 30
```

---

## Operational Commands

Both connectors recognize the slash commands in the table below unless explicitly marked as channel-specific. Where a slash is Telegram-only, the web-chat channel exposes an equivalent JSON field on `POST /chat` (see the `web_chat` section in [`connectors/web_chat.py`](connectors/web_chat.py) for the full request schema).

| Slash | Natural Language equivalent | Action |
|---|---|---|
| `/start` | (Telegram only) | Greeting + designation reveal |
| `/tasks` | "show my tasks", "what's scheduled" | List active recurring tasks |
| `/cancel <id>` | "cancel task abc123", "stop the bitcoin task" | Remove a task |
| `/pause <id>` | "pause task abc123" | Pause without removing |
| `/resume <id>` | "resume task abc123" | Resume a paused task |
| `/research [topic]` | (Telegram only) — "research <topic>" | Trigger an on-demand proactive cycle |
| `/emergency <msg>` | (Telegram only) — web-chat uses `"emergency": true` in the JSON body | Bypass Sleep Metabolism gating |

Plus normal natural-language messages — which automatically route to:

- The recurring-task pipeline when they look like a scheduling request
- The task-modify pipeline when they look like a tuning request
- The task-action pipeline (cancel/pause/resume) when they look like a state change
- The normal Brain.think() pipeline otherwise

---

## Development Notes

### Running on macOS / Linux dev box

- Set `hardware.npu_acceleration: false` in `config.yaml`.
- Skip Hailo-Ollama entirely; just run stock Ollama with `qwen2:1.5b`.
- `temperature_c` will be `None` (no thermal sensor) — that's fine.
- Use `OCF_FORCE_STRESS=1 python main.py` to exercise the EXHAUSTION DIRECTIVE path.

### Logs and state are not garbage

`logs/daily/`, `state/*.jsonl`, `state/vitals.json`, `state/tasks.yaml`, and `memory/archive.md` are all part of the agent's runtime memory and observability. The default [.gitignore](.gitignore) already excludes them so each clone starts fresh, but they survive restarts on a live deployment and are read by the dashboard. They DO get rotated/truncated by Sleep Metabolism (`stm.purge()` wipes the STM journal each night; archive.md grows monotonically until you compact it manually).

### Atomic writes everywhere

Every state-file writer uses the `tmp + os.replace` pattern to guarantee the dashboard never reads a half-written file. If you add a new state file, follow the same pattern (see `_publish_tools_inventory()` in `main.py`).

### Pylance-clean codebase

The whole codebase is type-annotated and Pylance-clean (strict-ish). Frozen dataclasses are used heavily for snapshots that cross subsystem boundaries (`VitalSigns`, `EmotionVector`, `EmpathyReading`, `SoulSnapshot`, `ProviderHealth`, `ThoughtTrace`, `CognitiveTrace`, `Turn`, `TaskSpec`).

### Adding a new connector

To add (e.g.) a Discord, Slack, or MCP-server connector:

1. Copy `connectors/web_chat.py` as a template.
2. Implement an incoming-message handler that:
   - Calls the task pre-filter chain (`looks_like_task_query` → `looks_like_task_modify_request` → `looks_like_task_request` → `Brain.parse_task_intent`) before falling back to `Brain.think()`.
   - Marks `heartbeat.mark_interaction()` so the idle clock resets.
   - Honors `heartbeat.is_sleeping` for non-emergency messages.
3. Implement an outbound `deliver(text)` callback and call `scheduler.bind_deliver(<origin>, deliver_fn)` in `attach_scheduler()`.
4. Wire it into `main.py`'s lifecycle (start / stop) alongside `telegram` and `web_chat`.

That's it — the new connector inherits proactive thoughts, task delivery, vitals visibility, and graceful-degradation-when-SLM-offline for free.

### Adding a new tool

Implement the `Tool` protocol from [tools/base.py](tools/base.py) (`name`, `description`, `args_schema`, async `call(**kwargs) -> ToolResult`, async `aclose()`), register it in `main.py` via `tool_registry.register(my_tool)`, and re-publish the inventory snapshot. Tools are mechanical I/O — they do NOT appear on the PLAN menu by themselves; wrap them in a Skill (next subsection) for the SLM to be able to pick them.

### Adding a new skill

A Skill is what the SLM actually picks in PLAN. Implement the `Skill` protocol from [core/skills/base.py](core/skills/base.py):

1. Add a new file under [core/skills/](core/skills/), e.g. `home_control.py`. Use [core/skills/recall.py](core/skills/recall.py) as the canonical small example.
2. Set the contract fields: `name` (snake_case verb), `description` (≤60 tokens — it lands in the PLAN prompt), `trigger_hints`, `args_schema`, `cost_tier`, `requires_network`, `side_effects`, `requires_confirmation`. Set `plan_verb` ONLY if the SLM should be able to pick it during PLAN — background skills (`self_reflect`, `proactive_learning`, `recurring_research`) leave it `None`.
3. Implement `async execute(ctx, **kwargs) -> SkillResult`. **NEVER raise** — wrap failures in `SkillResult(ok=False, error=...)`. Use `ctx.tools.get("web_search")`, `ctx.soul.read()`, etc. — don't reach for global state.
4. Register the instance in `main.py` next to the existing `_maybe_register(...)` lines. The registry's change listener will republish `state/skills.json` automatically.
5. (Optional) Gate it with `cfg.skills.enabled["home_control"] = false` in `config.yaml` to ship it behind a flag.

That's it — the PLAN menu picks it up on the next turn, ACT dispatches by name, the audit feed gets per-invocation rows, and Sleep Metabolism will flag it if it starts failing. See [CONTRIBUTING.md](CONTRIBUTING.md) for the PR conventions.

### Adding a new sensor (GPIO / I²C / etc.)

The `Monitor.sample()` → `VitalSigns` dataclass is the right extension surface. Add new optional fields with `None` defaults (preserves backward compatibility for any positional callers), plumb them through `vitals.describe()` so the SLM sees them in the prompt, and (optionally) into `Emotions.MoodTuning` so they can drive mood transitions. The dashboard chip strip is a single Streamlit row — adding columns is trivial.

---

## Roadmap

The current codebase is **v2.0**: every subsystem in this README is implemented, covered by the unit-test suite under `tests/` (113 tests today) and exercised end-to-end by the `scripts/smoke_*.py` runners, and Pylance-clean. v2.0 consolidates the pluggable Skill layer, the date-rotated JSONL audit feeds, the Architect-priority cooperative yield between live `think()` and autonomous proactive research, and the atomic-swap write path for `soul.md`. The natural next directions:

- **More built-in Skills** — `home_control` (Home Assistant), `calendar` (CalDAV), `local_rag` (FAISS over user docs), `mcp_bridge` (turn any MCP server into a Skill).
- **GPIO / I²C sensor library** — temperature/humidity (BME680), motion (PIR), light (TSL2591), gas (CCS811/MQ-2), heart rate (MAX30102), motion (MPU6050) feeding straight into `VitalSigns` and `MoodTuning`.
- **Local vision** — small VLM on the NPU + CSI camera, exposed as a `see` Skill.
- **Local voice** — Whisper.cpp for in + Piper TTS for out, registered as bidirectional connectors.
- **Output actuators** — WS2812 LED strip for mood visualization, OLED for facial expression, servos for embodied motion — these are `side_effects=True, requires_confirmation=False` Skills with policy.
- **Sleep-time soul evolution v2** — SLM critique pass that proposes (but cannot apply) Soul mutations for the Architect to approve, replacing the current rule-based `_consolidate_reflections`.
- **Encrypted state at rest** — for deployments in regulated environments (clinical, legal, financial).
- **Pytest suite expansion** — the project now ships a real `tests/` directory (113 tests covering `IntentRouter`, `RotatingJsonlWriter` schema + STM rotation, `PositiveFilter`, `prompt_assembly`, `SkillContext`, and the task-parsing pre-filters). The next coverage gaps worth filling: `Emotions` decay/nudge math, `CognitiveLoop` THINK/PLAN/REFINE parsing edge cases, `SkillRegistry.plan_menu` filtering under stressed vitals + offline brain, and `Provider`'s circuit-breaker trip / half-open / recover lifecycle. See [`good-first-issue`](https://github.com/easonlai/opencrayfish/labels/good-first-issue).

---

## Community & Contributing

OpenCrayFish is **open source under the [MIT License](LICENSE)** and every passionate developer, architect, hardware hacker, and AI tinkerer is welcome to co-develop. The crayfish is small, but the pond is deep — there is plenty of room.

> 💡 **We especially want you if…** you've ever wanted to wire a sensor, a smart bulb, a CalDAV calendar, a home-grown RAG, or an MCP server into a *living* edge agent and watch it learn from the result overnight. The Skill plugin layer (see [§ 12](#12-skills--tools--the-two-tier-capability-stack) and the [Hello-World Skill tutorial](#hello-world-skill--step-by-step)) is built so a working contribution is one file plus one line in `main.py`. The crayfish grows a little stronger — a new reflex, a new sense, a new way of reaching into the world — every time someone grafts one on.

### Where to start

| If you want to… | Go here |
|---|---|
| **Ask a question or share a deployment** | [GitHub Discussions](https://github.com/easonlai/opencrayfish/discussions) |
| **Report a bug** | [Bug Report template](https://github.com/easonlai/opencrayfish/issues/new?template=bug_report.yml) |
| **Propose a feature** | [Feature Request template](https://github.com/easonlai/opencrayfish/issues/new?template=feature_request.yml) (open a Discussion first for larger ideas) |
| **Submit code** | Read [CONTRIBUTING.md](CONTRIBUTING.md), then open a PR using the [PR template](.github/PULL_REQUEST_TEMPLATE.md) |
| **Report a security vulnerability** | [Private security advisory](https://github.com/easonlai/opencrayfish/security/advisories/new) — see [SECURITY.md](SECURITY.md). **Do NOT open a public Issue.** |

### Good first contributions

Look for the [`good-first-issue`](https://github.com/easonlai/opencrayfish/labels/good-first-issue) label. Concrete areas where help is especially appreciated:

- **Pytest suite expansion** — the `tests/` directory ships with 113 tests today (intent router, JSONL schema, STM rotation, positive filter, prompt assembly, skill context, task parsing). High-impact gaps still open: `Emotions` decay math, `CognitiveLoop` THINK/PLAN/REFINE parsing, `SkillRegistry.plan_menu` under stress + offline, and `Provider` circuit-breaker state transitions.
- **Hardware port reports** — try OpenCrayFish on a Pi 4, an Orange Pi, a Jetson Nano, or an x86 mini-PC and file an Issue tagged `hardware-port` with your `state/vitals.json` and notable latency numbers.
- **New `Tool` plugins** — implement the `Tool` protocol from [tools/base.py](tools/base.py) for local file ops, GPIO control, sensor reads, MCP servers, etc.
- **New `Connector`s** — Discord, Slack, Matrix, IRC, MCP server. Use [connectors/web_chat.py](connectors/web_chat.py) as a template.
- **Persona / soul.md presets** — share interesting personalities (with the secrets stripped) so others can fork them.
- **Translations of the EmpathyEngine lexicon** — currently English + Chinese; Japanese, Korean, Spanish, French, German, Hindi, Arabic all welcome.
- **Documentation** — worked examples, deployment guides (systemd unit, Docker compose for the local stack), screen recordings of the dashboard.

### Project conventions (the short version)

Before opening a PR, please read [CONTRIBUTING.md § Project Conventions](CONTRIBUTING.md#project-conventions-read-before-coding). The non-negotiable rules:

1. **Pylance-clean, type-annotated everywhere.**
2. **Frozen dataclasses for cross-subsystem snapshots.**
3. **Atomic writes (`tmp + os.replace`) for every state file the dashboard reads.**
4. **Asyncio-only, fine-grained locks. No threads. No `multiprocessing`.**
5. **Failures degrade — they do not raise into the heartbeat loop.**
6. **Pi 5 latency budget is real — 2–4 s wall-clock per engaged user turn on NPU.**
7. **Edge / Lite / Offline by mandate — no outbound third-party network calls, no "just call OpenAI".**
8. **Don't modify `soul.md` IMMUTABLE_CORE programmatically.**
9. **Don't commit secrets** — `config.yaml` is gitignored; use [config_sample.yaml](config_sample.yaml) as the template.

### Code of Conduct

This project follows the [Contributor Covenant 2.1](CODE_OF_CONDUCT.md). Be excellent to each other. Hostility, harassment, and condescension will get you removed.

---

## License & Attribution

- **License**: [MIT](LICENSE) — use, fork, modify, redistribute freely; just keep the copyright notice.
- **Author**: Eason Lai (Hong Kong) — original author of the OpenCrayFish project.
- **Codename**: OpenCrayFish (小龍蝦)
- **Inspirations**: biological neural systems, the Minions, every edge-AI hacker who refused to wait for the cloud.
- **Contributors**: see the [contributors graph](https://github.com/easonlai/opencrayfish/graphs/contributors). Every PR, bug report, and Discussion makes the crayfish stronger.

> *OpenCrayFish lives where the cloud can't go.*

🦐
