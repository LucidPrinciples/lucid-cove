# #D32 — the shared/bundled Caddy admin API must not be loadable by any container on the
# box. The rendered base now constrains the admin endpoint with an origin allowlist +
# enforce_origin, while keeping the sanctioned app path (Set-Address POST) working.
# Pure string-generation; no live Caddy.
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "provision"))
import netconfig  # noqa: E402


def test_shared_base_admin_has_origin_allowlist_and_enforce():
    base = netconfig.build_shared_caddy_base_caddyfile()
    assert "enforce_origin" in base
    assert "origins" in base
    # sanctioned shared-Caddy app path preserved (COVE_CADDY_ADMIN = lucidcove-caddy:2019)
    assert "lucidcove-caddy:2019" in base
    # loopback (host docker-exec) preserved
    assert "127.0.0.1:2019" in base
    # the bare permissive form is gone
    assert "admin :2019\n" not in base.replace("    ", "")


def test_bundled_selfhost_admin_has_origin_allowlist():
    cf = netconfig.build_selfhost_caddyfile(domain="smith.example.org", app_port=8200)
    assert "enforce_origin" in cf
    assert "origins" in cf
    # bundled Caddy: app reaches admin at caddy:2019
    assert "caddy:2019" in cf


def test_admin_origins_env_override(monkeypatch):
    monkeypatch.setenv("LP_CADDY_ADMIN_ORIGINS", "onlythis:2019")
    block = "\n".join(netconfig.render_admin_global_block("caddy:2019"))
    assert "onlythis:2019" in block
    # an explicit override replaces the defaults entirely (operator's choice)
    assert "lucidcove-caddy:2019" not in block
    assert "caddy:2019" not in block.replace("onlythis:2019", "")


def test_admin_block_includes_sanctioned_host_by_default():
    block = "\n".join(netconfig.render_admin_global_block("lucidcove-caddy:2019"))
    assert "lucidcove-caddy:2019" in block
    assert "enforce_origin" in block
