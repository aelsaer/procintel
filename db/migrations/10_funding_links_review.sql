-- 10_funding_links_review.sql
-- Adds a review-queue marker to funding_links, mirroring act_links' own
-- reviewed_by column: description.txt §19.2 Level 4 ("normalized title +
-- similar amount + same region + same beneficiary") explicitly requires
-- mandatory review when confidence isn't high. NULL means "not yet
-- reviewed" (the pending-review state for a Level 4 match); act_links
-- already has this column, funding_links didn't.
-- Spec refs: description.txt §19.2, §8 (review queue concept), §25.

ALTER TABLE funding_links ADD COLUMN reviewed_by UUID;
