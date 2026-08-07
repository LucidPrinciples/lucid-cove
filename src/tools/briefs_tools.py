"""Briefs tools — publish operator-facing readable docs and pin them on Links.

Markdown remains source of truth. Operators read /briefs/{slug}. Agents paste
that URL in chat (linkified) and optionally pin a Links card under Briefs.
"""

from __future__ import annotations

import logging

from langchain_core.tools import tool

from src.tools.approval import auto, notify

logger = logging.getLogger(__name__)


async def _await_pin(title: str, url: str, note: str, kind: str) -> str:
    try:
        from src.tools import links_tools as lt

        icon = {"brief": "📄", "plan": "📋", "spec": "📐"}.get(kind, "📄")
        # Prefer direct store write via add_action_link tool coroutine
        coro = None
        tool_obj = lt.add_action_link
        if hasattr(tool_obj, "ainvoke"):
            return await tool_obj.ainvoke(
                {
                    "title": title,
                    "url": url,
                    "note": note or f"{kind.title()} — open in reader",
                    "icon": icon,
                    "group": "Briefs",
                }
            )
        fn = getattr(tool_obj, "coroutine", None)
        if fn is not None:
            return await fn(
                title=title,
                url=url,
                note=note or f"{kind.title()} — open in reader",
                icon=icon,
                group="Briefs",
            )
        return ""
    except Exception as e:
        logger.warning("Could not pin brief on Links: %s", e)
        return f"(Links pin skipped: {e})"


@notify
@tool
async def publish_brief(
    title: str,
    content_markdown: str,
    kind: str = "brief",
    summary: str = "",
    source_path: str = "",
    slug: str = "",
    pin_to_links: str = "true",
) -> str:
    """Publish an operator-facing brief, plan, or spec to the readable Briefs reader.

    Use when you have a plan, discovery pack, or spec the operator should review
    in the browser (not as a raw .md path). Returns a /briefs/{slug} URL you
    should paste in chat. Optionally pins a card under the Briefs group on Links.

    Args:
        title: Document title shown in the reader and library.
        content_markdown: Full markdown body (headings, lists, tables OK).
        kind: brief | plan | spec (default brief). Use promote_brief to move
              an existing doc forward without rewriting.
        summary: One-line blurb for the library card and Links note.
        source_path: Optional vault-relative path to the raw .md power users
                     still open (e.g. AgentSkills/Working/Specs/foo.md).
        slug: Optional stable slug to update an existing doc in place.
        pin_to_links: 'true' (default) pins/updates Links under group Briefs.
    """
    try:
        from src.dashboard.routes import briefs as br

        kind_l = (kind or "brief").strip().lower()
        if kind_l not in br._KINDS:
            return f"kind must be one of: {', '.join(br._KINDS)}"
        if not (title or "").strip():
            return "title is required."
        if not (content_markdown or "").strip() and not (source_path or "").strip():
            return "Provide content_markdown and/or a readable source_path."

        meta = br.publish_doc(
            title=title,
            content_markdown=content_markdown or "",
            kind=kind_l,
            summary=summary or "",
            source_path=source_path or "",
            slug=(slug or "").strip() or None,
            published_by="agent",
        )
        url = br.reader_url(meta["slug"])
        pin_msg = ""
        if str(pin_to_links or "true").strip().lower() in ("1", "true", "yes", "y"):
            pin_msg = await _await_pin(
                title=meta.get("title") or title,
                url=url,
                note=summary or "",
                kind=kind_l,
            )
        lines = [
            f"Published {meta.get('kind')} '{meta.get('title')}'.",
            f"Open: {url}",
            f"Library: /briefs",
        ]
        if meta.get("source_path"):
            lines.append(f"Source path (power user): {meta['source_path']}")
        if pin_msg:
            lines.append(pin_msg)
        return "\n".join(lines)
    except Exception as e:
        logger.error("publish_brief failed: %s", e)
        return f"Error publishing brief: {e}"


@notify
@tool
async def promote_brief(slug_or_title: str, to_kind: str = "plan") -> str:
    """Promote a published brief forward: brief → plan → spec (same slug/URL).

    Use when a living doc matures (operator approved the brief, now it is the
    build plan or the locked spec). Does not demote.

    Args:
        slug_or_title: Slug or exact title of the published doc.
        to_kind: plan or spec (or brief if already brief — no-op).
    """
    try:
        from src.dashboard.routes import briefs as br

        meta, err = br.promote_doc(slug_or_title, to_kind)
        if err:
            return err
        url = br.reader_url(meta["slug"])
        # Refresh Links card title/note with new kind
        pin = await _await_pin(
            title=meta.get("title") or meta["slug"],
            url=url,
            note=f"{(meta.get('kind') or 'brief').title()} — open in reader",
            kind=meta.get("kind") or "plan",
        )
        msg = (
            f"Promoted '{meta.get('title')}' → {meta.get('kind')}.\n"
            f"Open: {url}"
        )
        if pin:
            msg += f"\n{pin}"
        return msg
    except Exception as e:
        logger.error("promote_brief failed: %s", e)
        return f"Error promoting brief: {e}"


@auto
@tool
async def list_briefs(kind: str = "", status: str = "active") -> str:
    """List published briefs/plans/specs with their reader URLs.

    Args:
        kind: Optional filter: brief | plan | spec.
        status: active (default), draft, archived, or all.
    """
    try:
        from src.dashboard.routes import briefs as br

        docs = br.list_docs(kind=kind or "", status=status or "active")
        if not docs:
            return "No published briefs."
        lines = []
        for d in docs[:50]:
            lines.append(
                f"- [{d.get('kind')}] {d.get('title')} → {br.reader_url(d.get('slug'))}"
                + (f" — {d.get('summary')}" if d.get("summary") else "")
            )
        return f"{len(docs)} doc(s):\n" + "\n".join(lines)
    except Exception as e:
        logger.error("list_briefs failed: %s", e)
        return f"Error listing briefs: {e}"


ALL_BRIEFS_TOOLS = [
    publish_brief,
    promote_brief,
    list_briefs,
]

TOOLS = ALL_BRIEFS_TOOLS
