"""Remove owned app/service files, retaining show, key, and service configuration."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

if sys.argv[1:] != ['uninstall']:
    raise SystemExit('Usage: axisbridge-ctl uninstall')
receipt=Path('/etc/axisbridge/installation.json')
if not receipt.exists() or json.loads(receipt.read_text()).get('package')!='axisbridge-slate7':
    raise SystemExit('Installation receipt missing; refusing to remove unmanaged files.')
subprocess.run(['/etc/init.d/axisbridge','stop'],check=False)
subprocess.run(['/etc/init.d/axisbridge','disable'],check=False)
for path in ('/etc/init.d/axisbridge','/usr/bin/axisbridge-ctl'):
    Path(path).unlink(missing_ok=True)
shutil.rmtree('/usr/share/axisbridge')
receipt.unlink()
print('AxisBridge removed. Show/key remain in /etc/axisbridge; settings remain in /etc/config/axisbridge.')
print('Python and router networking were retained.')
