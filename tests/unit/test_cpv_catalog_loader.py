from __future__ import annotations

from scripts.load_cpv_catalog import parse_cpv_rows


def test_genericode_parser_extracts_greek_english_and_hierarchy(tmp_path):
    source = tmp_path / "cpv.gc"
    source.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<gc:CodeList xmlns:gc="http://docs.oasis-open.org/codelist/ns/genericode/1.0/">
  <SimpleCodeList>
    <Row>
      <Value ColumnRef="code"><SimpleValue>77312000</SimpleValue></Value>
      <Value ColumnRef="parentCode"><SimpleValue>77310000</SimpleValue></Value>
      <Value ColumnRef="ell_label"><SimpleValue>Υπηρεσίες εκκαθάρισης από αγριόχορτα</SimpleValue></Value>
      <Value ColumnRef="eng_label"><SimpleValue>Weed-clearance services</SimpleValue></Value>
    </Row>
  </SimpleCodeList>
</gc:CodeList>
""",
        encoding="utf-8",
    )

    rows = list(parse_cpv_rows(source))

    assert rows == [{
        "code": "77312000",
        "check_digit": None,
        "prefix_2": "77",
        "prefix_3": "773",
        "prefix_4": "7731",
        "prefix_5": "77312",
        "parent_code": "77310000",
        "description_el": "Υπηρεσίες εκκαθάρισης από αγριόχορτα",
        "description_en": "Weed-clearance services",
    }]
