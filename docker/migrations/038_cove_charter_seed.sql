-- 038: Cove Charter seed — #D58 (behavior-calibration-spec §B).
-- The Charter lives in the system_settings key/value store (024 pattern):
--   charter.mission    — one line, what this Cove is for (wizard seeds it;
--                        admin refines in Cove Settings → Charter)
--   charter.principles — markdown list, how this Cove operates
-- This migration seeds the DEFAULT principles for existing Coves (empty
-- mission — the admin fills it in), so upgraded Coves get the triage/truth
-- principles without waiting on operator action. Fresh installs get the same
-- rows here, then the wizard writes mission at finalize. Idempotent.

INSERT INTO system_settings (key, value)
VALUES ('charter.mission', '')
ON CONFLICT (key) DO NOTHING;

INSERT INTO system_settings (key, value)
VALUES ('charter.principles', E'- Truth over comfort, warmth over coldness. Both, not either.\n- New breakage outranks the plan. When something breaks or a more urgent issue appears mid-task, stop, name it, and re-prioritize with the operator before continuing. Never push the original task through a fire. Finishing the wrong thing is not progress.\n- Say what you don''t know. Never invent file paths, names, numbers, or artifacts.\n- The operator decides priorities. Surface, propose, then follow their call.')
ON CONFLICT (key) DO NOTHING;
