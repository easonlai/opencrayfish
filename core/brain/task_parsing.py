"""Recurring-research task intent parsing.

This module owns the SLM-backed parsers that convert natural-language
operator messages into structured ``TaskSpec`` / ``TaskUpdate`` /
``TaskAction`` records the ``TaskScheduler`` can consume:

  * ``parse_create``  \u2014 \"check the MSFT price every hour\"
                        \u2192 ``TaskSpec``
  * ``parse_modify``  \u2014 \"change t8f3a to every 2 hours\"
                        \u2192 ``TaskUpdate``
  * ``parse_action``  \u2014 \"cancel the MSFT task\"
                        \u2192 ``TaskAction``

Each parser is a TWO-STAGE pipeline by design:

  1. **Cheap regex pre-filter** (``scheduler.looks_like_task_*``). A normal
     chitchat message never pays the SLM call \u2014 we only invoke the model
     when the message contains scheduler-shaped tokens (interval words,
     action verbs, modify verbs).
  2. **Bounded SLM intent extraction**. One ``provider.generate`` call
     with a hard-formatted output contract; every parser validates EVERY
     field and degrades to ``None`` on any error or ambiguity. Failures
     are deliberately silent (the connector falls back to the next intent
     path, then to chat) \u2014 better to ignore a task than to silently
     mis-schedule one.

Determinism guards live next to the SLM call: ``extract_explicit_interval``
overrides the SLM whenever the operator's literal cadence phrase
(\"every 10 minutes\") disagrees with the parsed value, and the action
parser has a fast-path that bypasses the SLM entirely when the message
contains an explicit ``t[0-9a-f]{4}`` id plus an unambiguous verb.

``synthesize_task_report`` is intentionally NOT in this module: it
orchestrates a full ``Brain.proactive_thought`` cycle (with mood +
identity + reflection) and stays on the ``Brain`` class.
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from ..provider import ChatMessage, Provider

if TYPE_CHECKING:
    from ..scheduler import Task, TaskAction, TaskSpec, TaskUpdate

log = logging.getLogger(__name__)


class TaskIntentParser:
    """Stateless SLM-backed parser for scheduler intents.

    Holds a single dependency \u2014 the LLM provider \u2014 so it can be
    instantiated once per ``Brain`` and reused across every turn. All
    methods are coroutine-safe: there is no mutable state.
    """

    def __init__(self, *, provider: Provider) -> None:
        self._provider = provider

    # ------------------------------------------------------------------ create
    async def parse_create(self, user_input: str) -> TaskSpec | None:
        """Decide whether ``user_input`` is a recurring-task CREATE request.

        Two-stage parser:
          1. Cheap regex pre-filter (``scheduler.looks_like_task_request``).
             If the message contains no interval-words (\"every X hours\",
             \"hourly\", \"daily\", \"every minute\", \u2026) we return ``None``
             immediately \u2014 no SLM call. This means a normal chat message
             never pays the parse cost.
          2. SLM intent extraction. If the pre-filter matched, ask the
             SLM to extract topic + interval + queries in a strict format.
             We then validate every field; any failure \u2192 ``None`` (the
             connector falls back to normal ``think()``).

        Returns a ``TaskSpec`` ready for ``TaskScheduler.add_task``, or
        ``None`` when the message is not a scheduling request (or could
        not be parsed safely). Callers MUST treat ``None`` as \"fall back
        to chat\".
        """
        # Local import: scheduler imports Brain via TYPE_CHECKING; doing
        # the same here is purely cosmetic, so we just import inside
        # the function (called rarely \u2014 once per chat turn at most).
        from ..scheduler import (
            TaskSpec,
            extract_explicit_interval,
            looks_like_task_request,
            parse_interval_spec,
        )

        if not user_input or not looks_like_task_request(user_input):
            return None

        system = (
            "You are an INTENT PARSER for a personal AI agent. The operator "
            "may be requesting a RECURRING research task (something the agent "
            "should run on a schedule and report back) or just chatting.\n\n"
            "Your job: decide which, and if it IS a recurring task, extract "
            "the schedule and the search queries.\n\n"
            "Output EXACTLY one of these two forms \u2014 nothing else, no preamble:\n\n"
            "  NOT_TASK\n\n"
            "  TASK\n"
            "  TOPIC: <short label, \u2264 8 words>\n"
            "  INTERVAL: <number><unit>  (units: m / h / d / w; e.g. 1h, 30m, 1d)\n"
            "  QUERIES:\n"
            "    - <one search query per line, 2\u20135 queries total>\n\n"
            "RULES:\n"
            "  \u2022 TASK only when the operator clearly asks for something\n"
            "    REPEATING on a schedule. One-off questions are NOT_TASK.\n"
            "  \u2022 INTERVAL must use a numeric unit. 'hourly' \u2192 1h, 'daily' \u2192 1d.\n"
            "  \u2022 QUERIES are what you would type into a web search engine\n"
            "    to gather the information needed. Be specific. Include\n"
            "    company names / ticker symbols / topic keywords explicitly.\n"
            "  \u2022 If you cannot confidently extract all three fields, emit\n"
            "    NOT_TASK \u2014 the agent will then handle the message as\n"
            "    normal chat. Better to miss a task than to schedule\n"
            "    something the operator didn't want.\n\n"
            "EXAMPLES:\n\n"
            "  Operator: \"check the Microsoft stock price and news every "
            "hour and give me an insight summary\"\n"
            "  \u2192\n"
            "  TASK\n"
            "  TOPIC: Microsoft stock + news\n"
            "  INTERVAL: 1h\n"
            "  QUERIES:\n"
            "    - Microsoft MSFT stock price today\n"
            "    - Microsoft latest news\n\n"
            "  Operator: \"every morning summarise top AI research papers\"\n"
            "  \u2192\n"
            "  TASK\n"
            "  TOPIC: Daily AI research digest\n"
            "  INTERVAL: 1d\n"
            "  QUERIES:\n"
            "    - top AI research papers this week\n"
            "    - latest LLM research arxiv\n\n"
            "  Operator: \"hello, how are you doing today\"\n"
            "  \u2192 NOT_TASK\n\n"
            "  Operator: \"what is the capital of France\"\n"
            "  \u2192 NOT_TASK"
        )
        try:
            raw = await self._provider.generate(
                system,
                [ChatMessage(role="user", content=user_input.strip())],
            )
        except Exception:
            log.exception("parse_task_intent: provider call failed")
            return None
        text = (raw or "").strip()
        if not text:
            return None
        # Cheap rejection paths.
        upper_first = text.splitlines()[0].strip().upper()
        if upper_first.startswith("NOT_TASK") or "NOT_TASK" in upper_first:
            log.info("TASK parse: SLM returned NOT_TASK")
            return None
        if not upper_first.startswith("TASK"):
            log.info("TASK parse: SLM did not start with TASK or NOT_TASK \u2014 "
                     "falling back to chat. raw=%r", text[:200])
            return None

        # Field extraction. Tolerant: tokens may be in any order, may have
        # extra whitespace, queries may use various bullet markers.
        topic_m = re.search(r"^\s*TOPIC\s*:\s*(?P<v>.+)$", text, re.IGNORECASE | re.MULTILINE)
        interval_m = re.search(r"^\s*INTERVAL\s*:\s*(?P<v>.+)$", text, re.IGNORECASE | re.MULTILINE)
        queries_block_m = re.search(
            r"^\s*QUERIES\s*:\s*\n(?P<v>(?:\s*[-*\u2022]?\s*.+\n?)+)\s*$",
            text, re.IGNORECASE | re.MULTILINE,
        )
        if not (topic_m and interval_m and queries_block_m):
            log.info(
                "TASK parse: missing fields topic=%s interval=%s queries=%s",
                bool(topic_m), bool(interval_m), bool(queries_block_m),
            )
            return None
        topic = topic_m.group("v").strip().strip("\"'`")
        interval_seconds = parse_interval_spec(interval_m.group("v").strip())
        if interval_seconds is None:
            log.info("TASK parse: bad interval %r", interval_m.group("v"))
            return None
        # Deterministic override \u2014 qwen2:1.5b copies the prompt example
        # ("INTERVAL: 1h") rather than respecting the operator's literal
        # cadence ("every 10 minutes"). When the operator's text contains
        # an explicit cadence phrase, trust THAT over whatever the SLM
        # emitted. See `extract_explicit_interval` in scheduler.py for
        # the supported phrasings.
        explicit = extract_explicit_interval(user_input)
        if explicit is not None and explicit != interval_seconds:
            log.info(
                "TASK parse: SLM emitted %ds but operator said %ds \u2014 using operator's value",
                interval_seconds, explicit,
            )
            interval_seconds = explicit
        queries: list[str] = []
        for line in queries_block_m.group("v").splitlines():
            ln = line.strip()
            ln = re.sub(r"^[-*\u2022]\s*", "", ln).strip().strip("\"'`")
            if ln:
                queries.append(ln)
        # Cap to 5 \u2014 anything beyond is excess SLM cost per fire.
        queries = queries[:5]
        if not queries:
            log.info("TASK parse: no queries extracted")
            return None
        log.info(
            "TASK parse OK topic=%r interval=%ds queries=%d",
            topic, interval_seconds, len(queries),
        )
        return TaskSpec(
            topic=topic,
            queries=queries,
            interval_seconds=interval_seconds,
            description=user_input.strip(),
        )

    # ------------------------------------------------------------------ modify
    async def parse_modify(
        self,
        user_input: str,
        current_tasks: list[Task],
    ) -> TaskUpdate | None:
        """Decide whether ``user_input`` asks to MODIFY an existing task.

        Two-stage parser, mirrors ``parse_create``:
          1. Cheap regex pre-filter (``scheduler.looks_like_task_modify_request``).
             Skips the SLM call when the message has no update verbs.
          2. SLM resolves the operator's reference (\"the MSFT task\",
             \"t8f3a\", \"my hourly one\") against the live task list and
             extracts which fields are being changed.

        Returns a ``TaskUpdate`` (with task_id + only the fields that
        change) ready for ``TaskScheduler.update_task(...)``. Returns
        ``None`` when the message is not a modify request, when no live
        tasks exist, or when the SLM cannot safely resolve the target.
        Callers MUST treat ``None`` as \"fall back to the next intent
        path\".
        """
        from ..scheduler import (
            TaskUpdate,
            extract_explicit_interval,
            looks_like_task_modify_request,
            parse_interval_spec,
        )

        if not user_input or not looks_like_task_modify_request(user_input):
            return None
        if not current_tasks:
            # Nothing to modify \u2014 don't waste an SLM call. The connector
            # will fall through to chat and the operator will see a
            # "no tasks" reply via Brain.think.
            log.info("TASK modify parse: no live tasks \u2014 skipping SLM")
            return None

        # Compact task table for the prompt. Keep it short so the SLM
        # doesn't drown in context: id, topic, current interval.
        from ..scheduler import format_interval as _fmt_iv
        task_table = "\n".join(
            f"  {t.id}  |  {t.topic}  |  every {_fmt_iv(t.interval_seconds)}"
            for t in current_tasks
        )

        system = (
            "You are an INTENT PARSER for a personal AI agent. The operator "
            "may be asking to MODIFY one of their existing recurring tasks "
            "(change its cadence, topic, or search queries) or just chatting.\n\n"
            "Here are the operator's CURRENT tasks (id | topic | cadence):\n"
            f"{task_table}\n\n"
            "Your job: decide whether the operator is editing one of these, "
            "and if so, identify which task and which fields they're changing.\n\n"
            "Output EXACTLY one of these two forms \u2014 nothing else, no preamble:\n\n"
            "  NOT_MODIFY\n\n"
            "  MODIFY\n"
            "  TARGET_ID: <one of the ids above, exact match>\n"
            "  INTERVAL: <number><unit>  (omit line entirely if unchanged)\n"
            "  TOPIC: <new short label>  (omit line entirely if unchanged)\n"
            "  QUERIES:                  (omit BLOCK entirely if unchanged)\n"
            "    - <one search query per line, 2\u20135 queries>\n\n"
            "RULES:\n"
            "  \u2022 MODIFY only when the operator clearly references an EXISTING\n"
            "    task above. \"Schedule a new task to ...\" is NOT_MODIFY.\n"
            "  \u2022 TARGET_ID must be an exact id from the table. If you can\n"
            "    not unambiguously pick one, emit NOT_MODIFY.\n"
            "  \u2022 Emit ONLY the fields the operator is changing. Omitted\n"
            "    lines mean \"keep current value\".\n"
            "  \u2022 INTERVAL uses a numeric unit. 'hourly' \u2192 1h, 'daily' \u2192 1d.\n"
            "  \u2022 If unsure, emit NOT_MODIFY \u2014 better to ignore than to\n"
            "    silently rewrite the operator's task.\n\n"
            "EXAMPLES (with current tasks t8f3a=Microsoft stock + news every 1h, "
            "taa12=Daily AI digest every 1 day):\n\n"
            "  Operator: \"change task t8f3a to every 2 hours\"\n"
            "  \u2192\n"
            "  MODIFY\n"
            "  TARGET_ID: t8f3a\n"
            "  INTERVAL: 2h\n\n"
            "  Operator: \"update the Microsoft task to also track Azure news\"\n"
            "  \u2192\n"
            "  MODIFY\n"
            "  TARGET_ID: t8f3a\n"
            "  QUERIES:\n"
            "    - Microsoft MSFT stock price today\n"
            "    - Microsoft latest news\n"
            "    - Microsoft Azure news\n\n"
            "  Operator: \"rename the AI digest to Morning AI brief\"\n"
            "  \u2192\n"
            "  MODIFY\n"
            "  TARGET_ID: taa12\n"
            "  TOPIC: Morning AI brief\n\n"
            "  Operator: \"thanks!\"  \u2192 NOT_MODIFY\n"
            "  Operator: \"schedule a new task to track gold prices hourly\"  \u2192 NOT_MODIFY"
        )
        try:
            raw = await self._provider.generate(
                system,
                [ChatMessage(role="user", content=user_input.strip())],
            )
        except Exception:
            log.exception("parse_task_modify_intent: provider call failed")
            return None
        text = (raw or "").strip()
        if not text:
            return None
        upper_first = text.splitlines()[0].strip().upper()
        if upper_first.startswith("NOT_MODIFY") or "NOT_MODIFY" in upper_first:
            log.info("TASK modify parse: SLM returned NOT_MODIFY")
            return None
        if not upper_first.startswith("MODIFY"):
            log.info("TASK modify parse: SLM did not start with MODIFY/NOT_MODIFY \u2014 "
                     "falling back. raw=%r", text[:200])
            return None

        # Tolerant field extraction \u2014 same approach as parse_create.
        target_m = re.search(r"^\s*TARGET_ID\s*:\s*(?P<v>\S+)\s*$",
                             text, re.IGNORECASE | re.MULTILINE)
        if not target_m:
            log.info("TASK modify parse: missing TARGET_ID")
            return None
        target_id = target_m.group("v").strip().strip("\"'`")
        # Reject ids the SLM hallucinated.
        valid_ids = {t.id for t in current_tasks}
        if target_id not in valid_ids:
            log.info("TASK modify parse: SLM returned unknown id %r (have %s)",
                     target_id, sorted(valid_ids))
            return None

        new_topic: str | None = None
        new_interval_seconds: int | None = None
        new_queries: list[str] | None = None

        topic_m = re.search(r"^\s*TOPIC\s*:\s*(?P<v>.+)$",
                            text, re.IGNORECASE | re.MULTILINE)
        if topic_m:
            cand = topic_m.group("v").strip().strip("\"'`")
            if cand:
                new_topic = cand

        interval_m = re.search(r"^\s*INTERVAL\s*:\s*(?P<v>.+)$",
                               text, re.IGNORECASE | re.MULTILINE)
        if interval_m:
            secs = parse_interval_spec(interval_m.group("v").strip())
            if secs is None:
                log.info("TASK modify parse: bad interval %r", interval_m.group("v"))
                return None
            new_interval_seconds = secs
        # Deterministic override \u2014 same rationale as parse_create.
        # If the operator's modify request contains an explicit cadence
        # phrase ("change t8f3a to every 10 minutes"), trust THAT over the
        # SLM, even when the SLM omitted INTERVAL entirely.
        explicit = extract_explicit_interval(user_input)
        if explicit is not None and explicit != new_interval_seconds:
            if new_interval_seconds is not None:
                log.info(
                    "TASK modify parse: SLM emitted %ds but operator said %ds \u2014 using operator's value",
                    new_interval_seconds, explicit,
                )
            else:
                log.info(
                    "TASK modify parse: SLM omitted INTERVAL but operator said %ds \u2014 applying it",
                    explicit,
                )
            new_interval_seconds = explicit

        queries_block_m = re.search(
            r"^\s*QUERIES\s*:\s*\n(?P<v>(?:\s*[-*\u2022]?\s*.+\n?)+)\s*$",
            text, re.IGNORECASE | re.MULTILINE,
        )
        if queries_block_m:
            qs: list[str] = []
            for line in queries_block_m.group("v").splitlines():
                ln = line.strip()
                ln = re.sub(r"^[-*\u2022]\s*", "", ln).strip().strip("\"'`")
                if ln:
                    qs.append(ln)
            qs = qs[:5]
            if qs:
                new_queries = qs

        if new_topic is None and new_interval_seconds is None and new_queries is None:
            # SLM identified a target but extracted no actual changes \u2014
            # treat as a no-op rather than triggering a registry write.
            log.info("TASK modify parse: no changed fields for id=%s", target_id)
            return None
        log.info(
            "TASK modify parse OK id=%s topic=%s interval=%s queries=%s",
            target_id,
            "yes" if new_topic else "no",
            f"{new_interval_seconds}s" if new_interval_seconds else "no",
            len(new_queries) if new_queries else "no",
        )
        return TaskUpdate(
            task_id=target_id,
            new_topic=new_topic,
            new_interval_seconds=new_interval_seconds,
            new_queries=new_queries,
            new_description=user_input.strip(),
        )

    # ------------------------------------------------------------------ action
    async def parse_action(
        self,
        user_input: str,
        current_tasks: list[Task],
    ) -> TaskAction | None:
        """Decide whether ``user_input`` asks to CANCEL / PAUSE / RESUME a task.

        Two-stage parser, mirrors ``parse_modify``:
          1. Cheap regex pre-filter (``scheduler.looks_like_task_action_request``).
             Skips the SLM call when the message has no action verbs.
          2. SLM resolves which task the operator means (\"the MSFT one\",
             \"t8f3a\", \"my hourly task\") against the live task list and
             picks one of the three canonical actions.

        Fast-path before the SLM: when the operator's message contains
        an explicit task id token (``t[0-9a-f]{4}``) AND the action verb
        is unambiguous, dispatch directly. This makes \"/cancel-style\"
        NL phrases (\"cancel t091f\", \"pause t091f\") deterministic and
        zero-cost.

        Returns a ``TaskAction`` (with task_id + action) ready for
        ``TaskScheduler.cancel_task`` / ``TaskScheduler.pause_task``.
        Returns ``None`` when the message is not an action request, when
        no live tasks exist, or when the SLM cannot safely resolve the
        target. Callers MUST treat ``None`` as \"fall back to the next
        intent path\".
        """
        from ..scheduler import TaskAction, looks_like_task_action_request

        if not user_input or not looks_like_task_action_request(user_input):
            return None
        if not current_tasks:
            log.info("TASK action parse: no live tasks \u2014 skipping SLM")
            return None

        valid_ids = {t.id for t in current_tasks}

        # Fast-path: explicit id + unambiguous action verb. Skips the SLM
        # entirely. We classify the verb by which group matches first in
        # a tight regex; if the message somehow contains BOTH a cancel
        # AND a pause verb we fall through to the SLM for arbitration.
        text_lc = user_input.lower()
        id_m = re.search(r"\bt[0-9a-f]{4}\b", text_lc)
        if id_m and id_m.group(0) in valid_ids:
            cancel_hit = bool(re.search(
                r"\b(cancel|stop|delete|remove|kill|abort|terminate|end)\b",
                text_lc,
            ))
            pause_hit = bool(re.search(
                r"\b(pause|suspend|halt|freeze)\b", text_lc,
            ))
            resume_hit = bool(re.search(
                r"\b(resume|unpause|restart|continue|reactivate|re-?enable)\b",
                text_lc,
            ))
            hits = sum([cancel_hit, pause_hit, resume_hit])
            if hits == 1:
                action = (
                    "cancel" if cancel_hit
                    else ("pause" if pause_hit else "resume")
                )
                log.info(
                    "TASK action parse OK (fast-path) id=%s action=%s",
                    id_m.group(0), action,
                )
                return TaskAction(task_id=id_m.group(0), action=action)

        # Compact task table for the prompt \u2014 same shape as modify path.
        from ..scheduler import format_interval as _fmt_iv
        task_table = "\n".join(
            f"  {t.id}  |  {t.topic}  |  every {_fmt_iv(t.interval_seconds)}"
            f"  |  {'paused' if t.paused else 'active'}"
            for t in current_tasks
        )

        system = (
            "You are an INTENT PARSER for a personal AI agent. The operator "
            "may be asking to CANCEL, PAUSE, or RESUME one of their existing "
            "recurring tasks, or just chatting.\n\n"
            "Here are the operator's CURRENT tasks (id | topic | cadence | state):\n"
            f"{task_table}\n\n"
            "Your job: decide whether the operator is asking for one of the "
            "three actions, identify which task, and which action.\n\n"
            "Output EXACTLY one of these two forms \u2014 nothing else, no preamble:\n\n"
            "  NOT_ACTION\n\n"
            "  ACTION\n"
            "  TARGET_ID: <one of the ids above, exact match>\n"
            "  VERB: <one of: cancel, pause, resume>\n\n"
            "RULES:\n"
            "  \u2022 ACTION only when the operator clearly references an EXISTING\n"
            "    task above AND uses an action verb. Modify requests like\n"
            "    'change the cadence' are NOT_ACTION.\n"
            "  \u2022 TARGET_ID must be an exact id from the table. If you can\n"
            "    not unambiguously pick one, emit NOT_ACTION.\n"
            "  \u2022 VERB must be exactly one of: cancel, pause, resume.\n"
            "    Map operator synonyms:\n"
            "      cancel \u2190 stop, delete, remove, kill, abort, terminate, end\n"
            "      pause  \u2190 pause, suspend, halt, freeze\n"
            "      resume \u2190 resume, unpause, restart, continue, reactivate\n"
            "  \u2022 If unsure, emit NOT_ACTION \u2014 better to ignore than to\n"
            "    silently delete the operator's task.\n\n"
            "EXAMPLES (with current tasks t8f3a=Microsoft stock + news every 1h "
            "active, taa12=Daily AI digest every 1 day paused):\n\n"
            "  Operator: \"cancel the MSFT task\"\n"
            "  \u2192\n"
            "  ACTION\n"
            "  TARGET_ID: t8f3a\n"
            "  VERB: cancel\n\n"
            "  Operator: \"pause my hourly one\"\n"
            "  \u2192\n"
            "  ACTION\n"
            "  TARGET_ID: t8f3a\n"
            "  VERB: pause\n\n"
            "  Operator: \"resume the AI digest\"\n"
            "  \u2192\n"
            "  ACTION\n"
            "  TARGET_ID: taa12\n"
            "  VERB: resume\n\n"
            "  Operator: \"thanks\"  \u2192 NOT_ACTION\n"
            "  Operator: \"change t8f3a to every 2 hours\"  \u2192 NOT_ACTION"
        )
        try:
            raw = await self._provider.generate(
                system,
                [ChatMessage(role="user", content=user_input.strip())],
            )
        except Exception:
            log.exception("parse_task_action_intent: provider call failed")
            return None
        text = (raw or "").strip()
        if not text:
            return None
        upper_first = text.splitlines()[0].strip().upper()
        if upper_first.startswith("NOT_ACTION") or "NOT_ACTION" in upper_first:
            log.info("TASK action parse: SLM returned NOT_ACTION")
            return None
        if not upper_first.startswith("ACTION"):
            log.info(
                "TASK action parse: SLM did not start with ACTION/NOT_ACTION \u2014 "
                "falling back. raw=%r", text[:200],
            )
            return None

        target_m = re.search(
            r"^\s*TARGET_ID\s*:\s*(?P<v>\S+)\s*$",
            text, re.IGNORECASE | re.MULTILINE,
        )
        verb_m = re.search(
            r"^\s*VERB\s*:\s*(?P<v>\S+)\s*$",
            text, re.IGNORECASE | re.MULTILINE,
        )
        if not (target_m and verb_m):
            log.info(
                "TASK action parse: missing fields target=%s verb=%s",
                bool(target_m), bool(verb_m),
            )
            return None
        target_id = target_m.group("v").strip().strip("\"'`")
        if target_id not in valid_ids:
            log.info(
                "TASK action parse: SLM returned unknown id %r (have %s)",
                target_id, sorted(valid_ids),
            )
            return None
        verb = verb_m.group("v").strip().strip("\"'`").lower()
        if verb not in {"cancel", "pause", "resume"}:
            log.info("TASK action parse: SLM returned invalid verb %r", verb)
            return None
        log.info("TASK action parse OK id=%s action=%s", target_id, verb)
        return TaskAction(task_id=target_id, action=verb)
