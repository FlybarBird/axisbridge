"""Run on each target OS to build its native executable with PyInstaller."""
import importlib.util
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
if not importlib.util.find_spec('PyInstaller'):
    raise SystemExit('Install the build tool first: python -m pip install pyinstaller psutil')
subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
    '--onefile', '--name', 'AxisBridge', '--collect-all', 'psutil',
    '--add-data', str(root / 'axisbridge' / 'web') + ':axisbridge/web',
    str(root / 'run.py')], cwd=root, check=True)
print('Native executable is in', root / 'dist')
