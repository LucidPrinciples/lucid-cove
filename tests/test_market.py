"""
test_market.py (#169) — pure-logic tests for credit-rail affiliate attribution.

The money math (plan_purchase) is covered in test_ledger.py; here we test the chain
resolution that decides WHO gets the L1/L2 split — walked from the seller via the
registry referred_by edge, with self/cycle guards. No DB.
Run: pytest, or `python3 tests/test_market.py`.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.economy.market import affiliate_chain  # noqa: E402


def test_two_level_chain():
    # carol recruited bob (the seller); alice recruited carol.
    rb = {"seller": "carol", "carol": "alice"}
    assert affiliate_chain("seller", rb) == ("carol", "alice")


def test_one_level_chain():
    assert affiliate_chain("seller", {"seller": "carol"}) == ("carol", None)


def test_no_referrer():
    assert affiliate_chain("seller", {}) == (None, None)


def test_self_referral_guarded():
    # a handle listed as its own referrer must not pay itself
    assert affiliate_chain("seller", {"seller": "seller"}) == (None, None)


def test_cycle_guarded():
    # carol → seller cycle: L2 must not resolve back to the seller or to L1
    assert affiliate_chain("seller", {"seller": "carol", "carol": "seller"}) == ("carol", None)
    assert affiliate_chain("seller", {"seller": "carol", "carol": "carol"}) == ("carol", None)


def test_empty_referrer_string_is_none():
    assert affiliate_chain("seller", {"seller": ""}) == (None, None)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)}/{len(tests)} market tests passed")
