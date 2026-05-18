"""core.soul_handler — Guardian of `soul.md`.

The Soul is split into two regions by HTML-style markers:

    <!-- IMMUTABLE_CORE_START -->  ... [IDENTITY] / [FUNDAMENTAL_LAWS] / [BEHAVIORAL_MATRIX] ...  <!-- IMMUTABLE_CORE_END -->
    <!-- DYNAMIC_GROWTH_START   -->  ... [CORE_MEMORIES] / [LEARNED_PREFERENCES] / [EMOTIONAL_EVOLUTION] ...  <!-- DYNAMIC_GROWTH_END -->

Per FUNDAMENTAL_LAW #4 (Architect's Sovereignty) the IMMUTABLE_CORE is READ-ONLY
for the Agent. This module enforces that contract via regex: any write that would
mutate bytes inside the immutable region is rejected (`SoulProtectionError`),
and only the DYNAMIC_GROWTH region can be appended to / rewritten.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

log = logging.getLogger(__name__)

# --- Region markers (must match `soul.md` exactly) ----------------------------
IMMUTABLE_START: Final = "<!-- IMMUTABLE_CORE_START -->"
IMMUTABLE_END: Final = "<!-- IMMUTABLE_CORE_END -->"
DYNAMIC_START: Final = "<!-- DYNAMIC_GROWTH_START -->"
DYNAMIC_END: Final = "<!-- DYNAMIC_GROWTH_END -->"

_IMMUTABLE_RE: Final = re.compile(
    r"(?P<prefix>.*?)"
    rf"(?P<core_open>{re.escape(IMMUTABLE_START)})"
    r"(?P<core_body>.*?)"
    rf"(?P<core_close>{re.escape(IMMUTABLE_END)})"
    r"(?P<middle>.*?)"
    rf"(?P<dyn_open>{re.escape(DYNAMIC_START)})"
    r"(?P<dyn_body>.*?)"
    rf"(?P<dyn_close>{re.escape(DYNAMIC_END)})"
    r"(?P<suffix>.*)",
    re.DOTALL,
)

# Sub-section anchors inside DYNAMIC_GROWTH used for typed appends.
_SUBSECTIONS: Final = {
    "core_memory": "# [CORE_MEMORIES]",
    "preference": "# [LEARNED_PREFERENCES]",
    "emotion": "# [EMOTIONAL_EVOLUTION]",
}

# Tokens that, if echoed verbatim by an SLM into an append() call, would
# corrupt the soul.md region structure. We collapse any occurrence to a safe
# textual representation BEFORE the write candidate is built. This protects
# Pillar 1 (Identity Sovereignty) against indirect mutation: even if the
# IMMUTABLE_CORE bytes are unchanged, an attacker who can influence what gets
# appended must not be able to relocate the dynamic boundaries.
_FORBIDDEN_SUBSTRINGS: Final = (
    IMMUTABLE_START,
    IMMUTABLE_END,
    DYNAMIC_START,
    DYNAMIC_END,
)
_SUBSECTION_HEADER_RE: Final = re.compile(r"^\s*#\s*\[[A-Z_]+\]\s*$", re.MULTILINE)


def _sanitize_dynamic_text(text: str) -> str:
    """Strip control sequences that would corrupt soul.md's region markers.

    Applied to every operand passed into `_append`. Removes:
      * region-marker comments (collapsed to literal-safe placeholders),
      * subsection headers (so the SLM cannot inject a bogus `# [IDENTITY]`),
      * carriage returns / NULs that would break line-based parsing.
    """
    cleaned = text
    for marker in _FORBIDDEN_SUBSTRINGS:
        if marker in cleaned:
            cleaned = cleaned.replace(marker, marker.strip("<!- >") + "[neutralized]")
    cleaned = _SUBSECTION_HEADER_RE.sub(
        lambda m: m.group(0).replace("#", "(neutralized)"), cleaned
    )
    cleaned = cleaned.replace("\x00", "").replace("\r", "")
    # Collapse newlines — soul append entries are single-line bullets per the spec.
    cleaned = " ".join(cleaned.split())
    return cleaned.strip()


class SoulProtectionError(RuntimeError):
    """Raised when an agent attempts to mutate the IMMUTABLE_CORE."""


@dataclass(frozen=True)
class SoulSnapshot:
    immutable_core: str   # Contents between IMMUTABLE markers (exclusive).
    dynamic_growth: str   # Contents between DYNAMIC markers (exclusive).
    raw: str              # Entire on-disk file.


class SoulHandler:
    """Async-safe accessor for `soul.md` with hard write-protection."""

    # Matches the IDENTITY `Designation` bullet line in any reasonable casing.
    _DESIGNATION_RE: Final = re.compile(
        r"^(?P<lead>\s*[-*]\s*\*\*Designation\*\*\s*:\s*).*$",
        re.MULTILINE | re.IGNORECASE,
    )

    def __init__(
        self,
        soul_path: str | Path,
        *,
        designation_override: str | None = None,
    ) -> None:
        self._path = Path(soul_path)
        self._lock = asyncio.Lock()
        if not self._path.exists():
            raise FileNotFoundError(f"soul.md not found at {self._path}")
        # Cache the canonical immutable bytes on construction; any later read
        # will be cross-checked against this fingerprint. The override (if any)
        # is applied ONLY on the read path — the on-disk file is never
        # mutated, so write-protection in `_append` continues to compare raw
        # bytes against the raw on-disk canonical.
        self._canonical_immutable = self._parse(self._path.read_text(encoding="utf-8")).immutable_core
        self._designation_override: str | None = (
            (designation_override or "").strip() or None
        )

    # ---------- read paths ----------------------------------------------------

    def _apply_overrides(self, immutable_core: str) -> str:
        """Swap (or inject) the IDENTITY Designation line at runtime.

        Returns `immutable_core` unchanged if no override is configured. When
        an override is set:
          * If a `**Designation**:` line exists, its value is replaced in
            place (preserving the bullet style and indentation).
          * If no such line exists, a fresh bullet is injected immediately
            after the `# [IDENTITY]` heading so the prompt assembly still
            has the expected structure.
        Either way, the on-disk file is never mutated — these substitutions
        are in-memory only and `_append`'s write-protection still compares
        raw bytes against the canonical on-disk image.
        """
        if not self._designation_override:
            return immutable_core
        new_value = self._designation_override
        if self._DESIGNATION_RE.search(immutable_core):
            return self._DESIGNATION_RE.sub(
                lambda m: f"{m.group('lead')}{new_value}",
                immutable_core,
                count=1,
            )
        # Inject after the [IDENTITY] heading. If that heading is missing
        # too (operator chose a different layout), fall back to prepending.
        injection = f"- **Designation**: {new_value}"
        identity_match = re.search(
            r"^(?P<heading>\s*#\s*\[IDENTITY\]\s*)$",
            immutable_core,
            re.MULTILINE,
        )
        if identity_match:
            insert_at = identity_match.end()
            return (
                immutable_core[:insert_at]
                + "\n"
                + injection
                + immutable_core[insert_at:]
            )
        return f"# [IDENTITY]\n{injection}\n\n{immutable_core}"

    async def read(self) -> SoulSnapshot:
        async with self._lock:
            snap = self._parse(self._path.read_text(encoding="utf-8"))
        if not self._designation_override:
            return snap
        # Return an overridden snapshot WITHOUT mutating on-disk bytes.
        return SoulSnapshot(
            immutable_core=self._apply_overrides(snap.immutable_core),
            dynamic_growth=snap.dynamic_growth,
            raw=snap.raw,
        )

    async def render_identity_block(self) -> str:
        """Return the IDENTITY + FUNDAMENTAL_LAWS text used by Brain prompt assembly."""
        snap = await self.read()
        return self._strip_html_comments(snap.immutable_core).strip()

    async def render_dynamic_block(self) -> str:
        snap = await self.read()
        return self._strip_html_comments(snap.dynamic_growth).strip()

    @staticmethod
    def _strip_html_comments(text: str) -> str:
        """Remove `<!-- ... -->` comments from text destined for the SLM prompt.

        soul.md uses HTML comments for human-facing operator notes (e.g.
        "Designation is injected from config.yaml"). Small SLMs sometimes
        echo or re-interpret such notes verbatim into their replies, so we
        strip them at the prompt-render boundary. The on-disk file and the
        write-protection canonical (`_canonical_immutable`) keep the
        original bytes — only the prompt-facing view is sanitized.
        """
        return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)

    # ---------- write paths (DYNAMIC region only) -----------------------------

    async def append_core_memory(self, memory: str) -> None:
        await self._append("core_memory", memory)

    async def append_preference(self, preference: str) -> None:
        await self._append("preference", preference)

    async def append_emotion_event(self, event: str) -> None:
        await self._append("emotion", event)

    async def _append(self, section_key: str, text: str) -> None:
        if section_key not in _SUBSECTIONS:
            raise ValueError(f"Unknown soul subsection: {section_key}")
        anchor = _SUBSECTIONS[section_key]
        # Pillar 1: sanitize before the candidate is even constructed.
        safe_text = _sanitize_dynamic_text(text)
        if not safe_text:
            raise SoulProtectionError(
                "Refusing write: candidate text is empty after sanitization."
            )
        stamped = f"- {safe_text}"

        async with self._lock:
            current = self._path.read_text(encoding="utf-8")
            parts = self._parse(current)

            # Insert under the matching anchor inside the DYNAMIC region only.
            if anchor in parts.dynamic_growth:
                new_dynamic = parts.dynamic_growth.replace(
                    anchor,
                    f"{anchor}\n{stamped}",
                    1,
                )
            else:
                new_dynamic = f"{parts.dynamic_growth.rstrip()}\n{anchor}\n{stamped}\n"

            new_raw = self._reassemble(parts, dynamic_growth=new_dynamic)

            # Final guard: re-parse and verify the immutable region is byte-identical.
            verify = self._parse(new_raw)
            if verify.immutable_core != self._canonical_immutable:
                raise SoulProtectionError(
                    "Refusing write: candidate output mutated IMMUTABLE_CORE."
                )
            # Defense-in-depth: the dynamic region must still parse cleanly,
            # i.e. the four markers must each occur exactly once.
            for marker in _FORBIDDEN_SUBSTRINGS:
                if new_raw.count(marker) != 1:
                    raise SoulProtectionError(
                        f"Refusing write: marker {marker!r} count would become "
                        f"{new_raw.count(marker)} (must be 1)."
                    )
            # Atomic disk swap: write to a sibling tmp then POSIX-rename
            # over the canonical path. Guarantees a power-loss / OOM-kill
            # mid-write never leaves soul.md truncated — the on-disk file
            # is ALWAYS either the previous good content or the new good
            # content, never a half-written intermediate. The four dry-run
            # checks above (sanitize, immutable fingerprint, marker count)
            # have already proven `new_raw` is structurally valid, so the
            # only failure mode left is the disk I/O itself.
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            try:
                tmp.write_text(new_raw, encoding="utf-8")
                tmp.replace(self._path)  # atomic on the same filesystem
            except Exception:
                # Clean up any half-written tmp so it doesn't linger as
                # operator-visible cruft. The canonical soul.md is
                # untouched because tmp.replace was either not reached
                # or is itself atomic.
                tmp.unlink(missing_ok=True)
                raise
            log.info(
                "Soul append: section=%s len=%d preview=%r",
                section_key,
                len(safe_text),
                safe_text[:80],
            )

    # ---------- internals -----------------------------------------------------

    def _parse(self, raw: str) -> SoulSnapshot:
        m = _IMMUTABLE_RE.match(raw)
        if not m:
            raise SoulProtectionError(
                "soul.md is malformed: required IMMUTABLE_CORE / DYNAMIC_GROWTH markers missing."
            )
        return SoulSnapshot(
            immutable_core=m.group("core_body"),
            dynamic_growth=m.group("dyn_body"),
            raw=raw,
        )

    def _reassemble(self, parts: SoulSnapshot, *, dynamic_growth: str) -> str:
        m = _IMMUTABLE_RE.match(parts.raw)
        assert m is not None  # parsed once in caller
        return (
            m.group("prefix")
            + m.group("core_open")
            + parts.immutable_core            # NEVER substituted
            + m.group("core_close")
            + m.group("middle")
            + m.group("dyn_open")
            + dynamic_growth
            + m.group("dyn_close")
            + m.group("suffix")
        )
