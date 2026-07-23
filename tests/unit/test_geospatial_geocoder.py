import asyncio

import httpx

from services.geospatial.config import GeocoderConfig
from services.geospatial.extract import AdminUnit, LocationCandidate
from services.geospatial.geocoder import NominatimGeocoder, match_local_boundary


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
