"""
Agent Overlay Generator — creates a deploy-ready overlay directory for a new personal agent.

Given the provisioned agent data (from the wizard), this generates:
  - docker/docker-compose.yml
  - docker/init.sql
  - docker/.env.template
  - config/agent.yaml
  - config/family.yaml (copy from source Cove)
  - config/personas/{agent_id}.md
  - config/cove.yaml (copy from source Cove)
  - deploy-to-p620.sh

The generated overlay follows the exact same pattern as AtlasCove — separate container,
separate DB, overlay on cove-core, Caddy route. The deploy script syncs to P620 and
starts the container.

Usage:
    from src.utils.provision_overlay import generate_overlay
    result = generate_overlay(
        agent_name="Holden",
        agent_id="holden",
        member_id="jeff",
        operator_name="Jeff",
        port=8202,
        db_port=5441,
        proxy_ip="172.30.0.12",
        provisioned_dir="/app/data/provisioned",
        output_dir="/app/data/provisioned/overlay",
    )
"""

import os
import secrets
import shutil
import textwrap
from pathlib import Path

import yaml

from .provision_templates import (
    FOUNDER_MODEL_PROVIDERS, build_agent_yaml, build_backup_setup_script,
    build_caddy_block, build_deploy_script, build_docker_compose, build_init_sql,
    model_env_keys,
)


# =============================================================================
# Port / IP allocation for the Cove
# =============================================================================

# Known allocations — FULL TABLE from Ops-Reference
# ALWAYS check this before assigning. Update after each provisioning.
USED_PORTS = {8200, 8201, 8202, 8203, 8204, 8205, 8206, 8207, 8222, 8300, 8008, 8009, 8080, 8081, 8448, 3000, 5001}
USED_DB_PORTS = {5434, 5435, 5436, 5437, 5438, 5439, 5440, 5441}
USED_IPS = {10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21}  # .10 through .21


def _next_free_port(start: int = 8207) -> int:
    """Find next free app port."""
    port = start
    while port in USED_PORTS:
        port += 1
    return port


def _next_free_db_port(start: int = 5442) -> int:
    """Find next free DB port."""
    port = start
    while port in USED_DB_PORTS:
        port += 1
    return port


def _next_free_ip() -> str:
    """Find next free IP on 172.30.0.x."""
    suffix = 22
    while suffix in USED_IPS:
        suffix += 1
    return f"172.30.0.{suffix}"


def _get_allocation(member_id: str, family_config: dict) -> dict:
    """Get port/IP allocation for a family member."""
    members = family_config.get("members", [])
    member = next((m for m in members if m["id"] == member_id), None)
    if not member:
        raise ValueError(f"Member '{member_id}' not found in family.yaml")

    # Use mc_port from family.yaml if set and free, otherwise find next free
    configured_port = member.get("mc_port")
    if configured_port and configured_port not in USED_PORTS:
        port = configured_port
    else:
        port = _next_free_port()

    db_port = _next_free_db_port()
    proxy_ip = _next_free_ip()

    return {
        "port": port,
        "db_port": db_port,
        "proxy_ip": proxy_ip,
    }


# =============================================================================
# Template generators
# =============================================================================

def _docker_compose(agent_name: str, agent_id: str, member_id: str,
                    operator_name: str, port: int, db_port: int,
                    proxy_ip: str, family_name: str,
                    db_password: str = "") -> str:
    """Generate docker-compose.yml for a personal agent container."""
    family_lc = family_name.lower()
    home_dir = "/home/lphomebase"
    nc_user = member_id  # NC user = family member ID
    extra_volumes = (
        f"# ── NC AgentSkills ──\n"
        f"      - {home_dir}/nextcloud-data/{nc_user}/files/AgentSkills:/vault/AgentSkills:ro\n"
        f"      # ── Shared framework (LP Knowledge Base) ──\n"
        f'      - "{home_dir}/CLAUDE SKILLS/LP-Vault/Knowledge Base:/shared/framework:ro"\n'
        f"      # ── Content ──\n"
        f"      - {home_dir}/nextcloud-data/{nc_user}/files/AgentSkills/Content:/content:ro\n"
        f"      # ── Host data ──\n"
        f"      - /data:/host-data:ro"
    )
    return build_docker_compose(
        agent_system_name=f"{agent_name}Cove", agent_id=agent_id,
        agent_name=agent_name, db_name=f"{agent_id}_{family_lc}",
        db_port=db_port, port=port, timezone="America/New_York",
        tuning_family="covington", nextcloud_user=nc_user,
        nextcloud_public_url="https://cloud.cove.lucidcove.org",
        operator_name=operator_name, proxy_ip=proxy_ip,
        proxy_network="cove-proxy", family_lc=family_lc,
        container_app=f"{agent_id}-{family_lc}-app",
        container_postgres=f"{agent_id}-{family_lc}-postgres",
        compose_project=f"{agent_id}-{family_lc}", home_dir=home_dir,
        model_providers=FOUNDER_MODEL_PROVIDERS, overlay_mode=":ro",
        extra_volumes=extra_volumes,
    )


def _init_sql(agent_id: str, agent_name: str, archetype: str,
              operator_id: str) -> str:
    """Generate init.sql for a personal agent database."""
    return build_init_sql(
        title=f"{agent_name}Cove",
        description="Base schema from cove-core + agent-specific seeds.",
        agent_id=agent_id,
        agent_name=agent_name,
        archetype=archetype,
        operator_id=operator_id,
        overlay_tables=True,
    )


def _agent_yaml(agent_name: str, agent_id: str, archetype: str,
                operator_name: str, operator_id: str, family_name: str,
                port: int, frequency: str, frequency_color: str,
                tuning_key: str, pronouns: str, role: str,
                qualities: list, emoji: str) -> str:
    """Generate agent.yaml for a personal agent."""
    return build_agent_yaml(
        agent_name=agent_name, agent_id=agent_id, archetype=archetype,
        operator_name=operator_name, operator_id=operator_id,
        family_name=family_name, port=port,
        accent_color=frequency_color, tuning_key=tuning_key,
        frequency=frequency, frequency_color=frequency_color,
        pronouns=pronouns, role=role, emoji=emoji,
    )


def _cove_yaml(operator_name: str, operator_id: str, family_name: str) -> str:
    """Generate cove.yaml for a personal agent with the correct operator."""
    config = {
        "cove": {
            "id": "stuart-cove",
            "name": "Covington",
            "operator": {
                "name": operator_name,
                "id": operator_id,
                "contact": "",
                "aliases": [],
            },
            "billing": {
                "plan": "cove",
                "affiliate_code": "LP1015",
            },
            "timezone": "America/New_York",
            "team": {
                "admin_agent": "stuart",
            },
            "steward_channel": {
                "enabled": True,
                "operator_only": True,
                "agent_id": "stuart",
                "name": "Stuart",
                "archetype": "The Steward",
                "emoji": "\U0001F3E0",
                "role": "Family steward. Coordinates family projects, logistics, schedules, and infrastructure.",
                "description": "Family steward — coordination, requests, household operations",
                "admin_url": "https://stuart.cove.lucidcove.org",
                "channels": {
                    "day": {
                        "description": "Daily family coordination — tasks, schedules, delegation.",
                        "system_addition": "You are **Stuart**, the family steward, in the **Day** channel.\nThe operator speaking with you is **{operator}**.\nYou remember conversations with ALL operators in this Cove.\n\nFocus on: daily family coordination, task delegation, schedules, logistics.\nKeep responses focused and actionable.",
                        "rotation_threshold": 40,
                    },
                    "deep": {
                        "description": "Family strategy — long-term planning, patterns, Cove direction.",
                        "system_addition": "You are **Stuart**, the family steward, in the **Deep** channel.\nThe operator speaking with you is **{operator}**.\nYou remember conversations with ALL operators in this Cove.\n\nFocus on: family strategy, long-term planning, Cove architecture.\nTake time to think carefully.",
                        "rotation_threshold": 30,
                    },
                },
            },
            "merchant_channel": {
                "enabled": True,
                "operator_only": True,
                "agent_id": "mercer",
                "name": "Mercer",
                "archetype": "The Merchant",
                "emoji": "\U0001F4E6",
                "role": "Domain manager for income generation — commerce, sales, marketing, product.",
                "description": "Business operations — products, sales, marketing, income",
                "admin_url": "https://mercer.cove.lucidcove.org",
                "channels": {
                    "day": {
                        "description": "Business operations — products, listings, sales, marketing.",
                        "system_addition": "You are **Mercer**, the domain manager for business operations, in the **Day** channel.\nThe operator speaking with you is **{operator}**.\nYou remember conversations with ALL operators in this Cove.\n\nFocus on: product management, sales, marketing, business execution.\nKeep responses focused and actionable.",
                        "rotation_threshold": 40,
                    },
                    "deep": {
                        "description": "Business strategy — market positioning, growth, product direction.",
                        "system_addition": "You are **Mercer**, the domain manager for business operations, in the **Deep** channel.\nThe operator speaking with you is **{operator}**.\nYou remember conversations with ALL operators in this Cove.\n\nFocus on: business strategy, growth planning, product roadmap.\nTake time to think carefully.",
                        "rotation_threshold": 30,
                    },
                },
            },
        }
    }
    return yaml.dump(config, default_flow_style=False, allow_unicode=True, sort_keys=False)


def _env_template(agent_id: str, operator_id: str, db_port: int,
                  db_password: str = "",
                  model_providers=FOUNDER_MODEL_PROVIDERS) -> str:
    """Generate .env.template for a personal agent."""
    pw = db_password or secrets.token_urlsafe(32)
    return textwrap.dedent(f"""\
        # =============================================================================
        # {agent_id}-cove — Environment Variables
        # =============================================================================
        # Copy this to .env and fill in the secrets.
        # This file is gitignored — secrets never leave the host.
        # =============================================================================

        # Database
        POSTGRES_USER={agent_id}
        POSTGRES_PASSWORD={pw}
        POSTGRES_DB={agent_id}_cove

        # API Keys (copy from Atlas .env — same keys, all agents share)
        __MODEL_KEYS__

        # Ollama (local models on P620)
        OLLAMA_BASE_URL=http://host.docker.internal:11434

        # Nextcloud
        NEXTCLOUD_URL=http://host.docker.internal:8080
        NEXTCLOUD_USER={operator_id}
        NEXTCLOUD_PASSWORD=

        # Agent identity
        AGENT_ID={agent_id}-cove
        OPERATOR_NAME=

        # Stuart bridge + shared Steward/Merchant DB (#138).
        # Reached by CONTAINER NAME over the cove-proxy network, not host ports —
        # the steward/merchant postgres join cove-proxy; no host exposure needed.
        STUART_MC_URL=http://host.docker.internal:8200
        STEWARD_DATABASE_URL=postgresql://stuart:STUART_DB_PASSWORD@stuart-cove-postgres:5432/stuart_cove
        MERCHANT_DATABASE_URL=postgresql://mercer:MERCER_DB_PASSWORD@mercer-cove-postgres:5432/mercer_cove

        # Shared container (VPS)
        SHARED_CONTAINER_URL=https://app.lucidcove.org
        SHARED_CONTAINER_SECRET=

        # Timezone
        APP_TIMEZONE=America/New_York

        # LTP
        LTP_DRY_RUN=false
        TUNING_DELIVERY=git
        TUNING_FAMILY=covington
    """).replace("__MODEL_KEYS__", model_env_keys(model_providers))


def _deploy_script(agent_name: str, agent_id: str, port: int) -> str:
    """Generate deploy-to-p620.sh for a personal agent (canonical, founder values)."""
    return build_deploy_script(
        agent_system_name=f"{agent_name}Cove", agent_id=agent_id,
        domain="cove.lucidcove.org", ssh_user="lphomebase",
        ssh_host="lp-homebase.mesh.lucidcove.org", home_dir="/home/lphomebase",
        family_lc="cove", port=port, container_app=f"{agent_id}-cove-app",
    )


def _caddy_block(agent_id: str, port: int, proxy_ip: str) -> str:
    """Generate the Caddy reverse proxy block to add to the Caddyfile."""
    return build_caddy_block(
        comment=f"# {agent_id.capitalize()} Cove — personal agent",
        agent_id=agent_id,
        domain="cove.lucidcove.org",
        proxy_ip=proxy_ip,
        port=port,
    )


# =============================================================================
# Main generator
# =============================================================================

def generate_overlay(
    agent_name: str,
    agent_id: str,
    agent_data: dict,
    member_id: str,
    operator_name: str,
    family_config: dict,
    family_name: str = "Cove",
    output_dir: str = "/app/data/provisioned/overlay",
) -> dict:
    """Generate a complete overlay directory for a new personal agent.

    Args:
        agent_name: Display name (e.g. "Holden")
        agent_id: Lowercase ID (e.g. "holden")
        agent_data: Full agent config dict from the provisioner
        member_id: Family member ID (e.g. "jeff")
        operator_name: Operator display name (e.g. "Jeff")
        family_config: Parsed family.yaml dict
        family_name: Cove family name (e.g. "Cove")
        output_dir: Where to write the overlay

    Returns:
        Dict with paths, ports, and next steps.
    """
    # Get allocation
    alloc = _get_allocation(member_id, family_config)
    port = alloc["port"]
    db_port = alloc["db_port"]
    proxy_ip = alloc["proxy_ip"]

    archetype = agent_data.get("archetype", "The Guide")
    frequency = agent_data.get("frequency", "Peace")
    frequency_color = agent_data.get("frequency_color", "#5ce1e6")
    tuning_key = agent_data.get("tuning_key", "")
    pronouns = agent_data.get("pronouns", "it/its")
    role = agent_data.get("role", f"Personal agent — {archetype}")
    qualities = agent_data.get("qualities", [])
    emoji = agent_data.get("emoji", "🌟")

    # Generate a strong random password for the DB (used in both compose and .env)
    db_password = secrets.token_urlsafe(32)

    # Create output directory structure
    overlay_root = Path(output_dir) / f"{agent_name}Cove"
    (overlay_root / "docker").mkdir(parents=True, exist_ok=True)
    (overlay_root / "config" / "personas").mkdir(parents=True, exist_ok=True)

    # Generate files
    # docker-compose.yml
    (overlay_root / "docker" / "docker-compose.yml").write_text(
        _docker_compose(agent_name, agent_id, member_id, operator_name,
                        port, db_port, proxy_ip, family_name,
                        db_password=db_password)
    )

    # init.sql
    (overlay_root / "docker" / "init.sql").write_text(
        _init_sql(agent_id, agent_name, archetype, member_id)
    )

    # .env.template (pre-filled with the generated password)
    (overlay_root / "docker" / ".env.template").write_text(
        _env_template(agent_id, member_id, db_port, db_password=db_password)
    )

    # agent.yaml
    (overlay_root / "config" / "agent.yaml").write_text(
        _agent_yaml(agent_name, agent_id, archetype, operator_name, member_id,
                     family_name, port, frequency, frequency_color,
                     tuning_key, pronouns, role, qualities, emoji)
    )

    # Copy persona from provisioned
    persona_src = Path("/app/data/provisioned/personas") / f"{agent_id}.md"
    persona_dst = overlay_root / "config" / "personas" / f"{agent_id}.md"
    if persona_src.exists():
        shutil.copy2(persona_src, persona_dst)

    # Generate cove.yaml with correct operator (NOT copied from source)
    (overlay_root / "config" / "cove.yaml").write_text(
        _cove_yaml(operator_name, member_id, family_name)
    )

    # Copy family.yaml and profile.yaml from current config
    config_dir = Path("/app/config")
    for fname in ["family.yaml", "profile.yaml"]:
        src = config_dir / fname
        if src.exists():
            shutil.copy2(src, overlay_root / "config" / fname)

    # Copy overlay route files (bridge.py, tasks_nc.py) from source agent
    overlay_routes = overlay_root / "src" / "dashboard" / "routes"
    overlay_routes.mkdir(parents=True, exist_ok=True)
    source_overlay = Path("/overlay/src/dashboard/routes")
    for route_file in ["bridge.py", "tasks_nc.py"]:
        src = source_overlay / route_file
        if src.exists():
            shutil.copy2(src, overlay_routes / route_file)

    # deploy-to-p620.sh
    deploy_path = overlay_root / "deploy-to-p620.sh"
    deploy_path.write_text(_deploy_script(agent_name, agent_id, port))
    deploy_path.chmod(0o755)

    # setup-backup.sh — one-time backup wiring (run once on host after deploy)
    backup_path = overlay_root / "setup-backup.sh"
    backup_path.write_text(build_backup_setup_script(
        agent_system_name=f"{agent_name}Cove", agent_id=agent_id))
    backup_path.chmod(0o755)

    # Caddy block (written as a reference file, not auto-applied)
    (overlay_root / "caddy-route.txt").write_text(
        _caddy_block(agent_id, port, proxy_ip)
    )

    # NC provisioning command reference
    (overlay_root / "nc-setup.sh").write_text(textwrap.dedent(f"""\
        #!/bin/bash
        # =============================================================================
        # Nextcloud user provisioning for {operator_name} ({member_id})
        # =============================================================================
        # Run on P620 via SSH. Creates NC user, folder structure, and calendar.
        # =============================================================================

        set -e
        NC="nextcloud-app"
        USER="{member_id}"
        DATA="/var/www/html/data/$USER/files"

        NC_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
        echo "[1/6] Creating NC user..."
        docker exec -u 33 -e OC_PASS="$NC_PASSWORD" $NC php occ user:add \\
            --display-name="{operator_name}" \\
            --password-from-env $USER

        echo "[2/6] Creating folder structure..."
        docker exec $NC mkdir -p \\
            $DATA/AgentSkills/{{Inbox,Inbox/Archive,Ops,Content,Context,Sites,Flows,Actions,Shared}} \\
            $DATA/Documents \\
            $DATA/InstantUpload/{{Camera,Screenshots}} \\
            $DATA/Notes \\
            $DATA/Projects
        docker exec $NC touch "$DATA/\\`Ideas.md"

        echo "[3/6] Removing NC default files..."
        docker exec $NC rm -rf \\
            $DATA/Documents/Example.md \\
            $DATA/Templates \\
            "$DATA/Nextcloud intro.mp4" \\
            "$DATA/Nextcloud Manual.pdf" \\
            $DATA/Nextcloud.png \\
            $DATA/Readme.md \\
            "$DATA/Reasons to use Nextcloud.pdf" \\
            "$DATA/Templates credits.md" \\
            $DATA/Photos \\
            2>/dev/null || true

        echo "[4/6] Fixing ownership..."
        docker exec $NC chown -R www-data:www-data /var/www/html/data/$USER/

        echo "[5/6] Scanning files..."
        docker exec -u 33 $NC php occ files:scan $USER

        echo "[6/6] Creating default calendar..."
        docker exec -u 33 $NC php occ dav:create-calendar $USER personal

        echo ""
        echo "NC user '$USER' provisioned."
        echo "Password: $NC_PASSWORD"
        echo "Set this in the .env: NEXTCLOUD_PASSWORD=$NC_PASSWORD"
    """))
    (overlay_root / "nc-setup.sh").chmod(0o755)

    return {
        "overlay_dir": str(overlay_root),
        "agent_name": agent_name,
        "agent_id": agent_id,
        "port": port,
        "db_port": db_port,
        "proxy_ip": proxy_ip,
        "container": f"{agent_id}-cove-app",
        "url": f"https://{agent_id}.cove.lucidcove.org",
        "caddy_route": _caddy_block(agent_id, port, proxy_ip),
        "next_steps": [
            f"1. Copy overlay to Mac: scp -r /app/data/provisioned/overlay/{agent_name}Cove ~/Documents/LucidCove/Haven/Cove/",
            f"2. Copy .env.template to .env and fill in secrets (copy API keys from AtlasCove/.env)",
            f"3. Add Caddy route (see caddy-route.txt)",
            f"4. Run NC setup: bash nc-setup.sh",
            f"5. Deploy: cd {agent_name}Cove && bash deploy-to-p620.sh",
        ],
    }
