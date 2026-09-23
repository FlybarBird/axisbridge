"""Create the portable Python source release without build or local state files."""
from __future__ import annotations

from pathlib import Path
import runpy
import zipfile


ROOT = Path(__file__).resolve().parents[1]
VERSION = runpy.run_path(str(ROOT / 'axisbridge/__init__.py'))['__version__']
OUTPUT = ROOT / "downloads" / f"AxisBridge-v{VERSION}.zip"
PREFIX = Path("AxisBridge")
TOP_LEVEL = (
    "README.md",
    "Start-Mac.command",
    "Start-Windows.bat",
    "TEST-RESULTS.md",
    "requirements.txt",
    "run.py",
    "start.sh",
)
TREES = ("axisbridge", "docs", "packaging", "tests")
SCRIPTS = ("build.py", "psn_sender.py")


def included_files() -> list[Path]:
    files = [ROOT / name for name in TOP_LEVEL]
    files.extend(ROOT / "scripts" / name for name in SCRIPTS)
    for name in TREES:
        files.extend(
            path
            for path in (ROOT / name).rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and not path.name.endswith((".pyc", ".DS_Store"))
        )
    return sorted(files)


def build_archive() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUTPUT, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in included_files():
            relative = path.relative_to(ROOT)
            info = zipfile.ZipInfo.from_file(path, (PREFIX / relative).as_posix())
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes(), compresslevel=9)
    print(OUTPUT)


if __name__ == "__main__":
    build_archive()
