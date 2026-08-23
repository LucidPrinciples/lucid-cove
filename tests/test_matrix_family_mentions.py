"""COVEMX1-S1: mention detection and poll guards for the Family-room worker.

No homeserver. poll_once is mocked at the HTTP / session boundary.
"""
import pytest

from src.utils import matrix_family as mf
from src.utils.matrix_family import (
    NOTICE_MAX_CHARS,
    event_is_mention,
    family_reply_body,
    family_turn_memory_content,
    family_turn_prompt,
    mention_localparts,
    mentions_in_timeline,
    timeline_events,
)


STEWARD = "@steward:matrix.example.org"
LOCALS = mention_localparts("steward", "stuart", STEWARD)


def _msg(body, sender="@jag:matrix.example.org", mentions=None, formatted=""):
    content = {"msgtype": "m.text", "body": body}
    if formatted:
        content["formatted_body"] = formatted
    if mentions is not None:
        content["m.mentions"] = {"user_ids": mentions}
    return {
        "type": "m.room.message",
        "sender": sender,
        "content": content,
    }


def test_should_run_requires_steward_channel(monkeypatch):
    monkeypatch.setattr(mf, "should_run_family_mention_worker", mf.should_run_family_mention_worker)
    monkeypatch.setattr("src.env.env_bool", lambda key, default=False: False)

    def _no_steward():
        return None

    monkeypatch.setattr("src.config.get_steward_channel_config", _no_steward)
    assert mf.should_run_family_mention_worker() is False

    monkeypatch.setattr(
        "src.config.get_steward_channel_config",
        lambda: {"name": "stuart", "enabled": True},
    )
    assert mf.should_run_family_mention_worker() is True


def test_should_run_skips_public_shared_app(monkeypatch):
    monkeypatch.setattr("src.env.env_bool", lambda key, default=False: key == "LP_REGISTRY_MASTER")
    monkeypatch.setattr(
        "src.config.get_steward_channel_config",
        lambda: {"name": "stuart", "enabled": True},
    )
    assert mf.should_run_family_mention_worker() is False


def test_mention_localparts_strips_mxid():
    assert "steward" in mention_localparts("@steward:matrix.x.org", "Stuart")
    assert "stuart" in mention_localparts("@steward:matrix.x.org", "Stuart")


def test_at_handle_is_a_mention():
    assert event_is_mention(
        _msg("hey @stuart can you check the calendar"),
        steward_user_id=STEWARD,
        localparts=LOCALS,
    )


def test_mxid_in_body_is_a_mention():
    assert event_is_mention(
        _msg("ping " + STEWARD),
        steward_user_id=STEWARD,
        localparts=LOCALS,
    )


def test_structured_mentions_key():
    assert event_is_mention(
        _msg("can you look", mentions=[STEWARD]),
        steward_user_id=STEWARD,
        localparts=LOCALS,
    )


def test_plain_chat_is_not_a_mention():
    assert not event_is_mention(
        _msg("dinner is at 6, no bot needed"),
        steward_user_id=STEWARD,
        localparts=LOCALS,
    )


def test_own_echo_is_ignored():
    assert not event_is_mention(
        _msg("I already said this @stuart", sender=STEWARD),
        steward_user_id=STEWARD,
        localparts=LOCALS,
    )


def test_notice_is_not_a_mention():
    ev = _msg("reply that mentions @stuart")
    ev["content"]["msgtype"] = "m.notice"
    assert not event_is_mention(ev, steward_user_id=STEWARD, localparts=LOCALS)


def test_bare_name_without_at_is_not_a_mention():
    assert not event_is_mention(
        _msg("Stuart the house is loud tonight"),
        steward_user_id=STEWARD,
        localparts=LOCALS,
    )


def test_family_reply_body_caps_long_turns():
    long = ("line %s\n" % "x" * 40) * 80
    out = family_reply_body(long)
    assert len(out) < len(long)
    assert len(out) <= NOTICE_MAX_CHARS
    assert "Mission Control" in out


def test_family_turn_prompt_marks_the_room():
    text = family_turn_prompt("jag", "hey @stuart status")
    assert "Family room" in text
    assert "jag" in text
    assert "host commands" in text


def test_family_turn_memory_names_speaker_and_caps():
    note = family_turn_memory_content("jag", "hey @stuart " + ("x" * 400), "yes " + ("y" * 400))
    assert "jag mentioned the steward in the Family room" in note
    assert "hey @stuart" in note
    assert "Reply:" in note
    assert len(note) < 800


def test_remember_family_turn_is_observation_not_context():
    import inspect
    src = inspect.getsource(mf._remember_family_turn)
    assert 'category="observation"' in src
    assert 'category="context"' not in src
    assert "family_memory_agent_id()" in src
    assert "get_primary_agent_id()" not in src


def test_family_memory_agent_id_uses_steward_pool_not_host_primary(monkeypatch):
    monkeypatch.setattr(
        "src.config.get_steward_channel_config",
        lambda: {"name": "stuart", "agent_id": "stuart"},
    )
    monkeypatch.setattr("src.config.get_primary_agent_id", lambda: "agent")
    assert mf.family_memory_agent_id() == "stuart"
    assert mf.family_thread_id() == "stuart-matrix-family"


def test_family_memory_agent_id_falls_back_to_stuart(monkeypatch):
    monkeypatch.setattr("src.config.get_steward_channel_config", lambda: None)
    assert mf.family_memory_agent_id() == "stuart"


def test_set_status_drops_stale_reason():
    mf._last_status.clear()
    mf._last_status.update({"ok": None, "reason": "not started", "answered": 0})
    mf._set_status({"ok": True, "answered": 1, "caught_up": False})
    assert mf.worker_status() == {"ok": True, "answered": 1, "caught_up": False}


def test_cove_steward_label_is_first_name(monkeypatch):
    from src.dashboard.routes import matrix_spaces as ms

    monkeypatch.setattr(
        "src.config.get_steward_channel_config",
        lambda: {"name": "stuart"},
    )
    assert ms._cove_steward_label() == "Stuart"


def test_timeline_events_only_that_room():
    room = "!fam:example.org"
    body = {
        "rooms": {
            "join": {
                room: {"timeline": {"events": [_msg("hi @stuart")]}},
                "!other:example.org": {"timeline": {"events": [_msg("ignore @stuart")]}},
            }
        }
    }
    events = timeline_events(body, room)
    assert len(events) == 1
    assert events[0]["content"]["body"] == "hi @stuart"


def test_mentions_skip_already_answered():
    ev = _msg("hey @stuart")
    ev["event_id"] = "$once"
    mf._answered_event_ids.clear()
    first = mentions_in_timeline([ev], steward_user_id=STEWARD, localparts=LOCALS)
    assert len(first) == 1
    mf._answered_event_ids.add("$once")
    again = mentions_in_timeline([ev], steward_user_id=STEWARD, localparts=LOCALS)
    assert again == []
    mf._answered_event_ids.clear()


@pytest.mark.asyncio
async def test_poll_once_first_sync_does_not_answer(monkeypatch):
    """Empty cursor = catch-up only. Do not replay history into the room."""
    sent = []

    async def _session(*, force=False):
        return {
            "ok": True,
            "token": "t",
            "hub": "http://dendrite:8008",
            "room_id": "!fam:ex",
            "user_id": STEWARD,
            "localparts": LOCALS,
        }

    async def _http(method, url, token, params=None, body=None):
        return 200, {
            "next_batch": "s1",
            "rooms": {"join": {"!fam:ex": {"timeline": {"events": [
                {**_msg("old @stuart"), "event_id": "$old"},
            ]}}}},
        }

    saved = []

    async def _save(next_batch):
        saved.append(next_batch)

    async def _load():
        return ""

    monkeypatch.setattr(mf, "_bind_session", _session)
    monkeypatch.setattr(mf, "_http", _http)
    monkeypatch.setattr(mf, "_load_cursor", _load)
    monkeypatch.setattr(mf, "_save_cursor", _save)
    monkeypatch.setattr(mf, "_send_notice", lambda *a, **k: sent.append(a))
    monkeypatch.setattr(mf, "_run_steward_turn", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("should not run")))

    result = await mf.poll_once()
    assert result["ok"] is True
    assert result["caught_up"] is True
    assert result["answered"] == 0
    assert sent == []
    assert saved == ["s1"]


@pytest.mark.asyncio
async def test_poll_once_answers_new_mention(monkeypatch):
    sent = []

    async def _session(*, force=False):
        return {
            "ok": True,
            "token": "t",
            "hub": "http://dendrite:8008",
            "room_id": "!fam:ex",
            "user_id": STEWARD,
            "localparts": LOCALS,
        }

    async def _http(method, url, token, params=None, body=None):
        return 200, {
            "next_batch": "s2",
            "rooms": {"join": {"!fam:ex": {"timeline": {"events": [
                {**_msg("hey @stuart what is next"), "event_id": "$new"},
                {**_msg("plain chat"), "event_id": "$plain"},
            ]}}}},
        }

    async def _turn(text, speaker):
        return "got it"

    async def _notice(hub, token, room_id, body):
        sent.append(body)

    monkeypatch.setattr(mf, "_bind_session", _session)
    monkeypatch.setattr(mf, "_http", _http)
    monkeypatch.setattr(mf, "_load_cursor", lambda: _async("s1"))
    monkeypatch.setattr(mf, "_save_cursor", lambda nb: _async(None))
    remembered = []
    seen = []
    typing = []

    async def _remember(speaker, user_text, reply):
        remembered.append((speaker, user_text, reply))

    async def _seen(hub, token, room_id, user_id, event_id):
        seen.append(event_id)
        typing.append(True)

    async def _typing(hub, token, room_id, user_id, on):
        typing.append(bool(on))

    monkeypatch.setattr(mf, "_run_steward_turn", _turn)
    monkeypatch.setattr(mf, "_send_notice", _notice)
    monkeypatch.setattr(mf, "_remember_family_turn", _remember)
    monkeypatch.setattr(mf, "_mark_mention_seen", _seen)
    monkeypatch.setattr(mf, "_set_typing", _typing)
    mf._answered_event_ids.clear()

    result = await mf.poll_once()
    assert result["ok"] is True
    assert result["caught_up"] is False
    assert result["answered"] == 1
    assert sent == ["got it"]
    assert remembered == [("jag", "hey @stuart what is next", "got it")]
    assert seen == ["$new"]
    assert typing[-1] is False
    assert "$new" in mf._answered_event_ids
    assert "reason" not in result
    mf._answered_event_ids.clear()


async def _async(value):
    return value

