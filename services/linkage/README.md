# services/linkage

**This package is an empty placeholder — there is no code here.** The
cross-act/cross-source linkage engine described below (spec §8,
§16.5-16.6, §19.2, §20.1, §21.3: the confidence hierarchy from official
source relation → exact identifier → multi-attribute → fuzzy → human
review, `act_links`/`funding_links` writes, stable `process_id`
management) exists, but distributed per-connector rather than centralized
here:

| Where the real logic lives | What it links |
|---|---|
| `services/ingestion/connectors/khmdhs/adamchain.py` | ΑΔΑΜ chain resolution (`decisionRelatedAdam`/`contractRelatedAdam`/...) — the primary `process_members`/process-grouping mechanism |
| `services/ingestion/connectors/diavgeia/resolve.py` | ΑΔΑ-referenced Διαύγεια decisions → `act_links(APPROVES, ...)`, confidence 1.0 (direct fetch) or 0.75 (SEARCH fallback) |
| `services/ingestion/connectors/ted/resolve.py` | TED notice ↔ ΚΗΜΔΗΣ process matching → `act_links` |
| `services/ingestion/connectors/anaptyxi/resolve.py` | ΕΣΠΑ/ΑΝΑΠΤΥΞΗ funding project ↔ process matching → `funding_links` |
| `services/ingestion/connectors/mef/resolve.py` | ΜΕΦ expense signal ↔ process matching |
| `services/ingestion/on_demand.py` | On-demand (user-triggered) fetch-and-link path, reusing the same per-connector resolvers |
| `services/entity_resolution/` | A *different* concern — company/person entity dedup (§8's entity-matching hierarchy), not act/process linkage. See its own README. |

Every one of the modules above independently implements the same
confidence-hierarchy shape (official relation first, exact identifier
next, fuzzy/similarity last), since each source's "how do I know this
links to that" evidence is genuinely different — an ΑΔΑ reference isn't
the same shape as a TED notice number match. Consolidating them into one
shared engine module (what this package's name promises) is a real,
worthwhile refactor, just not one that's been done yet — don't add new
code here expecting it to be picked up; wire new linkage logic into the
relevant connector's own `resolve.py` instead, following the existing
pattern.
