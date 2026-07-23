# services/search_index

OpenSearch full-text indexing for `procurement_acts` — description.txt
§11/§29 ("OpenSearch για full-text και document search").

Uses a small, purpose-built `httpx` REST wrapper (`client.py`), not the
official `opensearch-py` SDK — see the note in `pyproject.toml` for why
(it pulls in unused gRPC/protobuf dependencies and its transport isn't
`httpx`-based, so it can't be exercised with the `respx` mocking this
codebase uses everywhere else).

| Module | Purpose |
|---|---|
| `config.py` | `OpenSearchConfig.from_env()` — `OPENSEARCH_URL` required, `OPENSEARCH_INDEX_NAME`/`OPENSEARCH_USERNAME`/`OPENSEARCH_PASSWORD` optional |
| `mapping.py` | `PROCUREMENT_ACTS_MAPPING` — the index mapping, using OpenSearch's built-in `greek` analyzer for `title`/`buyer_name`/`supplier_names` |
| `client.py` | `index_exists`/`create_index`/`delete_index`/`bulk_index`/`search` — thin REST calls, respx-tested |
| `document.py` | `build_act_document()` — pure function, act row + related data -> OpenSearch document |
| `indexer.py` | `index_single_act()`/`reindex_all_acts()` — reads `procurement_acts` (+ identifiers/CPV/locations/parties) from Postgres and bulk-indexes |
| `search.py` | `build_query_body()` (pure) + `search_procurement_acts()` — relevance search (`multi_match` over title/buyer/supplier, CPV-prefix/NUTS filters) |
| `cli.py` | `python -m services.search_index.cli create-index` / `reindex-all` |

Wired into `apps/api` as `GET /v1/search/fulltext` — see that router's own
docstring for why it's a separate endpoint from `/v1/search` rather than
replacing that endpoint's Postgres-based exact-match-first ranking.

`infra/docker/docker-compose.yml` has an `opensearch` service (single-node,
security plugin disabled — dev only) for local development.

## Not yet implemented

- `index_single_act()` is now wired in as an incremental-indexing hook on
  ΚΗΜΔΗΣ ingestion, both the manual CLI (`khmdhs/cli.py backfill --with-opensearch`)
  and the scheduled job (`khmdhs/scheduled.py`, automatically when
  `OPENSEARCH_URL` is set) — mirroring how adamChain/alerts hook in.
  Indexing failures are caught/logged and never break ingestion. TED
  ingestion does **not** have this hook yet (only ΚΗΜΔΗΣ) — a reasonable
  follow-up, same pattern.
- `reindex-all` (bulk/from-scratch reindexing) is still a standalone
  manually-triggered CLI command, not scheduled — could reuse
  `services/ingestion/orchestration`'s scheduler mechanism, not done here.
- Document/attachment full-text search (indexing `document_pages.text`
  from `services/documents/`) — this pass only indexes `procurement_acts`
  metadata, not extracted document text.
- Highlighting, synonyms, did-you-mean/fuzzy-suggestion UX beyond the
  basic `fuzziness: AUTO` already in `search.py`'s query.

## Tests

`tests/unit/test_search_index_{document,query_builder}.py` (pure logic),
`tests/contract/test_search_index_client.py` (respx-mocked REST calls),
`tests/integration/test_search_index_reindex_db.py` — needs **both**
`DATABASE_URL` and `OPENSEARCH_URL` set (confirmed to skip cleanly here;
no live OpenSearch cluster was available in this sandbox).
`tests/unit/test_khmdhs_scheduled_opensearch_hook.py` (in the ΚΗΜΔΗΣ
connector's own test suite) covers the incremental-indexing hook itself:
called when configured, a no-op when not, and a failure there never
breaks ingestion — all without a real DB/OpenSearch/ΚΗΜΔΗΣ API.
