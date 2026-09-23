import copy
import json
import math
import os
from pathlib import Path
import threading
import time
from collections import deque
from .model import default_show, validate_show, normalize, atomic_json
from .network import PSNReceiver, MAConnection
from .psn import decode, encode_demo

COMMAND_REFRESH_SECONDS = 10
DEMO_NAMES = ['Upstage truss', 'Center pod', 'Stage lift']


class Conflict(ValueError):
    pass


class Engine:
    def __init__(self, data_dir):
        self.lock = threading.RLock()
        self.operation_lock = threading.Lock()
        self.path = Path(data_dir) / 'current-show.json'
        self.password_path = Path(data_dir) / 'ma-password.txt'
        self.show = validate_show(json.loads(self.path.read_text())) if self.path.exists() else default_show()
        self.revision = 1
        self.receiver = self.ma = None
        self.armed = self.demo = False
        self.password = self.load_password()
        self.entities = {}
        self.stats = {'packets': 0, 'bad_packets': 0, 'out_of_order': 0, 'commands': 0}
        self.input_state, self.input_message = 'stopped', 'PSN input stopped'
        self.ma_state, self.ma_message = 'disconnected', 'grandMA2 disconnected'
        self.last_sent, self.sent_history, self.smoothed = {}, {}, {}
        self.events = deque(maxlen=100)
        self.log('Application started. Output held.')
        self.started = time.monotonic()
        self.demo_levels = {1: 0.0, 2: 50.0, 3: 100.0}
        self.demo_timestamp = 0
        self.closed = threading.Event()
        self.demo_thread = threading.Thread(target=self.demo_loop, daemon=True)
        self.demo_thread.start()
        self.start_automatic_connections()
        self.auto_thread = threading.Thread(target=self.auto_connect_loop, name='Automatic connections', daemon=True)
        self.auto_thread.start()

    def load_password(self):
        try:
            password = self.password_path.read_text(encoding='utf-8')
            if len(password) > 128 or any(c in password for c in '\r\n;"\\'):
                raise ValueError('Stored MA password is invalid')
            return password
        except FileNotFoundError:
            return ''

    def save_password(self, password):
        self.password_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.password_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                stream.write(password)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            os.chmod(self.password_path, 0o600)

    def start_automatic_connections(self):
        network = self.show['network']
        if network['auto_start_psn'] and network['psn_interface']:
            try:
                self.start_input()
                self.log('PSN auto-started from saved network settings')
            except (OSError, ValueError) as exc:
                self.input_error('Automatic PSN start failed: ' + str(exc))
        if network['auto_connect_ma'] and all(network[k] for k in ('ma_interface', 'ma_host', 'ma_user')):
            try:
                self.connect_ma(None)
                self.log('MA auto-connect started; output remains held')
            except (OSError, ValueError) as exc:
                self.ma_status('error', 'Automatic MA connection failed: ' + str(exc))

    def auto_connect_loop(self):
        while not self.closed.wait(3):
            with self.lock:
                network = self.show['network']
                retry_psn = network['auto_start_psn'] and network['psn_interface'] and not self.receiver and not self.demo
                retry_ma = network['auto_connect_ma'] and not self.ma and all(network[k] for k in ('ma_interface', 'ma_host', 'ma_user'))
            if retry_psn:
                try:
                    self.start_input()
                    self.log('PSN connected automatically after adapter became available')
                except (OSError, ValueError) as exc:
                    self.input_error('Automatic PSN start failed: ' + str(exc))
            if retry_ma:
                try:
                    self.connect_ma(None)
                    self.log('MA auto-connect restarted; output remains held')
                except (OSError, ValueError) as exc:
                    self.ma_status('error', 'Automatic MA connection failed: ' + str(exc))

    def log(self, message):
        with self.lock:
            self.events.appendleft({'time': time.strftime('%H:%M:%S'), 'message': message})

    def disarm(self):
        with self.lock:
            self.armed = False
            self.last_sent.clear()
            self.smoothed.clear()

    def ma_status(self, state, message):
        with self.lock:
            changed = state != self.ma_state or message != self.ma_message
            self.ma_state, self.ma_message = state, message
            if state != 'ready':
                self.disarm()
            if changed:
                self.log(message)

    def input_error(self, message):
        with self.lock:
            changed = self.input_state != 'error' or self.input_message != message
            self.input_state, self.input_message = 'error', message
            self.disarm()
            if changed:
                self.log('PSN: ' + message)

    def bad_packet(self):
        with self.lock:
            self.stats['bad_packets'] += 1

    def ingest(self, source, packet, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            self.stats['packets'] += 1
            for ident in set(packet.names) | set(packet.trackers):
                key = f'{source}/{ident}'
                if key not in self.entities:
                    if len(self.entities) >= 4096:
                        oldest = min(self.entities, key=lambda k: self.entities[k]['seen'])
                        del self.entities[oldest]
                    self.entities[key] = {'key': key, 'source': source, 'tracker_id': ident,
                        'name': f'Entity {ident}', 'system': '', 'values': {}, 'times': {},
                        'timestamps': {}, 'seen': now}
                entity = self.entities[key]
                entity['seen'] = now
                if packet.system:
                    entity['system'] = packet.system
                if ident in packet.names:
                    entity['name'] = packet.names[ident]
                # Metadata never refreshes an axis's freshness timer.
                for axis, value in packet.trackers.get(ident, {}).items():
                    previous = entity['timestamps'].get(axis, -1)
                    age = now - entity['times'].get(axis, -1e9)
                    if packet.timestamp <= previous and age < self.show['network']['timeout_ms'] / 1000:
                        self.stats['out_of_order'] += 1
                        continue
                    entity['values'][axis] = value
                    entity['times'][axis] = now
                    entity['timestamps'][axis] = packet.timestamp

    def block_state(self, block, now):
        entity = self.entities.get(f"{block['source']}/{block['tracker_id']}")
        axis = block['axis']
        value = entity['values'].get(axis) if entity else None
        age = now - entity['times'].get(axis, -1e9) if entity else None
        percent, status = None, 'Uncalibrated'
        if not block['enabled']:
            status = 'Disabled'
        elif value is None:
            status = 'No signal'
        elif age > self.show['network']['timeout_ms'] / 1000:
            status = 'Signal lost · holding'
        elif block['bottom'] is None or block['top'] is None:
            status = 'Capture bottom & top'
        else:
            percent = normalize(value, block['bottom'], block['top'])
            status = ('Demo live' if self.armed else 'Preview') if self.demo else ('Live' if self.armed else 'Held')
            if not block['targets']:
                status = 'Assign a fader'
        sent = {target: self.sent_history[target]['level'] for target in block['targets'] if target in self.sent_history}
        return {'id': block['id'], 'value': value, 'age_ms': round(age * 1000) if age is not None else None,
                'percent': percent, 'status': status, 'sent': sent}

    def pending_commands(self, now):
        if not self.armed or self.ma_state != 'ready':
            return []
        live_status = 'Demo live' if self.demo else 'Live'
        commands = []
        for block in self.show['blocks']:
            state = self.block_state(block, now)
            if state['status'] != live_status:
                self.smoothed.pop(block['id'], None)
                # Remove stale dedup state so recovered input resends current position.
                for target in block['targets']:
                    self.last_sent.pop(target, None)
                continue
            level = state['percent']
            previous = self.smoothed.get(block['id'])
            if previous and block['smoothing_ms']:
                alpha = 1 - math.exp(-(now - previous[1]) / (block['smoothing_ms'] / 1000))
                level = previous[0] + alpha * (level - previous[0])
            self.smoothed[block['id']] = (level, now)
            level = round(level, 2)
            for target in block['targets']:
                old = self.last_sent.get(target)
                if old is None or (level != old['level'] and
                        (abs(level - old['level']) >= self.show['network']['deadband'] or level in (0, 100))) or now - old['at'] >= COMMAND_REFRESH_SECONDS:
                    commands.append((target, level, f'Fader {target} At {level:.2f}'))
        return commands

    def mark_sent(self, commands, now):
        for target, level, command in commands:
            self.last_sent[target] = {'level': level, 'at': now, 'command': command}
            self.sent_history[target] = dict(self.last_sent[target])
        self.stats['commands'] += len(commands)

    def config(self):
        with self.lock:
            return {'show': copy.deepcopy(self.show), 'revision': self.revision}

    def save(self, raw, revision):
        clean = validate_show(raw)
        with self.lock:
            if revision != self.revision:
                raise Conflict('Show changed in another window. Reload before saving.')
            if self.armed:
                raise Conflict('Hold output before changing the show.')
            transport_keys = set(clean['network']) - {'auto_start_psn', 'auto_connect_ma'}
            changed_network = any(clean['network'][key] != self.show['network'][key] for key in transport_keys)
            if changed_network and (self.receiver or self.ma):
                raise Conflict('Stop PSN and disconnect MA before changing network settings.')
            atomic_json(self.path, clean)
            self.show = clean
            self.revision += 1
            self.smoothed.clear()
            self.last_sent.clear()
            self.sent_history.clear()
            self.log('Show saved: ' + clean['name'])
            return self.config()

    def capture(self, block_id, endpoint, revision):
        with self.lock:
            if endpoint not in ('bottom', 'top'):
                raise ValueError('Choose bottom or top')
            if self.armed:
                raise Conflict('Hold output before capturing positions.')
            row = next((b for b in self.show['blocks'] if b['id'] == block_id), None)
            if row is None:
                raise ValueError('Block not found')
            state = self.block_state(row, time.monotonic())
            if state['value'] is None or state['age_ms'] > self.show['network']['timeout_ms']:
                raise ValueError('A fresh sample from the selected axis is required')
            draft = copy.deepcopy(self.show)
            next(b for b in draft['blocks'] if b['id'] == block_id)[endpoint] = state['value']
            result = self.save(draft, revision)
            self.log(f"Captured {endpoint} for {row['name']}: {state['value']:.5f}")
            return result

    def start_input(self):
        with self.lock:
            if self.demo:
                raise ValueError('Exit demo before starting live PSN')
            if self.receiver:
                return
            if not self.show['network']['psn_interface']:
                raise ValueError('Select the PSN adapter IPv4 address in Network')
            self.entities.clear()
            self.receiver = PSNReceiver(self, copy.deepcopy(self.show['network']))
            self.input_state, self.input_message = 'listening', 'Listening for PSN'
            self.log('PSN receiver started')

    def stop_input(self):
        self.disarm()
        receiver, self.receiver = self.receiver, None
        if receiver:
            receiver.close()
        with self.lock:
            self.entities.clear()
            self.input_state, self.input_message = 'stopped', 'PSN input stopped'
            self.log('PSN receiver stopped; output held')

    def connect_ma(self, password):
        n = self.show['network']
        if not all(n[k] for k in ('ma_interface', 'ma_host', 'ma_user')):
            raise ValueError('Set MA adapter, console IP, and username in Network')
        if password is not None:
            if not isinstance(password, str) or len(password) > 128 or any(c in password for c in '\r\n;"\\'):
                raise ValueError('Password contains unsupported characters')
            self.password = password
            self.save_password(password)
        self.disconnect_ma()
        self.ma = MAConnection(self, copy.deepcopy(n), self.password)

    def disconnect_ma(self):
        self.disarm()
        ma, self.ma = self.ma, None
        if ma:
            ma.close()
        self.ma_status('disconnected', 'grandMA2 disconnected')

    def arm(self):
        with self.lock:
            if self.ma_state != 'ready':
                raise ValueError('A logged-in MA connection is required')
            if not self.demo and not self.receiver:
                raise ValueError('Live PSN input is required outside demo mode')
            active = [b for b in self.show['blocks'] if b['enabled']]
            if not active:
                raise ValueError('Add an enabled control block first')
            ready_status = 'Preview' if self.demo else 'Held'
            for block in active:
                state = self.block_state(block, time.monotonic())
                if state['status'] != ready_status:
                    raise ValueError(f"{block['name']}: {state['status']}")
            self.last_sent.clear()
            self.smoothed.clear()
            self.armed = True
            self.log('Demo output armed. Faders follow demo controls.' if self.demo else 'Output armed. Faders follow live positions.')

    def set_demo(self, enabled):
        if not isinstance(enabled, bool):
            raise ValueError('Demo enabled must be true or false')
        self.stop_input()
        with self.lock:
            self.demo = enabled
            self.entities.clear()
            self.input_state = 'demo' if enabled else 'stopped'
            self.input_message = 'Demo signals · MA output available' if enabled else 'PSN input stopped'
            self.log('Demo enabled; output held until explicitly armed' if enabled else 'Demo closed; output held')

    def next_demo_timestamp(self):
        self.demo_timestamp = max(time.monotonic_ns() // 1000, self.demo_timestamp + 1)
        return self.demo_timestamp

    def ingest_demo_entity(self, ident, level, timestamp):
        self.ingest('demo', decode(encode_demo(ident, (0, 0, 0), timestamp, name=DEMO_NAMES[ident - 1])))
        self.ingest('demo', decode(encode_demo(ident, (level / 10, level / 10, level / 10), timestamp)))

    def set_demo_level(self, ident, level):
        with self.lock:
            if ident not in self.demo_levels:
                raise ValueError('Demo entity must be 1, 2, or 3')
            self.demo_levels[ident] = level
            if self.demo:
                self.ingest_demo_entity(ident, level, self.next_demo_timestamp())

    def demo_loop(self):
        while not self.closed.wait(0.04):
            with self.lock:
                if not self.demo:
                    continue
                timestamp = self.next_demo_timestamp()
                for ident, level in self.demo_levels.items():
                    self.ingest_demo_entity(ident, level, timestamp)

    def snapshot(self):
        now = time.monotonic()
        with self.lock:
            entities = []
            for e in self.entities.values():
                entities.append({k: copy.deepcopy(e[k]) for k in ('key', 'source', 'tracker_id', 'name', 'system', 'values')})
                entities[-1]['ages_ms'] = {a: round((now - t) * 1000) for a, t in e['times'].items()}
            return {'revision': self.revision, 'armed': self.armed, 'demo': self.demo,
                'input': {'state': self.input_state, 'message': self.input_message, 'running': bool(self.receiver)},
                'ma': {'state': self.ma_state, 'message': self.ma_message, 'running': bool(self.ma)},
                'stats': dict(self.stats), 'entities': sorted(entities, key=lambda e: (e['source'], e['tracker_id'])),
                'blocks': [self.block_state(b, now) for b in self.show['blocks']],
                'events': list(self.events), 'uptime': int(now - self.started),
                'demo_levels': dict(self.demo_levels), 'last_sent': copy.deepcopy(self.sent_history)}

    def close(self):
        self.closed.set()
        self.stop_input()
        self.disconnect_ma()
        self.demo_thread.join(timeout=1)
        self.auto_thread.join(timeout=1)
