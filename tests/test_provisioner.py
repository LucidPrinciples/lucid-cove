"""Characterization tests for the personal-agent provisioner (#99 safety net).

These pin what src/utils/provision_overlay.generate_overlay produces TODAY, so
the consolidation of provision.py (CLI) onto this module can be verified to
change structure, not behavior. Pure generation — no DB, no network.
"""
from src.utils import provision_overlay as prov


def _generate(tmp: str) -> dict:
    return prov.generate_overlay(
        agent_name="Holden",
        agent_id="holden",
        agent_data={
            "archetype": "The Guide",
            "frequency": "Peace",
            "frequency_color": "#5ce1e6",
            "role": "Personal agent",
            "pronouns": "he/him",
            "emoji": "🌟",
            "qualities": ["curious", "steady"],
        },
        member_id="jeff",
        operator_name="Jeff",
        family_config={"members": [{"id": "jeff", "mc_port": 8210}], "family_name": "Cove"},
        family_name="Cove",
        output_dir=tmp,
    )


class TestGenerateOverlay:
    def test_return_shape(self, tmp_path):
        res = _generate(str(tmp_path))
        assert res["agent_id"] == "holden"
        assert res["agent_name"] == "Holden"
        assert res["container"] == "holden-cove-app"
        assert res["port"] == 8210
        assert isinstance(res["db_port"], int)
        for k in ("caddy_route", "next_steps", "overlay_dir", "proxy_ip", "url"):
            assert k in res

    def test_exact_file_set(self, tmp_path):
        _generate(str(tmp_path))
        root = tmp_path / "HoldenCove"
        produced = {str(f.relative_to(root)) for f in root.rglob("*") if f.is_file()}
        assert produced == {
            "caddy-route.txt",
            "config/agent.yaml",
            "config/cove.yaml",
            "deploy-to-p620.sh",
            "docker/.env.template",
            "docker/docker-compose.yml",
            "docker/init.sql",
            "nc-setup.sh",
            "setup-backup.sh",
        }

    def test_compose_identity(self, tmp_path):
        _generate(str(tmp_path))
        compose = (tmp_path / "HoldenCove" / "docker" / "docker-compose.yml").read_text()
        assert "name: holden-cove" in compose
        assert "container_name: holden-cove-app" in compose
        assert "container_name: holden-cove-postgres" in compose
        assert "image: pgvector/pgvector:pg16" in compose
        assert "8210" in compose
        # compose references the secret from .env, never hardcodes it
        assert "${POSTGRES_PASSWORD}" in compose

    def test_compose_canonical_volumes_and_env(self, tmp_path):
        # After #99 the dashboard adopts the canonical compose: family-scoped
        # external volumes (for the founder family slug "cove" these equal the
        # existing P620 names, so no data migration) + the universal env.
        _generate(str(tmp_path))
        compose = (tmp_path / "HoldenCove" / "docker" / "docker-compose.yml").read_text()
        assert "name: holden-cove-postgres-data" in compose  # family-scoped volume
        assert "external: true" in compose                   # pre-created volumes
        assert "NEXTCLOUD_PUBLIC_URL" in compose             # NC universal
        assert "STUART_MC_URL" in compose                    # steward bridge universal

    def test_deploy_script_is_canonical(self, tmp_path):
        # The dashboard now emits the canonical deploy script (the thorough one):
        # verifies cove-core dir, syncs src/, family-scoped volume create.
        _generate(str(tmp_path))
        deploy = (tmp_path / "HoldenCove" / "deploy-to-p620.sh").read_text()
        assert "test -d /home/lphomebase/cove-core/src" in deploy  # cove-core dir check
        assert "holden-cove-postgres-data" in deploy              # family-scoped volume
        assert "holden.cove.lucidcove.org" in deploy

    def test_env_template_carries_a_real_secret(self, tmp_path):
        _generate(str(tmp_path))
        env = (tmp_path / "HoldenCove" / "docker" / ".env.template").read_text()
        assert "POSTGRES_USER=holden" in env
        assert "POSTGRES_DB=holden_cove" in env
        pw_line = next(l for l in env.splitlines() if l.startswith("POSTGRES_PASSWORD="))
        # a generated password, not an empty placeholder
        assert len(pw_line.split("=", 1)[1]) >= 20

    def test_init_sql_targets_the_agent(self, tmp_path):
        _generate(str(tmp_path))
        sql = (tmp_path / "HoldenCove" / "docker" / "init.sql").read_text()
        assert "holden" in sql.lower()
        assert "create table" in sql.lower()
