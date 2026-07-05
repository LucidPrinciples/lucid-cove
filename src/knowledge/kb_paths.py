"""Knowledge Base path resolution (#135 cutover).

The Knowledge Base is hub-owned and distributed via the public Drop, synced into
each Cove (mounted at the FRAMEWORK_DIR / SHARED_FRAMEWORK_DIR location, default
``/shared/framework``). The public ``lucid-cove-core`` repo does NOT ship
``data/knowledge-base`` — so KB reads resolve at call time in priority order:
synced location first, the repo-bundled copy only as a founder/dev fallback.

Resolving per call (rather than once at import) means a KB sync that lands after
startup is picked up without a container restart.
"""
from pathlib import Path

from src.env import env

# cove-core repo root (this file is at <root>/src/knowledge/kb_paths.py)
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_REPO_KB = _REPO_ROOT / "data" / "knowledge-base"        # founder/dev only; absent in public tree
_LOCAL_FRAMEWORK = _REPO_ROOT / "config" / "framework"   # identity.py's legacy local fallback


def kb_dir_candidates():
    """KB directories to try, highest priority first (de-duplicated)."""
    cands = []
    for var in ("FRAMEWORK_DIR", "SHARED_FRAMEWORK_DIR"):
        v = env(var)
        if v:
            cands.append(Path(v))
    cands += [
        Path("/shared/framework"),                 # provisioned synced mount (from the Drop)
        Path("/cove-core/data/knowledge-base"),    # repo-bundled via /cove-core:ro (founder/dev)
        _REPO_KB,                                  # direct run from the repo root
        _LOCAL_FRAMEWORK,                          # last-resort local framework copy
    ]
    seen, out = set(), []
    for c in cands:
        s = str(c)
        if s not in seen:
            seen.add(s)
            out.append(c)
    return out


def resolve_kb_dir() -> Path:
    """First existing KB directory. If none exist, the top candidate (for clear logs)."""
    cands = kb_dir_candidates()
    for c in cands:
        try:
            if c.exists():
                return c
        except OSError:
            continue
    return cands[0]


def resolve_kb_file(filename: str) -> Path:
    """First existing KB file across the candidate dirs. Else the top-candidate path."""
    cands = kb_dir_candidates()
    for c in cands:
        p = c / filename
        try:
            if p.exists():
                return p
        except OSError:
            continue
    return cands[0] / filename
