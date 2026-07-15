"""#D54 — Semantic index over the vault session archive.

Harness Tier-1 #1: embedding index (nomic-embed-text / same backend as KB) over
`Archive/session-log-archive.md` + dated session files so agents retrieve by
MEANING, not tag-grep.

Storage reuses `knowledge_base` (pgvector 768). Archive docs are namespaced:
  - `_archive_s{N}`            — one doc per parsed session in session-log-archive.md
  - `_archive_file:{rel_path}` — other Archive/*.md files (dated sessions, etc.)

Digestion (LLM fact extraction) remains a separate weekly path in
`archive_digestion.py`. This module is the always-on, embed-only index.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from src.env import env
from src.memory.database import get_db
from src.memory.knowledge import (
    CHUNK_SIZE,
    chunk_document,
    get_embedding,
    ensure_local_embedding_model,
)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SESSION_HEADER_RE = re.compile(
    r"^###\s+(\d{4}-\d{2}-\d{2})\s+\(session\s+(\d+)\)\s*[—\-–]\s*(.+)$",
    re.MULTILINE,
)

# Doc name prefixes — search filters on these so framework KB stays separate.
ARCHIVE_SESSION_PREFIX = "_archive_s"
ARCHIVE_FILE_PREFIX = "_archive_file:"


def _ts() -> str:
    return datetime.now(ZoneInfo("America/New_York")).strftime("[%Y-%m-%d %H:%M:%S ET]")


def resolve_vault_lp() -> Path:
    """Locate the LP-Vault root under VAULT_DIR (canonical env: /vault).

    Layouts seen in the wild:
      /vault/LP-Vault/...
      /vault/...          (LP-Vault contents mounted at vault root)
    """
    root = Path(env("VAULT_DIR", "/vault"))
    candidates = [
        root / "LP-Vault",
        root,
        Path("/vault/LP-Vault"),
        Path("/vault"),
    ]
    for c in candidates:
        if not c.exists():
            continue
        if (c / "Archive").is_dir() or (c / "Memory.md").is_file():
            return c
    return root / "LP-Vault"


def session_log_archive_path() -> Path:
    return resolve_vault_lp() / "Archive" / "session-log-archive.md"


def archive_dir() -> Path:
    return resolve_vault_lp() / "Archive"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def extract_session_entries(text: str) -> list[dict]:
    """Parse session-log-archive.md into session entries.

    Pattern: ``### YYYY-MM-DD (session N) — Title``
    Returns list of {session_num, date, title, content}.
    """
    entries: list[dict] = []
    # Split on session headers; capture header fields + body until next header
    pattern = (
        r"^###\s+(\d{4}-\d{2}-\d{2})\s+\(session\s+(\d+)\)\s*[—\-–]\s*(.+?)\s*$"
        r"([\s\S]*?)(?=^###\s+\d{4}-\d{2}-\d{2}\s+\(session\s+\d+\)|\Z)"
    )
    for match in re.finditer(pattern, text, re.MULTILINE):
        date_str, session_str, title, content = match.groups()
        entries.append({
            "session_num": int(session_str),
            "date": date_str.strip(),
            "title": title.strip(),
            "content": content.strip(),
        })
    return sorted(entries, key=lambda x: x["session_num"])


def _file_hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _file_hash_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _session_doc_name(session_num: int) -> str:
    return f"{ARCHIVE_SESSION_PREFIX}{session_num}"


def _file_doc_name(rel_path: str) -> str:
    # keep path separators stable inside the doc_name
    safe = rel_path.replace("\\", "/").lstrip("/")
    return f"{ARCHIVE_FILE_PREFIX}{safe}"


def list_dated_session_files() -> list[Path]:
    """Other session markdown under Archive/ (not the monolithic archive)."""
    ad = archive_dir()
    if not ad.is_dir():
        return []
    skip_names = {"session-log-archive.md", "session-index.md", "README.md"}
    out: list[Path] = []
    for p in sorted(ad.rglob("*.md")):
        if not p.is_file():
            continue
        if p.name in skip_names:
            continue
        # Prefer session-looking names; still allow any Archive md for continuity
        name_l = p.name.lower()
        if "session" in name_l or p.parent.name.lower() in {"sessions", "session-logs"}:
            out.append(p)
            continue
        # dated files like 2026-07-09.md
        if re.match(r"^\d{4}-\d{2}-\d{2}", p.name):
            out.append(p)
    return out


# ---------------------------------------------------------------------------
# Index write
# ---------------------------------------------------------------------------

async def _existing_archive_hashes() -> dict[str, str]:
    """doc_name -> file_hash for archive-namespaced rows."""
    hashes: dict[str, str] = {}
    async with get_db() as conn:
        try:
            result = await conn.execute(
                """SELECT DISTINCT doc_name, metadata->>'file_hash' AS file_hash
                   FROM knowledge_base
                   WHERE doc_name LIKE %s OR doc_name LIKE %s""",
                (f"{ARCHIVE_SESSION_PREFIX}%", f"{ARCHIVE_FILE_PREFIX}%"),
            )
            for row in await result.fetchall():
                row = dict(row)
                if row.get("file_hash"):
                    hashes[row["doc_name"]] = row["file_hash"]
        except Exception as e:
            print(f"{_ts()} [archive-index] hash load failed (will re-embed): {e}")
    return hashes


async def _upsert_doc_chunks(
    doc_name: str,
    text: str,
    file_hash: str,
    base_meta: dict,
) -> int:
    """Chunk + embed one document into knowledge_base. Returns chunk count stored."""
    chunks = chunk_document(text, doc_name)
    if not chunks:
        return 0

    stored = 0
    async with get_db() as conn:
        await conn.execute(
            "DELETE FROM knowledge_base WHERE doc_name = %s",
            (doc_name,),
        )
        for i, chunk in enumerate(chunks):
            embedding = await get_embedding(chunk["text"])
            if not embedding:
                print(f"{_ts()} [archive-index] skip {doc_name} chunk {i}: no embedding")
                continue
            meta = {
                **base_meta,
                "section": chunk.get("section", ""),
                "file_hash": file_hash,
                "source": "vault_archive",
            }
            await conn.execute(
                """INSERT INTO knowledge_base (doc_name, chunk_index, chunk_text, embedding, metadata)
                   VALUES (%s, %s, %s, %s::vector, %s::jsonb)
                   ON CONFLICT (doc_name, chunk_index) DO UPDATE SET
                       chunk_text = EXCLUDED.chunk_text,
                       embedding = EXCLUDED.embedding,
                       metadata = EXCLUDED.metadata""",
                (doc_name, i, chunk["text"], str(embedding), json.dumps(meta)),
            )
            stored += 1
    return stored


async def index_session_log_archive(force: bool = False) -> dict:
    """Index sessions from Archive/session-log-archive.md."""
    path = session_log_archive_path()
    if not path.is_file():
        return {
            "status": "missing",
            "path": str(path),
            "sessions": 0,
            "chunks": 0,
            "skipped": 0,
        }

    text = path.read_text(encoding="utf-8", errors="replace")
    sessions = extract_session_entries(text)
    existing = {} if force else await _existing_archive_hashes()

    sessions_indexed = 0
    chunks = 0
    skipped = 0

    if not sessions:
        # Fall back: treat whole file as one doc
        doc_name = _file_doc_name("session-log-archive.md")
        fh = _file_hash_text(text)
        if not force and existing.get(doc_name) == fh:
            return {
                "status": "up_to_date",
                "path": str(path),
                "sessions": 0,
                "chunks": 0,
                "skipped": 1,
                "note": "no session headers; whole-file already indexed",
            }
        n = await _upsert_doc_chunks(
            doc_name,
            text,
            fh,
            {"kind": "session_log_archive_file", "path": "Archive/session-log-archive.md"},
        )
        return {
            "status": "ok",
            "path": str(path),
            "sessions": 0,
            "chunks": n,
            "skipped": 0,
            "note": "no session headers; indexed as single file",
        }

    for s in sessions:
        doc_name = _session_doc_name(s["session_num"])
        body = (
            f"## Session {s['session_num']}: {s['title']}\n\n"
            f"Date: {s['date']}\n\n"
            f"{s['content']}"
        )
        fh = _file_hash_text(body)
        if not force and existing.get(doc_name) == fh:
            skipped += 1
            continue
        n = await _upsert_doc_chunks(
            doc_name,
            body,
            fh,
            {
                "kind": "session",
                "source_session": str(s["session_num"]),
                "session_date": s["date"],
                "session_title": s["title"],
                "path": "Archive/session-log-archive.md",
            },
        )
        sessions_indexed += 1
        chunks += n

    return {
        "status": "ok",
        "path": str(path),
        "sessions": sessions_indexed,
        "sessions_total": len(sessions),
        "chunks": chunks,
        "skipped": skipped,
    }


async def index_dated_session_files(force: bool = False) -> dict:
    """Index other Archive session markdown files."""
    files = list_dated_session_files()
    if not files:
        return {"status": "ok", "files": 0, "chunks": 0, "skipped": 0}

    existing = {} if force else await _existing_archive_hashes()
    ad = archive_dir()
    files_indexed = 0
    chunks = 0
    skipped = 0

    for path in files:
        try:
            rel = str(path.relative_to(ad)).replace("\\", "/")
        except ValueError:
            rel = path.name
        doc_name = _file_doc_name(rel)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            print(f"{_ts()} [archive-index] read failed {path}: {e}")
            continue
        fh = _file_hash_path(path)
        if not force and existing.get(doc_name) == fh:
            skipped += 1
            continue
        n = await _upsert_doc_chunks(
            doc_name,
            text,
            fh,
            {
                "kind": "session_file",
                "path": f"Archive/{rel}",
                "filename": path.name,
            },
        )
        files_indexed += 1
        chunks += n

    return {
        "status": "ok",
        "files": files_indexed,
        "files_total": len(files),
        "chunks": chunks,
        "skipped": skipped,
    }


async def index_vault_archive(force: bool = False) -> dict:
    """Full archive index pass — sessions + dated files.

    Safe to call from boot / scheduler / tool. Idempotent via content hashes.
    """
    print(f"{_ts()} [archive-index] starting vault archive index (force={force})")
    await ensure_local_embedding_model()

    # Probe embedding once so we fail loud instead of writing empty rows
    probe = await get_embedding("archive-index-healthcheck")
    if not probe:
        print(f"{_ts()} [archive-index] embedding backend unavailable — abort")
        return {
            "status": "error",
            "error": "embedding backend unavailable",
            "vault": str(resolve_vault_lp()),
        }

    session_result = await index_session_log_archive(force=force)
    files_result = await index_dated_session_files(force=force)

    summary = {
        "status": "ok",
        "vault": str(resolve_vault_lp()),
        "session_log": session_result,
        "dated_files": files_result,
        "chunks_total": (
            int(session_result.get("chunks") or 0)
            + int(files_result.get("chunks") or 0)
        ),
    }
    print(
        f"{_ts()} [archive-index] done — "
        f"sessions={session_result.get('sessions', 0)} "
        f"files={files_result.get('files', 0)} "
        f"chunks={summary['chunks_total']}"
    )
    return summary


async def archive_index_status() -> dict:
    """Counts + paths for ops / tools."""
    path = session_log_archive_path()
    sessions_on_disk = 0
    if path.is_file():
        try:
            sessions_on_disk = len(
                extract_session_entries(path.read_text(encoding="utf-8", errors="replace"))
            )
        except Exception:
            sessions_on_disk = -1

    async with get_db() as conn:
        result = await conn.execute(
            """SELECT
                 count(*) FILTER (WHERE doc_name LIKE %s) AS session_docs,
                 count(*) FILTER (WHERE doc_name LIKE %s) AS file_docs,
                 count(*) FILTER (
                   WHERE (doc_name LIKE %s OR doc_name LIKE %s)
                     AND embedding IS NOT NULL
                 ) AS chunks_embedded
               FROM knowledge_base""",
            (
                f"{ARCHIVE_SESSION_PREFIX}%",
                f"{ARCHIVE_FILE_PREFIX}%",
                f"{ARCHIVE_SESSION_PREFIX}%",
                f"{ARCHIVE_FILE_PREFIX}%",
            ),
        )
        row = dict(await result.fetchone())

    return {
        "status": "ok",
        "vault": str(resolve_vault_lp()),
        "archive_path": str(path),
        "archive_exists": path.is_file(),
        "sessions_on_disk": sessions_on_disk,
        "dated_files_on_disk": len(list_dated_session_files()),
        "session_docs_indexed": int(row.get("session_docs") or 0),
        "file_docs_indexed": int(row.get("file_docs") or 0),
        "chunks_embedded": int(row.get("chunks_embedded") or 0),
    }


# ---------------------------------------------------------------------------
# Search / get
# ---------------------------------------------------------------------------

async def search_archive_semantic(
    query: str,
    limit: int = 8,
    min_similarity: float = 0.28,
) -> list[dict]:
    """Semantic search over vault-archive chunks only."""
    query = (query or "").strip()
    if not query:
        return []

    query_embedding = await get_embedding(query)
    if not query_embedding:
        return []

    fetch_limit = max(limit * 3, 20)
    async with get_db() as conn:
        result = await conn.execute(
            """SELECT doc_name, chunk_index, chunk_text, metadata,
                      1 - (embedding <=> %s::vector) AS similarity
               FROM knowledge_base
               WHERE embedding IS NOT NULL
                 AND (doc_name LIKE %s OR doc_name LIKE %s)
               ORDER BY embedding <=> %s::vector
               LIMIT %s""",
            (
                str(query_embedding),
                f"{ARCHIVE_SESSION_PREFIX}%",
                f"{ARCHIVE_FILE_PREFIX}%",
                str(query_embedding),
                fetch_limit,
            ),
        )
        rows = [dict(r) for r in await result.fetchall()]

    out: list[dict] = []
    for r in rows:
        sim = float(r.get("similarity") or 0)
        if sim < min_similarity:
            continue
        meta = r.get("metadata") or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}
        ref = f"archive:{r['doc_name']}#{r['chunk_index']}"
        out.append({
            "ref": ref,
            "source": "archive",
            "doc_name": r["doc_name"],
            "chunk_index": r["chunk_index"],
            "text": r["chunk_text"],
            "similarity": round(sim, 4),
            "section": meta.get("section", ""),
            "session_num": meta.get("source_session"),
            "session_date": meta.get("session_date"),
            "session_title": meta.get("session_title"),
            "path": meta.get("path", ""),
            "title": meta.get("session_title") or meta.get("filename") or r["doc_name"],
        })
        if len(out) >= limit:
            break
    return out


async def get_archive_chunk(ref: str) -> Optional[dict]:
    """Fetch one archive chunk by ref: ``archive:{doc_name}#{chunk_index}``."""
    ref = (ref or "").strip()
    if ref.startswith("archive:"):
        ref = ref[len("archive:"):]
    if "#" not in ref:
        return None
    doc_name, _, idx_s = ref.rpartition("#")
    try:
        chunk_index = int(idx_s)
    except ValueError:
        return None
    if not (
        doc_name.startswith(ARCHIVE_SESSION_PREFIX)
        or doc_name.startswith(ARCHIVE_FILE_PREFIX)
    ):
        return None

    async with get_db() as conn:
        result = await conn.execute(
            """SELECT doc_name, chunk_index, chunk_text, metadata
               FROM knowledge_base
               WHERE doc_name = %s AND chunk_index = %s""",
            (doc_name, chunk_index),
        )
        row = await result.fetchone()
    if not row:
        return None
    row = dict(row)
    meta = row.get("metadata") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}
    return {
        "ref": f"archive:{doc_name}#{chunk_index}",
        "source": "archive",
        "doc_name": doc_name,
        "chunk_index": chunk_index,
        "text": row["chunk_text"],
        "metadata": meta,
        "path": meta.get("path", ""),
        "session_num": meta.get("source_session"),
        "session_date": meta.get("session_date"),
        "session_title": meta.get("session_title"),
    }


async def search_memory_unified(
    query: str,
    agent_id: str | None = None,
    limit: int = 8,
    source: str = "all",
    min_similarity: float = 0.3,
) -> list[dict]:
    """Combined meaning-search: agent_memory + vault archive.

    ``source``: all | memories | archive
    """
    source = (source or "all").strip().lower()
    if source not in {"all", "memories", "archive"}:
        source = "all"

    results: list[dict] = []

    if source in {"all", "memories"}:
        from src.memory.memory import search_memories_semantic, _default_agent_id

        aid = _default_agent_id(agent_id)
        mems = await search_memories_semantic(
            query=query,
            agent_id=aid,
            limit=limit,
            min_similarity=min_similarity,
        )
        for m in mems:
            results.append({
                "ref": f"memory:{m['id']}",
                "source": "memory",
                "id": m["id"],
                "content": m["content"],
                "category": m.get("category"),
                "importance": m.get("importance"),
                "tags": m.get("tags") or [],
                "similarity": m.get("similarity"),
                "score": m.get("score"),
            })

    if source in {"all", "archive"}:
        arch = await search_archive_semantic(
            query=query,
            limit=limit,
            min_similarity=max(0.25, min_similarity - 0.05),
        )
        for a in arch:
            results.append({
                "ref": a["ref"],
                "source": "archive",
                "content": a["text"],
                "similarity": a["similarity"],
                "score": a["similarity"],
                "title": a.get("title"),
                "path": a.get("path"),
                "session_num": a.get("session_num"),
                "session_date": a.get("session_date"),
                "doc_name": a.get("doc_name"),
                "chunk_index": a.get("chunk_index"),
            })

    # Rank by score / similarity
    results.sort(
        key=lambda r: float(r.get("score") or r.get("similarity") or 0),
        reverse=True,
    )
    return results[:limit]
