"""APP3: the public Tuner home-screen icon is the Lucid Tuner mark, not Cove MC."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHELL = ROOT / "src/dashboard/static/tuner-app"


def test_tuner_app_links_its_own_home_icon():
    html = (SHELL / "index.html").read_text()
    assert 'rel="apple-touch-icon"' in html
    assert 'href="/static/tuner-app/apple-touch-icon.png"' in html
    assert 'name="apple-mobile-web-app-title" content="Lucid Tuner"' in html
    assert 'rel="manifest" href="/static/tuner-app/manifest.json"' in html
    assert 'href="/static/manifest.json"' not in html
    assert 'href="/static/favicon.svg"' not in html
    assert 'href="/static/tuner-app/favicon.svg"' in html
    icon = SHELL / "apple-touch-icon.png"
    manifest_path = SHELL / "manifest.json"
    assert icon.is_file()
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text())
    assert manifest["name"] == "Lucid Tuner"
    assert manifest["short_name"] == "Lucid Tuner"
    assert manifest["start_url"] == "/"
    assert manifest["display"] == "standalone"
    sizes = {item["sizes"] for item in manifest["icons"]}
    assert sizes == {"192x192", "512x512"}
    assert any(item.get("purpose") == "maskable" for item in manifest["icons"])
    assert all(item["src"].startswith("/static/tuner-app/") for item in manifest["icons"])
