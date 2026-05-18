"""conftest.py — pytest fixtures shared across the unit-test suite.

Lives at repo root so BOTH ``tests/`` and ``scripts/smoke_*.py`` can pick
up the fixtures. The smokes don't currently consume fixtures (they're
self-contained scripts), but having conftest at the root means a future
smoke can opt in by declaring a fixture parameter.

Conventions in this file:
  * Fixtures are kept tiny — fixture bodies do ONE thing. Test logic
    that needs orchestration lives in the test, not the fixture.
  * Every fixture's docstring explains WHY it's separate from the
    test, not just what it does. Fixtures with no clear "why" are
    candidates for inlining.
  * No autouse fixtures. Every dependency is explicit.

Shared fixtures here:
  * ``tmp_archive``        — a writable ``Path`` for tests that need a
                              throw-away on-disk file (soul.md, archive.md,
                              rotated JSONL feed, etc.).
  * ``stub_provider``      — minimal fake of ``core.provider.Provider``
                              that returns a programmable canned response
                              from ``generate()``. Used by tests of any
                              module that calls ``self._provider.generate``.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

# Make ``core``, ``connectors``, ``tools``, ``ui`` importable without an
# editable install. CI does ``pip install -e .[dev]`` which would also
# work, but this keeps ``pytest`` runnable from a fresh checkout with
# only the dev dependencies installed.
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


@pytest.fixture
def tmp_archive(tmp_path: Path) -> Path:
    """Return a writable temp directory wrapped as a ``Path``.

    Thin alias over pytest's built-in ``tmp_path`` — kept as a named
    fixture so test signatures read ``def test_x(tmp_archive)`` instead
    of ``def test_x(tmp_path)`` which is opaque to a reader who's never
    used pytest's directory fixtures.
    """
    return tmp_path


class _StubProvider:
    """Programmable fake of ``core.provider.Provider`` for parser tests.

    Tests construct it with a list of canned replies; each call to
    ``generate(system, messages)`` pops one off the front. When the
    queue is exhausted ``generate`` raises ``IndexError`` — that's a
    fixture bug, not a parser bug, and we want it loud.

    The call history is recorded on ``calls`` (list of ``(system, msgs)``
    tuples) so tests can assert exactly which prompt the parser sent.
    """

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.calls: list[tuple[str, list[Any]]] = []

    async def generate(self, system: str, messages: list[Any]) -> str:
        self.calls.append((system, messages))
        return self._replies.pop(0)


@pytest.fixture
def stub_provider():
    """Return the StubProvider class itself so tests can spin one up.

    Returning the class (not an instance) lets each test declare its
    own canned-reply list inline, which is more readable than passing
    parametrize markers for the reply queue.
    """
    return _StubProvider
