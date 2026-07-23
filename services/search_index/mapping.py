"""OpenSearch index mapping for `procurement_acts` documents.

`title`/`buyer_name`/`supplier_names` use the built-in `greek` analyzer
(stemming + accent/case folding for Modern Greek — the language nearly
all procurement titles/entity names are in); `.raw` keyword sub-fields on
`title` support exact-match/sorting/aggregation alongside the analyzed
full-text field.
"""

from __future__ import annotations

PROCUREMENT_ACTS_MAPPING: dict = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
    },
    "mappings": {
        "properties": {
            "id": {"type": "keyword"},
            "process_id": {"type": "keyword"},
            "adam": {"type": "keyword"},
            "ada_list": {"type": "keyword"},
            "title": {
                "type": "text",
                "analyzer": "greek",
                "fields": {"raw": {"type": "keyword", "ignore_above": 512}},
            },
            "normalized_title": {"type": "keyword"},
            "act_type": {"type": "keyword"},
            "status": {"type": "keyword"},
            "procedure_type": {"type": "keyword"},
            "amount_net": {"type": "double"},
            "amount_gross": {"type": "double"},
            "currency": {"type": "keyword"},
            "cpv_codes": {"type": "keyword"},
            "nuts_codes": {"type": "keyword"},
            "buyer_id": {"type": "keyword"},
            "buyer_name": {"type": "text", "analyzer": "greek", "fields": {"raw": {"type": "keyword"}}},
            "supplier_ids": {"type": "keyword"},
            "supplier_names": {"type": "text", "analyzer": "greek"},
            "submission_date": {"type": "date"},
            "decision_date": {"type": "date"},
        }
    },
}
