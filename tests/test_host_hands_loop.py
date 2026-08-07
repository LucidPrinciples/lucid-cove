"""Host-hands loop — always-on prompt + manager path + Mac-safe skill."""
from src.agents import identity


def test_host_hands_block_is_non_negotiable_and_mac_safe():
    block = identity._host_hands_block()
    assert "Host-hands loop" in block
    assert "non-negotiable" in block
    assert "first reply" in block.lower() or "On the first reply" in block
    assert "nested heredoc" in block.lower() or "Never** nested heredoc" in block
    assert "paste" in block.lower()
    # Must not leave room to stall on isolation
    assert "never wait for the operator to remind" in block.lower() or "never wait" in block.lower()


def test_build_system_prompt_includes_host_hands(monkeypatch):
    monkeypatch.setattr(
        identity,
        "load_agents_config",
        lambda: {
            "stuart": {
                "name": "Stuart",
                "archetype": "The Steward",
                "role": "ops",
                "status": "active",
            }
        },
    )
    monkeypatch.setattr(identity, "load_persona", lambda _aid: "")
    monkeypatch.setattr(identity, "_charter_block", lambda: "")

    prompt = identity.build_system_prompt("stuart")
    assert "Host-hands loop" in prompt
    assert "nested heredoc" in prompt.lower() or "Never** nested" in prompt


def test_dev_workflow_boundary_routes_to_host_hands_not_stop():
    """HARD BOUNDARY used to train 'report and stop'; must open host-hands."""
    agent = {
        "name": "Stuart",
        "archetype": "The Steward",
        "role": "Family steward",
        "can_delegate_to": ["archimedes"],
    }
    block = identity._dev_workflow_block(agent)
    assert "Host-hands" in block or "host-hands" in block
    # Markdown may wrap **not** — accept either phrasing
    assert "not** a stop" in block or "not a stop" in block.lower()


def test_manager_prompt_includes_host_hands(monkeypatch):
    """Stuart Clearfield (steward channel) must get host-hands without team agent path."""
    from src.graphs import channels

    monkeypatch.setattr(identity, "load_persona", lambda _aid: "")

    cfg = {
        "name": "Stuart",
        "archetype": "The Steward",
        "role": "Dev steward",
        "agent_id": "stuart-clearfield",
    }
    prompt = channels._build_manager_prompt_from_config(cfg)
    assert "Host-hands loop" in prompt
    assert "Shared product vocabulary" in prompt


def test_host_hands_skill_ships_and_forbids_nested_heredoc():
    from src.skills.loader import load_skill

    skill = load_skill("host-hands")
    assert skill is not None
    body = skill["body"]
    assert "Mac-safe" in body or "mac-safe" in body.lower()
    assert "heredoc" in body.lower()
    assert "first reply" in body.lower()
    assert skill.get("name") == "host-hands"


def test_operator_brevity_block_and_prompts(monkeypatch):
    block = identity._operator_brevity_block()
    assert "half the length" in block.lower()
    assert "what this means" in block.lower() or "happens next" in block.lower()

    monkeypatch.setattr(
        identity,
        "load_agents_config",
        lambda: {
            "stuart": {
                "name": "Stuart",
                "archetype": "The Steward",
                "role": "ops",
                "status": "active",
            }
        },
    )
    monkeypatch.setattr(identity, "load_persona", lambda _aid: "")
    monkeypatch.setattr(identity, "_charter_block", lambda: "")
    prompt = identity.build_system_prompt("stuart")
    assert "How you talk to the operator" in prompt

    from src.graphs import channels

    monkeypatch.setattr(identity, "load_persona", lambda _aid: "")
    mp = channels._build_manager_prompt_from_config(
        {
            "name": "Stuart",
            "archetype": "The Steward",
            "role": "Dev steward",
            "agent_id": "stuart-clearfield",
        }
    )
    assert "How you talk to the operator" in mp
