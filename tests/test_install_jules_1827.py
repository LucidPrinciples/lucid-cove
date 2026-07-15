"""Jules 1825 + 1827 — post-address Open my Cove SSL wait + Attention nudge.

Jules 2026-07-15_1825:
  After Add intelligence + Open chat, brain-ack / done-line no longer pointed the
  operator back to Attention for the next setup card (set address). Restore that.

Jules 2026-07-15_1827 (Calhoun install):
  Host resolve was green (system + DoH → mesh IP) but Open my Cove hit
  ERR_SSL_PROTOCOL_ERROR because the TLS cert was still issuing. Product must
  call out that Open my Cove is a sign-on link and first open can take a minute
  (wait + Reload) — not imply the door is browser-ready the instant mark-live lands.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOME_JS = (ROOT / "src/dashboard/static/js/home.js").read_text()
WAKE = (ROOT / "src/dashboard/routes/wake_thread.py").read_text()
MESH = (ROOT / "MESH.md").read_text()


def test_open_my_cove_done_line_warns_ssl_wait():
    assert "First open can take a minute" in HOME_JS
    assert "ERR_SSL_PROTOCOL_ERROR" in HOME_JS
    assert "sign-on link" in HOME_JS
    # Prominent callout on the claim_address done-line path
    assert "can't provide a secure connection" in HOME_JS


def test_mark_live_confirm_mentions_ssl_lag():
    assert "ERR_SSL_PROTOCOL_ERROR" in HOME_JS
    assert "30–90s" in HOME_JS or "30-90s" in HOME_JS
    # Confirm dialog before address-live
    assert "Mark the address live and refresh setup?" in HOME_JS


def test_host_command_card_mentions_cert_lag():
    assert "another 30–90 seconds" in HOME_JS or "30–90 seconds" in HOME_JS
    assert "host_resolve_failed" in HOME_JS


def test_fully_live_panel_warns_ssl():
    # Hub-managed fully_live path also gets the prominent cert warning
    assert "First open can take a minute" in HOME_JS
    assert "Open my Cove" in HOME_JS
    assert "sign-on link" in HOME_JS


def test_intelligence_done_line_points_back_to_attention():
    # Jules 1825 — after Open chat, explicit pointer back to Attention
    assert "then go back to Attention for the next setup step" in HOME_JS


def test_brain_ack_nudge_says_go_back_to_attention():
    assert "go back to Attention and set your Cove's address" in WAKE
    # Non-address remaining steps also point at Attention
    assert "go back to Attention" in WAKE


def test_mesh_md_documents_ssl_protocol_error():
    assert "ERR_SSL_PROTOCOL_ERROR" in MESH
    assert "Name resolves but browser says" in MESH
