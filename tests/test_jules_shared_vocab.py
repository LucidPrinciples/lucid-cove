"""Jules = Operator transcript tool — baked into every agent prompt."""
from src.agents import identity


def test_shared_product_vocab_names_jules_as_tool_not_agent():
    block = identity._shared_product_vocab_block()
    assert "Jules" in block
    assert "tool" in block.lower()
    assert "Operator" in block
    assert "not an agent" in block.lower() or "not an agent" in block
    # Must not leave room to invent an agent named Jules
    assert "Never invent an agent named Jules" in block
    assert "Julian" in block


def test_build_system_prompt_includes_jules_vocab(monkeypatch):
    # load_agents_config returns dict keyed by agent_id (not nested under "agents")
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
    assert "Shared product vocabulary" in prompt
    assert "Jules" in prompt
    assert "Operator" in prompt
