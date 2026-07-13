"""Tests for #D43: /backlog lane rendering — unknown lanes must not swallow into NOW.

Ensures that INTERACTIVE and BLOCKED sections get their own lanes,
and any unknown header maps to its own group (never merged into NOW).
"""

import pytest

from src.dashboard.routes.backlog import _parse_backlog


class TestLaneParsing:
    """Test that _parse_backlog recognizes all lane types."""

    def test_standard_lanes(self):
        """Standard lanes (now, soon, later, projects, completed) parse correctly."""
        board = """## Now
- [ ] **#1 Active ticket.** Desc `[dev]` *(test)*

## Soon
- [ ] **#2 Upcoming.** More desc `[board]` *(src)*

## Later
- [ ] **#3 Backlog.** Later desc `[ops]` *(src)*

## Projects
- [ ] **#4 Big project.** Needs spec `[plan]` *(idea)*

## Completed
- [x] **#5 Done.** Finished `[done]` *(src)*
"""
        lanes = _parse_backlog(board)
        assert len(lanes["now"]) == 1
        assert len(lanes["soon"]) == 1
        assert len(lanes["later"]) == 1
        assert len(lanes["projects"]) == 1
        assert len(lanes["done"]) == 1
        assert lanes["now"][0]["title"] == "Active ticket"

    def test_interactive_lane(self):
        """INTERACTIVE section is its own lane, not merged into NOW."""
        board = """## Now
- [ ] **#1 Build this.** Code `[dev]` *(test)*

## INTERACTIVE
- [ ] **#2 Needs live state.** With Chords `[test]` *(jules)*

## Soon
- [ ] **#3 After.** Later `[dev]` *(test)*
"""
        lanes = _parse_backlog(board)
        assert "now" in lanes
        assert "interactive" in lanes
        assert "soon" in lanes
        assert len(lanes["now"]) == 1
        assert len(lanes["interactive"]) == 1
        assert len(lanes["soon"]) == 1
        assert lanes["interactive"][0]["title"] == "Needs live state"

    def test_blocked_lane(self):
        """BLOCKED section is its own lane, not merged into NOW."""
        board = """## Now
- [ ] **#1 Active.** Working `[dev]` *(test)*

## BLOCKED
- [ ] **#2 Waiting on API.** External dep `[ext]` *(jules)*

## Later
- [ ] **#3 Future.** Later work `[dev]` *(test)*
"""
        lanes = _parse_backlog(board)
        assert "now" in lanes
        assert "blocked" in lanes
        assert "later" in lanes
        assert len(lanes["now"]) == 1
        assert len(lanes["blocked"]) == 1
        assert len(lanes["later"]) == 1
        assert lanes["blocked"][0]["title"] == "Waiting on API"

    def test_unknown_header_not_now(self):
        """Unknown headers get their own lane key, never merge into NOW."""
        board = """## Now
- [ ] **#1 Real now.** Active `[dev]` *(test)*

## MysteryLane
- [ ] **#2 Unknown.** Mystery item `[tag]` *(src)*

## Soon
- [ ] **#3 Real soon.** Upcoming `[dev]` *(test)*
"""
        lanes = _parse_backlog(board)
        # MysteryLane should NOT be merged into now
        assert "now" in lanes
        assert len(lanes["now"]) == 1
        assert lanes["now"][0]["title"] == "Real now"
        # MysteryLane should be its own key (normalized)
        assert "mysterylane" in lanes
        assert len(lanes["mysterylane"]) == 1
        assert lanes["mysterylane"][0]["title"] == "Unknown"

    def test_interactive_items_not_in_now(self):
        """Interactive items are strictly separated from NOW items."""
        board = """## Now
- [ ] **#1 Build.** Code `[dev]` *(test)*

## INTERACTIVE
- [ ] **#2 Live debug.** Needs Chords `[test]` *(jules)*
- [ ] **#3 Verify fix.** Live check `[test]` *(jules)*

## Now
- [ ] **#4 More build.** Continued `[dev]` *(test)*
"""
        lanes = _parse_backlog(board)
        # Both NOW sections should be combined
        assert len(lanes["now"]) == 2
        assert lanes["now"][0]["title"] == "Build"
        assert lanes["now"][1]["title"] == "More build"
        # Interactive stays separate
        assert len(lanes["interactive"]) == 2
        assert lanes["interactive"][0]["title"] == "Live debug"
        assert lanes["interactive"][1]["title"] == "Verify fix"

    def test_case_insensitive_headers(self):
        """Lane headers are case-insensitive."""
        board = """## NOW
- [ ] **#1 Upper.** `[dev]` *(test)*

## interactive
- [ ] **#2 Lower.** `[test]` *(src)*

## Blocked
- [ ] **#3 Mixed.** `[tag]` *(src)*
"""
        lanes = _parse_backlog(board)
        assert "now" in lanes
        assert "interactive" in lanes
        assert "blocked" in lanes
