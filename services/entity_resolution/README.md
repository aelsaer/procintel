# services/entity_resolution

Canonical buyer/supplier identity resolution with explicit confidence and human
review (`description.txt` §8/§25).

## Implemented

- Exact normalized ΑΦΜ resolution with checksum validity and restricted matching
  for invalid identifiers.
- Candidate blocking from identifiers, normalized names and available company
  attributes.
- Fuzzy name similarity, confidence breakdowns and explicit identifier-conflict
  penalties.
- Persisted `entity_match_candidates` review queue with pending, rejected and
  merged states.
- Owner/admin API/UI review, both merge directions, merge history and reversible
  undo through `entity_merge_log`.
- Reference rewrites are transactional and retain the source/evidence trail.

Candidates can be generated from the Archive → Entity review workspace or by
calling `POST /v1/entity-review/generate`. Exact ΑΦΜ matches remain automatic;
fuzzy and conflicting identities remain reviewable rather than silently merged.

## Limits

Confidence is deterministic heuristic scoring, not a learned identity model.
Postal code, email-domain and phone signals only contribute when providers have
actually supplied them. Large installations should schedule candidate generation
in bounded batches and treat merge/undo access as an audited administrative role.
