"""#SEC5 — retire legacy provisioner + dead migrations dir.

Guards:
  - provision_overlay / provision_templates are gone
  - agent_provision no longer imports or generates overlays
  - generate-overlay endpoint is retired (410)
  - top-level migrations/ is gone (live set is docker/migrations/)
  - docker/migrations has no duplicate numeric prefixes
  - requirements.lock header no longer names stuart-cove-app
"""
from pathlib import Path
import re
import ast

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"


def test_legacy_overlay_modules_removed():
    assert not (SRC / "utils" / "provision_overlay.py").exists()
    assert not (SRC / "utils" / "provision_templates.py").exists()
    assert not (REPO / "tests" / "test_provisioner.py").exists()
    # dendrite builder kept under provision/ for claim-time regen
    assert (REPO / "provision" / "dendrite_config.py").exists()
    nc = (REPO / "provision" / "netconfig.py").read_text()
    assert "provision_templates" not in nc
    assert "dendrite_config" in nc


def test_agent_provision_has_no_overlay_import():
    text = (SRC / "dashboard" / "routes" / "agent_provision.py").read_text()
    assert "provision_overlay" not in text
    assert "generate_overlay(" not in text
    # retired endpoint still exists as 410
    assert '"/api/flow/generate-overlay"' in text
    assert "status_code=410" in text
    assert "overlay_retired" in text


def test_agent_provision_syntax():
    text = (SRC / "dashboard" / "routes" / "agent_provision.py").read_text()
    ast.parse(text)


def test_top_level_migrations_dir_gone():
    assert not (REPO / "migrations").exists(), (
        "top-level migrations/ should be removed; live set is docker/migrations/"
    )


def test_docker_migrations_unique_numeric_prefixes():
    mig = REPO / "docker" / "migrations"
    assert mig.is_dir()
    # Files like 003_foo.sql / 003b_bar.sql — the pure numeric prefix before
    # optional letter must not collide across two *different* bare numbers only.
    # We require full stems' leading NNN (and optional letter) to be unique
    # as the sort key group: no two files share the exact same leading token
    # before the first underscore of the descriptive name... Simpler rule from
    # the ticket: no two files share the same NNN_ prefix without a letter.
    bare = []
    for p in mig.glob("*.sql"):
        m = re.match(r"^(\d+)([a-z]?)_", p.name)
        if not m:
            continue  # unnumbered (add-*.sql) — fine
        bare.append((m.group(1), m.group(2), p.name))
    # Group by pure number; at most one file may have empty letter suffix per number
    # Actually ticket is "dup migration numbers" — two files both named 003_*.
    # After rename we should have unique (number, letter) pairs and no two
    # files with the same full prefix token (e.g. two "003_").
    prefixes = []
    for p in mig.glob("*.sql"):
        m = re.match(r"^(\d+[a-z]?)_", p.name)
        if m:
            prefixes.append(m.group(1))
    assert len(prefixes) == len(set(prefixes)), (
        f"duplicate migration number prefixes: "
        f"{sorted(x for x in prefixes if prefixes.count(x) > 1)}"
    )


def test_requirements_lock_header_no_stuart_container():
    text = (REPO / "requirements.lock").read_text().splitlines()[:6]
    joined = "\n".join(text)
    assert "stuart-cove-app" not in joined
    assert "cove-app-container" in joined or "Lucid Cove" in joined


def test_open_source_cleanliness_skip_no_overlay():
    text = (REPO / "tests" / "test_open_source_cleanliness.py").read_text()
    # Extract the SKIP_FILES = {...} literal only (comments may still mention the name)
    import re
    m = re.search(r"SKIP_FILES\s*=\s*\{([^}]*)\}", text)
    assert m, "SKIP_FILES assignment not found"
    body = m.group(1)
    assert "provision_overlay" not in body
    assert "family.config.example" not in body


def test_centralized_notes_retirement():
    text = (REPO / "provision" / "centralized.py").read_text()
    assert "#SEC5" in text or "#99" in text
    assert "retired" in text.lower()
