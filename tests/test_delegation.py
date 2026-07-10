"""Delegation pure-logic tests — agent-name resolution and brief composition.
The channel/graph plumbing is exercised live; the judgment lives here."""

from src.tools.delegation_tools import resolve_agent, compose_brief

KNOWN = {"stuart", "mercer", "archimedes", "arthur", "gabe", "ezra",
         "julian", "iris", "vera", "soren"}


def test_exact_key_resolves():
    assert resolve_agent("archimedes", KNOWN) == "archimedes"


def test_registry_suffix_resolves():
    assert resolve_agent("archimedes-clearfield", KNOWN) == "archimedes"


def test_dotted_handle_resolves():
    assert resolve_agent("archimedes.clearfield", KNOWN) == "archimedes"


def test_case_and_whitespace_normalized():
    assert resolve_agent("  Archimedes ", KNOWN) == "archimedes"


def test_unknown_agent_is_none():
    assert resolve_agent("bartholomew", KNOWN) is None
    assert resolve_agent("", KNOWN) is None


def test_brief_carries_the_gate_rules():
    msg = compose_brief("archimedes", "Build the pre-queue validation.", "#D15", 42)
    assert "#D15" in msg
    assert "task #42" in msg
    assert "archimedes/" in msg              # branch naming per role scope
    assert "NOT done" in msg                 # pushed != done
    assert "create_github_pr" in msg         # the gated path, never raw shell
    assert "exceed your scope" in msg        # escalation stays explicit


def test_brief_without_ticket_ref():
    msg = compose_brief("gabe", "Research X thoroughly and log findings.", "", 7)
    assert "task #7" in msg
    assert "()" not in msg
