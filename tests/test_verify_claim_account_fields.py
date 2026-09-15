"""verify-claim returns the connected account fields for Help labels."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REG = ROOT / "src" / "dashboard" / "routes" / "registry.py"


def test_verify_claim_returns_email_and_display_name():
    src = REG.read_text()
    assert "SELECT username, email, display_name FROM accounts" in src
    assert '"email": acct.get("email")' in src
    assert '"name": acct.get("display_name")' in src
