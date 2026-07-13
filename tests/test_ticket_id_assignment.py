"""Tests for #D42: atomic ticket ID assignment.

Ensures that jules processing and any automated item creation correctly
finds the next free ID by scanning BOTH plain numbers (#1626) and D-prefixed
(#D38) tickets. Prevents collisions when agents or processes mint IDs.
"""

import pytest

from src.dashboard.routes.jules_process import _append_items


class TestAppendItemsIdAssignment:
    """Test that _append_items finds the true max ID across all formats."""

    def test_plain_numbers_only(self):
        """With only plain numbers, assigns sequentially."""
        board = "## Now\n- [ ] **#1 First.**\n- [ ] **#2 Second.**"
        text, nums = _append_items(board, [{"title": "Third", "desc": "x", "lane": "now"}], "test")
        assert "#3 Third" in text
        assert nums == [3]

    def test_d_prefixed_numbers(self):
        """D-prefixed IDs are included in max calculation."""
        board = "## Now\n- [ ] **#D38 Some ticket.**\n- [ ] **#1626 Another.**"
        text, nums = _append_items(board, [{"title": "New item", "desc": "x", "lane": "now"}], "test")
        # Max is 1626, so next is 1627
        assert "#1627 New item" in text
        assert nums == [1627]

    def test_mixed_ids(self):
        """Mix of D-prefixed and plain — max is found correctly."""
        board = "## Now\n- [ ] **#D50 Big.**\n- [ ] **#D52 Bigger.**\n- [ ] **#3 Small.**"
        text, nums = _append_items(board, [{"title": "Next", "desc": "x", "lane": "now"}], "test")
        # Max is 52 (from D52), so next is 53
        assert "#53 Next" in text
        assert nums == [53]

    def test_empty_board(self):
        """Empty board starts at 1."""
        board = "## Now\n"
        text, nums = _append_items(board, [{"title": "First", "desc": "x", "lane": "now"}], "test")
        assert "#1 First" in text
        assert nums == [1]

    def test_does_not_reuse_existing(self):
        """Never assigns an ID that already exists."""
        board = "## Now\n- [ ] **#D38 Taken.**\n- [ ] **#39 Also taken.**"
        text, nums = _append_items(board, [{"title": "New", "desc": "x", "lane": "now"}], "test")
        # Max is 39, next is 40 — never 38
        assert "#40 New" in text
        assert "#38 New" not in text
        assert nums == [40]

    def test_multiple_items_sequential(self):
        """Multiple new items get sequential IDs."""
        board = "## Now\n- [ ] **#5 Existing.**"
        items = [
            {"title": "A", "desc": "x", "lane": "now"},
            {"title": "B", "desc": "x", "lane": "now"},
        ]
        text, nums = _append_items(board, items, "test")
        assert "#6 A" in text
        assert "#7 B" in text
        assert nums == [6, 7]


class TestIdExtractionEdgeCases:
    """Edge cases in ID extraction."""

    def test_id_in_description_not_confused(self):
        """Numbers in descriptions don't affect ID assignment."""
        board = "## Now\n- [ ] **#1 Title.** Desc with #999 number."
        text, nums = _append_items(board, [{"title": "Next", "desc": "x", "lane": "now"}], "test")
        # Only the ticket ID (#1) counts, not #999 in description
        assert "#2 Next" in text

    def test_id_at_end_of_line(self):
        """IDs at end of line are captured."""
        board = "## Now\n- [ ] **#99 Last.**"
        text, nums = _append_items(board, [{"title": "Next", "desc": "x", "lane": "now"}], "test")
        assert "#100 Next" in text
