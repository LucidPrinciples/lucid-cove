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
    assert "same registry" not in html
    assert "https://hermes.cove.lucidcove.org" not in html
    assert "mark-tuner.png" in html
    assert "logo-icon" in html


def test_landing_first_paint_on_tuner_host_is_lucid_tuner():
    from src.dashboard.host_context import brand_public_tuner_landing

    html = (ROOT / "src/dashboard/static/landing.html").read_text()
    branded = brand_public_tuner_landing(html)
    assert "<title>Lucid Tuner</title>" in branded
    assert 'src="/static/mark-tuner.png"' in branded
    assert 'alt="Lucid Tuner"' in branded
    assert "Free Lucid Tuner" in branded
    assert "Lucid Principles account" not in branded
    assert "Action board" not in branded
    assert "<title>Lucid Cove</title>" in html
    assert "Lucid Principles account" in html


def test_tuner_landing_feature_bullets_are_stranger_copy():
    from src.dashboard.host_context import brand_public_tuner_landing

    html = (ROOT / "src/dashboard/static/landing.html").read_text()
    branded = brand_public_tuner_landing(html)
    for needle in (
        "Daily tuning from 22 Lucid Principles",
        "Customizable Daily Actions",
        "Music player — 20+ genres, 7 Signals",
        "Tuning Mirrors",
        "Earn with Referrals",
    ):
        assert needle in branded
        assert needle in html
    assert "The full Canon" not in branded
    assert "One Tune a day" not in branded
    assert "Music player and the daily Drop" not in branded
    assert "connect key" not in branded


def test_tuner_signin_mail_uses_lucid_tuner_account():
    email_src = (ROOT / "src/dashboard/routes/email.py").read_text()
    acc = (ROOT / "src/dashboard/routes/account.py").read_text()
    assert "product_name" in email_src
    assert "hermes_hint" not in email_src
    assert "hermes_hint" not in acc
    assert "_signin_email_kwargs" in acc
    assert 'product_name": "Lucid Tuner"' in acc
    assert "signin@lucidtuner.com" in email_src
    assert "mark-tuner.png" in email_src
    assert "LUCID_TUNER_ICON_HERO" not in email_src
    assert "LP_MARK.png" in email_src
    assert "connect Lucid Cove on Hermes" not in email_src
    assert "paste it in Gear" not in email_src


def _assert_tuner_signin_html(html: str, heading: str):
    assert heading in html
    assert "Lucid Tuner" in html
    assert "mark-tuner.png" in html
    assert 'width="96"' in html
    assert 'height="96"' in html
    assert "LUCID_TUNER_ICON_HERO" not in html
    assert "LP_MARK.png" not in html
    assert "Lucid Principles" not in html
    assert "connect Lucid Cove on Hermes" not in html
    assert "Gear" not in html
    assert "Settings" not in html
    assert "connect-key" not in html
    assert "connect key" not in html.lower()


def test_tuner_signin_html_uses_tuner_mark_not_lp():
    from src.dashboard.routes.email import render_signin_mail

    mail = render_signin_mail(
        is_signup=True,
        signin_link="https://app.lucidtuner.com/p/tok",
        product_name="Lucid Tuner",
    )
    assert mail["heading"] == "Welcome to Lucid Tuner"
    _assert_tuner_signin_html(mail["html"], mail["heading"])


def test_tuner_welcome_back_mail_matches_signin_template():
    from src.dashboard.routes.email import render_signin_mail

    mail = render_signin_mail(
        is_signup=False,
        signin_link="https://app.lucidtuner.com/p/tok",
        product_name="Lucid Tuner",
    )
    assert mail["heading"] == "Welcome back to Lucid Tuner"
    assert mail["subject"] == "Your Lucid Tuner sign-in link"
    assert mail["brand"]["sender_email"] == "signin@lucidtuner.com"
    _assert_tuner_signin_html(mail["html"], mail["heading"])


def test_tuner_signin_mail_brands_from_magic_link_without_product_name():
    from src.dashboard.routes.email import render_signin_mail

    mail = render_signin_mail(
        is_signup=False,
        signin_link="https://app.lucidtuner.com/p/tok",
    )
    assert mail["heading"] == "Welcome back to Lucid Tuner"
    assert mail["brand"]["sender_email"] == "signin@lucidtuner.com"
    _assert_tuner_signin_html(mail["html"], mail["heading"])


def test_cove_signin_html_keeps_lp_mark():
    from src.dashboard.routes.email import _build_email_html

    html = _build_email_html(
        "Welcome to Lucid Principles",
        "Your Lucid Principles account is ready.",
        "Sign in",
        "https://jason.lucidcove.org/p/tok",
    )
    assert "LP_MARK.png" in html
    assert "Lucid Principles" in html
    assert "mark-tuner.png" not in html
    assert 'width="48"' in html


def test_tuner_mail_brand_sender_is_signin_lucidtuner():
    from src.dashboard.routes.email import signin_mail_brand

    tuner = signin_mail_brand("Lucid Tuner")
    assert tuner["sender_email"] == "signin@lucidtuner.com"
    assert tuner["sender_name"] == "Lucid Tuner"
    assert tuner["mark_url"] == "https://app.lucidtuner.com/static/mark-tuner.png"
    assert tuner["mark_width"] == 96
    assert tuner["mark_height"] == 96
    cove = signin_mail_brand(None)
    assert cove["sender_email"] == "signin@lucidprinciples.com"
    assert cove["sender_name"] == "Lucid Principles"


def test_settings_connect_key_names_hermes_team():
    js = (ROOT / "src/dashboard/static/js/settings-account.js").read_text()
    assert "Hermes team" in js
    assert "Get my connect key" in js


def test_signin_from_tuner_host_stays_on_tuner_not_cove_subdomain():
    from src.dashboard.routes.account import signin_link_url

    url = signin_link_url(
        scheme="https",
        request_host="app.lucidtuner.com",
        raw_token="tok",
        username="jason",
        cove={"domain": "lucidcove.org", "subdomain_routing": True},
    )
    assert url == "https://app.lucidtuner.com/p/tok"


def test_signin_from_cove_host_still_uses_handle_subdomain():
    from src.dashboard.routes.account import signin_link_url

    url = signin_link_url(
        scheme="https",
        request_host="app.lucidcove.org",
        raw_token="tok",
        username="jason",
        cove={"domain": "lucidcove.org", "subdomain_routing": True},
    )
    assert url == "https://jason.lucidcove.org/p/tok"
