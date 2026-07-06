"""
Video Pipeline Routes — Cove Team Tool

Orchestrates the video shorts pipeline:
- Step 1: Trigger transcription (calls pipecat-voice /api/stt/video)
- Step 2: Analyze transcript → identify moments → propose clips (LLM)
- Step 3: Process approved clips (future: ffmpeg cutting)

This is a cove-core route — available to every Cove, every agent.
Stuart coordinates, Julian transcribes, Arthur analyzes, Vera audits.

Session 145, June 2026.
"""

import asyncio
import contextvars
import json
import logging
import os
from src.env import env
import re
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/video", tags=["video-pipeline"])

# A14: when a transcribe/analyze runs inside a background JOB (video_jobs.py) the
# held-open request is gone, so the internal timeouts can be generous. A normal
# phone video 504'd the sync request while the P620 job SUCCEEDED; the standard
# case is 20min/4-5GB and a local-brain moments pass needs minutes. The sync
# endpoints keep their short interactive caps (default False → 600/240).
_JOB_MODE = contextvars.ContextVar("video_job_mode", default=False)


def _job_timeout(interactive: int, job: int) -> int:
    """Pick the timeout for the current context: the short interactive cap for a
    live request, the generous cap when running inside a background job."""
    try:
        return job if _JOB_MODE.get() else interactive
    except Exception:
        return interactive


def _budget_local_client(client) -> None:
    """A13 r3: give a local (Ollama) brain a generous output budget + no-think for
    the moments JSON task, so the generation cap doesn't truncate the JSON mid
    structure. Best-effort across langchain client types (attributes differ);
    never raises. Cloud clients (ChatOpenAI) just get a bigger max_tokens."""
    try:
        model = str(getattr(client, "model", "") or getattr(client, "model_name", "")).lower()
        # Ollama-style knobs.
        if hasattr(client, "num_predict"):
            try:
                client.num_predict = 8192
            except Exception:
                pass
        # A13 r4: ENFORCE JSON at the sampler for Ollama brains — voluntary
        # compliance isn't real on small local models (nottington produced
        # truncated JSON one run and pure prose the next). Ollama's format="json"
        # constrains generation to valid JSON; cloud clients lack the field.
        if hasattr(client, "format"):
            try:
                client.format = "json"
            except Exception:
                pass
        # no-think for the qwen3 family (its <think> block eats the output budget
        # and risks truncating the JSON payload). Ollama exposes think=False.
        if "qwen3" in model and hasattr(client, "think"):
            try:
                client.think = False
            except Exception:
                pass
        # OpenAI-style output cap.
        if hasattr(client, "max_tokens") and not getattr(client, "max_tokens", None):
            try:
                client.max_tokens = 8192
            except Exception:
                pass
    except Exception:
        pass

# Pipecat-voice service URL (audio/media processor). Default to THIS Cove's own in-network
# voice container, which the provisioner wires as VOICE_INTERNAL_URL (http://{cove}-voice:8300).
# Without this, video fell back to a hardcoded host.docker.internal:8300 — a legacy hand-built
# pipecat that only exists on the founder's host (and nothing on a stranger's), so video work
# never reached the repo-provisioned voice container. An explicit PIPECAT_URL still wins
# (co-located reuse / a rented remote GPU). See LP-Vault/Reference/cross-cove-gpu-share-spec.md.
PIPECAT_URL = env("PIPECAT_URL") or env("VOICE_INTERNAL_URL") or "http://host.docker.internal:8300"

# Video folder — read via filesystem mount (read-only in cove agents)
VIDEO_BASE = env("VIDEO_BASE_PATH", "/vault/AgentSkills/Content/video")

# Cloud ASR keys cloud_asr.py uses (Groq / OpenAI / Deepgram). If any is set, a
# GPU-less Cove can still transcribe via the cloud. None set + no GPU = degraded.
_ASR_KEY_ENVS = ("GROQ_API_KEY", "OPENAI_API_KEY", "DEEPGRAM_API_KEY")


def _transcription_available() -> dict:
    """Can this Cove transcribe video, and how?

    CF-96: reads the ONE compute resolver (compute_status) so this gate can never
    drift from what Settings / Rent GPU / Pipeline Services display. GPU
    (video_asr local/external, external needing url+token) → gpu; cloud → only
    with a BYOK ASR key (env or saved in-app); otherwise the CPU-degraded
    pipeline. Returns {enabled, backend: gpu|cloud|none, mode, path, label}."""
    try:
        from src.compute_status import compute_status
        va = compute_status()["video_asr"]
        return {"enabled": bool(va["ready"]), "backend": va["backend"],
                "mode": va["mode"], "path": va.get("path", ""),
                "label": va.get("label", "")}
    except Exception:
        # Defensive fallback: never let a resolver hiccup hide the pipeline.
        mode = "cloud"
        try:
            from src.config import get_compute_config
            mode = ((get_compute_config() or {}).get("video_asr") or {}).get("mode") or "cloud"
        except Exception:
            pass
        if mode in ("local", "external"):
            return {"enabled": True, "backend": "gpu", "mode": mode}
        has_key = any(env(k) for k in _ASR_KEY_ENVS)
        return {"enabled": bool(has_key), "backend": "cloud" if has_key else "none", "mode": mode}


@router.get("/capabilities")
async def video_capabilities(request: Request):
    """Drives the UI. When transcription is unavailable (no GPU + no cloud ASR key) the
    Video Pipeline degrades to schedule + posting only, and offers connect/rent a GPU."""
    cap = _transcription_available()
    return {
        "transcription": cap["enabled"],
        "asr_backend": cap["backend"],
        "video_asr_mode": cap["mode"],
        # CF-96: the active-path label/key from the one resolver, so the panel
        # header can state "Transcription: rented GPU (host)" without re-deriving.
        "asr_path": cap.get("path", ""),
        "asr_label": cap.get("label", ""),
    }


async def pipecat_nc_headers(request) -> dict:
    """Per-presence NC credentials for pipecat (the stateless WebDAV processor).

    Returns X-NC-* headers so pipecat authenticates AS the current presence and can
    only touch THAT presence's NC files — never admin, never another presence.
    Empty in single-mode (founder) → pipecat falls back to its local mount.
    X-NC-URL is the pipecat-reachable NC address (NC_PIPECAT_URL — e.g. internal
    host.docker.internal:8081 when co-located, or the public cloud URL if split).
    """
    try:
        from src.dashboard.routes.presence import get_current_presence
        p = await get_current_presence(request)
        if not p or not p.get("nc_username") or not p.get("nc_password"):
            return {}
        # X-NC-URL = where THIS presence's NC is reachable by whichever pipecat does the
        # work, which depends on WHERE pipecat runs relative to the NC:
        #   local mode    -> pipecat is co-located on this Cove's docker network, so the
        #                    in-network NEXTCLOUD_URL is reachable AND avoids a mis-set
        #                    public URL (e.g. a legacy founder whose NEXTCLOUD_PUBLIC_URL
        #                    is a host-only http://localhost:PORT, dead inside a container).
        #   external mode -> pipecat is on ANOTHER box (a GPU-less Cove renting a remote
        #                    GPU); in-network names don't resolve across the internet, so
        #                    it must be this Cove's PUBLIC NC URL (reachable through Caddy).
        # An explicit NC_PIPECAT_URL always wins (operator/provisioner override).
        explicit = (env("NC_PIPECAT_URL") or "").strip()
        try:
            from src.config import get_compute_config
            _asr_mode = ((get_compute_config() or {}).get("video_asr") or {}).get("mode") or "local"
        except Exception:
            _asr_mode = "local"
        # C2: derive the public URL from the LIVE domain (_cloud_public_url — the
        # CF-93 pattern) instead of the provision-stamped envs, which stay
        # localhost/host.docker.internal forever after a domainless install
        # claims an address. The domainless NC_PIPECAT_URL stamp is a default,
        # not an operator override — for EXTERNAL pipecat it can't cross the
        # internet, so it doesn't count as explicit there.
        from src.config import _cloud_public_url
        if _asr_mode == "external" and "host.docker.internal" in explicit:
            explicit = ""
        if explicit:
            url = explicit
        elif _asr_mode == "external":
            url = _cloud_public_url() or env("NEXTCLOUD_URL")
        else:
            url = env("NEXTCLOUD_URL") or _cloud_public_url()
        return {
            "X-NC-URL": url,
            "X-NC-User": p["nc_username"],
            "X-NC-Pass": p["nc_password"],
        }
    except Exception:
        return {}


async def _read_video_json(request, subpath: str):
    """Read a JSON file from the video tree. Local VIDEO_BASE mount first (founder),
    then the CURRENT presence's Nextcloud via WebDAV (multi-mode / Clearfield, where
    there is no mount). `subpath` is relative to AgentSkills/Content/video. Returns
    a dict or None."""
    # jules 1646 (presence-scope): the local mount is a single-operator vault —
    # in multi mode it would serve ONE operator's video tree to EVERY presence
    # (Stuart's surface showing the operator's pipeline). Multi mode reads the
    # CURRENT presence's own NC namespace only.
    if env("COVE_MODE", "single") != "multi":
        local = os.path.join(VIDEO_BASE, subpath)
        if os.path.isfile(local):
            try:
                with open(local) as f:
                    return json.load(f)
            except Exception:
                pass
    try:
        from src.dashboard.routes.nextcloud import get_nc_creds
        nc_url, nc_user, nc_pass = await get_nc_creds(request)
        # The APP reads its own NC over the in-network nc_url from get_nc_creds
        # (NEXTCLOUD_URL — trusted + reachable on this Cove's docker network).
        # NC_PIPECAT_URL is PIPECAT's reachability override only (see
        # pipecat_nc_headers); applying it to the app's own reads routed through an
        # untrusted host (e.g. host.docker.internal) → NC 500. Leave nc_url as-is.
        if nc_url and nc_user and nc_pass:
            from urllib.parse import quote
            dav = f"{nc_url}/remote.php/dav/files/{nc_user}/AgentSkills/Content/video/{quote(subpath, safe='/')}"
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.get(dav, auth=(nc_user, nc_pass))
                if r.status_code == 200:
                    return r.json()
    except Exception as e:
        logger.warning(f"NC video read failed ({subpath}): {e}")
    return None


def _parse_propfind(xml_text: str, base_path: str) -> list:
    """Parse a WebDAV PROPFIND (Depth:1) response into a list of FILE entries
    (collections/dirs skipped). Returns [{filename, size_mb, modified}]."""
    import xml.etree.ElementTree as ET
    from urllib.parse import unquote
    from email.utils import parsedate_to_datetime
    ns = {"d": "DAV:"}
    out = []
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return out
    base = unquote(base_path).rstrip("/")
    for resp in root.findall("d:response", ns):
        href_el = resp.find("d:href", ns)
        if href_el is None or not href_el.text:
            continue
        href = unquote(href_el.text).rstrip("/")
        if href == base:
            continue  # the collection itself
        rt = resp.find(".//d:resourcetype", ns)
        if rt is not None and rt.find("d:collection", ns) is not None:
            continue  # skip subdirectories
        name = href.rsplit("/", 1)[-1]
        size_el = resp.find(".//d:getcontentlength", ns)
        lm_el = resp.find(".//d:getlastmodified", ns)
        size_mb = 0.0
        if size_el is not None and size_el.text and size_el.text.isdigit():
            size_mb = round(int(size_el.text) / (1024 * 1024), 1)
        modified = 0.0
        if lm_el is not None and lm_el.text:
            try:
                modified = parsedate_to_datetime(lm_el.text).timestamp()
            except Exception:
                modified = 0.0
        out.append({"filename": name, "size_mb": size_mb, "modified": modified})
    out.sort(key=lambda x: x["filename"])
    return out


async def _list_video_dir(request, subdir: str) -> list:
    """List FILES in a video subdir. Local VIDEO_BASE mount first (founder), then the
    CURRENT presence's Nextcloud via WebDAV PROPFIND (multi-mode / Clearfield).
    `subdir` is relative to AgentSkills/Content/video, e.g. 'inbox', 'transcripts'.
    Returns [{filename, size_mb, modified}]."""
    # jules 1646 (presence-scope): local mount = single mode only, same as
    # _read_video_json — multi mode stays inside the presence's NC namespace.
    local = os.path.join(VIDEO_BASE, subdir)
    if env("COVE_MODE", "single") != "multi" and os.path.isdir(local):
        out = []
        for f in sorted(os.listdir(local)):
            p = os.path.join(local, f)
            if os.path.isfile(p):
                st = os.stat(p)
                out.append({
                    "filename": f,
                    "size_mb": round(st.st_size / (1024 * 1024), 1),
                    "modified": st.st_mtime,
                })
        return out
    try:
        from src.dashboard.routes.nextcloud import get_nc_creds
        nc_url, nc_user, nc_pass = await get_nc_creds(request)
        # The APP reads its own NC over the in-network nc_url from get_nc_creds
        # (NEXTCLOUD_URL — trusted + reachable on this Cove's docker network).
        # NC_PIPECAT_URL is PIPECAT's reachability override only (see
        # pipecat_nc_headers); applying it to the app's own reads routed through an
        # untrusted host (e.g. host.docker.internal) → NC 500. Leave nc_url as-is.
        if nc_url and nc_user and nc_pass:
            base_path = f"/remote.php/dav/files/{nc_user}/AgentSkills/Content/video/{subdir}"
            body = ('<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop>'
                    '<d:getcontentlength/><d:getlastmodified/><d:resourcetype/>'
                    '</d:prop></d:propfind>')
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.request(
                    "PROPFIND", f"{nc_url}{base_path}",
                    auth=(nc_user, nc_pass),
                    headers={"Depth": "1", "Content-Type": "application/xml"},
                    content=body,
                )
                if r.status_code in (200, 207):
                    return _parse_propfind(r.text, base_path)
                if r.status_code == 404:
                    return []  # subdir not created yet
                logger.warning(f"NC video dir PROPFIND {subdir}: HTTP {r.status_code}")
    except Exception as e:
        logger.warning(f"NC video dir list failed ({subdir}): {e}")
    return []


async def _pipecat_write_json(subpath: str, data: dict, headers: dict = None) -> bool:
    """Write a JSON file via pipecat-voice's write endpoint.

    Pipecat-voice owns all video file I/O (it has the :rw mount).
    Cove agents have read-only mounts and proxy writes through here.

    Args:
        subpath: Path relative to VIDEO_MOUNT, e.g. "transcripts/STEM-moments.json"
        data: JSON-serializable dict to write
    """
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{PIPECAT_URL}/api/video/write-json",
                json={"subpath": subpath, "data": data},
                headers=headers or {},
            )
            if resp.status_code == 200:
                logger.info(f"pipecat write OK: {subpath}")
                return True
            else:
                logger.error(f"pipecat write failed ({resp.status_code}): {subpath} — {resp.text[:300]}")
                return False
    except Exception as e:
        logger.error(f"pipecat write error: {subpath} — {type(e).__name__}: {e}")
        return False

# NC WebDAV — all file writes go through centralized NC config.
# Stuart's NC user is the admin for this Cove's NC instance.
# Import from config.py — single source for all NC credentials.
from src.config import get_nc_url, get_nc_admin_user, get_nc_admin_password
# NC write paths for video pipeline data.
# Path differs based on WHO is running:
# - Presence agent (Atlas, STEWARD_DATABASE_URL set): writes to own NC space
#   → AgentSkills/Content/video/{subpath}
# - Steward (Stuart, no STEWARD_DATABASE_URL): writes via shared folder namespace
#   → Presences/{name}/Content/video/{subpath}
#
# Both resolve to the same NC files — just different WebDAV mount points.
# The provisioner creates shares so Stuart sees Presence Content as
# Presences/{name}/Content/. The Presence agent sees it as AgentSkills/Content/.

def _steward_video_path(presence_name: str, subpath: str = "") -> str:
    """Build the steward's NC path to a Presence's video folder."""
    base = f"Presences/{presence_name}/Content/video"
    return f"{base}/{subpath}" if subpath else base


def _nc_video_write_path(presence_name: str, subpath: str) -> str:
    """Build the correct NC WebDAV path for video pipeline writes.

    Detects whether we're a Presence agent or the steward and uses
    the appropriate path. Both point to the same underlying files.
    """
    if env("STEWARD_DATABASE_URL"):
        # Presence agent — write to own NC space
        return f"AgentSkills/Content/video/{subpath}"
    else:
        # Steward — write via shared folder namespace
        return _steward_video_path(presence_name, subpath)


# ── Video file lookup ─────────────────────────────────────────────

def _find_video_file(filename: str) -> str | None:
    """Find a video file in any video subfolder.

    Checks shorts/ and transcripts/ first (previews), then processing/, inbox/, raw/.
    """
    # jules 1646 (presence-scope): local mount = single mode only (see
    # _read_video_json) — never serve one operator's files across presences.
    if env("COVE_MODE", "single") == "multi":
        return None
    for subdir in ["shorts", "transcripts", "processing", "inbox", "raw"]:
        candidate = os.path.join(VIDEO_BASE, subdir, filename)
        if os.path.isfile(candidate):
            return candidate
    return None


# ── NC WebDAV helpers ─────────────────────────────────────────────
# Auth as admin, operate on target user's files.
# This is how Stuart manages Presence data — admin writes to any user's space.

def _webdav_url(filepath: str) -> str:
    """Build WebDAV URL in the steward's own NC space.
    Shared folders from Presences appear here — Stuart writes as himself.
    """
    from urllib.parse import quote
    steward = get_nc_admin_user()
    return f"{get_nc_url()}/remote.php/dav/files/{steward}/{quote(filepath, safe='/')}"


async def _nc_put_file(
    filepath: str,
    content: bytes,
    content_type: str = "application/json",
) -> bool:
    """Write a file via the steward's WebDAV.
    Works because Presence team-managed folders are shared with the steward.
    filepath relative to steward's NC root — shared folders appear at their
    share path (e.g. 'Content/video/transcripts/foo.json').
    """
    url = _webdav_url(filepath)
    admin_user = get_nc_admin_user()
    admin_pass = get_nc_admin_password()
    logger.info(f"NC write attempt: {filepath} → {url} (user={admin_user}, {len(content)} bytes)")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.put(
                url,
                auth=(admin_user, admin_pass),
                content=content,
                headers={"Content-Type": content_type},
            )
            if resp.status_code in (200, 201, 204):
                logger.info(f"NC write OK: {filepath}")
                return True
            else:
                logger.error(
                    f"NC write failed ({resp.status_code}): {filepath} "
                    f"| response: {resp.text[:300]}"
                )
                return False
    except Exception as e:
        logger.error(f"NC write error: {filepath} | {type(e).__name__}: {e}")
        return False


async def _nc_move_file(
    src_path: str,
    dst_path: str,
) -> bool:
    """Move a file via the steward's WebDAV.
    Works on shared folders from Presences.
    """
    src_url = _webdav_url(src_path)
    dst_url = _webdav_url(dst_path)
    admin_user = get_nc_admin_user()
    admin_pass = get_nc_admin_password()
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.request(
                "MOVE",
                src_url,
                auth=(admin_user, admin_pass),
                headers={"Destination": dst_url, "Overwrite": "T"},
            )
            if resp.status_code in (201, 204):
                logger.info(f"NC move: {src_path} → {dst_path}")
                return True
            else:
                logger.error(f"NC move failed ({resp.status_code}): {src_path}")
                return False
    except Exception as e:
        logger.error(f"NC move error: {e}")
        return False


# ── Step 1: Trigger transcription ─────────────────────────────────

@router.post("/transcribe")
async def trigger_transcription(request: Request):
    """Trigger video transcription via pipecat-voice GPU service.

    Body: { "filename": "IMG_7129.MOV" }
    Calls pipecat-voice /api/stt/video which handles:
    - Moving file from inbox/ → processing/
    - Transcribing with Qwen3-ASR + ForcedAligner
    - Writing JSON+TXT to transcripts/

    Returns the transcription result.
    """
    import httpx

    body = await request.json()
    filename = body.get("filename", "").strip()
    if not filename:
        return JSONResponse({"error": "No filename provided"}, status_code=400)

    # CPU-degraded guard: no GPU and no cloud ASR key → transcription is off; the
    # pipeline runs in schedule + posting mode. Don't call pipecat (it would fail).
    _cap = _transcription_available()
    if not _cap["enabled"]:
        return JSONResponse(
            {"error": "Transcription isn't available on this Cove (no GPU, no cloud ASR key). "
                      "Add a Groq, OpenAI, or Deepgram key in Settings, connect or rent a GPU, "
                      "or use the pipeline in schedule + posting mode.",
             "transcription_disabled": True},
            status_code=409,
        )

    # #181 — pick the ASR backend from the Cove's compute setting.
    #   local    -> the GPU service (P620/Clearfield run Qwen3-ASR; default)
    #   cloud    -> a BYOK cloud ASR API (hosted / GPU-less Coves)
    #   external -> borrow another box's GPU at compute.video_asr.url
    target = PIPECAT_URL
    payload = {"filename": filename, "timestamps": True}
    mode = "local"
    _vc = {}
    try:
        from src.config import get_compute_config
        _vc = (get_compute_config() or {}).get("video_asr") or {}
        mode = _vc.get("mode") or "local"
        if mode == "external" and _vc.get("url"):
            target = _vc["url"].rstrip("/")
        elif mode == "cloud":
            payload["engine"] = "cloud"
            # AT-1 keys live on the COVE (saved override or env) — pipecat only reads
            # its own env, which the provisioner never stamps with operator keys. Hand
            # the key to the job over the internal network (same trust boundary as the
            # X-NC-* creds these requests already carry); never echoed to the browser.
            from src.dashboard.routes.pipeline_keys import first_asr_provider_key
            _prov, _key = first_asr_provider_key()
            if _key:
                payload["asr_provider"] = _prov
                payload["asr_key"] = _key
    except Exception:
        pass

    # GPU auth (cross-Cove share). external -> send the renter's grant token to the
    # provider's pipecat; local/cloud -> send this Cove's own internal pipecat secret so the
    # provider's gate (when enabled) lets its OWN jobs through. Empty when unconfigured =
    # legacy open behavior. See LP-Vault/Reference/cross-cove-gpu-share-spec.md.
    _auth = {}
    if mode == "external":
        _tok = (_vc.get("token") or "").strip()
        if _tok:
            _auth["X-Cove-GPU-Token"] = _tok
    else:
        _sec = (env("PIPECAT_INTERNAL_SECRET") or "").strip()
        if _sec:
            _auth["X-Pipecat-Secret"] = _sec

    try:
        # #4 — the pipecat call is where the (multi-minute, mesh) transfer + ASR
        # happen; name it so the poll shows "transferring" instead of a bare spinner.
        try:
            from src.dashboard.routes.video_jobs import set_phase
            set_phase("transferring")
        except Exception:
            pass
        _nch = await pipecat_nc_headers(request)
        async with httpx.AsyncClient(timeout=_job_timeout(600, 3600)) as client:
            resp = await client.post(
                f"{target}/api/stt/video",
                json=payload,
                headers={**_nch, **_auth},
            )
            result = resp.json()
            if resp.status_code != 200:
                return JSONResponse(result, status_code=resp.status_code)
            # #183 — record ASR minutes + cost (the video path doesn't go
            # through the LLM cost chokepoint). The engine reports asr_service;
            # local/external GPU = $0 (own hardware), cloud = priced.
            try:
                secs = result.get("audio_duration_seconds")
                if secs:
                    from src.models.provider import write_asr_metric
                    from src.config import get_primary_agent_id
                    _svc = result.get("asr_service") or ("default" if mode == "cloud" else "local")
                    await write_asr_metric(
                        agent_id=get_primary_agent_id(),
                        operation_label=f"video-transcribe/{filename}",
                        minutes=float(secs) / 60.0,
                        service=_svc,
                        model_label=result.get("model") or "asr",
                    )
            except Exception as _asr_e:
                logger.warning(f"ASR cost capture skipped (non-fatal): {_asr_e}")
            return JSONResponse(result)
    except httpx.TimeoutException:
        # In a background job (A14) this cap is 60 min; the sync path is the short
        # one. Either way, prefer the async /transcribe/start flow for big files.
        return JSONResponse(
            {"error": "Transcription request timed out. Large videos should run as a "
                      "background job (they can take many minutes) — retry from the "
                      "pipeline, which polls instead of holding the request open."},
            status_code=504,
        )
    except Exception as e:
        logger.error(f"Transcription trigger error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Step 2: Analyze transcript → moments + clips ──────────────────

@router.post("/analyze")
async def analyze_transcript(request: Request):
    """Analyze a transcript to identify moments and propose clips.

    Body: { "stem": "IMG_7129", "presence_name": "alex" }
    Or: { "transcript_file": "IMG_7129-transcript.json", "presence_name": "alex" }

    presence_name identifies whose Presence data to write to.
    Stuart writes to Presences/{presence_name}/Content/video/transcripts/ in his own NC space.

    Reads the transcript JSON from local mount, sends to LLM for analysis,
    writes moments JSON via steward's NC WebDAV (shared folders).

    Returns: { "moments": [...], "moments_file": "..." }
    """
    body = await request.json()
    transcript_file = body.get("transcript_file", "").strip()
    stem = body.get("stem", "").strip()
    presence_name = body.get("presence_name", "").strip()

    if not presence_name:
        return JSONResponse(
            {"error": "presence_name required — identifies whose video data to write to"},
            status_code=400,
        )

    # Resolve transcript — prefer edited version if it exists (presence NC or founder mount)
    transcript = None
    if not transcript_file and stem:
        edited = await _read_video_json(request, f"transcripts/{stem}-transcript-edited.json")
        if edited is not None:
            transcript = edited
            transcript_file = f"{stem}-transcript-edited.json"
            logger.info(f"Using edited transcript: {transcript_file}")
        else:
            transcript_file = f"{stem}-transcript.json"

    if not transcript_file:
        return JSONResponse(
            {"error": "No transcript_file or stem provided"},
            status_code=400,
        )

    if transcript is None:
        transcript = await _read_video_json(request, f"transcripts/{transcript_file}")
    if transcript is None:
        return JSONResponse(
            {"error": f"Transcript not found: {transcript_file}"},
            status_code=404,
        )

    full_text = transcript.get("text", "")
    segments = transcript.get("segments", [])
    duration = transcript.get("audio_duration_seconds", 0)

    if not full_text:
        return JSONResponse(
            {"error": "Transcript has no text"},
            status_code=400,
        )

    # Build timestamped text for the LLM
    # Group word-level segments into sentence-like chunks for readability
    timestamped_text = _build_timestamped_text(segments, full_text)

    # Call LLM for moments analysis — AS the logged-in presence's agent (chat's
    # resolution, _personal_agent_id). The instance-level agents[0] default missed
    # per-presence model assignments entirely (nottington: Roger chats on a dolphin
    # assignment that moments never consulted while its fallbacks flailed).
    try:
        from src.dashboard.routes.chat import _personal_agent_id
        _aid = await _personal_agent_id(request)
    except Exception:
        _aid = ""
    try:
        moments = await _identify_moments(
            timestamped_text=timestamped_text,
            full_text=full_text,
            duration=duration,
            agent_id=_aid or "",
        )
    except Exception as e:
        logger.error(f"Moments analysis failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

    if not moments:
        return JSONResponse(
            {"error": "No moments identified"},
            status_code=200,
        )

    # Write moments file via pipecat-voice (owns the :rw video mount)
    # Extract the video stem — hyphen-safe (end-anchored suffix strip, #6). A
    # hyphenated stem like "IMG_7168-Test1" is preserved, not truncated at the
    # first hyphen. "IMG_7129-transcript(-edited).json" → "IMG_7129".
    from src.video_stems import stem_from_transcript_name
    file_stem = stem_from_transcript_name(transcript_file)
    moments_file = f"{file_stem}-moments.json"

    # Tag the tier used for this analysis
    dur_mins = int(duration // 60)
    if dur_mins < 5:
        tier = "short"
    elif dur_mins < 15:
        tier = "medium"
    elif dur_mins < 30:
        tier = "long"
    else:
        tier = "extended"

    moments_output = {
        "source_transcript": transcript_file,
        # Original video filename (2026-07-03): the moments-review page links the
        # ORIGINAL video (the full-video low-res preview step was removed — the
        # transcript sliders replaced it as the cut-line reference).
        "source_file": transcript.get("source_file", ""),
        "analysis_model": moments.get("_model", "unknown"),
        "analysis_time_ms": moments.get("_duration_ms", 0),
        "audio_duration_seconds": duration,
        "duration_tier": tier,
        "moments": moments.get("moments", []),
    }

    _nch = await pipecat_nc_headers(request)
    wrote = await _pipecat_write_json(f"transcripts/{moments_file}", moments_output, _nch)

    if not wrote:
        moments_output["_storage_error"] = "Failed to write via pipecat-voice"

    logger.info(
        f"Moments analysis complete: {len(moments_output['moments'])} moments "
        f"→ {moments_file} ({'ok' if wrote else 'FAILED'})"
    )

    return JSONResponse(moments_output)


def _build_timestamped_text(segments: list, full_text: str) -> str:
    """Convert word-level segments into readable timestamped paragraphs.

    Groups consecutive words into ~sentence-length chunks with timestamps.
    """
    if not segments:
        return full_text

    chunks = []
    current_words = []
    current_start = None

    for seg in segments:
        text = seg.get("text", "").strip()
        start = seg.get("start", 0)
        end = seg.get("end", 0)

        if not text:
            continue

        if current_start is None:
            current_start = start

        current_words.append(text)

        # Break on sentence-ending punctuation or ~15 words
        is_sentence_end = text.endswith((".","!","?"))
        if is_sentence_end or len(current_words) >= 15:
            sentence = " ".join(current_words)
            mins = int(current_start // 60)
            secs = int(current_start % 60)
            chunks.append(f"[{mins}:{secs:02d}] {sentence}")
            current_words = []
            current_start = None

    # Flush remaining
    if current_words and current_start is not None:
        sentence = " ".join(current_words)
        mins = int(current_start // 60)
        secs = int(current_start % 60)
        chunks.append(f"[{mins}:{secs:02d}] {sentence}")

    return "\n".join(chunks)


async def _identify_moments(
    timestamped_text: str,
    full_text: str,
    duration: float,
    agent_id: str = "",
) -> dict:
    """Send transcript to LLM for moments identification.

    Returns structured moments with proposed clips.
    Uses the cove-core model chain with fallback.
    """
    from src.config import get_primary_agent_id
    from src.models.provider import (
        get_model_client,
        _resolve_model_string,
        _write_jw_metric,
    )
    from langchain_core.messages import SystemMessage, HumanMessage

    duration_mins = int(duration // 60)

    # ── Duration-based analysis tiers ──────────────────────────────
    # Scale moments count and clip types to video length
    if duration_mins < 5:
        # SHORT: quick clips, no story tier — the whole video is the story
        expected_moments = max(1, min(2, duration_mins))
        clip_tiers = """For EVERY moment, propose TWO clip lengths:
- **Quote** (8-15 seconds): A single punchy statement or reaction. The hook — what stops scrolling.
- **Thought** (20-45 seconds): A complete idea with setup and payoff. Good for YouTube Shorts, TikTok, Reels.

Do NOT propose "story" clips — this video is short enough to be shared whole."""
        clip_example = """        {{
          "type": "quote",
          "label": "Short title for this clip",
          "start_seconds": 30.0,
          "end_seconds": 42.0,
          "duration_seconds": 12,
          "platform_fit": ["youtube_shorts", "tiktok", "reels"],
          "hook_line": "The exact quote or opening line"
        }},
        {{
          "type": "thought",
          "label": "Short title for this clip",
          "start_seconds": 25.0,
          "end_seconds": 60.0,
          "duration_seconds": 35,
          "platform_fit": ["youtube_shorts", "tiktok"],
          "hook_line": "Opening line that sets up the idea"
        }}"""
        length_guidance = f"This video is only about {duration_mins} minutes long. Find 1-2 of the strongest moments — don't force clips where there isn't enough content to justify them."
    elif duration_mins < 15:
        # MEDIUM: standard three tiers, moderate count
        expected_moments = max(2, min(4, duration_mins // 3))
        clip_tiers = """For EVERY moment, propose ALL THREE clip lengths:
- **Quote** (15-30 seconds): A single punchy statement or reaction. The hook — what stops scrolling.
- **Thought** (45-90 seconds): A complete idea with setup and payoff. Good for YouTube Shorts, TikTok, Reels.
- **Story** (2-4 minutes): A full narrative arc or detailed explanation. Good for YouTube, longer TikTok.

Always propose all three. The clips from the same moment WILL overlap — the quote exists inside the thought which exists inside the story."""
        clip_example = """        {{
          "type": "quote",
          "label": "Short title for this clip",
          "start_seconds": 145.0,
          "end_seconds": 168.0,
          "duration_seconds": 23,
          "platform_fit": ["youtube_shorts", "tiktok", "reels"],
          "hook_line": "The exact quote or opening line"
        }},
        {{
          "type": "thought",
          "label": "Short title for this clip",
          "start_seconds": 130.0,
          "end_seconds": 200.0,
          "duration_seconds": 70,
          "platform_fit": ["youtube_shorts", "tiktok"],
          "hook_line": "Opening line that sets up the idea"
        }},
        {{
          "type": "story",
          "label": "Short title for this clip",
          "start_seconds": 120.0,
          "end_seconds": 280.0,
          "duration_seconds": 160,
          "platform_fit": ["youtube"],
          "hook_line": "Opening line that draws the viewer in"
        }}"""
        length_guidance = f"This video is about {duration_mins} minutes long. Find 2-4 strong moments — quality over quantity."
    elif duration_mins < 30:
        # LONG: full extraction
        expected_moments = max(5, duration_mins // 3)
        clip_tiers = """For EVERY moment, propose ALL THREE clip lengths:
- **Quote** (15-30 seconds): A single punchy statement or reaction. The hook — what stops scrolling.
- **Thought** (45-90 seconds): A complete idea with setup and payoff. Good for YouTube Shorts, TikTok, Reels.
- **Story** (2-5 minutes): A full narrative arc or detailed explanation. Good for YouTube, longer TikTok.

Always propose all three. The clips from the same moment WILL overlap — the quote exists inside the thought which exists inside the story."""
        clip_example = """        {{
          "type": "quote",
          "label": "Short title for this clip",
          "start_seconds": 145.0,
          "end_seconds": 168.0,
          "duration_seconds": 23,
          "platform_fit": ["youtube_shorts", "tiktok", "reels"],
          "hook_line": "The exact quote or opening line"
        }},
        {{
          "type": "thought",
          "label": "Short title for this clip",
          "start_seconds": 130.0,
          "end_seconds": 200.0,
          "duration_seconds": 70,
          "platform_fit": ["youtube_shorts", "tiktok"],
          "hook_line": "Opening line that sets up the idea"
        }},
        {{
          "type": "story",
          "label": "Short title for this clip",
          "start_seconds": 120.0,
          "end_seconds": 280.0,
          "duration_seconds": 160,
          "platform_fit": ["youtube"],
          "hook_line": "Opening line that draws the viewer in"
        }}"""
        length_guidance = f"This video is about {duration_mins} minutes long. Find 5-{expected_moments} moments. Be generous — it's better to surface too many than too few."
    else:
        # EXTENDED: deep extraction
        expected_moments = max(8, duration_mins // 3)
        clip_tiers = """For EVERY moment, propose ALL THREE clip lengths:
- **Quote** (15-30 seconds): A single punchy statement or reaction. The hook — what stops scrolling.
- **Thought** (45-90 seconds): A complete idea with setup and payoff. Good for YouTube Shorts, TikTok, Reels.
- **Story** (2-5 minutes): A full narrative arc or detailed explanation. Good for YouTube, longer TikTok.

Always propose all three. The clips from the same moment WILL overlap — the quote exists inside the thought which exists inside the story."""
        clip_example = """        {{
          "type": "quote",
          "label": "Short title for this clip",
          "start_seconds": 145.0,
          "end_seconds": 168.0,
          "duration_seconds": 23,
          "platform_fit": ["youtube_shorts", "tiktok", "reels"],
          "hook_line": "The exact quote or opening line"
        }},
        {{
          "type": "thought",
          "label": "Short title for this clip",
          "start_seconds": 130.0,
          "end_seconds": 200.0,
          "duration_seconds": 70,
          "platform_fit": ["youtube_shorts", "tiktok"],
          "hook_line": "Opening line that sets up the idea"
        }},
        {{
          "type": "story",
          "label": "Short title for this clip",
          "start_seconds": 120.0,
          "end_seconds": 280.0,
          "duration_seconds": 160,
          "platform_fit": ["youtube"],
          "hook_line": "Opening line that draws the viewer in"
        }}"""
        length_guidance = f"This video is about {duration_mins} minutes long. Find at least {expected_moments} moments. A long video like this has many moments — don't leave good content on the table."

    system_prompt = f"""You are a video content analyst for a creator's team. Your job is to find the most compelling MOMENTS in a video transcript and propose clips for each.

A MOMENT is a region of the video where something worth sharing happens — a clear insight, a story with an arc, a strong opinion, an emotional beat, a practical demonstration, or a quotable line.

{clip_tiers}

IMPORTANT:
- {length_guidance}
- Timestamps must be exact — use the [M:SS] markers in the transcript.
- Each clip needs a hook — what makes someone stop scrolling?
- Think about what would make a viewer want to see the full video.
- Only surface moments that justify their length — don't pad short content into long clips.

## Virality rubric — SCORE EVERY CLIP
For each clip include "virality_score" (integer 0-100) and "why" (one short line). Score on:
- HOOK (biggest factor): do the first 1-2 seconds stop the scroll?
- PAYOFF: does it deliver a complete, satisfying idea or reaction within its length?
- EMOTION / SURPRISE: strong feeling, a surprising turn, or a bold/contrarian claim.
- PRACTICAL VALUE: does the viewer learn or gain something usable?
- STANDALONE CLARITY: does it make sense with zero prior context?
Don't inflate — reserve 85+ for genuinely scroll-stopping clips. **Order moments so the highest-scoring come FIRST.**
Also classify each moment with "content_type": one of insight | story | opinion | demo | quote | emotional | other.

Return ONLY valid JSON in this format. EVERY clip object MUST include "virality_score" and "why":
{{
  "moments": [
    {{
      "id": 1,
      "content_type": "insight",
      "topic": "Brief description of what this moment is about",
      "hook": "The specific line or idea that grabs attention",
      "start_seconds": 120.0,
      "end_seconds": 280.0,
      "reasoning": "Why this is compelling — what makes it watchable",
      "clips": [
{clip_example}
      ]
    }}
  ]
}}

Each clip object additionally carries:  "virality_score": 82  with  "why": "the contrarian claim lands in the first line"
"""

    human_prompt = f"""Here is the timestamped transcript. Find the best moments and propose clips.

{timestamped_text}"""

    # The Cove's REAL working chain first (nottington A13, round 2): invoke_with_fallback
    # resolves the calling agent's model ASSIGNMENT — the same models chat and protocols
    # actually run on — with the 2-tier fallback + JW metrics built in. (Round 1 used
    # get_primary_model(), which resolves a DIFFERENT way — env OpenRouter → bare local
    # Ollama — and timed out on a box whose chat worked fine.) The legacy founder list
    # (gemini/kimi/qwen2.5:32b) stays as the last resort.
    result = None
    model_used = None
    duration_ms = 0
    # #5b: collect a per-tier reason so a total failure surfaces WHY each tier fell
    # over (parse-empty vs key-less vs timeout), not just "All models failed".
    tier_failures = []

    # #4 — name the model phase so the poll shows "analyzing" (the moments LLM pass
    # can take minutes on a local brain) instead of a bare spinner.
    try:
        from src.dashboard.routes.video_jobs import set_phase
        set_phase("analyzing")
    except Exception:
        pass

    from src.moments_json import extract_moments_json, tail

    # The operator's compute choice governs every paid tier below (sovereignty gate).
    _llm_mode = "cloud"
    try:
        from src.config import get_compute_config
        _llm_mode = ((get_compute_config() or {}).get("llm") or {}).get("mode") or "cloud"
    except Exception:
        pass

    # CF-110 #3 — operator-selected Analysis model wins: consulted FIRST, before the
    # agent chain. Sovereignty gate: in llm.mode=local a paid (non-ollama) pick is
    # SKIPPED (never silently pays); a local pick is always honored.
    _analysis_model = ""
    try:
        from src.dashboard.routes.pipeline_keys import get_analysis_model
        _analysis_model = get_analysis_model()
    except Exception:
        pass
    if result is None and _analysis_model:
        from src.dashboard.routes.pipeline_keys import analysis_model_allowed
        _am_provider, _ = _resolve_model_string(_analysis_model)
        if not analysis_model_allowed(_am_provider, _llm_mode):
            logger.info("Analysis model %s skipped — llm.mode=local blocks paid tiers",
                        _analysis_model)
            tier_failures.append(f"analysis model {_analysis_model}: skipped (local-mode sovereignty gate)")
        else:
            try:
                client = get_model_client(_analysis_model, temperature=0.4)
                _budget_local_client(client)
                t0 = time.monotonic()
                response = await asyncio.wait_for(
                    client.ainvoke([SystemMessage(content=system_prompt),
                                    HumanMessage(content=human_prompt)]),
                    timeout=_job_timeout(600, 1800),
                )
                duration_ms = int((time.monotonic() - t0) * 1000)
                result = extract_moments_json((response.content or "").strip())
                if result is not None:
                    model_used = f"analysis-model/{_analysis_model}"
                    logger.info(f"Moments identified via the selected analysis model "
                                f"({_analysis_model}) in {duration_ms}ms")
                else:
                    tier_failures.append(f"analysis model {_analysis_model}: no JSON in the reply")
            except Exception as e:
                logger.warning(f"Moments analysis failed on the selected analysis model: {e}")
                tier_failures.append(f"analysis model {_analysis_model}: {e}")

    if result is None:
        try:
            from src.models.provider import invoke_with_fallback
            t0 = time.monotonic()
            content = await invoke_with_fallback(
                [SystemMessage(content=system_prompt), HumanMessage(content=human_prompt)],
                temperature=0.4, timeout=_job_timeout(600, 1800), label="video-moments",
                agent_id=(agent_id or get_primary_agent_id()), operation_type="tool",
            )
            duration_ms = int((time.monotonic() - t0) * 1000)
            # A13 r3: resilient extraction — a truncated tail loses one moment, not the run.
            result = extract_moments_json(content)
            if result is not None:
                model_used = "agent-chain"
                logger.info(f"Moments identified via the agent chain: "
                            f"{len(result.get('moments', []))} moments in {duration_ms}ms")
            else:
                logger.warning("Moments parse empty on the agent chain; raw tail: %r",
                               tail(content))
                tier_failures.append(f"agent chain: no JSON in the reply (tail: {tail(content, 80)!r})")
        except Exception as e:
            logger.warning(f"Moments analysis failed on the agent chain: {e}")
            tier_failures.append(f"agent chain: {e}")

    # Pipeline-key cloud tier (nottington follow-up, 2026-07-03): the operator's SAVED
    # pipeline key (Groq/OpenAI — the same store that powers cloud ASR) buys fast cloud
    # moments on a box whose only brain is a slow local model (15 min local vs seconds
    # cloud on the same transcript). Groq serves fast LLMs, not just Whisper. Skipped
    # key-less; model ids env-overridable in case providers rotate them.
    # ★ THE OPERATOR'S COMPUTE CHOICE GOVERNS (operator decision, 2026-07-03):
    # llm.mode == "local" means LOCAL — slow is fine, sovereignty is the point; this
    # tier must never silently move a local-choice Cove's work onto a paid API. Only
    # a cloud-mode Cove gets the key tier. (_llm_mode computed once above, #3.)
    if result is None and _llm_mode != "local":
        try:
            from src.dashboard.routes.pipeline_keys import get_service_key
            _pk_client = None
            _pk_model = None
            _gk = get_service_key("groq")
            _ok = get_service_key("openai")
            if _gk:
                from langchain_openai import ChatOpenAI
                _pk_model = os.getenv("LP_MOMENTS_GROQ_MODEL", "llama-3.3-70b-versatile")
                _pk_client = ChatOpenAI(model=_pk_model, api_key=_gk,
                                        base_url="https://api.groq.com/openai/v1",
                                        temperature=0.4, max_tokens=8000, timeout=120)
                _pk_model = f"groq/{_pk_model}"
            elif _ok:
                from langchain_openai import ChatOpenAI
                _pk_model = os.getenv("LP_MOMENTS_OPENAI_MODEL", "gpt-4o-mini")
                _pk_client = ChatOpenAI(model=_pk_model, api_key=_ok,
                                        temperature=0.4, max_tokens=8000, timeout=120)
                _pk_model = f"openai/{_pk_model}"
            if _pk_client is not None:
                t0 = time.monotonic()
                response = await asyncio.wait_for(
                    _pk_client.ainvoke([SystemMessage(content=system_prompt),
                                        HumanMessage(content=human_prompt)]),
                    timeout=_job_timeout(240, 600),
                )
                duration_ms = int((time.monotonic() - t0) * 1000)
                result = extract_moments_json((response.content or "").strip())
                if result is not None:
                    model_used = _pk_model
                    logger.info(f"Moments identified via the pipeline key ({_pk_model}) "
                                f"in {duration_ms}ms")
                else:
                    tier_failures.append(f"pipeline key {_pk_model}: no JSON in the reply")
        except Exception as e:
            logger.warning(f"Moments analysis failed on the pipeline-key tier: {e}")
            tier_failures.append(f"pipeline key: {e}")

    # Local-brain tier (nottington A13, round 3): a box whose ONLY intelligence is a
    # local Ollama has no agent assignment — the chain above defaults to founder kimi
    # and dies key-less. Ask get_primary_model directly with a BATCH budget: a local
    # model chewing a long prompt needs minutes, not the 240s interactive cap that
    # killed round 1 while chat (small prompts) worked fine.
    if result is None:
        try:
            from src.models.provider import get_primary_model
            client = get_primary_model(temperature=0.4)
            # A13 r3: give the local brain ROOM — a truncated generation is exactly
            # what broke the parse. Best-effort per client type: generous output
            # budget (num_predict / max_tokens) + no-think for the qwen3 family on
            # this JSON task (shorter output = less truncation risk). Never fatal.
            _budget_local_client(client)
            t0 = time.monotonic()
            response = await asyncio.wait_for(
                client.ainvoke([SystemMessage(content=system_prompt),
                                HumanMessage(content=human_prompt)]),
                timeout=_job_timeout(600, 1800),
            )
            duration_ms = int((time.monotonic() - t0) * 1000)
            content = (response.content or "").strip()
            result = extract_moments_json(content)
            if result is not None:
                model_used = (getattr(client, "model_name", None)
                              or getattr(client, "model", None) or "cove-brain")
                logger.info(f"Moments identified via the local brain ({model_used}) "
                            f"in {duration_ms}ms")
            else:
                logger.warning("Moments parse empty on the local brain; raw tail: %r",
                               tail(content))
                tier_failures.append(f"local brain: no JSON in the reply (tail: {tail(content, 80)!r})")
        except Exception as e:
            logger.warning(f"Moments analysis failed on the local brain: {e}")
            tier_failures.append(f"local brain: {e}")

    for model_name in ([] if result is not None else ["gemini-flash", "kimi-k2.5", "qwen2.5:32b"]):
        try:
            provider, model_string = _resolve_model_string(model_name)
            client = get_model_client(model_name, temperature=0.4)

            t0 = time.monotonic()
            response = await asyncio.wait_for(
                client.ainvoke([
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=human_prompt),
                ]),
                timeout=_job_timeout(240, 1800),
            )
            duration_ms = int((time.monotonic() - t0) * 1000)

            content = (response.content or "").strip()
            # A13 r3: resilient extraction (strips think tags + salvages a truncated tail)
            result = extract_moments_json(content)
            if result is not None:
                model_used = model_string

                # Log metric
                usage = getattr(response, "usage_metadata", {}) or {}
                await _write_jw_metric(
                    agent_id=(agent_id or get_primary_agent_id()),
                    operation_type="tool",
                    operation_label=f"video-moments/{model_name}",
                    model_used=model_string,
                    provider=provider,
                    tokens_in=usage.get("input_tokens"),
                    tokens_out=usage.get("output_tokens"),
                    duration_ms=duration_ms,
                    succeeded=True,
                )
                logger.info(
                    f"Moments identified via {model_name}: "
                    f"{len(result.get('moments', []))} moments in {duration_ms}ms"
                )
                break

        except asyncio.TimeoutError:
            logger.warning(f"Moments analysis timed out on {model_name}")
            tier_failures.append(f"{model_name}: timed out")
            continue
        except json.JSONDecodeError as e:
            logger.warning(f"Moments JSON parse failed on {model_name}: {e}")
            tier_failures.append(f"{model_name}: JSON parse failed ({e})")
            continue
        except Exception as e:
            logger.warning(f"Moments analysis failed on {model_name}: {e}")
            tier_failures.append(f"{model_name}: {e}")
            continue

    if result is None:
        # #5b: surface the per-tier reasons the log already has, so the editor toast
        # says WHY (e.g. "local brain: key-less / timed out") instead of a dead end.
        detail = "; ".join(tier_failures) if tier_failures else "no model produced usable JSON"
        raise RuntimeError(f"All models failed for moments analysis — {detail}")

    result["_model"] = model_used
    result["_duration_ms"] = duration_ms
    return result


# ── Transcript read/write endpoints ───────────────────────────────

@router.get("/transcript/{stem}")
async def get_transcript(stem: str, request: Request, edited: str = ""):
    """Load a transcript's word-level segments for the editor (presence's NC in
    multi-mode). Pass ?edited=1 to prefer a saved edit if one exists."""
    if edited == "1":
        d = await _read_video_json(request, f"transcripts/{stem}-transcript-edited.json")
        if d is not None:
            return JSONResponse(d)
    d = await _read_video_json(request, f"transcripts/{stem}-transcript.json")
    if d is None:
        return JSONResponse({"error": f"Transcript not found: {stem}"}, status_code=404)
    return JSONResponse(d)


@router.put("/transcript/{stem}")
async def save_transcript(stem: str, request: Request):
    """Save edited transcript back — preserves word-level timestamps.

    Body: {
        "segments": [{"text": "word", "start": 0.0, "end": 0.24}, ...],
        "text": "full concatenated text",
        "presence_name": "alex"
    }

    Writes an edited copy alongside the original:
    - Original: {stem}-transcript.json (untouched)
    - Edited:   {stem}-transcript-edited.json (this save)

    The moments analysis reads the edited version if it exists,
    falling back to the original. This preserves the pristine
    transcription for reference and re-editing.
    """
    body = await request.json()
    edited_segments = body.get("segments", [])
    full_text = body.get("text", "")
    presence_name = body.get("presence_name", "").strip()

    if not edited_segments:
        return JSONResponse({"error": "No segments provided"}, status_code=400)

    if not presence_name:
        return JSONResponse(
            {"error": "presence_name required"},
            status_code=400,
        )

    # Read original transcript to preserve metadata (presence NC or founder mount)
    original = await _read_video_json(request, f"transcripts/{stem}-transcript.json")
    if original is None:
        return JSONResponse(
            {"error": f"Original transcript not found: {stem}"},
            status_code=404,
        )

    # Build edited transcript — same structure, updated content
    edited = {
        "source_file": original.get("source_file", ""),
        "source_type": original.get("source_type", "video"),
        "language": original.get("language", "English"),
        "text": full_text,
        "model": original.get("model", ""),
        "transcription_seconds": original.get("transcription_seconds", 0),
        "audio_duration_seconds": original.get("audio_duration_seconds", 0),
        "realtime_factor": original.get("realtime_factor", 0),
        "segments": edited_segments,
        "edited": True,
        "edited_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "original_word_count": len(original.get("segments", [])),
        "edited_word_count": len(edited_segments),
    }

    # Write via pipecat-voice (owns the :rw video mount)
    edited_filename = f"{stem}-transcript-edited.json"
    _nch = await pipecat_nc_headers(request)
    wrote = await _pipecat_write_json(f"transcripts/{edited_filename}", edited, _nch)

    if not wrote:
        return JSONResponse(
            {"error": "Failed to write edited transcript via pipecat-voice"},
            status_code=500,
        )

    logger.info(
        f"Transcript saved: {edited_filename} "
        f"({len(edited_segments)} words, was {len(original.get('segments', []))})"
    )

    return JSONResponse({
        "status": "saved",
        "filename": edited_filename,
        "word_count": len(edited_segments),
        "original_word_count": len(original.get("segments", [])),
    })


# ── Status + listing endpoints ─────────────────────────────────────

@router.get("/inbox")
async def list_inbox(request: Request):
    """List videos waiting in the inbox (founder mount or the presence's NC)."""
    files = await _list_video_dir(request, "inbox")
    return {"files": files}


@router.get("/transcripts")
async def list_transcripts(request: Request):
    """List available transcripts and their analysis status (founder mount or NC)."""
    files = await _list_video_dir(request, "transcripts")
    names = {f["filename"] for f in files}
    transcripts = []
    for f in sorted(names):
        if f.endswith("-transcript.json") and not f.endswith("-edited.json"):
            stem = f.replace("-transcript.json", "")
            moments_file = f"{stem}-moments.json"
            edited_file = f"{stem}-transcript-edited.json"
            has_moments = moments_file in names
            has_edits = edited_file in names
            transcripts.append({
                "stem": stem,
                "transcript_file": f,
                "has_moments": has_moments,
                "moments_file": moments_file if has_moments else None,
                "has_edits": has_edits,
            })
    return {"transcripts": transcripts}


@router.get("/moments/{stem}")
async def get_moments(stem: str, request: Request):
    """Get moments analysis for a video (presence's NC in multi-mode)."""
    data = await _read_video_json(request, f"transcripts/{stem}-moments.json")
    if data is None:
        return JSONResponse({"error": f"No moments file for {stem}"}, status_code=404)
    return JSONResponse(data)


@router.post("/moments/mark-skipped")
async def mark_skipped(request: Request):
    """Mark skipped clips as processed so they don't reappear in review.

    Body: { stem, skipped: [{moment_id, clip_type}, ...] }
    """
    body = await request.json()
    stem = body.get("stem", "")
    skipped = body.get("skipped", [])
    if not stem or not skipped:
        return JSONResponse({"error": "stem and skipped required"}, status_code=400)

    moments_data = await _read_video_json(request, f"transcripts/{stem}-moments.json")
    if moments_data is None:
        return JSONResponse({"error": "Moments file not found"}, status_code=404)

    # Build set of skipped (moment_id, clip_type) pairs
    skip_keys = {(s["moment_id"], s["clip_type"]) for s in skipped}
    marked = 0

    for moment in moments_data.get("moments", []):
        for clip in moment.get("clips", []):
            key = (moment.get("id"), clip.get("type"))
            if key in skip_keys:
                clip["processed"] = True
                clip["skipped"] = True
                marked += 1

    # Write back via pipecat-voice (owns the :rw video mount)
    _nch = await pipecat_nc_headers(request)
    wrote = await _pipecat_write_json(f"transcripts/{stem}-moments.json", moments_data, _nch)

    if not wrote:
        return JSONResponse({"error": "Failed to write via pipecat-voice"}, status_code=500)

    logger.info(f"Marked {marked} clips as skipped in {stem}-moments.json")
    return {"marked": marked}


# ── Proxy endpoints for pipecat-voice (frame extraction, video info) ──
# The UI runs on cove-core but frame extraction requires pipecat-voice
# (which has the video mount + ffmpeg + GPU). These proxy the requests.
#
# NOTE: process-moments and generate-preview are in video_processing.py
# (separate file, same /api/video prefix). Kept apart to isolate the
# proxy-only routes from the logic-heavy routes in this file.

@router.get("/proxy/stream")
async def proxy_video_stream(filename: str = ""):
    """Serve a video player page for the given video.

    Opens in a new tab — the browser's native video player handles it.
    The actual video is served from /api/video/proxy/raw via filesystem.
    """
    if not filename:
        return JSONResponse({"error": "No filename"}, status_code=400)

    # No local existence check — /proxy/raw resolves the source (founder mount OR the
    # presence's NC via pipecat). The player only needs to render and point at it.
    source_type = "video/mp4"

    html = f"""<!DOCTYPE html>
<html><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{filename} — Video Player</title>
<style>
body {{ background: #0a0a0f; margin: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 100vh; font-family: -apple-system, sans-serif; padding: 12px; }}
video {{ max-width: 95vw; max-height: 80vh; border-radius: 8px; }}
.close-btn {{ position: fixed; top: 12px; right: 12px; z-index: 100; background: rgba(0,0,0,0.7); color: #fff; border: 1px solid #444; border-radius: 50%; width: 40px; height: 40px; font-size: 20px; cursor: pointer; display: flex; align-items: center; justify-content: center; -webkit-tap-highlight-color: transparent; }}
.close-btn:hover {{ background: rgba(255,255,255,0.15); }}
.filename {{ color: #5ce1e6; font-size: 14px; margin-top: 12px; }}
.hint {{ color: #666; font-size: 12px; margin-top: 6px; }}
</style>
</head><body>
<button class="close-btn" onclick="window.history.length > 1 ? window.history.back() : window.close()" title="Close">✕</button>
<video controls autoplay playsinline>
<source src="/api/video/proxy/raw?filename={filename}" type="{source_type}">
Your browser does not support video playback.
</video>
<div class="filename">{filename}</div>
<div class="hint">Use the timeline to check timestamps against the transcript editor</div>
</body></html>"""
    return Response(content=html, media_type="text/html")


@router.get("/proxy/raw")
async def proxy_video_raw(request: Request, filename: str = ""):
    """Serve the source video bytes. Founder: FileResponse from the local mount.
    Presence (multi-mode): proxy to pipecat /api/video/stream — which WebDAV-pulls the
    file from the presence's NC into scratch — forwarding the Range header so the
    browser can seek and scrub."""
    from starlette.responses import FileResponse, StreamingResponse

    if not filename:
        return JSONResponse({"error": "No filename"}, status_code=400)

    # Founder mount fast-path
    video_path = _find_video_file(filename)
    if video_path:
        return FileResponse(video_path, media_type="video/mp4", filename=filename)

    # Presence path — stream through pipecat (it has the NC creds + WebDAV access)
    import httpx
    _nch = await pipecat_nc_headers(request)
    if not _nch:
        return JSONResponse({"error": f"Video not found: {filename}"}, status_code=404)
    fwd = dict(_nch)
    rng = request.headers.get("range")
    if rng:
        fwd["Range"] = rng
    client = httpx.AsyncClient(timeout=None)
    upstream = await client.send(
        client.build_request("GET", f"{PIPECAT_URL}/api/video/stream",
                             params={"filename": filename}, headers=fwd),
        stream=True,
    )
    if upstream.status_code not in (200, 206):
        await upstream.aclose()
        await client.aclose()
        return JSONResponse({"error": f"Video not found: {filename}"}, status_code=404)
    passthrough = {k: upstream.headers[k] for k in
                   ("content-length", "content-range", "accept-ranges")
                   if k in upstream.headers}

    async def _gen():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(
        _gen(),
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "video/mp4"),
        headers=passthrough,
    )


@router.get("/proxy/frame")
async def proxy_frame(request: Request, filename: str = "", t: float = -1):
    """Proxy frame extraction to pipecat-voice."""
    import httpx
    params = {"filename": filename}
    if t >= 0:
        params["t"] = t
    try:
        _nch = await pipecat_nc_headers(request)
        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.get(f"{PIPECAT_URL}/api/video/frame", params=params, headers=_nch)
            if resp.status_code == 200:
                return Response(content=resp.content, media_type="image/jpeg")
            return JSONResponse({"error": "Frame extraction failed"}, status_code=resp.status_code)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/proxy/info")
async def proxy_video_info(request: Request, filename: str = ""):
    """Proxy video info to pipecat-voice.

    Patient timeout (crop-page find, 2026-07-03): on an NC box the FIRST info hit
    makes voice pull the whole video into scratch (minutes for phone-size files) —
    the old 10s cap 500'd the crop page every time. Later hits answer from scratch
    instantly."""
    import httpx
    try:
        _nch = await pipecat_nc_headers(request)
        async with httpx.AsyncClient(timeout=600) as client:
            resp = await client.get(f"{PIPECAT_URL}/api/video/info", params={"filename": filename}, headers=_nch)
            return JSONResponse(resp.json(), status_code=resp.status_code)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/pipeline-status")
async def pipeline_status(request: Request):
    """Overview of the entire video pipeline state (founder mount or the presence's NC)."""
    folders = {}
    # #1524 — match the REAL pipeline: inbox → processing → raw, with transcripts/shorts/moments
    # as products. "done" was never a folder (it's a job-state string), and raw/moments were
    # missing here — so the status overview showed a phantom folder and hid two real ones.
    for folder in ["inbox", "processing", "raw", "transcripts", "shorts", "moments"]:
        folders[folder] = len(await _list_video_dir(request, folder))

    return {
        "video_base": VIDEO_BASE,
        "mounted": os.path.isdir(VIDEO_BASE),
        "folders": folders,
    }
