"""
profile.py — Presence presentation (#169): the public face of a Presence (Operator +
Agent), and the manage side behind it.

A Presence profile fuses three sources, all keyed by the registry @handle:
  - accounts            — display_name, agent_name, last_name (Cove), agent_identity
                          {archetype, frequency, tuning_key, nickname, dials}
  - presence_profiles   — the presentation extras (avatars, bio, skills, links)
  - the marketplace     — this seller's offerings (from the Socrates catalog)

Two views: GET/POST /api/profile/me (the operator manages their own) and
GET /api/profile/{handle} (anyone in the Haven views a Presence, linked from a
product card). Skills come from a TEMPLATED taxonomy so the marketplace is
searchable/matchable, with free tags allowed on top.

Handle-keyed throughout, so the same surface serves an agent's own profile later
(agents are first-class economic actors — spec §8).
"""
import hmac
import json
import os
import re
from src.env import env, env_bool
import pathlib

import httpx
from fastapi import APIRouter, Request, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse

from src.dashboard.routes.presence import get_current_presence

router = APIRouter()

MARKET_URL = env("MARKETPLACE_API_URL")
MARKET_SECRET = env("SHARED_CONTAINER_SECRET")
SECRET = env("SHARED_CONTAINER_SECRET")
# Where uploaded avatars live (a writable volume) + the public base they're served from.
AVATAR_DIR = env("LP_AVATAR_DIR", "/app/data/avatars")
LP_PUBLIC_BASE = env("LP_PUBLIC_BASE", "https://app.lucidcove.org").rstrip("/")
_IMG_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}
# Hub-resolution (#173): the cross-Cove profile mirror lives on the registry master
# (the hub). A Cove resolves/pushes profiles against the hub via LP_REGISTRY_URL; the
# hub itself reads/writes its own profile_mirror in-process.
HUB_URL = env("LP_REGISTRY_URL")


def _is_master() -> bool:
    return env_bool("LP_REGISTRY_MASTER")

# Templated skills (framework-flavored + practical). The marketplace facets on these;
# operators can also add free tags. "Tune" ties the LP practice into the matchmaking.
SKILLS_TAXONOMY = {
    "Create": ["Writing", "Editing", "Design", "Music", "Video", "Art"],
    "Build": ["Code", "Automation", "Web", "Data", "Systems"],
    "Guide": ["Coaching", "Teaching", "Research", "Strategy", "Facilitation"],
    "Tend": ["Operations", "Admin", "Scheduling", "Bookkeeping", "Support"],
    "Tune": ["Tuning practice", "Canon study", "Reflection", "Coherence work"],
}
_VALID_SKILLS = {s for group in SKILLS_TAXONOMY.values() for s in group}


@router.get("/api/profile/skills")
async def skills_taxonomy(request: Request):
    """The templated skill vocabulary (for chips + search facets)."""
    return {"taxonomy": SKILLS_TAXONOMY}


async def _facets_for_handles(conn, handles) -> dict:
    """Match-facets for a set of seller handles, from the hub: the operator's skills +
    the agent's archetype/frequency (the matchable LP identity). One query."""
    hs = [str(h).lstrip("@").lower() for h in (handles or []) if str(h).strip()]
    if not hs:
        return {}
    r = await conn.execute(
        """SELECT lower(a.username) AS handle, a.agent_name,
                  a.agent_identity->>'archetype' AS archetype,
                  a.agent_identity->>'frequency' AS frequency,
                  pp.skills AS skills, pp.avatar_url, pp.agent_avatar_url
           FROM accounts a
                LEFT JOIN presence_profiles pp ON pp.handle = lower(a.username)
           WHERE lower(a.username) = ANY(%s)""", (hs,))
    out = {}
    for row in await r.fetchall():
        out[row["handle"]] = {
            "agent_name": row.get("agent_name") or "",
            "archetype": row.get("archetype") or "",
            "frequency": row.get("frequency") or "",
            "skills": row.get("skills") or [],
            "avatar_url": row.get("avatar_url") or "",
            "agent_avatar_url": row.get("agent_avatar_url") or "",
        }
    # Cross-Cove (#173): handles not in this instance's accounts resolve from the mirror.
    missing = [h for h in hs if h not in out]
    if missing:
        rm = await conn.execute(
            """SELECT handle, agent_name, archetype, frequency, skills, avatar_url, agent_avatar_url
               FROM profile_mirror WHERE handle = ANY(%s)""", (missing,))
        for row in await rm.fetchall():
            out[row["handle"]] = {
                "agent_name": row.get("agent_name") or "",
                "archetype": row.get("archetype") or "",
                "frequency": row.get("frequency") or "",
                "skills": row.get("skills") or [],
                "avatar_url": row.get("avatar_url") or "",
                "agent_avatar_url": row.get("agent_avatar_url") or "",
            }
    return out


@router.post("/api/profile/facets")
async def profile_facets(request: Request):
    """Service endpoint (secret-gated): facets for a list of handles, so the market
    search (which may run on a Cove) can enrich the catalog from the hub's profiles."""
    body = await request.json()
    supplied = request.headers.get("X-Shared-Secret", "") or (body.get("secret") or "")
    if not (SECRET and supplied and hmac.compare_digest(supplied, SECRET)):
        raise HTTPException(403, "Invalid secret")
    from src.memory.database import get_db
    async with get_db() as conn:
        return {"facets": await _facets_for_handles(conn, body.get("handles") or [])}


async def _offerings_for(handle: str) -> list:
    """This Presence's marketplace listings (best-effort; empty if the market's down)."""
    if not (MARKET_URL and MARKET_SECRET and handle):
        return []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(MARKET_URL.rstrip("/") + "/api/marketplace/listings",
                                  headers={"X-Shared-Secret": MARKET_SECRET})
        if r.status_code != 200:
            return []
        listings = (r.json() or {}).get("listings", [])
        return [l for l in listings if (l.get("seller_handle") or "").lower() == handle.lower()]
    except Exception:
        return []


# The 9 presence archetype images shipped in static/avatars/archetypes/. An agent
# with no uploaded avatar falls back to its archetype art (e.g. "The Navigator" ->
# navigator.png), so a fresh Presence never shows a bare initial.
_ARCHETYPE_IMAGES = {"anchor", "architect", "catalyst", "challenger", "companion",
                     "guide", "navigator", "spark", "witness"}


def _archetype_avatar(archetype: str) -> str:
    """The archetype's default avatar URL, or '' if the archetype has no shipped image."""
    slug = (archetype or "").strip().lower()
    if slug.startswith("the "):
        slug = slug[4:]
    slug = slug.split()[-1] if slug else ""
    return f"/static/avatars/archetypes/{slug}.png" if slug in _ARCHETYPE_IMAGES else ""


def _agent_facets(agent_identity) -> dict:
    """Pull the agent's matchable LP facets out of agent_identity (dict or JSON str)."""
    ai = agent_identity
    if isinstance(ai, str):
        try:
            ai = json.loads(ai or "{}")
        except Exception:
            ai = {}
    ai = ai or {}
    return {
        "agent_name": ai.get("agent_name") or "",
        "archetype": ai.get("archetype") or "",
        "frequency": ai.get("frequency") or "",
        "tuning_key": ai.get("tuning_key") or "",
        "nickname": ai.get("nickname") or "",
        "skills": ai.get("skills") or [],
    }


async def _assemble(conn, handle: str, *, include_private: bool = False) -> dict:
    handle = handle.lstrip("@").lower()
    r = await conn.execute(
        """SELECT username, display_name, agent_name, last_name, agent_identity
           FROM accounts WHERE lower(username) = %s AND active = TRUE""", (handle,))
    acct = await r.fetchone()
    if not acct:
        return None
    p = await conn.execute(
        "SELECT avatar_url, agent_avatar_url, bio, skills, links FROM presence_profiles WHERE handle = %s",
        (handle,))
    prof = await p.fetchone() or {}
    agent = _agent_facets(acct.get("agent_identity"))
    # Private persona (dials + lens/specialized-lines + shade) for the owner/admin full
    # presence-admin view — never exposed on a public profile. (CF-58)
    import json as _json
    _raw = acct.get("agent_identity")
    try:
        _ai = _raw if isinstance(_raw, dict) else (_json.loads(_raw) if _raw else {})
    except Exception:
        _ai = {}
    _priv = {"personality": _ai.get("personality") or {}, "lens": _ai.get("lens") or {},
             "shade": _ai.get("shade") or ""} if include_private else {}
    return {
        "handle": acct["username"],
        "operator": {
            "name": acct.get("display_name") or acct["username"],
            "avatar_url": prof.get("avatar_url") or "",
            "bio": prof.get("bio") or "",
            "skills": prof.get("skills") or [],
            "links": prof.get("links") or {},
        },
        "agent": {
            # Fall back to agent_identity.agent_name so the Presence card shows the
            # agent (e.g. "Knight") even when accounts.agent_name wasn't populated —
            # same source the MC header uses (#209b).
            "name": (acct.get("agent_name") or "").strip() or agent["agent_name"],
            "cove": acct.get("last_name") or "",
            # Uploaded avatar wins; otherwise default to the archetype art so a fresh
            # Presence agent shows its Navigator/Anchor/etc image, not a bare initial.
            "avatar_url": (prof.get("agent_avatar_url") or "") or _archetype_avatar(agent["archetype"]),
            "archetype": agent["archetype"],
            "frequency": agent["frequency"],
            "tuning_key": agent["tuning_key"],
            "nickname": agent["nickname"],
            "skills": agent["skills"],
            **_priv,
        },
        "offerings": await _offerings_for(handle),
    }


# ── Cross-Cove profile mirror (#173) ─────────────────────────────────────────
def _flatten_for_mirror(prof: dict) -> dict:
    """Nested assemble shape → flat profile_mirror columns (public fields only)."""
    op = prof.get("operator") or {}
    ag = prof.get("agent") or {}
    return {
        "handle": (prof.get("handle") or "").lstrip("@").lower(),
        "display_name": op.get("name") or "",
        "agent_name": ag.get("name") or "",
        "cove": ag.get("cove") or "",
        "archetype": ag.get("archetype") or "",
        "frequency": ag.get("frequency") or "",
        "tuning_key": ag.get("tuning_key") or "",
        "nickname": ag.get("nickname") or "",
        "avatar_url": op.get("avatar_url") or "",
        "agent_avatar_url": ag.get("avatar_url") or "",
        "bio": op.get("bio") or "",
        "skills": op.get("skills") or [],
        "links": op.get("links") or {},
    }


async def _mirror_upsert(conn, m: dict):
    await conn.execute(
        """INSERT INTO profile_mirror
             (handle, display_name, agent_name, cove, archetype, frequency, tuning_key,
              nickname, avatar_url, agent_avatar_url, bio, skills, links, updated_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
           ON CONFLICT (handle) DO UPDATE SET
             display_name=EXCLUDED.display_name, agent_name=EXCLUDED.agent_name,
             cove=EXCLUDED.cove, archetype=EXCLUDED.archetype, frequency=EXCLUDED.frequency,
             tuning_key=EXCLUDED.tuning_key, nickname=EXCLUDED.nickname,
             avatar_url=EXCLUDED.avatar_url, agent_avatar_url=EXCLUDED.agent_avatar_url,
             bio=EXCLUDED.bio, skills=EXCLUDED.skills, links=EXCLUDED.links, updated_at=NOW()""",
        (m["handle"], m["display_name"], m["agent_name"], m["cove"], m["archetype"],
         m["frequency"], m["tuning_key"], m["nickname"], m["avatar_url"], m["agent_avatar_url"],
         m["bio"], json.dumps(m["skills"]), json.dumps(m["links"])))


async def _mirror_get(conn, handle: str) -> dict:
    """Read a mirrored profile (assemble shape, minus offerings — caller adds those)."""
    handle = handle.lstrip("@").lower()
    r = await conn.execute("SELECT * FROM profile_mirror WHERE handle = %s", (handle,))
    m = await r.fetchone()
    if not m:
        return None
    return {
        "handle": m["handle"],
        "operator": {
            "name": m.get("display_name") or m["handle"], "avatar_url": m.get("avatar_url") or "",
            "bio": m.get("bio") or "", "skills": m.get("skills") or [], "links": m.get("links") or {},
        },
        "agent": {
            "name": m.get("agent_name") or "", "cove": m.get("cove") or "",
            "avatar_url": m.get("agent_avatar_url") or "", "archetype": m.get("archetype") or "",
            "frequency": m.get("frequency") or "", "tuning_key": m.get("tuning_key") or "",
            "nickname": m.get("nickname") or "", "skills": [],
        },
    }


async def sync_profile_mirror(handle: str):
    """Best-effort: publish a handle's PUBLIC profile to the hub mirror so it's
    resolvable cross-Cove. On the master, write the mirror in-process; from a Cove,
    POST it to the hub. Never raises — a mirror miss must not break the real action."""
    handle = (handle or "").lstrip("@").lower()
    if not handle:
        return
    from src.memory.database import get_db
    try:
        async with get_db() as conn:
            prof = await _assemble(conn, handle)
        if not prof:
            return
        flat = _flatten_for_mirror(prof)
        if _is_master():
            async with get_db() as conn:
                await _mirror_upsert(conn, flat)
        elif HUB_URL and SECRET:
            payload = dict(flat)
            payload["secret"] = SECRET
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(HUB_URL.rstrip("/") + "/api/profile/mirror",
                                  headers={"X-Shared-Secret": SECRET}, json=payload)
    except Exception:
        pass


@router.post("/api/profile/mirror")
async def profile_mirror_upsert(request: Request):
    """Secret-gated: a Cove pushes a presence's public profile to the hub mirror (#173)."""
    body = await request.json()
    supplied = request.headers.get("X-Shared-Secret", "") or (body.get("secret") or "")
    if not (SECRET and supplied and hmac.compare_digest(supplied, SECRET)):
        raise HTTPException(403, "Invalid secret")
    m = {k: (body.get(k) or "") for k in (
        "handle", "display_name", "agent_name", "cove", "archetype",
        "frequency", "tuning_key", "nickname", "avatar_url", "agent_avatar_url", "bio")}
    m["handle"] = m["handle"].lstrip("@").lower()
    if not m["handle"]:
        raise HTTPException(400, "handle is required")
    m["skills"] = body.get("skills") if isinstance(body.get("skills"), list) else []
    m["links"] = body.get("links") if isinstance(body.get("links"), dict) else {}
    from src.memory.database import get_db
    async with get_db() as conn:
        await _mirror_upsert(conn, m)
    return {"ok": True, "handle": m["handle"]}


@router.get("/api/profile/me")
async def my_profile(request: Request):
    presence = await get_current_presence(request)
    if not presence:
        raise HTTPException(401, "Sign in to view your profile")
    from src.memory.database import get_db
    handle = (presence.get("username") or "").lstrip("@").lower()
    async with get_db() as conn:
        prof = await _assemble(conn, handle, include_private=True)
    if not prof:
        raise HTTPException(404, "Profile not found")
    prof["taxonomy"] = SKILLS_TAXONOMY
    return prof


@router.post("/api/profile/me")
async def update_my_profile(request: Request):
    """Update the Presence presentation. Body: avatar_url, agent_avatar_url, bio,
    skills[] (validated against the taxonomy + free tags kept), links{}."""
    presence = await get_current_presence(request)
    if not presence:
        raise HTTPException(401, "Sign in to edit your profile")
    handle = (presence.get("username") or "").lstrip("@").lower()
    body = await request.json()
    # Partial update: only touch fields the caller actually sent, so saving bio/skills
    # never wipes an avatar uploaded separately (and vice-versa).
    cols, vals = [], []
    if "avatar_url" in body:
        cols.append("avatar_url"); vals.append((body.get("avatar_url") or "").strip() or None)
    if "agent_avatar_url" in body:
        cols.append("agent_avatar_url"); vals.append((body.get("agent_avatar_url") or "").strip() or None)
    if "bio" in body:
        cols.append("bio"); vals.append((body.get("bio") or "").strip()[:1000] or None)
    if "skills" in body:
        sk = body.get("skills") if isinstance(body.get("skills"), list) else []
        sk = [str(s).strip()[:40] for s in sk if str(s).strip()][:20]
        cols.append("skills"); vals.append(json.dumps(sk))
    if "links" in body:
        lk = body.get("links") if isinstance(body.get("links"), dict) else {}
        cols.append("links"); vals.append(json.dumps(lk))
    # Identity (#176): the unified Presence editor also edits the operator's display
    # name, which lives on accounts (not presence_profiles) — write it there.
    new_display = None
    if "display_name" in body:
        new_display = (body.get("display_name") or "").strip()[:120] or None
    from src.memory.database import get_db
    async with get_db() as conn:
        if cols:
            collist = ", ".join(cols)
            placeholders = ", ".join(["%s"] * len(cols))
            setlist = ", ".join(f"{c}=EXCLUDED.{c}" for c in cols)
            await conn.execute(
                f"""INSERT INTO presence_profiles (handle, {collist}, updated_at)
                    VALUES (%s, {placeholders}, NOW())
                    ON CONFLICT (handle) DO UPDATE SET {setlist}, updated_at=NOW()""",
                (handle, *vals))
        if new_display:
            await conn.execute(
                "UPDATE accounts SET display_name = %s WHERE lower(username) = %s",
                (new_display, handle))
        prof = await _assemble(conn, handle, include_private=True)
    await sync_profile_mirror(handle)   # keep the hub mirror current (#173)
    return {"ok": True, **(prof or {})}


# ── Avatar storage (#176): avatars must live where the public URL resolves. The hub
#    serves {LP_PUBLIC_BASE}/avatars/{file}; a Cove can't (different host), so a Cove
#    forwards the file to the hub's ingest endpoint and uses the hub URL — same
#    centralization as the profile mirror, so avatars load cross-Cove. ──
async def _store_avatar(handle: str, kind: str, ext: str, data: bytes) -> str:
    fname = f"{handle}-{kind}.{ext}"
    if _is_master():
        pathlib.Path(AVATAR_DIR).mkdir(parents=True, exist_ok=True)
        pathlib.Path(AVATAR_DIR, fname).write_bytes(data)
        return f"{LP_PUBLIC_BASE}/avatars/{fname}"
    if HUB_URL and SECRET:
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.post(
                    HUB_URL.rstrip("/") + "/api/profile/avatar-ingest",
                    headers={"X-Shared-Secret": SECRET},
                    data={"handle": handle, "kind": kind, "ext": ext},
                    files={"file": (fname, data)})
            if r.status_code == 200:
                u = r.json().get("url")
                if u:
                    return u
        except Exception:
            pass
    # Fallback: store locally and return a RELATIVE url so it loads from THIS Cove's own
    # origin (the /avatars/{file} route serves it). The hub URL would 404 here because the
    # file lives on the Cove, not the hub. Cross-Cove still needs the hub forward above;
    # this at least makes the operator's own avatars load on their own Cove (keyless self-host).
    pathlib.Path(AVATAR_DIR).mkdir(parents=True, exist_ok=True)
    pathlib.Path(AVATAR_DIR, fname).write_bytes(data)
    return f"/avatars/{fname}"


@router.post("/api/profile/avatar-ingest")
async def avatar_ingest(request: Request, handle: str = Form(...), kind: str = Form("operator"),
                        ext: str = Form("png"), file: UploadFile = File(...)):
    """Secret-gated: a Cove stores a presence's avatar on the hub so the public URL
    resolves everywhere (#176). Returns the hub-served URL."""
    supplied = request.headers.get("X-Shared-Secret", "")
    if not (SECRET and supplied and hmac.compare_digest(supplied, SECRET)):
        raise HTTPException(403, "Invalid secret")
    handle = (handle or "").lstrip("@").lower()
    if not handle:
        raise HTTPException(400, "handle required")
    kind = "agent" if kind == "agent" else "operator"
    ext = ext if ext in set(_IMG_EXT.values()) else "png"
    data = await file.read()
    if len(data) > 2_000_000:
        raise HTTPException(400, "Image too large (max 2MB)")
    pathlib.Path(AVATAR_DIR).mkdir(parents=True, exist_ok=True)
    fname = f"{handle}-{kind}.{ext}"
    pathlib.Path(AVATAR_DIR, fname).write_bytes(data)
    return {"ok": True, "url": f"{LP_PUBLIC_BASE}/avatars/{fname}"}


async def _store_image(fname: str, data: bytes) -> str:
    """Generic image storage (product/listing images) where the public URL resolves:
    the hub writes its volume; a Cove forwards to the hub so {LP_PUBLIC_BASE}/avatars/
    {fname} resolves everywhere (#175/#176). Same pattern as avatars."""
    if _is_master():
        pathlib.Path(AVATAR_DIR).mkdir(parents=True, exist_ok=True)
        pathlib.Path(AVATAR_DIR, fname).write_bytes(data)
        return f"{LP_PUBLIC_BASE}/avatars/{fname}"
    if HUB_URL and SECRET:
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.post(
                    HUB_URL.rstrip("/") + "/api/profile/image-ingest",
                    headers={"X-Shared-Secret": SECRET},
                    data={"fname": fname}, files={"file": (fname, data)})
            if r.status_code == 200:
                u = r.json().get("url")
                if u:
                    return u
        except Exception:
            pass
    # Fallback: store locally and return a RELATIVE url so it loads from THIS Cove's own
    # origin (the hub URL would 404 — the file is local, not on the hub).
    pathlib.Path(AVATAR_DIR).mkdir(parents=True, exist_ok=True)
    pathlib.Path(AVATAR_DIR, fname).write_bytes(data)
    return f"/avatars/{fname}"


@router.post("/api/profile/image-ingest")
async def image_ingest(request: Request, fname: str = Form(...), file: UploadFile = File(...)):
    """Secret-gated: a Cove stores a product/listing image on the hub so the URL
    resolves everywhere (#175). Returns the hub-served URL."""
    supplied = request.headers.get("X-Shared-Secret", "")
    if not (SECRET and supplied and hmac.compare_digest(supplied, SECRET)):
        raise HTTPException(403, "Invalid secret")
    safe = pathlib.Path(fname or "").name   # strip path traversal
    if not safe or "." not in safe or safe.rsplit(".", 1)[-1].lower() not in set(_IMG_EXT.values()):
        raise HTTPException(400, "bad image filename")
    data = await file.read()
    if len(data) > 2_000_000:
        raise HTTPException(400, "Image too large")
    pathlib.Path(AVATAR_DIR).mkdir(parents=True, exist_ok=True)
    pathlib.Path(AVATAR_DIR, safe).write_bytes(data)
    return {"ok": True, "url": f"{LP_PUBLIC_BASE}/avatars/{safe}"}


@router.post("/api/profile/avatar")
async def upload_avatar(request: Request, kind: str = "operator", file: UploadFile = File(...)):
    """Upload a profile pic (operator) or agent avatar. Saved to a hub volume, served
    publicly at {LP_PUBLIC_BASE}/avatars/{file}; the URL is persisted on the profile."""
    presence = await get_current_presence(request)
    if not presence:
        raise HTTPException(401, "Sign in to upload")
    handle = (presence.get("username") or "").lstrip("@").lower()
    if not handle:
        raise HTTPException(400, "No handle on this account")
    kind = "agent" if kind == "agent" else "operator"
    ext = _IMG_EXT.get(file.content_type or "")
    if not ext:
        raise HTTPException(400, "Upload a PNG, JPG, WEBP, or GIF")
    data = await file.read()
    if len(data) > 2_000_000:
        raise HTTPException(400, "Image too large (max 2MB)")
    url = await _store_avatar(handle, kind, ext, data)   # hub-centralized (#176)
    col = "avatar_url" if kind == "operator" else "agent_avatar_url"
    from src.memory.database import get_db
    async with get_db() as conn:
        await conn.execute(
            f"""INSERT INTO presence_profiles (handle, {col}, updated_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (handle) DO UPDATE SET {col}=EXCLUDED.{col}, updated_at=NOW()""",
            (handle, url))
    await sync_profile_mirror(handle)   # keep the hub mirror current (#173)
    return {"ok": True, "url": url, "kind": kind}


@router.post("/api/profile/image")
async def upload_image(request: Request, file: UploadFile = File(...)):
    """Generic image upload for product/listing thumbnails (≤1MB). Saved to the same
    public hub volume as avatars; returns the URL to store as the listing's image_url.
    Does NOT write the profile — the caller attaches the URL where it needs it."""
    presence = await get_current_presence(request)
    if not presence:
        raise HTTPException(401, "Sign in to upload")
    handle = (presence.get("username") or "").lstrip("@").lower() or "anon"
    ext = _IMG_EXT.get(file.content_type or "")
    if not ext:
        raise HTTPException(400, "Upload a PNG, JPG, WEBP, or GIF")
    data = await file.read()
    if len(data) > 1_000_000:
        raise HTTPException(400, "Image too large (max 1MB)")
    import uuid as _uuid
    fname = f"img-{handle}-{_uuid.uuid4().hex[:10]}.{ext}"
    url = await _store_image(fname, data)   # hub-centralized so it resolves cross-Cove
    return {"ok": True, "url": url}


@router.get("/avatars/{filename}")
async def serve_avatar(filename: str):
    """Public avatar serving (allowlisted) — avatars are public presentation, and load
    cross-domain from any Cove MC via the absolute hub URL."""
    safe = pathlib.Path(filename).name  # strip any path traversal
    fp = pathlib.Path(AVATAR_DIR, safe)
    if not fp.is_file():
        raise HTTPException(404, "Not found")
    return FileResponse(str(fp), headers={"Cache-Control": "public, max-age=300"})


# ── Agent persona editor (CF-29 detail+edit) ────────────────────────────────
# The identity spine is set at spark and never editable here; changing WHO the
# agent is means a new agent, not an edit.
_LOCKED_PERSONA_KEYS = ("archetype", "frequency", "frequency_color", "tuning_key")


def _clean_persona(body):
    """Whitelist + clamp a persona partial update. Returns (updates, error).
    Only keys actually sent land in updates; locked identity keys are an error."""
    if not isinstance(body, dict):
        return None, "Body must be a JSON object"
    locked = [k for k in _LOCKED_PERSONA_KEYS if k in body]
    if locked:
        return None, "Locked identity keys cannot be edited here: " + ", ".join(locked)
    updates = {}
    if "nickname" in body:
        updates["nickname"] = str(body.get("nickname") or "").strip()[:60]
    if "shade" in body:
        # "" is meaningful: it removes the shade.
        updates["shade"] = str(body.get("shade") or "").strip()[:80]
    if "avatar" in body:
        updates["avatar"] = str(body.get("avatar") or "").strip()[:300]
    if "personality" in body:
        raw = body.get("personality")
        dials = {}
        if isinstance(raw, dict):
            for k, v in raw.items():
                key = re.sub(r"[^a-z0-9_-]", "", str(k).strip().lower().replace(" ", "_"))[:24]
                if not key:
                    continue
                try:
                    iv = int(v)
                except (TypeError, ValueError):
                    continue
                dials[key] = max(0, min(100, iv))
                if len(dials) >= 12:
                    break
        updates["personality"] = dials
    if "lens" in body:
        raw = body.get("lens") if isinstance(body.get("lens"), dict) else {}
        lens = {}
        if "chips" in raw:
            ch = raw.get("chips") if isinstance(raw.get("chips"), list) else []
            lens["chips"] = [str(c).strip()[:40] for c in ch if str(c).strip()][:8]
        if "statement" in raw:
            lens["statement"] = str(raw.get("statement") or "").strip()[:280]
        if "standing_preferences" in raw:
            sp = raw.get("standing_preferences") if isinstance(raw.get("standing_preferences"), list) else []
            lens["standing_preferences"] = [str(s).strip()[:120] for s in sp if str(s).strip()][:8]
        updates["lens"] = lens
    return updates, None


@router.post("/api/profile/{handle}/persona")
async def update_persona(handle: str, request: Request):
    """Edit the editable persona of a Presence's agent: nickname, shade, avatar,
    personality dials, lens. Gate: the handle's owner or a Cove admin. The identity
    spine (archetype, frequency, frequency_color, tuning_key) is locked — 400."""
    presence = await get_current_presence(request)
    if not presence:
        raise HTTPException(401, "Sign in to edit a persona")
    h = handle.lstrip("@").lower()
    caller = (presence.get("username") or "").lstrip("@").lower()
    if not (caller == h or presence.get("cove_role") == "admin"):
        raise HTTPException(403, "Only the owner or a Cove admin can edit this persona")
    body = await request.json()
    updates, err = _clean_persona(body)
    if err:
        raise HTTPException(400, err)
    if not updates:
        raise HTTPException(400, "Nothing to update")
    from src.memory.database import get_db
    async with get_db() as conn:
        r = await conn.execute(
            "SELECT agent_identity FROM accounts WHERE lower(username) = %s AND active = TRUE", (h,))
        acct = await r.fetchone()
        if not acct:
            raise HTTPException(404, f"No Presence '@{h}'")
        raw = acct.get("agent_identity")
        try:
            ai = raw if isinstance(raw, dict) else (json.loads(raw) if raw else {})
        except Exception:
            ai = {}
        ai = ai or {}
        # Merge only sent keys; personality/lens sub-merge so a partial body never
        # wipes dials or lens fields the caller didn't send.
        for k, v in updates.items():
            if k in ("personality", "lens"):
                base = ai.get(k) if isinstance(ai.get(k), dict) else {}
                ai[k] = {**base, **v}
            else:
                ai[k] = v
        await conn.execute(
            "UPDATE accounts SET agent_identity = %s WHERE lower(username) = %s",
            (json.dumps(ai), h))
        prof = await _assemble(conn, h, include_private=True)
    await sync_profile_mirror(h)   # nickname rides the public mirror (#173)
    return {"ok": True, **(prof or {})}


@router.get("/api/profile/{handle}")
async def public_profile(handle: str, request: Request):
    """A Presence's public profile — what a product card links to. Resolves cross-Cove
    (#173): a Cove asks the hub (where the mirror lives); the hub resolves its own
    accounts first, then the mirror for presences that live on another Cove."""
    if handle.lower() == "me":  # /me is handled above; guard the path overlap
        raise HTTPException(404, "Not found")
    h = handle.lstrip("@").lower()

    # On a Cove, the cross-Cove identity lives on the hub — ask it first.
    if not _is_master() and HUB_URL and SECRET:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(HUB_URL.rstrip("/") + f"/api/profile/{h}",
                                     headers={"X-Shared-Secret": SECRET})
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass  # fall back to local resolution below

    from src.memory.database import get_db
    from src.dashboard.routes.presence import get_current_presence
    _viewer = await get_current_presence(request)
    _priv = bool(_viewer and ((_viewer.get("cove_role") == "admin")
                              or (_viewer.get("username") or "").lstrip("@").lower() == h))
    async with get_db() as conn:
        prof = await _assemble(conn, h, include_private=_priv)
        if not prof:
            prof = await _mirror_get(conn, h)   # cross-Cove seller mirrored to the hub
            if prof:
                prof["offerings"] = await _offerings_for(h)
    if not prof:
        raise HTTPException(404, f"No Presence '@{h}'")
    return prof
