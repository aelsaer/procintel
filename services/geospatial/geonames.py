"""GeoNames Greece dump parser and local place geocoder.

Dataset: https://download.geonames.org/export/dump/
License: Creative Commons Attribution 4.0.
"""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from math import log10
from typing import Sequence

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import geocoding_places

from .extract import LocationCandidate, normalize_place
from .geocoder import GeocodeResult

DEFAULT_GEONAMES_GR_URL = "https://download.geonames.org/export/dump/GR.zip"
DEFAULT_ADMIN1_URL = "https://download.geonames.org/export/dump/admin1CodesASCII.txt"
DEFAULT_ADMIN2_URL = "https://download.geonames.org/export/dump/admin2Codes.txt"
_GREEK_RE = re.compile(r"[Α-Ωα-ωΆΈΉΊΌΎΏάέήίόύώϊϋΐΰ]")


@dataclass(frozen=True)
class GazetteerPlace:
    geoname_id: int
    country_code: str
    name: str
    normalized_names: tuple[str, ...]
    admin_name_1: str | None
    admin_code_1: str | None
    admin_name_2: str | None
    admin_code_2: str | None
    admin_name_3: str | None
    admin_code_3: str | None
    feature_class: str
    feature_code: str
    population: int
    latitude: float
    longitude: float


def parse_admin_codes(payload: bytes) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for line in payload.decode("utf-8").splitlines():
        fields = line.split("\t")
        if len(fields) >= 2:
            mapping[fields[0]] = fields[1]
    return mapping


def _preferred_name(primary: str, alternates: Sequence[str]) -> str:
    return next((name for name in alternates if _GREEK_RE.search(name)), primary)


def parse_geonames_country_zip(
    payload: bytes,
    *,
    country_code: str,
    admin1_names: dict[str, str],
    admin2_names: dict[str, str],
) -> list[GazetteerPlace]:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        member = next((name for name in archive.namelist() if name.upper().endswith(f"{country_code.upper()}.TXT")), None)
        if member is None:
            raise ValueError("GeoNames archive has no country text file")
        text = archive.read(member).decode("utf-8")

    places: list[GazetteerPlace] = []
    for line in text.splitlines():
        row = line.split("\t")
        if len(row) < 19 or row[8].upper() != country_code.upper() or row[6] not in {"A", "P"}:
            continue
        try:
            geoname_id = int(row[0])
            latitude, longitude = float(row[4]), float(row[5])
            population = int(row[14] or 0)
        except ValueError:
            continue
        alternate_names = row[3].split(",") if row[3] else []
        aliases = tuple(
            dict.fromkeys(
                normalized
                for value in (row[1], row[2], *alternate_names)
                if (normalized := normalize_place(value))
            )
        )
        admin1_code, admin2_code, admin3_code = row[10] or None, row[11] or None, row[12] or None
        places.append(
            GazetteerPlace(
                geoname_id=geoname_id,
                country_code=row[8].upper(),
                name=_preferred_name(row[1], alternate_names),
                normalized_names=aliases,
                admin_name_1=admin1_names.get(f"{country_code.upper()}.{admin1_code}") if admin1_code else None,
                admin_code_1=admin1_code,
                admin_name_2=admin2_names.get(f"{country_code.upper()}.{admin1_code}.{admin2_code}") if admin1_code and admin2_code else None,
                admin_code_2=admin2_code,
                admin_name_3=None,
                admin_code_3=admin3_code,
                feature_class=row[6],
                feature_code=row[7],
                population=population,
                latitude=latitude,
                longitude=longitude,
            )
        )
    local_admin1 = {
        place.admin_code_1: place.name
        for place in places
        if place.feature_code == "ADM1" and place.admin_code_1
    }
    local_admin2 = {
        (place.admin_code_1, place.admin_code_2): place.name
        for place in places
        if place.feature_code == "ADM2" and place.admin_code_1 and place.admin_code_2
    }
    return [
        replace(
            place,
            admin_name_1=local_admin1.get(place.admin_code_1, place.admin_name_1),
            admin_name_2=local_admin2.get((place.admin_code_1, place.admin_code_2), place.admin_name_2),
        )
        for place in places
    ]


async def replace_place_gazetteer(
    conn: AsyncConnection,
    *,
    country_code: str,
    country_payload: bytes,
    admin1_payload: bytes,
    admin2_payload: bytes,
) -> int:
    places = parse_geonames_country_zip(
        country_payload,
        country_code=country_code,
        admin1_names=parse_admin_codes(admin1_payload),
        admin2_names=parse_admin_codes(admin2_payload),
    )
    source_version = hashlib.sha256(country_payload + admin1_payload + admin2_payload).hexdigest()
    await conn.execute(geocoding_places.delete().where(geocoding_places.c.country_code == country_code.upper()))
    now = datetime.now(timezone.utc)
    for start in range(0, len(places), 1000):
        await conn.execute(
            geocoding_places.insert(),
            [
                {
                    **place.__dict__,
                    "normalized_names": list(place.normalized_names),
                    "source_name": "GEONAMES",
                    "source_version": source_version,
                    "updated_at": now,
                }
                for place in places[start : start + 1000]
            ],
        )
    await conn.commit()
    return len(places)


async def load_gazetteer_places(conn: AsyncConnection, *, country_code: str = "GR") -> list[GazetteerPlace]:
    rows = (
        await conn.execute(sa.select(geocoding_places).where(geocoding_places.c.country_code == country_code.upper()))
    ).all()
    return [
        GazetteerPlace(
            geoname_id=row.geoname_id,
            country_code=row.country_code,
            name=row.name,
            normalized_names=tuple(row.normalized_names),
            admin_name_1=row.admin_name_1,
            admin_code_1=row.admin_code_1,
            admin_name_2=row.admin_name_2,
            admin_code_2=row.admin_code_2,
            admin_name_3=row.admin_name_3,
            admin_code_3=row.admin_code_3,
            feature_class=row.feature_class,
            feature_code=row.feature_code,
            population=row.population or 0,
            latitude=row.latitude,
            longitude=row.longitude,
        )
        for row in rows
    ]


def match_gazetteer_place(candidate: LocationCandidate, places: Sequence[GazetteerPlace]) -> GeocodeResult | None:
    query = normalize_place(candidate.place_text)
    ranked: list[tuple[float, GazetteerPlace]] = []
    for place in places:
        if query in place.normalized_names:
            score = 100.0
        elif len(query) >= 5 and any(
            len(alias) >= 5 and (query.startswith(f"{alias} ") or alias.startswith(f"{query} "))
            for alias in place.normalized_names
        ):
            score = 75.0
        else:
            continue
        if candidate.granularity_hint in {"MUNICIPALITY", "REGIONAL_UNIT", "REGION"} and place.feature_class == "A":
            score += 15
        if place.feature_code in {"PPLC", "PPLA", "PPLA2", "ADM1", "ADM2", "ADM3"}:
            score += 8
        score += min(log10(max(place.population, 1)), 7)
        ranked.append((score, place))
    if not ranked:
        return None
    _, place = max(ranked, key=lambda item: item[0])
    if place.feature_code.startswith("ADM"):
        municipality = place.name if place.feature_code in {"ADM3", "ADM4"} else None
        regional_unit = place.name if place.feature_code == "ADM2" else place.admin_name_2
        region = place.name if place.feature_code == "ADM1" else place.admin_name_1
        precision = place.feature_code
    else:
        municipality = candidate.place_text
        regional_unit = place.admin_name_2
        region = place.admin_name_1
        precision = "LOCALITY"
    return GeocodeResult(
        latitude=place.latitude,
        longitude=place.longitude,
        display_name=", ".join(filter(None, (place.name, regional_unit, region, "Ελλάδα"))),
        municipality_name=municipality,
        regional_unit_name=regional_unit,
        region_name=region,
        postal_code=candidate.postal_code,
        country_code=place.country_code,
        precision=precision,
        provider="GEONAMES",
        confidence=min(candidate.confidence, 0.90),
        raw_response={"geoname_id": place.geoname_id, "feature_code": place.feature_code, "license": "CC-BY-4.0"},
    )
