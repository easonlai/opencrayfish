# Security Policy

## Supported Versions

OpenCrayFish is in active development on `main`. There is currently no LTS branch — security fixes land on `main` and are tagged with the next release.

| Version | Supported |
|---|---|
| `main` (latest) | ✅ Yes |
| Tagged releases | ✅ Latest tag only |
| Older tags | ❌ Please upgrade |

## Reporting a Vulnerability

**Do NOT open a public GitHub Issue, Discussion, or Pull Request for security vulnerabilities.** Public disclosure before a fix is shipped puts every operator running OpenCrayFish at risk.

### Use GitHub's private security advisory flow

1. Go to <https://github.com/easonlai/opencrayfish/security/advisories/new>
2. Fill in:
   - **Title** — short, descriptive (e.g. `Telegram token leaked via /healthz response`)
   - **Description** — what you found, where (file + line if known), and impact
   - **Steps to reproduce** — minimal repro the maintainer can run locally
   - **Proof of concept** — attach if you have one; please redact any real secrets
   - **Affected versions** — commit SHA or tag where the issue exists
   - **Suggested fix** — optional but appreciated
3. Submit. Only the repository maintainers will see it.

### What to expect

| Stage | Target time |
|---|---|
| Acknowledgement that the report was received | ≤ 72 hours |
| Initial triage + severity assessment | ≤ 7 days |
| Fix landed on `main` (or written-up "won't fix" with reasoning) | ≤ 30 days for High / Critical, best-effort for Low / Medium |
| Coordinated public disclosure (CVE if applicable, advisory published) | After the fix is released, and you've had a chance to verify |

You will be credited in the advisory unless you prefer to remain anonymous.

## What Counts as a Security Issue

OpenCrayFish ships several attack surfaces. Anything in this list is in-scope:

| Surface | Concern |
|---|---|
| **Telegram connector** | Auth-bypass against `cfg.api_keys.telegram_user_id`, message spoofing, command injection via `/cancel`/`/pause` arg parsing |
| **WebChat HTTP bridge** | Auth-bypass against `web_chat.auth_token`, request smuggling, leaking another user's STM via `GET /history`, CSRF on `POST /chat`, RCE via JSON parsing |
| **SearXNG client** | SSRF, response-parsing crashes, content-injection that leaks into the SLM prompt unsanitised |
| **Provider / Ollama integration** | Prompt-injection that bypasses the PositiveFilter or the Identity short-circuit, prompt-leak that exposes the system prompt to the user |
| **soul.md handler** | Any path that mutates IMMUTABLE_CORE bytes from a code path other than a human edit |
| **State files** | Path traversal in `_publish_*` writers, secrets accidentally written into `state/vitals.json` or `state/logs/agent.log` |
| **STM journal / archive.md** | Disclosure of conversation history to an unauthorized reader on the same host |
| **Dashboard** | XSS in any panel that renders user-controlled strings (proactive topics, task names) |

## What Is NOT a Security Issue

To save us both time, the following are **not** security issues — they are documented constraints. PRs to harden any of these are welcome via the normal contribution flow.

- A user with **shell access to the device** can read `config.yaml`, `memory/archive.md`, `state/stm_journal.jsonl`, etc. The threat model assumes the operator owns the device. If you cannot trust who has shell access, encrypt the SD card at the OS level.
- A user with **physical access to the SD card** can read everything. Same reasoning.
- The `/healthz` endpoint returning 200 OK without auth.
- The dashboard not requiring auth on a `127.0.0.1` bind.
- The agent confidently saying something factually wrong (that's a model issue, not a security issue — file a normal bug).
- Long latency / DoS via expensive prompts to the SLM (intrinsic to local SLM hosting; rate-limit at your reverse proxy).

## Hardening Checklist for Operators

If you're running OpenCrayFish in production (always-on Pi, exposed to a LAN or beyond), apply these:

1. **Set `web_chat.auth_token`** to a long random secret. Default empty = no auth.
2. **Keep `web_chat.host: "127.0.0.1"`** unless you've put a reverse proxy with TLS in front of it. Never bind `0.0.0.0` and expose port 8765 to the internet.
3. **Set a strict `cfg.api_keys.telegram_user_id`** so only your Telegram account can talk to the bot. Verify with the bot's `/start` from a second account — it should refuse.
4. **Run `main.py` under a dedicated non-root user** with no sudo rights and no access to other users' home directories.
5. **Enable disk encryption** (`cryptsetup`/`LUKS` on Linux) on the SD card or NVMe. Conversations are persisted to disk by design.
6. **Rotate the Telegram bot token** if it has ever been committed to a repo, posted in a chat, or stored in plaintext outside `config.yaml`. Use `@BotFather` → `/revoke`.
7. **Pin all dependencies** in your fork (currently `requirements.txt` has loose pins) and review the diff on every `pip install --upgrade`.
8. **Run `ollama` and `searxng` on `127.0.0.1`** not `0.0.0.0` unless you specifically want LAN access — they will happily answer requests from anywhere on your network.

---

Thank you for helping keep OpenCrayFish — and the people running it — safe. 🦐
