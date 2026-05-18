"""tests.test_intent_router — unit tests for ``core.intent_router.IntentRouter``.

The IntentRouter is the deduplicated brain of the task-intent pipeline
that historically lived twice (Telegram + web_chat) and drifted between
versions. These tests pin down its 4-stage classification contract
WITHOUT depending on a real ``Brain`` or ``TaskScheduler`` — both are
replaced with minimal stubs so the tests are deterministic and fast.

Coverage map:
  * Stage 0 (guard)  — ``scheduler is None`` or empty input → noop
  * Stage 1 (list)   — "show me my tasks" → ListResult
  * Stage 2 (action) — "cancel/pause/resume X" → ActionResult
                       (cancel + pause + resume + not_found + error)
  * Stage 3 (modify) — "change t8f3a to every 2h" → UpdateResult
                       (success + scheduler ValueError → error)
  * Stage 4 (create) — "check news every hour" → CreateResult
                       (success + scheduler ValueError → error)
  * Outcome property — ``handled`` is False only for noop
  * Order            — list pre-empts everything else
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from core.intent_router import IntentRouter
from core.scheduler import Task, TaskAction, TaskSpec, TaskUpdate

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

def _make_task(
    *,
    task_id: str = "t8f3a",
    topic: str = "Microsoft stock + news",
    interval_seconds: int = 3600,
    paused: bool = False,
) -> Task:
    """Build a Task with realistic placeholder fields.

    Same helper shape as test_task_parsing.py — kept duplicated rather
    than shared because the two test modules cover different layers
    and the indirection isn't worth the import dependency.
    """
    return Task(
        id=task_id,
        topic=topic,
        queries=["q1", "q2"],
        interval_seconds=interval_seconds,
        description="placeholder",
        origin="test",
        created_at="2025-01-01T00:00:00",
        next_run_at="2025-01-01T01:00:00",
        paused=paused,
    )


@dataclass
class _StubBrain:
    """Records the four parser methods used by ``IntentRouter``.

    Each attribute holds the canned return value of the corresponding
    Brain method. ``ValueError`` is allowed as a sentinel — tests can
    set ``parse_task_modify_intent_returns = ValueError("boom")`` to
    simulate a parser blowing up (though the real Brain swallows
    exceptions, so this stays unused; we keep the seam available).
    """
    parse_task_intent_returns: TaskSpec | None = None
    parse_task_modify_intent_returns: TaskUpdate | None = None
    parse_task_action_intent_returns: TaskAction | None = None
    # Recorded call args for assertions.
    create_calls: list[str] = field(default_factory=list)
    modify_calls: list[tuple[str, list[Task]]] = field(default_factory=list)
    action_calls: list[tuple[str, list[Task]]] = field(default_factory=list)

    async def parse_task_intent(self, user_input: str) -> TaskSpec | None:
        self.create_calls.append(user_input)
        return self.parse_task_intent_returns

    async def parse_task_modify_intent(
        self, user_input: str, tasks: list[Task],
    ) -> TaskUpdate | None:
        self.modify_calls.append((user_input, tasks))
        return self.parse_task_modify_intent_returns

    async def parse_task_action_intent(
        self, user_input: str, tasks: list[Task],
    ) -> TaskAction | None:
        self.action_calls.append((user_input, tasks))
        return self.parse_task_action_intent_returns


@dataclass
class _StubScheduler:
    """In-memory scheduler that records every mutating call.

    Only the methods ``IntentRouter`` actually calls are implemented:
    ``list_tasks``, ``add_task``, ``update_task``, ``cancel_task``,
    ``pause_task``. Each can be told to raise ValueError or return
    None (not-found path) by tweaking the ``*_returns`` / ``*_raises``
    attributes — keeps the test bodies short.
    """
    tasks: list[Task] = field(default_factory=list)
    # Override behaviour:
    add_task_raises: Exception | None = None
    add_task_returns: Task | None = None
    update_task_raises: Exception | None = None
    update_task_returns: Task | None = None
    cancel_task_returns: Task | None = None
    pause_task_returns: Task | None = None
    # Recorded calls (for assertion).
    add_task_calls: list[tuple[TaskSpec, str]] = field(default_factory=list)
    update_task_calls: list[dict] = field(default_factory=list)
    cancel_task_calls: list[str] = field(default_factory=list)
    pause_task_calls: list[tuple[str, bool]] = field(default_factory=list)

    async def list_tasks(self) -> list[Task]:
        return list(self.tasks)

    async def add_task(self, spec: TaskSpec, *, origin: str) -> Task:
        self.add_task_calls.append((spec, origin))
        if self.add_task_raises is not None:
            raise self.add_task_raises
        return self.add_task_returns  # type: ignore[return-value]

    async def update_task(
        self,
        task_id: str,
        *,
        topic=None,
        interval_seconds=None,
        queries=None,
        description=None,
    ) -> Task | None:
        self.update_task_calls.append({
            "task_id": task_id, "topic": topic,
            "interval_seconds": interval_seconds,
            "queries": queries, "description": description,
        })
        if self.update_task_raises is not None:
            raise self.update_task_raises
        return self.update_task_returns

    async def cancel_task(self, task_id: str) -> Task | None:
        self.cancel_task_calls.append(task_id)
        return self.cancel_task_returns

    async def pause_task(self, task_id: str, *, paused: bool) -> Task | None:
        self.pause_task_calls.append((task_id, paused))
        return self.pause_task_returns


# ---------------------------------------------------------------------------
# Stage 0 — guards
# ---------------------------------------------------------------------------

async def test_route_returns_noop_when_scheduler_is_none():
    """Scheduler disabled → noop immediately, no Brain calls."""
    brain = _StubBrain()
    router = IntentRouter(brain=brain, scheduler=None, origin="test")

    outcome = await router.route("anything")

    assert outcome.kind == "noop"
    assert outcome.handled is False
    assert brain.create_calls == []
    assert brain.modify_calls == []
    assert brain.action_calls == []


async def test_route_returns_noop_on_empty_input():
    brain = _StubBrain()
    sched = _StubScheduler()
    router = IntentRouter(brain=brain, scheduler=sched, origin="test")

    outcome = await router.route("")

    assert outcome.kind == "noop"
    assert outcome.handled is False


# ---------------------------------------------------------------------------
# Stage 1 — LIST
# ---------------------------------------------------------------------------

async def test_route_list_returns_all_tasks_no_brain_calls():
    """"show me my tasks" → list outcome, NEVER consults Brain.

    List is the cheapest stage — pure regex, no SLM. We assert ZERO
    Brain calls to lock in the perf contract.
    """
    brain = _StubBrain()
    t1 = _make_task(task_id="t8f3a")
    t2 = _make_task(task_id="taa12", topic="AI digest", interval_seconds=86400)
    sched = _StubScheduler(tasks=[t1, t2])
    router = IntentRouter(brain=brain, scheduler=sched, origin="test")

    outcome = await router.route("show me my tasks")

    assert outcome.kind == "list"
    assert outcome.handled is True
    assert outcome.list_result is not None
    assert outcome.list_result.tasks == [t1, t2]
    # No SLM intent calls at all.
    assert brain.create_calls == []
    assert brain.modify_calls == []
    assert brain.action_calls == []


# ---------------------------------------------------------------------------
# Stage 2 — ACTION
# ---------------------------------------------------------------------------

async def test_route_action_cancel_success():
    """cancel → ActionResult.removed populated, ``task`` is None."""
    task = _make_task(task_id="t8f3a")
    brain = _StubBrain(parse_task_action_intent_returns=TaskAction(
        task_id="t8f3a", action="cancel",
    ))
    sched = _StubScheduler(tasks=[task], cancel_task_returns=task)
    router = IntentRouter(brain=brain, scheduler=sched, origin="test")

    outcome = await router.route("cancel t8f3a")

    assert outcome.kind == "action"
    assert outcome.action_result is not None
    assert outcome.action_result.action == "cancel"
    assert outcome.action_result.task_id == "t8f3a"
    assert outcome.action_result.removed == task
    assert outcome.action_result.task is None
    assert outcome.action_result.not_found is False
    assert sched.cancel_task_calls == ["t8f3a"]


async def test_route_action_cancel_not_found():
    """cancel for unknown id → ``not_found=True``, ``removed`` is None.

    Input uses ``cancel my task`` (matches the noun-based pre-filter) —
    a literal ``cancel tBOGUS`` would not match the action regex because
    the id pattern requires lowercase hex (``[0-9a-f]{4}``).
    """
    brain = _StubBrain(parse_task_action_intent_returns=TaskAction(
        task_id="tBOGUS", action="cancel",
    ))
    sched = _StubScheduler(tasks=[_make_task()], cancel_task_returns=None)
    router = IntentRouter(brain=brain, scheduler=sched, origin="test")

    outcome = await router.route("cancel my task")

    assert outcome.kind == "action"
    assert outcome.action_result.not_found is True
    assert outcome.action_result.removed is None


async def test_route_action_pause_success():
    task = _make_task(task_id="t8f3a", paused=True)
    brain = _StubBrain(parse_task_action_intent_returns=TaskAction(
        task_id="t8f3a", action="pause",
    ))
    sched = _StubScheduler(tasks=[task], pause_task_returns=task)
    router = IntentRouter(brain=brain, scheduler=sched, origin="test")

    outcome = await router.route("pause t8f3a")

    assert outcome.kind == "action"
    assert outcome.action_result.action == "pause"
    assert outcome.action_result.task == task
    assert sched.pause_task_calls == [("t8f3a", True)]


async def test_route_action_resume_success():
    """resume → pause_task called with paused=False."""
    task = _make_task(task_id="t8f3a", paused=False)
    brain = _StubBrain(parse_task_action_intent_returns=TaskAction(
        task_id="t8f3a", action="resume",
    ))
    sched = _StubScheduler(tasks=[task], pause_task_returns=task)
    router = IntentRouter(brain=brain, scheduler=sched, origin="test")

    outcome = await router.route("resume t8f3a")

    assert outcome.kind == "action"
    assert outcome.action_result.action == "resume"
    assert sched.pause_task_calls == [("t8f3a", False)]


# ---------------------------------------------------------------------------
# Stage 3 — MODIFY
# ---------------------------------------------------------------------------

async def test_route_modify_success():
    """Modify → UpdateResult populated with changed_* flags reflecting
    only the fields the operator asked to change.
    """
    task = _make_task(task_id="t8f3a", interval_seconds=7200)
    brain = _StubBrain(parse_task_modify_intent_returns=TaskUpdate(
        task_id="t8f3a", new_interval_seconds=7200,
    ))
    sched = _StubScheduler(tasks=[task], update_task_returns=task)
    router = IntentRouter(brain=brain, scheduler=sched, origin="test")

    outcome = await router.route("change t8f3a to every 2 hours")

    assert outcome.kind == "modify"
    assert outcome.update_result is not None
    assert outcome.update_result.task == task
    assert outcome.update_result.changed_interval is True
    assert outcome.update_result.changed_topic is False
    assert outcome.update_result.changed_queries is False
    assert outcome.update_result.not_found is False
    assert outcome.update_result.error is None


async def test_route_modify_scheduler_value_error_returns_error_string():
    """update_task raised ValueError → ``error`` populated, task=None."""
    brain = _StubBrain(parse_task_modify_intent_returns=TaskUpdate(
        task_id="t8f3a", new_interval_seconds=7200,
    ))
    sched = _StubScheduler(
        tasks=[_make_task()],
        update_task_raises=ValueError("interval too short"),
    )
    router = IntentRouter(brain=brain, scheduler=sched, origin="test")

    outcome = await router.route("change t8f3a to every 2 hours")

    assert outcome.kind == "modify"
    assert outcome.update_result.task is None
    assert outcome.update_result.error == "Could not update that task: interval too short"


# ---------------------------------------------------------------------------
# Stage 4 — CREATE
# ---------------------------------------------------------------------------

async def test_route_create_success_uses_router_origin():
    """Create → CreateResult populated; scheduler called with router's origin."""
    spec = TaskSpec(
        topic="Microsoft stock + news",
        queries=["MSFT price", "MSFT news"],
        interval_seconds=3600,
        description="check MSFT every hour",
    )
    task = _make_task(topic=spec.topic)
    brain = _StubBrain(parse_task_intent_returns=spec)
    sched = _StubScheduler(add_task_returns=task)
    router = IntentRouter(brain=brain, scheduler=sched, origin="web_chat")

    outcome = await router.route("check MSFT every hour")

    assert outcome.kind == "create"
    assert outcome.create_result is not None
    assert outcome.create_result.task == task
    assert outcome.create_result.error is None
    # Router MUST forward its configured origin to the scheduler.
    assert sched.add_task_calls == [(spec, "web_chat")]


async def test_route_create_scheduler_value_error_returns_error_string():
    spec = TaskSpec(
        topic="t",
        queries=["q"],
        interval_seconds=3600,
        description="d",
    )
    brain = _StubBrain(parse_task_intent_returns=spec)
    sched = _StubScheduler(add_task_raises=ValueError("cap exceeded"))
    router = IntentRouter(brain=brain, scheduler=sched, origin="test")

    outcome = await router.route("check news every hour")

    assert outcome.kind == "create"
    assert outcome.create_result.task is None
    assert outcome.create_result.error == "Could not schedule that task: cap exceeded"


# ---------------------------------------------------------------------------
# Order + noop
# ---------------------------------------------------------------------------

async def test_route_list_pre_empts_other_stages():
    """A LIST-shaped message must short-circuit before any Brain parser
    runs — even if the message also contains words that would match
    other stages (insurance against the regex helpers fighting each
    other after a future tweak).
    """
    brain = _StubBrain(
        parse_task_intent_returns=TaskSpec(
            topic="x", queries=["q"], interval_seconds=3600, description="d",
        ),
    )
    sched = _StubScheduler(tasks=[_make_task()])
    router = IntentRouter(brain=brain, scheduler=sched, origin="test")

    outcome = await router.route("list my tasks")

    assert outcome.kind == "list"
    assert brain.create_calls == []
    assert brain.modify_calls == []
    assert brain.action_calls == []


async def test_route_noop_when_no_path_matches_falls_through():
    """Chat message that doesn't match any stage → noop.

    The Brain parsers all return None (their default for chit-chat).
    Modify + create parsers ARE consulted (after the cheap pre-filters
    inside them fail), so the router must still return noop without
    confusing the connector.
    """
    brain = _StubBrain()  # all parsers return None
    sched = _StubScheduler()
    router = IntentRouter(brain=brain, scheduler=sched, origin="test")

    outcome = await router.route("hello, how are you")

    assert outcome.kind == "noop"
    assert outcome.handled is False
    assert outcome.list_result is None
    assert outcome.action_result is None
    assert outcome.update_result is None
    assert outcome.create_result is None


# ---------------------------------------------------------------------------
# IntentOutcome.handled — quick property test
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind,expected", [
    ("list", True),
    ("action", True),
    ("modify", True),
    ("create", True),
    ("noop", False),
])
def test_intent_outcome_handled_property(kind, expected):
    from core.intent_router import IntentOutcome
    assert IntentOutcome(kind=kind).handled is expected
