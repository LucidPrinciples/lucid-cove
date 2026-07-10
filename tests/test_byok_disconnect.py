# #D12 — BYOK disconnect + moonshot removal. NOTE: this shipped ahead of batch-2
# (PR #43, per the 07-09/10 session log): the "Use Cove default (disconnect)"
# action, the disconnectBYOK() handler, the backend {disconnect:true} path, and
# the removal of the retired moonshot direct provider are all already on
# origin/main. These are REGRESSION GUARDS so the behavior can't quietly regress
# — no new implementation was needed.
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
SETTINGS_JS = (ROOT / "src" / "dashboard" / "static" / "js" / "settings.js").read_text()
PRESENCE_PY = (ROOT / "src" / "dashboard" / "routes" / "presence.py").read_text()


def test_disconnect_button_present_in_settings_ui():
    assert "Use Cove default (disconnect)" in SETTINGS_JS
    assert "disconnectBYOK" in SETTINGS_JS


def test_disconnect_handler_posts_disconnect_flag():
    assert "disconnect: true" in SETTINGS_JS
    assert "/api/settings/model-key" in SETTINGS_JS


def test_backend_disconnect_nulls_the_byok_fields():
    # the disconnect branch clears provider + key (the "NULL it by hand" fix)
    assert 'disconnect = body.get("disconnect") is True' in PRESENCE_PY
    assert 'ac.pop("model_provider", None)' in PRESENCE_PY
    assert 'ac.pop("model_api_key", None)' in PRESENCE_PY


def test_moonshot_is_not_a_selectable_provider():
    # retired: Moonshot runs via OpenRouter only now — a dead provider must not be
    # in the allowlist set literal or the UI picker (ignore code comments).
    assert "moonshot" not in _known_providers_set().lower()
    assert "moonshot" not in SETTINGS_JS.lower()


def _known_providers_set() -> str:
    """The allowlist SET LITERAL only (drops the trailing '# moonshot retired' comment)."""
    for line in PRESENCE_PY.splitlines():
        if "_KNOWN_PROVIDERS" in line and "{" in line:
            code = line.split("#", 1)[0]  # strip the comment
            return code[code.index("{"): code.rindex("}") + 1]
    raise AssertionError("_KNOWN_PROVIDERS allowlist not found")


def test_known_providers_are_the_live_five():
    s = _known_providers_set().lower()
    for p in ("openrouter", "openai", "google", "groq", "ollama"):
        assert p in s
