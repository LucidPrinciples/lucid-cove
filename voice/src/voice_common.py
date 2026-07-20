"""
Shared utilities for pipecat-voice routes.
WebSocket endpoint for real-time voice conversations on port 8300.
Jules transcription mode available at /jules.
"""

import asyncio
import logging
import json
import os
import tempfile
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Dict, Optional
from dataclasses import dataclass, field

from fastapi import WebSocket

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Vault Inbox — filesystem path mounted from host (single-Presence / operator mode)
VAULT_INBOX = os.environ.get("VAULT_INBOX_PATH", "/vault-inbox")
VIDEO_MOUNT = os.environ.get("VIDEO_MOUNT", "/video")
NC_VIDEO_PATH = "AgentSkills/Content/video"

# Nextcloud WebDAV config — for per-Presence vault routing (multi-Presence mode)
NEXTCLOUD_URL = os.environ.get("NEXTCLOUD_URL", "")
NEXTCLOUD_ADMIN_USER = os.environ.get("NEXTCLOUD_USER", "")
NEXTCLOUD_ADMIN_PASSWORD = os.environ.get("NEXTCLOUD_PASSWORD", "")


async def _save_to_nextcloud_vault(
    nc_user: str,
    filename: str,
    content: bytes,
    content_type: str = "text/markdown",
) -> dict:
    """Save a file to a Presence's Nextcloud vault Inbox via WebDAV.

    Args:
        nc_user: Nextcloud username for this Presence
        filename: Filename to save (e.g. 'jules-2026-05-18_1430.md')
        content: File content as bytes
        content_type: MIME type

    Returns:
        {"ok": True, "path": "Vault/Inbox/filename"} or {"error": "..."}
    """
    import httpx
    from urllib.parse import quote

    if not NEXTCLOUD_URL or not NEXTCLOUD_ADMIN_USER or not NEXTCLOUD_ADMIN_PASSWORD:
        return {"error": "Nextcloud not configured"}

    webdav_url = (
        f"{NEXTCLOUD_URL}/remote.php/dav/files/{nc_user}"
        f"/AgentSkills/Inbox/{quote(filename, safe='')}"
    )

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.put(
                webdav_url,
                auth=(NEXTCLOUD_ADMIN_USER, NEXTCLOUD_ADMIN_PASSWORD),
                content=content,
                headers={"Content-Type": content_type},
            )
            if resp.status_code in (201, 204):
                logger.info(f"Saved to Nextcloud vault ({nc_user}): {filename}")
                return {"ok": True, "path": f"AgentSkills/Inbox/{filename}"}
            else:
                logger.error(f"Nextcloud save failed ({nc_user}): HTTP {resp.status_code}")
                return {"error": f"Nextcloud HTTP {resp.status_code}"}
    except Exception as e:
        logger.error(f"Nextcloud save error ({nc_user}): {e}")
        return {"error": str(e)}


@dataclass
class AudioBuffer:
    """Buffer for incoming audio frames from a client."""
    frames: list = field(default_factory=list)
    last_activity: float = field(default_factory=time.time)

    def add_frame(self, frame: bytes):
        self.frames.append(frame)
        self.last_activity = time.time()
        # Cap at ~5 minutes of audio (2400 frames at 256ms each) to prevent memory issues
        if len(self.frames) > 2400:
            self.frames = self.frames[-2000:]

    def get_buffered_audio(self) -> bytes:
        return b''.join(self.frames)

    def clear(self):
        self.frames = []
        self.last_activity = time.time()


class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.audio_buffers: Dict[str, AudioBuffer] = {}
        self.connection_metadata: Dict[str, dict] = {}
        self.connection_modes: Dict[str, str] = {}  # "full" or "transcribe" (jules)

    async def connect(self, websocket: WebSocket, client_id: str) -> bool:
        try:
            await websocket.accept()
            self.active_connections[client_id] = websocket
            self.audio_buffers[client_id] = AudioBuffer()
            self.connection_metadata[client_id] = {
                'connected_at': time.time(),
                'frames_received': 0,
                'bytes_received': 0
            }
            self.connection_modes[client_id] = "full"
            logger.info(f"Client {client_id} connected")
            return True
        except Exception as e:
            logger.error(f"Failed to accept connection: {e}")
            return False

    def disconnect(self, client_id: str):
        if client_id in self.active_connections:
            del self.active_connections[client_id]
        if client_id in self.audio_buffers:
            del self.audio_buffers[client_id]
        if client_id in self.connection_metadata:
            del self.connection_metadata[client_id]
        if client_id in self.connection_modes:
            del self.connection_modes[client_id]
        logger.info(f"Client {client_id} disconnected")

    def process_audio_frame(self, client_id: str, frame: bytes):
        if client_id in self.audio_buffers:
            self.audio_buffers[client_id].add_frame(frame)
            if client_id in self.connection_metadata:
                self.connection_metadata[client_id]['frames_received'] += 1
                self.connection_metadata[client_id]['bytes_received'] += len(frame)


manager = ConnectionManager()


def _transcribe_file(stt, filepath: str) -> str:
    """Transcribe an audio file using the STT model. Runs in thread pool."""
    try:
        segments, info = stt.model.transcribe(
            filepath,
            language="en",
            beam_size=5,
            vad_filter=True,
        )
        parts = [seg.text.strip() for seg in segments if seg.text.strip()]
        return "\n\n".join(parts) if parts else ""
    except Exception as e:
        logger.error(f"Transcription error: {e}")
        return ""


# ── NC WebDAV file operations (replicable write pattern) ───────────

def _nc_webdav_base() -> str:
    return f"{NEXTCLOUD_URL}/remote.php/dav/files/{NEXTCLOUD_ADMIN_USER}"


async def _nc_put(filepath: str, content: bytes,
                  content_type: str = "application/json") -> bool:
    """Write a file to NC via WebDAV PUT.
    filepath relative to NC user root, e.g.
    'AgentSkills/Content/video/transcripts/foo.json'
    """
    import httpx
    from urllib.parse import quote
    url = f"{_nc_webdav_base()}/{quote(filepath, safe='/')}"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.put(
                url,
                auth=(NEXTCLOUD_ADMIN_USER, NEXTCLOUD_ADMIN_PASSWORD),
                content=content,
                headers={"Content-Type": content_type},
            )
            if resp.status_code in (200, 201, 204):
                logger.info(f"NC write: {filepath}")
                return True
            logger.error(f"NC write failed ({resp.status_code}): {filepath}")
            return False
    except Exception as e:
        logger.error(f"NC write error: {e}")
        return False


async def _nc_move(src_path: str, dst_path: str) -> bool:
    """Move a file within NC via WebDAV MOVE.
    Paths relative to NC user root.
    """
    import httpx
    from urllib.parse import quote
    src_url = f"{_nc_webdav_base()}/{quote(src_path, safe='/')}"
    dst_url = f"{_nc_webdav_base()}/{quote(dst_path, safe='/')}"
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.request(
                "MOVE",
                src_url,
                auth=(NEXTCLOUD_ADMIN_USER, NEXTCLOUD_ADMIN_PASSWORD),
                headers={"Destination": dst_url, "Overwrite": "T"},
            )
            if resp.status_code in (201, 204):
                logger.info(f"NC move: {src_path} → {dst_path}")
                return True
            logger.error(f"NC move failed ({resp.status_code}): {src_path}")
            return False
    except Exception as e:
        logger.error(f"NC move error: {e}")
        return False


async def _nc_get(filepath: str, local_path: str,
                  nc_url: str = None, nc_user: str = None, nc_pass: str = None) -> bool:
    """Pull a file FROM NC via WebDAV GET into local_path. Uses per-presence creds
    when given, else the configured admin creds. filepath is NC-user-relative."""
    import httpx
    from urllib.parse import quote
    url = nc_url or NEXTCLOUD_URL
    u = nc_user or NEXTCLOUD_ADMIN_USER
    p = nc_pass or NEXTCLOUD_ADMIN_PASSWORD
    dav = f"{url}/remote.php/dav/files/{u}/{quote(filepath, safe='/')}"
    part = None
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            async with client.stream("GET", dav, auth=(u, p)) as resp:
                if resp.status_code != 200:
                    logger.error(f"NC get failed ({resp.status_code}): {filepath}")
                    return False
                os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
                # ATOMIC pull (caption-full race, 2026-07-03): stream to a temp file
                # and rename into place — a concurrent resolve must never trust a
                # growing final path (truncated-MOV renders) or a poisoned partial.
                # The temp name is UNIQUE PER PULL (crop-page find, same day): the
                # page requests stream + info simultaneously, both pulled the same
                # file into ONE shared .part, and the loser's rename ENOENT'd —
                # 'video not found' for a file its sibling had just fetched.
                # Concurrent pulls now redundantly download and last-rename wins.
                import uuid
                part = f"{local_path}.part-{uuid.uuid4().hex[:8]}"
                with open(part, "wb") as f:
                    async for chunk in resp.aiter_bytes(1024 * 1024):
                        f.write(chunk)
                os.replace(part, local_path)
                part = None
                return True
    except Exception as e:
        logger.error(f"NC get error: {e}")
        return False
    finally:
        try:
            if part and os.path.exists(part):
                os.remove(part)
        except Exception:
            pass


@dataclass
class NCSession:
    """Per-request Nextcloud session for the presence whose video we're processing.

    cove-core passes the presence's NC connection (url/user/password) in the request
    body under `nc`; pipecat stays Cove- and NC-agnostic — no mounts. All subpaths
    are relative to the presence's video tree (AgentSkills/Content/video).
    This is the LOCKED 'stateless WebDAV processor' design.
    """
    url: str
    user: str
    password: str

    @classmethod
    def from_body(cls, body: dict):
        nc = (body or {}).get("nc") or {}
        return cls(
            url=nc.get("url") or NEXTCLOUD_URL,
            user=nc.get("user") or NEXTCLOUD_ADMIN_USER,
            password=nc.get("password") or NEXTCLOUD_ADMIN_PASSWORD,
        )

    @classmethod
    def from_request(cls, request, body: dict = None):
        """Build from X-NC-* headers (cove-core injects them per presence) or, as a
        fallback, from an `nc` block in the POST body. Returns None when no presence
        creds are supplied — the caller then uses the local VIDEO_MOUNT (founder)."""
        h = getattr(request, "headers", {}) or {}
        user = h.get("X-NC-User")
        if not user and body:
            return cls.from_body(body) if (body.get("nc")) else None
        if not user:
            return None
        return cls(
            url=h.get("X-NC-URL") or NEXTCLOUD_URL,
            user=user,
            password=h.get("X-NC-Pass") or NEXTCLOUD_ADMIN_PASSWORD,
        )

    def _rel(self, subpath: str) -> str:
        return f"{NC_VIDEO_PATH}/{subpath.strip('/')}"

    async def pull(self, subpath: str, local_path: str) -> bool:
        """Pull <video-tree>/<subpath> from NC into local_path."""
        return await _nc_get(self._rel(subpath), local_path, self.url, self.user, self.password)

    async def ensure_dir(self, subpath_dir: str):
        """MKCOL each parent collection under the video tree so a later PUT won't 404.
        The old disk mount always had these dirs; over WebDAV we must create them.
        MKCOL is non-recursive, so build segment by segment; an existing dir returns
        405 (or 301) which we ignore. subpath_dir is relative to the video tree."""
        import httpx
        from urllib.parse import quote
        rel = self._rel(subpath_dir).strip("/")
        if not rel:
            return
        async with httpx.AsyncClient(timeout=30) as client:
            path = ""
            for seg in rel.split("/"):
                path = f"{path}/{seg}" if path else seg
                dav = f"{self.url}/remote.php/dav/files/{self.user}/{quote(path, safe='/')}"
                try:
                    await client.request("MKCOL", dav, auth=(self.user, self.password))
                except Exception:
                    pass

    async def push(self, subpath: str, local_path: str,
                   content_type: str = "application/octet-stream") -> bool:
        """Push a local file up to <video-tree>/<subpath> in NC."""
        import httpx
        from urllib.parse import quote
        # Ensure the parent collection exists first — WebDAV PUT 404s into a missing dir.
        parent = "/".join(subpath.strip("/").split("/")[:-1])
        if parent:
            await self.ensure_dir(parent)
        dav = f"{self.url}/remote.php/dav/files/{self.user}/{quote(self._rel(subpath), safe='/')}"
        try:
            # STREAM the upload from disk with a batch-size timeout (2026-07-03): the
            # old read-whole-file + 300s cap held phone videos in RAM and timed out on
            # every big cross-mesh push (rented-GPU transcribes never landed their
            # processing/ copy — the lifecycle stalled in inbox). 4-5GB is the NORMAL
            # case. Connect timeout stays short; the transfer gets an hour.
            _size = os.path.getsize(local_path)

            async def _body():
                with open(local_path, "rb") as f:
                    while True:
                        chunk = f.read(1024 * 1024)
                        if not chunk:
                            break
                        yield chunk

            _timeout = httpx.Timeout(3600.0, connect=30.0)
            async with httpx.AsyncClient(timeout=_timeout) as client:
                resp = await client.put(dav, auth=(self.user, self.password),
                                        content=_body(),
                                        headers={"Content-Type": content_type,
                                                 "Content-Length": str(_size)})
                ok = resp.status_code in (200, 201, 204)
                if not ok:
                    logger.error(f"NC push failed ({resp.status_code}) {subpath}: {resp.text[:200]}")
                return ok
        except Exception as e:
            logger.error(
                f"NC push error ({subpath}): {type(e).__name__}: {e!r}"
            )
            return False

    async def move(self, src_subpath: str, dst_subpath: str) -> bool:
        """MOVE <video-tree>/<src> → <video-tree>/<dst> in the presence's NC (WebDAV).
        Ensures the destination parent exists first. Used for the C4 lifecycle
        graduation (processing → raw). Returns False on any non-2xx, never raises."""
        import httpx
        from urllib.parse import quote
        parent = "/".join(dst_subpath.strip("/").split("/")[:-1])
        if parent:
            await self.ensure_dir(parent)
        src = f"{self.url}/remote.php/dav/files/{self.user}/{quote(self._rel(src_subpath), safe='/')}"
        dst = f"{self.url}/remote.php/dav/files/{self.user}/{quote(self._rel(dst_subpath), safe='/')}"
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.request("MOVE", src, auth=(self.user, self.password),
                                            headers={"Destination": dst, "Overwrite": "T"})
                ok = resp.status_code in (200, 201, 204)
                if not ok:
                    logger.error(f"NC move failed ({resp.status_code}) {src_subpath}→{dst_subpath}: {resp.text[:200]}")
                return ok
        except Exception as e:
            logger.error(f"NC move error ({src_subpath}→{dst_subpath}): {e}")
            return False

    async def delete(self, subpath: str) -> bool:
        """DELETE <video-tree>/<subpath> in the presence's NC (WebDAV). 404 counts as
        success (already gone). Added for true inbox→processing MOVE semantics — the
        WebDAV path previously only ever copied, so inbox originals lingered."""
        import httpx
        from urllib.parse import quote
        dav = f"{self.url}/remote.php/dav/files/{self.user}/{quote(self._rel(subpath), safe='/')}"
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.delete(dav, auth=(self.user, self.password))
                ok = resp.status_code in (200, 204, 404)
                if not ok:
                    logger.error(f"NC delete failed ({resp.status_code}) {subpath}: {resp.text[:200]}")
                return ok
        except Exception as e:
            logger.error(f"NC delete error ({subpath}): {e}")
            return False


SCRATCH_ROOT = os.environ.get("VIDEO_SCRATCH", "/tmp/cove-video")
# Optional bind of the Cove Nextcloud html root (same volume as nextcloud:/var/www/html).
# When set, resolve_video_source can read presence files in place under
#   {NC_HTML_ROOT}/data/{nc_user}/files/AgentSkills/Content/video/...
# instead of WebDAV-pulling multi-GB objects into scratch every job.
NC_HTML_ROOT = (os.environ.get("NC_HTML_ROOT") or "").rstrip("/")


def nc_data_video_path(nc_user: str, sub: str, filename: str) -> str:
    """Absolute path on a mounted NC data volume for one video object."""
    if not NC_HTML_ROOT or not nc_user:
        return ""
    # Strip slashes so a leading "/" on nc_user cannot reset os.path.join,
    # and we never emit .../datajason/... from a missing separator.
    user = str(nc_user).strip().strip("/")
    sub_clean = str(sub or "").strip().strip("/")
    name = os.path.basename(str(filename or "").strip())
    if not user or not name:
        return ""
    return os.path.join(
        NC_HTML_ROOT, "data", user, "files", NC_VIDEO_PATH, sub_clean, name
    )


def find_on_nc_data(nc_user: str, filename: str,
                    subdirs=("processing", "inbox", "raw",
                             "shorts", "processed", "clips", "done", "captioned")) -> str:
    """Return local path if filename exists under mounted NC data for this user."""
    if not NC_HTML_ROOT or not nc_user:
        return ""
    for sub in subdirs:
        cand = nc_data_video_path(nc_user, sub, filename)
        if cand and os.path.isfile(cand):
            return cand
    return ""


async def resolve_video_source(filename: str, nc: "NCSession" = None,
                               subdirs=("processing", "inbox", "raw",
                                        "shorts", "processed", "clips", "done", "captioned")) -> str:
    """Local path to a source video.

    Order when an NCSession is present:
      1. scratch (prior pull)
      2. mounted NC data dir for that presence (NC_HTML_ROOT) — no network
      3. WebDAV pull into scratch

    Without NCSession, read the local VIDEO_MOUNT (founder bind). Returns None if
    not found.
    """
    if nc is not None:
        # Scratch-first across ALL subdirs (crop-page find, 2026-07-03): the file is
        # often already local under a DIFFERENT subdir than the caller's first guess
        # (transcribe caches under inbox/ before processing/ exists on NC) — the old
        # interleaved order re-downloaded an 826MB video from NC just to ffprobe it,
        # starving every proxy request into timeouts. Check every scratch location
        # before ANY network pull.
        for sub in subdirs:
            local = os.path.join(SCRATCH_ROOT, nc.user, sub, filename)
            if os.path.isfile(local):
                return local
        # Same-host NC volume mount: read jason/.../inbox in place (no multi-GB DAV).
        on_disk = find_on_nc_data(nc.user, filename, subdirs=subdirs)
        if on_disk:
            logger.info(f"resolve_video_source: NC data mount hit {on_disk}")
            return on_disk
        for sub in subdirs:
            local = os.path.join(SCRATCH_ROOT, nc.user, sub, filename)
            if await nc.pull(f"{sub}/{filename}", local):
                return local
        return None
    for sub in subdirs:
        cand = os.path.join(VIDEO_MOUNT, sub, filename)
        if os.path.isfile(cand):
            return cand
    # Last resort: NC data mount with admin/env user if someone set NC_HTML_ROOT only.
    if NC_HTML_ROOT and NEXTCLOUD_ADMIN_USER:
        on_disk = find_on_nc_data(NEXTCLOUD_ADMIN_USER, filename, subdirs=subdirs)
        if on_disk:
            return on_disk
    return None


async def publish_video_output(local_path: str, subpath: str, nc: "NCSession" = None,
                               content_type: str = "video/mp4") -> bool:
    """Publish a produced file to <video-tree>/<subpath>.

    Order:
      1. If NC_HTML_ROOT is set and we know the presence user, copy onto the
         mounted NC data volume and occ-scan (avoids multi-GB WebDAV / proxy
         413s — captioned full is often 1–3GB).
      2. Else if NCSession is set, WebDAV PUT.
      3. Else copy into VIDEO_MOUNT + scan (legacy founder bind).

    Returns False when nothing landed. Callers (caption-full especially) must
    treat False as failure — do not graduate or rename a missing object.
    """
    import shutil

    if not local_path or not os.path.isfile(local_path):
        logger.error(f"publish_video_output: missing local file {local_path!r}")
        return False

    sub_clean = (subpath or "").strip().strip("/")
    if not sub_clean:
        logger.error("publish_video_output: empty subpath")
        return False

    size_mb = os.path.getsize(local_path) / (1024 * 1024)
    nc_user = (getattr(nc, "user", None) or "").strip().strip("/") if nc is not None else ""
    if not nc_user:
        nc_user = (NEXTCLOUD_ADMIN_USER or "").strip().strip("/")

    # 1) Same-host NC data volume — preferred for large outputs.
    if NC_HTML_ROOT and nc_user:
        dest = nc_data_video_path(nc_user, os.path.dirname(sub_clean), os.path.basename(sub_clean))
        if dest:
            try:
                os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
                shutil.copy2(local_path, dest)
                # NC serves as www-data; root-owned copies from voice break reads.
                try:
                    www_uid = int(os.environ.get("NC_WWW_UID", "33"))
                    www_gid = int(os.environ.get("NC_WWW_GID", "33"))
                    os.chown(dest, www_uid, www_gid)
                except OSError as ce:
                    logger.warning(f"publish_video_output: chown skipped on {dest}: {ce}")
                _nc_scan(f"{NC_VIDEO_PATH}/{os.path.dirname(sub_clean)}".rstrip("/"))
                logger.info(
                    f"publish_video_output: NC data mount write OK "
                    f"{sub_clean} ({size_mb:.1f} MB) → {dest}"
                )
                return True
            except OSError as e:
                # EROFS when the compose mount is :ro — fall through to WebDAV.
                logger.warning(
                    f"publish_video_output: NC data mount write failed "
                    f"({e}) for {sub_clean} ({size_mb:.1f} MB); "
                    f"falling back to WebDAV/VIDEO_MOUNT"
                )

    # 2) WebDAV for multi-presence when mount missing or read-only.
    if nc is not None:
        ok = await nc.push(sub_clean, local_path, content_type)
        if ok:
            logger.info(
                f"publish_video_output: WebDAV OK {sub_clean} ({size_mb:.1f} MB)"
            )
        else:
            logger.error(
                f"publish_video_output: WebDAV failed {sub_clean} ({size_mb:.1f} MB)"
            )
        return ok

    # 3) Legacy founder VIDEO_MOUNT bind.
    dest = os.path.join(VIDEO_MOUNT, sub_clean)
    try:
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        shutil.copy2(local_path, dest)
        _nc_scan(f"{NC_VIDEO_PATH}/{os.path.dirname(sub_clean)}".rstrip("/"))
        logger.info(
            f"publish_video_output: VIDEO_MOUNT write OK "
            f"{sub_clean} ({size_mb:.1f} MB) → {dest}"
        )
        return True
    except OSError as e:
        logger.error(f"publish_video_output: VIDEO_MOUNT write failed ({e}) {sub_clean}")
        return False


# ── NC filesystem scan (trigger after direct file writes) ─────────

NC_CONTAINER = os.environ.get("NC_CONTAINER", "nextcloud-app")
# Neutral default — the compose env always sets the real per-Cove value; the
# old fallback was a founder username.
NC_SCAN_USER = os.environ.get("NEXTCLOUD_USER", "admin")

def _nc_scan(scan_path: str = None):
    """Trigger NC occ files:scan for files written to the data dir.

    scan_path: NC-relative path, e.g. "AgentSkills/Content/video/shorts"
    Runs async in background — doesn't block the response.
    """
    import http.client
    import socket as socket_mod

    user = NC_SCAN_USER
    path_arg = f" --path=/{user}/files/{scan_path}" if scan_path else ""
    cmd = f"php occ files:scan {user}{path_arg}"

    try:
        conn = http.client.HTTPConnection("localhost")
        sock = socket_mod.socket(socket_mod.AF_UNIX, socket_mod.SOCK_STREAM)
        sock.connect("/var/run/docker.sock")
        conn.sock = sock

        import json as json_mod
        exec_body = json_mod.dumps({
            "Cmd": ["su", "-s", "/bin/sh", "www-data", "-c", cmd],
            "AttachStdout": False,
            "AttachStderr": False,
            "Detach": True,
        })
        conn.request("POST", f"/containers/{NC_CONTAINER}/exec",
                      body=exec_body,
                      headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        if resp.status != 201:
            logger.warning(f"NC scan exec create failed: {resp.status} {resp.read().decode()[:200]}")
            return

        exec_id = json_mod.loads(resp.read()).get("Id")

        conn2 = http.client.HTTPConnection("localhost")
        sock2 = socket_mod.socket(socket_mod.AF_UNIX, socket_mod.SOCK_STREAM)
        sock2.connect("/var/run/docker.sock")
        conn2.sock = sock2
        conn2.request("POST", f"/exec/{exec_id}/start",
                       body=json_mod.dumps({"Detach": True}),
                       headers={"Content-Type": "application/json"})
        resp2 = conn2.getresponse()
        if resp2.status == 200:
            logger.info(f"NC scan triggered: {scan_path or 'full user scan'}")
        else:
            logger.warning(f"NC scan start failed: {resp2.status}")
    except Exception as e:
        logger.warning(f"NC scan error: {e}")


# ── Video transcription — batch Qwen3-ASR ──────────────────────────

# Lazy singleton — loads on first request, unloads after each transcription
_qwen_asr = None

def _get_qwen_asr():
    global _qwen_asr
    if _qwen_asr is None:
        from src.transports.qwen_asr_stt import QwenASRTransport
        _qwen_asr = QwenASRTransport()
    return _qwen_asr


# Global STT transport (initialized on first use)
_stt_transport = None

async def get_stt_transport():
    """Lazy initialization of STT transport."""
    global _stt_transport
    if _stt_transport is None:
        try:
            from src.transports.whisper_stt import WhisperSTTTransport
            _stt_transport = WhisperSTTTransport(model_size="small")
            if not _stt_transport.initialize():
                logger.error("Failed to initialize STT transport")
                _stt_transport = None
        except Exception as e:
            logger.error(f"Could not load STT transport: {e}")
    return _stt_transport


# Global LLM transport
_llm_transport = None

async def get_llm_transport():
    """Lazy initialization of LLM transport."""
    global _llm_transport
    if _llm_transport is None:
        try:
            from src.transports.ollama_llm import OllamaLLMTransport
            _llm_transport = OllamaLLMTransport()
            if not _llm_transport.initialize():
                logger.error("Failed to initialize LLM transport")
                _llm_transport = None
        except Exception as e:
            logger.error(f"Could not load LLM transport: {e}")
    return _llm_transport


# Global TTS transport
_tts_transport = None

async def get_tts_transport():
    """Lazy initialization of TTS transport."""
    global _tts_transport
    if _tts_transport is None:
        try:
            from src.transports.piper_tts import PiperTTSTransport
            _tts_transport = PiperTTSTransport()
            if not _tts_transport.initialize():
                logger.error("Failed to initialize TTS transport")
                _tts_transport = None
        except Exception as e:
            logger.error(f"Could not load TTS transport: {e}")
    return _tts_transport
