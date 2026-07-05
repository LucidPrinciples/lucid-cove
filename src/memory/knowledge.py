"""
Knowledge Base — Framework document chunking, embedding, and semantic search.

Reads all markdown files from the vault Knowledge Base directory, chunks them
by section headers, generates embeddings via local Ollama (nomic-embed-text),
and stores them in the knowledge_base pgvector table for semantic search.

On app startup, populate_knowledge_base() checks if docs have changed
(by comparing file hashes) and re-embeds only what's new or modified.

Usage:
    from src.memory.knowledge import populate_knowledge_base, search_knowledge

    # At startup:
    await populate_knowledge_base()

    # In agent tools or context loading:
    results = await search_knowledge("tuning keys for INTEGRATION frequency")
"""

import hashlib
import json
import os
from src.env import env
import re
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import httpx

from src.memory.database import get_db


# =========================================================================
# Config
# =========================================================================

# Framework docs — hub-owned KB synced into each Cove (#135); resolved synced-first
# (repo-bundled copy only as a founder/dev fallback). Re-resolved at populate time.
from src.knowledge.kb_paths import resolve_kb_dir
FRAMEWORK_DIR = resolve_kb_dir()

# Reference tier docs — only available to agents with full vault access (e.g. Stuart)
# Agents without these paths simply skip them (existence checked at index time)
REFERENCE_DOCS = [
    Path("/vault/LP-Vault/Context-Map.md"),
    Path("/vault/LP-Vault/Workspace/roadmap.md"),
    Path("/vault/LP-Vault/Agents/_REGISTRY.md"),
]
OLLAMA_URL = env("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
EMBEDDING_MODEL = "nomic-embed-text"  # 768 dimensions, matches knowledge_base schema
EMBEDDING_DIM = 768                   # the knowledge_base pgvector column width
# Cloud embedding model — OpenAI's text-embedding-3-small supports a `dimensions` param, so
# we can pin it to 768 and keep the SAME pgvector schema as local nomic (no migration).
CLOUD_EMBEDDING_MODEL = "text-embedding-3-small"
CHUNK_SIZE = 800        # approx chars per chunk
CHUNK_OVERLAP = 100     # overlap between chunks for continuity


def _ts():
    return datetime.now(ZoneInfo("America/New_York")).strftime("[%Y-%m-%d %H:%M:%S ET]")


# =========================================================================
# Chunking
# =========================================================================

def chunk_document(text: str, doc_name: str) -> list[dict]:
    """Split a markdown document into chunks by ## headers, with size limits."""
    chunks = []
    sections = re.split(r"(?=^## )", text, flags=re.MULTILINE)

    for section in sections:
        section = section.strip()
        if not section:
            continue

        header_match = re.match(r"^##\s+(.+?)$", section, re.MULTILINE)
        header = header_match.group(1) if header_match else "Introduction"

        if len(section) <= CHUNK_SIZE:
            chunks.append({
                "text": section,
                "section": header,
                "doc_name": doc_name,
            })
        else:
            # Split large sections by paragraphs
            paragraphs = section.split("\n\n")
            current_chunk = ""
            for para in paragraphs:
                if len(current_chunk) + len(para) > CHUNK_SIZE and current_chunk:
                    chunks.append({
                        "text": current_chunk.strip(),
                        "section": header,
                        "doc_name": doc_name,
                    })
                    current_chunk = current_chunk[-CHUNK_OVERLAP:] + "\n\n" + para
                else:
                    current_chunk += "\n\n" + para if current_chunk else para

            if current_chunk.strip():
                chunks.append({
                    "text": current_chunk.strip(),
                    "section": header,
                    "doc_name": doc_name,
                })

    return chunks


# =========================================================================
# Embedding via Ollama
# =========================================================================

def _resolve_embedding_backend() -> dict:
    """CF-107: pick the embedding backend from the compute LLM mode, honoring the SOVEREIGNTY
    gate — a Cove on LOCAL never silently pays a cloud provider.
      - llm.mode == 'local'    → local Ollama nomic-embed (768).
      - llm.mode == 'external' → the external URL's Ollama-compatible /api/embed (768).
      - llm.mode == 'cloud'    → an OpenAI-compatible embeddings API pinned to 768 dims
                                 (text-embedding-3-small's `dimensions` param), IF an OpenAI
                                 key exists. The default cloud LLM provider (OpenRouter) has
                                 NO embeddings API, so absent an OpenAI key there is simply no
                                 backend → semantic search is OFF (we never fall back to a
                                 paid-by-surprise or a dimension-mismatched backend).
    Returns {'kind': 'local'|'external'|'cloud'|'none', 'url','model','key','reason'}."""
    try:
        from src.config import get_compute_config
        _llm = get_compute_config().get("llm") or {}
        mode = (_llm.get("mode") or "cloud").strip()
        url = (_llm.get("url") or "").strip()
    except Exception:
        mode, url = "local", ""
    if mode == "local":
        return {"kind": "local", "url": OLLAMA_URL, "model": EMBEDDING_MODEL, "key": "", "reason": ""}
    if mode == "external":
        return {"kind": "external", "url": (url or OLLAMA_URL).rstrip("/"),
                "model": EMBEDDING_MODEL, "key": "", "reason": ""}
    # cloud (reached ONLY in cloud mode → sovereignty gate intact)
    key = env("OPENAI_API_KEY")
    if key:
        return {"kind": "cloud", "url": "https://api.openai.com/v1",
                "model": CLOUD_EMBEDDING_MODEL, "key": key, "reason": ""}
    return {"kind": "none", "url": "", "model": "", "key": "",
            "reason": "Semantic search is off — this Cove's cloud provider has no embeddings "
                      "backend. Add an OpenAI API key, or set compute → LLM to a local Ollama."}


def semantic_search_status() -> dict:
    """CF-107 surface: is semantic search available, and if not, why? Lets callers/UI show
    'semantic search off' with an actionable reason instead of silently returning nothing."""
    b = _resolve_embedding_backend()
    return {"available": b["kind"] != "none", "backend": b["kind"], "reason": b.get("reason", "")}


async def get_embedding(text: str) -> Optional[list]:
    """Embed `text` via the compute-mode-resolved backend (local Ollama / external Ollama /
    cloud OpenAI @ 768 dims). Returns None when there is no backend (cloud mode without an
    OpenAI key) or on any failure — callers treat None as 'semantic search off'."""
    b = _resolve_embedding_backend()
    if b["kind"] == "none":
        return None
    try:
        async with httpx.AsyncClient() as client:
            if b["kind"] == "cloud":
                response = await client.post(
                    f"{b['url']}/embeddings",
                    headers={"Authorization": f"Bearer {b['key']}"},
                    json={"model": b["model"], "input": text, "dimensions": EMBEDDING_DIM},
                    timeout=30.0,
                )
                response.raise_for_status()
                data = response.json()
                return ((data.get("data") or [{}])[0] or {}).get("embedding")
            # local / external — Ollama-compatible /api/embed
            response = await client.post(
                f"{b['url']}/api/embed",
                json={"model": b["model"], "input": text},
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()
            embeddings = data.get("embeddings", [])
            if embeddings:
                return embeddings[0]
            return data.get("embedding")
    except Exception as e:
        print(f"{_ts()} [knowledge] embedding failed ({b['kind']}): {e}")
        return None


# =========================================================================
# Populate — runs at app startup
# =========================================================================

def _file_hash(path: Path) -> str:
    """MD5 hash of file contents for change detection."""
    return hashlib.md5(path.read_bytes()).hexdigest()


async def populate_knowledge_base() -> bool:
    """Chunk and embed all framework docs. Skips unchanged files.

    Compares file content hashes against what's stored in the DB metadata.
    Only re-embeds docs that are new or modified since last populate.

    Returns True when the index is settled (indexed or already up to date) and
    False when a retry might help (KB files not synced yet, Ollama unreachable,
    embedding model unavailable) — callers use this to re-kick (audit C3-2)."""
    framework_dir = resolve_kb_dir()
    if not framework_dir.exists():
        print(f"{_ts()} [knowledge] framework dir not found: {framework_dir}")
        return False   # fresh box — the KB sync hasn't landed files yet

    md_files = sorted(framework_dir.glob("*.md"))
    if not md_files:
        print(f"{_ts()} [knowledge] no markdown files in {framework_dir}")
        return False

    print(f"{_ts()} [knowledge] checking {len(md_files)} framework docs...")

    # Get existing doc hashes from DB
    existing_hashes = {}
    async with get_db() as conn:
        try:
            result = await conn.execute(
                "SELECT DISTINCT doc_name, metadata->>'file_hash' as file_hash FROM knowledge_base"
            )
            for row in await result.fetchall():
                row = dict(row)
                if row.get("file_hash"):
                    existing_hashes[row["doc_name"]] = row["file_hash"]
        except Exception as e:
            print(f"{_ts()} [knowledge] hash check failed (will re-embed all): {e}")

    # Check which framework docs need updating
    docs_to_embed = []
    for md_file in md_files:
        current_hash = _file_hash(md_file)
        if existing_hashes.get(md_file.name) == current_hash:
            continue  # unchanged
        docs_to_embed.append((md_file, current_hash))

    # Check which reference docs need updating
    refs_to_embed = []
    for ref_file in REFERENCE_DOCS:
        if not ref_file.exists():
            continue
        current_hash = _file_hash(ref_file)
        if existing_hashes.get(ref_file.name) == current_hash:
            continue
        refs_to_embed.append((ref_file, current_hash))

    if not docs_to_embed and not refs_to_embed:
        print(f"{_ts()} [knowledge] all {len(md_files)} framework + "
              f"{len(REFERENCE_DOCS)} reference docs up to date, skipping")
        return True

    print(f"{_ts()} [knowledge] {len(docs_to_embed)} framework + "
          f"{len(refs_to_embed)} reference docs need embedding")

    # Ensure embedding model is available
    try:
        test_emb = await get_embedding("test")
        if not test_emb:
            print(f"{_ts()} [knowledge] embedding model not responding, pulling {EMBEDDING_MODEL}...")
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"{OLLAMA_URL}/api/pull",
                    json={"name": EMBEDDING_MODEL},
                    timeout=300.0,
                )
            # Retry
            test_emb = await get_embedding("test")
            if not test_emb:
                print(f"{_ts()} [knowledge] FAILED: embedding model unavailable after pull")
                return False
    except Exception as e:
        print(f"{_ts()} [knowledge] FAILED: cannot reach Ollama for embeddings: {e}")
        return False

    # Chunk and embed each doc
    total_chunks = 0
    for md_file, file_hash in docs_to_embed:
        text = md_file.read_text(encoding="utf-8")
        chunks = chunk_document(text, md_file.name)
        print(f"{_ts()} [knowledge] {md_file.name}: {len(chunks)} chunks")

        async with get_db() as conn:
            # Clear old chunks for this doc (handles re-chunking cleanly)
            await conn.execute(
                "DELETE FROM knowledge_base WHERE doc_name = %s",
                (md_file.name,)
            )

            for i, chunk in enumerate(chunks):
                embedding = await get_embedding(chunk["text"])
                if not embedding:
                    print(f"{_ts()} [knowledge] skip chunk {i} of {md_file.name}: no embedding")
                    continue

                await conn.execute(
                    """INSERT INTO knowledge_base (doc_name, chunk_index, chunk_text, embedding, metadata)
                       VALUES (%s, %s, %s, %s::vector, %s::jsonb)
                       ON CONFLICT (doc_name, chunk_index) DO UPDATE SET
                           chunk_text = EXCLUDED.chunk_text,
                           embedding = EXCLUDED.embedding,
                           metadata = EXCLUDED.metadata""",
                    (
                        md_file.name,
                        i,
                        chunk["text"],
                        str(embedding),
                        json.dumps({
                            "section": chunk["section"],
                            "file_hash": file_hash,
                        }),
                    )
                )
                total_chunks += 1

    # Index Reference tier docs (Context-Map, roadmap) — same process
    for ref_file, file_hash in refs_to_embed:
        text = ref_file.read_text(encoding="utf-8")
        chunks = chunk_document(text, ref_file.name)
        print(f"{_ts()} [knowledge] {ref_file.name} (reference): {len(chunks)} chunks")

        async with get_db() as conn:
            await conn.execute(
                "DELETE FROM knowledge_base WHERE doc_name = %s",
                (ref_file.name,)
            )
            for i, chunk in enumerate(chunks):
                embedding = await get_embedding(chunk["text"])
                if not embedding:
                    continue
                await conn.execute(
                    """INSERT INTO knowledge_base (doc_name, chunk_index, chunk_text, embedding, metadata)
                       VALUES (%s, %s, %s, %s::vector, %s::jsonb)
                       ON CONFLICT (doc_name, chunk_index) DO UPDATE SET
                           chunk_text = EXCLUDED.chunk_text,
                           embedding = EXCLUDED.embedding,
                           metadata = EXCLUDED.metadata""",
                    (
                        ref_file.name,
                        i,
                        chunk["text"],
                        str(embedding),
                        json.dumps({
                            "section": chunk["section"],
                            "file_hash": file_hash,
                        }),
                    )
                )
                total_chunks += 1

    print(f"{_ts()} [knowledge] done: {total_chunks} chunks embedded "
          f"({len(docs_to_embed)} framework + {len(refs_to_embed)} reference docs)")
    return True


# =========================================================================
# Search — used by agent tools and Deep channel context loading
# =========================================================================

# =========================================================================
# Vault Working Memory — read directly for Day channel injection
# =========================================================================

# Full vault dir — only agents with full vault access (Stuart) use this for Working Memory
_VAULT_DIR = Path(env("VAULT_DIR", "/vault/LP-Vault"))


def load_working_memory(budget_chars: int = 3000) -> str:
    """Read Current Sprint, Last Session Handoff, and System State from Memory.md.

    These sections are the "what's happening right now" context for the Day channel.
    Returns formatted text ready for system prompt injection, or empty string.
    """
    memory_file = _VAULT_DIR / "Memory.md"
    if not memory_file.exists():
        return ""

    try:
        text = memory_file.read_text(encoding="utf-8")
    except Exception as e:
        print(f"{_ts()} [knowledge] failed to read Memory.md: {e}")
        return ""

    # Extract the three Working Memory sections
    sections_to_extract = ["Current Sprint", "Last Session Handoff", "System State"]
    extracted = []

    for section_name in sections_to_extract:
        # Match ## Section Name through to next ## or ---
        pattern = rf"## {re.escape(section_name)}\s*\n(.*?)(?=\n## |\n---|\Z)"
        match = re.search(pattern, text, re.DOTALL)
        if match:
            content = match.group(1).strip()
            # Strip blockquote lines (> description lines)
            lines = [l for l in content.split("\n") if not l.strip().startswith("> ")]
            content = "\n".join(lines).strip()
            if content:
                extracted.append(f"### {section_name}\n{content}")

    if not extracted:
        return ""

    result = "\n\n## Working Memory (Vault)\n" + "\n\n".join(extracted)

    # Respect budget
    if len(result) > budget_chars:
        result = result[:budget_chars] + "\n[...truncated]"

    return result


# =========================================================================
# Search — used by agent tools and Deep channel context loading
# =========================================================================

async def search_knowledge(query: str, limit: int = 5) -> list[dict]:
    """Semantic search over the knowledge base.

    Embeds the query text, then finds the most similar chunks via
    pgvector cosine distance.

    Returns list of dicts with: doc_name, section, text, similarity
    """
    query_embedding = await get_embedding(query)
    if not query_embedding:
        return []

    async with get_db() as conn:
        result = await conn.execute(
            """SELECT doc_name, chunk_text, metadata,
                      1 - (embedding <=> %s::vector) as similarity
               FROM knowledge_base
               WHERE embedding IS NOT NULL
               ORDER BY embedding <=> %s::vector
               LIMIT %s""",
            (str(query_embedding), str(query_embedding), limit)
        )
        rows = [dict(r) for r in await result.fetchall()]

    results = []
    for r in rows:
        meta = r.get("metadata", {})
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}
        results.append({
            "doc_name": r["doc_name"],
            "section": meta.get("section", ""),
            "text": r["chunk_text"],
            "similarity": round(float(r.get("similarity", 0)), 4),
        })

    return results
