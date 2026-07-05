"""
Canon routes — serve principle lyrics from the Lucid Canon.

Reads lucid-canon.md from the vault mount and parses lyrics per principle.
"""
import os
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()

# Canon is hub-owned and synced into each Cove (#135). Resolve at read-time so a KB
# sync that lands after startup is picked up; the repo-bundled copy is a fallback.
from src.knowledge.kb_paths import resolve_kb_file

_cache = {}


def _parse_canon():
    """Parse the Canon markdown into {principle_key: {name, lyrics, key_lyric, stage}}.
    Cached, but busts when the source file path or mtime changes (#135) so a Canon
    edit or a fresh KB sync is reflected without a restart."""
    canon_path = resolve_kb_file("lucid-canon.md")
    if not canon_path.exists():
        return {}

    try:
        mtime = canon_path.stat().st_mtime
    except OSError:
        mtime = None

    if (_cache.get("parsed")
            and _cache.get("path") == str(canon_path)
            and _cache.get("mtime") == mtime):
        return _cache["parsed"]

    text = canon_path.read_text(encoding="utf-8")
    principles = {}
    current = None
    in_lyrics = False
    lyrics_lines = []

    for line in text.split("\n"):
        # New principle header: ## PRINCIPLE NAME
        if line.startswith("## ") and not line.startswith("## PRINCIPLES"):
            # Save previous
            if current and lyrics_lines:
                principles[current["key"]]["lyrics"] = "\n".join(lyrics_lines).strip()
            current_name = line[3:].strip()
            key = current_name.lower().replace(" ", "_")
            current = {"name": current_name, "key": key}
            principles[key] = {"name": current_name, "lyrics": "", "key_lyric": "", "stage": ""}
            in_lyrics = False
            lyrics_lines = []
        elif current:
            stripped = line.strip()
            if stripped.startswith("**STAGE:**"):
                principles[current["key"]]["stage"] = stripped.replace("**STAGE:**", "").strip()
            elif stripped.startswith("**KEY_LYRIC:**"):
                principles[current["key"]]["key_lyric"] = stripped.replace("**KEY_LYRIC:**", "").strip().strip('"')
            elif stripped.startswith("### FULL_LYRICS:"):
                in_lyrics = True
            elif in_lyrics:
                # Stop at next ## or ### that isn't lyrics content
                if line.startswith("## "):
                    in_lyrics = False
                    if lyrics_lines:
                        principles[current["key"]]["lyrics"] = "\n".join(lyrics_lines).strip()
                    current_name = line[3:].strip()
                    key = current_name.lower().replace(" ", "_")
                    current = {"name": current_name, "key": key}
                    principles[key] = {"name": current_name, "lyrics": "", "key_lyric": "", "stage": ""}
                    lyrics_lines = []
                else:
                    lyrics_lines.append(line)

    # Save last principle
    if current and lyrics_lines:
        principles[current["key"]]["lyrics"] = "\n".join(lyrics_lines).strip()

    _cache["parsed"] = principles
    _cache["path"] = str(canon_path)
    _cache["mtime"] = mtime
    return principles


def _principle_to_key(principle: str) -> str:
    return principle.strip().lower().replace(" ", "_")


@router.get("/api/canon/{principle:path}")
async def get_canon_lyrics(principle: str):
    """Get lyrics for a specific principle. Accepts principle name or key."""
    key = _principle_to_key(principle)

    canon = _parse_canon()
    if key not in canon:
        return JSONResponse({"found": False, "error": f"Unknown principle: {principle}"})

    entry = canon[key]
    return JSONResponse({
        "found": True,
        "principle": entry["name"],
        "key_lyric": entry["key_lyric"],
        "stage": entry["stage"],
        "lyrics": entry["lyrics"],
    })


@router.get("/api/canon")
async def list_canon():
    """List all principles with names and stages (no lyrics)."""
    canon = _parse_canon()
    items = [{"key": k, "name": v["name"], "stage": v["stage"], "key_lyric": v["key_lyric"]}
             for k, v in canon.items()]
    return JSONResponse({"count": len(items), "principles": items})
