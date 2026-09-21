"""Independent PSN receiver and grandMA2 TCP worker; neither depends on a browser."""
import re
import select
import socket
import sys
import threading
import time
from .psn import decode, PacketError


def interfaces():
    try:
        import psutil
        stats = psutil.net_if_stats()
        return [{'name': name, 'ip': a.address, 'up': stats.get(name).isup if name in stats else True}
                for name, addresses in psutil.net_if_addrs().items()
                for a in addresses if a.family == socket.AF_INET]
    except ImportError:
        addresses = {'127.0.0.1'}
        try:
            addresses.update(a[4][0] for a in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET))
        except OSError:
            pass
        return [{'name': 'Manual IPv4 entry available', 'ip': a, 'up': True} for a in sorted(addresses)]


class PSNReceiver:
    def __init__(self, engine, settings):
        self.engine, self.settings = engine, settings
        self.stop_event = threading.Event()
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            s, n = self.socket, settings
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
            if n['psn_mode'] == 'multicast':
                s.bind(('', n['psn_port']))
                membership = socket.inet_aton(n['psn_group']) + socket.inet_aton(n['psn_interface'])
                s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
                # Linux defaults to accepting groups joined by other sockets.
                if sys.platform.startswith('linux'):
                    s.setsockopt(socket.IPPROTO_IP, 49, 0)  # IP_MULTICAST_ALL
            else:
                s.bind((n['psn_interface'], n['psn_port']))
            s.settimeout(0.2)
        except Exception:
            self.socket.close()
            raise
        self.thread = threading.Thread(target=self.run, name='PSN input', daemon=True)
        self.thread.start()

    def run(self):
        while not self.stop_event.is_set():
            try:
                data, address = self.socket.recvfrom(65535)
                if self.settings['psn_source'] and address[0] != self.settings['psn_source']:
                    continue
                try:
                    packet = decode(data)
                except PacketError:
                    self.engine.bad_packet()
                    continue
                self.engine.ingest(address[0], packet)
            except socket.timeout:
                continue
            except OSError as exc:
                if not self.stop_event.is_set():
                    self.engine.input_error(str(exc))
                break

    def close(self):
        self.stop_event.set()
        self.socket.close()
        self.thread.join(timeout=1)


class TelnetFilter:
    """Streaming IAC parsing, including commands split across TCP reads."""
    def __init__(self):
        self.state = 'text'
        self.command = None

    def feed(self, data):
        text, reply = bytearray(), bytearray()
        for byte in data:
            if self.state == 'text':
                if byte == 255:
                    self.state = 'iac'
                else:
                    text.append(byte)
            elif self.state == 'iac':
                if byte == 255:
                    text.append(byte)
                    self.state = 'text'
                elif byte in (251, 252, 253, 254):
                    self.command, self.state = byte, 'option'
                elif byte == 250:
                    self.state = 'sub'
                else:
                    self.state = 'text'
            elif self.state == 'option':
                if self.command == 253:  # DO -> WONT
                    reply.extend((255, 252, byte))
                elif self.command == 251:  # WILL -> DONT
                    reply.extend((255, 254, byte))
                self.state = 'text'
            elif self.state == 'sub':
                if byte == 255:
                    self.state = 'sub_iac'
            elif self.state == 'sub_iac':
                self.state = 'text' if byte == 240 else 'sub'
        return bytes(text), bytes(reply)


class MAConnection:
    def __init__(self, engine, settings, password):
        self.engine, self.settings, self.password = engine, settings, password
        self.stop_event = threading.Event()
        self.socket = None
        self.thread = threading.Thread(target=self.run, name='grandMA2 output', daemon=True)
        self.thread.start()

    def run(self):
        while not self.stop_event.is_set():
            try:
                self.engine.ma_status('connecting', 'Connecting to grandMA2')
                s = socket.create_connection((self.settings['ma_host'], self.settings['ma_port']),
                    timeout=2, source_address=(self.settings['ma_interface'], 0))
                self.socket = s
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                s.settimeout(0.5)
                self.session(s)
            except (OSError, ValueError) as exc:
                if not self.stop_event.is_set():
                    self.engine.ma_status('error', str(exc))
            finally:
                if self.socket:
                    self.socket.close()
                self.socket = None
                self.engine.disarm()
            if not self.stop_event.is_set():
                self.stop_event.wait(3)
        self.engine.ma_status('disconnected', 'Disconnected')

    def session(self, s):
        decoder, buffer = TelnetFilter(), ''
        authenticated, login_sent = False, False
        deadline = time.monotonic() + 5
        last_tick = last_keepalive = time.monotonic()
        self.engine.ma_status('login', 'Waiting for console login')
        while not self.stop_event.is_set():
            now = time.monotonic()
            ready, _, _ = select.select([s], [], [], 0.01)
            if ready:
                data = s.recv(16384)
                if not data:
                    raise OSError('Console disconnected; output held. Re-arm after reconnecting.')
                plain, reply = decoder.feed(data)
                if reply:
                    s.sendall(reply)
                buffer = (buffer + plain.decode('utf-8', errors='replace'))[-8192:]
                buffer = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', buffer)
                lower = buffer.lower()
                if 'remote command disabled' in lower or 'login disabled' in lower:
                    raise ValueError('Enable Telnet Login in grandMA2 Global Settings')
                if 'no login' in lower or 'login failed' in lower or 'error #43' in lower:
                    raise ValueError('grandMA2 login rejected; check username and password')
                if login_sent and re.search(r'logged in as user', lower):
                    if re.search(r'logged in as user\s*["\']?guest\b', lower):
                        raise ValueError('Use a grandMA2 user with playback rights, not Guest')
                    authenticated = True
                    self.engine.ma_status('ready', 'Logged in; ready for fader commands')
                    buffer = ''
                elif authenticated and re.search(r'\berror\b|\bdenied\b|please login', lower):
                    raise ValueError('Console rejected a command or ended login; output held')
                elif authenticated and ('>' in buffer or '\n' in buffer):
                    buffer = ''
            if not login_sent and ('please login' in buffer.lower() or now > deadline - 4.5):
                user = self.settings['ma_user']
                s.sendall(f'Login "{user}" "{self.password}"\r\n'.encode())
                login_sent = True
                buffer = ''
            if not authenticated:
                if now > deadline:
                    raise ValueError('No login acknowledgement from grandMA2')
                continue
            if now - last_tick >= 1 / self.settings['rate_hz']:
                # Lock through send: a completed disarm cannot race with queued output.
                with self.engine.lock:
                    commands = self.engine.pending_commands(now)
                    if commands:
                        s.sendall(('\r\n'.join(c[2] for c in commands) + '\r\n').encode('ascii'))
                        self.engine.mark_sent(commands, now)
                last_tick = now
            if now - last_keepalive > 3:
                s.sendall(b'\r\n')
                last_keepalive = now

    def close(self):
        self.stop_event.set()
        if self.socket:
            try:
                self.socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self.thread.join(timeout=3)
