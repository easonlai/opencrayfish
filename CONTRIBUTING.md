# Contributing to OpenCrayFish

First — thank you. OpenCrayFish exists because edge AI deserves more hands than just one. Whether you're hardening the Cognitive Loop, adding a new sensor, porting to a new SBC, or just fixing a typo, your time is appreciated.

This document is the **operator's manual for contributors**. Read the section relevant to what you want to do; you don't need to read the whole thing.

---

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Ways to Contribute](#ways-to-contribute)
- [Development Setup](#development-setup)
- [Project Conventions (Read Before Coding)](#project-conventions-read-before-coding)
- [Running the Smoke Tests](#running-the-smoke-tests)
- [Submitting a Pull Request](#submitting-a-pull-request)
- [Reporting Bugs](#reporting-bugs)
- [Proposing Features](#proposing-features)
- [Reporting Security Issues](#reporting-security-issues)
- [Maintainer Response Expectations](#maintainer-response-expectations)

---

## Code of Conduct

This project follows the [Contributor Covenant 2.1](CODE_OF_CONDUCT.md). By participating you agree to uphold it. **Be excellent to each other.** Hostility, harassment, and condescension will get you removed, full stop.

---

## Ways to Contribute

Not all contributions are code. The following are equally welcome:

| Contribution | Where it lands |
|---|---|
| Bug reports | [Issues](https://github.com/easonlai/opencrayfish/issues) using the Bug Report template |
| Feature proposals | [Issues](https://github.com/easonlai/opencrayfish/issues) using the Feature Request template |
| Documentation fixes | Direct PR to [README.md](README.md), [CONTRIBUTING.md](CONTRIBUTING.md), or any module docstring |
| New tools (web, GPIO, MCP, etc.) | New file under [tools/](tools/) implementing the `Tool` protocol |
| New skills (capability orchestration above Tools) | New file under [core/skills/](core/skills/) implementing the `Skill` protocol |
| New connectors (Discord, Slack, MCP server, …) | New file under [connectors/](connectors/) |
| New sensors (BME680, MPU6050, …) | Extension of `VitalSigns` in [core/monitor.py](core/monitor.py) |
| Hardware port reports (Pi 4, Orange Pi, Jetson Nano, x86 mini-PC) | Open an Issue tagged `hardware-port` with your `state/vitals.json` and `state/deliberation.jsonl` excerpts |
| Persona / soul.md presets | New file under a `presets/` folder (gitignored if it contains personal data) |

If you're not sure where your idea fits, **open a Discussion or an Issue first** — we'll help you find the cleanest place for it.

---

## Development Setup

```bash
# 1. Fork on GitHub, then clone your fork
git clone https://github.com/<your-username>/opencrayfish
cd opencrayfish

# 2. Create a virtual environment (Python 3.11+ required, 3.13 recommended)
python3.13 -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate

# 3. Install runtime + dev dependencies
pip install -r requirements.txt

# 4. Set up local config (NEVER commit config.yaml — it holds secrets)
cp config_sample.yaml config.yaml
$EDITOR config.yaml          # for dev: leave npu_acceleration: false

# 5. Bring up the local stack (in separate terminals)
ollama serve                 # CPU fallback
ollama pull qwen2:1.5b
docker run -d -p 8080:8080 searxng/searxng

# 6. Smoke-test imports + config load
python -c "from core.config import Config; c = Config.load('config.yaml'); print('OK', c.system.individual_designation)"

# 7. Run the agent
python main.py
```

You don't need a Raspberry Pi or NPU to develop. Set `hardware.npu_acceleration: false` in `config.yaml` and the Provider runs CPU-only via stock Ollama. `temperature_c` will be `None` on macOS / non-Linux dev boxes — that's expected and handled gracefully.

To exercise the EXHAUSTION DIRECTIVE branch on a cool dev machine:

```bash
OCF_FORCE_STRESS=1 python main.py
```

---

## Project Conventions (Read Before Coding)

OpenCrayFish has a small set of architectural rules that are NOT optional. Violating them silently breaks 24/7 reliability. They are short:

### 1. Pylance-clean, type-annotated everywhere

Every public function, dataclass field, and module-level variable has a type annotation. The codebase passes Pylance "basic" mode without warnings. New code must do the same.

### 2. Frozen dataclasses for cross-subsystem snapshots

Anything that crosses a subsystem boundary (e.g. `VitalSigns`, `EmotionVector`, `EmpathyReading`, `ThoughtTrace`) is a `@dataclass(frozen=True)` — immutable, hashable, easy to log. New cross-cutting types should follow this pattern.

### 3. Atomic writes for every state file the dashboard reads

Use the `tmp + os.replace` pattern. Reference: `_publish_tools_inventory()` in `main.py`. **Never** open the live state file for writing in place — Streamlit runs in a separate process and will read a torn JSON.

```python
tmp = out_path.with_suffix(".json.tmp")
tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
tmp.replace(out_path)   # POSIX atomic rename
```

### 4. Asyncio-only, fine-grained locks

OpenCrayFish is single-process, single-event-loop. **No threads. No `multiprocessing`. No queues.** If you need to mutate shared state, use an `asyncio.Lock` scoped to the smallest possible critical section. See `Emotions._lock`, `SoulHandler._lock`, `ShortTermMemory._lock` for reference patterns.

### 5. Failures degrade — they do not raise

Per-pulse / per-turn work is independent. If your subsystem encounters a recoverable failure (network down, file corrupted, parse failed), **log a WARN and continue** — never raise into the heartbeat loop. The only un-survivable failure is `Heartbeat.pulse_loop()` itself dying. Reference: the [Failure-Mode Matrix in README.md](README.md#failure-mode-matrix).

### 6. Pi 5 latency budget is real

Engaged-turn user-visible latency budget is **2–4 seconds wall-clock on NPU**, **~4–8× slower on CPU**. If your change adds an SLM call to the user-visible hot path, justify it. Background calls (reflection, metabolism, proactive) are exempt because they're fire-and-forget.

### 7. Edge / Lite / Offline by mandate

Three rules that gate every new dependency:

- **No outbound third-party network calls.** All inference is local (Ollama / Hailo-Ollama). All web search goes through self-hosted SearXNG. Pulling the network cable must not break the chat path.
- **No package > 50 MB without a vital justification.** This runs on an SD card.
- **No "we'll just call OpenAI / Claude / Gemini".** That defeats the entire project. If your feature requires a frontier model, propose an opt-in plugin path — not a default.

### 8. Don't modify `soul.md` IMMUTABLE_CORE programmatically

Pillar 1. The agent cannot mutate its own ethical framework. If you need to test a Soul change, edit `soul.md` directly as a human — never via code.

### 9. Don't commit secrets

`config.yaml` is gitignored. The committed template is [config_sample.yaml](config_sample.yaml). If you accidentally commit a Telegram token or any other credential, **rotate it immediately** (see [SECURITY.md](SECURITY.md)) — git history retention means assume-compromised.

---

## Running the Smoke Tests

The project does not yet have a full pytest suite (PRs welcome — see Issues tagged `good-first-issue`). The current smoke checks are:

```bash
# Config + imports load cleanly
python -c "from core.config import Config; c = Config.load('config.yaml'); print('OK', c.system.individual_designation)"

# Every core module imports without side effects
python -c "from core import brain, heartbeat, cognition, emotions, empathy, monitor, provider, reflection, scheduler, soul_handler, stm, positive_filter; print('imports OK')"

# Tools + connectors + skills import
python -c "from tools import searxng, registry, base, archive_read; from connectors import telegram, web_chat; from core.skills import identity, recall, research, direct_answer, self_reflect, proactive_learning, recurring_research; print('tools+connectors+skills OK')"

# PLAN-stage menu filter matrix (cost-tier cap, offline, stressed)
python scripts/smoke_skill_menu.py

# CognitiveLoop dispatch + dynamic menu + degrade paths
python scripts/smoke_cognition_dispatch.py

# JSONL rotation + reflection skill-failure summary + identity skill
python scripts/smoke_rotation_reflection_identity.py

# Dashboard rotation-aware feed readers (fan-out across rotated siblings)
python scripts/smoke_dashboard_rotation.py

# soul.md atomic-write + crash-safety (tmp + os.replace + cleanup-on-failure)
python scripts/smoke_soul_atomic.py

# Architect-priority cooperative yield (Brain ↔ Heartbeat foreground signalling)
python scripts/smoke_foreground_priority.py

# Pylance check (in VS Code: open the workspace — should show 0 problems)
```

Each smoke script prints `ALL ... PASSED` on success and exits non-zero on failure, so you can chain them in CI.

If your PR adds a new module, add an import line for it to the smoke checks. If your PR adds a new Skill, register it in `scripts/smoke_skill_menu.py` and verify the assertions still hold.

---

## Submitting a Pull Request

1. **Fork** the repo and create a topic branch from `main`:

   ```bash
   git checkout -b feat/short-descriptive-name
   ```

2. **Make your change.** Keep PRs focused — one logical change per PR. If you're refactoring while adding a feature, split into two PRs.

3. **Run the smoke tests** (above). They should all print `OK`.

4. **Update documentation.** If you changed behavior visible from `config.yaml`, the dashboard, the connector wire format, or any state file, update the README's relevant section in the same PR. Stale docs are worse than no docs.

5. **Write a clear commit message.** First line ≤ 72 chars, imperative mood ("Add X", not "Added X" / "Adds X"). Body explains the *why*, not the *what* — the diff already shows the what.

6. **Open the PR.** Fill in the [PR template](.github/PULL_REQUEST_TEMPLATE.md). Link the Issue it closes. Tag a maintainer if it's been > 7 days without review.

7. **Be responsive to review.** Reviewers will be polite and direct. Don't take suggestions personally — they're about the code, not you.

### What gets merged fast

- Bug fixes with a one-line repro and a one-line fix.
- Documentation improvements.
- New `Tool` plugins, `Connector` implementations, or `VitalSigns` sensor extensions that follow the existing patterns.
- Pylance / type-hint cleanups.

### What gets debated

- Changes to the Cognitive Loop's THINK→PLAN→ACT→REFINE structure.
- Changes to `Brain._cycle()` step order.
- New runtime dependencies (each one extends the cold-boot time on a Pi 5).
- Anything that touches `soul.md`'s IMMUTABLE_CORE handling.

### What gets rejected

- "Just call GPT-4" / "Just use OpenAI Embeddings" — see Convention #7.
- Adding `multiprocessing` or threads — see Convention #4.
- Removing the PositiveFilter or any other Pillar — those are non-negotiable.
- PRs that delete tests / smoke checks "to make CI pass".

---

## Reporting Bugs

Use the [Bug Report template](https://github.com/easonlai/opencrayfish/issues/new?template=bug_report.yml). Include:

- The exact `python main.py` command + relevant flag (e.g. `OCF_FORCE_STRESS=1`)
- `state/vitals.json` snapshot at time of failure
- Last ~50 lines of `state/logs/agent.log`
- Last ~10 entries of `state/deliberation.jsonl` (if the bug is cognition-related)
- `python --version`, OS, hardware (Pi 5 / dev box / other)

**Do NOT include** your `config.yaml`, your Telegram token, or anything from `state/stm_journal.jsonl` (it contains your conversations).

---

## Proposing Features

Use the [Feature Request template](https://github.com/easonlai/opencrayfish/issues/new?template=feature_request.yml). For larger features, open a **Discussion** first to scope it — saves you from writing code that won't be merged.

Good feature proposals answer:

1. What user-visible problem does this solve?
2. Why does it belong in core, vs. as an external tool / connector?
3. What's the impact on the Pi 5 latency budget?
4. Does it require a new runtime dependency? If yes, how heavy?
5. Does it violate any Convention? (Be honest — sometimes the answer is "yes, and we should change the convention.")

---

## Reporting Security Issues

**Do NOT open a public Issue for security vulnerabilities.** Use [GitHub's private security advisory flow](https://github.com/easonlai/opencrayfish/security/advisories/new). Full process is in [SECURITY.md](SECURITY.md).

---

## Maintainer Response Expectations

This is a small project run by humans with day jobs. Realistic SLOs:

| Item | Typical response |
|---|---|
| Bug report (with repro) | Acknowledged within 7 days |
| Bug report (no repro) | Eventually triaged, lower priority |
| Documentation PR | Reviewed within 7 days |
| Small code PR (< 100 LoC, tests pass) | Reviewed within 14 days |
| Larger feature PR | Reviewed within 30 days; expect multiple rounds |
| Security report (private advisory) | Acknowledged within 72 hours |

If you haven't heard back within those windows, a polite ping in the thread is welcome.

---

Thank you again. The crayfish is small, but the pond is deep — let's swim well together. 🦐
