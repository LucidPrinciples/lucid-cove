"""
Research Tools — web search, webpage extraction, documentation lookup.

All AUTO tier (read-only operations).

Environment variables:
  SEARXNG_URL — SearXNG instance URL (default: http://localhost:8888)
"""

import asyncio
import json
import os
from src.env import env
from typing import Optional
from urllib.parse import quote_plus

from langchain_core.tools import tool

from src.tools.approval import auto

SEARXNG_URL = env("SEARXNG_URL", "http://localhost:8888")


# =============================================================================
# Web Search
# =============================================================================

@auto
@tool
async def web_search(query: str, num_results: int = 10, engines: str = "") -> str:
    """Search the web using SearXNG (or Bing fallback).

    Args:
        query: Search query
        num_results: Number of results (default 10)
        engines: Comma-separated engine list (default: auto)
    """
    try:
        # Try SearXNG first
        url = f"{SEARXNG_URL}/search?q={quote_plus(query)}&format=json"
        if engines:
            url += f"&engines={engines}"

        result = await _fetch_url(url)
        if result.startswith("Error"):
            return f"Search unavailable: {result}\nQuery was: {query}"

        data = json.loads(result)
        results = data.get("results", [])[:num_results]

        if not results:
            return f"No results for: {query}"

        lines = [f"SEARCH: {query}\n"]
        for i, r in enumerate(results, 1):
            title = r.get("title", "")
            url = r.get("url", "")
            snippet = r.get("content", "")[:200]
            lines.append(f"{i}. {title}")
            lines.append(f"   {url}")
            if snippet:
                lines.append(f"   {snippet}")
            lines.append("")

        return "\n".join(lines)
    except json.JSONDecodeError:
        return f"Search returned invalid response. Query: {query}"
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
        # Use Python's readability-like extraction
        cmd = (
            f"python3 -c \""
            f"import urllib.request; "
            f"r = urllib.request.urlopen('{url}', timeout=15); "
            f"html = r.read().decode('utf-8', errors='replace'); "
            f"import re; "
            f"text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL); "
            f"text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL); "
            f"text = re.sub(r'<[^>]+>', ' ', text); "
            f"text = re.sub(r'\\\\s+', ' ', text).strip(); "
            f"print(text[:{max_chars}])"
            f"\""
        )
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=20)
        text = stdout.decode(errors="replace").strip()
        if not text:
            err = stderr.decode(errors="replace").strip()
            return f"Could not extract text from {url}: {err}"
        return f"WEBPAGE: {url}\n\n{text}"
    except asyncio.TimeoutError:
        return f"Fetch timed out for {url}"
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
        cmd = f"grep -rnil --include='*.md' --include='*.txt' --include='*.rst' '{query}' '{doc_path}' 2>/dev/null | head -20"
        proc = await asyncio.create_subprocess_shell(
            cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        files = stdout.decode(errors="replace").strip()

        if not files:
            return f"No documentation matches for '{query}' in {doc_path}"

        file_list = files.split("\n")
        lines = [f"DOC SEARCH: '{query}' — {len(file_list)} files\n"]

        for f in file_list[:10]:
            # Get a snippet from each file
            snippet_cmd = f"grep -m 2 -i '{query}' '{f}' 2>/dev/null"
            proc = await asyncio.create_subprocess_shell(
                snippet_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
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
    """Fetch a URL and return the response body."""
    cmd = f"python3 -c \"import urllib.request; r = urllib.request.urlopen('{url}', timeout={timeout}); print(r.read().decode('utf-8', errors='replace'))\""
    proc = await asyncio.create_subprocess_shell(
        cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout + 5)
    except asyncio.TimeoutError:
        proc.kill()
        return "Error: Request timed out"

    if proc.returncode != 0:
        return f"Error: {stderr.decode(errors='replace').strip()}"
    return stdout.decode(errors="replace").strip()


# =============================================================================
# Tool Registry
# =============================================================================

ALL_RESEARCH_TOOLS = [
    web_search, fetch_webpage, search_docs,
]
TOOLS = ALL_RESEARCH_TOOLS  # alias for cove-core channels.py loader
