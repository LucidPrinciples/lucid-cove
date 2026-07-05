# =============================================================================
# compute.py — CF-96: GET /api/compute/status (the one compute-state read API).
# =============================================================================
# Thin route over src.compute_status.compute_status(). Admin-gated like the
# other compute editors (set_compute lives in settings.py). Surfaces call this
# instead of re-deriving readiness from raw yaml/env. The token is never in the
# resolver output, so nothing here can leak a GPU-rent grant.
# =============================================================================
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/api/compute/status")
async def get_compute_status(request: Request):
    """One state for every compute surface: chooser, Settings, Rent GPU,
    Pipeline Services, and the video pipeline gating all read this."""
    from src.dashboard.routes.settings import _is_admin_presence
    if not await _is_admin_presence(request):
        return JSONResponse(status_code=403, content={"error": "Admin Presence only"})
    from src.compute_status import compute_status
    return {"ok": True, **compute_status()}
