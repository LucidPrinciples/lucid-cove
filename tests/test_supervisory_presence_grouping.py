# #D25 (b) — the supervisory chat view must group manager threads by a STABLE presence id
# (the account UUID), merging ghost keys and quarantining threads that resolve to no
# current account. Pure-logic tests of the resolution helper (no DB).
from src.dashboard.routes.memory import resolve_presence_key, ORPHANED_PRESENCE_KEY

ACCT = "11111111-1111-1111-1111-111111111111"
ACCT2 = "22222222-2222-2222-2222-222222222222"

ACCOUNTS_BY_ID = {
    ACCT: {"id": ACCT, "display_name": "Alex", "active": True},
    ACCT2: {"id": ACCT2, "display_name": "Sam", "active": True},
    "dead": {"id": "dead", "display_name": "Gone", "active": False},
}
ACCOUNTS_BY_NAME = {"alex": ACCOUNTS_BY_ID[ACCT], "sam": ACCOUNTS_BY_ID[ACCT2]}


def test_explicit_presence_id_wins():
    key, name = resolve_presence_key({"presence_id": ACCT}, "whatever", ACCOUNTS_BY_ID, ACCOUNTS_BY_NAME)
    assert key == ACCT and name == "Alex"


def test_agent_id_as_account_resolves():
    # manager threads are scoped by the presence account UUID = agent_id
    key, name = resolve_presence_key({}, ACCT2, ACCOUNTS_BY_ID, ACCOUNTS_BY_NAME)
    assert key == ACCT2 and name == "Sam"


def test_ghost_keys_merge_to_one_presence():
    # three legacy shapes for the SAME person must collapse to one stable key
    by_pid = resolve_presence_key({"presence_id": ACCT}, "agent", ACCOUNTS_BY_ID, ACCOUNTS_BY_NAME)
    by_aid = resolve_presence_key({}, ACCT, ACCOUNTS_BY_ID, ACCOUNTS_BY_NAME)
    by_name = resolve_presence_key({"operator_name": "Alex"}, "agent", ACCOUNTS_BY_ID, ACCOUNTS_BY_NAME)
    assert by_pid[0] == by_aid[0] == by_name[0] == ACCT


def test_unresolvable_is_quarantined_not_labeled_raw_uuid():
    # a deleted-presence / legacy container agent_id → Orphaned, never a raw UUID label
    key, name = resolve_presence_key({}, "6119abcd-uuid-legacy", ACCOUNTS_BY_ID, ACCOUNTS_BY_NAME)
    assert key == ORPHANED_PRESENCE_KEY and name == "Orphaned"


def test_inactive_account_does_not_resolve():
    key, name = resolve_presence_key({"presence_id": "dead"}, "dead", ACCOUNTS_BY_ID, ACCOUNTS_BY_NAME)
    assert key == ORPHANED_PRESENCE_KEY


def test_ambiguous_operator_name_does_not_mismerge():
    # two active accounts share a display name → name mapping is None (ambiguous) → Orphaned
    amb = {"alex": None}
    key, name = resolve_presence_key({"operator_name": "Alex"}, "x", ACCOUNTS_BY_ID, amb)
    assert key == ORPHANED_PRESENCE_KEY


def test_operator_name_only_no_account_is_orphaned():
    key, _ = resolve_presence_key({"operator_name": "Nobody"}, "x", ACCOUNTS_BY_ID, ACCOUNTS_BY_NAME)
    assert key == ORPHANED_PRESENCE_KEY
