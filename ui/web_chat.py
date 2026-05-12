"""ui.web_chat — Browser chat client for OpenCrayFish.

A minimal Streamlit chat surface that talks to the in-process aiohttp
bridge published by `connectors.web_chat`. Use it to fast-test the SAME
live agent (same Brain / STM / Heartbeat / Mood) that Telegram talks to,
without leaving the browser.

Run:
    streamlit run ui/web_chat.py

This is intentionally SEPARATE from `ui/dashboard.py` — that one is
read-only telemetry; this one is a chat channel. Run them on different
ports if you want both:

    streamlit run ui/dashboard.py --server.port 8501
    streamlit run ui/web_chat.py  --server.port 8502

Configuration:
  * Defaults to http://127.0.0.1:8765 (matches the WebChatCfg defaults).
  * Override via sidebar inputs OR by setting these env vars before launch:
      OCF_WEB_CHAT_URL    (default: http://127.0.0.1:8765)
      OCF_WEB_CHAT_TOKEN  (default: empty — must match cfg.web_chat.auth_token)
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# Streamlit launches with ui/ as the script dir; make repo-root imports
# resolve in case we ever want to share helpers with the dashboard.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import httpx  # noqa: E402  (already in OpenCrayFish requirements.txt)
import streamlit as st  # noqa: E402

# Optional dep: streamlit-autorefresh. When installed, the chat surface
# polls /history every few seconds so scheduled-task reports (which are
# pushed into STM by the scheduler in the background) appear without the
# operator having to type or click. Without the dep, a manual refresh
# button is the only way to surface them — log a hint at module-load
# time so the operator knows what's missing.
try:
    from streamlit_autorefresh import st_autorefresh  # type: ignore
    _AUTOREFRESH_AVAILABLE = True
except ImportError:  # pragma: no cover
    _AUTOREFRESH_AVAILABLE = False

DEFAULT_URL = os.environ.get("OCF_WEB_CHAT_URL", "http://127.0.0.1:8765")
DEFAULT_TOKEN = os.environ.get("OCF_WEB_CHAT_TOKEN", "")
# How often the chat surface re-fetches /history when autorefresh is on.
# 3 s feels live but doesn't hammer the bridge. Configurable via env so
# operators on slow networks can dial it up.
AUTOREFRESH_INTERVAL_S = int(os.environ.get("OCF_WEB_CHAT_AUTOREFRESH_S", "3"))
REQUEST_TIMEOUT_S = 90.0  # SLM cold-start on Pi 5 can be slow

st.set_page_config(
    page_title="OpenCrayFish — Web Chat",
    page_icon="🦞",
    layout="centered",
)


# ---------- HTTP client helpers ---------------------------------------------

def _headers(token: str) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if token.strip():
        headers["X-OCF-Token"] = token.strip()
    return headers


def _ping(base_url: str, token: str) -> tuple[bool, str]:
    """Return (ok, message). Cheap GET /healthz with no auth required."""
    try:
        r = httpx.get(f"{base_url.rstrip('/')}/healthz", timeout=3.0)
        if r.status_code == 200:
            return True, "agent online"
        return False, f"healthz HTTP {r.status_code}"
    except httpx.ConnectError:
        return False, "agent unreachable (is `python main.py` running?)"
    except Exception as exc:
        return False, f"healthz error: {exc.__class__.__name__}: {exc}"


def _fetch_state(base_url: str, token: str) -> dict | None:
    try:
        r = httpx.get(
            f"{base_url.rstrip('/')}/state",
            headers=_headers(token),
            timeout=5.0,
        )
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def _fetch_history(base_url: str, token: str, limit: int) -> list[dict]:
    try:
        r = httpx.get(
            f"{base_url.rstrip('/')}/history",
            params={"limit": limit},
            headers=_headers(token),
            timeout=5.0,
        )
        if r.status_code != 200:
            return []
        return list(r.json().get("turns") or [])
    except Exception:
        return []


def _send_chat(
    base_url: str, token: str, message: str, *, emergency: bool
) -> tuple[dict | None, str | None]:
    """Returns (response_json, error_string)."""
    try:
        r = httpx.post(
            f"{base_url.rstrip('/')}/chat",
            headers=_headers(token),
            json={"message": message, "emergency": emergency},
            timeout=REQUEST_TIMEOUT_S,
        )
    except httpx.ConnectError:
        return None, "Connection refused — is the agent running?"
    except httpx.ReadTimeout:
        return None, f"Agent did not reply within {REQUEST_TIMEOUT_S:.0f}s."
    except Exception as exc:
        return None, f"{exc.__class__.__name__}: {exc}"
    if r.status_code == 423:
        return None, (
            "💤 Agent is in Sleep Metabolism (02:00-06:00). "
            "Toggle 'Emergency' to wake it."
        )
    if r.status_code == 401:
        return None, "401 Unauthorised — check the auth token in the sidebar."
    if r.status_code != 200:
        try:
            payload = r.json()
        except Exception:
            payload = {"error": r.text[:200]}
        return None, f"HTTP {r.status_code}: {payload.get('error', 'unknown')}"
    try:
        return r.json(), None
    except Exception as exc:
        return None, f"Bad JSON response: {exc}"


# ---------- Sidebar (connection settings + live state) ----------------------

if "base_url" not in st.session_state:
    st.session_state["base_url"] = DEFAULT_URL
if "token" not in st.session_state:
    st.session_state["token"] = DEFAULT_TOKEN
if "messages" not in st.session_state:
    # In-page conversation buffer. Pre-seed from the agent's STM on first
    # render so a fresh browser session shows the SAME history Telegram
    # sees (proof that the channels share state).
    st.session_state["messages"] = []
    st.session_state["_seeded"] = False
if "emergency" not in st.session_state:
    st.session_state["emergency"] = False

with st.sidebar:
    st.title("🦞 OpenCrayFish — Web Chat")
    st.caption("Direct channel to the live agent. Same Brain/STM as Telegram.")

    st.session_state["base_url"] = st.text_input(
        "Bridge URL", st.session_state["base_url"],
        help="The WebChatConnector server inside main.py. "
             "Matches `web_chat.host:port` in config.yaml.",
    )
    st.session_state["token"] = st.text_input(
        "Auth token (optional)", st.session_state["token"], type="password",
        help="Required if you set `web_chat.auth_token` in config.yaml.",
    )
    st.session_state["emergency"] = st.toggle(
        "Emergency mode",
        value=st.session_state["emergency"],
        help="Bypasses Sleep Metabolism (02:00-06:00) for the next message.",
    )

    st.divider()
    st.subheader("Connection")
    ok, msg = _ping(st.session_state["base_url"], st.session_state["token"])
    if ok:
        st.success(msg)
    else:
        st.error(msg)

    st.divider()
    st.subheader("Live agent state")
    if ok:
        state = _fetch_state(
            st.session_state["base_url"], st.session_state["token"]
        )
        if state:
            st.markdown(f"**Designation:** `{state.get('designation', '?')}`")
            sleeping = state.get("sleeping")
            stressed = state.get("stressed")
            brain_online = state.get("brain_online", True)
            brain_backend = state.get("brain_backend", "unknown")
            chips: list[str] = []
            chips.append("💤 sleeping" if sleeping else "🟢 awake")
            chips.append("🔥 stressed" if stressed else "❄️ calm vitals")
            # Brain (SLM) life-sign chip — the SLM is the agent's
            # cognition; if it's offline, surface it loudly so the
            # operator knows replies will be the synthetic fallback.
            if brain_online:
                chips.append(f"🧠 brain `{brain_backend}`")
            else:
                chips.append("🔴 🧠 BRAIN OFFLINE")
            st.markdown(" · ".join(chips))
            st.caption(state.get("vitals_describe", ""))
            if not brain_online:
                err = state.get("brain_last_error")
                if err:
                    st.error(f"Inference offline: {err}")
            ach = state.get("mood_active_channel") or "none"
            ai = float(state.get("mood_active_intensity") or 0.0)
            if ach != "none" and ai >= 0.15:
                emoji = {
                    "joy": "😊", "anger": "😠", "sorrow": "😔",
                    "excitement": "✨", "calm": "🟦",
                }.get(ach, "")
                st.markdown(f"**Active mood:** {emoji} {ach.title()} ({ai:.2f})")
            else:
                st.markdown("**Active mood:** ⚪️ baseline")
        else:
            st.caption("(state endpoint returned nothing)")

    st.divider()
    if st.button("🧹 Clear local view", help="Only clears this browser tab."):
        st.session_state["messages"] = []
        st.session_state["_seeded"] = False
        st.rerun()
    if st.button("🔄 Re-seed from agent STM", help="Pull recent turns from the agent."):
        st.session_state["_seeded"] = False
        st.rerun()

    # Auto-refresh control. Without this the page only reruns on user
    # input, which means scheduled-task reports (delivered into STM by
    # the background scheduler) don't surface until the operator types
    # a new message. Default ON when the dep is present.
    st.divider()
    st.subheader("Auto-refresh")
    if _AUTOREFRESH_AVAILABLE:
        st.session_state.setdefault("autorefresh_on", True)
        st.session_state["autorefresh_on"] = st.toggle(
            f"Poll /history every {AUTOREFRESH_INTERVAL_S}s",
            value=st.session_state["autorefresh_on"],
            help="Surfaces scheduled-task reports without typing.",
        )
    else:
        st.caption(
            "_Install `streamlit-autorefresh` to surface scheduled-task "
            "reports automatically:_  \n`pip install streamlit-autorefresh`"
        )
        st.session_state["autorefresh_on"] = False


# ---------- Auto-refresh trigger -------------------------------------------
# Must be called BEFORE the chat surface renders so the rerun cadence is
# consistent regardless of where the script is in execution.
if _AUTOREFRESH_AVAILABLE and st.session_state.get("autorefresh_on") and ok:
    st_autorefresh(
        interval=AUTOREFRESH_INTERVAL_S * 1000,
        key="ocf_web_chat_autorefresh",
    )


# ---------- Resync from agent STM ------------------------------------------
# We always re-fetch /history when the bridge is reachable (not just on
# first load). This way the source-of-truth is always the agent's STM —
# any scheduled-task report the scheduler pushed into STM in the
# background appears here on the next autorefresh tick. Local optimistic
# appends in the submit handler still work because the /chat endpoint
# writes to STM SYNCHRONOUSLY before returning, so by the time the
# fragment re-fetches /history both turns are already there.
def _resync_messages_from_history() -> None:
    if not ok:
        return
    history = _fetch_history(
        st.session_state["base_url"], st.session_state["token"], limit=50,
    )
    new_messages = [
        {
            "role": "user" if t["role"] == "architect" else "assistant",
            "content": t["content"],
        }
        for t in history
    ]
    # Only replace when something actually changed — avoids a flicker on
    # every autorefresh tick when nothing's new.
    if new_messages != st.session_state.get("messages"):
        st.session_state["messages"] = new_messages


# ---------- Initial seed from agent STM -------------------------------------

if not st.session_state["_seeded"] and ok:
    _resync_messages_from_history()
    st.session_state["_seeded"] = True
elif st.session_state.get("autorefresh_on") and ok:
    # Background path: every Streamlit rerun (driven by autorefresh, by
    # the user typing, or by a sidebar action) we re-pull /history so any
    # scheduled-task reports the scheduler pushed into STM since the last
    # render show up. _resync_messages_from_history is a no-op when
    # nothing changed, so this is cheap.
    _resync_messages_from_history()


# ---------- Main chat surface -----------------------------------------------

st.title("🦞 OpenCrayFish")
st.caption(
    "Web channel — your messages go into the SAME STM the Telegram channel "
    "uses, and the same Heartbeat/Mood/Vitals are applied."
)

if not ok:
    st.warning(
        "Cannot reach the agent. Start it with `python main.py` from the "
        "repo root, then hit refresh."
    )

# Render existing transcript.
for m in st.session_state["messages"]:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])

# Input box.
prompt = st.chat_input(
    "Speak to the agent..." if ok else "Agent unreachable — start `main.py` first.",
    disabled=not ok,
)

if prompt:
    st.session_state["messages"].append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        placeholder = st.empty()
        placeholder.markdown("_thinking…_")
        t0 = time.perf_counter()
        resp, err = _send_chat(
            st.session_state["base_url"],
            st.session_state["token"],
            prompt,
            emergency=st.session_state["emergency"],
        )
        client_ms = int((time.perf_counter() - t0) * 1000)

        if err:
            placeholder.error(err)
            # Don't store the error as an "assistant turn" — keep the local
            # transcript faithful to what the agent actually said.
        else:
            reply = (resp or {}).get("reply", "")
            placeholder.markdown(reply)
            st.session_state["messages"].append(
                {"role": "assistant", "content": reply}
            )
            # Footer line with the metadata the agent returned.
            backend = (resp or {}).get("backend", "?")
            elapsed = (resp or {}).get("elapsed_ms", client_ms)
            stressed = (resp or {}).get("stressed")
            ach = (resp or {}).get("mood_active_channel") or "none"
            ai = float((resp or {}).get("mood_active_intensity") or 0.0)
            chips = [f"backend={backend}", f"agent={elapsed}ms", f"client={client_ms}ms"]
            if stressed:
                chips.append("🔥 stressed")
            if ach != "none" and ai >= 0.15:
                chips.append(f"mood={ach}:{ai:.2f}")
            st.caption(" · ".join(chips))

    # Reset emergency toggle after each message — explicit re-arming next turn.
    if st.session_state["emergency"]:
        st.session_state["emergency"] = False
        st.toast("Emergency flag consumed for that turn.", icon="✅")
