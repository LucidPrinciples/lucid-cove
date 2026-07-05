"""
Content Mirrors — complementary passages from other canons mapped to LP tuning combinations.

A mirror maps Principle × Frequency × Signal Type to passages in another canon.
The tuning engine picks the daily combination; the mirror reflects complementary
content through the creator's lens.

YAML format (combo keying):
  principle_key:
    frequency_key:
      signal_key:
        ref: "Book chapter:verse"
        text: "Passage text"
        thread: "Connection back to LP concept"

Mirror data: /cove-core/data/mirrors/*.yaml
Active mirror: ACTIVE_MIRROR env var (default: scripture-tpt)
"""

import json
import os
from src.env import env
import yaml
from pathlib import Path
from typing import Optional

from fastapi import APIRouter

from src.config import resolve_mirror_id as _resolve_mirror_id

router = APIRouter()

# Mirror data directories — check published (writable) first, then cove-core (read-only)
_PUBLISHED_DIR = Path("/app/data/mirrors/published")
_COVECORE_MIRRORS = Path("/cove-core/data/mirrors")
if not _COVECORE_MIRRORS.exists():
    _COVECORE_MIRRORS = Path(__file__).parent.parent.parent.parent / "data" / "mirrors"
if not _PUBLISHED_DIR.exists():
    _PUBLISHED_DIR = _COVECORE_MIRRORS  # fallback for local dev

_mirror_cache: dict = {}


def _to_key(name: str) -> str:
    """Convert display name to YAML key: 'Valley of Shadows' -> 'valley_of_shadows'."""
    return name.strip().lower().replace(" ", "_")


def _signal_to_key(signal_type: str) -> str:
    """Convert signal type to YAML key: 'Bright_Signal' -> 'bright'."""
    return signal_type.strip().lower().replace("_signal", "").replace(" signal", "").replace(" ", "_")


def _load_mirror(mirror_id: str) -> Optional[dict]:
    """Load and cache a mirror YAML file.

    Checks published mirrors first (writable, from Creation Flow),
    then cove-core mirrors (read-only, shipped with system).
    """
    if mirror_id in _mirror_cache:
        return _mirror_cache[mirror_id]

    # Check published dir first, then cove-core
    for search_dir in [_PUBLISHED_DIR, _COVECORE_MIRRORS]:
        mirror_path = search_dir / f"{mirror_id}.yaml"
        if mirror_path.exists():
            try:
                with open(mirror_path, "r") as f:
                    data = yaml.safe_load(f) or {}
                _mirror_cache[mirror_id] = data
                return data
            except Exception as e:
                print(f"[mirrors] Failed to load {mirror_id} from {search_dir}: {e}")

    return None


def _get_active_mirror() -> str:
    """Get the active mirror ID via cascade resolver."""
    return _resolve_mirror_id()


def _collect_all_entries(principle_data: dict) -> list:
    """Gather all entries from a principle's nested freq/signal structure."""
    entries = []
    for freq_key, freq_data in principle_data.items():
        if isinstance(freq_data, dict):
            for sig_key, entry in freq_data.items():
                if isinstance(entry, dict) and "ref" in entry:
                    entries.append(entry)
    return entries


def get_mirror_entry(
    principle: str,
    frequency: Optional[str] = None,
    signal_type: Optional[str] = None,
    mirror_id: Optional[str] = None,
) -> Optional[dict]:
    """Look up mirror entries for a tuning combination.

    Lookup order:
    1. Exact match: principle → frequency → signal_type (featured)
    2. Frequency match: principle → frequency → all signals
    3. Any match: principle → all frequencies → all signals

    Returns dict with mirror_name, canon, featured entry, and all entries for Reflect view.
    """
    mid = mirror_id or _get_active_mirror()
    data = _load_mirror(mid)
    if not data:
        return None

    p_key = _to_key(principle)
    principle_data = data.get(p_key)
    if not principle_data or not isinstance(principle_data, dict):
        return None

    meta = data.get("meta", {})
    featured = None
    all_entries = []

    # Try exact combo: principle → frequency → signal
    if frequency and signal_type:
        f_key = _to_key(frequency)
        s_key = _signal_to_key(signal_type)

        freq_data = principle_data.get(f_key)
        if isinstance(freq_data, dict):
            entry = freq_data.get(s_key)
            if entry and isinstance(entry, dict) and "ref" in entry:
                featured = entry

            # All entries for this frequency (Reflect view)
            all_entries = [
                v for k, v in freq_data.items()
                if isinstance(v, dict) and "ref" in v
            ]

    # Fallback: no frequency match — gather everything for this principle
    if not all_entries:
        all_entries = _collect_all_entries(principle_data)

    if not featured and all_entries:
        featured = all_entries[0]

    if not featured:
        return None

    # Build entry dicts — pass through extra fields for music mirrors (artist, title)
    def _entry_dict(e):
        d = {"ref": e.get("ref", ""), "text": e.get("text", ""), "thread": e.get("thread", "")}
        if e.get("artist"):
            d["artist"] = e["artist"]
        if e.get("title"):
            d["title"] = e["title"]
        if e.get("spotify_id"):
            d["spotify_id"] = e["spotify_id"]
        if e.get("youtube_id"):
            d["youtube_id"] = e["youtube_id"]
        return d

    return {
        "mirror_id": mid,
        "mirror_name": meta.get("name", mid),
        "mirror_type": meta.get("type", "text"),
        "canon": meta.get("canon", ""),
        "principle_key": p_key,
        "featured": _entry_dict(featured),
        "entries": [_entry_dict(e) for e in all_entries],
    }


def _parse_mirror_sources(sources_param: Optional[str] = None) -> list:
    """Parse enabled mirror IDs from query param or config.

    Frontend passes mirror_sources from MC.features (which already
    includes per-user DB preferences in multi mode).
    Falls back to config cascade resolver.
    """
    raw = sources_param or ""
    if raw:
        sources = []
        for s in raw.split(","):
            t = s.strip()
            if t == "scripture":
                t = "scripture-tpt"
            if t:
                sources.append(t)
        if sources:
            return sources

    # Fallback to single cascade resolver
    return [_get_active_mirror()]


@router.get("/api/mirrors/today")
async def mirror_for_today(sources: Optional[str] = None):
    """Mirror content for today's tuning combination.

    Reads the current tuning (principle + frequency + signal_type),
    looks up matching mirror entries from ALL enabled mirrors.
    Returns an array of mirror results for stacking on the home tab.

    Query param `sources` = comma-separated mirror IDs (from MC.features.mirror_sources).
    """
    try:
        from src.tuning.receiver import get_todays_tuning, TuningPackage
        from src.config import get_primary_agent_id
        agent_id = get_primary_agent_id()
        package = await get_todays_tuning(agent_id)

        # Fallback to latest echo if no package for today
        if not package or not package.principle:
            try:
                from src.memory.database import get_db
                async with get_db() as conn:
                    result = await conn.execute(
                        """SELECT frequency, signal_type, principle
                           FROM echoes WHERE agent_id = %s
                           ORDER BY tuned_at DESC LIMIT 1""",
                        (agent_id,),
                    )
                    row = await result.fetchone()
                if row and row["principle"]:
                    package = TuningPackage({
                        "frequency": row["frequency"],
                        "signal_type": row["signal_type"],
                        "principle": row["principle"],
                    })
            except Exception:
                pass

        # Open-source fallback: derive today's tuning from the signed public Drop
        # so mirrors render on a Cove that doesn't run its own LTP. The music
        # mirror is keyed by frequency name (its top-level keys are the 13
        # frequencies), so we drive the lookup with the Drop's frequency.
        if not package or not package.principle:
            try:
                from src.tuning.public_drop import get_public_drop
                _d = get_public_drop()
                if _d is not None:
                    package = TuningPackage({
                        "frequency": _d.frequency_name,
                        "signal_type": _d.signal_type,
                        # Mirror YAML is keyed principle -> frequency -> signal, where
                        # the top-level principle is the Canon song. Use the Drop's
                        # tuning_key_source_song (matches drop_as_operator_tuning), NOT
                        # the frequency name, or the top-level lookup misses.
                        "principle": _d.tuning_key_source_song,
                    })
            except Exception:
                pass

        if not package or not package.principle:
            return {"has_mirror": False, "note": "No tuning available"}

        enabled_mirrors = _parse_mirror_sources(sources)
        mirrors = []

        for mid in enabled_mirrors:
            result = get_mirror_entry(
                principle=package.principle,
                frequency=getattr(package, "frequency", None),
                signal_type=getattr(package, "signal_type", None),
                mirror_id=mid,
            )
            if result:
                mirror_resp = {
                    "mirror_name": result["mirror_name"],
                    "mirror_type": result.get("mirror_type", "text"),
                    "canon": result["canon"],
                    "mirror_id": result["mirror_id"],
                    "featured": result["featured"],
                    "all_entries": result["entries"],
                    "entry_count": len(result["entries"]),
                }
                mirrors.append(mirror_resp)

        if not mirrors:
            return {"has_mirror": False, "note": "No mirror entries for this combination"}

        # Backward-compatible: top-level fields from first mirror
        first = mirrors[0]
        return {
            "has_mirror": True,
            "principle": package.principle,
            "principle_key": _to_key(package.principle),
            "frequency": getattr(package, "frequency", None),
            "signal_type": getattr(package, "signal_type", None),
            # First mirror (backward compat for existing UI)
            "mirror_name": first["mirror_name"],
            "canon": first["canon"],
            "mirror_id": first["mirror_id"],
            "featured": first["featured"],
            "all_entries": first["all_entries"],
            "entry_count": first["entry_count"],
            # All mirrors for stacking
            "mirrors": mirrors,
        }
    except Exception as e:
        return {"has_mirror": False, "error": str(e)}


@router.get("/api/mirrors/reflect/{principle_key}")
async def reflect_view(principle_key: str, mirror_id: Optional[str] = None):
    """Full Reflect view — all entries for a given principle."""
    mid = mirror_id or _get_active_mirror()
    data = _load_mirror(mid)
    if not data:
        return {"error": "Mirror not found"}

    principle_data = data.get(principle_key)
    if not principle_data or not isinstance(principle_data, dict):
        return {"error": "No entries for this principle"}

    meta = data.get("meta", {})
    entries = _collect_all_entries(principle_data)

    # Convert key back to display name
    display_name = principle_key.replace("_", " ").title()
    display_name = display_name.replace(" Of ", " of ").replace(" Is", " Is").replace(" The ", " the ")
    if display_name.startswith("The "):
        display_name = "The " + display_name[4:]
    if display_name.startswith("A "):
        display_name = "A " + display_name[2:]

    return {
        "mirror_name": meta.get("name", mid),
        "canon": meta.get("canon", ""),
        "principle": display_name,
        "principle_key": principle_key,
        "entries": [
            {"ref": e.get("ref", ""), "text": e.get("text", ""), "thread": e.get("thread", "")}
            for e in entries
        ],
    }


@router.get("/api/mirrors/list")
async def list_mirrors():
    """List all available mirror files from both published and cove-core dirs."""
    mirrors = []
    seen_ids = set()
    # Check published first (user-created), then cove-core (shipped)
    for search_dir in [_PUBLISHED_DIR, _COVECORE_MIRRORS]:
        if not search_dir.exists():
            continue
        for f in sorted(search_dir.glob("*.yaml")):
            if f.stem.startswith(".") or f.stem in seen_ids:
                continue
            seen_ids.add(f.stem)
            try:
                with open(f, "r") as fh:
                    data = yaml.safe_load(fh) or {}
                meta = data.get("meta", {})
                mirrors.append({
                    "id": f.stem,
                    "name": meta.get("name", f.stem),
                    "canon": meta.get("canon", ""),
                    "lens": meta.get("lens", ""),
                    "curator": meta.get("curator", ""),
                    "description": meta.get("description", ""),
                })
            except Exception:
                mirrors.append({"id": f.stem, "name": f.stem, "canon": "", "description": ""})
    return {"mirrors": mirrors, "active_mirror": _get_active_mirror()}


@router.get("/api/mirrors/registry")
async def mirror_registry():
    """Curated mirror catalog for Settings UI and Mirror Manager.

    Reads mirror-registry.json for display metadata (name, description, type,
    default flag, icon). Each entry also reports whether the mirror content
    file actually exists (available=true) so the UI can distinguish between
    'catalog entry' and 'ready to use'.
    """
    registry_path = _COVECORE_MIRRORS / "mirror-registry.json"
    if not registry_path.exists():
        return {"mirrors": [], "error": "Registry not found"}

    try:
        with open(registry_path, "r") as f:
            registry = json.load(f)
    except Exception as e:
        return {"mirrors": [], "error": f"Failed to load registry: {e}"}

    mirrors = []
    for entry in registry.get("mirrors", []):
        mid = entry.get("id", "")
        # Check if content file exists in either published or cove-core
        available = False
        for search_dir in [_PUBLISHED_DIR, _COVECORE_MIRRORS]:
            content_file = entry.get("content_file", f"{mid}.yaml")
            if (search_dir / content_file).exists():
                available = True
                break

        mirrors.append({
            "id": mid,
            "name": entry.get("name", mid),
            "description": entry.get("description", ""),
            "type": entry.get("type", "text"),
            "canon": entry.get("canon", ""),
            "curator": entry.get("curator", ""),
            "default": entry.get("default", False),
            "icon": entry.get("icon", "book"),
            "available": available,
        })

    return {"mirrors": mirrors}


@router.get("/api/mirrors/settings")
async def mirror_settings():
    """Current mirror resolution — shows which mirror is active and why."""
    from src.config import _get_presence_tuning, _get_cove_defaults, _get_haven_defaults

    presence = _get_presence_tuning()
    cove = _get_cove_defaults()
    haven = _get_haven_defaults()

    active = _get_active_mirror()

    return {
        "active_mirror": active,
        "cascade": {
            "presence": presence.get("mirror_id"),
            "cove": cove.get("mirror_id"),
            "haven": haven.get("mirror_id"),
            "env": env("ACTIVE_MIRROR"),
            "default": "scripture-tpt",
        },
        "resolved_from": (
            "presence" if presence.get("mirror_id")
            else "cove" if cove.get("mirror_id")
            else "haven" if haven.get("mirror_id")
            else "env" if env("ACTIVE_MIRROR")
            else "default"
        ),
    }
