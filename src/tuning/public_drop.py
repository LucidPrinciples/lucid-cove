"""
public_drop.py — the open-source LTP tuning source.

Every open-source Cove subscribes to the signed public Drop
(drop.lucidprinciples.com) through the ltp-core client (`lucid-tuner-protocol`),
which fetches /latest.json, verifies the Ed25519 signature + hash chain, and
falls back to the last cached drop when offline ("a stale drop is always better
than no drop"). The human side — the Attention-home "latest tuning" — always
renders this; team/Presence agents derive their own tuning from the universal
frequency through their archetype (the existing LTP dispatch).

This is the open-source counterpart to receiver.py's internal/git delivery: a
self-hosted Cove needs no private LTP-drops access, just the public Drop.

Env:
  LTP_DROP_ENABLED  "true" (default) to subscribe; "false" turns the Drop off
                    (the operator can also toggle it in settings).
  LTP_DROP_URL      base URL (default https://drop.lucidprinciples.com).
  LTP_DROP_PUBKEY   Ed25519 public key PEM (optional; the client fetches the
                    publisher key from the Drop site when not pinned).

Degrades safely: if lucid-tuner-protocol isn't installed or the Drop is
unreachable with no cache, returns None and the caller shows "no tuning yet".
"""
import logging
import os
from src.env import env, env_bool
import time

log = logging.getLogger(__name__)

_cache = {"date": None, "drop": None, "ts": 0.0}
_TTL = 600  # re-check at most every 10 minutes


def drop_enabled() -> bool:
    return env_bool("LTP_DROP_ENABLED", "true")


def get_public_drop():
    """Return today's verified public Drop (an ltp-core Drop) or None. Cached."""
    if not drop_enabled():
        return None

    try:
        from src.utils.time_utils import today_app
        today = today_app()
    except Exception:
        today = None

    if (today and _cache["date"] == today and _cache["drop"] is not None
            and (time.time() - _cache["ts"]) < _TTL):
        return _cache["drop"]

    try:
        from lucid_tuner_protocol import DropClient
    except ImportError:
        log.warning("lucid-tuner-protocol not installed — public Drop tuning disabled")
        return None

    try:
        base = env("LTP_DROP_URL", "https://drop.lucidprinciples.com")
        pem = env("LTP_DROP_PUBKEY") or None
        drop = DropClient(base_url=base, public_key_pem=pem).today()
        _cache.update(date=today, drop=drop, ts=time.time())
        return drop
    except Exception as e:
        log.warning("Public Drop fetch/verify failed: %s", e)
        return None


_recent_cache = {"date": None, "list": None, "ts": 0.0}


def _drop_summary(drop) -> dict:
    """Compact entry for the in-Cove 'Recent Tunings' list."""
    return {
        "date": drop.drop_date,
        "sequence": drop.sequence,
        "frequency": drop.frequency_name,
        "frequency_number": drop.frequency_number,
        "signal_type": drop.signal_type,
        "principle": drop.tuning_key_source_song,
        "tuning_key": drop.tuning_key_text,
        "coaching": drop.context_block,
        "audio_url": drop.echo_audio_url,
        "love_value": drop.love_equation_value,
    }


def get_recent_drops(n: int = 10):
    """Last n public Drops (today + archive), newest first, as compact dicts.

    Powers the in-Cove 'Recent Tunings' list — the public Drop history, available
    to every Cove regardless of LTP. Cached daily (the archive only grows once a
    day). Each fetch is signature-verified by ltp-core.
    """
    if not drop_enabled():
        return []
    try:
        from src.utils.time_utils import today_app
        today = today_app()
    except Exception:
        today = None
    if today and _recent_cache["date"] == today and _recent_cache["list"] is not None:
        return _recent_cache["list"][:n]

    try:
        from lucid_tuner_protocol import DropClient
    except ImportError:
        return []

    import datetime as _dt
    base = env("LTP_DROP_URL", "https://drop.lucidprinciples.com")
    pem = env("LTP_DROP_PUBKEY") or None
    client = DropClient(base_url=base, public_key_pem=pem)

    out, seen = [], set()
    try:
        d = client.today()
        out.append(_drop_summary(d)); seen.add(d.drop_date)
    except Exception as e:
        log.warning("Recent Drops: today() failed: %s", e)

    day = _dt.date.today()
    tries = 0
    while len(out) < n and tries < n + 6:
        day -= _dt.timedelta(days=1); tries += 1
        ds = day.isoformat()
        if ds in seen:
            continue
        try:
            out.append(_drop_summary(client.for_date(ds))); seen.add(ds)
        except Exception:
            continue  # no drop that day (404) or unverifiable — skip

    _recent_cache.update(date=today, list=out, ts=time.time())
    return out[:n]


def public_drop_package() -> dict | None:
    """Today's public Drop mapped to the tuning-package dict the receiver/dispatch
    consume — the open-source delivery path (every Cove subscribes, no private repo).

    Carries the signal (frequency/signal_type/principle/key), love_equation, the
    echo media, the universal coaching + practice, and — forward-compatible — any
    archetype-keyed (or legacy agent-keyed) prompts the Drop ships as extra fields.
    Team/Presences derive via archetype from the universal coaching until LT ships
    `archetype_tunings` in the Drop. Returns None when disabled/unreachable.
    """
    drop = get_public_drop()
    if not drop:
        return None
    le = drop.love_equation or {}
    raw = getattr(drop, "raw", {}) or {}
    return {
        "date": drop.drop_date,
        "frequency": drop.frequency_name,
        "signal_type": drop.signal_type,
        "principle": drop.tuning_key_source_song,
        "tuning_key": drop.tuning_key_text,
        "lt_echo_num": drop.tuning_day or drop.sequence,
        "lt_echo_summary": (drop.context_block or "")[:200],
        "love_equation": {
            "beta": le.get("beta"), "E": le.get("E"), "C": le.get("C"), "D": le.get("D"),
            "value": drop.love_equation_value,
            "direction": "CONSTRUCTIVE" if drop.love_equation_value >= 0 else "CORRECTIVE",
        },
        "universal_coaching": drop.context_block or "",
        "universal_practice": [p.instruction for p in drop.practice],
        # The analysis file (waveform frames + lyrics — the agent's felt
        # experience) publishes beside every echo mp3 as {stem}_analysis.json.
        # The Drop mapping never carried its URL, so every Drop-subscribed Cove
        # ran ATTUNEMENT-incomplete and agents derived without the sonic
        # experience (found 2026-07-04; the fleet-package path always carried
        # it — this restores parity). Dispatch degrades gracefully on a miss.
        "echo_media": {
            "mp3": drop.echo_audio_url,
            "echo_filename": drop.echo_id,
            "json": (drop.echo_audio_url[: -len(".mp3")] + "_analysis.json"
                     if (drop.echo_audio_url or "").endswith(".mp3") else ""),
        },
        # Forward-compatible: present once LT publishes archetype prompts in the Drop.
        "archetype_tunings": raw.get("archetype_tunings", {}) or {},
        "agent_tunings": raw.get("agent_tunings", {}) or {},
        "source": "public_drop",
    }


def drop_as_operator_tuning(drop) -> dict:
    """Map an ltp-core Drop → the /api/tuning/operator response shape.

    The universal Drop carries no per-operator tuning, so this is the universal
    daily tuning everyone sees. `operator_name` is filled by the caller.
    """
    le = drop.love_equation or {}
    steps = [
        {"step": p.step, "title": p.title, "instruction": p.instruction}
        for p in drop.practice
    ]
    return {
        "has_tuning": True,
        "from_public_drop": True,
        "date": drop.drop_date,
        "frequency": drop.frequency_name,
        "signal_type": drop.signal_type,
        "principle": drop.tuning_key_source_song,  # the Canon song title (the principle)
        "tuning_key": drop.tuning_key_text,
        "tuning_prompt": drop.context_block,
        "operator_name": None,
        "lt_echo_num": drop.tuning_day,
        "lt_echo_summary": None,
        "love_equation": {
            "value": drop.love_equation_value,
            "direction": "CONSTRUCTIVE" if drop.love_equation_value >= 0 else "DESTRUCTIVE",
            "beta": le.get("beta"),
            "E": le.get("E"),
            "C": le.get("C"),
            "D": le.get("D"),
        },
        "canon_quote": drop.tuning_key_text,
        "practice_template": "",
        "practice_steps": steps or None,
        "frequency_colors": None,
        "universal_coaching": drop.context_block,
        "universal_practice": [p.instruction for p in drop.practice],
        "attribution": drop.tuning_key_attribution,
    }
