-- Founding operator seed (#140) — runs after 00-base.sql at DB init.
INSERT INTO accounts (id, display_name, username, email, agent_name, last_name,
                      cove_role, tier, auth_token, agent_config, agent_identity)
VALUES ('c945e18c-9f6b-42ac-a3f6-bfe2de4c15ce', '', 'setup-51e6', NULL, '', '',
        'admin', 'cove', 'ae729728f7372599b7ec69159dff3681dae9eb01c24162d1048e8dcd23ffcc0f', '{}', '{}')
ON CONFLICT (id) DO NOTHING;
