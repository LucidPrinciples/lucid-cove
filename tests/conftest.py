"""Shared fixtures for the cove-core test suite (#94).

DB tests run against a THROWAWAY Postgres with the cove-core schema loaded.
They never mutate it — each test runs inside a transaction that is rolled back.

Setup (one time):

    createdb cove_test
    psql cove_test -f docker/init-base.sql
    export TEST_DATABASE_URL="postgresql://localhost/cove_test"

Run:

    pip install -e ".[dev]"
    pytest

If TEST_DATABASE_URL is unset, the DB tests skip cleanly so the pure-logic
tests still run anywhere.

--------------------------------------------------------------------------
#D31 — HERMETIC BY DEFAULT (incident 2026-07-10 11:41)
--------------------------------------------------------------------------
A pytest run inside the PRODUCTION Cove container reached the real netconfig +
caddy layer through a domain-set test: it re-rendered the Cove's live caddy
snippet (~/.lucidcove/caddy/conf.d/{cove}.caddy) and live-loaded it via caddy's
admin API (:2019), taking the Cove's TLS/routing down for ~1h. The suite also
runs where DATABASE_URL can point at the LIVE database.

The default posture is now HERMETIC. Unless LP_TEST_LIVE=1 is explicitly set:
  * subprocess calls that shell out to docker / caddy / systemctl are blocked;
  * network calls via urllib.request.urlopen are blocked (caddy admin :2019,
    Cloudflare DNS, the Hub registrar all go through urllib);
  * the infra helpers that WRITE a live caddy snippet or live-reload caddy
    (netconfig.install_caddy_snippet / install_haven_cove_snippet and
    runtime_address.set_address_live / set_address_live_shared) refuse to run;
  * DB tests bind ONLY to TEST_DATABASE_URL (a throwaway). DATABASE_URL is used
    as the test DB only when LP_TEST_LIVE=1, so a stray run in a container whose
    DATABASE_URL is production never touches it.

A test that genuinely needs a live box sets LP_TEST_LIVE=1 (or mocks the layer,
which every hermetic test here already does).
"""
import os
import pathlib
import subprocess as _subprocess
import sys
import urllib.request as _urllib_request

import pytest
import pytest_asyncio

# Repo root on the path so `provision` (the infra package) is importable here for the
# hermetic guard regardless of which test imports it first.
_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# LP_TEST_LIVE=1 (or true/yes) opts a run back into touching real infrastructure.
LIVE_ALLOWED = os.getenv("LP_TEST_LIVE", "").strip().lower() in ("1", "true", "yes")

# Default hermetic: only a THROWAWAY TEST_DATABASE_URL enables DB tests. DATABASE_URL
# (which in a container is the LIVE db) is used only when LP_TEST_LIVE=1 is explicit.
TEST_DB_URL = os.getenv("TEST_DATABASE_URL")
if not TEST_DB_URL and LIVE_ALLOWED:
    TEST_DB_URL = os.getenv("DATABASE_URL")

# Mark DB-dependent tests so they skip when no throwaway DB is configured.
requires_db = pytest.mark.skipif(
    not TEST_DB_URL,
    reason="Set TEST_DATABASE_URL to a throwaway Postgres to run DB tests "
           "(DATABASE_URL is used only with LP_TEST_LIVE=1).",
)


class HermeticInfraError(RuntimeError):
    """A test tried to touch live infrastructure under the default (hermetic)
    posture. Mock the layer, or set LP_TEST_LIVE=1 if a live box is intended."""


def _blocks_live_shellout(cmd) -> bool:
    """True if this argv shells out to docker / caddy / systemctl — the mutating
    infra kill points (docker exec caddy reload, docker psql, occ via docker,
    docker stop/start, systemctl). Read-only callers wrap subprocess in try/except
    so a raise degrades to their safe reason; mutating callers are stopped cold."""
    try:
        parts = [str(a) for a in (cmd if isinstance(cmd, (list, tuple)) else [cmd])]
    except Exception:
        return False
    if not parts:
        return False
    head = os.path.basename(parts[0]).lower()
    if head in ("docker", "caddy", "systemctl", "occ"):
        return True
    # `sh -c "docker exec ... occ ..."` — the NC-HTTPS reconcile Popen path.
    if head in ("sh", "bash"):
        joined = " ".join(parts).lower()
        if " docker " in f" {joined} " or "caddy reload" in joined or " occ " in f" {joined} ":
            return True
    return False


def _guarded_run(orig):
    def _run(cmd, *a, **kw):
        if not LIVE_ALLOWED and _blocks_live_shellout(cmd):
            raise HermeticInfraError(
                f"blocked live infra subprocess in a hermetic test: {cmd!r}. "
                f"Mock it, or set LP_TEST_LIVE=1 for an intentional live run.")
        return orig(cmd, *a, **kw)
    return _run


def _guarded_urlopen(orig):
    def _open(url, *a, **kw):
        if not LIVE_ALLOWED:
            target = getattr(url, "full_url", url)
            raise HermeticInfraError(
                f"blocked network call in a hermetic test: {target!r}. "
                f"Mock it, or set LP_TEST_LIVE=1 for an intentional live run.")
        return orig(url, *a, **kw)
    return _open


def _guarded_infra(name):
    def _blocked(*a, **kw):
        raise HermeticInfraError(
            f"{name} would touch live networking/caddy in a hermetic test. "
            f"Mock it, or set LP_TEST_LIVE=1 for an intentional live run.")
    return _blocked


def _guard_infra_module(monkeypatch, import_name, attrs):
    """Force-import an infra module and replace each named live-side-effect helper
    with the hermetic raiser. Force-import (not sys.modules.get) so the guard holds
    even when the test imports the module AFTER this fixture runs. The infra modules
    also load under a bare identity (`netconfig`) when imported off the provision
    path; patch that copy too if it exists."""
    try:
        mod = __import__(import_name, fromlist=["_"])
    except Exception:
        mod = None
    bare = sys.modules.get(import_name.split(".")[-1])
    for target in (mod, bare):
        if target is None:
            continue
        for attr in attrs:
            if hasattr(target, attr):
                monkeypatch.setattr(target, attr, _guarded_infra(f"{import_name}.{attr}"),
                                    raising=False)


@pytest.fixture(autouse=True)
def _hermetic_guard(monkeypatch):
    """Default-on infra guard (#D31). No-op when LP_TEST_LIVE=1."""
    if LIVE_ALLOWED:
        yield
        return
    monkeypatch.setattr(_subprocess, "run", _guarded_run(_subprocess.run))
    monkeypatch.setattr(_subprocess, "Popen", _guarded_run(_subprocess.Popen))
    monkeypatch.setattr(_urllib_request, "urlopen", _guarded_urlopen(_urllib_request.urlopen))
    # The helpers that WRITE a live caddy snippet / live-reload caddy (the incident's
    # exact path). Force-imported + patched so import order can't sneak past the guard.
    _guard_infra_module(monkeypatch, "provision.netconfig",
                        ("install_caddy_snippet", "install_haven_cove_snippet"))
    _guard_infra_module(monkeypatch, "provision.runtime_address",
                        ("set_address_live", "set_address_live_shared"))
    yield


@pytest_asyncio.fixture
async def db():
    """A connection wrapped in a transaction that is rolled back after the test.

    The CRUD helpers in src.memory.database take a connection and leave the
    commit to the caller, so a test can insert, read it back within the same
    connection, and the rollback in the finally block discards everything.
    """
    import psycopg
    from psycopg.rows import dict_row

    conn = await psycopg.AsyncConnection.connect(
        TEST_DB_URL, row_factory=dict_row, autocommit=False
    )
    try:
        yield conn
    finally:
        await conn.rollback()
        await conn.close()
