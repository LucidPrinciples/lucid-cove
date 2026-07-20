"""
Video Processing Proxy Routes — pipecat-voice forwarding + social queue.

Proxy endpoints forward to pipecat-voice for GPU-heavy work (ffmpeg batch
processing, preview generation). After processing completes, draft entries
are inserted into social_queue for each platform the operator selected.

  video_pipeline.py   — cove-core logic (LLM analysis, NC WebDAV, file
                         listing, transcript CRUD, local file serving)
  video_processing.py — proxy + queue insertion (this file)

Both use the same /api/video prefix. No route conflicts — these are
POST endpoints with unique paths.
"""

import json
import logging
import os
from src.env import env

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.dashboard.routes.video_pipeline import pipecat_nc_headers

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/video", tags=["video-processing"])

# Route clip-render + enqueue to the Cove's OWN repo voice container, which the
# provisioner wires as VOICE_INTERNAL_URL (http://{cove}-voice:8300). Mirrors
# video_pipeline.py:38 — without this, process-moments/caption-full fall back to
# the legacy host:8300 box and break on any Cove that doesn't have one.
PIPECAT_URL = env("PIPECAT_URL") or env("VOICE_INTERNAL_URL") or "http://host.docker.internal:8300"

# Platform → preferred video format mapping
# Instagram Reels and Facebook Reels both use 9:16 vertical
PLATFORM_FORMATS = {
    "youtube": "vertical",
    "tiktok": "vertical",
    "x": "horizontal",
    "instagram": "vertical",
    "facebook": "vertical",
}


def _formats_for_platforms(platforms: list) -> list:
    """C3 #5 — the deduped, order-stable set of output formats the SELECTED
    platforms need. Empty/unknown selection → ["vertical"] (the safe default
    shape). Drives the render: no more always-both, no duration coupling."""
    seen, out = set(), []
    for p in (platforms or []):
        fmt = PLATFORM_FORMATS.get((p or "").strip().lower())
        if fmt and fmt not in seen:
            seen.add(fmt)
            out.append(fmt)
    return out or ["vertical"]


def _clip_window_seconds(clip: dict, moments_by_key: dict | None = None) -> tuple[float, float]:
    """Source-timeline window for a rendered clip.

    Prefer start/end on the processed payload (voice must stamp these). Fall back to
    the approved moments list from the request (same keys the renderer used). Last
    resort: start=0 + duration only — that mis-labels mid-talk clips and is logged.
    """
    moments_by_key = moments_by_key or {}
    start = clip.get("start_seconds")
    end = clip.get("end_seconds")
    try:
        if start is not None and end is not None:
            s, e = float(start), float(end)
            if e > s:
                return s, e
    except (TypeError, ValueError):
        pass
    key = (clip.get("moment_id"), clip.get("clip_type") or clip.get("type"))
    src = moments_by_key.get(key) or moments_by_key.get((clip.get("moment_id"), None))
    if src:
        try:
            s = float(src.get("start_seconds", 0) or 0)
            e = float(src.get("end_seconds", 0) or 0)
            if e > s:
                return s, e
        except (TypeError, ValueError):
            pass
    try:
        s = float(clip.get("start_seconds", 0) or 0)
    except (TypeError, ValueError):
        s = 0.0
    try:
        dur = float(clip.get("duration_seconds", 0) or 0)
    except (TypeError, ValueError):
        dur = 0.0
    return s, s + dur if dur > 0 else s


def _transcript_text_for_window(
    segments: list,
    start: float,
    end: float,
    *,
    min_overlap: float = 0.05,
) -> str:
    """Join transcript segment text that overlaps [start, end) on the source timeline.

    Overlap (not strict containment) so word/segment boundaries that slightly straddle
    the cut still contribute. Empty if nothing overlaps.
    """
    if not segments or end <= start:
        return ""
    parts: list[str] = []
    for seg in segments:
        try:
            seg_s = float(seg.get("start", 0) or 0)
            seg_e = float(seg.get("end", seg_s) or seg_s)
        except (TypeError, ValueError):
            continue
        if seg_e <= seg_s:
            continue
        overlap = min(seg_e, end) - max(seg_s, start)
        if overlap >= min_overlap:
            t = (seg.get("text") or "").strip()
            if t:
                parts.append(t)
    return " ".join(parts).strip()


@router.post("/process-moments")
async def process_moments(request: Request):
    """Process approved moments into finished videos, then queue for distribution.

    Body: { stem, crop_template, moments[], platforms[] }
    1. Proxies to pipecat-voice for ffmpeg processing
    2. On success, inserts draft entries into social_queue per platform per clip
    Returns: { processed[], errors[], manifest_file, queued_count }
    """
    body = await request.json()
    platforms = body.get("platforms", ["youtube"])
    stem = body.get("stem", "")

    # C3 #5 — the PLATFORM selection drives the formats (was: always both). Render
    # only the formats the chosen platforms actually need — deduped, order-stable.
    # No platform selected → a single vertical (the safe default shape). This kills
    # the wasted second render per clip and the "under 3 min" duration coupling.
    formats = _formats_for_platforms(platforms)
    body["formats"] = formats
    logger.info(f"Platforms {platforms} → rendering formats {formats}")

    # Forward to pipecat-voice for processing
    _nch = await pipecat_nc_headers(request)
    try:
        try:
            from src.dashboard.routes.video_jobs import set_phase
            set_phase("rendering")
        except Exception:
            pass
        async with httpx.AsyncClient(timeout=1800) as client:  # 30min for batch
            resp = await client.post(
                f"{PIPECAT_URL}/api/video/process-moments",
                json=body,
                headers=_nch,
            )
            raw = (resp.text or "").strip()
            if not raw:
                return JSONResponse(
                    {"error": (
                        f"process-moments empty response (HTTP {resp.status_code}). "
                        "Use /process-moments/start for long batches."
                    )},
                    status_code=502,
                )
            try:
                result = resp.json()
            except Exception:
                return JSONResponse(
                    {"error": f"process-moments non-JSON (HTTP {resp.status_code}): {raw[:240]}"},
                    status_code=502,
                )
            if resp.status_code != 200:
                return JSONResponse(result, status_code=resp.status_code)
    except httpx.TimeoutException:
        return JSONResponse({"error": "Processing timed out"}, status_code=504)
    except Exception as e:
        logger.error(f"process-moments proxy error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

    # Processing succeeded — load transcript for metadata generation, then queue
    processed = result.get("processed", [])
    queued_count = 0

    # Approved moments from the request — fallback when processed[] lacked a window
    # (older voice images) so metadata still keys off the cut the operator approved.
    moments_in = body.get("moments") or []
    moments_by_key: dict = {}
    for m in moments_in:
        if not isinstance(m, dict):
            continue
        mid = m.get("moment_id", m.get("id"))
        ctype = m.get("clip_type") or m.get("type")
        moments_by_key[(mid, ctype)] = m
        # Also index by moment_id alone for loose match
        moments_by_key.setdefault((mid, None), m)

    # Load transcript segments for extracting per-clip text
    transcript_segments = []
    if processed and stem:
        try:
            from src.dashboard.routes.video_pipeline import _read_video_json
            for tf in [f"{stem}-transcript-edited.json", f"{stem}-transcript.json"]:
                tdata = await _read_video_json(request, f"transcripts/{tf}")
                if tdata:
                    transcript_segments = tdata.get("segments", [])
                    break
        except Exception:
            pass

    if processed and platforms:
        try:
            from src.memory.database import get_db
            from src.dashboard.routes.social_templates import generate_platform_metadata
            from src.dashboard.routes.posting_identity import owner_id_from_request

            # Stamp the owning presence so the scheduler posts each card from
            # that presence's OWN account (per-presence posting credentials).
            owner_id = await owner_id_from_request(request)
            # CF-1: strict self-scope — stamp presence_id so the drafts appear
            # on the acting presence's board (NULL in single mode: unchanged).
            from src.dashboard.routes.action_board import _acting_presence_id
            _cf1_pid = await _acting_presence_id(request)
            from src.dashboard.routes.video_meta import resolve_video_meta
            _vm = await resolve_video_meta(owner_id=owner_id, request=request)

            async with get_db() as conn:
                # Index processed clips by (moment_id, clip_type, format).
                # NOTE: moment_id alone is NOT unique — one moment renders
                # multiple clip types (quote/story/thought) sharing the same
                # moment_id. Keying without clip_type collided and silently
                # dropped all but the last-rendered clip (bug fixed 2026-06-11).
                clips_by_key = {}
                for clip in processed:
                    key = (clip.get("moment_id"), clip.get("clip_type"),
                           clip.get("format", "vertical"))
                    clips_by_key[key] = clip

                # Unique (moment_id, clip_type) units, preserving order
                clip_units = list(dict.fromkeys(
                    (c.get("moment_id"), c.get("clip_type")) for c in processed
                ))

                # Sibling sizes + moment analysis so each platform draft is mixed
                # against the rest of the moment, not written in isolation.
                def _moment_context_for(m_id, c_type) -> str:
                    lines: list[str] = []
                    src = moments_by_key.get((m_id, c_type)) or moments_by_key.get((m_id, None)) or {}
                    for key in ("topic", "theme_tag", "hook", "reasoning", "content_type"):
                        val = (src.get(key) or "").strip() if isinstance(src.get(key), str) else ""
                        if val:
                            lines.append(f"{key}: {val[:400]}")
                    siblings = []
                    for oc in processed:
                        if oc.get("moment_id") != m_id:
                            continue
                        ot = oc.get("clip_type") or ""
                        if ot == c_type:
                            continue
                        lab = oc.get("label") or ot
                        dur = oc.get("duration_seconds") or 0
                        siblings.append(f"- {ot}: {lab} ({dur}s)")
                    # Also surface approved-but-not-yet-keyed siblings from moments_in
                    for m in moments_in:
                        if not isinstance(m, dict):
                            continue
                        mid = m.get("moment_id", m.get("id"))
                        if mid != m_id:
                            continue
                        ot = m.get("clip_type") or m.get("type") or ""
                        if not ot or ot == c_type:
                            continue
                        lab = m.get("label") or m.get("clip_label") or ot
                        dur = m.get("duration_seconds") or 0
                        entry = f"- {ot}: {lab} ({dur}s)"
                        if entry not in siblings:
                            siblings.append(entry)
                    if siblings:
                        lines.append("sibling sizes in this moment:")
                        lines.extend(siblings[:8])
                    return "\n".join(lines).strip()

                for m_id, c_type in clip_units:
                    # Extract transcript text for this clip's SOURCE window
                    any_clip = next(
                        (c for c in processed
                         if c.get("moment_id") == m_id and c.get("clip_type") == c_type),
                        None,
                    )
                    if not any_clip:
                        continue
                    clip_start, clip_end = _clip_window_seconds(any_clip, moments_by_key)
                    clip_text = _transcript_text_for_window(
                        transcript_segments, clip_start, clip_end,
                    )
                    if not clip_text:
                        # Last resort: label/topic — never silently use t=0 of the full talk
                        src_m = moments_by_key.get((m_id, c_type)) or moments_by_key.get((m_id, None)) or {}
                        clip_text = (
                            (src_m.get("topic") or src_m.get("clip_label") or "")
                            or any_clip.get("label", "")
                        )
                        logger.warning(
                            "No transcript overlap for clip m%s/%s window %.1f–%.1f — "
                            "metadata falling back to label/topic",
                            m_id, c_type, clip_start, clip_end,
                        )
                    else:
                        logger.info(
                            "Clip metadata window m%s/%s: %.1f–%.1f (%.0f chars)",
                            m_id, c_type, clip_start, clip_end, len(clip_text),
                        )

                    moment_ctx = _moment_context_for(m_id, c_type)

                    for platform in platforms:
                        # Find this clip in the platform's preferred format
                        pref_fmt = PLATFORM_FORMATS.get(platform, "vertical")
                        clip = clips_by_key.get((m_id, c_type, pref_fmt))
                        if not clip:
                            # Fall back to any available format for this clip
                            clip = next(
                                (c for c in processed
                                 if c.get("moment_id") == m_id and c.get("clip_type") == c_type),
                                None,
                            )
                        if not clip:
                            continue

                        # Generate platform-specific metadata via LLM
                        try:
                            meta = await generate_platform_metadata(
                                platform=platform,
                                clip_label=clip.get("label", "Untitled"),
                                clip_type=clip.get("clip_type", "thought"),
                                duration_seconds=clip.get("duration_seconds", 0),
                                transcript_text=clip_text,
                                video_meta=_vm,
                                request=request,
                                owner_id=owner_id,
                                moment_context=moment_ctx,
                            )
                        except Exception as me:
                            logger.warning(f"Metadata gen failed for {platform}: {me}")
                            meta = {"title": clip.get("label", "Untitled"), "description": "", "hashtags": "", "tags": []}

                        tags_json = json.dumps(meta.get("tags", []))

                        clip_format = clip.get("format", "vertical")
                        await conn.execute(
                            """INSERT INTO social_queue
                               (platform, title, description, hashtags, tags,
                                file_path, preview_path,
                                source_stem, moment_id, clip_type, clip_label,
                                duration_seconds, is_vertical, format, series, agent_id,
                                presence_id, status)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'draft')""",
                            (
                                platform,
                                meta.get("title", clip.get("label", "Untitled")),
                                meta.get("description", ""),
                                meta.get("hashtags", ""),
                                tags_json,
                                clip.get("nc_path", clip.get("filename", "")),
                                clip.get("preview_nc_path", ""),
                                stem,
                                clip.get("moment_id"),
                                clip.get("clip_type", ""),
                                clip.get("label", ""),
                                clip.get("duration_seconds", 0),
                                clip_format == "vertical",
                                clip_format,
                                f"moments-{stem}",
                                owner_id,
                                _cf1_pid if _cf1_pid else None,
                            ),
                        )
                        queued_count += 1
            unique_moments = len(set(c.get("moment_id") for c in processed))
            logger.info(
                f"Queued {queued_count} social drafts "
                f"({unique_moments} moments × {len(formats)} formats × {len(platforms)} platforms)"
            )
        except Exception as e:
            logger.error(f"social_queue insert failed: {e}")
            result["queue_error"] = str(e)

    # Mark processed clips in the moments JSON so the review page filters them out
    if processed and stem:
        try:
            from src.dashboard.routes.video_pipeline import (
                _pipecat_write_json, _read_video_json,
            )

            moments_data = await _read_video_json(request, f"transcripts/{stem}-moments.json")
            if moments_data:
                # Build set of processed (moment_id, clip_type) pairs
                processed_keys = {
                    (c.get("moment_id"), c.get("clip_type"))
                    for c in processed
                }

                # Mark matching clips as processed
                for moment in moments_data.get("moments", []):
                    for clip in moment.get("clips", []):
                        key = (moment.get("id"), clip.get("type"))
                        if key in processed_keys:
                            clip["processed"] = True

                # Write back via pipecat-voice (owns the :rw video mount)
                await _pipecat_write_json(f"transcripts/{stem}-moments.json", moments_data, _nch)
                logger.info(f"Marked {len(processed_keys)} clips as processed in {stem}-moments.json")
        except Exception as e:
            logger.warning(f"Failed to update moments JSON with processed status: {e}")

    result["queued_count"] = queued_count
    result["platforms"] = platforms
    return JSONResponse(result)


@router.post("/caption-full")
async def caption_full_video(request: Request):
    """Render full-length video with burnt-in captions. Proxies to pipecat-voice.

    After render (or skip), generates YouTube metadata from the transcript
    via LLM, renames the file to include the title, and inserts into
    social_queue with real metadata.

    Body: { stem, caption (optional), video_filter (optional) }
    Returns: { filename, nc_path, size_mb, duration_seconds, metadata }
    """
    body = await request.json()
    stem = body.get("stem", "")
    _nch = await pipecat_nc_headers(request)
    try:
        # Mark render phase when running under video_jobs (#A14 caption-full async).
        try:
            from src.dashboard.routes.video_jobs import set_phase
            set_phase("rendering")
        except Exception:
            pass
        async with httpx.AsyncClient(timeout=3600) as client:  # 1hr for long videos
            resp = await client.post(
                f"{PIPECAT_URL}/api/video/caption-full",
                json=body,
                headers=_nch,
            )
            # Empty body = gateway/proxy cut mid-render — never throw JSONDecodeError
            # up to the browser as "Unexpected end of JSON input".
            raw = (resp.text or "").strip()
            if not raw:
                return JSONResponse(
                    {"error": (
                        "Captioned full returned an empty response "
                        f"(HTTP {resp.status_code}). The render may still be running "
                        "or was cut by a proxy — check voice logs / shorts folder, "
                        "or re-run via /caption-full/start."
                    )},
                    status_code=502,
                )
            try:
                result = resp.json()
            except Exception:
                return JSONResponse(
                    {"error": f"Captioned full non-JSON response (HTTP {resp.status_code}): "
                              f"{raw[:240]}"},
                    status_code=502,
                )

            if resp.status_code == 200 and stem:
                # Generate YouTube metadata from transcript
                metadata = await _generate_video_metadata(stem, request)
                result["metadata"] = metadata

                # Rename file to include title (if metadata generated)
                if metadata.get("title"):
                    try:
                        rename_resp = await client.post(
                            f"{PIPECAT_URL}/api/video/rename-captioned",
                            json={"stem": stem, "title": metadata["title"]},
                            timeout=30,
                            headers=_nch,
                        )
                        if rename_resp.status_code == 200:
                            rename_data = rename_resp.json()
                            result["filename"] = rename_data["new_name"]
                            result["nc_path"] = rename_data["nc_path"]
                            logger.info(f"Renamed captioned full: {rename_data['new_name']}")
                        else:
                            logger.warning(f"Rename failed ({rename_resp.status_code}): {rename_resp.text}")
                    except Exception as e:
                        logger.warning(f"Rename request failed: {e}")

                # Insert into social_queue with real metadata
                title = metadata.get("title", f"{stem} — Full Video")
                description = metadata.get("description", "")
                hashtags = metadata.get("hashtags", "")
                tags_json = json.dumps(metadata.get("tags", []))

                try:
                    from src.memory.database import get_db
                    from src.dashboard.routes.social_templates import generate_platform_metadata
                    from src.dashboard.routes.posting_identity import owner_id_from_request
                    owner_id = await owner_id_from_request(request)
                    # CF-1: strict self-scope — stamp presence_id (NULL in single mode)
                    from src.dashboard.routes.action_board import _acting_presence_id
                    _cf1_pid = await _acting_presence_id(request)

                    # Full-length goes to every long-form home: YouTube (API
                    # upload) + X (manual post — over 140s needs Premium, posted
                    # natively via the card's copy-paste flow).
                    # X gets its own post text (240 chars, no links), generated
                    # from the YouTube summary — NOT the YouTube description.
                    try:
                        x_meta = await generate_platform_metadata(
                            platform="x",
                            clip_label=title,
                            clip_type="full",
                            duration_seconds=result.get("duration_seconds", 0),
                            transcript_text=description or title,
                            request=request,
                            owner_id=owner_id,
                        )
                    except Exception as xe:
                        logger.warning(f"X full metadata gen failed: {xe}")
                        x_meta = {"title": title, "description": "", "hashtags": "", "tags": []}

                    per_platform = {
                        "youtube": (title, description, hashtags, tags_json),
                        "x": (
                            x_meta.get("title") or title,
                            x_meta.get("description", ""),
                            x_meta.get("hashtags", ""),
                            json.dumps([]),
                        ),
                    }
                    async with get_db() as conn:
                        for full_platform, (p_title, p_desc, p_tags_str, p_tags_json) in per_platform.items():
                            await conn.execute(
                                """INSERT INTO social_queue
                                   (platform, title, description, hashtags, tags,
                                    file_path, preview_path,
                                    source_stem, clip_type, clip_label,
                                    duration_seconds, is_vertical, format, series, agent_id,
                                    presence_id, status)
                                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'draft')""",
                                (
                                    full_platform,
                                    p_title,
                                    p_desc,
                                    p_tags_str,
                                    p_tags_json,
                                    result.get("nc_path", ""),
                                    "",
                                    stem,
                                    "full",
                                    p_title,
                                    result.get("duration_seconds", 0),
                                    False,
                                    "horizontal",
                                    f"moments-{stem}",
                                    owner_id,
                                    _cf1_pid if _cf1_pid else None,
                                ),
                            )
                    logger.info(f"Queued captioned full (youtube + x): {title}")
                except Exception as e:
                    logger.warning(f"social_queue insert for captioned full failed: {e}")
                    result["queue_error"] = str(e)

            return JSONResponse(result, status_code=resp.status_code)
    except httpx.TimeoutException:
        return JSONResponse({"error": "Captioned video timed out"}, status_code=504)
    except Exception as e:
        logger.error(f"caption-full proxy error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


# Video folder — read via filesystem mount (read-only in cove agents)
_VIDEO_BASE = env("VIDEO_BASE_PATH", "/vault/AgentSkills/Content/video")


async def _generate_video_metadata(stem: str, request=None) -> dict:
    """Generate YouTube title, description, tags from the full transcript.

    Uses the cove-core model chain (same as moments analysis). Reads the transcript
    from the presence's NC (founder mount fallback) when a request is supplied.
    Returns: { title, description, hashtags, tags: [] }
    """
    # Load transcript — presence NC (multi-mode) or founder mount
    from src.dashboard.routes.video_pipeline import _read_video_json
    transcript_text = ""
    for tf in [f"{stem}-transcript-edited.json", f"{stem}-transcript.json"]:
        tdata = await _read_video_json(request, f"transcripts/{tf}") if request is not None else None
        if tdata:
            segments = tdata.get("segments", [])
            transcript_text = " ".join(s.get("text", "") for s in segments)
            logger.info(f"Loaded transcript for metadata: {tf} ({len(segments)} segments)")
            break

    if not transcript_text:
        logger.warning(f"No transcript found for {stem} — using generic metadata")
        return {"title": f"{stem} — Full Video", "description": "", "hashtags": "", "tags": []}

    # Truncate to ~4000 words to fit context
    words = transcript_text.split()
    if len(words) > 4000:
        transcript_text = " ".join(words[:4000]) + " [truncated]"

    try:
        from src.models.provider import get_model_client, _resolve_model_string
        from langchain_core.messages import SystemMessage, HumanMessage
        from src.dashboard.routes.video_meta import (
            build_full_video_system_prompt,
            resolve_video_meta,
        )

        _meta = await resolve_video_meta(request=request)
        system_prompt = build_full_video_system_prompt(_meta)

        for model_name in ["gemini-flash", "kimi-k2.5"]:
            try:
                provider, model_string = _resolve_model_string(model_name)
                client = get_model_client(model_name, temperature=0.4)
                messages = [
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=f"Generate YouTube metadata for this video transcript:\n\n{transcript_text}"),
                ]
                response = await client.ainvoke(messages)
                content = response.content.strip()
                # Extract JSON from response
                if "```json" in content:
                    content = content.split("```json")[1].split("```")[0].strip()
                elif "```" in content:
                    content = content.split("```")[1].split("```")[0].strip()
                metadata = json.loads(content)
                logger.info(f"Video metadata generated via {model_name}: {metadata.get('title', '')[:60]}")
                return metadata
            except Exception as e:
                logger.warning(f"Metadata generation failed with {model_name}: {e}")
                continue

        logger.warning(f"All models failed for metadata generation — using generic")
        return {"title": f"{stem} — Full Video", "description": "", "hashtags": "", "tags": []}

    except ImportError as e:
        logger.warning(f"Model imports failed: {e}")
        return {"title": f"{stem} — Full Video", "description": "", "hashtags": "", "tags": []}


@router.get("/caption-full-status")
async def caption_full_status(request: Request, stem: str = ""):
    """Check if a captioned full-length video already exists for this stem.
    Checks pipecat filesystem (source of truth) first, DB as fallback."""
    if not stem:
        return JSONResponse({"exists": False})
    # Primary check: ask pipecat if the file exists on disk
    try:
        _nch = await pipecat_nc_headers(request)
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                f"{PIPECAT_URL}/api/video/caption-full-exists",
                params={"stem": stem},
                headers=_nch,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("exists"):
                    return JSONResponse({"exists": True})
    except Exception:
        pass  # Fall through to DB check
    # Fallback: check social_queue DB
    # CF-1: left unscoped (processor path) — existence check per stem, not a
    # queue listing; scoping it would re-render fulls another presence made.
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            result = await conn.execute(
                """SELECT id FROM social_queue
                   WHERE source_stem = %s AND clip_type = 'full'
                   AND status != 'cancelled'
                   LIMIT 1""",
                (stem,),
            )
            row = await result.fetchone()
            return JSONResponse({"exists": row is not None})
    except Exception:
        return JSONResponse({"exists": False})


@router.post("/generate-preview")
async def generate_preview(request: Request):
    """Generate a low-res preview for a video. Proxies to pipecat-voice."""
    body = await request.json()
    _nch = await pipecat_nc_headers(request)
    try:
        async with httpx.AsyncClient(timeout=1200) as client:
            resp = await client.post(
                f"{PIPECAT_URL}/api/video/preview",
                json=body,
                headers=_nch,
            )
            return JSONResponse(resp.json(), status_code=resp.status_code)
    except httpx.TimeoutException:
        return JSONResponse({"error": "Preview generation timed out (>20min)"}, status_code=504)
    except Exception as e:
        logger.error(f"generate-preview proxy error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)
