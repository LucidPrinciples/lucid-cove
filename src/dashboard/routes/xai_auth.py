"""
xAI Grok OAuth2 authorization routes — device-code flow for Settings UI.

Endpoints:
  - /api/xai/auth/start      → Start device-code flow, return user_code + verification_uri
  - /api/xai/auth/poll       → Poll for completion (called by UI)
  - /api/xai/auth/status     → Check current authorization status
  - /api/xai/auth/revoke     → Revoke/clear tokens

Used by Settings page to authorize xAI Grok models.
"""

import time
from fastapi import APIRouter, HTTPException

from src.models import xai_oauth

router = APIRouter()

# In-memory store for active device-code flows
_active_flows: dict[str, dict] = {}


@router.post("/api/xai/auth/start")
async def xai_auth_start():
    """Start xAI OAuth device-code flow."""
    try:
        flow = await xai_oauth.start_device_code_flow()
        
        _active_flows["xai"] = {
            "device_code": flow["device_code"],
            "expires_at": time.time() + flow["expires_in"],
            "interval": flow.get("interval", 5),
        }
        
        return {
            "status": "pending",
            "user_code": flow["user_code"],
            "verification_uri": flow["verification_uri"],
            "expires_in": flow["expires_in"],
            "message": f"Go to {flow['verification_uri']} and enter code: {flow['user_code']}",
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start OAuth flow: {e}")


@router.post("/api/xai/auth/poll")
async def xai_auth_poll():
    """Poll for OAuth completion."""
    # Check if already authorized
    try:
        token = await xai_oauth.get_valid_access_token()
        _active_flows.pop("xai", None)
        return {"status": "authorized", "message": "Already authorized"}
    except ValueError:
        pass
    
    flow = _active_flows.get("xai")
    if not flow:
        return {"status": "no_flow", "message": "No active flow. Call /start first."}
    
    if time.time() > flow["expires_at"]:
        _active_flows.pop("xai", None)
        raise HTTPException(status_code=400, detail="Flow expired. Start new flow.")
    
    try:
        tokens = await xai_oauth.poll_for_token(flow["device_code"])
        if tokens:
            _active_flows.pop("xai", None)
            return {"status": "authorized", "message": "Authorization successful"}
        else:
            return {"status": "pending", "message": "Still pending. Complete in browser."}
    except RuntimeError as e:
        _active_flows.pop("xai", None)
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/xai/auth/status")
async def xai_auth_status():
    """Check xAI authorization status."""
    try:
        tokens = xai_oauth._load_cached_tokens()
        if not tokens:
            return {"authorized": False, "message": "Not authorized"}
        
        try:
            access_token = await xai_oauth.get_valid_access_token()
            return {
                "authorized": True,
                "has_refresh_token": bool(tokens.get("refresh_token")),
                "message": "Authorized",
            }
        except ValueError as e:
            return {"authorized": False, "needs_reauth": True, "message": str(e)}
    except Exception as e:
        return {"authorized": False, "error": str(e)}


@router.post("/api/xai/auth/revoke")
async def xai_auth_revoke():
    """Revoke xAI authorization."""
    await xai_oauth.revoke_tokens()
    _active_flows.pop("xai", None)
    return {"status": "revoked", "message": "xAI authorization revoked"}
