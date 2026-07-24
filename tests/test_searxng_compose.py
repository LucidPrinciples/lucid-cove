"""SearXNG — compose-in-repo agent web_search (#GABS-V1 Phase 0)."""

from __future__ import annotations

from pathlib import Path

import provision.centralized as cz

ROOT = Path(__file__).resolve().parents[1]


def test_compose_includes_searxng_service_and_app_url():
    cove = {"id": "lucidcove-test", "name": "Test", "_app_port": 8200}
    deploy = {
        "nextcloud_port": 8080,
        "matrix_port": 8008,
        "voice_port": 8301,
        "umami_port": 3000,
        "searxng_port": 8888,
        "lucid_cove_path": "/cove-core",
    }
    out = cz.build_compose(
        cove, deploy, matrix_on=False, voice_local=True, umami_on=True, searx_on=True
    )
    assert "container_name: lucidcove-test-searxng" in out
    assert "docker.io/searxng/searxng" in out
    assert "./docker/searxng:/etc/searxng" in out
    assert "SEARXNG_URL: http://lucidcove-test-searxng:8080" in out
    assert "8888:8080" in out


def test_compose_can_disable_searx():
    cove = {"id": "lucidcove-test", "name": "Test", "_app_port": 8200}
    deploy = {
        "nextcloud_port": 8080,
        "matrix_port": 8008,
        "voice_port": 8301,
        "lucid_cove_path": "/cove-core",
    }
    out = cz.build_compose(
        cove, deploy, matrix_on=False, voice_local=False, umami_on=False, searx_on=False
    )
    assert "searxng:" not in out
    assert "SEARXNG_URL:" not in out


def test_build_env_emits_searx_url():
    cove = {"id": "lucidcove-ab12", "name": "T", "domain": ""}
    deploy = {"nextcloud_port": 8080, "matrix_port": 8008, "searxng_port": 8888}
    op = {"name": "Op", "handle": "op", "email": "o@t.test"}
    env_txt = cz.build_env(cove, op, [], {}, {}, deploy)
    assert "SEARXNG_URL=http://lucidcove-ab12-searxng:8080" in env_txt
    assert "SEARXNG_PORT=8888" in env_txt
    assert "SEARXNG_SECRET=" in env_txt


def test_settings_yml_enables_json_format():
    settings = (ROOT / "docker/searxng/settings.yml").read_text(encoding="utf-8")
    assert "json" in settings
    assert "use_default_settings" in settings


def test_example_compose_documents_searxng():
    ex = (ROOT / "docker/docker-compose.example.yml").read_text(encoding="utf-8")
    assert "cove-searxng" in ex
    assert "SEARXNG_URL" in ex


def test_presence_defaults_include_research_tools():
    from src.config import _PRESENCE_DEFAULT_MODULES

    assert "tools.research_tools" in _PRESENCE_DEFAULT_MODULES


def test_env_registry_has_searx_knobs():
    env_src = (ROOT / "src/env.py").read_text(encoding="utf-8")
    for name in ("SEARXNG_URL", "SEARXNG_SECRET", "SEARXNG_PORT"):
        assert name in env_src


def test_web_search_docstring_no_fake_bing():
    src = (ROOT / "src/tools/research_tools.py").read_text(encoding="utf-8")
    # Docstring used to promise Bing fallback that was never implemented.
    assert "Bing fallback" not in src
