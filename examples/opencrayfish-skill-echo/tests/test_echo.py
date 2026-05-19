"""Package-local sanity tests for the echo reference plugin.

These mirror the integration test in the parent repo
(``tests/test_example_echo_integration.py``) but are scoped to this
package so a third-party author can ``pytest`` from this directory
without the parent repo on PYTHONPATH \u2014 the parent test exercises
the whole stack, this one just checks the manifest is well-formed.
"""
from __future__ import annotations

from opencrayfish_skill_echo import EchoSkill


def test_echo_manifest_basic_fields() -> None:
    m = EchoSkill.manifest
    assert m.name == "echo"
    assert m.compat_version == "skill-protocol/1"
    assert m.plan_verb == "ECHO"
    assert m.cost_tier == "free"
    assert m.requires_network is False
    assert m.requires_tools == ()
