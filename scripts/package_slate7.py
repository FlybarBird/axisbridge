"""Refresh checksums and create the distributable Slate 7 installer archive."""
from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import tarfile
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "slate7"
OUTPUT = ROOT / "downloads" / "AxisBridge-Slate7-Installer-v0.1.0.tar.gz"
ARCHIVE_ROOT = "AxisBridge-Slate7-Installer"


def package_files() -> list[Path]:
    return sorted(
        path
        for path in SOURCE.rglob("*")
        if path.is_file()
        and path.name != "SHA256SUMS"
        and "__pycache__" not in path.parts
        and not path.name.endswith((".pyc", ".DS_Store"))
    )


def refresh_manifest(files: list[Path]) -> None:
    lines = []
    for path in files:
        relative = path.relative_to(SOURCE).as_posix()
        lines.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {relative}\n")
    (SOURCE / "SHA256SUMS").write_text("".join(lines), encoding="utf-8")


def build_archive() -> None:
    files = package_files()
    refresh_manifest(files)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="axisbridge-slate7-") as temporary:
        stage = Path(temporary) / ARCHIVE_ROOT
        shutil.copytree(SOURCE, stage, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))
        with tarfile.open(OUTPUT, "w:gz", format=tarfile.PAX_FORMAT) as archive:
            archive.add(stage, arcname=ARCHIVE_ROOT)
    digest = hashlib.sha256(OUTPUT.read_bytes()).hexdigest()
    print(f"{OUTPUT.name}: {digest}")


if __name__ == "__main__":
    build_archive()
