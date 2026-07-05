-- =============================================================================
-- 026_gpu_marketplace_grant.sql — marketplace-scoped GPU grants (batch-13 C2).
-- =============================================================================
-- A marketplace purchase (S1 on the hub) calls this Cove's /api/gpu/marketplace-grant
-- to auto-open the GPU. It mints a normal gpu_grant (hash-only token, reusing the
-- one-to-one machinery) but tagged so the provider can see it came from a sale, keep it
-- idempotent per (listing, buyer), and optionally hold it for approval.
--
--   source          — 'manual' (the one-to-one mint) | 'marketplace' (a sale).
--   listing_id      — the hub listing the sale was for.
--   buyer_handle    — who bought it (the registry @handle).
--   approval_status — NULL for auto-granted; 'pending' when require-approval holds it
--                     (the grant is minted revoked=TRUE so it can't verify until the
--                     provider approves → revoked=FALSE, approval_status='approved';
--                     deny leaves it revoked with approval_status='denied').
--
-- Idempotency per (listing, buyer): the partial unique index below + ON CONFLICT.
-- Additive + idempotent.
-- =============================================================================

ALTER TABLE gpu_grants ADD COLUMN IF NOT EXISTS source          TEXT NOT NULL DEFAULT 'manual';
ALTER TABLE gpu_grants ADD COLUMN IF NOT EXISTS listing_id      BIGINT;
ALTER TABLE gpu_grants ADD COLUMN IF NOT EXISTS buyer_handle    TEXT;
ALTER TABLE gpu_grants ADD COLUMN IF NOT EXISTS approval_status TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_gpu_grants_marketplace
    ON gpu_grants (listing_id, buyer_handle)
    WHERE source = 'marketplace';
