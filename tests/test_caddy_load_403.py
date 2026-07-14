# Install-pass: set-address Caddy admin /load must name HTTP 403 token mismatches
# instead of a opaque "Caddy admin /load failed: …" string.
from io import BytesIO

import provision.runtime_address as ra
import urllib.error


def test_caddy_load_403_names_token_mismatch(monkeypatch):
    monkeypatch.setenv("LP_CADDY_ADMIN_TOKEN", "s3cr3t")
    monkeypatch.setenv("COVE_CADDY_ADMIN", "http://lucidcove-caddy:2019")

    def _raise(req, timeout=0):
        raise urllib.error.HTTPError(
            req.full_url, 403, "Forbidden", hdrs=None, fp=BytesIO(b"Forbidden")
        )

    monkeypatch.setattr(ra.urllib.request, "urlopen", _raise)
    res = ra._caddy_load("{ }")
    assert res["ok"] is False
    assert res.get("code") == "caddy_admin_403"
    assert "403" in res["reason"]
    assert "LP_CADDY_ADMIN_TOKEN" in res["reason"]
    assert "token_set=yes" in res["reason"]


def test_caddy_load_ok_with_bearer(monkeypatch):
    monkeypatch.setenv("LP_CADDY_ADMIN_TOKEN", "s3cr3t")
    captured = {}

    class _Resp:
        def read(self):
            return b""

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _ok(req, timeout=0):
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        return _Resp()

    monkeypatch.setattr(ra.urllib.request, "urlopen", _ok)
    res = ra._caddy_load("{ }")
    assert res == {"ok": True}
    assert captured["headers"].get("authorization") == "Bearer s3cr3t"
