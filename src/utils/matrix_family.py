"""Family-room mention worker (COVEMX1-S1).

Connect already owns the steward Matrix identity and the Family room.
This loop is the missing reader: long-poll /sync as that steward, answer
only when mentioned, and keep the turn on a dedicated LangGraph thread
so Mission Control Day is not the same conversation.

Does not rebuild Connect, does not speak unprompted, does not touch Haven.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import urllib.parse
import uuid

import httpx
from src.env import env

log = logging.getLogger(__name__)

# Graph + memory use the steward Day channel so tools and the shared
# memory pool match Mission Control. The chat_threads row uses a
# different channel so this loop cannot steal the active Day thread.
FAMILY_THREAD_CHANNEL = "matrix-family"
DEFAULT_GRAPH_CHANNEL = "stuart-day"


def should_run_family_mention_worker() -> bool:
    """True on the family Cove process that owns Connect / cove_matrix.

    Instance type is not the gate: Clearfield's overlay reports ``domain``,
    not ``admin``. Personal agents and the public shared app must not start
    this loop. A merchant-only box with no steward channel also stays off.
    """
    try:
        from src.env import env_bool
        if env_bool("LP_REGISTRY_MASTER"):
            return False
    except Exception:
        pass
    try:
        from src.config import get_steward_channel_config
        return bool(get_steward_channel_config())
    except Exception:
        return False


def family_graph_channel() -> str:
    """Steward Day channel for this Cove — not a Clearfield-only hardcode."""
    try:
        from src.config import get_steward_channel_config
        sc = get_steward_channel_config() or {}
        name = (sc.get("name") or "stuart").strip().lower() or "stuart"
        return f"{name}-day"
    except Exception:
        return DEFAULT_GRAPH_CHANNEL


def worker_status() -> dict:
    """Last poll result for Mission Control / operator checks. No secrets."""
    return dict(_last_status)


def _set_status(result: dict) -> dict:
    """Replace the last poll snapshot so leftover keys cannot linger."""
    _last_status.clear()
    _last_status.update(result)
    return result


def family_thread_id() -> str:
    try:
        from src.config import get_primary_agent_id
        agent = (get_primary_agent_id() or "stuart").strip().lower() or "stuart"
    except Exception:
        agent = "stuart"
    return f"{agent}-matrix-family"
SYNC_TIMEOUT_MS = 30_000
ERROR_BACKOFF_SEC = 15.0
CHAT_RECURSION_LIMIT = 200
SESSION_TTL_SEC = 45 * 60
NOTICE_MAX_CHARS = 1800
MEMORY_BODY_MAX = 280
FAMILY_TURN_PREFIX = (
    "You are answering a mention in the shared Family Matrix room. "
    "Keep the reply short. Do not paste host commands, secrets, file dumps, "
    "or Mission Control internals. If the ask needs the workbench, say so "
    "and keep this reply to the next step only.\n\n"
)

_HANDLE_RE = re.compile(r"(?<!\w)@([a-z0-9._=\-/]{1,64})", re.I)

_session: dict = {}
_answered_event_ids: set[str] = set()
_last_status: dict = {"ok": None, "reason": "not started"}


def mention_localparts(*names: str) -> set[str]:
    """Lowercased localparts this worker answers to."""
    out = set()
    for name in names:
        raw = (name or "").strip().lstrip("@").split(":", 1)[0].lower()
        if raw:
            out.add(raw[:64])
    return out


def event_is_mention(event: dict, *, steward_user_id: str, localparts: set[str]) -> bool:
    """True when a room event is a text mention of the steward, not our own echo."""
    if not isinstance(event, dict):
        return False
    if event.get("type") != "m.room.message":
        return False
    sender = (event.get("sender") or "").strip()
    if not sender or sender == steward_user_id:
        return False
    content = event.get("content") or {}
    msgtype = content.get("msgtype") or "m.text"
    if msgtype != "m.text":
        return False

    mentions = (content.get("m.mentions") or {}).get("user_ids") or []
    if steward_user_id and steward_user_id in mentions:
        return True

    blob = " ".join(
        str(content.get(k) or "")
        for k in ("body", "formatted_body")
    )
    if steward_user_id and steward_user_id in blob:
        return True
    for handle in _HANDLE_RE.findall(blob):
        if handle.lower() in localparts:
            return True
    return False


def timeline_events(sync_body: dict, room_id: str) -> list:
    """m.room.message events in one room from a /sync body."""
    rooms = ((sync_body or {}).get("rooms") or {}).get("join") or {}
    room = rooms.get(room_id) or {}
    events = ((room.get("timeline") or {}).get("events")) or []
    return [e for e in events if isinstance(e, dict)]


def mentions_in_timeline(events: list, *, steward_user_id: str, localparts: set[str]) -> list:
    """Mention events we have not already answered this process."""
    out = []
    for event in events:
        event_id = (event.get("event_id") or "").strip()
        if event_id and event_id in _answered_event_ids:
            continue
        if event_is_mention(event, steward_user_id=steward_user_id, localparts=localparts):
            out.append(event)
    return out


def _event_text(event: dict) -> str:
    content = event.get("content") or {}
    return (content.get("body") or "").strip()


def _speaker_label(sender: str) -> str:
    local = sender.split(":", 1)[0].lstrip("@")
    return local or sender


def family_turn_prompt(speaker: str, user_text: str) -> str:
    who = (speaker or "someone").strip() or "someone"
    return f"{FAMILY_TURN_PREFIX}{who} in the Family room: {user_text}"


def family_reply_body(text: str) -> str:
    """Cap a Family-room notice so a Day-length turn does not flood the room."""
    body = (text or "").strip()
    if not body:
        return "I heard that, but I could not finish a reply."
    if len(body) <= NOTICE_MAX_CHARS:
        return body
    cut = body[: NOTICE_MAX_CHARS - 80].rsplit("\n", 1)[0].rsplit(" ", 1)[0].strip()
    if len(cut) < 200:
        cut = body[: NOTICE_MAX_CHARS - 80].rstrip()
    return cut + "\n\nContinue in Mission Control if you want the rest."


def family_turn_memory_content(speaker: str, user_text: str, reply: str) -> str:
    """Short Day-visible note for one Family-room mention. No secrets, no dump."""
    who = (speaker or "someone").strip() or "someone"
    asked = " ".join((user_text or "").split())
    said = " ".join((reply or "").split())
    if len(asked) > MEMORY_BODY_MAX:
        asked = asked[: MEMORY_BODY_MAX - 1].rstrip() + "…"
    if len(said) > MEMORY_BODY_MAX:
        said = said[: MEMORY_BODY_MAX - 1].rstrip() + "…"
    return (
        f"{who} mentioned the steward in the Family room: {asked} "
        f"Reply: {said}"
    )


async def _remember_family_turn(speaker: str, user_text: str, reply: str) -> None:
    """Write the mention into the shared steward memory pool."""
    from src.config import get_primary_agent_id
    from src.memory.memory import store_memory

    await store_memory(
        family_turn_memory_content(speaker, user_text, reply),
        category="context",
        importance=0.65,
        tags=["matrix", "family-room"],
        agent_id=get_primary_agent_id(),
        source_thread=family_thread_id(),
        source_channel=FAMILY_THREAD_CHANNEL,
        source_summary="Family room mention",
        source_operator_name=(speaker or "").strip() or None,
    )


def _clear_session():
    _session.clear()


def _cursor_column_missing(exc: Exception) -> bool:
    text = str(exc).lower()
    return "sync_next_batch" in text or "undefinedcolumn" in text.replace(" ", "")


async def _http(method: str, url: str, token: str, *, params=None, body=None):
    headers = {"Authorization": "Bearer " + token}
    if body is not None:
        headers["Content-Type"] = "application/json"
    async with httpx.AsyncClient(timeout=45.0) as client:
        resp = await client.request(method, url, headers=headers, params=params, json=body)
    try:
        data = resp.json()
    except Exception:
        data = {}
    return resp.status_code, data


async def _load_cursor() -> str:
    from src.memory.database import get_db
    try:
        async with get_db() as conn:
            r = await conn.execute("SELECT sync_next_batch FROM cove_matrix WHERE id = 1")
            row = await r.fetchone()
        return ((row or {}).get("sync_next_batch") or "").strip()
    except Exception as e:
        if _cursor_column_missing(e):
            log.info("matrix family cursor column not applied yet")
        else:
            log.info("matrix family cursor read skipped: %s", e)
        return ""


async def _save_cursor(next_batch: str) -> None:
    if not next_batch:
        return
    from src.memory.database import get_db
    try:
        async with get_db() as conn:
            await conn.execute(
                "UPDATE cove_matrix SET sync_next_batch = %s, updated_at = NOW() WHERE id = 1",
                (next_batch,),
            )
    except Exception as e:
        if _cursor_column_missing(e):
            log.info("matrix family cursor write skipped — migration not applied")
        else:
            log.warning("matrix family cursor write failed: %s", e)


async def _ensure_family_thread() -> None:
    from src.memory.database import get_db
    from src.config import get_primary_agent_id

    agent_id = get_primary_agent_id()
    thread_id = family_thread_id()
    async with get_db() as conn:
        r = await conn.execute(
            "SELECT thread_id FROM chat_threads WHERE thread_id = %s",
            (thread_id,),
        )
        if await r.fetchone():
            return
        await conn.execute(
            """INSERT INTO chat_threads
               (thread_id, agent_id, channel, title, status, first_message_at, metadata)
               VALUES (%s, %s, %s, %s, 'active', NOW(), %s::jsonb)
               ON CONFLICT (thread_id) DO NOTHING""",
            (
                thread_id,
                agent_id,
                FAMILY_THREAD_CHANNEL,
                "Family room",
                json.dumps({"source": "matrix", "kind": "family-room"}),
            ),
        )


def _extract_reply(messages) -> str:
    from src.dashboard.routes.chat import _extract_thinking

    text = ""
    for msg in reversed(list(messages or [])):
        if getattr(msg, "type", "") != "ai":
            continue
        if getattr(msg, "tool_calls", None):
            continue
        raw = getattr(msg, "content", "") or ""
        if not raw:
            continue
        text, _ = _extract_thinking(raw)
        if text and text.strip():
            return text.strip()
    return ""


async def _run_steward_turn(user_text: str, speaker: str) -> str:
    from langchain_core.messages import HumanMessage
    from src.config import get_primary_agent_id
    from src.graphs.channels import get_channel_graph
    from src.memory.checkpointer import get_checkpointer
    from src.memory.threads import update_thread_stats

    await _ensure_family_thread()
    agent_id = get_primary_agent_id()
    prompt = family_turn_prompt(speaker, user_text)
    thread_id = family_thread_id()
    channel = family_graph_channel()
    reply = ""
    async with get_checkpointer() as checkpointer:
        graph = await get_channel_graph(channel, checkpointer)
        cfg = {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": CHAT_RECURSION_LIMIT,
        }
        graph_input = {
            "messages": [HumanMessage(
                content=prompt,
                additional_kwargs={"created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                   "input_mode": "matrix"},
            )],
            "agent_id": agent_id,
            "channel": channel,
            "input_mode": "matrix",
            "agent_identity": None,
        }
        last_msgs = []
        async for event in graph.astream(graph_input, config=cfg):
            for state_update in event.values():
                last_msgs = state_update.get("messages", last_msgs)
        reply = _extract_reply(last_msgs)
    try:
        await update_thread_stats(thread_id)
    except Exception:
        pass
    return family_reply_body(reply)


async def _send_notice(hub: str, token: str, room_id: str, body: str) -> None:
    txn = uuid.uuid4().hex
    path = "/_matrix/client/v3/rooms/%s/send/m.room.message/%s" % (
        urllib.parse.quote(room_id),
        txn,
    )
    status, data = await _http(
        "PUT",
        hub + path,
        token,
        body={"msgtype": "m.notice", "body": body},
    )
    if status not in (200, 201):
        log.warning("matrix family send failed status=%s err=%s", status, (data or {}).get("error"))


async def _bind_session(*, force: bool = False) -> dict:
    """Steward token + Family room, cached so /sync does not mint a device each loop."""
    now = time.time()
    if (
        not force
        and _session.get("token")
        and _session.get("room_id")
        and (now - float(_session.get("loaded_at") or 0)) < SESSION_TTL_SEC
    ):
        return _session

    from src.dashboard.routes.matrix_spaces import (
        _configured,
        _has_state_table,
        _internal,
        _uid,
        ensure_cove_space,
        ensure_steward,
    )
    from src.config import get_primary_agent_id, get_instance

    if not _configured():
        return {"ok": False, "reason": "matrix not configured"}
    if not await _has_state_table():
        return {"ok": False, "reason": "cove_matrix absent"}

    built = await ensure_cove_space()
    if not built.get("ok"):
        return {"ok": False, "reason": built.get("reason") or "no family room"}
    room_id = built.get("room_id")
    if not room_id:
        return {"ok": False, "reason": "no family room id"}

    steward = await ensure_steward()
    _session.update({
        "ok": True,
        "token": steward["token"],
        "user": steward["user"],
        "user_id": _uid(steward["user"]),
        "room_id": room_id,
        "hub": _internal(),
        "localparts": mention_localparts(
            steward["user"],
            env("MATRIX_STEWARD_LOCALPART", "steward"),
            get_primary_agent_id(),
            (get_instance() or {}).get("name"),
            "stuart",
        ),
        "loaded_at": now,
    })
    return _session


async def poll_once() -> dict:
    """One /sync + mention pass. Safe no-op when Matrix or cove_matrix is missing."""
    sess = await _bind_session()
    if not sess.get("ok"):
        return _set_status({"ok": False, "reason": sess.get("reason") or "no session"})

    token = sess["token"]
    hub = sess["hub"]
    room_id = sess["room_id"]
    user_id = sess["user_id"]
    localparts = sess["localparts"]

    since = await _load_cursor()
    filt = json.dumps({
        "room": {
            "rooms": [room_id],
            "timeline": {"limit": 20, "types": ["m.room.message"]},
        }
    })
    params = {"timeout": 0 if not since else SYNC_TIMEOUT_MS, "filter": filt}
    if since:
        params["since"] = since

    status, body = await _http("GET", hub + "/_matrix/client/v3/sync", token, params=params)
    if status in (401, 403):
        _clear_session()
        return _set_status({"ok": False, "reason": "auth %s" % status})
    if status != 200:
        return _set_status({"ok": False, "reason": "sync %s" % status})

    next_batch = (body or {}).get("next_batch") or ""
    answered = 0
    # First cursor write: catch up without replaying history.
    if since:
        for event in mentions_in_timeline(
            timeline_events(body, room_id),
            steward_user_id=user_id,
            localparts=localparts,
        ):
            text = _event_text(event)
            if not text:
                continue
            speaker = _speaker_label(event.get("sender") or "")
            try:
                reply = await _run_steward_turn(text, speaker)
            except Exception as e:
                log.warning("matrix family turn failed: %s", e)
                reply = (
                    "I caught the mention, but that turn failed. "
                    "Try again in Mission Control if it keeps happening."
                )
            await _send_notice(hub, token, room_id, reply)
            try:
                await _remember_family_turn(speaker, text, reply)
            except Exception as e:
                log.info("matrix family memory write skipped: %s", e)
            event_id = (event.get("event_id") or "").strip()
            if event_id:
                _answered_event_ids.add(event_id)
                if len(_answered_event_ids) > 400:
                    _answered_event_ids.clear()
            answered += 1

    if next_batch:
        await _save_cursor(next_batch)
    return _set_status({"ok": True, "answered": answered, "caught_up": not bool(since)})


async def run_family_mention_loop(stop: asyncio.Event):
    """Long-running worker. Cancel via stop or task cancel."""
    log.info("matrix family mention worker starting")
    while not stop.is_set():
        try:
            result = await poll_once()
            if not result.get("ok"):
                try:
                    await asyncio.wait_for(stop.wait(), timeout=ERROR_BACKOFF_SEC)
                except asyncio.TimeoutError:
                    pass
                continue
            if result.get("caught_up"):
                try:
                    await asyncio.wait_for(stop.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("matrix family loop error: %s", e)
            try:
                await asyncio.wait_for(stop.wait(), timeout=ERROR_BACKOFF_SEC)
            except asyncio.TimeoutError:
                pass
    log.info("matrix family mention worker stopped")
