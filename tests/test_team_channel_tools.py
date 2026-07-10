"""Team-agent channel tool binding — the _team_agent_key parser. The resolver's
DB/config paths are exercised live; the channel-name judgment lives here."""

from unittest.mock import patch


def _key(channel, registry_keys, config_keys, is_manager=False):
    import src.graphs.channels as ch
    with patch.object(ch, "_is_manager_channel", return_value=is_manager), \
         patch("src.tools.agent_tools.AGENT_TOOL_REGISTRY",
               {k: "x" for k in registry_keys}), \
         patch("src.agents.identity.load_agents_config",
               return_value={k: {} for k in config_keys}):
        return ch._team_agent_key(channel)


KNOWN = ["archimedes", "gabe", "stuart"]


def test_team_agent_day_channel_resolves():
    assert _key("archimedes-day", KNOWN, KNOWN) == "archimedes"


def test_deep_channel_resolves():
    assert _key("gabe-deep", KNOWN, KNOWN) == "gabe"


def test_manager_channel_is_not_team():
    # stuart-day is a manager channel — handled by the config path, never here.
    assert _key("stuart-day", KNOWN, KNOWN, is_manager=True) is None


def test_bare_presence_channels_never_match():
    assert _key("day", KNOWN, KNOWN) is None
    assert _key("deep", KNOWN, KNOWN) is None


def test_unknown_agent_never_matches():
    assert _key("bartholomew-day", KNOWN, KNOWN) is None


def test_registry_agent_missing_from_cove_config_never_matches():
    # In the registry (code ships everywhere) but NOT provisioned in THIS Cove.
    assert _key("archimedes-day", KNOWN, ["stuart"]) is None


def test_non_day_deep_suffix_never_matches():
    assert _key("archimedes-ops", KNOWN, KNOWN) is None
