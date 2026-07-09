"""Content file resolution for platform uploaders — /content mount OR Nextcloud WebDAV.

Legacy per-agent containers bind-mount the operator's NC Content folder at
/content, so content_paths.resolve_content_path() finds clips on disk. The
centralized multi-presence stack mounts NO /content — clips live in each
presence's own Nextcloud space. Without a fallback, every scheduled upload
dies with FileNotFoundError on a centralized Cove.

This module is the ONE place platform posters (YouTube, X, future) turn a
social_queue file_path into a real local file: try the /content mount first
(legacy, free), else download the clip over WebDAV using the owning
presence's NC credentials (the same credential model pipecat uses), falling
back to env / admin credentials for single-mode Coves.

fetch_content_file() returns (path, is_temp). When is_temp is True the caller
MUST unlink the file after use — clips can be multi-GB.
"""

import tempfile
from pathlib import Path
from urllib.parse import quote

from src.env import env


def _content_relative(file_path) -> str | None:
    """Normalize a stored file_path to a Content-relative path
    ('video/shorts/x.mp4'). Mirrors content_paths.resolve_content_path."""
    if not file_path:
        return None
    p = str(file_path).strip().lstrip("/")
    for pfx in ("AgentSkills/Content/", "Content/"):
        if p.startswith(pfx):
            p = p[len(pfx):]
            break
    return p or None


async def _nc_creds(presence_id: str | None) -> tuple[str, str, str] | None:
    """(nc_url, user, password) for WebDAV — the owning presence's own creds
    when we know the presence, else env NEXTCLOUD_USER/PASSWORD (legacy
    single-mode), else the NC admin account (centralized founder)."""
    nc_url = (env("NEXTCLOUD_URL") or "").rstrip("/")
    if not nc_url:
        return None

    if presence_id:
        try:
            from src.memory.database import get_db
            async with get_db() as conn:
                r = await conn.execute(
                    "SELECT nc_username, nc_password FROM accounts WHERE id = %s",
                    (presence_id,))
                row = await r.fetchone()
            if row and row["nc_username"] and row["nc_password"]:
                return nc_url, row["nc_username"], row["nc_password"]
        except Exception:
            pass

    u, p = env("NEXTCLOUD_USER"), env("NEXTCLOUD_PASSWORD")
    if u and p:
        return nc_url, u, p

    try:
        from src.config import get_nc_admin_user, get_nc_admin_password
        au, ap = get_nc_admin_user(), get_nc_admin_password()
        if au and ap:
            return nc_url, au, ap
    except Exception:
        pass
    return None


async def fetch_content_file(file_path, presence_id: str | None = None,
                             label: str = "content") -> tuple[Path | None, bool]:
    """Resolve a social_queue file_path to a local file the uploader can stream.

    1. /content mount (legacy bind mount) — free, no copy.
    2. Nextcloud WebDAV download to a temp file — centralized stacks.

    Returns (path, is_temp); (None, False) when the file can't be found either
    way. is_temp=True files are the caller's to unlink."""
    from src.utils.content_paths import resolve_content_path
    local = resolve_content_path(file_path)
    if local:
        return local, False

    rel = _content_relative(file_path)
    if not rel:
        return None, False

    creds = await _nc_creds(presence_id)
    if not creds:
        print(f"[{label}] No /content mount and no NC credentials — "
              f"cannot fetch '{file_path}'")
        return None, False
    nc_url, user, pw = creds

    # Candidate NC paths, most specific first (same fallbacks as the mount path)
    candidates = [f"AgentSkills/Content/{rel}",
                  f"AgentSkills/Content/video/shorts/{Path(rel).name}"]

    import httpx
    suffix = Path(rel).suffix or ".mp4"
    for cand in candidates:
        url = f"{nc_url}/remote.php/dav/files/{user}/{quote(cand)}"
        try:
            async with httpx.AsyncClient(auth=(user, pw),
                                         timeout=httpx.Timeout(600.0, connect=15.0)) as client:
                async with client.stream("GET", url) as resp:
                    if resp.status_code != 200:
                        continue
                    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix,
                                                      prefix="cove-upload-")
                    try:
                        async for chunk in resp.aiter_bytes(8 * 1024 * 1024):
                            tmp.write(chunk)
                    finally:
                        tmp.close()
                    print(f"[{label}] Fetched '{cand}' via WebDAV "
                          f"({Path(tmp.name).stat().st_size // 1024} KB, user={user})")
                    return Path(tmp.name), True
        except Exception as e:
            print(f"[{label}] WebDAV fetch failed for '{cand}': {e}")
            continue
    return None, False
