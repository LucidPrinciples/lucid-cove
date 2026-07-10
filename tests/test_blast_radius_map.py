# #D34 — blast-radius map (static analysis). Docs/blast-radius-map.md enumerates
# the control planes an agent container can reach. These tests keep the doc honest:
# it has the shape the brief asked for AND its two verifiable claims (no docker
# socket; the named credential surfaces really exist in the provisioner) hold
# against the repo.
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOC = (ROOT / "Docs" / "blast-radius-map.md").read_text()
CENTRALIZED = (ROOT / "provision" / "centralized.py").read_text()


def test_doc_has_the_required_table_columns():
    # surface → who can reach it → credential involved → risk → proposed hardening
    for col in ("Surface", "Who can reach it", "Credential involved", "Risk", "Proposed hardening"):
        assert col in DOC


def test_doc_covers_the_core_surfaces():
    for surface in ("Caddy admin", "Ollama", "Nextcloud admin", "Postgres",
                    "Cloudflare", "Matrix", "GitHub push"):
        assert surface in DOC


def test_doc_flags_live_confirm_rows():
    # brief: rows only a running box can verify must be flagged
    assert "NEEDS-LIVE-CONFIRM" in DOC
    assert DOC.count("NEEDS-LIVE-CONFIRM") >= 3


def test_doc_is_a_report_not_a_fix():
    assert "No fixes applied" in DOC or "no fixes" in DOC.lower()
    assert "own ticket" in DOC.lower() or "future tickets" in DOC.lower()


# ── grounded claims: the doc must match the repo it describes ─────────────────
def test_docker_socket_boundary_claim_is_true():
    # the doc claims the docker socket is never MOUNTED — verify no such volume
    # exists in the provisioner-generated compose (comments mentioning it are fine)
    for line in CENTRALIZED.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert "/var/run/docker.sock" not in stripped
        assert "docker.sock:" not in stripped


def test_named_credential_surfaces_exist_in_provisioner():
    # every credential the doc calls out as reachable is really wired into the app env
    for cred in ("NEXTCLOUD_ADMIN_PASSWORD", "SHARED_CONTAINER_SECRET",
                 "OLLAMA_BASE_URL", "LP_CADDY_ADMIN_TOKEN", "POSTGRES_PASSWORD"):
        assert cred in DOC and cred in CENTRALIZED
