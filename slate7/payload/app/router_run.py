"""procd entrypoint: persistent state, clean SIGTERM, no access keys in syslog."""
import argparse
import os
from pathlib import Path
import secrets
import signal
import threading
from axisbridge.engine import Engine
from axisbridge.server import Server


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=8080)
    args=parser.parse_args()
    directory=Path('/etc/axisbridge')
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path=directory/'portal-key.txt'
    if not path.exists():
        fd=os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
        with os.fdopen(fd,'w') as stream: stream.write(secrets.token_urlsafe(24)+'\n')
    key=path.read_text().strip()
    if len(key)<20: raise SystemExit('Invalid portal key. Restore it from backup.')
    engine=Engine(directory)
    server=None
    try:
        server=Server((args.host,args.port), engine,key)
        def stop(signum, frame):
            engine.disarm()
            threading.Thread(target=server.shutdown,daemon=True).start()
        signal.signal(signal.SIGTERM,stop)
        signal.signal(signal.SIGINT,stop)
        print(f'AxisBridge portal listening on {args.host}:{args.port}; output held.',flush=True)
        server.serve_forever(poll_interval=0.2)
    finally:
        if server: server.server_close()
        engine.close()

if __name__=='__main__':main()
