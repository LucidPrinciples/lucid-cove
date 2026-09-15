"""Contact Help labels for Haven MC Messages."""
from src.dashboard.routes.contact import compose_contact_fields, normalize_product


def test_normalize_product_allowlist():
    assert normalize_product("lucid-tuner") == "Lucid Tuner"
    assert normalize_product("Lucid Cove on Hermes") == "Lucid Cove on Hermes"
    assert normalize_product("lucid-cove-hermes") == "Lucid Cove on Hermes"
    assert normalize_product("not-a-product") == ""


def test_compose_puts_origin_in_subject_and_body():
    subject, body = compose_contact_fields(
        message="The Drop will not play.",
        product="lucid-tuner",
        host="app.lucidtuner.com",
        path="/tune",
        handle="jagcot",
        email="a@example.com",
        name="Jason",
        tier="pro",
        connected="yes",
    )
    assert subject.startswith("Lucid Tuner")
    assert "app.lucidtuner.com" in subject
    assert "@jagcot" in subject
    assert "product: Lucid Tuner" in body
    assert "host: app.lucidtuner.com" in body
    assert "path: /tune" in body
    assert "The Drop will not play." in body
    assert body.strip().endswith("The Drop will not play.")


def test_compose_strips_control_chars():
    subject, body = compose_contact_fields(
        message="hi\x00there",
        product="lucid-cove",
        host="example.com\r\nX-Injected: 1",
        handle="@ok",
    )
    assert "\x00" not in body
    assert "\r" not in subject
    assert "X-Injected" not in subject
    assert subject.startswith("Lucid Cove")


def test_compose_allows_missing_email():
    subject, body = compose_contact_fields(
        message="Atlas help",
        product="lucid-cove",
        host="atlas.cove.example",
        handle="atlas",
        email="",
    )
    assert "atlas.cove.example" in subject
    assert "email:" not in body
    assert body.strip().endswith("Atlas help")


def test_should_proxy_to_hub_skips_forwarded():
    from types import SimpleNamespace
    from src.dashboard.routes.contact import should_proxy_to_hub

    forwarded = SimpleNamespace(headers={"X-Contact-Forward": "1"})
    assert should_proxy_to_hub(forwarded) is False
