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
    VIDEO_MOUNT, NC_VIDEO_PATH, NC_HTML_ROOT,
    NEXTCLOUD_URL, NEXTCLOUD_ADMIN_USER, NEXTCLOUD_ADMIN_PASSWORD,
    _nc_scan, NCSession, resolve_video_source, publish_video_output,
    find_on_nc_data,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# Look presets — base brightness/contrast/saturation (+ optional ffmpeg extras).
# "original" is TRUE identity: no eq/curves/temp when B/C/S stay at 0/1/1.
# UI sliders override B/C/S; extras stay with the chip (curves / color temp)
# so Rich/Cinematic keep their character on the graded path only.
LOOK_PRESETS = {
    "original": {
        "brightness": 0.0,
        "contrast": 1.0,
        "saturation": 1.0,
        "extra": "",
    },
    "natural": {
        "brightness": -0.02,
        "contrast": 1.10,
        "saturation": 0.95,
        "extra": "",
    },
    "rich": {
        "brightness": -0.05,
        "contrast": 1.12,
        "saturation": 0.78,
        "extra": "curves=all='0/0.035 0.5/0.5 1/0.965'",
    },
    "cinematic": {
        "brightness": -0.04,
        "contrast": 1.18,
        "saturation": 0.88,
        "extra": "colortemperature=temperature=6200",
    },
}

# Default look for encode when crop/body omits video_filter.
DEFAULT_VIDEO_FILTER = "original"


def _clamp_look_val(name: str, val) -> float:
    try:
        x = float(val)
    except (TypeError, ValueError):
        x = LOOK_PRESETS["original"][name]
    if name == "brightness":
        return max(-0.5, min(0.5, x))
    # contrast / saturation
    return max(0.0, min(3.0, x))


def _is_identity_look(b: float, c: float, s: float, extra: str = "") -> bool:
    """True when no color grade should touch the pixels."""
    if (extra or "").strip():
        return False
    return abs(b) < 1e-6 and abs(c - 1.0) < 1e-6 and abs(s - 1.0) < 1e-6


def resolve_look_vf(source=None) -> str:
    """Build ffmpeg color filter from preset id + optional B/C/S overrides.

    source may be crop_template or the request body. Keys:
      video_filter: original|natural|rich|cinematic
      filter_brightness / filter_contrast / filter_saturation: optional floats

    Returns "" for true identity (Original + untouched sliders) so encode does
    not run a no-op eq= chain — identity eq still resamples luma/chroma and
    was washing iPhone 4K footage.
    """
    src = source or {}
    name = (src.get("video_filter") or DEFAULT_VIDEO_FILTER).strip().lower()
    if name not in LOOK_PRESETS:
        name = DEFAULT_VIDEO_FILTER
    preset = LOOK_PRESETS[name]

    def _pick(key: str, filter_key: str) -> float:
        if filter_key in src and src.get(filter_key) is not None and src.get(filter_key) != "":
            return _clamp_look_val(key, src.get(filter_key))
        return float(preset[key])

    b = _pick("brightness", "filter_brightness")
    c = _pick("contrast", "filter_contrast")
    s = _pick("saturation", "filter_saturation")
    extra = (preset.get("extra") or "").strip()
    if _is_identity_look(b, c, s, extra):
        return ""
    eq = f"eq=contrast={c:.4g}:brightness={b:.4g}:saturation={s:.4g}"
    if extra:
        return f"{eq},{extra}"
    return eq


def hq_scale(w, h, *, out_matrix: str | None = "bt709") -> str:
    """High-quality geometric scale — used on every publish path.

    Default ffmpeg scale is bilinear and softens fine detail; lanczos + full
    chroma interpolation keeps 4K source texture through crop→output.
    in_color_matrix=auto reads the source correctly.

    out_matrix (ffmpeg *scale* filter names — NOT x265/zscale names):
      - "bt709" (default) — SDR publish paths after color_prep / graded looks.
      - "bt2020" — Original+HDR native passthrough. Keep 2020 on the scale step
        so we do NOT reshuffle HLG/bt2020 into bt709 then tag the file as
        bt2020/HLG (sparkle / wrong pop / soft phone look).
        IMPORTANT: scale's enum is `bt2020` only. `bt2020nc` / `bt2020c` are
        valid for zscale and x265-params, but ffmpeg scale rejects them and
        every native-HDR moment fails with 0 clips rendered.
      - None or "" — omit out_color_matrix (preserve; ffmpeg 7.x rejects
        out_color_matrix=auto).
    """
    base = (
        f"scale={int(w)}:{int(h)}:flags=lanczos+accurate_rnd+full_chroma_int"
        f":in_color_matrix=auto"
    )
    om = (out_matrix or "").strip().lower()
    # Normalize accidental zscale/x265 names so a caller cannot re-break encode.
    if om in {"bt2020nc", "bt2020ncl", "bt2020-ncl", "bt2020c", "bt2020cl", "bt2020-cl"}:
        om = "bt2020"
    if om:
        base += f":out_color_matrix={om}"
    base += ":force_original_aspect_ratio=disable"
    return base


def scale_out_matrix(color_info: dict | None, *, native_hdr: bool) -> str | None:
    """Matrix for hq_scale: keep bt2020 on native HDR, bt709 on SDR deliverables.

    Returns scale-filter enum names only (`bt709` / `bt2020`). Never `bt2020nc`.
    """
    if not native_hdr:
        return "bt709"
    # scale filter: bt2020 covers both NCL and CL; encoder tags carry nc/c.
    spc = ((color_info or {}).get("color_space") or "").strip().lower()
    if not spc or spc in {"unknown", "reserved", "gbr"} or "2020" in spc or spc.startswith("bt2020"):
        return "bt2020"
    # Unusual but HDR-tagged space we do not recognize — still avoid bt709 shuffle.
    return "bt2020"


def probe_video_fps(video_path: str) -> float | None:
    """Best-effort constant fps for CFR output. None → let encoder decide.

    Forcing 30fps on 24/60fps iPhone sources re-times every frame and looks
    soft next to QuickTime 'same as original'. Prefer source rate when known.
    """
    try:
        probe = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=avg_frame_rate,r_frame_rate",
                "-of", "default=nw=1",
                video_path,
            ],
            capture_output=True, text=True, timeout=15,
        )
        rates = {}
        for line in (probe.stdout or "").splitlines():
            if "=" not in line:
                continue
            k, _, v = line.partition("=")
            rates[k.strip()] = (v or "").strip()
        for key in ("avg_frame_rate", "r_frame_rate"):
            raw = rates.get(key) or ""
            if not raw or raw in ("0/0", "N/A"):
                continue
            if "/" in raw:
                num_s, _, den_s = raw.partition("/")
                try:
                    num, den = float(num_s), float(den_s)
                except ValueError:
                    continue
                if den <= 0 or num <= 0:
                    continue
                fps = num / den
            else:
                try:
                    fps = float(raw)
                except ValueError:
                    continue
            if 5.0 <= fps <= 120.0:
                return round(fps, 3)
    except Exception as e:
        logger.warning("probe_video_fps failed for %s: %s", video_path, e)
    return None


def encode_fps_args(video_path: str) -> list:
    """CFR args that preserve source cadence when probeable."""
    fps = probe_video_fps(video_path)
    if fps:
        # vsync cfr + explicit -r matching source (not hard-coded 30).
        return ["-vsync", "cfr", "-r", f"{fps:g}"]
    # Unknown rate: still CFR, but don't invent 30fps.
    return ["-vsync", "cfr"]


def join_vf(*parts) -> str:
    """Comma-join non-empty filtergraph segments."""
    out = []
    for p in parts:
        if p is None:
            continue
        s = str(p).strip().strip(",")
        if s:
            out.append(s)
    return ",".join(out)


def _rect_crop_expr(src_w, src_h, src_x, src_y) -> str:
    """Clamped rectangular crop that never exceeds the (post-rotation) source.

    Used for full 9:16 (border off) where src_w/src_h are not square. iw/ih and
    ow/oh are evaluated by ffmpeg AFTER auto-rotation. min() takes exactly two
    args — nest runtime pairs; fold Python constants first.
    """
    w_req = max(1, int(src_w))
    h_req = max(1, int(src_h))
    w = f"min({w_req}\,iw)"
    h = f"min({h_req}\,ih)"
    x = f"max(0\,min({int(src_x)}\,iw-ow))"
    y = f"max(0\,min({int(src_y)}\,ih-oh))"
    return f"crop={w}:{h}:{x}:{y}"


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


# HLG (iPhone HDR) + PQ (HDR10). Browser <video> tone-maps these for display;
# bare libx264 / JPEG extract does not — still + encode look washed without this.
_HDR_TRANSFERS = frozenset({
    "arib-std-b67",  # HLG
    "smpte2084",     # PQ
    "smpte428",
})


def probe_video_color(video_path: str) -> dict:
    """Best-effort v:0 color tags from ffprobe. Empty strings when unknown."""
    keys = ("color_range", "color_space", "color_transfer", "color_primaries", "pix_fmt")
    out = {k: "" for k in keys}
    try:
        probe = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries",
                "stream=color_range,color_space,color_transfer,color_primaries,pix_fmt",
                "-of", "default=nw=1",
                video_path,
            ],
            capture_output=True, text=True, timeout=15,
        )
        for line in (probe.stdout or "").splitlines():
            if "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            if k in out:
                out[k] = (v or "").strip()
    except Exception as e:
        logger.warning("probe_video_color failed for %s: %s", video_path, e)
    return out


def is_hdr_color(info: dict | None) -> bool:
    """True when stream transfer is HLG/PQ (or clearly named HDR)."""
    if not info:
        return False
    trc = (info.get("color_transfer") or "").strip().lower()
    if trc in _HDR_TRANSFERS:
        return True
    return "hlg" in trc or "smpte2084" in trc


def _hdr_input_zscale_params(color_info: dict | None) -> str:
    """Declare HLG/PQ + bt2020 on the FIRST zscale so tonemap isn't guessing.

    A bare `zscale=t=linear` without tin/min/pin left ffmpeg free to mis-read
    the transfer/primaries. Wrong input matrix on HLG iPhone shows up as a
    magenta/red cast + washed mids next to the browser player (which tone-maps
    correctly from the stream tags).
    """
    info = color_info or {}
    trc = (info.get("color_transfer") or "").strip().lower()
    prim = (info.get("color_primaries") or "").strip().lower()
    spc = (info.get("color_space") or "").strip().lower()
    rng_in = (info.get("color_range") or "").strip().lower()

    if trc in ("arib-std-b67",) or "hlg" in trc:
        tin = "arib-std-b67"
    elif trc in ("smpte2084",) or "smpte2084" in trc:
        tin = "smpte2084"
    elif trc in ("smpte428",):
        tin = "smpte428"
    else:
        # is_hdr_color already true — prefer HLG (iPhone path)
        tin = "arib-std-b67"

    if "2020" in prim or prim in ("bt2020",):
        pin = "bt2020"
    else:
        pin = "bt2020"

    if "2020" in spc or spc in ("bt2020nc", "bt2020c", "bt2020"):
        min_m = "bt2020nc" if "c" not in spc or "nc" in spc else "bt2020c"
        if spc in ("bt2020c",):
            min_m = "bt2020c"
        else:
            min_m = "bt2020nc"
    else:
        min_m = "bt2020nc"

    # iPhone HLG is almost always limited/tv; be explicit so zscale doesn't
    # treat the signal as full-range and crush/lift the wrong way.
    rin = "tv"
    if rng_in in ("pc", "jpeg", "full"):
        rin = "pc"
    elif rng_in in ("tv", "mpeg", "limited"):
        rin = "tv"

    return f"tin={tin}:min={min_m}:pin={pin}:rin={rin}"


def hdr_to_sdr_vf(color_info: dict | None = None, *, for_still: bool = False) -> str:
    """HLG/PQ → display-referred bt709 SDR (zscale + hable tonemap).

    Used on crop stills and publish encodes. Without this, iPhone HDR is
    written as if it were already SDR and mids go flat next to <video>.

    Input transfer/primaries/matrix/range are taken from probed stream tags
    (see _hdr_input_zscale_params). Peak 100 + hable is a display-referred
    map close to what mobile Safari/Chrome do for HLG preview — not a grade.
    """
    rng = "pc" if for_still else "tv"
    pix = "yuvj420p" if for_still else "yuv420p"
    inp = _hdr_input_zscale_params(color_info)
    # ★ GAMUT MAP IN LINEAR, BEFORE TONEMAP (2026-07-20). The old chain folded
    # p=bt709 into the FINAL zscale — but by then the frame's bt2020 origin is
    # lost through the tonemap step, so zscale TAGGED the pixels 709 without
    # remapping them. Wide-gamut chroma wearing a 709 label = oversaturated
    # red-pushed skin (measured: pure bt2020 red hit V=255 chroma-clip through
    # the old chain; 235 — correct — through this one). This is the canonical
    # ffmpeg HDR→SDR recipe: linearize → convert primaries → tonemap → re-encode
    # transfer/matrix. The final zscale carries t/m/r only; p is done here.
    return (
        f"zscale={inp}:t=linear:npl=100,"
        "format=gbrpf32le,"
        "zscale=p=bt709,"
        "tonemap=tonemap=hable:desat=0:peak=100,"
        f"zscale=t=bt709:m=bt709:r={rng},"
        f"format={pix}"
    )


def native_hdr_encode_args(
    color_info: dict | None,
    *,
    preset: str = "medium",
) -> list | None:
    """ORIGINAL look + HDR source → NATIVE COLOR PASSTHROUGH (2026-07-21).

    No SDR conversion can match how Apple/players render an HLG original
    (EDR brightness, 10-bit gradients): even a mathematically correct tonemap
    reads dimmer/warmer/banded side-by-side with the camera file. 'Original'
    must mean original. So when NO grade is applied and the source is HDR,
    keep the video in its native color: 10-bit HEVC, source transfer/
    primaries/matrix carried through (tags + bitstream VUI), captions burned
    on top. Platforms ingest HLG HEVC natively — it is what iPhones upload.
    Returns the video-encoder arg list, or None for SDR sources (x264 path).
    Graded looks still go through hdr_to_sdr_vf — a look is an SDR deliverable.

    preset: x265 speed. Default medium (near-transparent with CRF 14).
    Callers may pass "fast" only when wall-clock forces it; duration-scaled
    timeouts are the normal budget control, not a softer encode.
    """
    if not is_hdr_color(color_info):
        return None
    info = color_info or {}
    trc = (info.get("color_transfer") or "").strip().lower() or "arib-std-b67"
    spc = (info.get("color_space") or "").strip().lower() or "bt2020nc"
    p = (preset or "medium").strip().lower()
    if p not in {
        "ultrafast", "superfast", "veryfast", "faster", "fast",
        "medium", "slow", "slower", "veryslow",
    }:
        p = "medium"
    # CRF 14 matches the SDR near-transparent bar. preset default medium —
    # "fast" was for a fixed 600s budget that no longer applies (duration-scaled
    # timeouts). Callers may still pass preset="fast" if they need speed.
    return [
        "-c:v", "libx265", "-tag:v", "hvc1", "-pix_fmt", "yuv420p10le",
        "-preset", p, "-crf", "14",
        "-x265-params", f"colorprim=bt2020:transfer={trc}:colormatrix={spc}:range=limited",
        "-colorspace", spc, "-color_primaries", "bt2020",
        "-color_trc", trc, "-color_range", "tv",
    ]


def moment_encode_timeout_seconds(duration: float, *, native_hdr: bool) -> int:
    """Per-clip ffmpeg wall clock for process-moments.

    Fixed 600s was killing ~2 min native-HLG 4K encodes (IMG_7159 m2 vertical
    + horizontal both "Timed out"). Scale with source window; native x265 is
    slower than SDR x264. Cap at 45 min so a runaway encode cannot hang the
    voice worker forever.
    """
    try:
        dur = max(0.0, float(duration or 0.0))
    except (TypeError, ValueError):
        dur = 0.0
    # Base + per-second budget. Native HLG 4K x265 needs more headroom.
    per_sec = 20.0 if native_hdr else 10.0
    base = 900 if native_hdr else 600
    return int(min(2700, max(base, base + per_sec * dur)))


def sdr_still_vf() -> str:
    """Limited-range SDR → full-range JPEG (browser stills are full-range)."""
    return (
        # out_color_matrix intentionally UNSET: ffmpeg 7.x rejects 'auto' here,
        # and unset preserves the pre-existing still behavior (input matrix kept).
        "scale=in_range=auto:out_range=pc"
        ":flags=lanczos+accurate_rnd+full_chroma_int"
        ":in_color_matrix=auto,"
        "format=yuvj420p"
    )


def color_prep_vf(color_info: dict | None = None) -> str:
    """Publish-path prefix: HDR→SDR when needed; empty for ordinary SDR."""
    if is_hdr_color(color_info):
        return hdr_to_sdr_vf(color_info, for_still=False)
    return ""


def frame_still_vf(color_info: dict | None = None) -> str:
    """Filtergraph for crop-page stills that match the video element.

    HDR (HLG/PQ): tonemap to bt709 full-range JPEG.
    SDR limited-range: expand tv→pc so blacks aren't lifted in the browser.
    Geometry-only / no look grade — CSS owns Original/Natural/etc.
    """
    if is_hdr_color(color_info):
        return hdr_to_sdr_vf(color_info, for_still=True)
    return sdr_still_vf()


def frame_still_ffmpeg_cmd(
    video_path: str,
    t: float,
    color_info: dict | None = None,
) -> list:
    """Build the ffmpeg argv for one identity still JPEG (testable)."""
    if color_info is None:
        color_info = probe_video_color(video_path)
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-ss", str(t), "-i", video_path,
        "-vframes", "1",
        "-vf", frame_still_vf(color_info),
        "-q:v", "2",
        "-color_range", "pc",
        "-colorspace", "bt709",
        "-color_primaries", "bt709",
        "-color_trc", "bt709",
        "-f", "image2",
        "pipe:1",
    ]


@router.get("/api/video/frame")
async def extract_frame(request: Request, filename: str = "", t: float = -1):
    """Extract a single frame from a video file as JPEG.

    Query params:
        filename: Video filename (looked up in /video/processing/ or /video/inbox/)
        t: Timestamp in seconds (-1 = mid-point)

    Returns: JPEG image (full-range, identity — no Look grade; CSS applies that).
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

    # Identity still: HDR→SDR when needed, else limited→full-range JPEG so the
    # crop preview matches the native video player. Look grade stays CSS-only.
    try:
        result = subprocess.run(
            frame_still_ffmpeg_cmd(video_path, t, probe_video_color(video_path)),
            capture_output=True, timeout=30,
        )
        if result.returncode != 0 or not result.stdout:
            err = (result.stderr or b"")[-400:].decode("utf-8", errors="replace")
            logger.error("Frame extraction failed t=%s: %s", t, err)
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

        # Duration: format first, then stream. Guard N/A / empty so the crop
        # scrubber never gets max=0 (range pegged at left).
        def _dur(val) -> float:
            try:
                if val is None or val == "" or val == "N/A":
                    return 0.0
                d = float(val)
                return d if d > 0 and d == d else 0.0  # NaN check
            except (TypeError, ValueError):
                return 0.0

        duration = _dur(fmt.get("duration")) or _dur(stream.get("duration"))

        return {
            "filename": filename,
            "width": w,
            "height": h,
            "rotation": rot,
            "duration": duration,
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
    video_filter = crop.get("video_filter", DEFAULT_VIDEO_FILTER)
    border_enabled = crop.get("border_enabled", True)

    # Look preset + optional B/C/S slider overrides (from crop template)
    vf_color = resolve_look_vf(crop)
    color_info = probe_video_color(video_path)
    vf_prep = color_prep_vf(color_info)
    # Original (no grade) + HDR source → native HLG/PQ passthrough: no SDR
    # conversion at all; encoder carries the source color through.
    # medium + CRF 14: same quality bar as SDR publish; matrix kept on scale.
    native_v_args = (
        native_hdr_encode_args(color_info, preset="medium") if not vf_color else None
    )
    if native_v_args:
        vf_prep = ""
        logger.info(
            "Original+HDR → NATIVE COLOR PASSTHROUGH (10-bit HEVC medium/crf14, no tonemap)"
        )
    scale_matrix = scale_out_matrix(color_info, native_hdr=bool(native_v_args))
    logger.info(
        "Look filter: preset=%s → %s; color_prep=%s (trc=%s prim=%s)",
        video_filter,
        vf_color or "identity (no eq)",
        "hdr→sdr" if vf_prep else "none",
        color_info.get("color_transfer") or "?",
        color_info.get("color_primaries") or "?",
    )
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
                # HDR→SDR first (when needed), then geometry; look grade last.
                if fmt == "vertical" and border_enabled:
                    # Crop → hq scale to square → pad with black bars → optional look
                    video_h = fmt_out_h - fmt_bar_top - fmt_bar_bot
                    vf = join_vf(
                        vf_prep,
                        _square_crop_expr(src_w, src_h, src_x, src_y),
                        hq_scale(fmt_out_w, video_h, out_matrix=scale_matrix),
                        f"pad={fmt_out_w}:{fmt_out_h}:0:{fmt_bar_top}:black",
                        vf_color,
                    )
                elif fmt == "vertical":
                    # Vertical without border — full 9:16 rect crop (not square stretch)
                    vf = join_vf(
                        vf_prep,
                        _rect_crop_expr(src_w, src_h, src_x, src_y),
                        hq_scale(fmt_out_w, fmt_out_h, out_matrix=scale_matrix),
                        vf_color,
                    )
                elif fmt == "horizontal":
                    # Pillarboxed: same square crop as vertical, placed in 16:9 frame
                    h_pad_left = (fmt_out_w - h_square) // 2
                    if border_enabled and src_w > 0:
                        vf = join_vf(
                            vf_prep,
                            _square_crop_expr(src_w, src_h, src_x, src_y),
                            hq_scale(h_square, h_square, out_matrix=scale_matrix),
                            f"pad={fmt_out_w}:{fmt_out_h}:{h_pad_left}:{fmt_bar_top}:black",
                            vf_color,
                        )
                    else:
                        vf = join_vf(
                            vf_prep,
                            r"crop=min(iw\,ih):min(iw\,ih)",
                            hq_scale(h_square, h_square, out_matrix=scale_matrix),
                            f"pad={fmt_out_w}:{fmt_out_h}:{h_pad_left}:{fmt_bar_top}:black",
                            vf_color,
                        )
                elif fmt == "square":
                    # Same 1:1 crop as vertical → hq scale to output square
                    vf = join_vf(
                        vf_prep,
                        _square_crop_expr(src_w, src_h, src_x, src_y),
                        hq_scale(fmt_out_w, fmt_out_h, out_matrix=scale_matrix),
                        vf_color,
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
                    # Border-off vertical: geometry bars are 0 (full 9:16 fill) but
                    # captions still sit in the template bottom zone — same place as
                    # the transparent caption overlay in the crop preview.
                    ass_bar_top = fmt_bar_top
                    ass_bar_bot = fmt_bar_bot
                    if fmt == "vertical" and not border_enabled:
                        ass_bar_top = 0
                        ass_bar_bot = bar_bot  # template bottom zone from crop page
                    ass_path = _generate_ass_subtitles(
                        segments=transcript_segments,
                        start_time=start,
                        end_time=end,
                        caption_style=caption,
                        format_name=fmt,
                        out_w=fmt_out_w,
                        out_h=fmt_out_h,
                        bar_top=ass_bar_top,
                        bar_bot=ass_bar_bot,
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
                    # CFR at SOURCE rate when known — hard-coded 30fps was
                    # re-timing 24/60fps iPhone and looking soft vs QuickTime.
                    *encode_fps_args(video_path),
                    # Native passthrough (Original+HDR) or the SDR bt709 x264 bar.
                    *(native_v_args or [
                        "-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p",
                        # CRF 14 + slow: near-transparent after crop/scale; bt709
                        # tags stop players mis-reading limited-range as washed.
                        "-preset", "slow", "-crf", "14",
                        "-x264-params", "colorprim=bt709:transfer=bt709:colormatrix=bt709",
                        "-colorspace", "bt709", "-color_primaries", "bt709",
                        "-color_trc", "bt709", "-color_range", "tv",
                    ]),
                    "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                    "-movflags", "+faststart",
                    out_tmp,
                ]

                enc_timeout = moment_encode_timeout_seconds(
                    duration, native_hdr=bool(native_v_args),
                )
                logger.info(
                    f"Processing m{m_id} ({m_type}) [{fmt}]: "
                    f"{start:.1f}s → {end:.1f}s (timeout {enc_timeout}s, "
                    f"{'native-hdr' if native_v_args else 'sdr'})"
                )
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=enc_timeout,
                )

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
                    # A native-HLG final needs a tonemap in its SDR preview, or
                    # the UI thumbnail plays washed and misleads the operator.
                    _prev_prep = color_prep_vf(probe_video_color(final_path))
                    _prev_vf = (f"{_prev_prep}," if _prev_prep else "") + f"scale={preview_scale}"
                    preview_cmd = [
                        "ffmpeg", "-y", "-i", final_path,
                        "-vf", _prev_vf,
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

            except subprocess.TimeoutExpired as te:
                to_s = getattr(te, "timeout", None) or "?"
                msg = (
                    f"Timed out after {to_s}s "
                    f"(clip {duration:.1f}s, "
                    f"{'native-hdr' if native_v_args else 'sdr'})"
                )
                errors.append({"moment_id": m_id, "format": fmt, "error": msg})
                logger.error(f"m{m_id} [{fmt}] {msg}")
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
    if errors:
        # Surface the real failure strings in logs — "0 done, 2 errors" alone
        # forces another round-trip to the manifest just to see why.
        for err in errors[:8]:
            logger.error(
                "moments error moment_id=%s format=%s: %s",
                err.get("moment_id"),
                err.get("format"),
                (err.get("error") or "")[:500],
            )

    return JSONResponse({
        "processed": processed,
        "errors": errors,
        "manifest_file": manifest_name,
    })



@router.post("/api/video/heal-inbox-processing")
async def heal_inbox_processing(request: Request):
    """Ensure original is in processing/ and cleared from inbox/ (post-transcribe).

    Body: { "filename": "short-horses.mov" }
    """
    body = await request.json()
    filename = (body.get("filename") or "").strip()
    if not filename:
        return JSONResponse({"error": "No filename"}, status_code=400)
    nc = NCSession.from_request(request, body)
    from src.video_lifecycle import ensure_inbox_cleared_after_processing
    result = await ensure_inbox_cleared_after_processing(os.path.basename(filename), nc=nc)
    return JSONResponse(result)



def _find_captioned_full_on_mount(nc_user: str, stem: str) -> tuple[str, str]:
    """Return (local_path, filename) for plain or title-renamed captioned full on NC data mount."""
    import glob as glob_mod
    plain = f"{stem}-captioned.mp4"
    on_disk = find_on_nc_data(nc_user, plain, subdirs=("shorts", "captioned"))
    if on_disk and os.path.isfile(on_disk):
        return on_disk, plain
    # Title-renamed: STEM-*-captioned.mp4 under shorts/captioned on the mount.
    if not NC_HTML_ROOT or not nc_user:
        return "", ""
    user = str(nc_user).strip().strip("/")
    for sub in ("shorts", "captioned"):
        base = os.path.join(NC_HTML_ROOT, "data", user, "files", NC_VIDEO_PATH, sub)
        if not os.path.isdir(base):
            continue
        matches = sorted(glob_mod.glob(os.path.join(base, f"{stem}-*-captioned.mp4")))
        # Prefer non-plain (title) matches; plain already checked.
        for m in matches:
            name = os.path.basename(m)
            if name == plain:
                continue
            if os.path.isfile(m):
                return m, name
    return "", ""


async def _resolve_captioned_full(stem: str, nc: "NCSession" = None) -> tuple[str, str]:
    """Locate captioned full for stem. Returns (local_path_or_empty, filename_or_empty).

    Order: NC data mount (plain then renamed) → scratch plain/renamed → WebDAV
    PROPFIND plain then renamed listing is not available cheaply, so pull plain
    last. Never WebDAV-pull multi-GB renamed files just to answer exists.
    """
    import glob as glob_mod
    stem = (stem or "").strip()
    if not stem:
        return "", ""
    plain = f"{stem}-captioned.mp4"

    if nc is not None:
        path, name = _find_captioned_full_on_mount(nc.user, stem)
        if path:
            return path, name
        scratch_dir = os.path.join("/tmp/cove-video", nc.user, "shorts")
        local_plain = os.path.join(scratch_dir, plain)
        if os.path.isfile(local_plain):
            return local_plain, plain
        if os.path.isdir(scratch_dir):
            matches = sorted(glob_mod.glob(os.path.join(scratch_dir, f"{stem}-*-captioned.mp4")))
            for m in matches:
                name = os.path.basename(m)
                if name != plain and os.path.isfile(m):
                    return m, name
        # Last resort: pull plain only (small meta / remote GPU). Renamed multi-GB
        # objects are not pulled for exists checks.
        if await nc.pull(f"shorts/{plain}", local_plain):
            return local_plain, plain
        # PROPFIND plain already failed via pull; try listing via file_meta on
        # common pattern is not possible without DAV list. Attempt exists on
        # known rename from /tmp/cove-out if present.
        out_dir = "/tmp/cove-out"
        if os.path.isdir(out_dir):
            matches = sorted(glob_mod.glob(os.path.join(out_dir, f"{stem}-*-captioned.mp4")))
            for m in matches:
                name = os.path.basename(m)
                if os.path.isfile(m):
                    return m, name
            plain_out = os.path.join(out_dir, plain)
            if os.path.isfile(plain_out):
                return plain_out, plain
        return "", ""

    shorts_dir = os.path.join(VIDEO_MOUNT, "shorts")
    path = os.path.join(shorts_dir, plain)
    if os.path.isfile(path):
        return path, plain
    matches = sorted(glob_mod.glob(os.path.join(shorts_dir, f"{stem}-*-captioned.mp4")))
    if matches:
        path = matches[0]
        return path, os.path.basename(path)
    return "", ""


@router.get("/api/video/caption-full-exists")
async def caption_full_exists(request: Request, stem: str = ""):
    """Check if a captioned full-length video already exists on disk.

    Sees both plain STEM-captioned.mp4 and title-renamed STEM-*-captioned.mp4
    (rename-captioned). Mount/scratch first — never WebDAV-pull multi-GB just
    to answer exists=true.
    """
    if not stem:
        return JSONResponse({"exists": False})

    nc = NCSession.from_request(request)
    path, filename = await _resolve_captioned_full(stem, nc)
    if not path or not filename:
        return JSONResponse({"exists": False})
    size_mb = os.path.getsize(path) / (1024 * 1024)
    return JSONResponse({
        "exists": True,
        "filename": filename,
        "size_mb": round(size_mb, 1),
    })



@router.post("/api/video/rename-captioned")
async def rename_captioned(request: Request):
    """Rename a captioned full-length video to include the title.

    Body: { "stem": "IMG_7129", "title": "The Lucid Cove AI for Families" }
    Renames: IMG_7129-captioned.mp4 → IMG_7129-The_Lucid_Cove_AI_for_Families-captioned.mp4
    Returns: { "old_name": "...", "new_name": "...", "nc_path": "..." }

    Also accepts already-titled names: if plain stem-captioned.mp4 is gone but a
    title-renamed file exists, returns that path (idempotent).
    """
    body = await request.json()
    stem = body.get("stem", "").strip()
    title = body.get("title", "").strip()
    if not stem or not title:
        return JSONResponse({"error": "stem and title required"}, status_code=400)

    # Per-presence NC session (cove-core injects X-NC-* headers); None = local mount.
    nc = NCSession.from_request(request, body)

    # Sanitize title for filename: replace spaces with _, strip unsafe chars
    import re as _re
    safe_title = _re.sub(r'[^\w\s-]', '', title).strip()
    safe_title = _re.sub(r'\s+', '_', safe_title)[:80]
    new_name = f"{stem}-{safe_title}-captioned.mp4"
    nc_path = f"{NC_VIDEO_PATH}/shorts/{new_name}"
    old_name = f"{stem}-captioned.mp4"

    def _size_mb(path: str) -> float:
        try:
            return round(os.path.getsize(path) / (1024 * 1024), 1)
        except OSError:
            return 0.0

    def _chown_www(path: str) -> None:
        try:
            www_uid = int(os.environ.get("NC_WWW_UID", "33"))
            www_gid = int(os.environ.get("NC_WWW_GID", "33"))
            os.chown(path, www_uid, www_gid)
        except OSError:
            pass

    def _fix_mount_perms(path: str) -> None:
        """docker cp recovery leaves root:root; NC php-fpm can't MOVE that."""
        if not path or not os.path.isfile(path):
            return
        try:
            st = os.stat(path)
            www_uid = int(os.environ.get("NC_WWW_UID", "33"))
            if st.st_uid == 0 or st.st_uid != www_uid:
                _chown_www(path)
                try:
                    os.chmod(path, 0o644)
                except OSError:
                    pass
                logger.info(
                    f"rename-captioned: fixed ownership on {os.path.basename(path)} "
                    f"(was uid={st.st_uid})"
                )
        except OSError as e:
            logger.warning(f"rename-captioned: chown probe failed: {e}")

    if nc is not None:
        on_disk_old = find_on_nc_data(nc.user, old_name, subdirs=("shorts", "captioned"))
        on_disk_new = find_on_nc_data(nc.user, new_name, subdirs=("shorts", "captioned"))

        # Already renamed to this title — idempotent success
        if on_disk_new and os.path.isfile(on_disk_new):
            _fix_mount_perms(on_disk_new)
            logger.info(f"Renamed captioned already done: {new_name}")
            return JSONResponse({
                "old_name": old_name,
                "new_name": new_name,
                "nc_path": nc_path,
                "size_mb": _size_mb(on_disk_new),
                "already": True,
            })

        _fix_mount_perms(on_disk_old)

        # Prefer in-place rename on NC data mount (zero copy).
        if on_disk_old and os.path.isfile(on_disk_old):
            new_disk = os.path.join(os.path.dirname(on_disk_old), new_name)
            try:
                os.rename(on_disk_old, new_disk)
                _chown_www(new_disk)
                try:
                    os.chmod(new_disk, 0o644)
                except OSError:
                    pass
                _nc_scan(f"{NC_VIDEO_PATH}/shorts")
                logger.info(f"Renamed captioned on mount: {old_name} → {new_name}")
                return JSONResponse({
                    "old_name": old_name,
                    "new_name": new_name,
                    "nc_path": nc_path,
                    "size_mb": _size_mb(new_disk),
                })
            except OSError as e:
                logger.warning(f"Mount rename failed ({e}); trying WebDAV MOVE")

        # WebDAV MOVE (server-side). Longer timeout — multi-GB metadata ops on NC
        # can exceed the default 120s used for small lifecycle moves.
        moved = await nc.move(
            f"shorts/{old_name}", f"shorts/{new_name}", timeout=600.0
        )
        if moved:
            on_disk_new = find_on_nc_data(
                nc.user, new_name, subdirs=("shorts", "captioned")
            )
            _fix_mount_perms(on_disk_new)
            logger.info(f"Renamed captioned via WebDAV MOVE: {old_name} → {new_name}")
            return JSONResponse({
                "old_name": old_name,
                "new_name": new_name,
                "nc_path": nc_path,
                "size_mb": _size_mb(on_disk_new) if on_disk_new else 0.0,
            })

        # Last resort: host-side copy via Nextcloud container is operator recovery;
        # here only confirm existence for error clarity.
        local = os.path.join("/tmp/cove-video", nc.user, "shorts", old_name)
        exists = (
            (on_disk_old and os.path.isfile(on_disk_old))
            or os.path.isfile(local)
            or await nc.pull(f"shorts/{old_name}", local)
        )
        if not exists:
            return JSONResponse(
                {"error": f"Captioned file not found: {stem}"}, status_code=404
            )
        return JSONResponse(
            {
                "error": (
                    f"Rename MOVE failed for {old_name} → {new_name}. "
                    "If this file was docker-cp recovered, chown to www-data (uid 33) "
                    "on the Nextcloud data file and retry finalize-captioned-full."
                )
            },
            status_code=502,
        )

    # Local VIDEO_MOUNT path (founder / no NC session)
    import glob as glob_mod
    shorts_dir = os.path.join(VIDEO_MOUNT, "shorts")
    old_path = os.path.join(shorts_dir, old_name)
    new_path = os.path.join(shorts_dir, new_name)
    if os.path.isfile(new_path):
        return JSONResponse({
            "old_name": old_name,
            "new_name": new_name,
            "nc_path": nc_path,
            "size_mb": _size_mb(new_path),
            "already": True,
        })
    if not os.path.isfile(old_path):
        matches = glob_mod.glob(os.path.join(shorts_dir, f"{stem}-*-captioned.mp4"))
        if matches:
            # Already titled under a (possibly different) name — report it
            fname = os.path.basename(matches[0])
            return JSONResponse({
                "old_name": old_name,
                "new_name": fname,
                "nc_path": f"{NC_VIDEO_PATH}/shorts/{fname}",
                "size_mb": _size_mb(matches[0]),
                "already": True,
            })
        return JSONResponse(
            {"error": f"Captioned file not found: {stem}"}, status_code=404
        )
    os.rename(old_path, new_path)
    logger.info(f"Renamed captioned: {old_name} → {new_name}")
    return JSONResponse({
        "old_name": old_name,
        "new_name": new_name,
        "nc_path": nc_path,
        "size_mb": _size_mb(new_path),
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
        "video_filter": "original"  // optional — identity when omitted
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

    # Guard: skip if captioned full already exists (plain or title-renamed).
    # Mount/scratch first — do not WebDAV-pull multi-GB just to skip encode.
    existing_path, existing_name = await _resolve_captioned_full(stem, nc)
    if existing_path and existing_name:
        size_mb = os.path.getsize(existing_path) / (1024 * 1024)
        # Best-effort duration for social_queue (ffprobe); 0 if unavailable.
        duration_seconds = 0.0
        try:
            import subprocess as _sp
            _p = _sp.run(
                [
                    "ffprobe", "-v", "error", "-show_entries",
                    "format=duration", "-of",
                    "default=noprint_wrappers=1:nokey=1", existing_path,
                ],
                capture_output=True, text=True, timeout=60,
            )
            if _p.returncode == 0 and _p.stdout.strip():
                duration_seconds = float(_p.stdout.strip())
        except Exception:
            pass
        logger.info(
            f"Caption-full already exists: {existing_name} ({size_mb:.1f} MB) — skipping render"
        )
        return JSONResponse({
            "filename": existing_name,
            "nc_path": f"{NC_VIDEO_PATH}/shorts/{existing_name}",
            "size_mb": round(size_mb, 1),
            "duration_seconds": round(duration_seconds, 1),
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

    # Look preset + optional B/C/S overrides (body and/or crop_template)
    video_filter = body.get("video_filter") or (body.get("crop_template") or {}).get("video_filter") or DEFAULT_VIDEO_FILTER
    look_src = dict(body.get("crop_template") or {})
    look_src["video_filter"] = video_filter
    for k in ("filter_brightness", "filter_contrast", "filter_saturation"):
        if k in body and body.get(k) is not None:
            look_src[k] = body.get(k)
    vf_color = resolve_look_vf(look_src)
    color_info = probe_video_color(video_path)
    vf_prep = color_prep_vf(color_info)
    # Original (no grade) + HDR source → native passthrough (see moments path).
    native_v_args = native_hdr_encode_args(color_info) if not vf_color else None
    if native_v_args:
        vf_prep = ""
        logger.info("Original+HDR (caption-full) → NATIVE COLOR PASSTHROUGH")
    scale_matrix = scale_out_matrix(color_info, native_hdr=bool(native_v_args))
    logger.info(
        "Look filter (caption-full): preset=%s → %s; color_prep=%s (trc=%s)",
        video_filter,
        vf_color or "identity (no eq)",
        "hdr→sdr" if vf_prep else "none",
        color_info.get("color_transfer") or "?",
    )

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
            # HDR prep → crop to operator's square → hq scale → pillarbox → look
            vf = join_vf(
                vf_prep,
                _square_crop_expr(src_w, src_h, src_x, src_y),
                hq_scale(h_square, h_square, out_matrix=scale_matrix),
                f"pad={fmt_out_w}:{fmt_out_h}:{pad_left}:{h_bar_top}:black",
                vf_color,
            )
            logger.info(
                f"Caption-full: crop {src_w}x{src_h}@{src_x},{src_y} → "
                f"square {h_square}px → pillarbox {fmt_out_w}x{fmt_out_h} "
                f"(left={pad_left}, top={h_bar_top}, bot={h_bar_bot}); "
                f"look={vf_color or 'identity'}; prep={'hdr→sdr' if vf_prep else 'none'}"
            )
        else:
            # Fallback: center-crop to square from source, then same layout
            vf = join_vf(
                vf_prep,
                r"crop=min(iw\,ih):min(iw\,ih)",
                hq_scale(h_square, h_square, out_matrix=scale_matrix),
                f"pad={fmt_out_w}:{fmt_out_h}:{pad_left}:{h_bar_top}:black",
                vf_color,
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
            # CFR at SOURCE rate when known (not hard-coded 30).
            *encode_fps_args(video_path),
            # Native passthrough (Original+HDR) or the SDR bt709 x264 bar.
            *(native_v_args or [
                "-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p",
                # Match short quality bar: slow + CRF 14 + explicit bt709 tags.
                "-preset", "slow", "-crf", "14",
                "-x264-params", "colorprim=bt709:transfer=bt709:colormatrix=bt709",
                "-colorspace", "bt709", "-color_primaries", "bt709",
                "-color_trc", "bt709", "-color_range", "tv",
            ]),
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-movflags", "+faststart",
            "-threads", "0",
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

        # Stage the finished video locally, then publish to the presence's NC.
        # Prefer NC data mount write inside publish_video_output (multi-GB safe);
        # WebDAV is fallback only. Never graduate or 200 if publish fails — the
        # local /tmp/cove-out file is kept for operator recovery.
        import shutil
        local_out_dir = "/tmp/cove-out"
        os.makedirs(local_out_dir, exist_ok=True)
        dest_path = os.path.join(local_out_dir, out_name)
        shutil.move(out_tmp, dest_path)
        size_mb = os.path.getsize(dest_path) / (1024 * 1024)
        nc_path = f"{NC_VIDEO_PATH}/shorts/{out_name}"
        wrote = await publish_video_output(dest_path, f"shorts/{out_name}", nc, "video/mp4")

        if ass_path and os.path.isfile(ass_path):
            os.remove(ass_path)

        if not wrote:
            logger.error(
                f"Captioned full publish FAILED: {out_name} ({size_mb:.1f} MB) "
                f"still at {dest_path} — not graduating, not claiming success"
            )
            return JSONResponse({
                "error": (
                    f"Captioned full rendered ({size_mb:.1f} MB) but failed to publish "
                    f"to shorts/{out_name}. Local recovery path on voice: {dest_path}"
                ),
                "filename": out_name,
                "local_path": dest_path,
                "size_mb": round(size_mb, 1),
                "published": False,
            }, status_code=502)

        logger.info(f"Captioned full video done: {out_name} ({size_mb:.1f} MB) → {dest_path}")

        # C4 #1 — captioned-full published for this stem: GRADUATE the original
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
            "published": True,
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

@router.get("/api/video/read-json")
async def read_json(request: Request, subpath: str = ""):
    """Read a JSON file from the video tree (scratch / NC mount / WebDAV).

    Query: subpath=transcripts/STEM-transcript.json (relative, no leading slash).
    Used by cove-core metadata generation so title rename does not fall back to
    "{stem} — Full Video" when the app's NC read races voice scratch/publish.
    """
    sub = (subpath or "").strip().lstrip("/")
    if not sub or ".." in sub or sub.startswith("/"):
        return JSONResponse({"error": "Invalid subpath"}, status_code=400)

    nc = NCSession.from_request(request)

    # 1) Voice scratch (often warmer than NC right after STT/publish)
    if nc is not None:
        local = os.path.join("/tmp/cove-video", nc.user, sub)
        if os.path.isfile(local):
            try:
                with open(local) as f:
                    return JSONResponse(json.load(f))
            except Exception as e:
                logger.warning("read-json scratch parse failed %s: %s", sub, e)

    # 2) NC data mount
    if nc is not None:
        name = os.path.basename(sub)
        parent = os.path.dirname(sub)
        on_disk = find_on_nc_data(
            nc.user, name, subdirs=(parent,) if parent else ("transcripts", "shorts"),
        )
        if on_disk and os.path.isfile(on_disk):
            try:
                with open(on_disk) as f:
                    return JSONResponse(json.load(f))
            except Exception as e:
                logger.warning("read-json mount parse failed %s: %s", sub, e)

    # 3) WebDAV pull into scratch then parse
    if nc is not None:
        local = os.path.join("/tmp/cove-video", nc.user, sub)
        os.makedirs(os.path.dirname(local) or ".", exist_ok=True)
        if await nc.pull(sub, local) and os.path.isfile(local):
            try:
                with open(local) as f:
                    return JSONResponse(json.load(f))
            except Exception as e:
                logger.warning("read-json pull parse failed %s: %s", sub, e)
        return JSONResponse({"error": f"Not found: {sub}"}, status_code=404)

    # Local VIDEO_MOUNT (founder / no NC session)
    path = os.path.join(VIDEO_MOUNT, sub)
    if not os.path.isfile(path):
        return JSONResponse({"error": f"Not found: {sub}"}, status_code=404)
    try:
        with open(path) as f:
            return JSONResponse(json.load(f))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


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
    """Retire a file from the video tree into to-delete/ (never hard-delete).

    Body: { "subpath": "shorts/STEM-moments-processed.json" }

    Operator policy 2026-07-20: user content is moved to to-delete/ so it can be
    offloaded or emptied later — not os.remove'd off the mount (that bypassed NC
    trash and made recovery impossible).
    """
    body = await request.json()
    subpath = (body.get("subpath") or "").strip()

    if not subpath:
        return JSONResponse({"error": "subpath required"}, status_code=400)

    if ".." in subpath or subpath.startswith("/"):
        return JSONResponse({"error": "Invalid subpath"}, status_code=400)

    from src.video_lifecycle import retire_to_delete

    nc = None
    try:
        nc = NCSession.from_request(request, body)
    except Exception:
        nc = None

    result = await retire_to_delete(subpath, nc=nc, video_mount=VIDEO_MOUNT)
    if result.get("ok"):
        logger.info(
            "delete-file retired %s via %s → %s",
            subpath, result.get("method"), result.get("dest") or "(nc trash)",
        )
        return {
            "deleted": True,  # backward-compat key for callers
            "retired": True,
            "method": result.get("method"),
            "dest": result.get("dest") or "",
            "path": result.get("dest") or subpath,
        }
    reason = result.get("reason") or "retire failed"
    if reason == "not found":
        return {"deleted": False, "retired": False, "reason": "not found"}
    logger.error("delete-file FAILED: %s — %s", subpath, reason)
    return JSONResponse({"error": f"Retire failed: {reason}"}, status_code=500)

