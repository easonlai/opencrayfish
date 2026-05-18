"""tests — pytest unit test suite for OpenCrayFish.

The historical ``scripts/smoke_*.py`` scripts remain the canonical
end-to-end regression contract. Tests in this package target the smaller,
pure-logic pieces that are most likely to regress quietly during a
refactor (intent routing, prompt formatting, SLM intent parsing).

Run with:  ``pytest -q``  (from repo root)
"""
