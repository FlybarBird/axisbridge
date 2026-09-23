import argparse
import hmac
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import mimetypes
import os
from pathlib import Path
import secrets
import socket
import sys
import threading
from urllib.parse import urlparse
try:
    import webbrowser
except ImportError:  # OpenWrt's split stdlib omits this desktop-only helper.
    webbrowser = None
from .engine import Engine, Conflict
from .model import number
from .network import interfaces

WEB = Path(__file__).resolve().parent / 'web'
MAX_BODY = 1024 * 1024


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, engine, key):
        self.engine, self.key = engine, key
        self.session = secrets.token_urlsafe(40)
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = 'AxisBridge/0.1'
    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, *args):
        pass

    def reply(self, status, value, content_type='application/json', headers=None):
        data = json.dumps(value, allow_nan=False).encode() if content_type == 'application/json' else value
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def authenticated(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get('Cookie', ''))
            value = cookie['axisbridge_session'].value if 'axisbridge_session' in cookie else ''
            return hmac.compare_digest(value, self.server.session)
        except Exception:
            return False

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ('/', '/app.js', '/style.css', '/status.css', '/favicon.svg'):
            filename = 'index.html' if path == '/' else path[1:]
            file = WEB / filename
            if file.exists():
                self.reply(200, file.read_bytes(), mimetypes.guess_type(filename)[0] or 'text/plain')
            else:
                self.reply(404, {'error': 'Not found'})
            return
        if not self.authenticated():
            self.reply(401, {'error': 'Enter the portal access key shown by the app'})
            return
        engine = self.server.engine
        if path == '/api/state':
            self.reply(200, engine.snapshot())
        elif path == '/api/config':
            self.reply(200, engine.config())
        elif path == '/api/interfaces':
            self.reply(200, interfaces())
        elif path == '/api/export':
            self.reply(200, engine.config()['show'], headers={'Content-Disposition': 'attachment; filename="AxisBridge-show.json"'})
        else:
            self.reply(404, {'error': 'Not found'})

    def do_POST(self):
        path = urlparse(self.path).path
        origin = self.headers.get('Origin')
        if origin and urlparse(origin).netloc != self.headers.get('Host'):
            self.reply(403, {'error': 'Cross-origin requests are not allowed'})
            return
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            self.reply(415, {'error': 'JSON required'})
            return
        if path != '/api/login' and not self.authenticated():
            self.reply(401, {'error': 'Sign in to the portal'})
            return
        try:
            length = int(self.headers.get('Content-Length', 0))
            if not 0 < length <= MAX_BODY:
                raise ValueError('Request body must be between 1 byte and 1 MB')
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError('Expected a JSON object')
            if path == '/api/login':
                if not isinstance(data.get('key'), str) or not hmac.compare_digest(data['key'], self.server.key):
                    self.reply(403, {'error': 'Incorrect access key'})
                    return
                self.reply(200, {'ok': True}, headers={'Set-Cookie': f'axisbridge_session={self.server.session}; HttpOnly; SameSite=Strict; Path=/'})
                return
            engine = self.server.engine
            with engine.operation_lock:
                result = {'ok': True}
                if path == '/api/config':
                    result = engine.save(data.get('show'), data.get('revision'))
                elif path == '/api/capture':
                    result = engine.capture(data.get('block_id'), data.get('endpoint'), data.get('revision'))
                elif path == '/api/action':
                    action = data.get('action')
                    if action == 'start_psn': engine.start_input()
                    elif action == 'stop_psn': engine.stop_input()
                    elif action == 'connect_ma': engine.connect_ma(data.get('password'))
                    elif action == 'disconnect_ma': engine.disconnect_ma()
                    elif action == 'arm': engine.arm()
                    elif action == 'hold': engine.disarm(); engine.log('Output held by operator')
                    elif action == 'demo': engine.set_demo(data.get('enabled'))
                    elif action == 'demo_level':
                        ident = number(data.get('id'), 'Demo entity', 1, 3, True)
                        level = number(data.get('level'), 'Demo level', 0, 100)
                        engine.set_demo_level(ident, level)
                    else: raise ValueError('Unknown action')
                else:
                    self.reply(404, {'error': 'Not found'})
                    return
            self.reply(200, result)
        except Conflict as exc:
            self.reply(409, {'error': str(exc)})
        except (ValueError, TypeError, KeyError, OSError) as exc:
            self.reply(400, {'error': str(exc)})


def main():
    parser = argparse.ArgumentParser(description='AxisBridge — PSN to grandMA2 with a local web portal')
    parser.add_argument('--host', default='0.0.0.0', help='Portal listening address; default all adapters')
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--data-dir', default=str(Path.home() / 'AxisBridge'))
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    directory = Path(args.data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    key_path = directory / 'portal-key.txt'
    if key_path.exists():
        key = key_path.read_text().strip()
        if len(key) < 20:
            raise SystemExit('Portal key is invalid. Remove portal-key.txt to generate a new one.')
    else:
        key = secrets.token_urlsafe(24)
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(key + '\n')
    engine = Engine(directory)
    try:
        server = Server((args.host, args.port), engine, key)
    except OSError as exc:
        engine.close()
        raise SystemExit(f'Cannot start portal: {exc}. Try --port 8081.')
    port = server.server_address[1]
    host = '127.0.0.1' if args.host == '0.0.0.0' else args.host
    print(f'\nAxisBridge 0.1.0\nPortal: http://{host}:{port}\nAccess key: {key}\n')
    if args.host == '0.0.0.0':
        for adapter in interfaces():
            if adapter['up'] and adapter['ip'] != '127.0.0.1':
                print(f"LAN portal: http://{adapter['ip']}:{port}")
    print('\nSaved auto-connect settings are restored automatically. Output always starts held.\nCtrl+C stops the app.\n', flush=True)
    if not args.no_browser and webbrowser is not None:
        threading.Timer(0.5, lambda: webbrowser.open(f'http://{host}:{port}/#key={key}')).start()
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        engine.close()


if __name__ == '__main__':
    main()
