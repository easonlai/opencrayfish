"""core.reflection — Self-reflection / self-learning engine.

After every interaction (user-driven or proactive), a short SLM call critiques
the agent's own output and emits a structured `ReflectionEntry`:

    {
        "ts": ISO timestamp,
        "kind": "user" | "proactive",
        "input": <user_input or proactive topic>,
        "response": <agent's filtered text>,
        "web_searched": bool,
        "quality": "high" | "medium" | "low",
        "critique": <one-sentence self-assessment>,
        "lesson": <one-sentence actionable lesson>,
        "interest": <topic worth exploring later, or "">,
        "backend": <model id that produced the answer>
    }

All entries are appended (newline-delimited JSON) to
`state/reflection.jsonl` (rotated by local date with bounded retention)
for permanent audit. During Sleep Metabolism, the heartbeat consolidates
recurring `interest` topics into LEARNED_PREFERENCES, recurring `lesson`
themes into EMOTIONAL_EVOLUTION, and chronic Skill failures (from
`state/skills.jsonl`) into EMOTIONAL_EVOLUTION as well — this is the
closed self-learning loop.

The engine never raises; failures are logged and produce no entry.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from .provider import Provider

log = logging.getLogger(__name__)

REFLECTION_FEED: Path = Path("state/reflection.jsonl")
# Rejected payloads land here so the operator can audit *why* a turn produced
# no reflection (instead of just disappearing). One JSONL line per drop.
REFLECTION_DROPPED_FEED: Path = Path("state/reflection_dropped.jsonl")
# ReflectionEngine reads the skill-invocation audit feed during Sleep
# Metabolism consolidation so the agent can spot recurring Skill
# failures (e.g. SearXNG unreachable for the last 24 h) and act on them.
SKILLS_AUDIT_FEED: Path = Path("state/skills.jsonl")

# Tolerant key scanner: finds each KEY: anywhere in the raw text (not just at
# line start). Each field's value is everything between this key and the next
# key (or end-of-string). This recovers from common small-model misformats
# like collapsing all four fields onto one line with `|` separators, e.g.
# `QUALITY: high | CRITIQUE: ... | LESSON: ... | INTEREST: ...`.
_KEY_RE = re.compile(
    r"\b(QUALITY|CRITIQUE|LESSON|INTEREST)\s*:\s*",
    re.IGNORECASE,
)
_QUALITY_VALUE_RE = re.compile(r"\b(high|medium|low)\b", re.IGNORECASE)


@dataclass(frozen=True)
class ReflectionEntry:
    ts: str
    kind: str
    input: str
    response: str
    web_searched: bool
    quality: str
    critique: str
    lesson: str
    interest: str
    backend: str


class ReflectionEngine:
    """SLM-driven self-critique + persistence."""

    def __init__(
        self,
        *,
        provider: Provider,
        timezone: str = "UTC",
        feed_path: Path | str = REFLECTION_FEED,
        dropped_feed_path: Path | str = REFLECTION_DROPPED_FEED,
        skills_feed_path: Path | str = SKILLS_AUDIT_FEED,
        retain_days: int = 60,
    ) -> None:
        from .jsonl_writer import RotatingJsonlWriter
        from .provider import ChatMessage  # local import; avoid cycle on type stub

        self._provider = provider
        self._ChatMessage = ChatMessage
        self._tz = ZoneInfo(timezone)
        self._feed_path = Path(feed_path)
        self._feed_path.parent.mkdir(parents=True, exist_ok=True)
        self._dropped_feed_path = Path(dropped_feed_path)
        self._dropped_feed_path.parent.mkdir(parents=True, exist_ok=True)
        # Rotate both feeds by local date with bounded retention.
        # Reflections drive Sleep Metabolism consolidation, so the
        # window must be longer than the consolidation lookback
        # (currently 24 h) by a comfortable margin.
        self._writer = RotatingJsonlWriter(
            self._feed_path,
            retain_days=retain_days,
            tz=timezone,
        )
        self._dropped_writer = RotatingJsonlWriter(
            self._dropped_feed_path,
            retain_days=retain_days,
            tz=timezone,
        )
        # Skills audit feed is OWNED by SkillRegistry (which writes
        # through its own RotatingJsonlWriter). ReflectionEngine only
        # READS sibling rotated files for the Sleep Metabolism summary.
        self._skills_reader = RotatingJsonlWriter(
            Path(skills_feed_path),
            retain_days=0,   # never sweep — we're a reader, not the owner
            tz=timezone,
        )

    # ---------- public surface ------------------------------------------------

    async def reflect(
        self,
        *,
        kind: str,
        input_text: str,
        response: str,
        web_searched: bool,
        backend: str,
    ) -> ReflectionEntry | None:
        """Produce + persist one reflection entry. Returns None on failure."""
        if not response.strip():
            return None
        try:
            critique_raw = await self._invoke_critique(
                kind=kind,
                input_text=input_text,
                response=response,
                web_searched=web_searched,
            )
        except Exception:
            log.exception("Reflection LLM call failed")
            return None

        parsed = self._parse(critique_raw)
        if parsed is None:
            log.warning("Reflection output unparseable; dropping. Raw=%r", critique_raw[:200])
            await self._persist_dropped(
                kind=kind,
                input_text=input_text,
                response=response,
                web_searched=web_searched,
                backend=backend,
                raw=critique_raw,
                reason="unparseable",
            )
            return None

        entry = ReflectionEntry(
            ts=datetime.now(tz=self._tz).isoformat(),
            kind=kind,
            input=input_text[:500],
            response=response[:1000],
            web_searched=web_searched,
            backend=backend,
            **parsed,
        )
        await self._persist(entry)
        log.info(
            "Reflection: quality=%s interest=%r lesson=%r",
            entry.quality,
            entry.interest or "(none)",
            entry.lesson[:80],
        )
        return entry

    async def fire_and_forget(
        self,
        *,
        kind: str,
        input_text: str,
        response: str,
        web_searched: bool,
        backend: str,
    ) -> asyncio.Task:
        """Schedule reflection without blocking the caller (e.g. user reply)."""
        return asyncio.create_task(
            self.reflect(
                kind=kind,
                input_text=input_text,
                response=response,
                web_searched=web_searched,
                backend=backend,
            )
        )

    # ---------- reading (used by heartbeat for consolidation) ----------------

    def read_recent(self, since: datetime | None = None) -> list[ReflectionEntry]:
        """Return reflection entries newer than `since`.

        Reads BOTH the legacy single-file feed (if present, for backwards
        compatibility with sites that still have the un-rotated file) AND
        every rotated sibling produced by `RotatingJsonlWriter`. Entries
        with malformed JSON or missing keys are silently skipped — the
        consolidator must keep working even if a partial line ever lands
        on disk during an unclean shutdown.
        """
        paths: list[Path] = list(self._writer.sibling_paths())
        if self._feed_path.exists() and self._feed_path not in paths:
            paths.append(self._feed_path)
        out: list[ReflectionEntry] = []
        for p in paths:
            try:
                lines = p.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    d = json.loads(line)
                    ts = datetime.fromisoformat(d["ts"])
                    if since is not None and ts < since:
                        continue
                    out.append(ReflectionEntry(**d))
                except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                    continue
        out.sort(key=lambda e: e.ts)
        return out

    def read_recent_skills(
        self, since: datetime | None = None,
    ) -> list[dict]:
        """Return Skill audit rows newer than `since` (raw dicts).

        Owned by `SkillRegistry`; ReflectionEngine only reads. The shape
        matches the registry's `_audit()` payload:
            {ts, skill, ok, latency_ms, tools_used, kwargs_keys, error}
        Tolerant of legacy/rotated files just like `read_recent` above.
        """
        legacy = self._skills_reader.base_path
        paths: list[Path] = list(self._skills_reader.sibling_paths())
        if legacy.exists() and legacy not in paths:
            paths.append(legacy)
        out: list[dict] = []
        for p in paths:
            try:
                lines = p.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    d = json.loads(line)
                    ts_str = d.get("ts")
                    if not ts_str:
                        continue
                    if since is not None and datetime.fromisoformat(ts_str) < since:
                        continue
                    out.append(d)
                except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                    continue
        out.sort(key=lambda e: e.get("ts", ""))
        return out

    def summarise_skills_recent(
        self, since: datetime | None = None,
    ) -> dict[str, dict]:
        """Aggregate recent Skill invocations into a per-skill summary.

        Returns ``{skill_name: {total, ok, failed, fail_rate,
        avg_latency_ms, last_error}}`` for every Skill seen since the
        cutoff. Used by Sleep Metabolism to detect systemic failures
        (e.g. SearXNG unreachable, RAG corpus broken) and surface them
        in soul.md so the agent remembers what went wrong.
        """
        rows = self.read_recent_skills(since=since)
        agg: dict[str, dict] = {}
        for r in rows:
            name = str(r.get("skill") or "?")
            slot = agg.setdefault(name, {
                "total": 0, "ok": 0, "failed": 0,
                "latency_sum": 0, "last_error": "",
            })
            slot["total"] += 1
            if r.get("ok"):
                slot["ok"] += 1
            else:
                slot["failed"] += 1
                err = (r.get("error") or "").strip()
                if err:
                    slot["last_error"] = err[:200]
            try:
                slot["latency_sum"] += int(r.get("latency_ms") or 0)
            except (TypeError, ValueError):
                pass
        # Finalise computed fields. Done after the pass so we never
        # divide-by-zero mid-loop.
        for slot in agg.values():
            total = slot["total"] or 1
            slot["avg_latency_ms"] = round(slot["latency_sum"] / total, 1)
            slot["fail_rate"] = round(slot["failed"] / total, 3)
            del slot["latency_sum"]
        return agg

    # ---------- internals -----------------------------------------------------

    async def _invoke_critique(
        self,
        *,
        kind: str,
        input_text: str,
        response: str,
        web_searched: bool,
    ) -> str:
        ctx = (
            "user-driven exchange (the Architect spoke; the agent replied)"
            if kind == "user"
            else "autonomous proactive thought (no human input)"
        )
        ground = "Web search WAS performed for this turn." if web_searched else "No web search this turn."
        triage_system = (
            "You are a self-reflection module embedded in an AI agent.\n"
            "You evaluate the agent's OWN reply for accuracy, helpfulness, and\n"
            "whether the agent should learn anything for the future.\n\n"
            f"Context: {ctx}.\n"
            f"{ground}\n\n"
            "Output EXACTLY four lines, in this order, no extra prose. Put\n"
            "each KEY on its OWN line. NEVER use the `|` (pipe) character.\n"
            "  QUALITY: <one word — either high, medium, or low>\n"
            "  CRITIQUE: <one full sentence describing what worked or what failed>\n"
            "  LESSON: <one full sentence — actionable takeaway for next time>\n"
            "  INTEREST: <a short topic worth exploring later, or NONE>\n\n"
            "Important:\n"
            "  - QUALITY is a SINGLE WORD only (high / medium / low). Do not\n"
            "    repeat the legend on the QUALITY line.\n"
            "  - CRITIQUE is a SENTENCE, not a grade. Never write the words\n"
            "    high, medium, or low alone on the CRITIQUE line.\n"
            "  - INTEREST should be a *topic* (3-8 words), not a sentence.\n"
            "  - INTEREST = NONE for greetings / chitchat / trivial answers.\n\n"
            "Heuristics:\n"
            "  - If the reply hallucinated facts or refused unnecessarily -> low\n"
            "  - If the reply was correct but shallow -> medium\n"
            "  - If the reply was accurate, grounded, and useful -> high\n"
        )
        user_payload = (
            f"INPUT:\n{input_text.strip()[:1200]}\n\n"
            f"AGENT_REPLY:\n{response.strip()[:1500]}"
        )
        return await self._provider.generate(
            triage_system,
            [self._ChatMessage(role="user", content=user_payload)],
        )

    @staticmethod
    def _parse(raw: str) -> dict | None:
        """Tolerant parser — recovers from common SLM misformats.

        The strategy is positional: find every KEY: marker, then take each
        field's value as everything between this marker and the next one
        (or end-of-string). This works whether the SLM puts each key on
        its own line OR collapses them onto one line with `|` / `,`
        separators (a common chat-tuned model failure mode).
        """
        if not raw:
            return None

        matches = list(_KEY_RE.finditer(raw))
        if not matches:
            return None

        fields: dict[str, str] = {}
        for idx, m in enumerate(matches):
            key = m.group(1).upper()
            value_start = m.end()
            value_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(raw)
            value = raw[value_start:value_end]
            # Strip separator characters that the SLM may have used to
            # glue fields onto a single line.
            value = value.strip().rstrip(" |,;\t\r\n").strip()
            # Last-key-wins if the model emitted duplicates.
            fields[key] = value

        if not all(k in fields for k in ("QUALITY", "CRITIQUE", "LESSON")):
            return None

        # QUALITY is required to contain one of the three grade words. If
        # the model put the legend ("high | medium | low") here, we still
        # extract the first valid grade rather than dropping the entry.
        q_match = _QUALITY_VALUE_RE.search(fields["QUALITY"])
        if not q_match:
            return None
        quality = q_match.group(1).lower()

        critique = fields["CRITIQUE"].strip()
        lesson = fields["LESSON"].strip()
        # Reject pure-grade-word CRITIQUE values (like the SLM emitting
        # `CRITIQUE: medium`). Those carry no diagnostic signal and would
        # pollute downstream Sleep Metabolism consolidation.
        if not critique or _QUALITY_VALUE_RE.fullmatch(critique):
            return None
        if not lesson:
            return None

        interest = fields.get("INTEREST", "").strip(" .\"'`")
        if interest.upper() == "NONE":
            interest = ""

        # Defensive length caps.
        return {
            "quality": quality,
            "critique": critique[:300],
            "lesson": lesson[:300],
            "interest": interest[:120],
        }

    async def _persist(self, entry: ReflectionEntry) -> None:
        await self._writer.append(asdict(entry))

    async def _persist_dropped(
        self,
        *,
        kind: str,
        input_text: str,
        response: str,
        web_searched: bool,
        backend: str,
        raw: str,
        reason: str,
    ) -> None:
        """Sidecar audit log for unparseable critiques.

        Lets the operator open `state/reflection_dropped.jsonl` to see
        EXACTLY what the SLM emitted whenever a turn produced no reflection,
        without polluting the main feed (which downstream consolidation
        consumes).
        """
        record = {
            "ts": datetime.now(tz=self._tz).isoformat(),
            "kind": kind,
            "input": input_text[:500],
            "response": response[:1000],
            "web_searched": web_searched,
            "backend": backend,
            "reason": reason,
            "raw": raw[:2000],
        }
        try:
            await self._dropped_writer.append(record)
        except Exception:  # writer never raises; belt-and-braces
            log.exception("Failed to persist dropped reflection record")
