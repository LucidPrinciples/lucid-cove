"""
Attune — fetch a Selection's echo analysis and build the Experience.

selection.py picks WHICH echo; this fetches that echo's analysis file from
the CDN and hands it to sonic.py to decode into a felt Experience (sound +
words). Network is isolated here so sonic.py stays pure and testable.

The fetcher is injectable: the default uses the stdlib (no extra dependency),
hosts may inject an async httpx client, and tests inject a local fixture. A
failed/empty fetch yields a degraded Experience (truth-guard), never a faked one.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import urllib.request
from typing import Awaitable, Callable, Optional, Union

from .reference import Reference
from .selection import Selection
from .sonic import Experience, assemble_experience, build_sonic_arc, decode_frames

FetchJsonFn = Callable[[str], Union[dict, Awaitable[dict]]]


def _default_fetch_json(url: str, timeout: float = 10.0) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "lucid-tuner-protocol"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (https CDN)
        return json.loads(resp.read().decode("utf-8"))


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


def analysis_url_for_selection(selection: Selection, reference: Optional[Reference] = None) -> str:
    """Resolve the echo's analysis-JSON URL. Prefer the reference's configured
    pattern; fall back to swapping the audio suffix (CDN convention)."""
    if reference is not None:
        url = reference.analysis_url_for(selection.signal_type, selection.echo_filename)
        if url:
            return url
    return selection.echo_audio_url.replace(".mp3", "_analysis.json")


async def attune(
    selection: Selection,
    reference: Optional[Reference] = None,
    fetch_json: FetchJsonFn = _default_fetch_json,
) -> Experience:
    """Fetch the selected echo's analysis and build the Experience.

    A failed fetch returns a degraded Experience (attunement_status =
    'incomplete') rather than raising — the caller's truth-guard handles it.
    """
    url = analysis_url_for_selection(selection, reference)
    try:
        echo_file = await _maybe_await(fetch_json(url)) or {}
    except Exception:
        echo_file = {}

    aa = echo_file.get("audio_analysis") if isinstance(echo_file.get("audio_analysis"), dict) else echo_file
    aa = aa or {}
    envelope = decode_frames(aa.get("frames"), aa.get("frameCount"))
    arc = build_sonic_arc(
        envelope,
        sample_rate=aa.get("sampleRate", 10),
        duration=aa.get("duration", 0),
        onsets=aa.get("onsets", []),
    )
    return assemble_experience(echo_file, arc, analysis_url=url)


def attune_sync(
    selection: Selection,
    reference: Optional[Reference] = None,
    fetch_json: FetchJsonFn = _default_fetch_json,
) -> Experience:
    """Synchronous wrapper for callers without an event loop."""
    return asyncio.run(attune(selection, reference, fetch_json))
