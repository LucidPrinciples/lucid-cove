"""Umami analytics — compose-in-repo product path (landscape + umami spec)."""

from __future__ import annotations

from pathlib import Path

import provision.centralized as cz
import provision.netconfig as nc

ROOT = Path(__file__).resolve().parents[1]


def test_build_umami_db_sql_creates_role_and_db():
    sql = cz.build_umami_db_sql("s3cret-umami")
    assert "CREATE ROLE umami" in sql
    assert "CREATE DATABASE umami OWNER umami" in sql
    assert "s3cret-umami" in sql


def test_compose_includes_umami_service_and_db_init():
    cove = {"id": "lucidcove-test", "name": "Test", "_app_port": 8200}
    deploy = {
        "nextcloud_port": 8080,
        "matrix_port": 8008,
        "voice_port": 8301,
        "umami_port": 3000,
        "lucid_cove_path": "/cove-core",
    }
    out = cz.build_compose(cove, deploy, matrix_on=False, voice_local=True, umami_on=True)
    assert "container_name: lucidcove-test-umami" in out
    assert "ghcr.io/umami-software/umami:postgresql-latest" in out
    assert "init-umami-db.sql:/docker-entrypoint-initdb.d/04-umami.sql" in out
    assert "UMAMI_INTERNAL_URL: http://lucidcove-test-umami:3000" in out
    assert "DATABASE_URL: postgresql://umami:${UMAMI_DB_PASSWORD}@postgres:5432/umami" in out


def test_compose_can_disable_umami():
    cove = {"id": "lucidcove-test", "name": "Test", "_app_port": 8200}
    deploy = {
        "nextcloud_port": 8080,
        "matrix_port": 8008,
        "voice_port": 8301,
        "lucid_cove_path": "/cove-core",
    }
    out = cz.build_compose(cove, deploy, matrix_on=False, voice_local=False, umami_on=False)
    assert "umami:" not in out
    assert "04-umami.sql" not in out


def test_host_caddy_snippet_routes_analytics():
    snip = nc.build_cove_caddy_snippet(
        cove_id="x",
        domain="example.test",
        app_port=8200,
        nextcloud_port=8080,
        matrix_port=8008,
        matrix_server_name="matrix.example.test",
        matrix_on=False,
        voice_port=8301,
        umami_port=3000,
    )
    assert "analytics.example.test" in snip
    assert "reverse_proxy localhost:3000" in snip


def test_haven_caddy_snippet_routes_analytics_container():
    snip = nc.build_haven_cove_snippet(
        cove_id="lucidcove-ab12",
        domain="family.test",
        app_port=8200,
        matrix_on=False,
        voice_on=True,
        umami_on=True,
    )
    assert "analytics.family.test" in snip
    assert "reverse_proxy lucidcove-ab12-umami:3000" in snip


def test_env_registry_has_umami_knobs():
    env_src = (ROOT / "src/env.py").read_text(encoding="utf-8")
    for name in (
        "UMAMI_ENABLED",
        "UMAMI_INTERNAL_URL",
        "UMAMI_PUBLIC_URL",
        "UMAMI_API_KEY",
        "HAVEN_STATS_SITES",
    ):
        assert name in env_src
    assert '"Analytics"' in env_src


def test_example_compose_documents_umami():
    ex = (ROOT / "docker/docker-compose.example.yml").read_text(encoding="utf-8")
    assert "cove-umami" in ex
    assert "UMAMI_DB_PASSWORD" in ex


def test_bootstrap_sql_present_for_existing_volumes():
    boot = (ROOT / "docker/umami-bootstrap.sql").read_text(encoding="utf-8")
    assert "CREATE ROLE umami" in boot
    assert "CREATE DATABASE umami" in boot
