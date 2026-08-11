from pathlib import Path

from services.geospatial.service import _raw_payload_paths, _read_raw_payload


def test_relative_raw_uri_maps_to_container_raw_root(monkeypatch, tmp_path):
    raw_root = tmp_path / "var" / "lib" / "procintel" / "raw"
    payload = raw_root / "khmdhs" / "notice" / "record.json"
    payload.parent.mkdir(parents=True)
    payload.write_text('{"nutsCode": {"key": "EL303"}}', encoding="utf-8")
    monkeypatch.setenv("RAW_STORE_ROOT", str(raw_root))

    loaded, warning = _read_raw_payload("raw/khmdhs/notice/record.json")

    assert warning is None
    assert loaded["nutsCode"]["key"] == "EL303"


def test_host_absolute_raw_uri_maps_to_container_raw_root(monkeypatch, tmp_path):
    raw_root = tmp_path / "mounted" / "raw"
    payload = raw_root / "ted" / "notice" / "record.json"
    payload.parent.mkdir(parents=True)
    payload.write_text('{"place-of-performance": ["EL303"]}', encoding="utf-8")
    monkeypatch.setenv("RAW_STORE_ROOT", str(raw_root))

    loaded, warning = _read_raw_payload(
        "/home/operator/procintel/raw/ted/notice/record.json"
    )

    assert warning is None
    assert loaded["place-of-performance"] == ["EL303"]


def test_raw_path_candidates_keep_original_path_without_configuration(monkeypatch):
    monkeypatch.delenv("RAW_STORE_ROOT", raising=False)

    assert _raw_payload_paths("raw/khmdhs/request/record.json") == [
        Path("raw/khmdhs/request/record.json")
    ]
