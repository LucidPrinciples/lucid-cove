# #D35 — close the Caddy admin API at the NETWORK layer (finish #D32). #D32's
# origin allowlist is Host-header based (a co-tenant spoofing Host still gets in);
# the token gate moves the REAL admin to loopback (:2018) and puts a token-gated
# proxy on the bridge (:2019). No token configured => the #D32 behavior is used
# verbatim (inert, no regression).
import provision.netconfig as nc


def _bundled(**over):
    return nc.build_selfhost_caddyfile(domain="rivera.lucidcove.org", app_port=8200, **over)


# ── gate OFF (no token) → #D32 behavior, unchanged ───────────────────────────
def test_no_token_keeps_d32_bridge_admin(monkeypatch):
    monkeypatch.delenv("LP_CADDY_ADMIN_TOKEN", raising=False)
    block = "\n".join(nc.render_admin_global_block("caddy:2019"))
    assert "admin :2019" in block
    assert "enforce_origin" in block
    assert nc.render_admin_proxy_site() == []


def test_no_token_bases_have_no_proxy(monkeypatch):
    monkeypatch.delenv("LP_CADDY_ADMIN_TOKEN", raising=False)
    for base in (_bundled(), nc.build_shared_caddy_base_caddyfile()):
        assert "admin :2019" in base
        assert "localhost:2018" not in base
        assert "reverse_proxy localhost:2018" not in base


# ── gate ON (token set) → loopback admin + token-gated proxy ─────────────────
def test_token_moves_admin_to_loopback(monkeypatch):
    monkeypatch.setenv("LP_CADDY_ADMIN_TOKEN", "s3cr3t")
    block = "\n".join(nc.render_admin_global_block("caddy:2019"))
    assert "admin localhost:2018" in block   # real admin: loopback only
    assert "admin :2019" not in block        # NOT on the bridge anymore


def test_token_renders_gated_proxy(monkeypatch):
    monkeypatch.setenv("LP_CADDY_ADMIN_TOKEN", "s3cr3t")
    site = "\n".join(nc.render_admin_proxy_site())
    assert ":2019 {" in site                       # bridge entrance
    assert "reverse_proxy localhost:2018" in site  # → the loopback admin
    assert 'Bearer {$LP_CADDY_ADMIN_TOKEN}' in site  # requires the secret
    assert "respond @noauth 403" in site
    # the secret itself is NEVER written into the Caddyfile — only the env placeholder
    assert "s3cr3t" not in site


def test_token_bases_wire_the_proxy(monkeypatch):
    monkeypatch.setenv("LP_CADDY_ADMIN_TOKEN", "s3cr3t")
    for base in (_bundled(), nc.build_shared_caddy_base_caddyfile()):
        assert "admin localhost:2018" in base
        assert "admin :2019" not in base
        assert "reverse_proxy localhost:2018" in base
        assert "header_up Host localhost:2018" in base
        assert "s3cr3t" not in base  # secret stays in env, not on disk


# ── compose passthrough (one box-env source of truth) ────────────────────────
def test_shared_compose_passes_token_through():
    compose = nc.build_shared_caddy_compose()
    assert "LP_CADDY_ADMIN_TOKEN=${LP_CADDY_ADMIN_TOKEN:-}" in compose


# ── app-side authenticates its /load when the gate is on ─────────────────────
def test_caddy_load_adds_bearer_when_token_set(monkeypatch):
    import provision.runtime_address as ra
    captured = {}

    class _Resp:
        def read(self):
            return b""
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def _fake_urlopen(req, timeout=0):
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        return _Resp()

    monkeypatch.setattr(ra.urllib.request, "urlopen", _fake_urlopen)

    monkeypatch.setenv("LP_CADDY_ADMIN_TOKEN", "s3cr3t")
    ra._caddy_load("{ }")
    assert captured["headers"].get("authorization") == "Bearer s3cr3t"

    captured.clear()
    monkeypatch.delenv("LP_CADDY_ADMIN_TOKEN", raising=False)
    ra._caddy_load("{ }")
    assert "authorization" not in captured["headers"]  # inert when the gate is off
