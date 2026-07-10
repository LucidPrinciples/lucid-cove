# #D31 — the hermetic guard itself (incident 2026-07-10). Proves the default posture
# blocks the exact side-effects that took a production Cove down: a docker/caddy
# shellout, a caddy-admin (:2019) network call, and the live caddy-snippet installers.
# Pure-logic; no infra required.
import subprocess
import urllib.request

import pytest

from conftest import HermeticInfraError, _blocks_live_shellout


def test_detects_docker_and_caddy_shellouts():
    assert _blocks_live_shellout(["docker", "exec", "caddy-proxy", "caddy", "reload"]) is True
    assert _blocks_live_shellout(["docker", "stop", "smith-dendrite"]) is True
    assert _blocks_live_shellout(["/usr/bin/caddy", "reload"]) is True
    assert _blocks_live_shellout(["systemctl", "restart", "ollama"]) is True
    # the NC-HTTPS reconcile shape: sh -c "docker exec ... occ ..."
    assert _blocks_live_shellout(["sh", "-c", "docker exec -u www-data nc php occ ..."]) is True


def test_allows_innocuous_shellouts():
    assert _blocks_live_shellout(["git", "status"]) is False
    assert _blocks_live_shellout(["python3", "-c", "print(1)"]) is False
    assert _blocks_live_shellout(["echo", "hello"]) is False


def test_subprocess_run_docker_is_blocked():
    # The autouse _hermetic_guard fixture has patched subprocess.run for this test.
    with pytest.raises(HermeticInfraError):
        subprocess.run(["docker", "ps"])


def test_caddy_admin_urlopen_is_blocked():
    with pytest.raises(HermeticInfraError):
        urllib.request.urlopen("http://caddy:2019/load")


def test_live_caddy_installers_refuse_to_run():
    # netconfig.install_caddy_snippet writes a live conf.d/*.caddy snippet + reloads caddy.
    from provision import netconfig
    with pytest.raises(HermeticInfraError):
        netconfig.install_caddy_snippet("stub", "smith")
