import asyncio

import httpx

from services.geospatial.config import GeocoderConfig
from services.geospatial.extract import AdminUnit, LocationCandidate
from services.geospatial.geocoder import NominatimGeocoder, match_local_boundary
from services.geospatial.geonames import (
    GazetteerPlace,
    build_gazetteer_alias_index,
    match_indexed_gazetteer_place,
)


def _candidate(place: str = "Δήμος Αθηναίων") -> LocationCandidate:
    return LocationCandidate(
        place_text=place,
        postal_code="10552",
        nuts_codes=("EL303",),
        granularity_hint="MUNICIPALITY",
        confidence=0.95,
        source_paths=("$.objectDetailsList[0].city",),
        extraction_method="STRUCTURED_FIELD",
    )


def test_local_boundary_match_respects_nuts_and_returns_point():
    units = [
        AdminUnit("MUNICIPALITY", "6101", "ΔΗΜΟΣ ΑΘΗΝΑΙΩΝ", "EL303", 37.98, 23.73),
        AdminUnit("MUNICIPALITY", "9999", "ΔΗΜΟΣ ΑΘΗΝΑΙΩΝ", "EL999", 0, 0),
    ]

    result = match_local_boundary(_candidate(), units)

    assert result is not None
    assert result.provider == "LOCAL_BOUNDARY"
    assert result.latitude == 37.98
    assert result.municipality_name == "ΔΗΜΟΣ ΑΘΗΝΑΙΩΝ"


def test_postal_candidate_resolves_by_exact_nuts_when_name_does_not_match():
    units = [
        AdminUnit(
            "REGIONAL_UNIT",
            "EL303",
            "Κεντρικός Τομέας Αθηνών",
            "EL303",
            37.98,
            23.73,
        ),
    ]
    candidate = LocationCandidate(
        place_text="10552",
        postal_code="10552",
        nuts_codes=("EL303",),
        granularity_hint="POSTAL_CODE",
        confidence=0.68,
        source_paths=("document_pages[0]:postal-code",),
        extraction_method="POSTAL_CODE_PATTERN",
    )

    result = match_local_boundary(candidate, units)

    assert result is not None
    assert result.regional_unit_name == "Κεντρικός Τομέας Αθηνών"
    assert result.postal_code == "10552"


def test_indexed_gazetteer_resolves_an_exact_alias():
    place = GazetteerPlace(
        geoname_id=264371,
        country_code="GR",
        name="Αθήνα",
        normalized_names=("ΑΘΗΝΑ", "ATHENS"),
        admin_name_1="Αττική",
        admin_code_1="I",
        admin_name_2="Κεντρικός Τομέας Αθηνών",
        admin_code_2="A1",
        admin_name_3=None,
        admin_code_3=None,
        feature_class="P",
        feature_code="PPLC",
        population=664046,
        latitude=37.9838,
        longitude=23.7275,
    )
    places = [place]
    alias_index = build_gazetteer_alias_index(places)

    result = match_indexed_gazetteer_place(
        _candidate("Athens"),
        places,
        alias_index,
    )

    assert alias_index["ATHENS"] == (place,)
    assert result is not None
    assert result.display_name.startswith("Αθήνα")


def test_public_nominatim_periodic_rate_is_clamped():
    config = GeocoderConfig(
        base_url="https://nominatim.openstreetmap.org",
        user_agent="Procintel test (test@example.test)",
        rate_limit_per_minute=120,
    )

    assert config.is_public_osmf is True
    assert config.effective_rate_limit_per_minute == 4


def test_nominatim_request_is_greece_scoped_and_parses_admin_names():
    seen_request: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_request
        seen_request = request
        return httpx.Response(
            200,
            json=[
                {
                    "lat": "37.9838",
                    "lon": "23.7275",
                    "display_name": "Αθήνα, Αττική, Ελλάδα",
                    "type": "administrative",
                    "address": {
                        "municipality": "Δήμος Αθηναίων",
                        "county": "Κεντρικός Τομέας Αθηνών",
                        "state": "Αττική",
                        "postcode": "10552",
                        "country_code": "gr",
                    },
                }
            ],
        )

    async def run():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        geocoder = NominatimGeocoder(
            GeocoderConfig(
                base_url="https://geo.example.test",
                user_agent="Procintel test (test@example.test)",
                rate_limit_per_minute=6000,
            ),
            client=client,
        )
        try:
            return await geocoder.geocode(_candidate("Αθήνα"))
        finally:
            await client.aclose()

    result = asyncio.run(run())

    assert result is not None
    assert result.municipality_name == "Δήμος Αθηναίων"
    assert result.regional_unit_name == "Κεντρικός Τομέας Αθηνών"
    assert seen_request is not None
    assert seen_request.url.params["countrycodes"] == "gr"
    assert "Procintel test" in seen_request.headers["User-Agent"]
