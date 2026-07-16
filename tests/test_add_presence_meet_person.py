"""Add Presence Meet pairs the person from step 1 with the new agent.

Admin runs the flow under their own session cookie. Meet used to label the
"joining now" pair with /api/presence/me (the admin) instead of ?person= from
Agent Setup, and display_name could fall back to the agent name. Person and
agent stay distinct.
"""
from pathlib import Path

HTML = Path("src/dashboard/static/action-board/new-agent-setup.html").read_text()


def test_person_helper_uses_step1_params_not_session():
    assert "function _personBeingAdded()" in HTML
    assert "personParam || memberNameParam || forLabelParam" in HTML
    # Must not read display_name from /api/presence/me for the person half
    assert "_personBeingAdded" in HTML


def test_joining_now_pair_uses_person_being_added():
    assert "_presencePairCard(_personName || 'New member'" in HTML
    assert "_personName = _personBeingAdded()" in HTML
    # Regression: admin session name on the new pair
    assert "_presencePairCard(_meName || 'You'" not in HTML


def test_new_presence_label_is_on_whole_box_not_under_agent():
    # Presence = person + agent together; caption belongs on the box.
    assert "boxLabel" in HTML
    assert "new presence" in HTML
    # Joining-now call: empty agentSub, boxLabel = 'new presence'
    assert "'', _newAvatar, 'new presence')" in HTML
    # Must not pass 'new presence' as the agent-only subtitle arg
    assert "agentName,\n                                      'new presence'" not in HTML
    assert "_presencePairCard(_personName || 'New member', '', _agentName,\n                                      'new presence'" not in HTML


def test_meet_copy_names_person_and_agent_separately():
    assert "Meet ${_agentCap} for ${_personCap}" in HTML
    assert "${_personCap} and ${_agentCap} are forming a Presence" in HTML
    assert "${_personCap} and ${_agentCap} are joining the ${familyName} Cove as an admin" in HTML


def test_provision_display_name_never_falls_back_to_agent():
    # Old bug: personParam || memberNameParam || name  (name = agent)
    assert "personParam || memberNameParam || name" not in HTML
    assert "display_name: _selfJoin ? '' : _personForProvision" in HTML
    assert "_personForProvision = _personBeingAdded()" in HTML


def test_provision_requires_person_on_admin_add_presence():
    # JS string uses person\'s — match the stable phrase, not the escape form.
    assert "Missing the person" in HTML and "name from the first step" in HTML
    assert "!_personBeingAdded()" in HTML


def test_result_and_wake_operator_use_person_helper():
    assert "const forWhom = _personBeingAdded()" in HTML
    assert "operator_name: _personBeingAdded()" in HTML


def test_already_in_cove_does_not_drop_admin_session():
    """Admin Add Presence must show founder/existing presences (Cracker 2nd presence).

    Old filter dropped whoever was signed in (the admin) from /api/family members,
    so Meet only showed JOINING NOW + build team with nobody under already-in.
    Session exclusion is self-join only.
    """
    assert "_selfJoin && _meHandle && un === _meHandle" in HTML
    # Must not blindly exclude session handle for all joining flows
    assert "!== _meHandle));" not in HTML
    assert "toLowerCase() !== _meHandle" not in HTML
    # Still has the already-in section
    assert "who's already in the Cove" in HTML
    # Self-join gate uses invite+self, not every admin add
    assert "urlParams.get('self') === '1'" in HTML
    assert "urlParams.get('invite')" in HTML


def test_already_in_excludes_person_being_added_not_admin():
    assert "_joinPerson && (dn === _joinPerson || un === _joinPerson)" in HTML
    assert "_personName = _personBeingAdded()" in HTML
