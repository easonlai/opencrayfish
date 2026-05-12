"""core.monitor — Homeostasis sensors (CPU/NPU temperature, RAM, load).

Per HEARTBEAT_LOGIC §1, every pulse must "Continuously monitor thermal and RAM
usage. Trigger 'stress' behavior if thresholds are met." This module exposes a
single async sample() returning a `VitalSigns` snapshot consumed by the
Heartbeat (stress detection) and the Brain (Physical State injection).

v2 design notes:
  - **Hysteresis**: stress now uses two thresholds. We ENTER stress at the
    configured limit but only RELEASE once the reading drops to a separate
    (lower) value. Prevents flap when the temperature/RAM jitters around the
    threshold, which previously caused the agent's persona to oscillate
    between "EXHAUSTION DIRECTIVE on" and "off" turn-by-turn.
  - **Cache**: Brain calls `sample()` on every user turn AND the heartbeat
    calls it every pulse. `psutil.cpu_percent(interval=0.1)` blocks 100 ms
    each time. We cache the last reading for `cache_ttl_s` seconds so user
    turns reuse the heartbeat's fresh data instead of paying the 100 ms cost.
  - **Force-stress for dev**: setting env `OCF_FORCE_STRESS=1` forces
    `is_stressed=True` so we can exercise the EXHAUSTION DIRECTIVE path on
    macOS dev boxes that have no thermal sensor.
"""
from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import psutil

if TYPE_CHECKING:
    from .provider import Provider


@dataclass(frozen=True)
class VitalSigns:
    cpu_percent: float
    ram_percent: float
    temperature_c: float | None  # None when sensor unavailable (e.g. macOS dev box).
    is_stressed: bool
    # ---- Brain (SLM) life signs --------------------------------------------
    # The SLM is the agent's brain — its availability is a vital sign on
    # the same level as temperature and RAM. `brain_online=False` is
    # treated by the dashboard as the equivalent of a stroke: the body
    # is alive (heart still pulsing) but cognition is offline.
    # Defaults preserve back-compat for any caller still constructing
    # `VitalSigns(...)` positionally without these fields.
    brain_online: bool = True
    brain_backend: str = "unknown"
    brain_last_error: str | None = None
    brain_recovery_seconds: float | None = None

    def describe(self) -> str:
        """Short natural-language description for prompt assembly."""
        if self.temperature_c is None:
            thermal = "ambient (sensor unavailable)"
        elif self.is_stressed:
            thermal = f"hot at {self.temperature_c:.1f}°C — under thermal stress"
        else:
            thermal = f"comfortable at {self.temperature_c:.1f}°C"
        body = (
            f"You feel {thermal}. CPU load {self.cpu_percent:.0f}%, "
            f"RAM usage {self.ram_percent:.0f}%."
        )
        if not self.brain_online:
            # Surface in the prompt itself so the agent acknowledges its
            # own offline-brain state if asked. (In practice _cycle
            # short-circuits before this is rendered, but the field is
            # also injected into proactive thoughts and reflection.)
            body += (
                f" Cognition link is DOWN — inference backend "
                f"`{self.brain_backend}` is unreachable."
            )
        return body


class Monitor:
    """Async wrapper around psutil + Linux thermal_zone reads.

    Hysteresis: `thermal_limit_c` / `ram_limit_pct` are the ENTER thresholds;
    `thermal_release_c` / `ram_release_pct` are the EXIT thresholds. If you
    leave the release values unset they default to (limit - 5°C) / (limit - 5%)
    which is enough to absorb normal sensor jitter on a Pi 5.
    """

    _FORCE_STRESS_ENV = "OCF_FORCE_STRESS"

    def __init__(
        self,
        thermal_limit_c: float,
        ram_limit_pct: float = 85.0,
        *,
        thermal_release_c: float | None = None,
        ram_release_pct: float | None = None,
        cache_ttl_s: float = 5.0,
        provider: "Provider | None" = None,
    ) -> None:
        self._thermal_enter = float(thermal_limit_c)
        self._ram_enter = float(ram_limit_pct)
        self._thermal_release = float(
            thermal_release_c if thermal_release_c is not None else thermal_limit_c - 5.0
        )
        self._ram_release = float(
            ram_release_pct if ram_release_pct is not None else ram_limit_pct - 5.0
        )
        # Defensive: if the operator misconfigured release >= enter, fall back
        # to the legacy single-threshold behaviour (release == enter) so the
        # state machine still terminates.
        if self._thermal_release >= self._thermal_enter:
            self._thermal_release = self._thermal_enter
        if self._ram_release >= self._ram_enter:
            self._ram_release = self._ram_enter

        self._cache_ttl_s = max(0.0, float(cache_ttl_s))
        self._cached: VitalSigns | None = None
        self._cached_at: float = 0.0
        self._cache_lock = asyncio.Lock()

        # Hysteresis state — sticky once entered, only released on cool-down.
        self._currently_stressed: bool = False

        # Optional Provider reference — when wired, every sample also
        # reports the SLM's life signs (online / backend / last error).
        # Kept optional so unit tests and tools can build a Monitor
        # without spinning up a Provider.
        self._provider: "Provider | None" = provider

    def attach_provider(self, provider: "Provider") -> None:
        """Late-bind a Provider so brain vitals show up in subsequent samples.

        Useful when subsystem construction order makes injection at
        `__init__` awkward. Calling this with `None` clears the link.
        """
        self._provider = provider

    async def sample(self, *, force_refresh: bool = False) -> VitalSigns:
        """Return a `VitalSigns` snapshot, possibly served from cache.

        The heartbeat passes `force_refresh=True` so its 30 s pulse always
        gets fresh data; Brain (per user turn) accepts the cached value if
        it's fresher than `cache_ttl_s`.
        """
        async with self._cache_lock:
            now = time.monotonic()
            if (
                not force_refresh
                and self._cached is not None
                and (now - self._cached_at) < self._cache_ttl_s
            ):
                return self._cached

            # psutil is synchronous; offload to the default executor so the
            # pulse loop never blocks.
            loop = asyncio.get_running_loop()
            cpu = await loop.run_in_executor(None, psutil.cpu_percent, 0.1)
            ram = (await loop.run_in_executor(None, psutil.virtual_memory)).percent
            temp = await loop.run_in_executor(None, self._read_temperature)

            stressed = self._evaluate_stress(temp=temp, ram=ram)
            self._currently_stressed = stressed

            # Brain (SLM) life signs — cheap synchronous snapshot from the
            # Provider's circuit-breaker state. No network call. When no
            # Provider is wired (unit tests, dev tooling) the brain is
            # reported as online so the dashboard's chip stays neutral.
            if self._provider is not None:
                health = self._provider.health()
                brain_online = health.online
                brain_backend = health.active_backend
                brain_last_error = health.last_error
                brain_recovery_seconds = health.seconds_until_recovery
            else:
                brain_online = True
                brain_backend = "unknown"
                brain_last_error = None
                brain_recovery_seconds = None

            vitals = VitalSigns(
                cpu_percent=cpu,
                ram_percent=ram,
                temperature_c=temp,
                is_stressed=stressed,
                brain_online=brain_online,
                brain_backend=brain_backend,
                brain_last_error=brain_last_error,
                brain_recovery_seconds=brain_recovery_seconds,
            )
            self._cached = vitals
            self._cached_at = now
            return vitals

    # ---------- hysteresis state machine -------------------------------------

    def _evaluate_stress(self, *, temp: float | None, ram: float) -> bool:
        """Apply hysteresis: enter at limit, exit at release threshold."""
        if os.environ.get(self._FORCE_STRESS_ENV) == "1":
            return True

        thermal_enter_hit = temp is not None and temp >= self._thermal_enter
        ram_enter_hit = ram >= self._ram_enter

        if not self._currently_stressed:
            # Calm → stressed only when an enter threshold is crossed.
            return thermal_enter_hit or ram_enter_hit

        # Already stressed → stay stressed until BOTH signals are below their
        # release thresholds. (If one is fine but the other is still hot, we
        # remain in stress to avoid premature relief.)
        thermal_clear = temp is None or temp <= self._thermal_release
        ram_clear = ram <= self._ram_release
        if thermal_clear and ram_clear:
            return False
        return True

    # ---------- internals -----------------------------------------------------

    @staticmethod
    def _read_temperature() -> float | None:
        # Preferred: psutil sensors_temperatures (Linux/Pi).
        getter = getattr(psutil, "sensors_temperatures", None)
        if getter is not None:
            try:
                temps = getter()
            except Exception:
                temps = {}
            for entries in temps.values():
                for entry in entries:
                    if entry.current:
                        return float(entry.current)

        # Fallback: Raspberry Pi thermal_zone0.
        zone = Path("/sys/class/thermal/thermal_zone0/temp")
        if zone.exists():
            try:
                return int(zone.read_text().strip()) / 1000.0
            except (OSError, ValueError):
                return None
        return None
