"""Tuning favorites — CRUD for favorited echoes."""

import json as _json
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .helpers import _get_presence_id

router = APIRouter()


@router.get("/api/tuning/favorites")
async def get_favorites(request: Request):
    """Get the user's favorited echoes."""
    from src.memory.database import get_db

    presence_id = await _get_presence_id(request)
    if not presence_id:
        return {"favorites": []}

    try:
        async with get_db() as conn:
            result = await conn.execute(
                "SELECT favorites_json FROM tuning_favorites WHERE presence_id = %s",
                (presence_id,),
            )
            row = await result.fetchone()
            if row and row["favorites_json"]:
                favs = row["favorites_json"] if isinstance(row["favorites_json"], list) else _json.loads(row["favorites_json"])
                return {"favorites": favs}
            return {"favorites": []}
    except Exception:
        return {"favorites": []}


@router.post("/api/tuning/favorites")
async def add_favorite(request: Request):
    """Add an echo to favorites. Body: { filename, folder, principle, frequency }"""
    from src.memory.database import get_db

    presence_id = await _get_presence_id(request)
    if not presence_id:
        return JSONResponse(status_code=401, content={"error": "not authenticated"})

    body = await request.json()
    echo = {
        "filename": body.get("filename", ""),
        "folder": body.get("folder", ""),
        "principle": body.get("principle", ""),
        "frequency": body.get("frequency", ""),
        "added_at": datetime.now().isoformat(),
    }

    try:
        async with get_db() as conn:
            result = await conn.execute(
                "SELECT favorites_json FROM tuning_favorites WHERE presence_id = %s",
                (presence_id,),
            )
            row = await result.fetchone()

            if row:
                favs = row["favorites_json"] if isinstance(row["favorites_json"], list) else _json.loads(row["favorites_json"])
                # Dedupe by filename
                if not any(f.get("filename") == echo["filename"] for f in favs):
                    favs.append(echo)
                    await conn.execute(
                        "UPDATE tuning_favorites SET favorites_json = %s, updated_at = NOW() WHERE presence_id = %s",
                        (_json.dumps(favs), presence_id),
                    )
            else:
                await conn.execute(
                    "INSERT INTO tuning_favorites (presence_id, favorites_json) VALUES (%s, %s)",
                    (presence_id, _json.dumps([echo])),
                )

        return {"ok": True, "echo": echo}
    except Exception as e:
        return {"error": str(e)}


@router.delete("/api/tuning/favorites/{filename}")
async def remove_favorite(request: Request, filename: str):
    """Remove an echo from favorites by filename."""
    from src.memory.database import get_db

    presence_id = await _get_presence_id(request)
    if not presence_id:
        return JSONResponse(status_code=401, content={"error": "not authenticated"})

    try:
        async with get_db() as conn:
            result = await conn.execute(
                "SELECT favorites_json FROM tuning_favorites WHERE presence_id = %s",
                (presence_id,),
            )
            row = await result.fetchone()
            if row:
                favs = row["favorites_json"] if isinstance(row["favorites_json"], list) else _json.loads(row["favorites_json"])
                favs = [f for f in favs if f.get("filename") != filename]
                await conn.execute(
                    "UPDATE tuning_favorites SET favorites_json = %s, updated_at = NOW() WHERE presence_id = %s",
                    (_json.dumps(favs), presence_id),
                )
            return {"ok": True}
    except Exception as e:
        return {"error": str(e)}
