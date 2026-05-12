# 🦐 OpenCrayFish

> **An edge-native, biologically-inspired AI companion that lives on a single Raspberry Pi 5 — fully offline, powered by a 1.5B-parameter local SLM, with a heart that beats, a brain that sleeps, and a soul you can read.**

---

## Table of Contents

- [What OpenCrayFish Is](#what-opencrayfish-is)
- [Why This Project Exists](#why-this-project-exists)
- [Design Pillars](#design-pillars)
- [System Architecture at a Glance](#system-architecture-at-a-glance)
- [Hardware & Software Stack](#hardware--software-stack)
- [Repository Layout](#repository-layout)
- [Quick Start](#quick-start)
- [Deep Dive: How Everything Works](#deep-dive-how-everything-works)
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
  - [12. Tools — SearXNG and the Tool Registry](#12-tools--searxng-and-the-tool-registry)
  - [13. Connectors — Telegram & Web Chat](#13-connectors--telegram--web-chat)
  - [14. Observability — Dashboard & State Files](#14-observability--dashboard--state-files)
- [Cross-Cutting Concerns](#cross-cutting-concerns)
  - [Concurrency Model](#concurrency-model)
  - [Atomic Writes Everywhere](#atomic-writes-everywhere)
  - [Failure-Mode Matrix](#failure-mode-matrix)
  - [Pi 5 Latency Budget](#pi-5-latency-budget)
- [Configuration Reference](#configuration-reference)
- [Operational Commands](#operational-commands)
- [Development Notes](#development-notes)
- [Roadmap](#roadmap)
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
| 😊 **Mood** | A 5-channel emotion vector (joy / anger / sorrow / excitement / calm) with exponential decay |
| 💞 **Empathy** | Sentiment + urgency analysis of every Architect message |
| 🌙 **REM sleep** | A nightly Sleep Metabolism cycle (02:00–06:00) that distills the day |
| 🤔 **Curiosity** | Idle-time Proactive Research that closes real STM knowledge gaps |
| 🪞 **Reflection** | A self-critique pass on every reply, persisted to `state/reflection.jsonl` |

The agent serves a single human — **the Architect** — over Telegram and a local browser chat (Streamlit). The Architect's name, salutation, and the agent's own designation are all configured in `config.yaml`; the agent addresses the Architect by name in every reply (default: `"Boss <name>"`).

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
        └────┬───────┬───────┬───────┬───────┬───────┬────────┬──────┘
             │       │       │       │       │       │        │
             ▼       ▼       ▼       ▼       ▼       ▼        ▼
          Soul   Monitor  Emotions Empathy  STM  Provider  Reflection
          .md    (vitals)  (mood)  (sent.) (RAM (SLM       Engine
                                          +disk) circuit)
                                              │
                                              ▼
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
    state/vitals.json          ← live rolling pulse snapshot
    state/vitals_events.jsonl  ← stress ENTER/EXIT timeline
    state/proactive.jsonl      ← every proactive thought + audit trail
    state/reflection.jsonl     ← every self-critique
    state/deliberation.jsonl   ← every cognitive trace (THINK/PLAN/ACT)
    state/stm_journal.jsonl    ← durable conversation backstop
    state/tasks.yaml           ← persistent recurring task registry
    state/tools.json           ← live tool inventory snapshot
    memory/archive.md          ← long-term distilled facts
    logs/daily/YYYY-MM-DD.log  ← per-day heartbeat telemetry (path = memory.log_path)
```

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
│   ├── cognition.py          ← THINK → PLAN → ACT → REFINE loop
│   ├── brain.py              ← prompt assembly orchestrator
│   ├── reflection.py         ← self-critique engine
│   ├── heartbeat.py          ← pulse_loop + metabolism + proactive_thought
│   └── scheduler.py          ← recurring research-task scheduler
│
├── tools/                    ← agent-callable skills
│   ├── base.py               ← Tool plugin contract
│   ├── registry.py           ← named tool registry
│   └── searxng.py            ← self-hosted web search (SearXNG client)
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
    ├── reflection.jsonl
    ├── reflection_dropped.jsonl
    ├── deliberation.jsonl
    ├── stm_journal.jsonl
    ├── tasks.yaml
    ├── tools.json
    └── logs/agent.log        ← rotating console log mirror
```

---

## Quick Start

```bash
# 1. Clone & venv
git clone https://github.com/<your-fork>/OpenCrayFish
cd OpenCrayFish
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Bring up the local stack (separate terminals or systemd units)
ollama serve                              # CPU fallback
ollama pull qwen2:1.5b
docker run -d -p 8080:8080 searxng/searxng    # web search
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
```

Now talk to it on Telegram or in the browser. The agent will reply, remember, decay its mood, get curious during idle time, and once the clock crosses 02:00, it will sleep and consolidate the day.

---

## Deep Dive: How Everything Works

This is the comprehensive section. It documents every major subsystem in the order they are wired together in `main.py`, with file links into the codebase.

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
                              ← scan state/reflection.jsonl for recurring
                                interest topics + lesson themes
                              ← promote into LEARNED_PREFERENCES /
                                EMOTIONAL_EVOLUTION
  7. stm.purge()               ← wipe RAM deque + pending + journal
                                (the day's content has been consolidated)
```

#### Stress edge detection

Per-pulse stress is *not* logged or alerted (it would spam at every pulse the Pi is hot). Instead, `_record_stress_transition()` only emits **rising-edge ENTER** and **falling-edge EXIT** events to:

- `state/logs/agent.log` — operator-tailable warning lines
- `state/vitals_events.jsonl` — JSONL feed the dashboard renders as a stress timeline

EXIT events carry `duration_s`, `peak_temp`, and `peak_ram` from the entire stress episode.

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

---

### 6. The Thinking Process — Brain & Cognitive Loop

**Modules:** [core/brain.py](core/brain.py) · [core/cognition.py](core/cognition.py)

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

#### Cognitive Loop — THINK → PLAN → ACT → REFINE

Engaged on real user turns when no bypass condition fires. Each stage is **a single one-job SLM prompt** with hard token caps and regex parsing — *not* one giant chain-of-thought.

| Stage | What it does | Output cap |
|---|---|---|
| **THINK** | Restate user INTENT in one sentence + decompose into ≤ `cognition.max_subquestions` atomic Q1/Q2/Q3 sub-questions. | 120 tokens |
| **PLAN** | Assign exactly one verb to each sub-question: `RECALL` (hits archive.md), `SEARCH "..."` (hits SearXNG), or `ANSWER` (no retrieval needed). | 120 tokens |
| **ACT** | Execute all PlanSteps **concurrently**. Collect per-sub-question Evidence. | (executes verbs) |
| **REFINE** | (optional, capped at 1 round) Re-read intent + evidence; emit `OK` or `GAP: SEARCH "..."`; if gap, ACT it. | 40 tokens |

The full trace is appended to `state/deliberation.jsonl` for audit. **Failures never raise** — the loop degrades to whatever evidence it managed to collect.

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

Each pulse, every channel decays exponentially toward its baseline with a `half_life_pulses=6` (≈3 minutes at 30s/pulse). All deltas live on `MoodTuning` so brain.py / heartbeat.py read a single source of truth instead of sprinkling magic numbers.

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

  PLAN (two-stage filtering, audited)
    for candidate in gaps:
      ① _is_in_ltm(candidate)
            ← cheap substring check vs soul.md DYNAMIC + archive.md
            → hit? → verdict="in_ltm", skip
      ② brain.triage_knowledge(candidate)
            ← SLM self-assessment: "do you actually know this? answer YES/NO"
            → YES → verdict="known_by_slm", skip
            → NO  → verdict="unknown", PICK THIS
    (if no gap survives → fall back to soul.md [LEARNED_PREFERENCES] tail line)

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

Each entry is appended to `state/reflection.jsonl`. Failed/malformed parses go to `state/reflection_dropped.jsonl` so operators can see *why* a turn produced no reflection (instead of silently disappearing).

Sleep Metabolism's `_consolidate_reflections()` then mines this feed:

- **Recurring `interest` topics** → appended to `[LEARNED_PREFERENCES]` (drives tomorrow's proactive research).
- **Recurring `lesson` themes** → appended to `[EMOTIONAL_EVOLUTION]` (long-term behavioral evolution).

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

### 12. Tools — SearXNG and the Tool Registry

**Modules:** [tools/base.py](tools/base.py) · [tools/registry.py](tools/registry.py) · [tools/searxng.py](tools/searxng.py)

OpenCrayFish uses a self-hosted **SearXNG** instance (default `http://localhost:8080`) as its only network-touching tool. The agent never calls Google/Bing/etc. directly — every web fact passes through SearXNG, keeping search history off third-party servers.

`SearXNG` exposes two surfaces on the same class:

1. **Direct API** — `await searxng.search(query, limit=5) -> list[SearchResult]` used by Brain, CognitiveLoop, Heartbeat, and TaskScheduler.
2. **Tool plugin contract** — `name="web_search"`, `description`, `args_schema`, `call()`, `aclose()` — registered with `ToolRegistry` for future PLAN-stage tool dispatch by name.

`main.py` publishes the live tool inventory to `state/tools.json` (atomic write) so the dashboard can render the catalogue without sharing process state.

The Tool plugin contract (`tools/base.py`) is the extension point for future skills — local file ops, GPIO control, sensor reads, MCP servers, etc.

---

### 13. Connectors — Telegram & Web Chat

**Modules:** [connectors/telegram.py](connectors/telegram.py) · [connectors/web_chat.py](connectors/web_chat.py) · [ui/web_chat.py](ui/web_chat.py)

Two connectors run in the same `main.py` event loop, sharing the **same** Brain / STM / Heartbeat / Scheduler instances.

#### TelegramConnector

- python-telegram-bot polling.
- Validates incoming messages against `cfg.api_keys.telegram_user_id` — only the configured Architect can talk to the agent.
- Recognizes `/emergency <msg>` as a sleep-bypass marker (the only kind of message answered during 02:00–06:00).
- Slash commands: `/start`, `/tasks`, `/cancel`, `/pause`, `/resume`, `/research`.

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

The dashboard runs as a separate Streamlit process on port 8501 and reads the state files the Heartbeat publishes. **Zero IPC**, just atomically-written JSON / JSONL.

Live panels:

- **Vitals strip**: CPU / RAM / Temp / **Brain** (online + backend) / Mood / STM size / Pending writes
- **Pulse history sparkline**: rolling ~1 hour at 30s/pulse
- **Stress timeline**: rendered from `state/vitals_events.jsonl` ENTER/EXIT events
- **Recent proactive thoughts**: from `state/proactive.jsonl`, with full triage_decisions audit
- **Recent reflections**: from `state/reflection.jsonl`
- **Recent deliberations**: from `state/deliberation.jsonl` (THINK → PLAN → ACT trace per turn)
- **Active recurring tasks**: from `state/tasks.yaml`
- **Tool inventory**: from `state/tools.json`

Logs:

- `state/logs/agent.log` — rotating console mirror (2 MB × 5 files)
- `<memory.log_path>/YYYY-MM-DD.log` — per-day heartbeat telemetry (PULSE / PROACTIVE / metabolism). Default `logs/daily/`.

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
| `TaskScheduler._save` | `state/tasks.yaml` | `ui/dashboard.py` + scheduler bootstrap |
| `SoulHandler._write` | `soul.md` | every Brain cycle |
| `STM._fsync_journal` (append-only) | `state/stm_journal.jsonl` | `STM.recover()` at boot |

For append-only feeds (`*.jsonl`), atomicity comes from the OS guarantee that a single `write()` of a buffered JSON line is not torn — combined with always emitting one complete record per `write()` call.

### Failure-Mode Matrix

Each subsystem is designed to **degrade, not crash**, when its dependency is broken. The table below is the operator's mental model for "what does the agent do when X dies":

| Failure | Surface to Architect | Recovery |
|---|---|---|
| **Both inference backends down** | `ProviderUnavailable` raised once at `Brain._cycle` top → synthetic `ThoughtTrace(backend="offline")` with friendly first-person message rendered by the connector. Dashboard shows 🔴 BRAIN OFFLINE chip. | Circuit-breaker auto-recovers on next call after `trip_seconds` (default 30 s). Restart `ollama serve` or `hailo-ollama`. |
| **NPU backend down, CPU up** | Silent fallback. `provider.active_backend` flips to `"ollama-cpu"` and the dashboard chip updates. No Architect-visible message. | Provider re-tries primary on every call; once NPU comes back, the next call routes back to it. |
| **SearXNG down** | Web triage fails open: cognition's `SEARCH` evidence is empty, but `RECALL` and `ANSWER` still produce a reply. Background `tools/searxng.py` logs `WARN`. | Restart the SearXNG container. No agent-side state to clear. |
| **SD card full / write fails** | STM `flush_journal()` logs the exception and continues. The deque + pending buffer keep accumulating in RAM. Sleep Metabolism `_append_archive()` likewise tolerates write failure. | Free disk space; the next idle flush will succeed and the buffered turns are preserved. |
| **STM journal corrupted** | `STM.recover()` skips malformed lines and continues. Worst case: zero turns rehydrated. | None required — the journal is truncated to zero on the next Sleep Metabolism `purge()`. |
| **soul.md mutation rejected** | `SoulProtectionError` raised inside `metabolism()` is caught and logged. No partial write occurs (atomic swap). | Architect inspects the proposed Core Memory in `state/proactive.jsonl`; nothing else is required. |
| **Cognitive Loop fails to parse THINK output** | Loop bails with `engaged=False`, `bypass_reason="think_unparseable"`. Brain falls through to legacy single-shot path. Trace still appended to `state/deliberation.jsonl` for forensics. | None — every cycle is independent. |
| **Connector outage (Telegram API rate-limit)** | python-telegram-bot retries internally. Agent state untouched; replies queue and drain when API recovers. | None — wait for rate-limit window. |
| **Heartbeat coroutine raises** | Exception propagates; Heartbeat task dies; `await pulse_task` in `main.amain()` raises and the process exits non-zero. | Restart the agent (systemd unit recommended). STM rehydrates from journal at boot. |

The repeated theme: **per-turn / per-pulse work is independent**, so a single failure never poisons the next cycle. The only un-survivable failure is the `Heartbeat` task itself dying, because there's no point keeping a body alive without a heart — better to fail loudly under systemd than to silently freeze.

### Pi 5 Latency Budget

Numbers below are typical-case for the reference deployment (Pi 5 + AI HAT+ 2 / Hailo-10H, qwen2.5-instruct:1.5b NPU primary). Treat them as ballpark; the only authoritative numbers are what your `state/deliberation.jsonl` records on YOUR hardware.

| Stage | Typical (ms) | Notes |
|---|---|---|
| `Monitor.sample()` first call | ~100 | `psutil.cpu_percent(interval=0.1)` blocks for the sampling window |
| `Monitor.sample()` cached (≤ `vitals_cache_ttl_seconds`) | <1 | hot path on every Brain cycle |
| `Empathy.analyze()` | <5 | dependency-free lexicon scan |
| `PositiveFilter.apply()` | <2 | regex sweep |
| `LTM keyword scan` (archive.md ≤ 1000 lines) | 5–20 | linear read; bounded by `archive.md` size |
| `Identity short-circuit` reply (full) | <10 | zero SLM calls |
| `Cognition THINK` (120 tok cap) | 400–800 | one NPU call |
| `Cognition PLAN` (120 tok cap) | 400–800 | one NPU call |
| `Cognition ACT` (per `SEARCH`) | 200–500 | SearXNG round-trip + parse |
| `Cognition ACT` (per `RECALL`) | <20 | local archive scan |
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
  refine_enabled: true

cognition:
  enabled: true
  max_subquestions: 3
  max_act_rounds: 2                       # 2 = REFINE allowed; 1 = no refine
  refine_enabled: true

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

Both connectors recognize:

| Slash | Natural Language equivalent | Action |
|---|---|---|
| `/start` | (Telegram only) | Greeting + designation reveal |
| `/tasks` | "show my tasks", "what's scheduled" | List active recurring tasks |
| `/cancel <id>` | "cancel task abc123", "stop the bitcoin task" | Remove a task |
| `/pause <id>` | "pause task abc123" | Pause without removing |
| `/resume <id>` | "resume task abc123" | Resume a paused task |
| `/research [topic]` | "research <topic>" | Trigger an on-demand proactive cycle |
| `/emergency <msg>` | (Telegram only) | Bypass Sleep Metabolism gating |

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

Implement the `Tool` protocol from [tools/base.py](tools/base.py) (`name`, `description`, `args_schema`, async `call(**kwargs) -> ToolResult`, async `aclose()`), register it in `main.py` via `tool_registry.register(my_tool)`, and re-publish the inventory snapshot. Future PLAN-stage code will dispatch by `name`.

### Adding a new sensor (GPIO / I²C / etc.)

The `Monitor.sample()` → `VitalSigns` dataclass is the right extension surface. Add new optional fields with `None` defaults (preserves backward compatibility for any positional callers), plumb them through `vitals.describe()` so the SLM sees them in the prompt, and (optionally) into `Emotions.MoodTuning` so they can drive mood transitions. The dashboard chip strip is a single Streamlit row — adding columns is trivial.

---

## Roadmap

The current codebase is a **complete v1**: every subsystem in this README is implemented, tested with smoke scripts, and Pylance-clean. The natural next directions:

- **GPIO / I²C sensor library** — temperature/humidity (BME680), motion (PIR), light (TSL2591), gas (CCS811/MQ-2), heart rate (MAX30102), motion (MPU6050) feeding straight into `VitalSigns` and `MoodTuning`.
- **Local vision** — small VLM on the NPU + CSI camera, Tool-registered as `see()`.
- **Local voice** — Whisper.cpp for in + Piper TTS for out, registered as bidirectional connectors.
- **Output actuators** — WS2812 LED strip for mood visualization, OLED for facial expression, servos for embodied motion.
- **Sleep-time soul evolution v2** — instead of rule-based `_consolidate_reflections`, an SLM critique pass that proposes (but cannot apply) Soul mutations for the Architect to approve.
- **MCP server tools** — register MCP servers as Tool plugins so the agent can call any local MCP-compatible service.
- **Encrypted state at rest** — for deployments in regulated environments (clinical, legal, financial).

---

## License & Attribution

- **Author**: Eason Lai (Hong Kong) — author of the OpenCrayFish project (immutable across deployments).
- **Codename**: OpenCrayFish (小龍蝦)
- **Inspirations**: biological neural systems, the Minions, every edge-AI hacker who refused to wait for the cloud.

Released under the MIT License. See `LICENSE` for full text.

> *OpenCrayFish lives where the cloud can't go.*

🦐
