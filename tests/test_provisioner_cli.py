"""Characterization of the CLI provisioner (provision/provision.py) — the second
half of the #99 safety net.

Pins what generate_personal_agent produces TODAY (a different file set than the
dashboard's generate_overlay) so the template consolidation can be proven not to
change the CLI's output. Pure generation — no DB, no network.
"""
import pathlib
import sys

import yaml

# The CLI lives outside src/; make it importable.
_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "provision"))
import provision as cli  # noqa: E402

_CONFIG = yaml.safe_load((_ROOT / "provision" / "family.config.example.yaml").read_text())


def _pa() -> dict:
    """First personal agent from the example config + the allocations main() adds."""
    pa = dict(_CONFIG["personal_agents"][0])
    pa.setdefault("port", 8201)
    pa.setdefault("db_port", 5435)
    pa.setdefault("proxy_ip", "172.30.0.11")
    return pa


class TestCliPersonalAgent:
    def test_exact_file_set(self, tmp_path):
        cli.generate_personal_agent(_CONFIG, _pa(), tmp_path)
        root = next(tmp_path.iterdir())
        produced = {str(f.relative_to(root)) for f in root.rglob("*") if f.is_file()}
        assert produced == {
            "Caddyfile.snippet",
            "config/agent.yaml",
            "config/personas/atlas.md",
            "deploy-to-p620.sh",
            "docker/.env",
            "docker/docker-compose.yml",
            "docker/init.sql",
            "setup-backup.sh",
            "src/.gitkeep",
        }

    def test_env_is_real_with_steward_routing(self, tmp_path):
        cli.generate_personal_agent(_CONFIG, _pa(), tmp_path)
        root = next(tmp_path.iterdir())
        env = (root / "docker" / ".env").read_text()
        # CLI writes a real .env (not a template) and wires steward DB routing
        assert "STEWARD_DATABASE_URL=" in env
        assert "AtlasCove" in env

    def test_compose_mounts_shared_framework(self, tmp_path):
        cli.generate_personal_agent(_CONFIG, _pa(), tmp_path)
        root = next(tmp_path.iterdir())
        compose = (root / "docker" / "docker-compose.yml").read_text()
        assert "atlas" in compose.lower()
        assert "FRAMEWORK_DIR" in compose  # CLI mounts the shared framework

    def test_setup_backup_script_targets_agent(self, tmp_path):
        cli.generate_personal_agent(_CONFIG, _pa(), tmp_path)
        root = next(tmp_path.iterdir())
        s = (root / "setup-backup.sh").read_text()
        assert "github-atlas-code" in s            # per-repo deploy-key alias
        assert 'AGENT="AtlasCove"' in s             # correct repo name
        assert 'ORG="LucidTunerAI"' in s            # correct org
        assert "docker/.env" in s                   # gitignores secrets
        assert "__AGENT_ID__" not in s              # all tokens substituted

    def test_compose_external_family_scoped_volumes(self, tmp_path):
        cli.generate_personal_agent(_CONFIG, _pa(), tmp_path)
        root = next(tmp_path.iterdir())
        compose = (root / "docker" / "docker-compose.yml").read_text()
        assert "name: atlas-cove-postgres-data" in compose  # {agent_id}-{family}-*
        assert "external: true" in compose
        assert "STUART_MC_URL" in compose                   # steward bridge universal

    def test_agent_yaml_canonical_and_tools_fixed(self, tmp_path):
        # The CLI used to emit a broken agent.yaml: tool prefix `src.tools.`
        # (the loader prepends `src.`, so it became `src.src.tools.` and failed),
        # a nonexistent `system_tools` module, and no routes. Adopting the shared
        # canonical builder fixes all three. Lock that here.
        cli.generate_personal_agent(_CONFIG, _pa(), tmp_path)
        root = next(tmp_path.iterdir())
        ay = (root / "config" / "agent.yaml").read_text()
        assert "tools.memory_tools" in ay          # correct prefix (loader adds src.)
        assert "src.tools." not in ay              # the double-prefix bug is gone
        assert "system_tools" not in ay            # nonexistent module no longer referenced
        assert "routes:" in ay                     # routes now present
        assert "Does not access other family members' private data." in ay


class TestCliAdminAgent:
    """Smoke characterization of generate_admin_agent — the steward/family path.
    The personal-agent tests don't exercise this code, so a shared-template change
    can silently break it (it did once: a removed module template left a dangling
    reference). These guard that the whole admin generation path runs end-to-end.
    """

    def test_generates_key_files_without_error(self, tmp_path):
        cli.generate_admin_agent(_CONFIG, tmp_path)
        root = next(tmp_path.iterdir())
        produced = {str(f.relative_to(root)) for f in root.rglob("*") if f.is_file()}
        for required in (
            "docker/.env", "docker/docker-compose.yml", "docker/init.sql",
            "config/agent.yaml", "config/family.yaml",
            "Caddyfile.snippet", "deploy-to-p620.sh",
        ):
            assert required in produced, f"admin overlay missing {required}"

    def test_caddy_snippet_well_formed(self, tmp_path):
        cli.generate_admin_agent(_CONFIG, tmp_path)
        root = next(tmp_path.iterdir())
        caddy = (root / "Caddyfile.snippet").read_text()
        assert "reverse_proxy" in caddy
        assert caddy.strip().endswith("}")


class TestCliMatrixHomeserver:
    """Characterization of generate_matrix_homeserver (#127) — the per-machine
    Dendrite overlay. One homeserver per machine, keyed to the family domain,
    with real generated secrets (no placeholders) and bots derived from agents.
    """

    def test_exact_file_set(self, tmp_path):
        cli.generate_matrix_homeserver(_CONFIG, tmp_path)
        md = tmp_path / "Matrix"
        produced = {f.name for f in md.iterdir() if f.is_file()}
        assert produced == {
            ".env", "Caddyfile.snippet", "dendrite.yaml",
            "docker-compose.yml", "register.sh", "setup.sh",
            "matrix-cove-setup.py",
        }

    def test_cove_setup_substituted(self, tmp_path):
        cli.generate_matrix_homeserver(_CONFIG, tmp_path)
        txt = (tmp_path / "Matrix" / "matrix-cove-setup.py").read_text()
        # Operator owns the Space; server + cove name + agent invites are baked in.
        assert 'OPERATOR = "jason"' in txt
        assert 'SERVER_NAME = "matrix.cove.lucidcove.org"' in txt
        assert 'COVE_NAME = "Cove"' in txt
        assert '"atlas-cove"' in txt          # an agent invited into the Cove Space
        assert "__OPERATOR__" not in txt        # no unsubstituted tokens
        assert "__INVITE_LIST__" not in txt

    def test_server_name_and_bots_from_config(self, tmp_path):
        cli.generate_matrix_homeserver(_CONFIG, tmp_path)
        dy = yaml.safe_load((tmp_path / "Matrix" / "dendrite.yaml").read_text())
        assert dy["global"]["server_name"] == "matrix.cove.lucidcove.org"
        exempt = dy["client_api"]["rate_limiting"]["exempt_user_ids"]
        # Cove-qualified per the identity model: steward + team + personal agents
        assert "@stuart-cove:matrix.cove.lucidcove.org" in exempt   # steward
        assert "@mercer-cove:matrix.cove.lucidcove.org" in exempt   # team
        assert "@gabe-cove:matrix.cove.lucidcove.org" in exempt     # team
        assert "@atlas-cove:matrix.cove.lucidcove.org" in exempt    # personal
        # bare (unqualified) names must NOT appear — they'd collide across Coves
        assert "@stuart:matrix.cove.lucidcove.org" not in exempt
        assert "@lt:matrix.cove.lucidcove.org" not in exempt

    def test_secrets_generated_not_placeholders_and_matched(self, tmp_path):
        cli.generate_matrix_homeserver(_CONFIG, tmp_path)
        md = tmp_path / "Matrix"
        dy = yaml.safe_load((md / "dendrite.yaml").read_text())
        env = (md / ".env").read_text()
        pw = [l.split("=", 1)[1] for l in env.splitlines()
              if l.startswith("POSTGRES_PASSWORD=")][0]
        assert pw and pw != "changeme_use_strong_password"
        assert pw in dy["global"]["database"]["connection_string"]  # yaml == .env
        assert dy["client_api"]["registration_shared_secret"] != "cove-dendrite-setup-2026"

    def test_compose_valid_and_register_fixed(self, tmp_path):
        cli.generate_matrix_homeserver(_CONFIG, tmp_path)
        md = tmp_path / "Matrix"
        comp = yaml.safe_load((md / "docker-compose.yml").read_text())
        assert comp["services"]["app"]["networks"]["cove-proxy"]["ipv4_address"] == "172.30.0.17"
        assert comp["networks"]["cove-proxy"]["external"] is True
        reg = (md / "register.sh").read_text()
        assert "-it" not in reg                      # the bug that broke account creation
        assert "matrix.cove.lucidcove.org" in reg    # correct server_name (not the stale one)
        caddy = (md / "Caddyfile.snippet").read_text()
        assert "matrix.cove.lucidcove.org {" in caddy and "reverse_proxy 172.30.0.17:8008" in caddy

    def test_caddy_is_federation_ready(self, tmp_path):
        cli.generate_matrix_homeserver(_CONFIG, tmp_path)
        caddy = (tmp_path / "Matrix" / "Caddyfile.snippet").read_text()
        # .well-known server discovery (lets other machines federate over :443)
        assert "/.well-known/matrix/server" in caddy
        assert '"m.server": "matrix.cove.lucidcove.org:443"' in caddy
        # .well-known client discovery (auto base_url for clients)
        assert "/.well-known/matrix/client" in caddy
        assert '"base_url": "https://matrix.cove.lucidcove.org"' in caddy
        assert "__" not in caddy  # all tokens substituted
