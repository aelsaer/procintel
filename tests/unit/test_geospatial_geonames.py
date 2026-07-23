import io
import zipfile

from services.geospatial.extract import LocationCandidate
from services.geospatial.geonames import match_gazetteer_place, parse_geonames_country_zip


def _archive() -> bytes:
    row = "264371\tAthens\tAthens\tΑθήνα,Athína\t37.98376\t23.72784\tP\tPPLC\tGR\t\tI\tA1\t\t\t664046\t\t70\tEurope/Athens\t2025-01-01\n"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("GR.txt", row)
    return buffer.getvalue()


def test_geonames_country_dump_resolves_greek_alias_locally():
    places = parse_geonames_country_zip(
        _archive(),
        country_code="GR",
        admin1_names={"GR.I": "Αττική"},
        admin2_names={"GR.I.A1": "Κεντρικός Τομέας Αθηνών"},
    )
    candidate = LocationCandidate(
        place_text="ΑΘΗΝΑ",
        postal_code="10552",
        nuts_codes=("EL303",),
        granularity_hint="LOCALITY",
        confidence=0.97,
        source_paths=("$.nutsCity",),
        extraction_method="STRUCTURED_FIELD",
    )

    result = match_gazetteer_place(candidate, places)

    assert len(places) == 1
    assert result is not None
    assert result.provider == "GEONAMES"
    assert result.municipality_name == "ΑΘΗΝΑ"
    assert result.region_name == "Αττική"
    assert result.latitude == 37.98376
