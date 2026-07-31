from pathlib import Path

from services.exports.generate import _safe_export_path


def test_safe_export_path_accepts_file_below_root(tmp_path: Path):
    root = tmp_path / "exports"
    target = root / "tenant" / "report.csv"

    assert _safe_export_path(str(target), root) == target.resolve()


def test_safe_export_path_rejects_file_outside_root(tmp_path: Path):
    root = tmp_path / "exports"
    outside = tmp_path / "private.txt"

    assert _safe_export_path(str(outside), root) is None
