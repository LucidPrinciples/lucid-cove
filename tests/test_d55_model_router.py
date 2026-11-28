"""Retired #D55 scoring tests — hops are Cove settings only. See test_cove_hops.py."""

from src.models.router import plan_hops


def test_plan_hops_still_importable():
    assert callable(plan_hops)
