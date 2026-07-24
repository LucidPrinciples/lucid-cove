"""
Posting Accounts — per-presence social posting credentials.

Surfaced on the Video Pipeline page. Each Presence manages its OWN posting
accounts; credentials live in that presence's account preferences (X) or the
namespaced oauth_tokens row (YouTube), never in git. Admin/Cove-wide only for
the YouTube OAuth *app* creds (redirect_uri is domain-level).

  GET  /api/posting/accounts   — status for the current presence (no secrets)
  POST /api/posting/x          — save this presence's 4 X keys
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.env import env
from src.dashboard.routes.posting_identity import (
    owner_id_from_request, resolve_x_creds, save_posting_section, X_ENV_KEYS,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _youtube_app_configured() -> bool:
    """Whether the Cove's YouTube OAuth app creds (id + redirect) are set."""
    try:
        from src.config import get_feature_flags
        f = get_feature_flags()
    except Exception:
        f = {}
    cid = f.get("youtube_client_id") or env("YOUTUBE_CLIENT_ID")
    redirect = f.get("youtube_redirect_uri") or env("YOUTUBE_REDIRECT_URI")
    secret = f.get("youtube_client_secret") or env("YOUTUBE_CLIENT_SECRET")
    return bool(cid and redirect and secret)


@router.get("/api/posting/accounts")
async def posting_accounts(request: Request):
    """Posting-account status for the current presence. Never returns secrets."""
    owner_id = await owner_id_from_request(request)
    x_creds, _ = await resolve_x_creds(request=request)

    # YouTube: do we have a per-presence token?
    yt_connected = False
    yt_channel = None
    try:
        from src.dashboard.routes.posting_identity import yt_service_key
        from src.dashboard.routes.youtube_auth import _get_tokens
        toks = await _get_tokens(yt_service_key(owner_id))
        yt_connected = bool(toks and toks.get("refresh_token"))
    except Exception:
        pass

    # X caption ceiling (Premium long-post vs free 280)
    from src.dashboard.routes.x_posting import resolve_x_max_chars_for
    x_max = await resolve_x_max_chars_for(request=request, owner_id=owner_id)
    x_long = False
    try:
        from src.dashboard.routes.posting_identity import _account_prefs
        prefs = await _account_prefs(owner_id) if owner_id else {}
        x_sec = ((prefs or {}).get("posting") or {}).get("x") or {}
        from src.dashboard.routes.x_posting import _truthy
        from src.env import env_bool
        x_long = _truthy(x_sec.get("long_posts")) or env_bool("X_LONG_POSTS", "false")
    except Exception:
        x_long = x_max > 280

    return {
        "owner_id": owner_id,
        "x": {
            "configured": x_creds is not None,
            "long_posts": bool(x_long),
            "max_chars": int(x_max),
        },
        "youtube": {
            "connected": yt_connected,
            "channel": yt_channel,
            "app_configured": _youtube_app_configured(),
        },
    }


class XKeys(BaseModel):
    api_key: str = ""
    api_secret: str = ""
    access_token: str = ""
    access_token_secret: str = ""
    # Premium long-form posts (up to 25k). Optional — omit to leave unchanged.
    long_posts: bool | None = None


@router.post("/api/posting/x")
async def save_x(keys: XKeys, request: Request):
    """Save the current presence's X (Twitter) OAuth 1.0a keys + long-post flag."""
    owner_id = await owner_id_from_request(request)
    if not owner_id:
        return JSONResponse(status_code=400, content={
            "error": "No presence in context — sign in as the presence whose account this is."})

    data = {k: (getattr(keys, k) or "").strip() for k in X_ENV_KEYS}
    # Ignore masked echoes so re-saving the form never wipes a stored key.
    data = {k: v for k, v in data.items() if v and v != "********"}
    if keys.long_posts is not None:
        data["long_posts"] = bool(keys.long_posts)
    if not data:
        return JSONResponse(status_code=400, content={
            "error": "No keys or settings provided."})

    ok = await save_posting_section(owner_id, "x", data)
    if not ok:
        return JSONResponse(status_code=500, content={"error": "Could not save X keys."})

    x_creds, _ = await resolve_x_creds(owner_id=owner_id)
    from src.dashboard.routes.x_posting import resolve_x_max_chars_for
    x_max = await resolve_x_max_chars_for(owner_id=owner_id)
    return {
        "ok": True,
        "configured": x_creds is not None,
        "long_posts": bool(keys.long_posts) if keys.long_posts is not None else None,
        "max_chars": int(x_max),
    }


class YouTubeClient(BaseModel):
    client_id: str = ""
    client_secret: str = ""
    redirect_uri: str = ""


@router.post("/api/posting/youtube/client")
async def save_youtube_client(creds: YouTubeClient, request: Request):
    """Save the Cove-wide YouTube OAuth *app* creds (one Google app per Cove).

    The redirect_uri is domain-level, so these are Cove-wide (feature overrides),
    not per-presence. Each presence then connects their own channel via OAuth.
    """
    data = {}
    if creds.client_id.strip():
        data["youtube_client_id"] = creds.client_id.strip()
    if creds.client_secret.strip() and creds.client_secret.strip() != "********":
        data["youtube_client_secret"] = creds.client_secret.strip()
    if creds.redirect_uri.strip():
        data["youtube_redirect_uri"] = creds.redirect_uri.strip()
    if not data:
        return JSONResponse(status_code=400, content={"error": "No values provided."})

    from src.config import save_feature_overrides
    if not save_feature_overrides(data):
        return JSONResponse(status_code=500, content={"error": "Could not save YouTube app creds."})
    return {"ok": True, "app_configured": _youtube_app_configured()}


# ── Video description / brand profile (empty by default) ─────────────

class VideoMetaBody(BaseModel):
    brand_name: str = ""
    brand_topics: str = ""
    theme_mix: str = ""
    short_cta_url: str = ""
    short_cta_line: str = ""
    full_cta_url: str = ""
    full_cta_line: str = ""
    hashtag_seeds: str = ""
    description_extra: str = ""
    voice_notes: str = ""


@router.get("/api/posting/video-meta")
async def get_video_meta(request: Request):
    """Effective + layer video_meta for the current presence.

    Returns presence, cove, and merged effective profiles. Empty strings are
    the product default (hardware-store Cove has no Lucid Tuner links).
    """
    from src.dashboard.routes.video_meta import (
        empty_video_meta,
        get_cove_video_meta,
        get_presence_video_meta,
        merge_video_meta,
        VIDEO_META_FIELDS,
        VIDEO_META_FIELD_META,
    )
    owner_id = await owner_id_from_request(request)
    presence = await get_presence_video_meta(owner_id)
    cove = get_cove_video_meta()
    effective = merge_video_meta(presence, cove)
    can_edit_cove = False
    try:
        from src.dashboard.routes.settings import _is_admin_presence
        can_edit_cove = bool(await _is_admin_presence(request))
    except Exception:
        pass
    return {
        "owner_id": owner_id,
        "fields": list(VIDEO_META_FIELDS),
        "field_meta": VIDEO_META_FIELD_META,
        "presence": presence,
        "cove": cove,
        "effective": effective,
        "empty": empty_video_meta(),
        "can_edit_cove": can_edit_cove,
    }


@router.put("/api/posting/video-meta")
async def put_presence_video_meta(body: VideoMetaBody, request: Request):
    """Save this presence's video metadata profile (overrides Cove per field)."""
    from src.dashboard.routes.video_meta import save_presence_video_meta, get_presence_video_meta
    owner_id = await owner_id_from_request(request)
    if not owner_id:
        return JSONResponse(
            status_code=400,
            content={"error": "No presence in context — sign in as the presence this profile belongs to."},
        )
    data = body.model_dump() if hasattr(body, "model_dump") else body.dict()
    ok = await save_presence_video_meta(owner_id, data)
    if not ok:
        return JSONResponse(status_code=500, content={"error": "Could not save video meta."})
    return {"ok": True, "presence": await get_presence_video_meta(owner_id)}


@router.put("/api/posting/video-meta/cove")
async def put_cove_video_meta(body: VideoMetaBody, request: Request):
    """Save Cove-wide defaults (admin). Used when a presence field is empty."""
    from src.dashboard.routes.settings import _is_admin_presence
    if not await _is_admin_presence(request):
        return JSONResponse(status_code=403, content={"error": "Admin only."})
    from src.dashboard.routes.video_meta import save_cove_video_meta, get_cove_video_meta
    data = body.model_dump() if hasattr(body, "model_dump") else body.dict()
    if not save_cove_video_meta(data):
        return JSONResponse(status_code=500, content={"error": "Could not save Cove video meta."})
    return {"ok": True, "cove": get_cove_video_meta()}
