-- TED's estimated and awarded values describe lifecycle stages, not
-- net/gross VAT amounts. Preserve them explicitly and repair rows written
-- by the previous mapping.

ALTER TABLE ted_notice_details
    ADD COLUMN IF NOT EXISTS estimated_value NUMERIC(20,2),
    ADD COLUMN IF NOT EXISTS awarded_value NUMERIC(20,2),
    ADD COLUMN IF NOT EXISTS currency CHAR(3) DEFAULT 'EUR';

UPDATE ted_notice_details AS details
SET estimated_value = COALESCE(details.estimated_value, acts.amount_net),
    awarded_value = COALESCE(details.awarded_value, acts.amount_gross),
    currency = COALESCE(details.currency, acts.currency, 'EUR')
FROM procurement_acts AS acts
WHERE acts.id = details.act_id
  AND acts.act_type = 'TED_NOTICE';

UPDATE procurement_acts
SET amount_net = COALESCE(amount_gross, amount_net),
    amount_gross = NULL,
    vat_amount = NULL,
    updated_at = now()
WHERE act_type = 'TED_NOTICE'
  AND amount_gross IS NOT NULL;
