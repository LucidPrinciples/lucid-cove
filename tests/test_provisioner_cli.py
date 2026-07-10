"""Tests for the centralized provisioner CLI (centralized.py) — #D2.

Rewritten from the legacy per-agent provisioner tests. The centralized model
produces a single-stack Cove with all presences in one container, added later
via the admin UI (magic link), not as separate containers at provision time.

Pure generation — no DB, no network.
"""
import pathlib
import sys

import yaml

# The CLI lives outside src/; make it importable.
_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "provision"))
import centralized as cli  # noqa: E402


_CONFIG = yaml.safe_load((_ROOT / "provision" / "cove.config.example.yaml").read_text())


def _minimal_config() -> dict:
    """Return a minimal valid config for testing."""
    return {
        "cove": {
            "id": "testcove",
            "name": "Test Cove",
            "domain": "",
        },
        "operator": {
            "name": "Test Operator",
            "handle": "testop",
            "email": "",
            "token": "",
        },
        "team": "off",  # solo for faster tests
        "model_providers": ["openrouter"],
        "deploy": {
            "target": "standalone",
            "lucid_cove_path": "/tmp/test-lucid-cove",
            "app_port": 8200,
            "nextcloud_port": 8080,
        },
        "affiliate": {"referred_by": ""},
        "ltp": {"dry_run": True, "kb_public_key": ""},
        "matrix": {"enabled": False},
    }


class TestCliGenerateCove:
    """Characterization of generate_cove — the centralized provisioner's main entry."""

    def test_exact_file_set(self, tmp_path):
        """Produces the expected set of files for a minimal Cove."""
        cfg = _minimal_config()
        cli.generate_cove(cfg, tmp_path)
        root = tmp_path / "testcove-cove"
        produced = {str(f.relative_to(root)) for f in root.rglob("*") if f.is_file()}
        expected = {
            "docker-compose.yml",
            ".env",
            ".gitignore",
            "config/cove.yaml",
            "config/agent.yaml",
            "docker/init-nextcloud-db.sql",
            "docker/operator-seed.sql",
            "docker/nc-hooks/post-installation/20-apps.sh",
            "NEXT_STEPS.md",
            "connect-mesh.sh",
        }
        assert produced == expected, f"Extra/missing: {produced.symmetric_difference(expected)}"

    def test_env_has_required_vars(self, tmp_path):
        """.env contains the critical variables for a working Cove."""
        cfg = _minimal_config()
        cli.generate_cove(cfg, tmp_path)
        env_path = tmp_path / "testcove-cove" / ".env"
        env_text = env_path.read_text()
        # Core identity and config
        assert "COVE_ID=" in env_text
        assert "COVE_NAME=" in env_text
        assert "POSTGRES_USER=" in env_text
        assert "POSTGRES_PASSWORD=" in env_text
        assert "POSTGRES_DB=" in env_text
        # Generated secrets, not placeholders
        assert "NC_ADMIN_PASSWORD=" in env_text
        assert "SHARED_CONTAINER_SECRET=" in env_text
        assert "PIPECAT_INTERNAL_SECRET=" in env_text
        # No unsubstituted tokens
        assert "__" not in env_text

    def test_compose_is_valid_yaml(self, tmp_path):
        """docker-compose.yml parses as valid YAML with expected services."""
        cfg = _minimal_config()
        cli.generate_cove(cfg, tmp_path)
        compose_path = tmp_path / "testcove-cove" / "docker-compose.yml"
        compose = yaml.safe_load(compose_path.read_text())
        assert "services" in compose
        services = compose["services"]
        # Core services always present
        assert "app" in services
        assert "postgres" in services
        assert "redis" in services
        assert "nextcloud" in services

    def test_cove_yaml_has_identity(self, tmp_path):
        """config/cove.yaml captures the Cove identity and operator."""
        cfg = _minimal_config()
        cli.generate_cove(cfg, tmp_path)
        cove_yaml_path = tmp_path / "testcove-cove" / "config" / "cove.yaml"
        cove_cfg = yaml.safe_load(cove_yaml_path.read_text())
        assert cove_cfg["cove"]["id"] == "testcove"
        assert cove_cfg["cove"]["name"] == "Test Cove"
        assert cove_cfg["cove"]["operator"]["handle"] == "testop"
        assert cove_cfg["cove"]["operator"]["name"] == "Test Operator"

    def test_agent_yaml_has_team_when_enabled(self, tmp_path):
        """config/agent.yaml includes team when team: on."""
        cfg = _minimal_config()
        cfg["team"] = "on"
        cli.generate_cove(cfg, tmp_path)
        agent_yaml_path = tmp_path / "testcove-cove" / "config" / "agent.yaml"
        agent_cfg = yaml.safe_load(agent_yaml_path.read_text())
        assert "agents" in agent_cfg
        agent_ids = {a["id"] for a in agent_cfg["agents"]}
        # Standard team members present
        assert "stuart" in agent_ids
        assert "mercer" in agent_ids
        assert "vera" in agent_ids

    def test_agent_yaml_solo_when_team_off(self, tmp_path):
        """config/agent.yaml has minimal config when team: off."""
        cfg = _minimal_config()
        cfg["team"] = "off"
        cli.generate_cove(cfg, tmp_path)
        agent_yaml_path = tmp_path / "testcove-cove" / "config" / "agent.yaml"
        agent_cfg = yaml.safe_load(agent_yaml_path.read_text())
        # Solo mode: just operator's personal agent (no team, no stuart)
        agent_ids = {a["id"] for a in agent_cfg["agents"]}
        assert "agent" in agent_ids
        # No team members
        assert "stuart" not in agent_ids
        assert "mercer" not in agent_ids

    def test_nextcloud_db_init_has_role(self, tmp_path):
        """docker/init-nextcloud-db.sql creates the Nextcloud DB role."""
        cfg = _minimal_config()
        cli.generate_cove(cfg, tmp_path)
        sql_path = tmp_path / "testcove-cove" / "docker" / "init-nextcloud-db.sql"
        sql = sql_path.read_text()
        assert "CREATE ROLE nextcloud" in sql or "CREATE USER nextcloud" in sql
        assert "CREATE DATABASE nextcloud" in sql

    def test_next_steps_documented(self, tmp_path):
        """NEXT_STEPS.md exists and contains deployment instructions."""
        cfg = _minimal_config()
        cli.generate_cove(cfg, tmp_path)
        steps_path = tmp_path / "testcove-cove" / "NEXT_STEPS.md"
        steps = steps_path.read_text()
        assert "docker-compose" in steps.lower() or "docker compose" in steps.lower()
        assert "deploy" in steps.lower() or "start" in steps.lower()

    def test_port_collision_handling(self, tmp_path):
        """App and Nextcloud ports are wired through to the compose."""
        cfg = _minimal_config()
        cfg["deploy"]["app_port"] = 9999
        cfg["deploy"]["nextcloud_port"] = 9998
        cli.generate_cove(cfg, tmp_path)
        compose_path = tmp_path / "testcove-cove" / "docker-compose.yml"
        compose = yaml.safe_load(compose_path.read_text())
        # Ports are published on the host
        app_ports = compose["services"]["app"].get("ports", [])
        nc_ports = compose["services"]["nextcloud"].get("ports", [])
        assert any("9999" in str(p) for p in app_ports)
        assert any("9998" in str(p) for p in nc_ports)

    def test_matrix_enabled_adds_homeserver(self, tmp_path):
        """matrix.enabled: true adds Dendrite services and config."""
        cfg = _minimal_config()
        cfg["matrix"] = {"enabled": True}
        cli.generate_cove(cfg, tmp_path)
        compose_path = tmp_path / "testcove-cove" / "docker-compose.yml"
        compose = yaml.safe_load(compose_path.read_text())
        assert "dendrite" in compose["services"]
        # Matrix config file generated in docker/
        matrix_config = tmp_path / "testcove-cove" / "docker" / "dendrite.yaml"
        assert matrix_config.exists()

    def test_matrix_disabled_no_dendrite(self, tmp_path):
        """matrix.enabled: false does not include Dendrite."""
        cfg = _minimal_config()
        cfg["matrix"] = {"enabled": False}
        cli.generate_cove(cfg, tmp_path)
        compose_path = tmp_path / "testcove-cove" / "docker-compose.yml"
        compose = yaml.safe_load(compose_path.read_text())
        assert "dendrite" not in compose["services"]


class TestCliWithDomain:
    """Tests for domain configuration and DNS/Caddy handling."""

    def test_domain_empty_uses_localhost(self, tmp_path):
        """Empty domain produces localhost-only configuration."""
        cfg = _minimal_config()
        cfg["cove"]["domain"] = ""
        cli.generate_cove(cfg, tmp_path)
        env_path = tmp_path / "testcove-cove" / ".env"
        env_text = env_path.read_text()
        # No domain means no external DNS/Caddy
        assert "NEXTCLOUD_TRUSTED_DOMAIN=localhost" in env_text


class TestCliModelProviders:
    """Tests for model provider configuration."""

    def test_openrouter_wired(self, tmp_path):
        """OpenRouter provider creates placeholder in .env."""
        cfg = _minimal_config()
        cfg["model_providers"] = ["openrouter"]
        cli.generate_cove(cfg, tmp_path)
        env_path = tmp_path / "testcove-cove" / ".env"
        env_text = env_path.read_text()
        assert "OPENROUTER_API_KEY" in env_text

    def test_google_wired(self, tmp_path):
        """Google provider creates placeholder in .env."""
        cfg = _minimal_config()
        cfg["model_providers"] = ["google"]
        cli.generate_cove(cfg, tmp_path)
        env_path = tmp_path / "testcove-cove" / ".env"
        env_text = env_path.read_text()
        assert "GOOGLE_API_KEY" in env_text

    def test_multiple_providers(self, tmp_path):
        """Multiple providers all get placeholders."""
        cfg = _minimal_config()
        cfg["model_providers"] = ["openrouter", "google", "groq"]
        cli.generate_cove(cfg, tmp_path)
        env_path = tmp_path / "testcove-cove" / ".env"
        env_text = env_path.read_text()
        assert "OPENROUTER_API_KEY" in env_text
        assert "GOOGLE_API_KEY" in env_text
        assert "GROQ_API_KEY" in env_text


class TestCliExampleConfig:
    """Tests that the example config in the repo works."""

    def test_example_config_loads(self):
        """The example cove.config.example.yaml is valid YAML."""
        assert _CONFIG is not None
        assert "cove" in _CONFIG
        assert "operator" in _CONFIG

    def test_example_config_has_required_sections(self):
        """Example config has all required top-level sections."""
        required = ["cove", "operator", "team", "model_providers", "deploy", "affiliate", "ltp", "matrix"]
        for key in required:
            assert key in _CONFIG, f"Missing required section: {key}"
