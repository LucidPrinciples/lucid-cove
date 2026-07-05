"""
Central timezone utilities for Lucid Cove.

All date/time logic should go through these helpers rather than
calling date.today() or datetime.now() directly.

Timezone cascade:
    1. Presence timezone (per-user, from DB)    — use presence_tz()
    2. Cove timezone (from cove.yaml/agent.yaml) — use app_tz()
    3. Fallback: America/New_York

Usage:
    from src.utils.time_utils import app_tz, today_app, now_app, presence_tz
"""

import os
from src.env import env
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

_DEFAULT_TZ = "America/New_York"


def app_tz() -> ZoneInfo:
    """Return the Cove-level timezone from config (cove.yaml → agent.yaml → fallback).

    Reads from the config cascade, NOT from env vars. This is the Cove default
    that all Presences inherit unless they set their own.
    """
    try:
        from src.config import get_instance
        tz_name = get_instance().get("timezone", _DEFAULT_TZ)
    except Exception:
        tz_name = env("APP_TIMEZONE", _DEFAULT_TZ)
    return ZoneInfo(tz_name)


def presence_tz(tz_name: str | None = None) -> ZoneInfo:
    """Return a Presence-level timezone, falling back to Cove timezone.

    Args:
        tz_name: IANA timezone string from the Presence's account record.
                 If None or empty, falls back to app_tz().
    """
    if tz_name:
        try:
            return ZoneInfo(tz_name)
        except (KeyError, Exception):
            pass
    return app_tz()


def now_app() -> datetime:
    """Current datetime in the app timezone (timezone-aware)."""
    return datetime.now(app_tz())


def now_utc() -> datetime:
    """Current datetime in UTC (timezone-aware). Use for DB writes."""
    return datetime.now(timezone.utc)


def today_app() -> str:
    """Today's date string (YYYY-MM-DD) in the app timezone.

    Use this instead of date.today() everywhere. The VPS runs UTC — after
    midnight UTC but before midnight in the app timezone, date.today() returns
    tomorrow's date, breaking any 'did X happen today' checks.
    """
    return now_app().date().isoformat()


def ts_log() -> str:
    """Timestamp string for log lines, e.g. [2026-05-01 07:31:04 ET]."""
    tz = app_tz()
    tz_abbr = now_app().strftime("%Z")
    return datetime.now(tz).strftime(f"[%Y-%m-%d %H:%M:%S {tz_abbr}]")


def local_to_utc(dt_string: str, tz_override: str | None = None) -> str:
    """Convert a local datetime string to UTC with Z suffix.

    Used when external APIs (YouTube, etc.) require UTC format.
    The input is the Presence's local time — "11am" means 11am in their timezone.

    Timezone resolution:
        1. tz_override (explicit IANA string, e.g. from Presence account)
        2. Presence cascade via presence_tz() → app_tz() → fallback

    Args:
        dt_string: ISO 8601 datetime, naive or with offset.
                   Naive = Presence local time. Offset-aware = used as-is.
        tz_override: Optional IANA timezone string from the Presence's settings.

    Returns:
        UTC string with Z suffix, e.g. '2026-05-22T15:00:00Z'
    """
    try:
        parsed = datetime.fromisoformat(dt_string)
        if parsed.tzinfo is None:
            # Naive datetime = Presence's local time. Resolve through cascade.
            local_tz = presence_tz(tz_override)
            parsed = parsed.replace(tzinfo=local_tz)
        utc_dt = parsed.astimezone(timezone.utc)
        return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, TypeError):
        return dt_string  # fallback — pass through as-is


def utc_to_local(dt_input, tz_override: str | None = None) -> datetime:
    """Convert a UTC datetime (string or object) to the Presence's local timezone.

    Used when displaying times to the user — calendar events, UI display, logs.
    The Presence's timezone determines what "local" means.

    Args:
        dt_input: UTC datetime — string (ISO 8601) or datetime object.
        tz_override: Optional IANA timezone string from the Presence's settings.

    Returns:
        Timezone-aware datetime in the Presence's local timezone.
    """
    local_tz = presence_tz(tz_override)
    if isinstance(dt_input, str):
        dt_input = datetime.fromisoformat(dt_input.replace('Z', '+00:00'))
    if dt_input.tzinfo is None:
        dt_input = dt_input.replace(tzinfo=timezone.utc)
    return dt_input.astimezone(local_tz)


def local_to_utc_dt(dt_input, tz_override: str | None = None) -> str:
    """Convert a datetime object (e.g. from DB TIMESTAMPTZ) to UTC Z string.

    Same as local_to_utc but accepts datetime objects directly.
    Used by schedulers that read timezone-aware datetimes from PostgreSQL.
    """
    if hasattr(dt_input, "astimezone"):
        utc_dt = dt_input.astimezone(timezone.utc)
        return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    if hasattr(dt_input, "isoformat"):
        return local_to_utc(dt_input.isoformat(), tz_override)
    return str(dt_input)
