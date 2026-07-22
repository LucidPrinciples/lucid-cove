# =============================================================================
# sites.py — Site management for Presences and Cove
#
# Sites live under AgentSkills/Sites/{domain}/ on the ACTING user's Nextcloud account.
# Each site has a site.yaml config and the actual site files (HTML, CSS, etc.).
# Privacy (#TIER1): list/get/deploy use get_nc_creds(request) only — that NC user.
# No host-wide union, no steward browse of other presences' Sites folders.
# tier in site.yaml: "cove" (Tier A, admin/steward NC) | "presence" (Tier B).
#
# Agents (Archimedes) build site files via this API. The operator approves
# deploys via the Attention Board. Deploy = sync to git repo → push → Cloudflare Pages.
#
# API:
#   GET  /api/sites              — List sites for current Presence
#   POST /api/sites/create       — Create a new site (NC folder + site.yaml)
#   GET  /api/sites/{domain}     — Get site config and status
#   PUT  /api/sites/{domain}     — Update site config
#   POST /api/sites/{domain}/file — Write a file to the site
#   GET  /api/sites/{domain}/files — List files in the site
# =============================================================================

import os
import logging
from datetime import datetime, timezone
from urllib.parse import quote

import httpx
import yaml
from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse

from src.dashboard.routes.nextcloud import get_nc_creds
from src.config import get_primary_agent_id, get_operator_name, get_sites_path

log = logging.getLogger("sites")


def _dlog(msg: str) -> None:
    """Deploy progress → stdout (visible in `docker logs`) AND the logger.

    The 'sites' logger has no writing handler attached, so its records never
    surface. print() is what actually makes deploys observable when debugging
    a replicated Cove. Keep deploy-path progress/errors going through here.
    """
    print(f"[site-deploy] {msg}", flush=True)
    log.info(msg)

router = APIRouter()

# Sites folder is per acting scope — resolved at call time via config.get_sites_path()
# (default AgentSkills/Sites on that NC user; env var SITES_NC_PATH / agent.yaml).



# =============================================================================
# Tier binding (#TIER1)
# =============================================================================

def _acting_site_tier(request: Request | None) -> str:
    """Return 'cove' for steward/admin doors, 'presence' for member presence doors.

    Single-mode / no presence cookie → cove (legacy founder steward surface).
    """
    if request is None:
        return "cove"
    try:
        presence = getattr(getattr(request, "state", None), "presence", None)
        if isinstance(presence, dict):
            cr = (presence.get("cove_role") or "").strip().lower()
            if cr in ("admin", "steward"):
                return "cove"
            if presence.get("id"):
                return "presence"
    except Exception:
        pass
    return "cove"


def _annotate_site_config(config: dict | None, folder: str, tier: str, presence: dict | None) -> dict:
    """Ensure list payloads carry tier + owner_presence_id without rewriting NC yet."""
    if not isinstance(config, dict):
        config = {"domain": folder, "status": "unknown"}
    out = dict(config)
    if not out.get("domain"):
        out["domain"] = folder
    # Never trust client-supplied cross-tier claims on read — stamp from acting scope
    out["tier"] = tier
    if tier == "presence" and presence and presence.get("id"):
        out["owner_presence_id"] = str(presence["id"])
    elif tier == "cove":
        out.setdefault("owner_presence_id", None)
    return out


# =============================================================================
# Helpers
# =============================================================================

def _webdav_base(nc_url: str, nc_user: str) -> str:
    """Base WebDAV URL for a user's files."""
    return f"{nc_url}/remote.php/dav/files/{nc_user}"


async def _nc_mkcol(client: httpx.AsyncClient, url: str,
                    nc_user: str, nc_pass: str) -> bool:
    """Create a WebDAV collection (directory). Returns True if created or exists."""
    try:
        resp = await client.request("MKCOL", url, auth=(nc_user, nc_pass))
        # 201 = created, 405 = already exists
        return resp.status_code in (201, 405)
    except Exception as e:
        log.error("MKCOL failed for %s: %s", url, e)
        return False


async def _nc_put(client: httpx.AsyncClient, url: str,
                  nc_user: str, nc_pass: str,
                  content: bytes, content_type: str = "text/plain") -> bool:
    """Write a file via WebDAV PUT. Returns True on success."""
    try:
        resp = await client.put(
            url, auth=(nc_user, nc_pass),
            content=content,
            headers={"Content-Type": content_type},
        )
        return resp.status_code in (200, 201, 204)
    except Exception as e:
        log.error("PUT failed for %s: %s", url, e)
        return False


async def _nc_get(client: httpx.AsyncClient, url: str,
                  nc_user: str, nc_pass: str) -> bytes | None:
    """Read a file via WebDAV GET. Returns content or None."""
    try:
        resp = await client.get(url, auth=(nc_user, nc_pass))
        if resp.status_code == 200:
            return resp.content
        return None
    except Exception as e:
        log.error("GET failed for %s: %s", url, e)
        return None


async def _nc_propfind(client: httpx.AsyncClient, url: str,
                       nc_user: str, nc_pass: str) -> list[str]:
    """List files/folders in a WebDAV collection. Returns list of names."""
    try:
        resp = await client.request(
            "PROPFIND", url, auth=(nc_user, nc_pass),
            headers={"Depth": "1"},
        )
        if resp.status_code != 207:
            return []
        # Parse multistatus XML for hrefs
        import xml.etree.ElementTree as ET
        tree = ET.fromstring(resp.content)
        ns = {"d": "DAV:"}
        hrefs = [el.text for el in tree.findall(".//d:href", ns) if el.text]
        # Filter out the collection itself (first entry) and extract filenames
        names = []
        for href in hrefs[1:]:  # skip self
            name = href.rstrip("/").rsplit("/", 1)[-1]
            if name:
                from urllib.parse import unquote
                names.append(unquote(name))
        return names
    except Exception as e:
        log.error("PROPFIND failed for %s: %s", url, e)
        return []


# =============================================================================
# List sites
# =============================================================================

async def _list_sites_internal(request: Request) -> list:
    """Internal helper — returns list of site config dicts for the ACTING NC user only.

    Used by action_board.py to check for incomplete wizards without
    making an HTTP request. Returns empty list on any error.

    #TIER1: never unions other presences' folders. Steward list = admin NC
    Tier A sites only; presence list = that presence NC Tier B only.
    """
    try:
        nc_url, nc_user, nc_pass = await get_nc_creds(request)
        if not nc_user or not nc_pass:
            return []

        tier = _acting_site_tier(request)
        presence = getattr(getattr(request, "state", None), "presence", None)
        if not isinstance(presence, dict):
            presence = None

        base = _webdav_base(nc_url, nc_user)
        sites_url = f"{base}/{get_sites_path()}"

        async with httpx.AsyncClient(timeout=30) as client:
            all_entries = await _nc_propfind(client, sites_url, nc_user, nc_pass)
            # Skip known non-site entries
            _skip = {'.DS_Store', 'README.md', '.gitkeep'}
            folders = [f for f in all_entries if f not in _skip]

            sites = []
            for folder in folders:
                config_url = f"{sites_url}/{quote(folder, safe='')}/site.yaml"
                config_data = await _nc_get(client, config_url, nc_user, nc_pass)
                config = None
                if config_data:
                    try:
                        config = yaml.safe_load(config_data)
                    except Exception:
                        config = {"domain": folder, "status": "unknown"}
                else:
                    config = {"domain": folder, "status": "unknown"}
                sites.append(_annotate_site_config(config, folder, tier, presence))

        return sites
    except Exception:
        return []


@router.get("/api/sites")
async def list_sites(request: Request):
    """List sites for the acting scope only (Tier A on admin NC, Tier B on presence NC)."""
    sites = await _list_sites_internal(request)
    if not sites and sites is not None:
        # Could be NC not configured — check
        nc_url, nc_user, nc_pass = await get_nc_creds(request)
        if not nc_user or not nc_pass:
            return JSONResponse({"error": "Nextcloud not configured"}, status_code=500)
    return {"sites": sites}


# =============================================================================
# Create site
# =============================================================================

@router.post("/api/sites/create")
async def create_site(request: Request):
    """Create a new site: NC folder + site.yaml config.

    Body: { "domain": "example.com", "site_type": "business", "title": "My Site" }
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    domain = (body.get("domain") or "").strip().lower()
    site_type = body.get("site_type", "personal")
    title = body.get("title", "")

    if not domain:
        return JSONResponse({"error": "Domain is required"}, status_code=400)

    # Sanitize domain for folder name
    safe_domain = domain.replace("/", "").replace("\\", "").replace("..", "")
    if not safe_domain:
        return JSONResponse({"error": "Invalid domain"}, status_code=400)

    nc_url, nc_user, nc_pass = await get_nc_creds(request)
    if not nc_user or not nc_pass:
        return JSONResponse({"error": "Nextcloud not configured"}, status_code=500)

    base = _webdav_base(nc_url, nc_user)
    site_path = f"{get_sites_path()}/{safe_domain}"
    site_url = f"{base}/{quote(site_path, safe='/')}"

    async with httpx.AsyncClient(timeout=30) as client:
        # Ensure Sites parent folder exists
        sites_parent = f"{base}/{get_sites_path()}"
        await _nc_mkcol(client, sites_parent, nc_user, nc_pass)

        # Create site folder
        created = await _nc_mkcol(client, site_url, nc_user, nc_pass)
        if not created:
            return JSONResponse({"error": "Failed to create site folder"}, status_code=500)

        # Build site.yaml config
        now = datetime.now(timezone.utc).isoformat()
        agent_id = get_primary_agent_id()
        operator = get_operator_name()

        tier = _acting_site_tier(request)
        presence = getattr(getattr(request, "state", None), "presence", None)
        owner_presence_id = None
        if isinstance(presence, dict) and presence.get("id") and tier == "presence":
            owner_presence_id = str(presence["id"])

        config = {
            "domain": domain,
            "title": title or domain,
            "site_type": site_type,
            "tier": tier,  # cove | presence — #TIER1
            "owner_presence_id": owner_presence_id,
            "status": "setup",  # setup → building → staging → live
            "owner_agent": agent_id,
            "owner_operator": operator,
            "created_at": now,
            "updated_at": now,
            "github": {
                "repo": "",
                "branch": "main",
                "connected": False,
            },
            "cloudflare": {
                "project": "",
                "connected": False,
            },
            "pages": [],
            "design": {
                "brief": "",
                "colors": [],
                "style": "",
            },
        }

        config_yaml = yaml.dump(config, default_flow_style=False, sort_keys=False)
        config_url = f"{site_url}/site.yaml"
        saved = await _nc_put(client, config_url, nc_user, nc_pass,
                              config_yaml.encode("utf-8"), "text/yaml")

        if not saved:
            return JSONResponse({"error": "Site folder created but config save failed"}, status_code=500)

    log.info("Site created: %s for %s/%s", domain, nc_user, agent_id)
    return {"ok": True, "domain": domain, "path": site_path, "config": config}


# =============================================================================
# Get site config
# =============================================================================

@router.get("/api/sites/{domain}")
async def get_site(request: Request, domain: str):
    """Get a site's config."""
    nc_url, nc_user, nc_pass = await get_nc_creds(request)
    if not nc_user or not nc_pass:
        return JSONResponse({"error": "Nextcloud not configured"}, status_code=500)

    base = _webdav_base(nc_url, nc_user)
    config_url = f"{base}/{get_sites_path()}/{quote(domain, safe='')}/site.yaml"
    man_url = f"{base}/{get_sites_path()}/{quote(domain, safe='')}/{_DEPLOY_MANIFEST}"

    async with httpx.AsyncClient(timeout=30) as client:
        config_data = await _nc_get(client, config_url, nc_user, nc_pass)
        manifest_raw = await _nc_get(client, man_url, nc_user, nc_pass) if config_data else None

    if not config_data:
        return JSONResponse({"error": "Site not found"}, status_code=404)

    try:
        config = yaml.safe_load(config_data)
    except Exception as e:
        return JSONResponse({"error": f"Invalid config: {e}"}, status_code=500)

    # Last-deploy reference (commit + timestamp) from the deploy manifest, if present.
    last_deploy = None
    if manifest_raw:
        try:
            import json
            md = json.loads(manifest_raw)
            if isinstance(md, dict) and md.get("commit"):
                last_deploy = {"commit": md.get("commit", ""), "at": md.get("deployed_at", "")}
        except Exception:
            last_deploy = None

    return {"site": config, "last_deploy": last_deploy}


# =============================================================================
# Update site config
# =============================================================================

@router.put("/api/sites/{domain}")
async def update_site(request: Request, domain: str):
    """Update fields in a site's config.

    Body: partial config dict to merge (e.g., {"status": "building", "design": {...}})
    """
    try:
        updates = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    nc_url, nc_user, nc_pass = await get_nc_creds(request)
    if not nc_user or not nc_pass:
        return JSONResponse({"error": "Nextcloud not configured"}, status_code=500)

    base = _webdav_base(nc_url, nc_user)
    config_url = f"{base}/{get_sites_path()}/{quote(domain, safe='')}/site.yaml"

    async with httpx.AsyncClient(timeout=30) as client:
        # Read current config
        config_data = await _nc_get(client, config_url, nc_user, nc_pass)
        if not config_data:
            return JSONResponse({"error": "Site not found"}, status_code=404)

        try:
            config = yaml.safe_load(config_data)
        except Exception:
            config = {}

        # Merge updates (shallow for top-level, deep for nested dicts)
        for key, val in updates.items():
            if key in ("domain", "created_at"):
                continue  # immutable fields
            if isinstance(val, dict) and isinstance(config.get(key), dict):
                config[key].update(val)
            else:
                config[key] = val

        config["updated_at"] = datetime.now(timezone.utc).isoformat()

        # Write back
        config_yaml = yaml.dump(config, default_flow_style=False, sort_keys=False)
        saved = await _nc_put(client, config_url, nc_user, nc_pass,
                              config_yaml.encode("utf-8"), "text/yaml")

    if saved:
        return {"ok": True, "site": config}
    return JSONResponse({"error": "Failed to save config"}, status_code=500)


# =============================================================================
# Delete site
# =============================================================================

@router.delete("/api/sites/{domain}")
async def delete_site(request: Request, domain: str):
    """Delete a site folder from NC. Removes site.yaml and all site files.

    Does NOT touch GitHub repos or Cloudflare projects — those are external.
    """
    nc_url, nc_user, nc_pass = await get_nc_creds(request)
    if not nc_user or not nc_pass:
        return JSONResponse({"error": "Nextcloud not configured"}, status_code=500)

    base = _webdav_base(nc_url, nc_user)
    site_url = f"{base}/{get_sites_path()}/{quote(domain, safe='')}"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.request(
                "DELETE", site_url, auth=(nc_user, nc_pass),
            )
            if resp.status_code in (200, 204):
                log.info("Site deleted: %s", domain)
                return {"ok": True, "domain": domain}
            elif resp.status_code == 404:
                return JSONResponse({"error": "Site not found"}, status_code=404)
            else:
                return JSONResponse({"error": f"Delete failed: HTTP {resp.status_code}"}, status_code=resp.status_code)
    except Exception as e:
        return JSONResponse({"error": str(e)[:200]}, status_code=500)


# =============================================================================
# Write file to site
# =============================================================================

@router.post("/api/sites/{domain}/file")
async def write_site_file(request: Request, domain: str):
    """Write a file to the site folder.

    Body: { "path": "index.html", "content": "<html>..." }
    Paths are relative to the site root (the deploy root).
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    file_path = (body.get("path") or "").strip()
    content = body.get("content", "")

    if not file_path:
        return JSONResponse({"error": "File path required"}, status_code=400)

    # Security: prevent path traversal
    if ".." in file_path or file_path.startswith("/"):
        return JSONResponse({"error": "Invalid file path"}, status_code=400)

    nc_url, nc_user, nc_pass = await get_nc_creds(request)
    if not nc_user or not nc_pass:
        return JSONResponse({"error": "Nextcloud not configured"}, status_code=500)

    base = _webdav_base(nc_url, nc_user)
    # Files go in the site root
    file_url = f"{base}/{get_sites_path()}/{quote(domain, safe='')}/{quote(file_path, safe='/')}"

    # Create intermediate directories if path has them
    parts = file_path.split("/")
    if len(parts) > 1:
        async with httpx.AsyncClient(timeout=30) as client:
            for i in range(len(parts) - 1):
                dir_path = "/".join(parts[:i + 1])
                dir_url = f"{base}/{get_sites_path()}/{quote(domain, safe='')}/{quote(dir_path, safe='/')}"
                await _nc_mkcol(client, dir_url, nc_user, nc_pass)

    # Determine content type
    ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
    content_types = {
        "html": "text/html", "css": "text/css", "js": "application/javascript",
        "json": "application/json", "svg": "image/svg+xml", "xml": "text/xml",
        "txt": "text/plain", "md": "text/markdown", "yaml": "text/yaml",
        "yml": "text/yaml", "ico": "image/x-icon", "png": "image/png",
        "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp",
    }
    ct = content_types.get(ext, "application/octet-stream")

    async with httpx.AsyncClient(timeout=30) as client:
        if isinstance(content, str):
            content = content.encode("utf-8")
        saved = await _nc_put(client, file_url, nc_user, nc_pass, content, ct)

    if saved:
        return {"ok": True, "path": file_path}
    return JSONResponse({"error": "Failed to write file"}, status_code=500)


# =============================================================================
# List site files
# =============================================================================

@router.get("/api/sites/{domain}/files")
async def list_site_files(request: Request, domain: str):
    """List files in a site's root folder."""
    nc_url, nc_user, nc_pass = await get_nc_creds(request)
    if not nc_user or not nc_pass:
        return JSONResponse({"error": "Nextcloud not configured"}, status_code=500)

    base = _webdav_base(nc_url, nc_user)
    site_url = f"{base}/{get_sites_path()}/{quote(domain, safe='')}"

    async with httpx.AsyncClient(timeout=30) as client:
        files = await _nc_propfind(client, site_url, nc_user, nc_pass)

    return {"domain": domain, "files": files}


# =============================================================================
# Upload asset (logo, images, etc.)
# =============================================================================

@router.post("/api/sites/{domain}/upload")
async def upload_site_asset(
    request: Request,
    domain: str,
    file: UploadFile = File(...),
    folder: str = Form("assets"),
):
    """Upload a file to a site's asset folder via NC WebDAV.

    Multipart form: file (the upload), folder (default 'assets').
    Files go to AgentSkills/Sites/{domain}/{folder}/{filename}.
    Returns the relative path for storage in site.yaml.
    """
    if not file.filename:
        return JSONResponse({"error": "No file provided"}, status_code=400)

    # Security: sanitize folder and filename
    folder = folder.strip().strip("/")
    if ".." in folder or ".." in file.filename:
        return JSONResponse({"error": "Invalid path"}, status_code=400)

    # Read file content
    content = await file.read()
    if not content:
        return JSONResponse({"error": "Empty file"}, status_code=400)

    # Size limit: 10MB
    if len(content) > 10 * 1024 * 1024:
        return JSONResponse({"error": "File too large (max 10MB)"}, status_code=400)

    nc_url, nc_user, nc_pass = await get_nc_creds(request)
    if not nc_user or not nc_pass:
        return JSONResponse({"error": "Nextcloud not configured"}, status_code=500)

    base = _webdav_base(nc_url, nc_user)
    site_base = f"{base}/{get_sites_path()}/{quote(domain, safe='')}"

    async with httpx.AsyncClient(timeout=30) as client:
        # Ensure the folder exists
        folder_url = f"{site_base}/{quote(folder, safe='/')}"
        await _nc_mkcol(client, folder_url, nc_user, nc_pass)

        # Determine content type
        ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
        content_types = {
            "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "svg": "image/svg+xml", "webp": "image/webp", "gif": "image/gif",
            "ico": "image/x-icon", "pdf": "application/pdf",
        }
        ct = file.content_type or content_types.get(ext, "application/octet-stream")

        # Upload
        file_url = f"{folder_url}/{quote(file.filename, safe='')}"
        saved = await _nc_put(client, file_url, nc_user, nc_pass, content, ct)

    if saved:
        rel_path = f"{folder}/{file.filename}"
        log.info("Site asset uploaded: %s/%s → %s", domain, rel_path, len(content))
        return {"ok": True, "path": rel_path, "filename": file.filename, "size": len(content)}

    return JSONResponse({"error": "Upload failed"}, status_code=500)


# =============================================================================
# GitHub operations — PAT-based repo management
# =============================================================================

def _get_github_pat(request: Request) -> str:
    """Get the Presence's GitHub PAT from features/settings."""
    # In single mode, read from feature overrides file
    # In multi mode, would read from account preferences
    try:
        from src.config import get_feature_flags
        features = get_feature_flags()
        return features.get("github_pat", "")
    except Exception:
        return ""


@router.post("/api/sites/github/create-repo")
async def github_create_repo(request: Request):
    """Create a new GitHub repository using the Presence's PAT."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    name = (body.get("name") or "").strip()
    if not name:
        return JSONResponse({"error": "Repository name required"}, status_code=400)

    pat = _get_github_pat(request)
    if not pat:
        return JSONResponse({"error": "GitHub token not saved. Save your PAT first."}, status_code=400)

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.github.com/user/repos",
                headers={
                    "Authorization": f"Bearer {pat}",
                    "Accept": "application/vnd.github+json",
                },
                json={
                    "name": name,
                    "auto_init": True,
                    "private": False,
                },
            )

        if resp.status_code == 201:
            data = resp.json()
            return {"ok": True, "full_name": data["full_name"], "url": data["html_url"]}
        elif resp.status_code == 422:
            # Repo already exists
            return JSONResponse({"error": "Repository already exists"}, status_code=409)
        elif resp.status_code == 401:
            return JSONResponse({"error": "Token invalid or expired. Generate a new one."}, status_code=401)
        else:
            detail = resp.json().get("message", resp.text[:200])
            return JSONResponse({"error": f"GitHub API: {detail}"}, status_code=resp.status_code)

    except Exception as e:
        return JSONResponse({"error": str(e)[:200]}, status_code=500)


@router.get("/api/sites/github/test")
async def github_test_connection(request: Request, repo: str = ""):
    """Test access to a GitHub repository using the Presence's PAT."""
    if not repo:
        return JSONResponse({"error": "Repo parameter required"}, status_code=400)

    pat = _get_github_pat(request)
    if not pat:
        return JSONResponse({"error": "GitHub token not saved"}, status_code=400)

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"https://api.github.com/repos/{repo}",
                headers={
                    "Authorization": f"Bearer {pat}",
                    "Accept": "application/vnd.github+json",
                },
            )

        if resp.status_code == 200:
            data = resp.json()
            return {
                "ok": True,
                "full_name": data["full_name"],
                "description": data.get("description", ""),
                "private": data.get("private", False),
                "default_branch": data.get("default_branch", "main"),
            }
        elif resp.status_code == 404:
            return JSONResponse({"ok": False, "error": "Repo not found — check the name or create it"}, status_code=200)
        elif resp.status_code == 401:
            return JSONResponse({"ok": False, "error": "Token invalid or expired"}, status_code=200)
        else:
            return JSONResponse({"ok": False, "error": f"HTTP {resp.status_code}"}, status_code=200)

    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)[:200]}, status_code=200)


# =============================================================================
# GitHub diff — for approval card display
# =============================================================================

@router.get("/api/sites/github/diff")
async def github_diff(request: Request, repo: str = "", base: str = "main", head: str = ""):
    """Get diff between two branches for site-edit approval display."""
    if not repo or not head:
        return JSONResponse({"error": "repo and head parameters required"}, status_code=400)

    pat = _get_github_pat(request)
    if not pat:
        return JSONResponse({"error": "GitHub token not saved"}, status_code=400)

    try:
        from src.utils.github import github_get_compare
        compare = await github_get_compare(repo, base, head, pat)
        return {
            "repo": repo,
            "base": base,
            "head": head,
            "status": compare.get("status", ""),
            "files": compare.get("files", []),
            "total_commits": compare.get("total_commits", 0),
        }
    except Exception as e:
        return JSONResponse({"error": str(e)[:200]}, status_code=500)


# =============================================================================
# Deploy — mirror the WHOLE NC site folder to GitHub (one commit, approval-gated)
#
# The NC folder AgentSkills/Sites/{domain}/ is the source of truth. Deploy reads
# every file in it, commits the entire folder to the site's GitHub repo as one
# commit on a deploy branch, and raises an operator approval. On approve, the
# branch merges to main and Cloudflare Pages deploys. The mirror is exact:
# files removed from the folder are removed from the live site (git history keeps
# every prior state, so nothing is ever lost).
# =============================================================================

_DEPLOY_MANIFEST = ".lucid-deploy.json"  # per-site etag→sha cache; never deployed
_DEPLOY_SKIP_NAMES = {".DS_Store", "site.yaml", ".gitkeep", "Thumbs.db", _DEPLOY_MANIFEST}
_DEPLOY_SKIP_SUFFIX = ("-redesign.html",)  # scratch/preview files never go live


async def _nc_collect_site_files(client, nc_url, base, domain, nc_user, nc_pass) -> dict:
    """Recursively read every file under the NC site folder.

    Returns {repo_relative_path: bytes}. Skips config + scratch files.

    Walks one level at a time with Depth:1 (Nextcloud disables Depth:infinity by
    default, which silently returns nothing).
    """
    import xml.etree.ElementTree as ET
    from urllib.parse import unquote

    ns = {"d": "DAV:"}
    marker = f"/{get_sites_path()}/{domain}/"
    root_url = f"{base}/{get_sites_path()}/{quote(domain, safe='')}"
    files: dict = {}

    async def walk(url):
        resp = await client.request(
            "PROPFIND", url, auth=(nc_user, nc_pass), headers={"Depth": "1"}
        )
        if resp.status_code != 207:
            log.warning("Deploy PROPFIND %s -> HTTP %s", url, resp.status_code)
            return
        tree = ET.fromstring(resp.content)
        for i, r in enumerate(tree.findall("d:response", ns)):
            if i == 0:
                continue  # first entry is the collection itself
            href_el = r.find("d:href", ns)
            if href_el is None or not href_el.text:
                continue
            href = href_el.text
            full = nc_url.rstrip("/") + href
            if r.find(".//d:resourcetype/d:collection", ns) is not None:
                await walk(full)  # recurse into subdirectory
                continue
            idx = href.find(marker)
            if idx < 0:
                continue
            rel = unquote(href[idx + len(marker):])
            if not rel:
                continue
            name = rel.rsplit("/", 1)[-1]
            if name in _DEPLOY_SKIP_NAMES or rel.endswith(_DEPLOY_SKIP_SUFFIX):
                continue
            content = await _nc_get(client, full, nc_user, nc_pass)
            if content is not None:
                files[rel] = content

    await walk(root_url)
    return files


async def _nc_list_etags(client, nc_url, base, domain, nc_user, nc_pass) -> dict:
    """PROPFIND walk (metadata only) → {repo_rel_path: {"etag", "url"}}.

    No content is downloaded. etag is Nextcloud's per-file content fingerprint;
    `url` is the exact WebDAV href for a later GET if the file turns out changed.
    """
    import xml.etree.ElementTree as ET
    from urllib.parse import unquote

    ns = {"d": "DAV:"}
    marker = f"/{get_sites_path()}/{domain}/"
    root_url = f"{base}/{get_sites_path()}/{quote(domain, safe='')}"
    out: dict = {}

    async def walk(url):
        resp = await client.request(
            "PROPFIND", url, auth=(nc_user, nc_pass), headers={"Depth": "1"}
        )
        if resp.status_code != 207:
            log.warning("Deploy etag PROPFIND %s -> HTTP %s", url, resp.status_code)
            return
        tree = ET.fromstring(resp.content)
        for i, r in enumerate(tree.findall("d:response", ns)):
            if i == 0:
                continue
            href_el = r.find("d:href", ns)
            if href_el is None or not href_el.text:
                continue
            href = href_el.text
            full = nc_url.rstrip("/") + href
            if r.find(".//d:resourcetype/d:collection", ns) is not None:
                await walk(full)
                continue
            idx = href.find(marker)
            if idx < 0:
                continue
            rel = unquote(href[idx + len(marker):])
            if not rel:
                continue
            name = rel.rsplit("/", 1)[-1]
            if name in _DEPLOY_SKIP_NAMES or rel.endswith(_DEPLOY_SKIP_SUFFIX):
                continue
            et_el = r.find(".//d:getetag", ns)
            etag = (et_el.text or "").strip('"') if et_el is not None else ""
            out[rel] = {"etag": etag, "url": full}

    await walk(root_url)
    return out


async def _read_deploy_manifest(client, base, domain, nc_user, nc_pass) -> dict:
    """Read the per-site etag→sha cache from NC. Returns {path: {etag, sha}}."""
    url = f"{base}/{get_sites_path()}/{quote(domain, safe='')}/{_DEPLOY_MANIFEST}"
    raw = await _nc_get(client, url, nc_user, nc_pass)
    if not raw:
        return {}
    try:
        import json
        data = json.loads(raw)
        return data.get("files", {}) if isinstance(data, dict) else {}
    except Exception:
        return {}


async def _write_deploy_manifest(client, base, domain, nc_user, nc_pass,
                                 files_meta: dict, commit_sha: str) -> None:
    """Write the etag→sha cache back to NC (best-effort).

    Also stamps deployed_at so the manage panel can show a 'last deployed' reference.
    """
    import json
    from datetime import datetime, timezone
    url = f"{base}/{get_sites_path()}/{quote(domain, safe='')}/{_DEPLOY_MANIFEST}"
    body = json.dumps({
        "commit": commit_sha,
        "deployed_at": datetime.now(timezone.utc).isoformat(),
        "files": files_meta,
    }).encode("utf-8")
    await _nc_put(client, url, nc_user, nc_pass, body, content_type="application/json")


async def _deploy_site_core(nc_url, nc_user, nc_pass, domain, description, agent_id) -> dict:
    """Mirror the NC site folder into a GitHub deploy branch + raise approval.

    Shared by the /deploy endpoint and the site_deploy agent tool.
    Returns {ok: bool, ...}.
    """
    import uuid
    from datetime import datetime, timezone

    base = _webdav_base(nc_url, nc_user)

    from src.config import get_feature_flags
    pat = get_feature_flags().get("github_pat", "")
    if not pat:
        _dlog(f"Deploy {domain} FAILED: github_pat not set")
        return {"ok": False, "error": "GitHub PAT not configured (Settings → Tools → GitHub)"}

    async with httpx.AsyncClient(timeout=90) as client:
        # Site config (repo + branch) from site.yaml
        cfg_url = f"{base}/{get_sites_path()}/{quote(domain, safe='')}/site.yaml"
        cfg_raw = await _nc_get(client, cfg_url, nc_user, nc_pass)
        if not cfg_raw:
            _dlog(f"Deploy {domain} FAILED: site.yaml not found at {cfg_url}")
            return {"ok": False, "error": f"site.yaml not found for {domain}"}
        cfg = yaml.safe_load(cfg_raw) or {}
        gh = cfg.get("github", {})
        repo = gh.get("repo", "")
        main_branch = gh.get("branch", "main")
        if not repo:
            _dlog(f"Deploy {domain} FAILED: no github.repo in site.yaml")
            return {"ok": False, "error": f"No GitHub repo connected for {domain}"}

        from src.utils.github import github_commit_tree, github_get_tree_shas

        # Cheap pass: live repo shas + NC etags + last manifest. No content yet.
        existing = await github_get_tree_shas(repo, main_branch, pat)
        listing = await _nc_list_etags(client, nc_url, base, domain, nc_user, nc_pass)
        if not listing:
            _dlog(f"Deploy {domain} FAILED: 0 files listed in NC folder")
            return {"ok": False, "error": f"No files found in the {domain} folder"}
        manifest = await _read_deploy_manifest(client, base, domain, nc_user, nc_pass)

        # Download ONLY files that differ from the live site. A file is "unchanged"
        # iff its NC etag matches the manifest AND that cached sha matches live —
        # so we reuse the live blob with no download or upload.
        files, unchanged = {}, {}
        for path, meta in listing.items():
            m = manifest.get(path)
            if m and m.get("etag") == meta["etag"] and existing.get(path) == m.get("sha"):
                unchanged[path] = m["sha"]
            else:
                content = await _nc_get(client, meta["url"], nc_user, nc_pass)
                if content is not None:
                    files[path] = content

        _dlog(f"Deploy {domain}: {len(files)} changed/new (downloaded), "
              f"{len(unchanged)} unchanged (skipped), committing to {repo}")

        short_id = uuid.uuid4().hex[:6]
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        branch = f"{agent_id}/site-deploy-{date_str}-{short_id}"
        commit = await github_commit_tree(
            repo, files,
            message=f"Deploy {domain}: {description}",
            branch=branch, pat=pat, parent_branch=main_branch,
            unchanged_shas=unchanged,
        )

        # Refresh the etag→sha cache (content-keyed; correct regardless of approval).
        shas = commit.get("shas", {})
        new_manifest = {p: {"etag": listing[p]["etag"], "sha": shas[p]}
                        for p in listing if p in shas}
        if new_manifest:
            await _write_deploy_manifest(client, base, domain, nc_user, nc_pass,
                                         new_manifest, commit.get("commit_sha", ""))

    # Nothing changed since the live site — no approval needed
    if commit.get("no_changes"):
        _dlog(f"Deploy {domain}: no changes vs live")
        return {"ok": True, "no_changes": True, "domain": domain, "file_count": commit["file_count"]}

    changed = commit.get("changed", commit["file_count"])
    _dlog(f"Deploy {domain}: branch {branch} committed ({changed} changed) — raising approval")

    # Same approval path as site edits: approve → merge branch to main → Cloudflare deploys
    from src.tools.approval import _save_approval_to_db, ApprovalRequest, Tier
    req = ApprovalRequest(
        tool_name="site_deploy",
        description=f"Deploy {domain} ({changed} file(s) changed): {description}",
        args={"domain": domain, "repo": repo, "branch": branch, "description": description},
        tier=Tier.APPROVE,
    )
    await _save_approval_to_db(req)
    try:
        import asyncio
        from src.tools.calendar_notify import push_approval_to_calendar
        asyncio.ensure_future(push_approval_to_calendar(
            request_id=req.request_id, tool_name="site_deploy",
            description=f"Deploy {domain} — {changed} file(s) changed",
        ))
    except Exception:
        pass

    return {"ok": True, "domain": domain, "repo": repo, "branch": branch,
            "file_count": commit["file_count"], "changed": changed,
            "request_id": req.request_id}


@router.post("/api/sites/{domain}/deploy")
async def deploy_site(request: Request, domain: str):
    """Mirror the whole NC site folder to GitHub and raise a deploy approval.

    Body (optional): { "description": "what changed" }.
    On approve, the deploy branch merges to main and Cloudflare Pages deploys.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    description = (body.get("description") or "Full site deploy").strip()

    nc_url, nc_user, nc_pass = await get_nc_creds(request)
    if not nc_user or not nc_pass:
        return JSONResponse({"error": "Nextcloud not configured"}, status_code=500)

    result = await _deploy_site_core(
        nc_url, nc_user, nc_pass, domain, description, get_primary_agent_id()
    )
    if not result.get("ok"):
        return JSONResponse({"error": result.get("error", "Deploy failed")}, status_code=500)
    return result
