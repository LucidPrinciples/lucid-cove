"""On-demand tuning endpoint — Tune Now.

Split into sub-modules:
  helpers.py  — shared auth, reference data, caches
  core.py     — request_tuning, today_tuning, coaching generation
  meta.py     — frequencies, contexts, history, session update, events, streak
  favorites.py — favorites CRUD
"""

from fastapi import APIRouter

from .core import router as core_router
from .meta import router as meta_router
from .favorites import router as favorites_router

router = APIRouter()
router.include_router(core_router)
router.include_router(meta_router)
router.include_router(favorites_router)
