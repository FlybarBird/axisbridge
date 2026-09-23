"""Transactional deployment. No firmware, package-feed, or network rewrites."""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen
from urllib.error import HTTPError

BASE=Path(__file__).resolve().parent
APP=Path('/usr/share/axisbridge')
DATA=Path('/etc/axisbridge')
SERVICE=Path('/etc/init.d/axisbridge')
SETTINGS=Path('/etc/config/axisbridge')
CONTROL=Path('/usr/bin/axisbridge-ctl')
VERSION='0.1.0-slate7.3'


def is_slate7_model(value):
    normalized=value.lower().replace('_','-')
    return any(marker in normalized for marker in (
        'gl-be3600', 'gl.inet be3600', 'qcom,ipq5332-ap-mi04.1-c2'))


def atomic_write(path, data, mode=0o600):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix=path.name+'.',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(data)
            stream.flush();os.fsync(stream.fileno())
        os.chmod(name,mode)
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)


def verify_payload(base):
    """Reject changed files and path escapes before deployment."""
    for line in (base/'SHA256SUMS').read_text().splitlines():
        checksum,name=line.split(None,1)
        name=name.strip().lstrip('*')
        path=base/name
        if path.is_symlink() or not path.resolve().is_relative_to(base.resolve()):
            raise ValueError('Invalid checksum path: '+name)
        if hashlib.sha256(path.read_bytes()).hexdigest()!=checksum:
            raise ValueError('Checksum mismatch: '+name)
    expected={line.split(None,1)[1].strip().lstrip('*') for line in (base/'SHA256SUMS').read_text().splitlines()}
    for path in (base/'payload').rglob('*'):
        if path.is_file() and path.relative_to(base).as_posix() not in expected:
            raise ValueError('Unlisted payload file: '+str(path))


def router_guard():
    if not Path('/etc/openwrt_release').is_file():
        raise ValueError('OpenWrt firmware is required')
    model=' '.join(path.read_text(errors='replace').replace('\0','') for path in
        (Path('/tmp/sysinfo/model'),Path('/tmp/sysinfo/board_name'),Path('/proc/device-tree/model')) if path.exists())
    if not is_slate7_model(model):
        raise ValueError('This package targets Slate 7 GL-BE3600 only')
    if os.geteuid()!=0:raise ValueError('Run as root')
    if not Path('/lib/functions/procd.sh').exists():raise ValueError('procd not found')
    if not Path('/usr/bin/python3').exists():raise ValueError('Expected /usr/bin/python3 from firmware packages')


def uci(option,default):
    r=subprocess.run(['uci','-q','get','axisbridge.main.'+option],capture_output=True,text=True)
    return r.stdout.strip() if r.returncode==0 else default


def options(port,bind):
    if SETTINGS.exists():
        port=int(uci('port',str(port)));bind=uci('bind',bind)
    if not 1024<=port<=65535:raise ValueError('Portal port must be 1024–65535')
    address=ipaddress.IPv4Address(bind)
    if address.is_multicast or str(address)=='255.255.255.255':raise ValueError('Invalid portal bind address')
    return port,str(address)


def owned():
    receipt=DATA/'installation.json'
    if not receipt.exists():return False
    try:return json.loads(receipt.read_text()).get('package')=='axisbridge-slate7'
    except (ValueError,OSError):return False


def service(action,check=False):
    return subprocess.run([str(SERVICE),action],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=check).returncode==0


def check_port(port,bind):
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        try:s.bind((bind,port))
        except OSError as exc:raise ValueError(f'Portal cannot bind {bind}:{port}: {exc}. Use --port=8081 or another free port.')


def preflight(port,bind):
    router_guard();verify_payload(BASE)
    managed=owned()
    for target in (APP,SERVICE,CONTROL):
        if target.exists() and not managed:
            raise ValueError(f'{target} already exists without an AxisBridge receipt; no files replaced')
        if target.is_symlink():raise ValueError(f'Refusing symlink install target: {target}')
    if DATA.is_symlink() or SETTINGS.is_symlink():raise ValueError('Refusing symlink configuration directory/file')
    if SETTINGS.exists() and not managed and APP.exists():raise ValueError('Unmanaged settings')
    port,bind=options(port,bind)
    running=managed and SERVICE.exists() and service('status')
    enabled=managed and SERVICE.exists() and service('enabled')
    if not running:check_port(port,bind)
    if shutil.disk_usage('/usr/share').free<12*1024*1024:raise ValueError('At least 12 MiB free space required')
    print(f'Portal binding: {bind}:{port}; current service running: {running}',flush=True)
    return port,bind,managed,running,enabled


def health_check(port,bind,timeout=12):
    host='127.0.0.1' if bind=='0.0.0.0' else bind
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        try:
            with urlopen(f'http://{host}:{port}/',timeout=1) as response:
                if 'AxisBridge' in response.headers.get('Server','') and b'AxisBridge' in response.read(8192):return
        except OSError:pass
        time.sleep(0.25)
    raise ValueError('Portal health check failed. Inspect logread -e axisbridge after rollback.')


class Transaction:
    """Path-level rollback; unit-tested using a temporary root."""
    def __init__(self, backup):
        self.backup=Path(backup);self.records=[]
        self.backup.mkdir(parents=True,exist_ok=True)

    def snapshot(self,path):
        path=Path(path);saved=self.backup/str(len(self.records))
        existed=path.exists()
        if existed:
            if path.is_dir():shutil.copytree(path,saved)
            else:shutil.copy2(path,saved)
        self.records.append((path,saved,existed))

    def rollback(self):
        for path,saved,existed in reversed(self.records):
            if path.is_dir():shutil.rmtree(path)
            elif path.exists():path.unlink()
            if existed:
                path.parent.mkdir(parents=True,exist_ok=True)
                if saved.is_dir():shutil.copytree(saved,path)
                else:shutil.copy2(saved,path)


def install(port,bind):
    port,bind,managed,was_running,was_enabled=preflight(port,bind)
    os.umask(0o077)
    DATA.mkdir(mode=0o700,parents=True,exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix='.axisbridge-stage-',dir='/usr/share'))
    backup=Path(tempfile.mkdtemp(prefix='.axisbridge-rollback-',dir='/usr/share'))
    tx=Transaction(backup)
    swapped=False
    try:
        shutil.copytree(BASE/'payload/app',stage,dirs_exist_ok=True)
        # Check imports and existing show compatibility before stopping an old service.
        env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=str(stage))
        probe="from axisbridge.server import Server; from axisbridge.model import validate_show; import json,pathlib; p=pathlib.Path('/etc/axisbridge/current-show.json'); validate_show(json.loads(p.read_text())) if p.exists() else None"
        subprocess.run(['/usr/bin/python3','-c',probe],cwd=stage,env=env,check=True)
        for path in (APP,SERVICE,CONTROL,SETTINGS,DATA/'installation.json'):
            tx.snapshot(path)
        if managed:
            service('stop')
            # Wait for the old process to release its listener before testing the new port.
            for _ in range(40):
                if not service('status'):break
                time.sleep(0.25)
            if service('status'):raise ValueError('Existing service did not stop; installation aborted')
        swapped=True
        check_port(port,bind)
        if APP.exists():shutil.rmtree(APP)
        os.replace(stage,APP)
        atomic_write(SERVICE,(BASE/'payload/etc/init.d/axisbridge').read_bytes(),0o755)
        atomic_write(CONTROL,(BASE/'payload/usr/bin/axisbridge-ctl').read_bytes(),0o755)
        if not SETTINGS.exists():
            config=f"config axisbridge 'main'\n\toption enabled '1'\n\toption bind '{bind}'\n\toption port '{port}'\n"
            atomic_write(SETTINGS,config.encode())
        receipt={'package':'axisbridge-slate7','version':VERSION,'installed_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}
        atomic_write(DATA/'installation.json',json.dumps(receipt,indent=2).encode())
        if not managed or was_enabled:service('enable',check=True)
        if not managed or was_running:
            if uci('enabled','1')=='0':raise ValueError('Service is disabled in /etc/config/axisbridge; set enabled to 1 before installing/running')
            service('start',check=True)
            health_check(port,bind)
        print('AxisBridge installed. Show and portal key are stored in /etc/axisbridge.',flush=True)
        print(f'Open http://<router-LAN-IP>:{port} and run axisbridge-ctl key to see the access key.')
        print('Use axisbridge-ctl status or axisbridge-ctl logs. Output starts held.')
        print('Networking/firewall/DHCP settings are unchanged. Follow SETUP.md before connecting show networks.')
    except Exception:
        if swapped:
            service('stop')
            service('disable')
            tx.rollback()
            if managed:
                if was_enabled:service('enable')
                if was_running:service('start')
            print('App/service changes rolled back. Dependency packages and saved show/key were retained.',file=sys.stderr)
        raise
    finally:
        if stage.exists():shutil.rmtree(stage)
        shutil.rmtree(backup)


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['check','install']);p.add_argument('--port',type=int,default=8080);p.add_argument('--bind',default='0.0.0.0')
    args=p.parse_args()
    try:
        if args.action=='check':preflight(args.port,args.bind)
        else:install(args.port,args.bind)
    except Exception as exc:
        print('ERROR: '+str(exc),file=sys.stderr);return 1
    return 0

if __name__=='__main__':sys.exit(main())
