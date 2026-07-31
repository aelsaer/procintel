# services/search_index

OpenSearch full-text indexing for all seven description.txt §29 catalogs:
`opportunities`, `procurement_processes`, `procurement_acts`,
`organizations`, `companies`, `funding_projects`, and `documents`.

Uses a small, purpose-built `httpx` REST wrapper (`client.py`), not the
official `opensearch-py` SDK — see the note in `pyproject.toml` for why
(it pulls in unused gRPC/protobuf dependencies and its transport isn't
`httpx`-based, so it can't be exercised with the `respx` mocking this
codebase uses everywhere else).

| Module | Purpose |
|---|---|
| `config.py` | `OpenSearchConfig.from_env()` — `OPENSEARCH_URL` required; `OPENSEARCH_INDEX_NAME` names the act index and `OPENSEARCH_INDEX_PREFIX` isolates the other catalogs |
| `mapping.py` | `PROCUREMENT_ACTS_MAPPING` — the index mapping, using OpenSearch's built-in `greek` analyzer for `title`/`buyer_name`/`supplier_names` |
| `client.py` | Index lifecycle, stale-document cleanup, bulk indexing and search REST calls |
| `document.py` | `build_act_document()` — pure function, act row + related data -> OpenSearch document |
| `indexer.py` | `index_single_act()`/`reindex_all_acts()` — reads acts and then refreshes every catalog |
| `catalog.py` | Separate canonical catalog queries, tenant opportunity isolation and stale-document cleanup |
| `catalog_search.py` | Cross-catalog relevance queries with mandatory tenant filtering for opportunities |
| `search.py` | `build_query_body()` (pure) + `search_procurement_acts()` — relevance search (`multi_match` over title/buyer/supplier, CPV-prefix/NUTS filters) |
| `cli.py` | `python -m services.search_index.cli create-index` / `reindex-all` |

Wired into `apps/api` as:

- `GET /v1/search/fulltext` for procurement acts.
- `GET /v1/search/catalogs` for public processes, organizations, companies,
  funding projects and documents.
- `GET /v1/search/catalogs/opportunities` for authenticated, tenant-filtered
  opportunity search.

`infra/docker/docker-compose.yml` has an `opensearch` service (single-node,
security plugin disabled — dev only) for local development.

## Runtime behavior

- `index_single_act()` is wired into both ΚΗΜΔΗΣ and TED scheduled ingestion
  whenever `OPENSEARCH_URL` is set. Indexing failures are isolated and
  reported without rolling back canonical ingestion.
- `reindex-all` is the deterministic recovery/rebuild operation for all
  seven catalogs. Incremental act updates remain on the ingestion path.
- The daily scheduler rebuilds the six non-act catalogs after tenant
  opportunity scoring, so profile changes and deleted rows do not leave
  stale search results.
- Document text is indexed from the same page-level extraction used by
  `/v1/document-intelligence`.
- Highlighting, synonyms and did-you-mean UX are optional presentation
  enhancements; exact identifiers and strict all-term lexical matching are
  already supported.

## Tests

`tests/unit/test_search_index_{document,query_builder}.py` (pure logic),
`tests/contract/test_search_index_client.py` (respx-mocked REST calls),
`tests/integration/test_search_index_reindex_db.py` needs both
`DATABASE_URL` and `OPENSEARCH_URL`.
`tests/unit/test_khmdhs_scheduled_opensearch_hook.py` (in the ΚΗΜΔΗΣ
connector's own test suite) covers the incremental-indexing hook itself:
called when configured, a no-op when not, and a failure there never
breaks ingestion — all without a real DB/OpenSearch/ΚΗΜΔΗΣ API.
