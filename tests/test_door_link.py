"""
batch-10 #2 — door links must survive token rotation.

`/p/` sign-in tokens are stored ONLY as hashes (auth_sessions.token_hash), so a
raw token stamped into cove.yaml (operator_token) can never be validated back and
401s the moment it rotates or was minted registrar-side. The done-cards therefore
must NOT build a `/p/{operator_token}` door from cove.yaml; they link the Cove root,
and the reliable current signed-in link is minted fresh from the live token store in
Settings → Devices ("My door link").

This is a pure text scan (no imports of app code / DB) that locks the fix so a future
edit can't quietly re-introduce the stale-token door.
"""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "dashboard" / "routes"

# A door built as .../p/{...operator_token...} — the exact stale-token pattern.
STALE_DOOR = re.compile(r"/p/\{[^}]*operator_token[^}]*\}")


def _door_lines(path: Path):
    hits = []
    for i, line in enumerate(path.read_text().splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue  # comments may name the retired pattern to explain the fix
        if STALE_DOOR.search(line):
            hits.append(f"{path.name}:{i}: {line.strip()}")
    return hits


def test_domain_route_has_no_stale_operator_token_door():
    hits = _door_lines(SRC / "domain.py")
    assert not hits, "domain.py still builds a /p/{operator_token} door:\n" + "\n".join(hits)


def test_onboarding_route_has_no_stale_operator_token_door():
    hits = _door_lines(SRC / "onboarding.py")
    assert not hits, "onboarding.py still builds a /p/{operator_token} door:\n" + "\n".join(hits)
