"""Jules reinstall 0113 — onboard polish after name/door wins.

Jules 2026-07-15_0113:
  - brain-ack uses real Cove name (already fixed) but still said "bring the team online"
    while Stuart/Mercer channels were already visible
  - Set address / mobile cards collapsed mid-flow (Attention 30s refresh)
  - Connect on mobile false-completed after opening the join/command path
  - "N team agents missing today's tuning" alert on a fresh install
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOME_JS = (ROOT / "src/dashboard/static/js/home.js").read_text()
WAKE = (ROOT / "src/dashboard/routes/wake_thread.py").read_text()
WATCHER = (ROOT / "src/utils/watcher.py").read_text()
SCHED = (ROOT / "src/utils/scheduler.py").read_text()


def test_brain_ack_does_not_promise_to_bring_team_online():
    # Fallback / spoken copy must not promise a future team boot.
    bad = "Say the word and I'll bring the rest of the team online."
    # Quoted string form from the old fallback constant must be gone.
    assert ('"' + bad + '"') not in WAKE
    assert "The crew is already here with me" in WAKE
    assert "do NOT say you will bring the team online" in WAKE
    # Scrub patterns still mention the bad phrase so live model output is cleaned.
    assert "bring the rest of the team online" in WAKE


def test_brain_ack_scrubs_live_team_online_phrasing():
    assert "say the word" in WAKE.lower()
    assert r"bring the (?:rest of the )?team online" in WAKE or "team online" in WAKE


def test_setup_expansion_state_survives_refresh():
    assert "window._setupExpanded" in HOME_JS
    assert "_markSetupExpanded" in HOME_JS
    assert "_restoreSetupExpanded" in HOME_JS
    assert "Jules 0113: don't hard-refresh Attention while a setup form is open" in HOME_JS
    assert "setupBusy" in HOME_JS


def test_address_claim_marks_expanded():
    assert "_markSetupExpanded('claim_address'" in HOME_JS


def test_mobile_does_not_auto_complete_on_open_or_join():
    # Primary actions must not call ackOnboarding except the explicit mark-complete.
    # Open jules should only mark expanded, not ack.
    assert "onclick=\"_markSetupExpanded('device_jules', true);\"" in HOME_JS \
        or "Open jules" in HOME_JS
    assert "Mark complete" in HOME_JS
    assert "does <b>not</b> finish this step" in HOME_JS or "does not finish this step" in HOME_JS
    # getMeshKey must never POST onboarding/ack
    start = HOME_JS.index("async function getMeshKey")
    end = HOME_JS.index("async function ", start + 10)
    block = HOME_JS[start:end]
    assert "onboarding/ack" not in block
    assert "ackOnboarding" not in block
    assert "Jules 0113: this only reveals a join command" in block


def test_mobile_mark_complete_confirms():
    assert "Mark Connect on mobile complete only after your phone is on the mesh" in HOME_JS


def test_watcher_suppresses_tuning_missing_during_first_run():
    assert "_first_run_setup_incomplete" in WATCHER
    assert "_any_presence_has_intelligence" in WATCHER
    assert "Jules 0113" in WATCHER
    # Gate is consulted inside the check
    assert "if _first_run_setup_incomplete()" in WATCHER
    assert "if not await _any_presence_has_intelligence" in WATCHER


def test_boot_catchup_skips_first_run_setup():
    assert "_first_run_setup_incomplete" in SCHED
    assert "first-run setup incomplete" in SCHED


def test_first_run_setup_incomplete_pure():
    """Unit: no domain → incomplete; live domain without pending → complete."""
    import src.utils.watcher as w

    class _Cfg(dict):
        pass

    # Monkeypatch load_cove_config via the helper's import path.
    import src.config as cfg
    orig = cfg.load_cove_config

    try:
        cfg.load_cove_config = lambda: {}
        assert w._first_run_setup_incomplete() is True

        cfg.load_cove_config = lambda: {"domain": "hulton.lucidcove.org", "domain_live": True}
        assert w._first_run_setup_incomplete() is False

        cfg.load_cove_config = lambda: {
            "domain": "hulton.lucidcove.org",
            "domain_live": False,
            "pending_host_command": "python3 provision/set_domain.py --domain x",
        }
        assert w._first_run_setup_incomplete() is True
    finally:
        cfg.load_cove_config = orig
