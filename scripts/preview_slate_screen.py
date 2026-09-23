"""Render the actual hardware renderer to PPM for visual inspection."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from axisbridge.slate_screen import HoldControl, render

snapshot = {'psn': 'LIVE', 'ma': 'OFF', 'reason': '', 'armed': False, 'demo': False,
            'blocks': [{'name': 'POD 1', 'value': 3.75}, {'name': 'POD 2', 'value': 6.2}]}
hold = HoldControl()
if '--holding' in sys.argv:
    hold.pointer(True, 40, 35, 0, 1)
Path(sys.argv[1]).write_bytes(render(snapshot, hold, 2.0).ppm())
