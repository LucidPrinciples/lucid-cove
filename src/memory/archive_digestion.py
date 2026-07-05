"""
Archive Digestion Pipeline — Turn session archives into agent knowledge.

Processes session-log-archive.md entries that haven't been digested yet,
extracting structured memories (agent_memory) and semantic chunks (knowledge_base).

Triggered: Weekly after Memory consolidation (Sunday 8pm)
Owner: Ezra (archival) + Archimedes (analysis)
"""

import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from langchain_core.messages import SystemMessage, HumanMessage

from src.memory.database import get_db
from src.memory.knowledge import chunk_document, get_embedding
from src.memory.memory import store_memory
from src.models.provider import invoke_with_fallback
from src.config import get_primary_agent_id

ARCHIVE_PATH = Path("/vault/LP-Vault/Archive/session-log-archive.md")
AGENT_ID = get_primary_agent_id()

# Cancellation flag — set via API to stop a running digestion
_cancel_requested = False


def request_cancel():
    """Signal the running digestion to stop after the current session."""
    global _cancel_requested
    _cancel_requested = True


def _reset_cancel():
    """Clear the cancel flag at the start of a run."""
    global _cancel_requested
    _cancel_requested = False


def _ts():
    return datetime.now(ZoneInfo("America/New_York")).strftime("[%Y-%m-%d %H:%M:%S ET]")


def _extract_session_entries(text: str) -> list[dict]:
    """Parse archive markdown into session entries.

    Pattern: "### YYYY-MM-DD (session N) — Title"
    Returns list of {session_num, date, title, content}
    """
    entries = []
    # Match session headers and capture everything until next ### or end
    pattern = r"###\s+(\d{4}-\d{2}-\d{2})\s+\(session\s+(\d+)\)\s+—\s+(.+?)\n(.*?)(?=\n### |\Z)"
    matches = re.finditer(pattern, text, re.DOTALL)

    for match in matches:
        date_str, session_str, title, content = match.groups()
        entries.append({
            "session_num": int(session_str),
            "date": date_str.strip(),
            "title": title.strip(),
            "content": content.strip(),
        })

    return sorted(entries, key=lambda x: x["session_num"])


async def _get_digested_sessions() -> set[int]:
    """Get set of session numbers already processed."""
    digested = set()

    async with get_db() as conn:
        # Check knowledge_base for _archive_sN entries
        try:
            result = await conn.execute(
                """SELECT DISTINCT metadata->>'source_session' as session
                   FROM knowledge_base
                   WHERE doc_name LIKE '_archive_s%'"""
            )
            rows = await result.fetchall()
            for row in rows:
                if row.get("session"):
                    try:
                        digested.add(int(row["session"]))
                    except ValueError:
                        pass
        except Exception as e:
            print(f"{_ts()} [digestion] KB query failed: {e}")

        # Also check agent_memory for archive entries
        try:
            result = await conn.execute(
                """SELECT DISTINCT tags
                   FROM agent_memory
                   WHERE source_channel = 'archive'"""
            )
            rows = await result.fetchall()
            for row in rows:
                tags = row.get("tags", [])
                if isinstance(tags, str):
                    tags = tags.strip("{}").split(",")
                for tag in tags:
                    tag = tag.strip().strip('"')
                    if tag.startswith("session-"):
                        try:
                            digested.add(int(tag.replace("session-", "")))
                        except ValueError:
                            pass
        except Exception as e:
            print(f"{_ts()} [digestion] memory query failed: {e}")

    return digested


async def _extract_memories_with_llm(session_entry: dict) -> list[dict]:
    """Use LLM to extract discrete facts from a session entry."""

    system_msg = """You are a knowledge extraction system. Extract key facts, decisions, and outcomes from session logs.

For each significant fact, provide:
- content: the fact as a single clear sentence
- category: one of [technical, decision, architecture, deployment, bug_fix, feature, process]
- importance: 0.0-1.0 (1.0 = critical decision that constrains future work, 0.3 = minor detail)
- tags: relevant keywords (3-5 tags)

Return ONLY a JSON array of objects. No markdown, no explanation."""

    human_msg = f"""Session {session_entry['session_num']} — {session_entry['title']}
Date: {session_entry['date']}

Content:
{session_entry['content']}

JSON output:"""

    try:
        messages = [SystemMessage(content=system_msg), HumanMessage(content=human_msg)]
        text = await invoke_with_fallback(
            messages,
            temperature=0.3,
            timeout=120,
            label=f"digestion/session-{session_entry['session_num']}",
            agent_id=AGENT_ID,
            operation_type="archive_digestion",
        )

        # Clean up common LLM output issues
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        memories = json.loads(text)
        if not isinstance(memories, list):
            print(f"{_ts()} [digestion] session {session_entry['session_num']}: LLM returned non-list")
            return []

        # Validate and clean
        valid = []
        for m in memories:
            if not isinstance(m, dict):
                continue
            content = m.get("content", "").strip()
            if not content or len(content) < 10:
                continue
            valid.append({
                "content": content,
                "category": m.get("category", "general"),
                "importance": float(m.get("importance", 0.5)),
                "tags": m.get("tags", []),
            })

        return valid

    except json.JSONDecodeError as e:
        print(f"{_ts()} [digestion] session {session_entry['session_num']}: JSON parse failed: {e}")
        return []
    except Exception as e:
        print(f"{_ts()} [digestion] session {session_entry['session_num']}: LLM extraction failed: {e}")
        return []


async def _store_session_memories(session_num: int, memories: list[dict]):
    """Store extracted memories to agent_memory."""
    for mem in memories:
        tags = mem.get("tags", []) + [f"session-{session_num}", "archive"]
        await store_memory(
            content=mem["content"],
            category=mem["category"],
            importance=mem["importance"],
            tags=tags,
            agent_id=AGENT_ID,
            source_channel="archive",
            source_summary=f"Digested from session {session_num}",
        )


async def _store_session_chunks(session_entry: dict):
    """Chunk and embed session content to knowledge_base."""
    session_num = session_entry["session_num"]
    doc_name = f"_archive_s{session_num}"
    full_text = f"## Session {session_num}: {session_entry['title']}\n\n{session_entry['content']}"

    chunks = chunk_document(full_text, doc_name)

    async with get_db() as conn:
        for i, chunk in enumerate(chunks):
            embedding = await get_embedding(chunk["text"])
            if not embedding:
                print(f"{_ts()} [digestion] session {session_num}: skip chunk {i} (no embedding)")
                continue

            await conn.execute(
                """INSERT INTO knowledge_base (doc_name, chunk_index, chunk_text, embedding, metadata)
                   VALUES (%s, %s, %s, %s::vector, %s::jsonb)
                   ON CONFLICT (doc_name, chunk_index) DO UPDATE SET
                       chunk_text = EXCLUDED.chunk_text,
                       embedding = EXCLUDED.embedding,
                       metadata = EXCLUDED.metadata""",
                (
                    doc_name,
                    i,
                    chunk["text"],
                    str(embedding),
                    json.dumps({
                        "section": chunk["section"],
                        "source_session": str(session_num),
                        "session_date": session_entry["date"],
                        "session_title": session_entry["title"],
                    }),
                )
            )


async def run_archive_digestion() -> dict:
    """Main entry point — process all undigested archive sessions.

    Returns summary of what was processed.
    """
    print(f"{_ts()} [digestion] Starting archive digestion pipeline...")

    if not ARCHIVE_PATH.exists():
        return {"status": "error", "error": f"Archive not found: {ARCHIVE_PATH}"}

    # Read archive
    archive_text = ARCHIVE_PATH.read_text(encoding="utf-8")
    sessions = _extract_session_entries(archive_text)

    if not sessions:
        return {"status": "no_sessions", "message": "No session entries found in archive"}

    print(f"{_ts()} [digestion] Found {len(sessions)} session entries in archive")

    # Check what's already digested
    digested = await _get_digested_sessions()
    undigested = [s for s in sessions if s["session_num"] not in digested]

    if not undigested:
        print(f"{_ts()} [digestion] All {len(sessions)} sessions already digested")
        return {"status": "up_to_date", "sessions_total": len(sessions), "digested": len(digested)}

    print(f"{_ts()} [digestion] Processing {len(undigested)} undigested sessions: {[s['session_num'] for s in undigested]}")

    _reset_cancel()

    # Process each undigested session
    results = {
        "processed": [],
        "memories_created": 0,
        "chunks_created": 0,
        "errors": [],
    }

    for session in undigested:
        if _cancel_requested:
            print(f"{_ts()} [digestion] Cancelled by user after {len(results['processed'])} sessions")
            results["errors"].append("Cancelled by user")
            break

        session_num = session["session_num"]
        try:
            print(f"{_ts()} [digestion] Processing session {session_num}: {session['title']}")

            # 1. Extract structured memories
            memories = await _extract_memories_with_llm(session)
            if memories:
                await _store_session_memories(session_num, memories)
                results["memories_created"] += len(memories)
                print(f"{_ts()} [digestion]   → {len(memories)} memories extracted")

            # 2. Chunk and embed
            await _store_session_chunks(session)
            chunk_count = len(chunk_document(
                f"## Session {session_num}: {session['title']}\n\n{session['content']}",
                "_archive_digest"
            ))
            results["chunks_created"] += chunk_count
            print(f"{_ts()} [digestion]   → {chunk_count} chunks embedded")

            results["processed"].append(session_num)

        except Exception as e:
            err_msg = f"Session {session_num}: {str(e)}"
            print(f"{_ts()} [digestion] ERROR {err_msg}")
            results["errors"].append(err_msg)

    summary = {
        "status": "success" if not results["errors"] else "partial",
        "sessions_processed": len(results["processed"]),
        "sessions_total": len(sessions),
        "memories_created": results["memories_created"],
        "chunks_created": results["chunks_created"],
        "session_numbers": results["processed"],
        "errors": results["errors"],
    }

    print(f"{_ts()} [digestion] Complete: {summary['sessions_processed']} sessions, "
          f"{summary['memories_created']} memories, {summary['chunks_created']} chunks")

    return summary


# Manual trigger for testing or immediate use
if __name__ == "__main__":
    import asyncio
    result = asyncio.run(run_archive_digestion())
    print(json.dumps(result, indent=2))
