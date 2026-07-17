"""#CX-LOG — Matrix vhost Caddy access logging.

Spec: Working/Specs/connect-diagnostics-2026-07-17.md

Access log (JSON → stdout) must appear on every Matrix site block rendered by
netconfig so `docker logs <caddy> | grep _matrix` can answer reachability +
latency. Non-Matrix vhosts must stay quiet. No CORS changes on /_matrix.
"""

from provision import netconfig as nc


def _matrix_on(**over):
    base = dict(
        cove_id="founders",
        domain="founders.lucidcove.org",
        app_port=8200,
        nextcloud_port=8081,
        matrix_port=8008,
        matrix_server_name="matrix.founders.lucidcove.org",
        matrix_on=True,
    )
    base.update(over)
    return base


def test_cove_snippet_matrix_has_json_access_log():
    snip = nc.build_cove_caddy_snippet(**_matrix_on())
    assert "matrix.founders.lucidcove.org {" in snip
    # log block inside the matrix site
    mx = snip.split("matrix.founders.lucidcove.org {", 1)[1].split("\n*.", 1)[0]
    assert "log {" in mx
    assert "output stdout" in mx
    assert "format json" in mx
    # still proxies matrix; CORS only on .well-known client, not site-wide
    assert "reverse_proxy localhost:8008" in mx
    assert mx.count("Access-Control-Allow-Origin") == 1


def test_cove_snippet_matrix_off_has_no_access_log_directive_on_other_hosts():
    snip = nc.build_cove_caddy_snippet(**_matrix_on(matrix_on=False))
    assert "matrix.founders.lucidcove.org" not in snip
    # cloud / wildcard must not gain a site log just because we added the helper
    assert "log {" not in snip
    assert "cloud.founders.lucidcove.org" in snip
    assert "*.founders.lucidcove.org" in snip


def test_selfhost_caddyfile_matrix_has_json_access_log():
    cf = nc.build_selfhost_caddyfile(
        domain="rivera.lucidcove.org",
        app_port=8200,
        matrix_on=True,
        matrix_server_name="matrix.rivera.lucidcove.org",
    )
    assert "matrix.rivera.lucidcove.org {" in cf
    mx = cf.split("matrix.rivera.lucidcove.org {", 1)[1]
    # cut at next top-level site if present
    mx = mx.split("\nvoice.", 1)[0].split("\n*.", 1)[0]
    assert "log {" in mx
    assert "output stdout" in mx
    assert "format json" in mx


def test_haven_snippet_matrix_has_json_access_log():
    snip = nc.build_haven_cove_snippet(
        cove_id="woods",
        domain="woods.lucidcove.org",
        app_port=8200,
        matrix_on=True,
        matrix_server_name="matrix.woods.lucidcove.org",
    )
    assert "matrix.woods.lucidcove.org {" in snip
    mx = snip.split("matrix.woods.lucidcove.org {", 1)[1]
    mx = mx.split("\nvoice.", 1)[0].split("\n*.", 1)[0]
    assert "log {" in mx
    assert "output stdout" in mx
    assert "format json" in mx
    # helper comment tag for greppability
    assert "#CX-LOG" in mx or "# #CX-LOG" in mx


def test_helper_is_lean_json_stdout_only():
    lines = nc._matrix_caddy_access_log_lines()
    joined = "\n".join(lines)
    assert "output stdout" in joined
    assert "format json" in joined
    # no file path / no global logger name — keep docker-log simple
    assert "output file" not in joined
