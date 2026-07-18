-- #QL-SPACER: section dividers inside a quick list (produce / dairy / one-offs).
-- item_type = 'item' (default) | 'spacer'. Spacers are not checkable and do not
-- count toward unchecked badges. Label lives in `text` (may be empty).
ALTER TABLE quick_list_items
    ADD COLUMN IF NOT EXISTS item_type TEXT NOT NULL DEFAULT 'item';

-- Guardrail: only known types. Existing rows stay 'item'.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'quick_list_items_item_type_chk'
    ) THEN
        ALTER TABLE quick_list_items
            ADD CONSTRAINT quick_list_items_item_type_chk
            CHECK (item_type IN ('item', 'spacer'));
    END IF;
END $$;
