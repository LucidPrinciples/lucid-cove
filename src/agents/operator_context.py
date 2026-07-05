"""Operator Context wiring (batch8 #10 / CF-99).

The provisioner seeds each presence a `AgentSkills/Context/` corpus, but the
agents never READ it — about/preferences/working-memory sat inert. This module
pulls a BOUNDED, cached slice of the three that matter into the system prompt,
and gives agents a bounded way to APPEND to working-memory after real work.

Design constraints (locked):
  - per-presence NC read, steward-space fallback (the caller supplies the reader)
  - a HARD char budget per file so a runaway note can't blow the context window
  - graceful skip on 404 / read error — NEVER block prompt assembly on NC
  - only about.md + preferences.md + working-memory.md are wired (the rest of the
    18-file seeded corpus stays as-is)

I/O is INJECTED (an async reader/writer callable) so the assembly + budgeting is
pure and unit-testable without NC.
"""
from __future__ import annotations

from typing import Awaitable, Callable, Optional

# The three Context files wired into the prompt, in prompt order.
CONTEXT_FILES = ("about.md", "preferences.md", "working-memory.md")
CONTEXT_DIR = "AgentSkills/Context"

# Hard per-file budget (chars). A bounded slice — enough to carry the operator's
# essentials, small enough that three of them never dominate the prompt.
PER_FILE_BUDGET = 1500
WORKING_MEMORY_MAX = 4000  # cap the file itself so appends can't grow unbounded


def _clip(text: str, budget: int) -> str:
    text = (text or "").strip()
    if len(text) <= budget:
        return text
    return text[:budget].rstrip() + "\n…(truncated)"


def assemble_context_slice(files: dict, per_file_budget: int = PER_FILE_BUDGET) -> str:
    """Pure assembly: given {filename: content}, build the bounded prompt block.
    Missing / empty files are skipped. Returns "" when nothing usable — the caller
    then adds no section at all."""
    sections = []
    labels = {"about.md": "About the person you serve",
              "preferences.md": "Their preferences",
              "working-memory.md": "Working memory (current focus)"}
    for name in CONTEXT_FILES:
        body = _clip(files.get(name) or "", per_file_budget)
        if body:
            sections.append(f"### {labels[name]}\n{body}")
    if not sections:
        return ""
    return ("## Operator Context\n"
            "What you know about the person you serve and how they want you to work. "
            "Honor it.\n\n" + "\n\n".join(sections))


async def load_operator_context_slice(
    reader: Callable[[str], Awaitable[Optional[str]]],
    per_file_budget: int = PER_FILE_BUDGET,
) -> str:
    """Read the three Context files via the injected async `reader(path)` (returns
    the file text or None on 404/error) and assemble the bounded slice. Never
    raises — any reader error for a file just drops that file."""
    files = {}
    for name in CONTEXT_FILES:
        try:
            files[name] = await reader(f"{CONTEXT_DIR}/{name}")
        except Exception:
            files[name] = None
    return assemble_context_slice(files, per_file_budget)


def append_working_memory(existing: str, note: str, max_chars: int = WORKING_MEMORY_MAX) -> str:
    """Bounded append to working-memory.md content. Adds `note` as a new line and
    trims the OLDEST lines if the file would exceed `max_chars` (keep the newest).
    Pure — the caller writes the result back via the same NC write path agents use."""
    note = (note or "").strip()
    if not note:
        return existing or ""
    body = (existing or "").rstrip()
    updated = (body + "\n" + note) if body else note
    if len(updated) <= max_chars:
        return updated
    # Over budget: drop oldest lines until it fits (never drop the just-added note).
    lines = updated.splitlines()
    while lines and len("\n".join(lines)) > max_chars and len(lines) > 1:
        lines.pop(0)
    return "\n".join(lines)
