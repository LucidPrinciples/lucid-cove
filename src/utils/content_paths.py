"""Shared content-path resolution for all platform posters.

social_queue stores video paths as vault-relative NC paths
('AgentSkills/Content/video/shorts/x.mp4'), but the uploaders mount the Content
folder at /content. This module is the single place that maps one to the other,
so YouTube, X, and any future platform resolve paths identically.
"""
from pathlib import Path

CONTENT_ROOT = Path("/content")


def resolve_content_path(file_path) -> Path | None:
    """Map a social_queue file_path to a real file under the /content mount.

    Accepts 'AgentSkills/Content/video/shorts/x.mp4', 'video/shorts/x.mp4',
    or a bare filename (searched in video/shorts/). Returns a Path that exists,
    or None if the file can't be found.
    """
    if not file_path:
        return None
    p = str(file_path).strip().lstrip("/")
    if p.startswith("AgentSkills/Content/"):
        p = p[len("AgentSkills/Content/"):]
    candidate = CONTENT_ROOT / p
    if candidate.is_file():
        return candidate
    # Bare-filename fallback — search the standard shorts folder
    candidate = CONTENT_ROOT / "video" / "shorts" / Path(p).name
    if candidate.is_file():
        return candidate
    return None
