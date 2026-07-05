"""
test_ledger.py (#128) — pure-logic tests for the credit ledger.

No DB needed: exercises the planning functions (balanced double-entry, fee/split math,
micro-transaction precision, the no-LP-negative guard, validation). Runs under pytest
or standalone (`python3 tests/test_ledger.py`).
Spec: LP-Vault/Reference/commerce-credit-economy-spec.md.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.economy.ledger import (  # noqa: E402
    plan_topup, plan_purchase, plan_payout, plan_commission,
    plan_hold, plan_release, plan_refund, usd_to_credits, credits_to_usd,
    SYS_LP_FEES, SYS_ISSUED, SYS_ESCROW,
)


def _net(entries):
    return sum(e.delta for e in entries)


def _by(entries, handle):
    return sum(e.delta for e in entries if e.handle == handle)


def test_usd_credit_conversion():
    assert usd_to_credits(100) == 10000   # $100 = 10,000 credits ($0.01 each)
    assert usd_to_credits(1) == 100       # $1 = 100 credits
    assert usd_to_credits(0.50) == 50
    assert credits_to_usd(10000) == 100.0


def test_topup_balanced():
    e = plan_topup("sam", 2000)            # $20 top-up
    assert _net(e) == 0
    assert _by(e, "sam") == 2000
    assert _by(e, SYS_ISSUED) == -2000


def test_purchase_with_two_level_affiliate():
    # $100 sale, 10% fee, L1 30% / L2 10% of the fee.
    e = plan_purchase(buyer_handle="buyer", seller_handle="seller", gross=10000,
                      l1_handle="a", l2_handle="b")
    assert _net(e) == 0                    # double-entry invariant
    assert _by(e, "buyer") == -10000
    assert _by(e, "seller") == 9000        # seller keeps 90%
    assert _by(e, "a") == 300              # L1 = 30% of the $10 fee = $3
    assert _by(e, "b") == 100              # L2 = 10% of the fee = $1
    assert _by(e, SYS_LP_FEES) == 600      # LP keeps the rest of its fee = $6


def test_purchase_no_affiliate_lp_keeps_full_fee():
    e = plan_purchase(buyer_handle="buyer", seller_handle="seller", gross=10000)
    assert _net(e) == 0
    assert _by(e, "seller") == 9000
    assert _by(e, SYS_LP_FEES) == 1000     # no referral → LP keeps the whole 10% fee


def test_micro_dollar_subscription():
    # The whole point: a $1/mo mirror settles cleanly in integer credits.
    e = plan_purchase(buyer_handle="buyer", seller_handle="seller", gross=100,
                      l1_handle="a", l2_handle="b")
    assert _net(e) == 0
    assert _by(e, "seller") == 90          # $0.90
    assert _by(e, "a") == 3                # $0.03
    assert _by(e, "b") == 1                # $0.01
    assert _by(e, SYS_LP_FEES) == 6        # $0.06 — no Stripe per cycle


def test_affiliate_split_cannot_exceed_fee():
    # Misconfigured rates that would push LP negative must raise, not silently lose money.
    try:
        plan_purchase(buyer_handle="b", seller_handle="s", gross=10000,
                      l1_handle="a", l1_rate=0.8, l2_handle="c", l2_rate=0.5)
    except ValueError:
        return
    raise AssertionError("expected ValueError when affiliate split exceeds the fee")


def test_commission_external_sale():
    # A $500 Stripe-direct sale → LP fee $50 (5000 credits). Affiliate paid in LPC,
    # issued against the real-cash fee. L1 30% = 1500, L2 10% = 500.
    e = plan_commission(l1_handle="a", l2_handle="b", base=5000)
    assert _net(e) == 0
    assert _by(e, "a") == 1500
    assert _by(e, "b") == 500
    assert _by(e, SYS_ISSUED) == -2000


def test_commission_empty_without_referrer():
    assert plan_commission(l1_handle=None, l2_handle=None, base=5000) == []
    assert plan_commission(base=0) == []


def test_hire_hold_then_release():
    # Hire $100 service: hold buyer's credits, then release to seller on delivery with the
    # same split as a sale (seller 90, LP 6, affiliates 3+1).
    hold = plan_hold("buyer", 10000)
    assert _net(hold) == 0 and _by(hold, "buyer") == -10000 and _by(hold, SYS_ESCROW) == 10000
    rel = plan_release(seller_handle="seller", amount=10000, l1_handle="a", l2_handle="b")
    assert _net(rel) == 0
    assert _by(rel, SYS_ESCROW) == -10000
    assert _by(rel, "seller") == 9000
    assert _by(rel, "a") == 300 and _by(rel, "b") == 100
    assert _by(rel, SYS_LP_FEES) == 600
    # whole engagement conserves value: escrow nets to zero across hold+release
    assert _by(hold + rel, SYS_ESCROW) == 0


def test_hire_hold_then_refund():
    hold = plan_hold("buyer", 5000)
    refund = plan_refund("buyer", 5000)
    assert _net(refund) == 0
    assert _by(refund, SYS_ESCROW) == -5000 and _by(refund, "buyer") == 5000
    # cancelled engagement: buyer made whole, escrow back to zero
    assert _by(hold + refund, "buyer") == 0
    assert _by(hold + refund, SYS_ESCROW) == 0


def test_release_no_affiliate_lp_keeps_fee():
    rel = plan_release(seller_handle="seller", amount=10000)
    assert _net(rel) == 0
    assert _by(rel, "seller") == 9000 and _by(rel, SYS_LP_FEES) == 1000


def test_payout_balanced():
    e = plan_payout("seller", 9000)
    assert _net(e) == 0
    assert _by(e, "seller") == -9000
    assert _by(e, SYS_ISSUED) == 9000


def test_rejects_nonpositive():
    for fn, args in ((plan_topup, ("x", 0)), (plan_payout, ("x", -5))):
        try:
            fn(*args)
        except ValueError:
            continue
        raise AssertionError(f"{fn.__name__} should reject non-positive amounts")


def test_full_lifecycle_conserves_value():
    # topup → purchase → payout: across all txns, every wallet delta still nets to zero
    # (no credits created or destroyed except at the issued/liability boundary).
    all_entries = []
    all_entries += plan_topup("buyer", 10000)
    all_entries += plan_purchase(buyer_handle="buyer", seller_handle="seller",
                                 gross=10000, l1_handle="a", l2_handle="b")
    all_entries += plan_payout("seller", 9000)
    assert _net(all_entries) == 0
    # buyer spent everything; issued reflects net credits still in circulation
    assert _by(all_entries, "buyer") == 0
    assert _by(all_entries, "seller") == 0      # earned 9000, cashed out 9000
    assert _by(all_entries, SYS_ISSUED) == -1000  # 10000 minted - 9000 cashed out


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} ledger tests passed")
