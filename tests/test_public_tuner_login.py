"""Public Lucid Tuner login door (app.lucidtuner.com) — host, referral, connect.

Offline slice: same hub accounts as app.lucidcove.org. DNS flip is later.
"""
from pathlib import Path

from src.dashboard.routes.account import ALLOWED_REDIRECT_DOMAINS


ROOT = Path(__file__).resolve().parents[1]


def test_referral_bounce_allows_app_lucidtuner():
    assert "app.lucidtuner.com" in ALLOWED_REDIRECT_DOMAINS
    assert "www.lucidtuner.com" in ALLOWED_REDIRECT_DOMAINS


def test_cors_allows_app_lucidtuner():
    src = (ROOT / "src/dashboard/app.py").read_text()
    assert '"https://app.lucidtuner.com"' in src


def test_affiliates_is_one_lucid_tuner_referral_link():
    js = (ROOT / "src/dashboard/static/js/affiliates.js").read_text()
    assert "signed_in === false" in js
    assert "to=https://app.lucidtuner.com" in js
    assert "to=https://lucidcove.org" not in js
    assert "to=https://lucidprinciples.com" not in js
    assert "Direct Signup" not in js
    assert "Program tools" not in js
    assert "Earnings (LPC)" not in js
    assert js.count("aff-link-row") <= 1 or js.count("?to=") == 1


def test_landing_brands_as_lucid_tuner_on_app_host():
    html = (ROOT / "src/dashboard/static/landing.html").read_text()
    assert "app.lucidtuner.com" in html
    assert "Align your broadcast" in html or "Align Your Broadcast" in html
    assert "Hermes" in html


def test_settings_connect_key_names_hermes_team():
    js = (ROOT / "src/dashboard/static/js/settings-account.js").read_text()
    assert "Hermes team" in js
    assert "Get my connect key" in js
