-- =============================================================================
-- add-referral-edge.sql (#169) — the durable affiliate attribution edge.
-- registry_handles.referred_by = the @handle that recruited this handle. Identity-
-- level (survives a Cove moving hosts / re-forking), per the credit-economy spec §7.
-- The credit-rail purchase walks this to compute L1/L2 on the seller's chain.
-- Idempotent. Apply: RB12 on the hub (lucidcove_shared).
-- Spec: LP-Vault/Reference/commerce-credit-economy-spec.md §7.
-- =============================================================================
ALTER TABLE registry_handles ADD COLUMN IF NOT EXISTS referred_by TEXT;
