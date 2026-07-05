"""
Mirror Builder — API for creating, curating, and publishing mirrors.

Supports the Mirror Creation Flow:
1. Setup — creates a draft with metadata
2. Generate — triggers batch AI generation per principle
3. Curate — update individual entries during walkthrough
4. Publish — finalize and move from drafts to active mirrors

Draft storage: /app/data/mirrors/drafts/
Published mirrors: /cove-core/data/mirrors/ (read-only mount)
  → writable copy: /app/data/mirrors/published/
"""

import json
import os
import re
import yaml
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()

# Storage paths
_DATA_DIR = Path("/app/data/mirrors")
_DRAFTS_DIR = _DATA_DIR / "drafts"
_PUBLISHED_DIR = _DATA_DIR / "published"

# Fallback for local dev (Mac)
if not _DATA_DIR.parent.exists():
    _DATA_DIR = Path(__file__).parent.parent.parent.parent / "data" / "mirrors"
    _DRAFTS_DIR = _DATA_DIR / "drafts"
    _PUBLISHED_DIR = _DATA_DIR

# Foundation docs (read from cove-core mount or local)
_COVECORE_MIRRORS = Path("/cove-core/data/mirrors")
if not _COVECORE_MIRRORS.exists():
    _COVECORE_MIRRORS = Path(__file__).parent.parent.parent.parent / "data" / "mirrors"


def _load_foundation_doc(filename: str) -> str:
    """Load a foundation document (principle-concepts.md or mirror-formula.md)."""
    path = _COVECORE_MIRRORS / filename
    if path.exists():
        return path.read_text()
    return ""


def _slugify(name: str) -> str:
    """Convert mirror name to a URL-safe slug."""
    slug = name.lower().strip()
    slug = re.sub(r'[^a-z0-9\s-]', '', slug)
    slug = re.sub(r'[\s]+', '-', slug)
    slug = re.sub(r'-+', '-', slug).strip('-')
    return slug[:60]


# ── Draft Management ─────────────────────────────────────────────────────────

@router.post("/api/mirrors/builder/create")
async def create_draft(request: Request):
    """Create a new mirror draft with metadata.

    Body: { name, canon, lens, curator, coverage }
    Returns: { draft_id, path }
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    name = body.get("name", "").strip()
    canon = body.get("canon", "").strip()
    lens = body.get("lens", "").strip()
    curator = body.get("curator", "").strip()
    coverage = body.get("coverage", "primary")

    if not all([name, canon, lens]):
        return JSONResponse({"error": "name, canon, and lens are required"}, status_code=400)

    draft_id = _slugify(name)
    if not draft_id:
        return JSONResponse({"error": "Could not generate valid ID from name"}, status_code=400)

    _DRAFTS_DIR.mkdir(parents=True, exist_ok=True)

    draft = {
        "meta": {
            "name": name,
            "canon": canon,
            "curator": curator or "Unknown",
            "lens": lens,
            "coverage": coverage,
            "version": 1,
            "created": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "status": "draft",
            "entry_count": 0,
        },
        "entries": {},  # principle_key -> { freq_key -> { signal_key -> entry } }
    }

    draft_path = _DRAFTS_DIR / f"{draft_id}.json"
    with open(draft_path, "w") as f:
        json.dump(draft, f, indent=2)

    return {"draft_id": draft_id, "status": "created"}


@router.get("/api/mirrors/builder/draft/{draft_id}")
async def get_draft(draft_id: str):
    """Get a draft mirror's current state."""
    draft_path = _DRAFTS_DIR / f"{draft_id}.json"
    if not draft_path.exists():
        return JSONResponse({"error": "Draft not found"}, status_code=404)

    with open(draft_path) as f:
        return json.load(f)


@router.put("/api/mirrors/builder/draft/{draft_id}/entries")
async def update_entries(draft_id: str, request: Request):
    """Batch update entries in a draft (used after AI generation or curation edits).

    Body: { entries: { principle_key: { freq_key: { signal_key: { ref, text, thread, status } } } } }
    status: "generated" | "accepted" | "edited" | "skipped"
    """
    draft_path = _DRAFTS_DIR / f"{draft_id}.json"
    if not draft_path.exists():
        return JSONResponse({"error": "Draft not found"}, status_code=404)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    new_entries = body.get("entries", {})
    if not new_entries:
        return JSONResponse({"error": "No entries provided"}, status_code=400)

    with open(draft_path) as f:
        draft = json.load(f)

    # Deep merge new entries into existing
    for p_key, freq_data in new_entries.items():
        if p_key not in draft["entries"]:
            draft["entries"][p_key] = {}
        for f_key, sig_data in freq_data.items():
            if f_key not in draft["entries"][p_key]:
                draft["entries"][p_key][f_key] = {}
            for s_key, entry in sig_data.items():
                draft["entries"][p_key][f_key][s_key] = entry

    # Recount
    count = 0
    for p in draft["entries"].values():
        for f in p.values():
            for s in f.values():
                if s.get("ref"):
                    count += 1
    draft["meta"]["entry_count"] = count

    with open(draft_path, "w") as f:
        json.dump(draft, f, indent=2)

    return {"draft_id": draft_id, "entry_count": count}


@router.put("/api/mirrors/builder/draft/{draft_id}/entry")
async def update_single_entry(draft_id: str, request: Request):
    """Update a single entry during curation walkthrough.

    Body: { principle_key, frequency_key, signal_key, ref, text, thread, status }
    """
    draft_path = _DRAFTS_DIR / f"{draft_id}.json"
    if not draft_path.exists():
        return JSONResponse({"error": "Draft not found"}, status_code=404)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    p_key = body.get("principle_key", "")
    f_key = body.get("frequency_key", "")
    s_key = body.get("signal_key", "")

    if not all([p_key, f_key, s_key]):
        return JSONResponse({"error": "principle_key, frequency_key, signal_key required"}, status_code=400)

    with open(draft_path) as f:
        draft = json.load(f)

    if p_key not in draft["entries"]:
        draft["entries"][p_key] = {}
    if f_key not in draft["entries"][p_key]:
        draft["entries"][p_key][f_key] = {}

    draft["entries"][p_key][f_key][s_key] = {
        "ref": body.get("ref", ""),
        "text": body.get("text", ""),
        "thread": body.get("thread", ""),
        "status": body.get("status", "edited"),
    }

    # Recount
    count = sum(
        1 for p in draft["entries"].values()
        for f in p.values()
        for s in f.values()
        if s.get("ref")
    )
    draft["meta"]["entry_count"] = count

    with open(draft_path, "w") as f:
        json.dump(draft, f, indent=2)

    return {"ok": True, "entry_count": count}


# ── Publication ──────────────────────────────────────────────────────────────

@router.post("/api/mirrors/builder/publish/{draft_id}")
async def publish_mirror(draft_id: str):
    """Convert a draft to a published mirror YAML.

    Moves from JSON draft format to the standard YAML format
    that mirrors.py reads. Strips curation metadata (status fields).
    """
    draft_path = _DRAFTS_DIR / f"{draft_id}.json"
    if not draft_path.exists():
        return JSONResponse({"error": "Draft not found"}, status_code=404)

    with open(draft_path) as f:
        draft = json.load(f)

    meta = draft["meta"]

    # Build YAML structure
    yaml_data = {
        "meta": {
            "name": meta["name"],
            "canon": meta["canon"],
            "curator": meta["curator"],
            "lens": meta["lens"],
            "version": meta.get("version", 1),
            "description": f'{meta["canon"]} passages mapped to Lucid Principles tuning combinations through the lens of: {meta["lens"][:100]}...',
        }
    }

    # Convert entries: strip status field, keep ref/text/thread
    for p_key, freq_data in draft.get("entries", {}).items():
        yaml_data[p_key] = {}
        for f_key, sig_data in freq_data.items():
            yaml_data[p_key][f_key] = {}
            for s_key, entry in sig_data.items():
                if entry.get("ref"):  # skip empty/skipped entries
                    yaml_data[p_key][f_key][s_key] = {
                        "ref": entry["ref"],
                        "text": entry["text"],
                        "thread": entry["thread"],
                    }
            # Clean empty frequency buckets
            if not yaml_data[p_key][f_key]:
                del yaml_data[p_key][f_key]
        if not yaml_data[p_key]:
            del yaml_data[p_key]

    # Write to published location
    _PUBLISHED_DIR.mkdir(parents=True, exist_ok=True)
    published_path = _PUBLISHED_DIR / f"{draft_id}.yaml"

    header = f"""# =============================================================================
# {meta['name']}
# =============================================================================
# Canon: {meta['canon']}
# Curator: {meta['curator']}
# Lens: {meta['lens'][:120]}
# Created: {meta['created']}
# Entry count: {meta['entry_count']}
#
# Generated via Mirror Creation Flow. Structure:
#   principle -> frequency -> signal_type -> {{ ref, text, thread }}
# =============================================================================

"""
    with open(published_path, "w") as f:
        f.write(header)
        yaml.dump(yaml_data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    # Mark draft as published
    meta["status"] = "published"
    with open(draft_path, "w") as f:
        json.dump(draft, f, indent=2)

    return {
        "draft_id": draft_id,
        "status": "published",
        "path": str(published_path),
        "entry_count": meta["entry_count"],
    }


# ── Foundation Data (for the Creation Flow UI) ───────────────────────────────

@router.get("/api/mirrors/builder/foundation")
async def get_foundation():
    """Return foundation data the Creation Flow needs for generation.

    Returns principle concepts and signal profiles so the frontend
    can build AI prompts per-principle.
    """
    concepts_text = _load_foundation_doc("principle-concepts.md")
    formula_text = _load_foundation_doc("mirror-formula.md")

    # Parse principle concepts into structured data
    principles = []
    if concepts_text:
        # Simple parse: find ### headers and their content
        import re
        blocks = re.split(r'\n### ', concepts_text)
        for block in blocks[1:]:  # skip preamble
            lines = block.strip().split('\n')
            name = lines[0].strip()
            # Find core teaching line
            core = ""
            concept = ""
            for line in lines[1:]:
                if line.startswith("**Core teaching:**"):
                    core = line.replace("**Core teaching:**", "").strip()
                elif line.startswith("**") and "**" in line[2:]:
                    concept = line.strip("* \n")

            key = name.strip().lower().replace(" ", "_")
            principles.append({
                "name": name,
                "key": key,
                "concept": concept,
                "core_teaching": core,
            })

    return {
        "principles": principles,
        "principle_count": len(principles),
        "formula_available": bool(formula_text),
    }


@router.get("/api/mirrors/builder/drafts")
async def list_drafts():
    """List all in-progress mirror drafts."""
    drafts = []
    if _DRAFTS_DIR.exists():
        for f in sorted(_DRAFTS_DIR.glob("*.json")):
            try:
                with open(f) as fh:
                    data = json.load(fh)
                meta = data.get("meta", {})
                drafts.append({
                    "id": f.stem,
                    "name": meta.get("name", f.stem),
                    "canon": meta.get("canon", ""),
                    "status": meta.get("status", "draft"),
                    "entry_count": meta.get("entry_count", 0),
                    "created": meta.get("created", ""),
                })
            except Exception:
                drafts.append({"id": f.stem, "name": f.stem, "status": "error"})
    return {"drafts": drafts}
