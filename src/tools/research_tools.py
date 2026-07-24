"""
Research Tools — web search, webpage extraction, documentation lookup.

All AUTO tier (read-only operations).

Environment variables:
  SEARXNG_URL — SearXNG instance URL.
    Compose default: http://{cove-id}-searxng:8080
    Legacy default: http://localhost:8888 (fails inside app unless searx is on host net)
"""

import asyncio
import json
import logging
import os
from src.env import env
from typing import Optional
from urllib.parse import quote_plus

from langchain_core.tools import tool

from src.tools.approval import auto

log = logging.getLogger("research_tools")

SEARXNG_URL = env("SEARXNG_URL", "http://localhost:8888").rstrip("/")


# =============================================================================
# SSRF guard + in-process fetch (#SEC2) — no shell, ever. Model-controlled URLs
# used to be f-string-interpolated into create_subprocess_shell → RCE via a
# single quote / $(...) in a fetched or injected URL. All fetching now goes
# through httpx with an allow/deny check that blocks private + link-local space
# (incl. the 169.254.169.254 cloud-metadata endpoint SECURITY.md names).
# =============================================================================

import ipaddress
import socket
from urllib.parse import urlparse

_SEARXNG_HOST = urlparse(SEARXNG_URL).hostname or "localhost"


def _host_is_blocked(hostname: str) -> bool:
    """True if hostname resolves to private/loopback/link-local/reserved space.

    The SearXNG host is explicitly allowed (it is an internal service we call on
    purpose); every other target must resolve to a public address.
    """
    if not hostname:
        return True
    if hostname == _SEARXNG_HOST:
        return False
    try:
        infos = socket.getaddrinfo(hostname, None)
    except Exception:
        return True  # unresolvable → block
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return True
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return True
    return False


def _url_allowed(url: str) -> tuple[bool, str]:
    try:
        parsed = urlparse(url)
    except Exception:
        return False, "unparseable URL"
    if parsed.scheme not in ("http", "https"):
        return False, f"scheme not allowed: {parsed.scheme or '(none)'}"
    if _host_is_blocked(parsed.hostname or ""):
        return False, "target host resolves to a private/reserved address (blocked)"
    return True, ""


async def _http_get(url: str, timeout: int = 15) -> tuple[bool, str]:
    """SSRF-checked in-process GET. Returns (ok, body_or_error). No shell."""
    ok, why = _url_allowed(url)
    if not ok:
        return False, f"Blocked: {why}"
    try:
        import httpx
    except Exception:
        return False, "httpx not available"
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
            resp = await client.get(url, headers={"User-Agent": "LucidCove-Research/1.0"})
            # Re-check the FINAL host after redirects (redirect-based SSRF).
            final_ok, final_why = _url_allowed(str(resp.url))
            if not final_ok:
                return False, f"Blocked after redirect: {final_why}"
            return True, resp.text
    except Exception as e:
        return False, f"fetch error: {e}"


# =============================================================================
# Web Search
# =============================================================================

@auto
@tool
async def web_search(query: str, num_results: int = 10, engines: str = "") -> str:
    """Search the web using the Cove SearXNG service.

    Args:
        query: Search query
        num_results: Number of results (default 10)
        engines: Comma-separated engine list (default: auto)
    """
    try:
        # Primary: Cove SearXNG (compose sibling). No second provider is wired yet —
        # when this fails, say so clearly; do not invent results.
        url = f"{SEARXNG_URL}/search?q={quote_plus(query)}&format=json"
        if engines:
            url += f"&engines={engines}"

        ok, result = await _http_get(url)
        if not ok:
            log.warning("web_search unavailable via %s: %s", SEARXNG_URL, result)
            return (
                f"Search unavailable: {result}\n"
                f"Backend: {SEARXNG_URL}\n"
                f"Query was: {query}\n"
                "No API fallback is configured. If SearXNG is not running on this Cove, "
                "stand it up (compose service searxng) or set SEARXNG_URL."
            )

        # HTML instead of JSON usually means formats.json is off in settings.yml.
        stripped = (result or "").lstrip()
        if stripped.startswith("<!") or stripped.lower().startswith("<html"):
            return (
                f"Search returned HTML instead of JSON from {SEARXNG_URL}. "
                "Enable json under search.formats in docker/searxng/settings.yml. "
                f"Query: {query}"
            )

        data = json.loads(result)
        results = data.get("results", [])[:num_results]

        if not results:
            return f"No results for: {query}"

        lines = [f"SEARCH: {query}\n"]
        for i, r in enumerate(results, 1):
            title = r.get("title", "")
            link = r.get("url", "")
            snippet = r.get("content", "")[:200]
            lines.append(f"{i}. {title}")
            lines.append(f"   {link}")
            if snippet:
                lines.append(f"   {snippet}")
            lines.append("")

        return "\n".join(lines)
    except json.JSONDecodeError:
        return f"Search returned invalid JSON from {SEARXNG_URL}. Query: {query}"
    except Exception as e:
        return f"Search error: {e}"


@auto
@tool
async def fetch_webpage(url: str, max_chars: int = 8000) -> str:
    """Fetch and extract text content from a webpage.

    Args:
        url: URL to fetch
        max_chars: Max characters to return (default 8000)
    """
    try:
        import re
        ok, html = await _http_get(url, timeout=15)
        if not ok:
            return f"Could not fetch {url}: {html}"
        text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            return f"Could not extract text from {url}"
        return f"WEBPAGE: {url}\n\n{text[:max_chars]}"
    except Exception as e:
        return f"Error fetching {url}: {e}"


@auto
@tool
async def search_docs(query: str, doc_path: str = "/data/projects") -> str:
    """Search local documentation and markdown files.

    Args:
        query: Search terms
        doc_path: Directory to search (default: /data/projects)
    """
    try:
        # #SEC2: arg-list exec, never shell=True — query/doc_path are model-controlled.
        # `--` stops option injection; grep treats query as a fixed string (-F).
        proc = await asyncio.create_subprocess_exec(
            "grep", "-rniIlF", "--include=*.md", "--include=*.txt", "--include=*.rst",
            "--", query, doc_path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        files = stdout.decode(errors="replace").strip()

        if not files:
            return f"No documentation matches for '{query}' in {doc_path}"

        file_list = files.split("\n")[:20]
        lines = [f"DOC SEARCH: '{query}' — {len(file_list)} files\n"]

        for f in file_list[:10]:
            proc = await asyncio.create_subprocess_exec(
                "grep", "-m", "2", "-iF", "--", query, f,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            snippet = stdout.decode(errors="replace").strip()[:200]
            lines.append(f"  {f}")
            if snippet:
                lines.append(f"    → {snippet}")
            lines.append("")

        return "\n".join(lines)
    except Exception as e:
        return f"Error searching docs: {e}"


# =============================================================================
# Helper
# =============================================================================

async def _fetch_url(url: str, timeout: int = 15) -> str:
    """Fetch a URL and return the response body. #SEC2: SSRF-checked httpx, no shell."""
    ok, body = await _http_get(url, timeout=timeout)
    return body if ok else f"Error: {body}"


# =============================================================================
# Tool Registry
# =============================================================================

ALL_RESEARCH_TOOLS = [
    web_search, fetch_webpage, search_docs,
]
TOOLS = ALL_RESEARCH_TOOLS  # alias for cove-core channels.py loader
