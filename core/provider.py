"""core.provider — Inference backend abstraction.

Per CRITICAL CONSTRAINT #2 the Agent leverages the Raspberry Pi 5 + Raspberry
Pi AI HAT+ 2 (Hailo-10H NPU, 40 TOPS, 8 GB dedicated on-board RAM — the
generative-AI-capable HAT, NOT the older vision-only Hailo-8 / Hailo-8L
HAT+). The AI HAT+ 2 exposes its NPU through the **hailo-ollama** REST
service on **port 8000** — same `/api/chat` contract as upstream Ollama, but
the model is served from the NPU rather than CPU. Stock Ollama on **port
11434** acts as transparent CPU fallback when the NPU service is unreachable.

Reference (hailo-ollama)::

    POST http://localhost:8000/api/chat
    {"model": "qwen2.5-instruct:1.5b", "messages": [...], "stream": false}
    -> {"message": {"content": "..."}}

Reference (Ollama CPU fallback)::

    POST http://localhost:11434/api/chat
    {"model": "qwen2:1.5b", "messages": [...], "stream": false}

Public surface::

    await provider.generate(system_prompt, messages) -> str

Failure model::

    When BOTH backends raise transport errors the Provider raises
    `ProviderUnavailable` and arms a circuit-breaker for `trip_seconds`
    so subsequent calls fail fast without retrying dead sockets. The
    Brain catches `ProviderUnavailable` once at the top of `_cycle` and
    returns a synthetic "offline" `ThoughtTrace` — connectors render
    the friendly message verbatim and don't need any provider-failure
    code of their own.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

log = logging.getLogger(__name__)


class ProviderUnavailable(RuntimeError):
    """Raised when both inference endpoints are unreachable.

    The Brain catches this at one place (`_cycle`) and surfaces
    `friendly_message` verbatim through a synthetic `ThoughtTrace`
    with `backend="offline"`. Connectors render whatever they get;
    new connectors inherit this behaviour for free.
    """

    DEFAULT_MESSAGE = (
        "I can't reach the inference service right now — both the NPU "
        "endpoint (port 8000) and the CPU fallback (port 11434) are "
        "offline. Start `ollama serve` (CPU) or `hailo-ollama` (NPU) "
        "and try again in a moment."
    )

    def __init__(
        self,
        message: str | None = None,
        *,
        cause: BaseException | None = None,
    ) -> None:
        text = message or self.DEFAULT_MESSAGE
        super().__init__(text)
        self.friendly_message = text
        if cause is not None:
            self.__cause__ = cause


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str


@dataclass(frozen=True)
class ProviderHealth:
    """Lightweight snapshot of the inference layer's life signs.

    Consumed by `Monitor.sample()` so SLM availability shows up as a
    first-class vital — the SLM is the agent's brain, so its absence is
    analogous to a stroke for an organic being. `seconds_until_recovery`
    is `None` when the breaker isn't tripped (or when it just expired)
    and a positive float while the breaker is open.
    """
    online: bool
    active_backend: str
    seconds_until_recovery: float | None
    last_error: str | None


class _Backend(Protocol):
    name: str
    async def generate(self, system_prompt: str, messages: list[ChatMessage]) -> str: ...
    async def aclose(self) -> None: ...


# --- Shared Ollama-compatible HTTP backend ------------------------------------

class _OllamaCompatibleBackend:
    """Shared client for any server speaking the Ollama `/api/chat` contract.

    Both Hailo-Ollama (NPU, port 8000) and stock Ollama (CPU, port 11434) use
    this exact wire format, so a single implementation serves both.
    """

    def __init__(self, *, name: str, base_url: str, model: str, timeout_s: float = 120.0) -> None:
        self.name = name
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(timeout_s))

    async def generate(self, system_prompt: str, messages: list[ChatMessage]) -> str:
        payload: dict[str, Any] = {
            "model": self._model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system_prompt},
                *({"role": m.role, "content": m.content} for m in messages),
            ],
        }
        resp = await self._client.post(f"{self._base_url}/api/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()
        # Both Hailo-Ollama and Ollama return {"message": {"content": "..."}}.
        content = data.get("message", {}).get("content", "")
        text = content.strip() if isinstance(content, str) else ""
        return text or json.dumps(data)

    async def aclose(self) -> None:
        await self._client.aclose()


class HailoOllamaBackend(_OllamaCompatibleBackend):
    """NPU-accelerated path: Hailo-Ollama REST service on the Pi 5 AI HAT+."""

    # Plug-in manifest \u2014 read by the discovery layer + future
    # registry. Declares the NPU + outbound-network capabilities so
    # operators auditing capability tokens see the full picture.
    from core.provider_manifest import BackendManifest as _BM
    manifest = _BM(
        name="hailo-ollama-npu",
        description="NPU-accelerated Ollama-compatible backend "
                    "(Pi 5 AI HAT+ / Hailo).",
        requires_caps=("network.outbound", "npu"),
    )
    del _BM

    def __init__(self, base_url: str, model: str) -> None:
        super().__init__(name="hailo-ollama-npu", base_url=base_url, model=model)


class OllamaBackend(_OllamaCompatibleBackend):
    """CPU fallback: upstream Ollama daemon on `hardware.cpu_fallback_url`."""

    # Plug-in manifest \u2014 see HailoOllamaBackend above.
    from core.provider_manifest import BackendManifest as _BM
    manifest = _BM(
        name="ollama-cpu",
        description="CPU Ollama backend (upstream daemon).",
        requires_caps=("network.outbound",),
    )
    del _BM

    def __init__(self, base_url: str, model: str) -> None:
        super().__init__(name="ollama-cpu", base_url=base_url, model=model)


# --- Facade -------------------------------------------------------------------

class Provider:
    """Routes inference to NPU first, then CPU fallback.

    Carries a small circuit-breaker: when BOTH backends raise transport
    errors in the same call, the breaker trips for `trip_seconds` and
    subsequent `generate()` calls raise `ProviderUnavailable` immediately
    without re-attempting (avoids 4-call × 5-second TCP timeout cascades
    when the operator forgot to start `ollama serve`).
    """

    # Default circuit-breaker window. Long enough that a typical user
    # turn (with 1–3 SLM calls — triage + main + reflection) only pays
    # the real connect-timeout cost once, short enough that the agent
    # auto-recovers within a single sleep-pulse after `ollama serve`.
    _DEFAULT_TRIP_SECONDS = 30.0

    def __init__(
        self,
        primary: _Backend,
        fallback: _Backend,
        *,
        trip_seconds: float | None = None,
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._active = primary
        self._tripped_until: float = 0.0
        self._trip_seconds = float(
            trip_seconds if trip_seconds is not None else self._DEFAULT_TRIP_SECONDS
        )
        # Human-readable last failure surfaced through `health()` so the
        # dashboard can show WHY the brain is offline (e.g. "ConnectError:
        # Connection refused"). Cleared on the first successful call.
        self._last_error: str | None = None

    @property
    def active_backend(self) -> str:
        return self._active.name

    @property
    def is_tripped(self) -> bool:
        """True while the circuit-breaker is open (both backends down)."""
        return time.monotonic() < self._tripped_until

    def health(self) -> ProviderHealth:
        """Snapshot of the inference layer's life signs.

        Treated as a vital sign by `Monitor` — the SLM is the agent's
        brain, so its availability is reported alongside cpu / ram / temp.
        """
        remaining = self._tripped_until - time.monotonic()
        return ProviderHealth(
            online=not self.is_tripped,
            active_backend=self._active.name,
            seconds_until_recovery=remaining if remaining > 0 else None,
            last_error=self._last_error,
        )

    def _trip(self, cause: BaseException) -> None:
        self._tripped_until = time.monotonic() + self._trip_seconds
        self._last_error = f"{type(cause).__name__}: {cause}"
        log.warning(
            "Provider circuit-breaker TRIPPED for %.0fs — both backends "
            "unreachable (cause=%s: %s)",
            self._trip_seconds, type(cause).__name__, cause,
        )

    def _untrip(self) -> None:
        if self._tripped_until:
            log.info("Provider circuit-breaker recovered (active=%s)",
                     self._active.name)
        self._tripped_until = 0.0
        self._last_error = None

    async def generate(self, system_prompt: str, messages: list[ChatMessage]) -> str:
        # Fail fast while the breaker is open. Avoids stacking
        # connect-timeouts across the triage/main/reflection chain.
        if self.is_tripped:
            raise ProviderUnavailable()
        try:
            result = await self._active.generate(system_prompt, messages)
            self._untrip()
            return result
        except Exception as exc:
            # CPU-only mode: primary IS fallback (single shared instance).
            # No one to fail over to → trip and surface friendly error.
            if self._active is self._fallback:
                self._trip(exc)
                raise ProviderUnavailable(cause=exc) from exc
            log.warning("Primary backend %s failed (%s); failing over to %s",
                        self._active.name, exc, self._fallback.name)
            self._active = self._fallback
            try:
                result = await self._active.generate(system_prompt, messages)
                self._untrip()
                return result
            except Exception as exc2:
                # Both endpoints down — trip the breaker and raise the
                # synthesised friendly error so connectors don't have to
                # interpret httpx exception zoo.
                self._trip(exc2)
                raise ProviderUnavailable(cause=exc2) from exc2

    async def aclose(self) -> None:
        # When `npu_acceleration: false` the constructor sets primary and
        # fallback to the SAME instance. Closing the underlying httpx
        # client twice is currently tolerated by httpx but fragile —
        # dedupe explicitly so we only ever issue one aclose per backend.
        if self._primary is self._fallback:
            try:
                await self._primary.aclose()
            except Exception:  # pragma: no cover — defensive
                log.exception("Provider aclose failed")
            return
        await asyncio.gather(
            self._primary.aclose(), self._fallback.aclose(),
            return_exceptions=True,
        )

    @classmethod
    def from_config(
        cls,
        hardware_cfg: Any,
        *,
        discovered_backends: list[tuple[Any, Any]] | None = None,
    ) -> Provider:
        """Build a Provider, optionally routing third-party backends.

        ``discovered_backends`` is the list returned by
        ``core.provider_manifest.discover_external_backends()`` —
        ``[(manifest, instance), ...]``. When ``hardware_cfg.primary_backend``
        or ``hardware_cfg.fallback_backend`` names one of those manifests,
        that instance takes the slot instead of the built-in Pi 5 backend.
        Fail-loud on unknown names so a typo can't silently degrade to CPU.
        """
        by_name: dict[str, Any] = {}
        if discovered_backends:
            for manifest, instance in discovered_backends:
                by_name[manifest.name] = instance

        primary_override = getattr(hardware_cfg, "primary_backend", None)
        fallback_override = getattr(hardware_cfg, "fallback_backend", None)

        if primary_override and primary_override not in by_name:
            raise ValueError(
                f"hardware.primary_backend={primary_override!r} is not a "
                f"discovered backend. Available: {sorted(by_name) or 'none'}."
            )
        if fallback_override and fallback_override not in by_name:
            raise ValueError(
                f"hardware.fallback_backend={fallback_override!r} is not a "
                f"discovered backend. Available: {sorted(by_name) or 'none'}."
            )

        if fallback_override:
            fallback: _Backend = by_name[fallback_override]
        else:
            fallback = OllamaBackend(
                base_url=hardware_cfg.cpu_fallback_url,
                model=hardware_cfg.cpu_fallback_model,
            )

        if primary_override:
            primary: _Backend = by_name[primary_override]
        elif hardware_cfg.npu_acceleration:
            primary = HailoOllamaBackend(
                base_url=hardware_cfg.hailo_ollama_url,
                model=hardware_cfg.hailo_model,
            )
        else:
            primary = fallback
        return cls(primary=primary, fallback=fallback)
