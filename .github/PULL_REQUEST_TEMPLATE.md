<!--
  Thanks for the PR! Please fill in the sections below.
  Keep PRs focused — one logical change per PR.
-->

## Summary

<!-- One or two sentences. What does this PR do, and why? -->

## Linked issues

<!-- "Closes #123" / "Refs #456". If there's no Issue, briefly say why this change is being made. -->

Closes #

## Type of change

- [ ] 🐛 Bug fix (non-breaking change which fixes an issue)
- [ ] ✨ New feature (non-breaking change which adds capability)
- [ ] 💥 Breaking change (fix or feature that changes existing behavior)
- [ ] 📚 Documentation only
- [ ] 🧹 Refactor / cleanup (no functional change)
- [ ] 🧪 Test / smoke-test additions
- [ ] 🔌 New `Tool` / `Connector` / sensor (no core changes)

## How this was tested

<!--
  Be concrete. Examples:
  - "Ran the smoke checks from CONTRIBUTING.md § Running the Smoke Tests — all OK."
  - "Started the agent locally on macOS, sent 5 turns through Telegram, verified deliberation.jsonl shows the new field."
  - "Added unit tests in tests/test_X.py — pytest passes."
-->

## Conventions checklist

Please confirm each item or explain why it doesn't apply:

- [ ] Pylance-clean — no new type errors / warnings.
- [ ] New cross-subsystem types are `@dataclass(frozen=True)`.
- [ ] Any new state-file writer uses the `tmp + os.replace` atomic pattern.
- [ ] No threads / `multiprocessing` introduced — asyncio only.
- [ ] Any new fallible operation in the heartbeat / pulse / cycle path **logs and continues** rather than raising.
- [ ] No new outbound third-party network call (Ollama / SearXNG / local-only).
- [ ] No new runtime dependency, OR the new dependency is justified in the description and is < 50 MB.
- [ ] Documentation updated in the same PR if user-visible behavior, config keys, state files, or wire formats changed.
- [ ] No secrets, tokens, or `config.yaml` content committed.

## Screenshots / logs (if relevant)

<!--
  - Dashboard panel changes: a screenshot helps.
  - New deliberation.jsonl format: paste a sanitised example.
  - Performance changes: paste before/after timings.
-->

## Notes for reviewers

<!-- Anything you want a reviewer to pay extra attention to, or any open question you'd like guidance on. -->
