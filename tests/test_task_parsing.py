"""tests.test_task_parsing — unit tests for ``TaskIntentParser``.

``TaskIntentParser`` is the SLM-backed bridge between operator natural
language and the structured ``TaskSpec`` / ``TaskUpdate`` / ``TaskAction``
records the scheduler consumes. These tests pin down its TWO-STAGE
contract:

  * Stage 1: cheap regex pre-filter rejects chit-chat WITHOUT an SLM call.
    We assert the parser returns ``None`` AND records ZERO calls on the
    stub provider for messages that miss the pre-filter.

  * Stage 2: SLM call happens only when the pre-filter matches; the
    parser validates every field and returns ``None`` on any error or
    ambiguity. Tests pin specific behaviours that have caused real bugs:

      - Operator's literal cadence ("every 10 minutes") OVERRIDES the
        SLM's parroted example ("INTERVAL: 1h").
      - parse_modify returns None immediately when no tasks exist
        (NO SLM call wasted).
      - parse_action has a deterministic fast-path for explicit
        ``t[0-9a-f]{4}`` ids + unambiguous verbs (NO SLM call).

The ``stub_provider`` fixture (see conftest.py) returns a class so each
test can construct its own queued-reply provider inline.
"""
from __future__ import annotations

from core.brain.task_parsing import TaskIntentParser
from core.scheduler import Task

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_task(
    *,
    task_id: str = "t8f3a",
    topic: str = "Microsoft stock + news",
    interval_seconds: int = 3600,
    paused: bool = False,
) -> Task:
    """Build a Task with realistic placeholder fields.

    Tests only ever care about ``id``, ``topic``, ``interval_seconds``,
    and ``paused`` (these are the fields the parsers actually look at
    when rendering the in-prompt task table). Everything else is filled
    with sane placeholders.
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


# ---------------------------------------------------------------------------
# parse_create — Stage 1 (pre-filter) and Stage 2 (SLM)
# ---------------------------------------------------------------------------

async def test_parse_create_pre_filter_rejects_chat_without_slm_call(stub_provider):
    """Chat that has no interval words must return None WITHOUT an SLM call.

    This is the entire point of the pre-filter — the SLM is expensive
    (~200 ms on NPU, ~600 ms on CPU fallback) and every normal chat
    turn would burn one call without it.
    """
    provider = stub_provider(["TASK\nTOPIC: x\nINTERVAL: 1h\nQUERIES:\n - q"])
    parser = TaskIntentParser(provider=provider)

    result = await parser.parse_create("hello, how are you")

    assert result is None
    assert provider.calls == [], "pre-filter must skip the SLM call"


async def test_parse_create_empty_input_returns_none(stub_provider):
    provider = stub_provider([])  # Empty queue — must not be touched.
    parser = TaskIntentParser(provider=provider)

    assert await parser.parse_create("") is None
    assert provider.calls == []


async def test_parse_create_slm_says_not_task_returns_none(stub_provider):
    """Pre-filter matched but SLM said NOT_TASK → None."""
    provider = stub_provider(["NOT_TASK"])
    parser = TaskIntentParser(provider=provider)

    result = await parser.parse_create("every hour I think about nothing")

    assert result is None
    assert len(provider.calls) == 1


async def test_parse_create_well_formed_task_returns_taskspec(stub_provider):
    """Happy path: pre-filter matches, SLM emits a valid TASK block."""
    provider = stub_provider([
        "TASK\n"
        "TOPIC: Microsoft stock + news\n"
        "INTERVAL: 1h\n"
        "QUERIES:\n"
        "  - Microsoft MSFT stock price today\n"
        "  - Microsoft latest news\n"
    ])
    parser = TaskIntentParser(provider=provider)

    spec = await parser.parse_create(
        "check the Microsoft stock price and news every hour",
    )

    assert spec is not None
    assert spec.topic == "Microsoft stock + news"
    assert spec.interval_seconds == 3600
    assert spec.queries == [
        "Microsoft MSFT stock price today",
        "Microsoft latest news",
    ]
    # The operator's verbatim NL request is preserved as the description
    # — this is what shows up in the dashboard / list-tasks output.
    assert spec.description == "check the Microsoft stock price and news every hour"


async def test_parse_create_explicit_interval_overrides_slm(stub_provider):
    """qwen2:1.5b parrots in-context examples ("INTERVAL: 1h") instead of
    respecting the operator. When the operator says "every 10 minutes",
    ``extract_explicit_interval`` MUST override the SLM's value. This was
    a real v1 bug — without it, a 10-minute request scheduled hourly.
    """
    provider = stub_provider([
        # SLM (incorrectly) emits 1h, parroting the prompt example.
        "TASK\n"
        "TOPIC: latest AI news\n"
        "INTERVAL: 1h\n"
        "QUERIES:\n"
        "  - latest AI news today\n"
    ])
    parser = TaskIntentParser(provider=provider)

    spec = await parser.parse_create("summarise the latest AI news every 10 minutes")

    assert spec is not None
    assert spec.interval_seconds == 600, (
        "operator's literal '10 minutes' must override the SLM's 1h"
    )


async def test_parse_create_missing_fields_returns_none(stub_provider):
    """SLM emitted TASK but no QUERIES block → None (no half-baked task)."""
    provider = stub_provider([
        "TASK\n"
        "TOPIC: something\n"
        "INTERVAL: 1h\n"
        # Missing QUERIES entirely.
    ])
    parser = TaskIntentParser(provider=provider)

    spec = await parser.parse_create("check something every hour")

    assert spec is None


async def test_parse_create_provider_exception_returns_none(stub_provider):
    """Any provider exception must degrade to None — better to ignore a
    task than crash the whole chat loop.
    """
    class _Boom:
        async def generate(self, system, messages):
            raise RuntimeError("provider boom")

    parser = TaskIntentParser(provider=_Boom())
    spec = await parser.parse_create("track AI news every hour")

    assert spec is None


# ---------------------------------------------------------------------------
# parse_modify
# ---------------------------------------------------------------------------

async def test_parse_modify_no_live_tasks_skips_slm(stub_provider):
    """No current tasks → don't waste an SLM call. The connector falls
    through and the operator sees a "no tasks" reply from chat.
    """
    provider = stub_provider(["MODIFY\nTARGET_ID: t8f3a\nINTERVAL: 2h"])
    parser = TaskIntentParser(provider=provider)

    # Pre-filter matches ("change ..."), but no tasks exist.
    result = await parser.parse_modify("change t8f3a to every 2 hours", [])

    assert result is None
    assert provider.calls == [], "no live tasks → no SLM call"


async def test_parse_modify_pre_filter_rejects_chat(stub_provider):
    """Plain chat must not even consult the SLM."""
    provider = stub_provider([])
    parser = TaskIntentParser(provider=provider)
    tasks = [_make_task()]

    result = await parser.parse_modify("how are you today", tasks)

    assert result is None
    assert provider.calls == []


async def test_parse_modify_happy_path_returns_update(stub_provider):
    """Happy path: pre-filter matches, SLM emits valid MODIFY block."""
    provider = stub_provider([
        "MODIFY\n"
        "TARGET_ID: t8f3a\n"
        "INTERVAL: 2h\n"
    ])
    parser = TaskIntentParser(provider=provider)
    tasks = [_make_task(task_id="t8f3a", interval_seconds=3600)]

    update = await parser.parse_modify("change t8f3a to every 2 hours", tasks)

    assert update is not None
    assert update.task_id == "t8f3a"
    assert update.new_interval_seconds == 7200  # explicit "2 hours" override
    assert update.new_topic is None
    assert update.new_queries is None


async def test_parse_modify_unknown_id_returns_none(stub_provider):
    """SLM hallucinated an id that isn't in the live task list → None."""
    provider = stub_provider([
        "MODIFY\n"
        "TARGET_ID: tBOGUS\n"
        "INTERVAL: 2h\n"
    ])
    parser = TaskIntentParser(provider=provider)
    tasks = [_make_task(task_id="t8f3a")]

    update = await parser.parse_modify("change my task to every 2 hours", tasks)

    assert update is None


async def test_parse_modify_no_changed_fields_returns_none(stub_provider):
    """SLM resolved a target but emitted no field changes → no-op None."""
    provider = stub_provider([
        "MODIFY\n"
        "TARGET_ID: t8f3a\n"
    ])
    parser = TaskIntentParser(provider=provider)
    tasks = [_make_task(task_id="t8f3a")]

    update = await parser.parse_modify("update t8f3a please", tasks)

    assert update is None


# ---------------------------------------------------------------------------
# parse_action
# ---------------------------------------------------------------------------

async def test_parse_action_fast_path_explicit_id_no_slm(stub_provider):
    """Fast-path: explicit ``t[0-9a-f]{4}`` id + unambiguous verb → no SLM call.

    This makes "/cancel-style" NL phrasings deterministic AND free.
    """
    # Queue empty so any unexpected SLM call raises IndexError loudly.
    provider = stub_provider([])
    parser = TaskIntentParser(provider=provider)
    tasks = [_make_task(task_id="t8f3a")]

    action = await parser.parse_action("cancel t8f3a", tasks)

    assert action is not None
    assert action.task_id == "t8f3a"
    assert action.action == "cancel"
    assert provider.calls == [], "fast-path must skip SLM entirely"


async def test_parse_action_fast_path_pause_verb(stub_provider):
    provider = stub_provider([])
    parser = TaskIntentParser(provider=provider)
    tasks = [_make_task(task_id="t8f3a")]

    action = await parser.parse_action("pause t8f3a please", tasks)

    assert action is not None
    assert action.action == "pause"
    assert provider.calls == []


async def test_parse_action_fast_path_resume_verb(stub_provider):
    provider = stub_provider([])
    parser = TaskIntentParser(provider=provider)
    tasks = [_make_task(task_id="t8f3a", paused=True)]

    action = await parser.parse_action("resume t8f3a", tasks)

    assert action is not None
    assert action.action == "resume"
    assert provider.calls == []


async def test_parse_action_ambiguous_verb_falls_back_to_slm(stub_provider):
    """Message with BOTH cancel AND pause verbs disqualifies fast-path
    → SLM arbitrates. Verifies the fall-through happens, not the verb choice.
    """
    provider = stub_provider([
        "ACTION\n"
        "TARGET_ID: t8f3a\n"
        "VERB: cancel\n"
    ])
    parser = TaskIntentParser(provider=provider)
    tasks = [_make_task(task_id="t8f3a")]

    action = await parser.parse_action(
        "stop t8f3a, or pause the task if that's easier", tasks,
    )

    assert action is not None
    assert action.action == "cancel"
    assert len(provider.calls) == 1, "ambiguous verb must consult the SLM"


async def test_parse_action_no_live_tasks_skips_slm(stub_provider):
    provider = stub_provider([])
    parser = TaskIntentParser(provider=provider)

    action = await parser.parse_action("cancel the MSFT task", [])

    assert action is None
    assert provider.calls == []


async def test_parse_action_slm_unknown_id_returns_none(stub_provider):
    """No fast-path match (no explicit id), SLM hallucinates an id → None."""
    provider = stub_provider([
        "ACTION\n"
        "TARGET_ID: tBOGUS\n"
        "VERB: cancel\n"
    ])
    parser = TaskIntentParser(provider=provider)
    tasks = [_make_task(task_id="t8f3a")]

    action = await parser.parse_action("cancel my task", tasks)

    assert action is None


async def test_parse_action_slm_invalid_verb_returns_none(stub_provider):
    """SLM emitted a VERB not in {cancel, pause, resume} → None.

    Uses ``cancel my task`` (no explicit id) so the fast-path doesn't
    fire and the SLM is actually consulted; otherwise the test name
    would be misleading.
    """
    provider = stub_provider([
        "ACTION\n"
        "TARGET_ID: t8f3a\n"
        "VERB: nuke\n"
    ])
    parser = TaskIntentParser(provider=provider)
    tasks = [_make_task(task_id="t8f3a")]

    action = await parser.parse_action("cancel my task", tasks)

    assert action is None
    assert len(provider.calls) == 1
