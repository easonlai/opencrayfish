"""core.brain — Prompt Assembly Pipeline (PROMPT_ASSEMBLY.md).

Package layout (post-v2.0 split):

  * ``orchestrator``     — the ``Brain`` class itself: lifecycle, ``think()``,
                           ``_cycle()``, web-triage / LTM / cognitive-loop
                           integration, empathy→mood feedback. The
                           orchestrator owns nothing the other modules can't
                           be handed via constructor injection.
  * ``identity_responder``— identity / creator / status / name short-circuit
                           templates and the ``IdentitySkill`` delegation
                           fallback. Imported by orchestrator and instantiated
                           inside ``Brain.__init__``.
  * ``prompt_assembly``  — pure formatting helpers (``assemble_system_prompt``,
                           ``format_task_block``, ``build_minimal_retry_prompt``).
                           No I/O, no SLM calls, fully unit-testable.
  * ``task_parsing``     — recurring-research task intent parsing (create /
                           modify / action) and final-report synthesis. Each
                           method is one bounded SLM call with a hard output
                           cap and a regex parser; failures degrade to
                           ``NOT_TASK`` rather than raising.

External imports must keep using ``from core.brain import Brain`` — the
sub-modules are an implementation detail. The identity-block parser is
re-exported here as ``extract_identity`` for connector use (originally
named ``_extract_identity`` before the v2.0 split).
"""
from __future__ import annotations

from .identity_responder import extract_identity
from .orchestrator import Brain, ThoughtTrace

__all__ = [
    "Brain",
    "ThoughtTrace",
    "extract_identity",
]
