"""Books: a Presence whose own books are empty but who has shared books opens the shared ones."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "src/dashboard/static/books.html"
HARNESS = Path(__file__).resolve().parent / "books_shared_harness.js"


def test_page_has_the_shared_first_open():
    text = HTML.read_text()
    assert "function setupFallbackSpace(" in text
    assert 'id="setupShared"' in text and 'id="ownBooksLink"' in text
    assert "presenceFromUrl(location.search, state.spaces)" in text
    assert "#ownBooksLink{display:inline-block}" not in text.replace(" ", "")
    assert "#ownBooksLink:not(.hide){display:inline-block}" in text.replace(" ", "")
    assert 'id="btnAddStatements"' in text
    assert ">Add a line<" in text
    assert 'id="btnMore"' in text
    assert "Import category rules" in text
    assert "All transactions" in text
    assert "Read new statements" in text
    assert ">Account<" in text
    assert ">Ledger<" not in text
    assert "chrome-main button{flex: 1 1 100%}".replace(" ", "") in text.replace(" ", "")
    assert "Add your bank or card statements." in text
    assert 'id="dropHint"' in text
    assert "Statements go in Files > Bookkeeping > Drop >" in text
    assert "To review" in text
    assert "line.needs_review" in text


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_harness_runs_the_page_script():
    out = subprocess.run(["node", str(HARNESS), str(HTML)], capture_output=True, text=True, timeout=60)
    last = (out.stdout.strip().splitlines() or [""])[-1]
    assert out.returncode == 0 and last.startswith("HARNESS_OK"), out.stdout + out.stderr
