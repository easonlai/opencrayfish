"""core.intent_router — single source of truth for the task-intent pipeline.

Background (v2.0 / P1.2):
    ``connectors.telegram`` and ``connectors.web_chat`` historically each
    contained ~150 lines of nearly-identical task-routing code: list /
    action / modify / create classification, scheduler dispatch, and
    confirmation rendering. The two copies drifted twice in v1 (different
    bullet phrasing, different "first report incoming" wording). This
    module is the deduplicated brain of that pipeline — it classifies the
    incoming message, calls the right ``Brain`` parsers + ``TaskScheduler``
    operations, and returns a structured outcome. The connector then
    renders that outcome to its own channel-specific format (Telegram
    emoji + Markdown vs web JSON + Markdown).

Pipeline order (cheapest filter wins, identical to the v1 connectors):
    1. ``list``    ("show me my tasks")            — no SLM call
    2. ``action``  ("cancel / pause / resume X")   — SLM only on action verbs
    3. ``modify``  ("change t8f3a to every 2h")    — SLM only on update verbs
    4. ``create``  ("check news every hour")       — SLM only on interval words

When the scheduler is None (feature disabled) OR no path matches, the
router returns ``IntentOutcome(kind="noop")`` so the caller falls
through to normal chat (``Brain.think``).

Design rules (intentional):
    * The router NEVER renders user-facing strings — only executes the
      pipeline and reports what happened. Connectors own their wording
      so Telegram can keep its emoji palette and web_chat can keep its
      Markdown-bold + REST-endpoint hint.
    * The router NEVER touches STM. Connectors own STM appends because
      web_chat sometimes wraps a reply in JSON metadata (mood snapshot,
      timing) that the router can't know about.
    * The router NEVER calls ``Brain.think``. The caller does that as a
      final fall-through when ``outcome.handled is False``.

Smoke contract: any change here must preserve the pre-refactor connector
behaviour exactly — the same 6 smoke scripts (skill_menu / cognition_dispatch
/ rotation_reflection_identity / dashboard / soul_atomic / foreground)
must still pass.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from .brain.orchestrator import Brain
    from .scheduler import Task, TaskScheduler

log = logging.getLogger(__name__)

IntentKind = Literal["list", "action", "modify", "create", "noop"]
ActionVerb = Literal["cancel", "pause", "resume"]


@dataclass
class ListResult:
    """Live snapshot of all scheduled tasks (all origins).

    The router returns ALL tasks regardless of origin because reports
    broadcast to every connected channel — the operator naturally wants
    the unified view, not just the ones they happened to create from
    this channel.
    """
    tasks: list[Task]


@dataclass
class ActionResult:
    """Result of cancel / pause / resume on a single task."""
    action: ActionVerb
    task_id: str
    # For ``cancel``: ``removed`` is the task that was deleted (None when
    # ``task_id`` was unknown). ``task`` is always None for cancel.
    # For ``pause`` / ``resume``: ``task`` is the resulting task (with
    # the new ``paused`` flag); None when ``task_id`` was unknown.
    # ``removed`` is None.
    task: Task | None
    removed: Task | None = None
    not_found: bool = False  # convenience flag: True iff the id was unknown


@dataclass
class UpdateResult:
    """Result of a MODIFY operation on a single task.

    ``error`` is populated when the scheduler raised ``ValueError`` (the
    only failure mode of ``update_task``); connectors render it directly
    to the operator. ``not_found`` is True when the task id was unknown.
    The three ``changed_*`` flags let connectors render only the fields
    the operator actually asked to change (mirrors v1 phrasing).
    """
    task_id: str
    task: Task | None
    error: str | None = None
    not_found: bool = False
    changed_topic: bool = False
    changed_interval: bool = False
    changed_queries: bool = False


@dataclass
class CreateResult:
    """Result of scheduling a brand-new recurring task.

    ``error`` is populated when ``add_task`` raised ``ValueError`` (cap
    exceeded, duplicate, etc.); connectors render it directly.
    """
    task: Task | None
    error: str | None = None


@dataclass
class IntentOutcome:
    """Structured result handed back to the connector after routing.

    Exactly one of ``list_result`` / ``action_result`` / ``update_result``
    / ``create_result`` is populated, determined by ``kind``. When
    ``kind == "noop"`` ALL of them are None and the connector falls
    through to ``Brain.think``.
    """
    kind: IntentKind
    list_result: ListResult | None = None
    action_result: ActionResult | None = None
    update_result: UpdateResult | None = None
    create_result: CreateResult | None = None

    @property
    def handled(self) -> bool:
        """True iff the router fully handled the message (caller should
        skip the chat fall-through). ``noop`` is the only un-handled kind.
        """
        return self.kind != "noop"


class IntentRouter:
    """Channel-agnostic task-intent classifier + dispatcher.

    Construct one per connector (the connector owns its ``origin`` tag
    so the scheduler can route deliveries correctly). The single public
    method ``route(user_input)`` runs the 4-stage pipeline and returns
    an ``IntentOutcome``. The router is stateless beyond its constructor
    deps, so it's safe to share across concurrent ``_on_message`` calls.
    """

    def __init__(
        self,
        *,
        brain: Brain,
        scheduler: TaskScheduler | None,
        origin: str,
    ) -> None:
        self._brain = brain
        self._scheduler = scheduler
        self._origin = origin

    async def route(self, user_input: str) -> IntentOutcome:
        """Classify ``user_input`` against the 4-stage task pipeline.

        Returns ``IntentOutcome(kind="noop")`` immediately when the
        scheduler is disabled or the message is empty — the caller then
        falls through to chat. Otherwise runs the pipeline in
        cheapest-filter-first order and returns the first match.
        """
        if self._scheduler is None or not user_input:
            # Make the bypass observable — silent noop here used to be
            # a debugging headache ("why doesn't /tasks parsing fire?").
            log.debug(
                "IntentRouter noop: scheduler=%s input_len=%d origin=%s",
                "enabled" if self._scheduler is not None else "disabled",
                len(user_input or ""),
                self._origin,
            )
            return IntentOutcome(kind="noop")

        # Local imports — scheduler is a leaf module with no Brain deps;
        # importing at module top would still work but matches the v1
        # connector style (deferred import inside the handler).
        from .scheduler import (
            looks_like_task_action_request,
            looks_like_task_query,
        )

        # ---- Stage 1: LIST ("show me my tasks") --------------------------
        # Cheapest filter — pure regex, no SLM, no scheduler write.
        if looks_like_task_query(user_input):
            tasks = await self._scheduler.list_tasks()
            return IntentOutcome(
                kind="list",
                list_result=ListResult(tasks=tasks),
            )

        # Single live read shared across the action + modify SLM calls so
        # we don't pay two list_tasks() hits in the common case.
        current_tasks = await self._scheduler.list_tasks()

        # ---- Stage 2: ACTION (cancel / pause / resume) -------------------
        # Action precedes MODIFY because the verb classes are disjoint
        # but action's pre-filter is tighter (requires both a task noun
        # AND a cancel/pause/resume verb).
        if looks_like_task_action_request(user_input):
            action_req = await self._brain.parse_task_action_intent(
                user_input, current_tasks,
            )
            if action_req is not None:
                return await self._execute_action(action_req)

        # ---- Stage 3: MODIFY ("change t8f3a to every 2h") ----------------
        # Modify precedes CREATE because a modify request usually contains
        # an interval phrase ("every 2 hours") that would otherwise look
        # like a fresh schedule.
        update_req = await self._brain.parse_task_modify_intent(
            user_input, current_tasks,
        )
        if update_req is not None:
            return await self._execute_modify(update_req)

        # ---- Stage 4: CREATE ("check news every hour") -------------------
        spec = await self._brain.parse_task_intent(user_input)
        if spec is not None:
            return await self._execute_create(spec)

        return IntentOutcome(kind="noop")

    # ---------- private executors ----------------------------------------

    async def _execute_action(self, action_req) -> IntentOutcome:
        """Run cancel / pause / resume against the scheduler."""
        assert self._scheduler is not None  # guarded in route()
        if action_req.action == "cancel":
            removed = await self._scheduler.cancel_task(action_req.task_id)
            return IntentOutcome(
                kind="action",
                action_result=ActionResult(
                    action="cancel",
                    task_id=action_req.task_id,
                    task=None,
                    removed=removed,
                    not_found=removed is None,
                ),
            )
        # pause / resume share the same scheduler call signature.
        paused = action_req.action == "pause"
        task = await self._scheduler.pause_task(
            action_req.task_id, paused=paused,
        )
        return IntentOutcome(
            kind="action",
            action_result=ActionResult(
                action=action_req.action,  # "pause" | "resume"
                task_id=action_req.task_id,
                task=task,
                not_found=task is None,
            ),
        )

    async def _execute_modify(self, update_req) -> IntentOutcome:
        """Apply a MODIFY request against the scheduler."""
        assert self._scheduler is not None
        try:
            task = await self._scheduler.update_task(
                update_req.task_id,
                topic=update_req.new_topic,
                interval_seconds=update_req.new_interval_seconds,
                queries=update_req.new_queries,
                description=update_req.new_description,
            )
        except ValueError as exc:
            return IntentOutcome(
                kind="modify",
                update_result=UpdateResult(
                    task_id=update_req.task_id,
                    task=None,
                    error=f"Could not update that task: {exc}",
                ),
            )
        return IntentOutcome(
            kind="modify",
            update_result=UpdateResult(
                task_id=update_req.task_id,
                task=task,
                not_found=task is None,
                changed_topic=update_req.new_topic is not None,
                changed_interval=update_req.new_interval_seconds is not None,
                changed_queries=update_req.new_queries is not None,
            ),
        )

    async def _execute_create(self, spec) -> IntentOutcome:
        """Persist a brand-new scheduled task."""
        assert self._scheduler is not None
        try:
            task = await self._scheduler.add_task(spec, origin=self._origin)
        except ValueError as exc:
            return IntentOutcome(
                kind="create",
                create_result=CreateResult(
                    task=None,
                    error=f"Could not schedule that task: {exc}",
                ),
            )
        return IntentOutcome(
            kind="create",
            create_result=CreateResult(task=task),
        )
