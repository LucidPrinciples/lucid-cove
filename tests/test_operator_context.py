"""batch8 #10 / CF-99 — operator Context slice assembly, budgeting, append."""
import asyncio

from src.agents.operator_context import (
    assemble_context_slice, load_operator_context_slice, append_working_memory,
    PER_FILE_BUDGET,
)


def test_assembles_present_files_in_order():
    out = assemble_context_slice({
        "about.md": "Jordan builds furniture.",
        "preferences.md": "Terse. No emoji.",
        "working-memory.md": "Shipping the catalog.",
    })
    assert "Operator Context" in out
    assert out.index("About the person") < out.index("Their preferences") < out.index("Working memory")


def test_empty_files_yield_empty_slice():
    assert assemble_context_slice({}) == ""
    assert assemble_context_slice({"about.md": "   ", "preferences.md": ""}) == ""


def test_per_file_budget_clips():
    big = "x" * (PER_FILE_BUDGET + 500)
    out = assemble_context_slice({"about.md": big})
    assert "truncated" in out
    assert len(out) < PER_FILE_BUDGET + 300


def test_missing_files_are_skipped_not_errored():
    out = assemble_context_slice({"preferences.md": "Be brief."})
    assert "Their preferences" in out
    assert "About the person" not in out


def test_load_slice_via_reader_graceful_on_error():
    async def _reader(path):
        if path.endswith("about.md"):
            return "About text."
        if path.endswith("preferences.md"):
            raise RuntimeError("404")  # dropped, not fatal
        return None

    out = asyncio.run(load_operator_context_slice(_reader))
    assert "About text." in out
    assert "Their preferences" not in out  # errored file skipped


def test_append_working_memory_adds_line():
    assert append_working_memory("line1", "line2") == "line1\nline2"
    assert append_working_memory("", "first") == "first"
    assert append_working_memory("x", "") == "x"  # empty note is a no-op


def test_append_working_memory_bounded_drops_oldest():
    existing = "\n".join(f"line{i}" for i in range(100))
    out = append_working_memory(existing, "NEWEST", max_chars=40)
    assert "NEWEST" in out           # newest kept
    assert "line0" not in out        # oldest dropped
    assert len(out) <= 40 + len("NEWEST")


def test_build_system_prompt_includes_slice(monkeypatch):
    # The prompt param surfaces the slice; absent → no section.
    from src.agents import identity
    monkeypatch.setattr(identity, "load_agents_config",
                        lambda: {"a": {"name": "Aria", "archetype": "Guide", "role": "help",
                                       "status": "active"}})
    monkeypatch.setattr(identity, "load_persona", lambda _a: "")
    with_ctx = identity.build_system_prompt("a", operator_context="## Operator Context\nBe brief.")
    without = identity.build_system_prompt("a")
    assert "Operator Context" in with_ctx
    assert "Operator Context" not in without
