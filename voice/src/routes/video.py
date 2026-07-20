"""Video routes — frame extraction, preview generation, moments processing, streaming."""
import asyncio
import json
import logging
import os
import subprocess
import tempfile

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, FileResponse

from src.voice_common import (
    VIDEO_MOUNT, NC_VIDEO_PATH,
    NEXTCLOUD_URL, NEXTCLOUD_ADMIN_USER, NEXTCLOUD_ADMIN_PASSWORD,
    _nc_scan, NCSession, resolve_video_source, publish_video_output,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _square_crop_expr(src_w, src_h, src_x, src_y) -> str:
    """Clamped square-crop filter that can NEVER exceed the (post-rotation) source.

    The crop-position tool selects a square in source pixels. If that square was sized for
    a different resolution/orientation than the actual video (e.g. a 2700px square from a
    2160-tall 4K clip), an unclamped `crop=w:h:x:y` runs off the frame, so ffmpeg produces
    ZERO frames and libx264 dies with 'could not open encoder before EOF'. Clamp the side to
    min(requested, iw, ih) and keep the offset in-frame. iw/ih (and ow/oh) are evaluated by
    ffmpeg AFTER auto-rotation, so this is correct for rotated iPhone footage too."""
    # NB ffmpeg's expression min() takes EXACTLY TWO args (A16 forensics, 2026-07-03:
    # 'Missing )' or too many args in min(1000,1000,iw,ih)' killed the whole filter
    # graph at init and the encoder never opened). Fold the two constants in Python,
    # nest the runtime pair.
    side_req = min(int(src_w), int(src_h))
    side = f"min({side_req}\\,min(iw\\,ih))"
    x = f"max(0\\,min({int(src_x)}\\,iw-ow))"
    y = f"max(0\\,min({int(src_y)}\\,ih-oh))"
    return f"crop={side}:{side}:{x}:{y}"


@router.get("/api/video/frame")
async def extract_frame(request: Request, filename: str = "", t: float = -1):
    """Extract a single frame from a video file as JPEG.

    Query params:
        filename: Video filename (looked up in /video/processing/ or /video/inbox/)
        t: Timestamp in seconds (-1 = mid-point)

    Returns: JPEG image
    """
    if not filename:
        return JSONResponse({"error": "No filename"}, status_code=400)

    # Per-presence NC session (cove-core injects X-NC-* headers); None = local mount.
    nc = NCSession.from_request(request)

    # Find the video (pulls from the presence's NC when nc is set).
    video_path = await resolve_video_source(filename, nc)

    if not video_path:
        return JSONResponse({"error": f"Video not found: {filename}"}, status_code=404)

    # Get duration if we need mid-point
    if t < 0:
        try:
            probe = subprocess.run(
                ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                 "-of", "csv=p=0", video_path],
                capture_output=True, text=True, timeout=10,
            )
            duration = float(probe.stdout.strip())
            t = duration / 2
        except Exception:
            t = 10  # fallback

    # Extract frame via ffmpeg
    import subprocess
    try:
        result = subprocess.run(
            ["ffmpeg", "-ss", str(t), "-i", video_path,
             "-vframes", "1", "-q:v", "2", "-f", "image2", "pipe:1"],
            capture_output=True, timeout=30,
        )
        if result.returncode != 0 or not result.stdout:
            return JSONResponse({"error": "Frame extraction failed"}, status_code=500)

        return Response(content=result.stdout, media_type="image/jpeg")
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/video/info")
async def video_info(request: Request, filename: str = ""):
    """Get video dimensions and duration for crop positioning."""
    if not filename:
        return JSONResponse({"error": "No filename"}, status_code=400)

    # Per-presence NC session (cove-core injects X-NC-* headers); None = local mount.
    nc = NCSession.from_request(request)

    video_path = await resolve_video_source(filename, nc)

    if not video_path:
        return JSONResponse({"error": f"Video not found: {filename}"}, status_code=404)

    import subprocess
    try:
        # Get dimensions + display rotation (phone clips store landscape + a rotate flag)
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-select_streams", "v:0",
             "-show_entries", "stream=width,height,duration",
             "-show_entries", "stream_side_data=rotation",
             "-show_entries", "stream_tags=rotate",
             "-show_entries", "format=duration",
             "-of", "json", video_path],
            capture_output=True, text=True, timeout=10,
        )
        import json as json_mod
        info = json_mod.loads(probe.stdout)
        stream = info.get("streams", [{}])[0]
        fmt = info.get("format", {})

        w = stream.get("width", 0) or 0
        h = stream.get("height", 0) or 0

        # Honor display rotation. ffmpeg auto-rotates frames and renders, so the crop
        # page must lay out against the DISPLAYED size, not the stored size — otherwise
        # a portrait phone clip (stored landscape, rotate ±90) gets squished.
        rot = 0
        try:
            for sd in (stream.get("side_data_list") or []):
                if sd.get("rotation") is not None:
                    rot = int(sd["rotation"])
            tag_rot = (stream.get("tags") or {}).get("rotate")
            if tag_rot is not None:
                rot = int(tag_rot)
        except Exception:
            rot = 0
        if abs(rot) % 180 == 90:
            w, h = h, w

        return {
            "filename": filename,
            "width": w,
            "height": h,
            "rotation": rot,
            "duration": float(fmt.get("duration", stream.get("duration", 0))),
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/video/preview")
async def generate_preview(request: Request):
    """Generate a trimmed 360p preview from the edited transcript.

    Compares original vs edited transcript to find deletion boundaries,
    then uses ffmpeg concat demuxer to stitch kept ranges into a preview.
    This is what the moments review page scrubs against.

    Body: { "filename": "IMG_7129.MOV" }
    Looks in processing/, inbox/, raw/ for source video.
    Reads both {stem}-transcript.json and {stem}-transcript-edited.json.
    Writes {stem}-preview.mp4 to transcripts/.
    """
    body = await request.json()
    filename = body.get("filename", "").strip()
    if not filename:
        return JSONResponse({"error": "No filename"}, status_code=400)

    # Per-presence NC session (cove-core injects X-NC-* headers); None = local mount.
    nc = NCSession.from_request(request, body)

    # Find source video (pulls from the presence's NC when nc is set).
    video_path = await resolve_video_source(filename, nc)

    if not video_path:
        return JSONResponse({"error": f"Video not found: {filename}"}, status_code=404)

    stem = os.path.splitext(filename)[0]
    preview_filename = f"{stem}-preview.mp4"
    preview_tmp = f"/tmp/{preview_filename}"

    # Read both transcripts to find deletion boundaries.
    # With nc, pull each from the presence's NC into scratch; else read VIDEO_MOUNT.
    orig_tf = f"{stem}-transcript.json"
    edit_tf = f"{stem}-transcript-edited.json"
    if nc is not None:
        original_path = os.path.join("/tmp/cove-video", nc.user, "transcripts", orig_tf)
        edited_path = os.path.join("/tmp/cove-video", nc.user, "transcripts", edit_tf)
        if not os.path.isfile(original_path):
            await nc.pull(f"transcripts/{orig_tf}", original_path)
        if not os.path.isfile(edited_path):
            await nc.pull(f"transcripts/{edit_tf}", edited_path)
    else:
        original_path = os.path.join(VIDEO_MOUNT, "transcripts", orig_tf)
        edited_path = os.path.join(VIDEO_MOUNT, "transcripts", edit_tf)

    ranges = None  # None = full video, list = trimmed ranges

    if os.path.isfile(edited_path) and os.path.isfile(original_path):
        with open(original_path) as f:
            original = json.load(f)
        with open(edited_path) as f:
            edited = json.load(f)

        orig_segs = original.get("segments", [])
        edit_segs = edited.get("segments", [])

        if orig_segs and edit_segs:
            # Build set of kept timestamps from edited transcript
            kept_times = set()
            for seg in edit_segs:
                s = seg.get("start")
                if s is not None:
                    kept_times.add(round(s, 3))

            # Walk original segments, mark each as kept or deleted
            # Then find contiguous kept ranges
            ranges = []
            current_start = None
            current_end = None

            for seg in orig_segs:
                s, e = seg.get("start"), seg.get("end")
                if s is None or e is None:
                    continue

                if round(s, 3) in kept_times:
                    # This word was kept
                    if current_start is None:
                        current_start = s
                    current_end = e
                else:
                    # This word was deleted — close current range if open
                    if current_start is not None:
                        ranges.append((current_start, current_end))
                        current_start = None
                        current_end = None

            # Close final range
            if current_start is not None:
                ranges.append((current_start, current_end))

            # If nothing was actually deleted, skip trimming
            if len(ranges) == len(orig_segs) or not ranges:
                ranges = None
                logger.info(f"No deletions detected for {stem}, generating full preview")
            else:
                logger.info(f"Found {len(ranges)} kept ranges from {len(orig_segs)} original segments")
    elif not os.path.isfile(edited_path):
        logger.info(f"No edited transcript for {stem}, generating full preview")

    try:
        import subprocess as sp
        import shutil

        if ranges:
            # Use ffmpeg concat demuxer — scales to any number of segments.
            # Step 1: Extract each range as a temp segment file
            seg_dir = f"/tmp/preview_segs_{stem}"
            os.makedirs(seg_dir, exist_ok=True)

            seg_files = []
            for i, (s, e) in enumerate(ranges):
                seg_file = os.path.join(seg_dir, f"seg_{i:04d}.mp4")
                extract_cmd = [
                    "ffmpeg", "-y",
                    "-ss", f"{s:.3f}", "-to", f"{e:.3f}",
                    "-i", video_path,
                    "-vf", "scale=-2:360",
                    "-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p",
                    "-preset", "ultrafast", "-crf", "35",
                    "-c:a", "aac", "-b:a", "64k", "-ac", "1",
                    "-movflags", "+faststart",
                    seg_file,
                ]
                result = sp.run(extract_cmd, capture_output=True, text=True, timeout=300)
                if result.returncode != 0:
                    # Clean up and fail
                    shutil.rmtree(seg_dir, ignore_errors=True)
                    return JSONResponse(
                        {"error": f"Segment {i} extraction failed: {result.stderr[-300:]}"},
                        status_code=500,
                    )
                seg_files.append(seg_file)

            # Step 2: Write concat list file
            concat_list = os.path.join(seg_dir, "concat.txt")
            with open(concat_list, "w") as cl:
                for sf in seg_files:
                    cl.write(f"file '{sf}'\n")

            # Step 3: Concat segments into final preview
            concat_cmd = [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", concat_list,
                "-c", "copy",
                "-movflags", "+faststart",
                preview_tmp,
            ]
            result = sp.run(concat_cmd, capture_output=True, text=True, timeout=300)

            # Clean up temp segments
            shutil.rmtree(seg_dir, ignore_errors=True)

            if result.returncode != 0:
                return JSONResponse(
                    {"error": f"Concat failed: {result.stderr[-500:]}"},
                    status_code=500,
                )
        else:
            # No edits or no deletions — full video preview
            cmd = [
                "ffmpeg", "-y", "-i", video_path,
                "-vf", "scale=-2:360",
                "-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p",
                "-preset", "ultrafast", "-crf", "35",
                "-c:a", "aac", "-b:a", "64k", "-ac", "1",
                "-movflags", "+faststart",
                "-threads", "4",
                preview_tmp,
            ]
            result = sp.run(cmd, capture_output=True, text=True, timeout=900)
            if result.returncode != 0:
                return JSONResponse(
                    {"error": f"ffmpeg failed: {result.stderr[-500:]}"},
                    status_code=500,
                )

        # Stage locally, then publish to the presence's NC (WebDAV) or the local
        # mount (publish_video_output triggers the NC scan on the mount path).
        local_out_dir = "/tmp/cove-out"
        os.makedirs(local_out_dir, exist_ok=True)
        preview_local = os.path.join(local_out_dir, preview_filename)
        shutil.move(preview_tmp, preview_local)
        size_mb = os.path.getsize(preview_local) / (1024 * 1024)
        await publish_video_output(preview_local, f"transcripts/{preview_filename}", nc, "video/mp4")
        preview_nc_path = f"{NC_VIDEO_PATH}/transcripts/{preview_filename}"
        trimmed_info = f" (trimmed: {len(ranges)} ranges)" if ranges else " (full)"
        logger.info(f"Preview generated: {preview_filename} ({size_mb:.1f} MB){trimmed_info}")

        return JSONResponse({
            "preview_file": preview_filename,
            "size_mb": round(size_mb, 1),
            "nc_path": preview_nc_path,
            "trimmed": bool(ranges),
            "segment_count": len(ranges) if ranges else 0,
        })

    except sp.TimeoutExpired:
        return JSONResponse({"error": "Preview generation timed out"}, status_code=504)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/video/process-moments")
async def process_moments(request: Request):
    """Process approved moments into finished videos.

    The final pipeline step. Takes crop template + caption style + moment list.
    For each moment: cut → crop → pad → video treatment → audio treatment →
    burn word-level captions → write finished video via NC WebDAV.

    Body: {
        "stem": "IMG_7129",
        "crop_template": { src_x, src_y, src_w, src_h, out_w, out_h, bar_top_px, bar_bottom_px, ... },
        "moments": [ { start_seconds, end_seconds, moment_id, clip_type, clip_label, ... } ]
    }

    Returns: { "processed": [...], "manifest_file": "..." }
    """
    import subprocess
    import json as json_mod

    body = await request.json()
    stem = body.get("stem", "").strip()
    crop = body.get("crop_template") or {}
    moments = body.get("moments", [])

    if not stem or not moments:
        return JSONResponse({"error": "stem and moments required"}, status_code=400)

    # Per-presence NC session (cove-core injects X-NC-* headers); None = local mount.
    nc = NCSession.from_request(request, body)

    # Find source video (pulls from the presence's NC when nc is set).
    video_path = await resolve_video_source(f"{stem}.MOV", nc)
    if not video_path:
        for ext in [".mp4", ".mov", ".mkv"]:
            video_path = await resolve_video_source(f"{stem}{ext}", nc)
            if video_path:
                break
    if not video_path:
        return JSONResponse({"error": f"Source video not found: {stem}"}, status_code=404)

    # Load edited transcript for captions (prefer edited, fall back to original).
    transcript_segments = []
    for tf in [f"{stem}-transcript-edited.json", f"{stem}-transcript.json"]:
        tp = None
        if nc is not None:
            _local = os.path.join("/tmp/cove-video", nc.user, "transcripts", tf)
            if os.path.isfile(_local) or await nc.pull(f"transcripts/{tf}", _local):
                tp = _local
        else:
            _cand = os.path.join(VIDEO_MOUNT, "transcripts", tf)
            if os.path.isfile(_cand):
                tp = _cand
        if tp:
            with open(tp) as f:
                tdata = json_mod.load(f)
                transcript_segments = tdata.get("segments", [])
            logger.info(f"Loaded transcript for captions: {tf} ({len(transcript_segments)} segments)")
            break

    # Extract caption template and video filter
    caption = crop.get("caption", {})
    video_filter = crop.get("video_filter", "natural")
    border_enabled = crop.get("border_enabled", True)

    # Video filter presets — Test Round 1 (see moments-look-spec.md)
    VIDEO_FILTERS = {
        "natural":   "eq=contrast=1.10:brightness=-0.02:saturation=0.95",
        "rich":      "eq=contrast=1.12:brightness=-0.05:saturation=0.78,curves=all='0/0.035 0.5/0.5 1/0.965'",
        "cinematic": "eq=contrast=1.18:brightness=-0.04:saturation=0.88,colortemperature=temperature=6200",
    }
    vf_color = VIDEO_FILTERS.get(video_filter, VIDEO_FILTERS["natural"])
    out_w = crop.get("out_w", 2160)
    out_h = crop.get("out_h", 3840)
    bar_top = crop.get("bar_top_px", 600)
    bar_bot = crop.get("bar_bottom_px", 1080)

    # Crop source coordinates
    src_x = crop.get("src_x", 0)
    src_y = crop.get("src_y", 0)
    src_w = crop.get("src_w", 2160)
    src_h = crop.get("src_h", 2160)

    # Debug: log crop coordinates for off-center investigation
    video_w = crop.get("video_w", 0)
    video_h = crop.get("video_h", 0)
    logger.info(
        f"CROP DEBUG: src video={video_w}x{video_h}, "
        f"crop={src_w}x{src_h}@({src_x},{src_y}), "
        f"crop_center=({src_x + src_w//2},{src_y + src_h//2}), "
        f"video_center=({video_w//2},{video_h//2}), "
        f"offset=({src_x + src_w//2 - video_w//2},{src_y + src_h//2 - video_h//2})"
    )

    # Multi-format: render each requested format per moment
    # Formats: vertical (9:16 w/ bars), horizontal (16:9), square (1:1)
    formats = body.get("formats", ["vertical"])

    processed = []
    errors = []

    for moment in moments:
        m_id = moment.get("moment_id", 0)
        m_type = moment.get("clip_type", "thought")
        m_label = moment.get("clip_label", f"moment-{m_id}")
        start = moment.get("start_seconds", 0)
        end = moment.get("end_seconds", 0)
        duration = end - start

        if duration <= 0:
            errors.append({"moment_id": m_id, "error": "Invalid time range"})
            continue

        safe_label = m_label.replace(" ", "-").replace("'", "")[:40]

        # C3 #5 — every clip renders EXACTLY the requested formats, regardless of
        # duration. The old "under 180s → both, else horizontal-only" duration rule
        # is gone: the platform selection (which produced `formats`) is the single
        # source of truth for output shape now.
        moment_formats = formats

        for fmt in moment_formats:
            # Format-specific output dimensions
            if fmt == "vertical":
                fmt_out_w = out_w       # 2160
                fmt_out_h = out_h       # 3840
                fmt_bar_top = bar_top if border_enabled else 0
                fmt_bar_bot = bar_bot if border_enabled else 0
                suffix = ""
            elif fmt == "horizontal":
                fmt_out_w = 3840
                fmt_out_h = 2160
                # Pillarboxed: maximize square, no top bar, bottom bar for captions
                fmt_bar_top = 0
                h_square = int(fmt_out_h * 0.75)     # 1620
                fmt_bar_bot = fmt_out_h - h_square    # 540
                suffix = "-horiz"
            elif fmt == "square":
                fmt_out_w = 2160
                fmt_out_h = 2160
                fmt_bar_top = 0
                fmt_bar_bot = 0
                suffix = "-square"
            else:
                continue

            out_name = f"{stem}-m{m_id}-{m_type}-{safe_label}{suffix}.mp4"
            out_tmp = f"/tmp/{out_name}"

            try:
                # Build video filter chain per format
                if fmt == "vertical" and border_enabled:
                    # Crop → scale to square → pad with black bars
                    video_h = fmt_out_h - fmt_bar_top - fmt_bar_bot
                    vf = (
                        f"{_square_crop_expr(src_w, src_h, src_x, src_y)},"
                        f"scale={fmt_out_w}:{video_h},"
                        f"pad={fmt_out_w}:{fmt_out_h}:0:{fmt_bar_top}:black,"
                        f"{vf_color}"
                    )
                elif fmt == "vertical":
                    # Vertical without border — full 9:16 crop
                    vf = (
                        f"{_square_crop_expr(src_w, src_h, src_x, src_y)},"
                        f"scale={fmt_out_w}:{fmt_out_h},"
                        f"{vf_color}"
                    )
                elif fmt == "horizontal":
                    # Pillarboxed: same square crop as vertical, placed in 16:9 frame
                    h_pad_left = (fmt_out_w - h_square) // 2
                    if border_enabled and src_w > 0:
                        vf = (
                            f"{_square_crop_expr(src_w, src_h, src_x, src_y)},"
                            f"scale={h_square}:{h_square},"
                            f"pad={fmt_out_w}:{fmt_out_h}:{h_pad_left}:{fmt_bar_top}:black,"
                            f"{vf_color}"
                        )
                    else:
                        vf = (
                            f"crop=min(iw\\,ih):min(iw\\,ih),"
                            f"scale={h_square}:{h_square},"
                            f"pad={fmt_out_w}:{fmt_out_h}:{h_pad_left}:{fmt_bar_top}:black,"
                            f"{vf_color}"
                        )
                elif fmt == "square":
                    # Same 1:1 crop as vertical → scale to 1080×1080
                    vf = (
                        f"{_square_crop_expr(src_w, src_h, src_x, src_y)},"
                        f"scale={fmt_out_w}:{fmt_out_h},"
                        f"{vf_color}"
                    )

                af = (
                    "highpass=f=80,"
                    "lowpass=f=12000,"
                    "acompressor=threshold=-20dB:ratio=3:attack=5:release=50,"
                    "loudnorm=I=-14:TP=-1:LRA=11"
                )

                # Generate ASS subtitle file
                ass_path = None
                if transcript_segments and caption:
                    ass_path = _generate_ass_subtitles(
                        segments=transcript_segments,
                        start_time=start,
                        end_time=end,
                        caption_style=caption,
                        format_name=fmt,
                        out_w=fmt_out_w,
                        out_h=fmt_out_h,
                        bar_top=fmt_bar_top,
                        bar_bot=fmt_bar_bot,
                        moment_id=m_id,
                        stem=stem,
                        content_w=h_square if fmt == "horizontal" else 0,
                    )
                    if ass_path:
                        vf += f",ass={ass_path}"

                cmd = [
                    "ffmpeg", "-y",
                    "-ss", str(start), "-to", str(end),
                    "-i", video_path,
                    # Map only the real video + audio. iPhone clips carry extra
                    # timecode/motion DATA tracks that derail default stream selection.
                    "-map", "0:v:0", "-map", "0:a:0?",
                    "-vf", vf,
                    "-af", af,
                    # iPhone 4K is variable-frame-rate; force constant fps + resampled
                    # audio so the track doesn't drift out of sync on the re-encode.
                    "-vsync", "cfr", "-r", "30",
                    "-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p",
                    "-preset", "medium", "-crf", "18",
                    "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
                    "-movflags", "+faststart",
                    out_tmp,
                ]

                logger.info(f"Processing m{m_id} ({m_type}) [{fmt}]: {start:.1f}s → {end:.1f}s")
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

                if result.returncode != 0:
                    errors.append({"moment_id": m_id, "format": fmt, "error": result.stderr[-500:]})
                    logger.error(f"m{m_id} [{fmt}] ffmpeg failed: {result.stderr[:200]}")
                    continue

                # Stage the finished video locally (kept for the preview step below),
                # then publish to the presence's NC via WebDAV, or the local mount.
                import shutil
                local_out_dir = "/tmp/cove-out"
                os.makedirs(local_out_dir, exist_ok=True)
                final_path = os.path.join(local_out_dir, out_name)
                shutil.move(out_tmp, final_path)
                size_mb = os.path.getsize(final_path) / (1024 * 1024)
                wrote = await publish_video_output(final_path, f"shorts/{out_name}", nc, "video/mp4")
                nc_path = f"{NC_VIDEO_PATH}/shorts/{out_name}"

                # Generate preview
                preview_name = out_name.replace(".mp4", "-preview.mp4")
                preview_tmp = f"/tmp/{preview_name}"
                preview_wrote = False
                try:
                    is_tall = fmt_out_h > fmt_out_w
                    preview_scale = "480:-2" if is_tall else "-2:480"
                    preview_cmd = [
                        "ffmpeg", "-y", "-i", final_path,
                        "-vf", f"scale={preview_scale}",
                        "-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p",
                        "-preset", "fast", "-crf", "28",
                        "-c:a", "aac", "-b:a", "64k",
                        "-movflags", "+faststart",
                        preview_tmp,
                    ]
                    preview_result = subprocess.run(
                        preview_cmd, capture_output=True, text=True, timeout=120,
                    )
                    if preview_result.returncode == 0:
                        preview_final = os.path.join(local_out_dir, preview_name)
                        shutil.move(preview_tmp, preview_final)
                        preview_wrote = await publish_video_output(preview_final, f"shorts/{preview_name}", nc, "video/mp4")
                        logger.info(f"Preview m{m_id} [{fmt}]: {preview_name}")
                    else:
                        logger.warning(f"Preview ffmpeg failed m{m_id} [{fmt}]")
                except Exception as pe:
                    logger.warning(f"Preview failed m{m_id} [{fmt}]: {pe}")
                if os.path.isfile(preview_tmp):
                    os.remove(preview_tmp)
                if ass_path and os.path.isfile(ass_path):
                    os.remove(ass_path)

                if wrote:
                    processed.append({
                        "moment_id": m_id,
                        "clip_type": m_type,
                        "label": m_label,
                        "format": fmt,
                        "filename": out_name,
                        "preview_filename": preview_name if preview_wrote else None,
                        "size_mb": round(size_mb, 1),
                        # Window on the source timeline — required so social metadata
                        # pulls transcript for THIS clip, not the first N seconds of the
                        # full talk (book intro vs later agent-memory section).
                        "start_seconds": round(float(start or 0), 2),
                        "end_seconds": round(float(end or 0), 2),
                        "duration_seconds": round(duration, 1),
                        "nc_path": nc_path,
                        "preview_nc_path": (
                            f"{NC_VIDEO_PATH}/shorts/{preview_name}"
                            if preview_wrote else None
                        ),
                        "vertical": fmt == "vertical",
                    })
                    logger.info(f"m{m_id} [{fmt}] done: {out_name} ({size_mb:.1f} MB)")
                else:
                    errors.append({"moment_id": m_id, "format": fmt, "error": "NC write failed"})

            except subprocess.TimeoutExpired:
                errors.append({"moment_id": m_id, "format": fmt, "error": "Timed out"})
            except Exception as e:
                errors.append({"moment_id": m_id, "format": fmt, "error": str(e)})
                logger.error(f"m{m_id} [{fmt}] error: {e}")

    # Write manifest
    manifest = {
        "stem": stem,
        "source_video": os.path.basename(video_path),
        "processed": processed,
        "errors": errors,
        "crop_template": crop,
    }
    manifest_name = f"{stem}-moments-processed.json"
    manifest_json = json_mod.dumps(manifest, indent=2, ensure_ascii=False)
    os.makedirs("/tmp/cove-out", exist_ok=True)
    manifest_local = os.path.join("/tmp/cove-out", manifest_name)
    with open(manifest_local, "w") as mf:
        mf.write(manifest_json)
    # Publish to the presence's NC (WebDAV) or the local mount; publish_video_output
    # triggers the NC scan on the mount path.
    await publish_video_output(manifest_local, f"shorts/{manifest_name}", nc, "application/json")

    logger.info(
        f"Moments processing complete: {len(processed)} done, {len(errors)} errors"
    )

    return JSONResponse({
        "processed": processed,
        "errors": errors,
        "manifest_file": manifest_name,
    })


@router.get("/api/video/caption-full-exists")
async def caption_full_exists(request: Request, stem: str = ""):
    """Check if a captioned full-length video already exists on disk."""
    if not stem:
        return JSONResponse({"exists": False})

    # Per-presence NC session (cove-core injects X-NC-* headers); None = local mount.
    nc = NCSession.from_request(request)

    if nc is not None:
        # No WebDAV listing helper, so we can only check the plain name (the
        # title-renamed glob isn't expressible over pull/push). Pull it to scratch.
        plain = f"{stem}-captioned.mp4"
        local = os.path.join("/tmp/cove-video", nc.user, "shorts", plain)
        if os.path.isfile(local) or await nc.pull(f"shorts/{plain}", local):
            size_mb = os.path.getsize(local) / (1024 * 1024)
            return JSONResponse({"exists": True, "filename": plain, "size_mb": round(size_mb, 1)})
        return JSONResponse({"exists": False})

    import glob as glob_mod
    shorts_dir = os.path.join(VIDEO_MOUNT, "shorts")
    # Check plain name first, then title-renamed pattern
    path = os.path.join(shorts_dir, f"{stem}-captioned.mp4")
    if not os.path.isfile(path):
        matches = glob_mod.glob(os.path.join(shorts_dir, f"{stem}-*-captioned.mp4"))
        if matches:
            path = matches[0]
        else:
            return JSONResponse({"exists": False})
    size_mb = os.path.getsize(path) / (1024 * 1024)
    return JSONResponse({"exists": True, "filename": os.path.basename(path), "size_mb": round(size_mb, 1)})


@router.post("/api/video/rename-captioned")
async def rename_captioned(request: Request):
    """Rename a captioned full-length video to include the title.

    Body: { "stem": "IMG_7129", "title": "The Lucid Cove AI for Families" }
    Renames: IMG_7129-captioned.mp4 → IMG_7129-The_Lucid_Cove_AI_for_Families-captioned.mp4
    Returns: { "old_name": "...", "new_name": "...", "nc_path": "..." }
    """
    body = await request.json()
    stem = body.get("stem", "").strip()
    title = body.get("title", "").strip()
    if not stem or not title:
        return JSONResponse({"error": "stem and title required"}, status_code=400)

    # Per-presence NC session (cove-core injects X-NC-* headers); None = local mount.
    nc = NCSession.from_request(request, body)

    # Sanitize title for filename: replace spaces with _, strip unsafe chars
    import re
    safe_title = re.sub(r'[^\w\s-]', '', title).strip()
    safe_title = re.sub(r'\s+', '_', safe_title)[:80]
    new_name = f"{stem}-{safe_title}-captioned.mp4"
    nc_path = f"{NC_VIDEO_PATH}/shorts/{new_name}"

    if nc is not None:
        # No per-presence WebDAV MOVE helper, so pull the old file to scratch and
        # push it up under the new name (the observable result is the new file).
        old_name = f"{stem}-captioned.mp4"
        local = os.path.join("/tmp/cove-video", nc.user, "shorts", old_name)
        if not (os.path.isfile(local) or await nc.pull(f"shorts/{old_name}", local)):
            return JSONResponse({"error": f"Captioned file not found: {stem}"}, status_code=404)
        await nc.push(f"shorts/{new_name}", local, "video/mp4")
        logger.info(f"Renamed captioned: {old_name} → {new_name}")
        return JSONResponse({
            "old_name": old_name,
            "new_name": new_name,
            "nc_path": nc_path,
            "size_mb": round(os.path.getsize(local) / (1024 * 1024), 1),
        })

    shorts_dir = os.path.join(VIDEO_MOUNT, "shorts")
    old_path = os.path.join(shorts_dir, f"{stem}-captioned.mp4")
    if not os.path.isfile(old_path):
        return JSONResponse({"error": f"Captioned file not found: {stem}"}, status_code=404)

    new_path = os.path.join(shorts_dir, new_name)

    import shutil
    shutil.move(old_path, new_path)
    logger.info(f"Renamed captioned: {stem}-captioned.mp4 → {new_name}")

    # Tell NC about the rename
    _nc_scan("AgentSkills/Content/video/shorts")

    return JSONResponse({
        "old_name": f"{stem}-captioned.mp4",
        "new_name": new_name,
        "nc_path": nc_path,
        "size_mb": round(os.path.getsize(new_path) / (1024 * 1024), 1),
    })


@router.post("/api/video/caption-full")
async def caption_full_video(request: Request):
    """Render the full-length source video with burnt-in captions.

    Takes the edited transcript and overlays word-level captions onto the
    original video. Output is 1920×1080 (or native if smaller). This becomes
    the "related video" that all shorts link back to.

    Body: {
        "stem": "IMG_7129",
        "caption": { fontSize, fontFamily, color, stroke, font, style },  // optional
        "video_filter": "natural"  // optional
    }

    Returns: { "filename": "...", "nc_path": "...", "size_mb": ..., "duration": ... }
    """
    import subprocess
    import json as json_mod

    body = await request.json()
    stem = body.get("stem", "").strip()
    if not stem:
        return JSONResponse({"error": "stem required"}, status_code=400)

    # Per-presence NC session (cove-core injects X-NC-* headers); None = local mount.
    nc = NCSession.from_request(request, body)

    # Guard: skip if captioned full already exists (plain or title-renamed)
    if nc is not None:
        # No WebDAV listing helper, so we can only check the plain name (the
        # title-renamed glob isn't expressible over pull/push). Pull to scratch.
        plain = f"{stem}-captioned.mp4"
        existing_local = os.path.join("/tmp/cove-video", nc.user, "shorts", plain)
        if os.path.isfile(existing_local) or await nc.pull(f"shorts/{plain}", existing_local):
            size_mb = os.path.getsize(existing_local) / (1024 * 1024)
            logger.info(f"Caption-full already exists: {plain} ({size_mb:.1f} MB) — skipping render")
            return JSONResponse({
                "filename": plain,
                "nc_path": f"{NC_VIDEO_PATH}/shorts/{plain}",
                "size_mb": round(size_mb, 1),
                "skipped": True,
                "reason": "Captioned full already exists",
            })
    else:
        import glob as glob_mod
        shorts_dir = os.path.join(VIDEO_MOUNT, "shorts")
        existing = os.path.join(shorts_dir, f"{stem}-captioned.mp4")
        if not os.path.isfile(existing):
            # Check for title-renamed version: IMG_7129-Some_Title-captioned.mp4
            pattern = os.path.join(shorts_dir, f"{stem}-*-captioned.mp4")
            matches = glob_mod.glob(pattern)
            if matches:
                existing = matches[0]
        if os.path.isfile(existing):
            size_mb = os.path.getsize(existing) / (1024 * 1024)
            fname = os.path.basename(existing)
            logger.info(f"Caption-full already exists: {fname} ({size_mb:.1f} MB) — skipping render")
            return JSONResponse({
                "filename": fname,
                "nc_path": f"{NC_VIDEO_PATH}/shorts/{fname}",
                "size_mb": round(size_mb, 1),
                "skipped": True,
                "reason": "Captioned full already exists",
            })

    # Find source video (pulls from the presence's NC when nc is set).
    video_path = None
    for ext in [".MOV", ".mp4", ".mov", ".mkv"]:
        video_path = await resolve_video_source(f"{stem}{ext}", nc)
        if video_path:
            break
    if not video_path:
        return JSONResponse({"error": f"Source video not found: {stem}"}, status_code=404)

    # Load edited transcript (with nc, pull from the presence's NC into scratch).
    transcript_segments = []
    for tf in [f"{stem}-transcript-edited.json", f"{stem}-transcript.json"]:
        tp = None
        if nc is not None:
            _local = os.path.join("/tmp/cove-video", nc.user, "transcripts", tf)
            if os.path.isfile(_local) or await nc.pull(f"transcripts/{tf}", _local):
                tp = _local
        else:
            _cand = os.path.join(VIDEO_MOUNT, "transcripts", tf)
            if os.path.isfile(_cand):
                tp = _cand
        if tp:
            with open(tp) as f:
                tdata = json_mod.load(f)
                transcript_segments = tdata.get("segments", [])
            logger.info(f"Loaded transcript for full captions: {tf} ({len(transcript_segments)} segments)")
            break
    if not transcript_segments:
        return JSONResponse({"error": "No transcript found"}, status_code=404)

    # Trim boundaries from transcript — the edited transcript defines the output range
    trim_start = transcript_segments[0].get("start", 0) if transcript_segments else 0
    trim_end = transcript_segments[-1].get("end", 0) if transcript_segments else 0

    # Get video duration as fallback
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", video_path],
            capture_output=True, text=True, timeout=10,
        )
        duration = float(probe.stdout.strip())
    except Exception:
        duration = 0

    # Use transcript boundaries if they trim anything off the full video
    # trim_start > 0 means the beginning was cut, trim_end < duration means the end was cut
    has_start_trim = trim_start > 0.5  # > 0.5s to avoid float rounding
    has_end_trim = trim_end > 0 and duration > 0 and (duration - trim_end) > 0.5
    if has_start_trim or has_end_trim:
        effective_start = trim_start
        effective_end = trim_end
        effective_duration = trim_end - trim_start
        logger.info(f"Caption-full: trimming to transcript range {trim_start:.1f}s - {trim_end:.1f}s ({effective_duration:.0f}s of {duration:.0f}s total)")
    else:
        effective_start = 0
        effective_end = duration
        effective_duration = duration
        logger.info(f"Caption-full: no trim, using full duration {duration:.0f}s")

    # Caption style — use provided or defaults. Default = per-word (operator decision
    # 2026-07-03): word-mode is THE caption style; line mode stays available as an
    # explicit choice only. (The UI default is already 'word' — this fallback was the
    # one place still defaulting to line.)
    caption = body.get("caption", {})
    if not caption:
        caption = {
            "fontSize": 56,
            "fontFamily": "Arial",
            "color": "#ffffff",
            "strokeColor": "#000000",
            "stroke": "outlined",
            "font": "bold",
            "style": "word",
        }

    # Video filter
    video_filter = body.get("video_filter", "natural")
    VIDEO_FILTERS = {
        "natural":   "eq=contrast=1.10:brightness=-0.02:saturation=0.95",
        "rich":      "eq=contrast=1.12:brightness=-0.05:saturation=0.78,curves=all='0/0.035 0.5/0.5 1/0.965'",
        "cinematic": "eq=contrast=1.18:brightness=-0.04:saturation=0.88,colortemperature=temperature=6200",
    }
    vf_color = VIDEO_FILTERS.get(video_filter, VIDEO_FILTERS["natural"])

    # Output: 4K horizontal with crop position + caption bar
    fmt_out_w = 3840
    fmt_out_h = 2160
    out_name = f"{stem}-captioned.mp4"
    out_tmp = f"/tmp/{out_name}"

    # Use crop template for the square crop, placed in a 16:9 frame.
    # Square maximized (75% of height), no top bar, bottom bar for captions.
    #
    # Layout: 3840 × 2160 total
    #   ┌──────┬──────────┬──────┐
    #   │      │  Square  │      │
    #   │ blk  │  Video   │ blk  │  ← 1620px (75% of 2160)
    #   │      │          │      │
    #   │      ├──────────┤      │
    #   │      │ Caption  │      │  ← 540px
    #   └──────┴──────────┴──────┘

    crop_template = body.get("crop_template", {})
    src_x = crop_template.get("src_x", 0)
    src_y = crop_template.get("src_y", 0)
    src_w = crop_template.get("src_w", 0)
    src_h = crop_template.get("src_h", 0)
    border_enabled = crop_template.get("border_enabled", False)

    # Horizontal: maximize square, no top bar, bottom bar for captions only
    h_bar_top = 0
    h_square = int(fmt_out_h * 0.75)     # 1620
    h_bar_bot = fmt_out_h - h_square     # 540
    pad_left = (fmt_out_w - h_square) // 2   # 1110

    has_crop = border_enabled and src_w > 0 and src_h > 0

    try:
        # Generate ASS — caption goes in the bottom bar area
        # start_time=effective_start offsets ASS events so they align with the trimmed output
        ass_path = _generate_ass_subtitles(
            segments=transcript_segments,
            start_time=effective_start,
            end_time=effective_end,
            caption_style=caption,
            format_name="horizontal",
            out_w=fmt_out_w,
            out_h=fmt_out_h,
            bar_top=0,
            bar_bot=h_bar_bot,
            moment_id=0,
            stem=f"{stem}-full",
            content_w=h_square,
        )

        if has_crop:
            # Crop to operator's square → scale to match vertical proportions → pillarbox
            vf = (
                f"{_square_crop_expr(src_w, src_h, src_x, src_y)},"
                f"scale={h_square}:{h_square},"
                f"pad={fmt_out_w}:{fmt_out_h}:{pad_left}:{h_bar_top}:black,"
                f"{vf_color}"
            )
            logger.info(
                f"Caption-full: crop {src_w}x{src_h}@{src_x},{src_y} → "
                f"square {h_square}px → pillarbox {fmt_out_w}x{fmt_out_h} "
                f"(left={pad_left}, top={h_bar_top}, bot={h_bar_bot})"
            )
        else:
            # Fallback: center-crop to square from source, then same layout
            vf = (
                f"crop=min(iw\\,ih):min(iw\\,ih),"
                f"scale={h_square}:{h_square},"
                f"pad={fmt_out_w}:{fmt_out_h}:{pad_left}:{h_bar_top}:black,"
                f"{vf_color}"
            )

        if ass_path:
            vf += f",ass={ass_path}"

        af = (
            "highpass=f=80,"
            "lowpass=f=12000,"
            "acompressor=threshold=-20dB:ratio=3:attack=5:release=50,"
            "loudnorm=I=-14:TP=-1:LRA=11"
        )

        # Build ffmpeg command with trim if transcript defines boundaries
        cmd = ["ffmpeg", "-y"]
        if effective_start > 0:
            cmd += ["-ss", str(effective_start)]
        cmd += ["-i", video_path]
        if effective_end > 0 and effective_end < duration:
            cmd += ["-to", str(effective_duration)]  # -to is relative to -ss
        cmd += [
            # Map only real video + audio (skip iPhone timecode/motion data tracks).
            "-map", "0:v:0", "-map", "0:a:0?",
            "-vf", vf,
            "-af", af,
            # iPhone 4K is VFR — force CFR + resampled audio to keep A/V in sync.
            "-vsync", "cfr", "-r", "30",
            "-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p",
            "-preset", "medium", "-crf", "20",
            "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
            "-movflags", "+faststart",
            "-threads", "4",
            out_tmp,
        ]

        logger.info(f"Rendering captioned full video: {stem} ({effective_duration:.0f}s, trim {effective_start:.1f}–{effective_end:.1f})")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)  # 1hr max

        if result.returncode != 0:
            # Full forensics to the log (A16, 2026-07-03): the 500-char stderr tail
            # hid the actual cause of an encoder-open failure that a hand-run of the
            # same command didn't reproduce. Log the EXACT argv + whole stderr.
            logger.error("caption-full ffmpeg cmd: %s", cmd)
            logger.error("caption-full ffmpeg stderr (full):\n%s", result.stderr)
            return JSONResponse(
                {"error": f"ffmpeg failed: {result.stderr[-500:]}"},
                status_code=500,
            )

        # Stage the finished video locally, then publish to the presence's NC via
        # WebDAV (nc set) or copy into the local /video mount + NC scan (nc None).
        # For the founder mount, publish_video_output writes to the NC data dir,
        # which is accessible via Finder/Syncthing immediately and avoids the 413
        # WebDAV PUT limit on large files.
        import shutil
        local_out_dir = "/tmp/cove-out"
        os.makedirs(local_out_dir, exist_ok=True)
        dest_path = os.path.join(local_out_dir, out_name)
        shutil.move(out_tmp, dest_path)
        size_mb = os.path.getsize(dest_path) / (1024 * 1024)
        nc_path = f"{NC_VIDEO_PATH}/shorts/{out_name}"
        await publish_video_output(dest_path, f"shorts/{out_name}", nc, "video/mp4")

        if ass_path and os.path.isfile(ass_path):
            os.remove(ass_path)

        logger.info(f"Captioned full video done: {out_name} ({size_mb:.1f} MB) → {dest_path}")

        # C4 #1 — captioned-full succeeded for this stem: GRADUATE the original
        # processing/ → raw/ (finished source material, never auto-deleted).
        # Best-effort — a graduation hiccup must never fail the render.
        try:
            from src.video_lifecycle import graduate_processing_to_raw
            await graduate_processing_to_raw(stem, nc)
        except Exception as _ge:
            logger.warning(f"[lifecycle] graduation call skipped: {_ge}")

        return JSONResponse({
            "filename": out_name,
            "nc_path": nc_path,
            "size_mb": round(size_mb, 1),
            "duration_seconds": round(effective_duration, 1),
        })

    except subprocess.TimeoutExpired:
        return JSONResponse({"error": "Captioned video timed out (>1hr)"}, status_code=504)
    except Exception as e:
        logger.error(f"caption-full error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


def _generate_ass_subtitles(
    segments: list,
    start_time: float,
    end_time: float,
    caption_style: dict,
    format_name: str,
    out_w: int,
    out_h: int,
    bar_top: int,
    bar_bot: int,
    moment_id: int,
    stem: str,
    content_w: int = 0,
) -> str | None:
    """Generate an ASS subtitle file from word-level transcript segments.

    format_name: "vertical", "horizontal", or "square"
    content_w: effective content width for font scaling (pillarbox layouts).
               If 0, uses out_w.
    Returns path to the temp ASS file, or None if no segments in range.
    """
    # Filter segments to this moment's time range
    # Offset timestamps so the moment starts at t=0 (ffmpeg -ss shifts time)
    words_in_range = []
    for seg in segments:
        seg_start = seg.get("start", 0)
        seg_end = seg.get("end", 0)
        if seg_end > start_time and seg_start < end_time:
            words_in_range.append({
                "text": seg.get("text", ""),
                "start": max(0, seg_start - start_time),
                "end": max(0, seg_end - start_time),
            })

    if not words_in_range:
        return None

    # Caption style params
    # Scale font size to match preview: preview uses 1080 as reference width,
    # but ASS uses the actual output resolution. Multiply by out_w/1080 so
    # what you see in the preview matches the rendered output.
    raw_font_size = caption_style.get("fontSize", 72)
    scale_w = content_w if content_w > 0 else out_w
    font_size = int(raw_font_size * scale_w / 1080)
    font_family = caption_style.get("fontFamily", "Arial")
    color = caption_style.get("color", "#5ce1e6")
    stroke_color = caption_style.get("strokeColor", "#ffffff")
    stroke_preset = caption_style.get("stroke", "pop")
    font_weight = caption_style.get("font", "black")
    display_mode = caption_style.get("style", "word")  # "word" or "line"

    # Letter spacing — Impact and other condensed fonts need extra
    spacing = 4 if font_family == "Impact" else 0

    # Convert hex colors to ASS format (ASS uses &HBBGGRR&)
    def hex_to_ass(hex_color):
        hex_color = hex_color.lstrip("#")
        r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
        return f"&H00{b:02X}{g:02X}{r:02X}&"

    ass_primary = hex_to_ass(color)
    ass_outline = hex_to_ass(stroke_color)

    # Stroke width from preset — scale to content width like font size
    scale = scale_w / 1080
    stroke_widths = {"clean": 0, "outlined": 2, "pop": 3}
    border_w = int(stroke_widths.get(stroke_preset, 3) * scale)

    # Shadow from preset
    shadow_vals = {"clean": 0, "outlined": 0, "pop": 2}
    shadow_w = int(shadow_vals.get(stroke_preset, 0) * scale)

    # Font weight → bold flag
    bold = -1 if font_weight in ("bold", "black") else 0

    # Caption position per format (ASS alignment 2 = bottom-center, MarginV = from bottom)
    # Preview puts caption at 12% below bar top (16px padding / 135px bar).
    # In ASS, MarginV = distance from bottom edge to text bottom.
    # To match preview: margin_v = bar_bot * 0.88 - font_size
    # (text top at 12% from bar top regardless of format/font scaling)
    if bar_bot > 0 and format_name in ("vertical", "horizontal"):
        margin_v = max(10, int(bar_bot * 0.88 - font_size))
    elif format_name == "square":
        # Bottom area of the square — small margin from bottom edge
        margin_v = int(out_h * 0.08)
    else:
        # No bar — near bottom of frame
        margin_v = 80

    # Build ASS file
    ass_lines = []
    ass_lines.append("[Script Info]")
    ass_lines.append(f"PlayResX: {out_w}")
    ass_lines.append(f"PlayResY: {out_h}")
    ass_lines.append("ScaledBorderAndShadow: yes")
    ass_lines.append("")
    ass_lines.append("[V4+ Styles]")
    ass_lines.append("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
                     "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
                     "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
                     "Alignment, MarginL, MarginR, MarginV, Encoding")
    ass_lines.append(
        f"Style: Default,{font_family},{font_size},{ass_primary},&H000000FF&,"
        f"{ass_outline},&H80000000&,{bold},0,0,0,100,100,{spacing},0,1,"
        f"{border_w},{shadow_w},2,10,10,{margin_v},1"
    )
    ass_lines.append("")
    ass_lines.append("[Events]")
    ass_lines.append("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text")

    def fmt_ass_time(seconds):
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = seconds % 60
        return f"{h}:{m:02d}:{s:05.2f}"

    if display_mode == "word":
        # Per-word display: each word gets its own event
        for w in words_in_range:
            text = w["text"].strip()
            if not text:
                continue
            t_start = fmt_ass_time(w["start"])
            t_end = fmt_ass_time(w["end"])
            # Uppercase for visual impact (matches the preview)
            ass_lines.append(
                f"Dialogue: 0,{t_start},{t_end},Default,,0,0,0,,{text.upper()}"
            )
    else:
        # Full line: group into ~5-word phrases
        phrase = []
        phrase_start = 0
        for w in words_in_range:
            text = w["text"].strip()
            if not text:
                continue
            if not phrase:
                phrase_start = w["start"]
            phrase.append(text)
            if len(phrase) >= 5 or text.endswith((".", "!", "?", ",")):
                t_start = fmt_ass_time(phrase_start)
                t_end = fmt_ass_time(w["end"])
                line_text = " ".join(phrase)
                ass_lines.append(
                    f"Dialogue: 0,{t_start},{t_end},Default,,0,0,0,,{line_text}"
                )
                phrase = []
        # Flush remaining
        if phrase:
            t_start = fmt_ass_time(phrase_start)
            t_end = fmt_ass_time(words_in_range[-1]["end"])
            ass_lines.append(
                f"Dialogue: 0,{t_start},{t_end},Default,,0,0,0,,{' '.join(phrase)}"
            )

    # Write temp file
    ass_path = f"/tmp/{stem}_m{moment_id}.ass"
    with open(ass_path, "w") as f:
        f.write("\n".join(ass_lines))

    logger.info(f"ASS subtitles generated: {ass_path} ({len(words_in_range)} words)")
    return ass_path


@router.get("/api/video/stream")
async def stream_video(request: Request, filename: str = ""):
    """Stream a video file with HTTP range support for browser playback.

    Query params:
        filename: Video filename (looked up in processing/ → inbox/ → raw/)

    Returns: Video file with proper Content-Type and range support.
    """
    from starlette.responses import FileResponse

    if not filename:
        return JSONResponse({"error": "No filename"}, status_code=400)

    # Per-presence NC session (cove-core injects X-NC-* headers); None = local mount.
    # With nc, resolve_video_source pulls into scratch and we serve the local copy.
    nc = NCSession.from_request(request)
    video_path = await resolve_video_source(filename, nc)

    if not video_path:
        return JSONResponse({"error": f"Video not found: {filename}"}, status_code=404)

    # Determine content type from extension
    ext = os.path.splitext(filename)[1].lower()
    media_types = {
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".webm": "video/webm",
        ".mkv": "video/x-matroska",
        ".avi": "video/x-msvideo",
    }
    media_type = media_types.get(ext, "video/mp4")

    return FileResponse(video_path, media_type=media_type, filename=filename)


# ── NC filesystem scan ────────────────────────────────────────────
# When pipecat-voice writes directly to the NC data dir (bypassing WebDAV),
# NC doesn't know the file exists until we trigger occ files:scan.
# We talk to the Docker API via the mounted unix socket — no docker CLI needed.


# _nc_scan moved to voice_common.py (shared by video.py + stt.py)


# ── Video pipeline file I/O ───────────────────────────────────────
# Pipecat-voice owns all video file writes. Cove agents proxy through
# these endpoints instead of writing directly (they have read-only mounts).

@router.post("/api/video/write-json")
async def write_json(request: Request):
    """Write a JSON file to the video mount.

    Body: { "subpath": "transcripts/STEM-moments.json", "data": {...} }

    subpath is relative to VIDEO_MOUNT (e.g. "transcripts/foo.json",
    "shorts/foo-moments-processed.json"). No leading slash.
    data is the JSON object to write.

    Returns: { "wrote": true, "path": "/video/transcripts/..." }
    """
    body = await request.json()
    subpath = body.get("subpath", "")
    data = body.get("data")

    if not subpath or data is None:
        return JSONResponse(
            {"error": "subpath and data required"},
            status_code=400,
        )

    # Safety: prevent path traversal
    if ".." in subpath or subpath.startswith("/"):
        return JSONResponse(
            {"error": "Invalid subpath"},
            status_code=400,
        )

    # Per-presence NC session (cove-core injects X-NC-* headers); None = local mount.
    nc = NCSession.from_request(request, body)

    try:
        content = json.dumps(data, indent=2, ensure_ascii=False)
        if nc is not None:
            # Write to scratch, then push up to <video-tree>/<subpath> via WebDAV.
            local = os.path.join("/tmp/cove-video", nc.user, subpath)
            os.makedirs(os.path.dirname(local) or ".", exist_ok=True)
            with open(local, "w") as f:
                f.write(content)
            wrote = await nc.push(subpath, local, "application/json")
            if not wrote:
                logger.error(f"write-json FAILED (NC push): {subpath}")
                return JSONResponse({"error": "Write failed: NC push"}, status_code=500)
            logger.info(f"write-json OK: {subpath} ({len(content)} bytes)")
            return {"wrote": True, "path": f"{NC_VIDEO_PATH}/{subpath}"}

        dest = os.path.join(VIDEO_MOUNT, subpath)
        dest_dir = os.path.dirname(dest)
        os.makedirs(dest_dir, exist_ok=True)
        with open(dest, "w") as f:
            f.write(content)
        logger.info(f"write-json OK: {subpath} ({len(content)} bytes)")
        # Tell NC about the new file
        scan_dir = os.path.dirname(f"AgentSkills/Content/video/{subpath}")
        _nc_scan(scan_dir)
        return {"wrote": True, "path": dest}
    except Exception as e:
        logger.error(f"write-json FAILED: {subpath} — {e}")
        return JSONResponse(
            {"error": f"Write failed: {e}"},
            status_code=500,
        )


@router.post("/api/video/delete-file")
async def delete_file(request: Request):
    """Delete a file from the video mount.

    Body: { "subpath": "shorts/STEM-moments-processed.json" }
    """
    body = await request.json()
    subpath = body.get("subpath", "")

    if not subpath:
        return JSONResponse({"error": "subpath required"}, status_code=400)

    if ".." in subpath or subpath.startswith("/"):
        return JSONResponse({"error": "Invalid subpath"}, status_code=400)

    dest = os.path.join(VIDEO_MOUNT, subpath)
    if not os.path.isfile(dest):
        return {"deleted": False, "reason": "not found"}

    try:
        os.remove(dest)
        logger.info(f"delete-file OK: {subpath}")
        return {"deleted": True, "path": dest}
    except Exception as e:
        logger.error(f"delete-file FAILED: {subpath} — {e}")
        return JSONResponse({"error": f"Delete failed: {e}"}, status_code=500)
