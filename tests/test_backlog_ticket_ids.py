"""Board ids of every shape must pull + tick — '#NNNN', '#D44', and BARE 'JL-2'/'CF-5'.

_ticket_pattern force-prepended '#', turning a bare 'JL-2' into '#JL-2', so it never
matched the board's 'JL-2' and backlog_pull/backlog_update silently failed on those
ids (Stuart, board close-out). Now it matches an optional leading '#', case-insensitive,
whole-token. Runs under pytest and standalone (`python tests/test_backlog_ticket_ids.py`).
"""
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from src.tools.backlog_tools import find_ticket  # noqa: E402

BOARD = """## Now
- [ ] #1626 Set-address agent pop-in
- [ ] JL-2 Hide build-team agents from jules buttons
- [ ] JL-3 GPU-grant progress state
- [ ] CF-5 ltp-core vendor cleanup
- [ ] #1412 Backup not-set-up flag
- [ ] #D44 tuner wire
## Soon
- [ ] JL-20 unrelated, must not collide with JL-2
"""


def _line(tid):
    idx, _lane = find_ticket(BOARD, tid)
    return idx


def test_bare_alpha_ids_match():
    assert _line("JL-2") is not None
    assert _line("JL-3") is not None
    assert _line("CF-5") is not None


def test_hashed_ids_still_match():
    assert _line("#1626") is not None
    assert _line("#1412") is not None
    assert _line("#D44") is not None


def test_hash_optional_both_ways():
    # an id given without '#' resolves a board entry written with '#' and vice-versa
    assert _line("1626") == _line("#1626")
    assert _line("D44") == _line("#D44")


def test_case_insensitive():
    assert _line("jl-2") == _line("JL-2")


def test_whole_token_no_substring_collision():
    # JL-2 must resolve its OWN row, never JL-20
    assert _line("JL-2") == 2
    assert _line("JL-20") == 8
    assert _line("JL-2") != _line("JL-20")
    assert find_ticket(BOARD, "JL-99") == (None, None)


if __name__ == "__main__":
    tests = [
        ("bare_alpha_ids_match", test_bare_alpha_ids_match),
        ("hashed_ids_still_match", test_hashed_ids_still_match),
        ("hash_optional_both_ways", test_hash_optional_both_ways),
        ("case_insensitive", test_case_insensitive),
        ("whole_token_no_collision", test_whole_token_no_substring_collision),
    ]
    ok = True
    for name, fn in tests:
        try:
            fn()
            print("PASS -", name)
        except Exception as e:  # noqa: BLE001
            ok = False
            print("FAIL -", name, "::", repr(e))
    print("\nALL BACKLOG TICKET-ID TESTS PASSED" if ok else "\nSOME TESTS FAILED")
    sys.exit(0 if ok else 1)
