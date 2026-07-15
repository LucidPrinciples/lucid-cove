"""Jules reinstall 0225–0234 — onboard progression + mobile clarity.

Jules 2026-07-15_0225:
  - Only Add intelligence + Set address should be open first
  - Backup / team tuning / mobile must wait until foundation is done

Jules 2026-07-15_0229:
  - After mark-live, stay on Presences board with Open my Cove + Setup Compute
  - Compute before mobile; backup + tune after compute

Jules 2026-07-15_0231:
  - Compute choice must soft-refresh so checkmark appears without manual nav
  - Mobile action buttons need spacing separate from body text

Jules 2026-07-15_0234:
  - Join code provenance must be obvious (code comes from Get a join code)
  - Mobile instructions rewritten for clarity
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOME_JS = (ROOT / "src/dashboard/static/js/home.js").read_text()
ONBOARD = (ROOT / "src/dashboard/routes/onboarding.py").read_text()


def test_foundation_gates_later_steps():
    # Openers always available; later steps gated.
    assert "_foundation = bool(intel_done and address_done)" in ONBOARD
    assert "_after_compute = bool(_foundation and compute_done)" in ONBOARD
    # Compute after both openers
    assert '"available": _foundation' in ONBOARD or "'available': _foundation" in ONBOARD
    # Backup / mobile / team-tune after compute
    assert "available\": _after_compute" in ONBOARD or "available': _after_compute" in ONBOARD
    # Team-tune no longer opens on intelligence alone
    assert "available\": bool(intel_done and is_admin)" not in ONBOARD
    assert '_s["available"] = bool(_after_compute and is_admin)' in ONBOARD


def test_backup_not_open_on_intelligence_alone():
    # protect_backup must use _after_compute, not intel_done alone
    start = ONBOARD.index('"id": "protect_backup"')
    block = ONBOARD[start:start + 800]
    assert "_after_compute" in block
    assert "available\": intel_done" not in block


def test_mobile_after_compute():
    start = ONBOARD.index('"id": "device_jules"')
    block = ONBOARD[start:start + 500]
    assert "_after_compute" in block


def test_mark_live_stays_on_presences_board():
    # No full-page reload as the happy path after mark-live
    start = HOME_JS.index("async function _addrRanCommand")
    end = HOME_JS.index("async function _openMyCove", start)
    block = HOME_JS[start:end]
    assert "loadCoveAdminPresences" in block
    assert "loadHomeApprovals" in block
    # Soft refresh preferred; location.reload only as fallback
    assert "Jules 0229" in block
    assert "stay on" in block.lower() or "Stay on" in block


def test_compute_soft_refreshes_after_ack():
    start = HOME_JS.index("async function setCompute")
    end = HOME_JS.index("async function loadSiteDiff", start)
    block = HOME_JS[start:end]
    assert "ackOnboarding('set_compute')" in block
    assert "loadCoveAdminPresences" in block
    assert "Jules 0231" in block


def test_mobile_copy_explains_join_code_source():
    assert "Get a join code" in HOME_JS
    assert "where the code comes from" in HOME_JS or "from this button" in HOME_JS
    assert "Your join code (from this button" in HOME_JS
    assert "Paste this into the Tailscale app" in HOME_JS


def test_mobile_actions_separated_from_body():
    # Action row has border-top spacing; mark-complete is its own block
    assert "Mark Connect on mobile complete" in HOME_JS
    assert "border-top:1px solid var(--border)" in HOME_JS
    # Primary CTA is Get a join code (not buried laptop-only label)
    assert "onclick=\"getMeshKey(this)\">Get a join code</button>" in HOME_JS


def test_get_mesh_key_surfaces_bare_code():
    start = HOME_JS.index("async function getMeshKey")
    end = HOME_JS.index("function _domModeChange", start)
    block = HOME_JS[start:end]
    assert "d.key" in block
    assert "does not finish this step" in HOME_JS.lower() or "does <b>not</b> finish this step" in HOME_JS
    # Still must never ack
    assert "onboarding/ack" not in block
    assert "ackOnboarding" not in block


def test_help_modal_matches_progression():
    assert "Open first (either order; address listed first)" in HOME_JS
    assert "After compute" in HOME_JS
    assert "Set your address." in HOME_JS
    assert "Connect" in HOME_JS  # Matrix Connect callout after door is live


def test_ack_soft_refreshes_without_full_reload():
    # Skip / Got it must await loadHomeApprovals so cards clear without refresh.
    start = HOME_JS.index("async function ackOnboarding")
    end = HOME_JS.index("async function setCompute", start)
    block = HOME_JS[start:end]
    assert "await loadHomeApprovals" in block
    assert "location.reload" not in block
