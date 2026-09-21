import copy
import json
import math
from pathlib import Path
import socket
import struct
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from axisbridge.psn import decode, encode_demo, _chunk, PacketError
from axisbridge.model import default_show, validate_show, normalize
from axisbridge.engine import Engine, Conflict
from axisbridge.network import TelnetFilter
from axisbridge.server import Server


def block(**kwargs):
    result = {'id': 'block1', 'name': 'Truss', 'source': '127.0.0.1', 'tracker_id': 7,
              'axis': 'z', 'bottom': 0, 'top': 10, 'enabled': True,
              'targets': ['1.1', '2.15'], 'smoothing_ms': 0}
    result.update(kwargs)
    return result


def wait_for(predicate, timeout=3):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError('Timed out waiting for condition')


class CalibrationTests(unittest.TestCase):
    def test_ascending_and_clamping(self):
        self.assertEqual([normalize(v, 2, 12) for v in (-5, 2, 7, 12, 20)], [0, 0, 50, 100, 100])

    def test_descending_negative_range(self):
        self.assertEqual([normalize(v, 5, -5) for v in (10, 5, 0, -5, -10)], [0, 0, 50, 100, 100])

    def test_reject_invalid_calibration(self):
        for args in ((1, 2, 2), (1, None, 5), (math.nan, 0, 1)):
            with self.assertRaises(ValueError): normalize(*args)

    def test_duplicate_faders_rejected(self):
        show = default_show()
        show['blocks'] = [block(), block(id='second')]
        with self.assertRaises(ValueError): validate_show(show)
        show['blocks'][1]['enabled'] = False
        self.assertEqual(len(validate_show(show)['blocks']), 2)

    def test_target_injection_rejected(self):
        for target in ('1.1;Off All', '1.1\r\nGo', '1', '0.1', '1.0', '1.01'):
            show = default_show()
            show['blocks'] = [block(targets=[target])]
            with self.assertRaises(ValueError): validate_show(show)

    def test_explicit_interfaces_and_version(self):
        show = default_show()
        show['network']['ma_interface'] = '0.0.0.0'
        with self.assertRaises(ValueError): validate_show(show)
        show = default_show(); show['version'] = 2
        with self.assertRaises(ValueError): validate_show(show)


class ProtocolTests(unittest.TestCase):
    def test_golden_wire_packet(self):
        # Hand-authored PSN 2.0 packet: root/data header/list/tracker 7/XYZ.
        data = bytes.fromhex('5567288000000c0040420f000000000002002a01010014800700108000000c000000803f000000c00000b040')
        packet = decode(data)
        self.assertEqual(packet.timestamp, 1000000)
        self.assertEqual(packet.frame, 42)
        self.assertEqual(packet.trackers[7], {'x': 1, 'y': -2, 'z': 5.5})

    def test_official_vyv_multi_packet_frames(self):
        trackers, names = {}, {}
        for path in sorted((Path(__file__).parent / 'fixtures').glob('*.bin')):
            packet = decode(path.read_bytes())
            trackers.update(packet.trackers)
            names.update(packet.names)
        self.assertEqual(len(trackers), 150)
        self.assertEqual(len(names), 150)
        self.assertEqual(trackers[149]['z'], 149)
        self.assertEqual(names[149], 'Tracker 149')

    def test_names_and_partial_frame(self):
        info = decode(encode_demo(7, (0, 0, 0), 1, 'Upstage truss'))
        self.assertEqual(info.names[7], 'Upstage truss')
        self.assertEqual(info.system, 'AxisBridge simulator')
        data = bytearray(encode_demo(7, (1, 2, 3), 1))
        data[19] = 3
        self.assertEqual(decode(data).parts, 3)

    def test_orientation_and_unknown_field(self):
        header = _chunk(0, struct.pack('<QBBBB', 99, 2, 0, 1, 1))
        vector = _chunk(2, struct.pack('<fff', 0.1, 0.2, 0.3))
        tracker = _chunk(7, vector + _chunk(888, b'future'), True)
        packet = decode(_chunk(0x6755, header + _chunk(1, tracker, True), True))
        self.assertEqual(set(packet.trackers[7]), {'rx', 'ry', 'rz'})
        self.assertAlmostEqual(packet.trackers[7]['rz'], 0.3, places=6)

    def test_truncation_and_bad_numbers(self):
        packet = encode_demo(7, (1, 2, 3), 1)
        for cut in range(len(packet)):
            with self.assertRaises(PacketError): decode(packet[:cut])
        for bad in (b'garbage', encode_demo(7, (math.nan, 1, 2), 1), encode_demo(7, (math.inf, 1, 2), 1)):
            with self.assertRaises(PacketError): decode(bad)

    def test_split_telnet_negotiation(self):
        f = TelnetFilter()
        self.assertEqual(f.feed(b'Hello\xff'), (b'Hello', b''))
        self.assertEqual(f.feed(bytes([253])), (b'', b''))
        self.assertEqual(f.feed(bytes([1]) + b' world'), (b' world', bytes([255,252,1])))
        self.assertEqual(f.feed(bytes([255,250,1,9,255])), (b'', b''))
        self.assertEqual(f.feed(bytes([240]) + b'Done'), (b'Done', b''))


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.e = Engine(self.tmp.name)
        show = default_show(); show['blocks'] = [block()]
        self.e.save(show, self.e.revision)

    def tearDown(self):
        self.e.close(); self.tmp.cleanup()

    def sample(self, value, now, timestamp=100):
        self.e.ingest('127.0.0.1', decode(encode_demo(7, (0, 0, value), timestamp)), now=now)

    def test_mapping_to_multiple_faders_and_deadband(self):
        self.sample(5, 10)
        self.e.ma_state = 'ready'; self.e.armed = True
        commands = self.e.pending_commands(10.1)
        self.assertEqual([c[2] for c in commands], ['Fader 1.1 At 50.00', 'Fader 2.15 At 50.00'])
        self.e.mark_sent(commands, 10.1)
        self.assertEqual(self.e.pending_commands(10.2), [])
        self.sample(5.001, 10.25, 101)
        self.assertEqual(self.e.pending_commands(10.3), [])

    def test_stale_data_holds_and_info_does_not_refresh(self):
        self.sample(5, 10)
        self.e.ingest('127.0.0.1', decode(encode_demo(7, (0,0,0), 200, 'Renamed')), now=12)
        self.e.ma_state='ready'; self.e.armed=True
        self.assertEqual(self.e.pending_commands(12), [])
        self.assertIn('Signal lost', self.e.block_state(self.e.show['blocks'][0], 12)['status'])

    def test_out_of_order_and_source_restart(self):
        self.sample(5, 10, 100)
        self.sample(1, 10.1, 99)
        self.assertEqual(self.e.block_state(self.e.show['blocks'][0], 10.2)['value'], 5)
        self.sample(1, 12, 1)
        self.assertEqual(self.e.block_state(self.e.show['blocks'][0], 12)['value'], 1)

    def test_sources_with_same_tracker_id_remain_separate(self):
        self.sample(5, 10)
        self.e.ingest('10.0.0.1', decode(encode_demo(7, (0,0,9), 100)), now=10)
        self.assertEqual(len(self.e.entities), 2)
        self.assertEqual(self.e.block_state(self.e.show['blocks'][0], 10)['value'], 5)

    def test_orientation_does_not_refresh_position(self):
        self.sample(5, 10)
        from axisbridge.psn import Packet
        self.e.ingest('127.0.0.1', Packet('data', 200, 1, 1, trackers={7:{'rx':0.5}}), now=12)
        self.assertIn('Signal lost', self.e.block_state(self.e.show['blocks'][0], 12)['status'])

    def test_capture_uses_current_axis_and_persists(self):
        self.sample(-3.25, time.monotonic())
        result=self.e.capture('block1', 'bottom', self.e.revision)
        self.assertEqual(result['show']['blocks'][0]['bottom'], -3.25)
        saved=json.loads(self.e.path.read_text())
        self.assertEqual(saved['blocks'][0]['bottom'], -3.25)
        self.assertNotIn('password', json.dumps(saved))

    def test_capture_rejects_equal_or_stale_endpoints(self):
        self.sample(10, time.monotonic())
        with self.assertRaises(ValueError): self.e.capture('block1', 'bottom', self.e.revision)
        self.sample(5, time.monotonic()-5, 1)
        # Previous newer sample is not overwritten while fresh.
        self.e.entities['127.0.0.1/7']['times']['z'] = time.monotonic()-5
        with self.assertRaises(ValueError): self.e.capture('block1', 'top', self.e.revision)

    def test_atomic_save_and_conflict(self):
        with self.assertRaises(Conflict): self.e.save(self.e.show, self.e.revision-1)
        self.assertEqual(json.loads(self.e.path.read_text()), self.e.show)
        self.e.armed=True
        with self.assertRaises(Conflict): self.e.save(self.e.show, self.e.revision)

    def test_demo_cannot_output(self):
        self.e.demo=True; self.e.armed=True; self.e.ma_state='ready'
        self.sample(5,time.monotonic())
        self.assertEqual(self.e.pending_commands(time.monotonic()), [])
        with self.assertRaises(ValueError): self.e.arm()

    def test_smoothing_is_time_based(self):
        self.e.show['blocks'][0]['smoothing_ms']=1000
        self.e.ma_state='ready'; self.e.armed=True
        self.sample(0,10,1); self.e.pending_commands(10)
        self.sample(10,10.5,2)
        commands=self.e.pending_commands(11)
        self.assertAlmostEqual(commands[0][1],63.21,places=2)


class MockConsole:
    def __init__(self, reject=False):
        self.server=socket.socket(); self.server.bind(('127.0.0.1',0)); self.server.listen()
        self.port=self.server.getsockname()[1]; self.commands=[]; self.peer=None; self.client=None
        self.stop=threading.Event(); self.reject=reject
        self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()

    def run(self):
        self.server.settimeout(0.1)
        while not self.stop.is_set():
            try: conn, peer=self.server.accept()
            except socket.timeout: continue
            except OSError: return
            self.peer=peer;self.client=conn;conn.settimeout(0.1)
            try:
                conn.sendall(b'Please login\r\n')
                buffer=b''
                while not self.stop.is_set():
                    try: data=conn.recv(8192)
                    except socket.timeout: continue
                    if not data: break
                    buffer+=data
                    while b'\n' in buffer:
                        line,buffer=buffer.split(b'\n',1);line=line.strip().decode(errors='replace')
                        if line.lower().startswith('login'):
                            response=b'no login\r\n' if self.reject else b'Logged in as User "bridge"\r\n[Channel]>'
                            conn.sendall(response[:9]);conn.sendall(response[9:])
                        elif line:
                            self.commands.append(line);conn.sendall(b'[Channel]>')
            except OSError: pass
            finally: conn.close()

    def close(self):
        self.stop.set()
        if self.client:
            try: self.client.shutdown(socket.SHUT_RDWR)
            except OSError: pass
        self.server.close();self.thread.join(timeout=1)


class NetworkIntegrationTests(unittest.TestCase):
    def test_real_udp_to_tcp_with_two_bound_addresses(self):
        with tempfile.TemporaryDirectory() as directory:
            console=MockConsole();e=Engine(directory);sender=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
            psn_ip='127.0.0.3' if sys.platform.startswith('linux') else '127.0.0.1'
            ma_ip='127.0.0.2' if sys.platform.startswith('linux') else '127.0.0.1'
            probe=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);probe.bind((psn_ip,0));port=probe.getsockname()[1];probe.close()
            try:
                sender.bind(('127.0.0.1',0))
                show=default_show();show['blocks']=[block(bottom=None,top=None)]
                show['network'].update(psn_interface=psn_ip,psn_mode='unicast',psn_port=port,ma_interface=ma_ip,ma_host='127.0.0.1',ma_port=console.port,ma_user='bridge',timeout_ms=200)
                e.save(show,e.revision);e.start_input();e.connect_ma('test')
                wait_for(lambda:e.ma_state=='ready')
                self.assertEqual(console.peer[0],ma_ip)
                def send(z):
                    sender.sendto(encode_demo(7,(0,0,z),time.monotonic_ns()//1000),(psn_ip,port))
                    wait_for(lambda:e.snapshot()['blocks'][0]['value']==z)
                send(0);e.capture('block1','bottom',e.revision)
                send(10);e.capture('block1','top',e.revision)
                send(5);self.assertEqual(console.commands,[])
                e.arm();wait_for(lambda:len(console.commands)>=2)
                self.assertIn('Fader 1.1 At 50.00',console.commands)
                self.assertIn('Fader 2.15 At 50.00',console.commands)
                time.sleep(0.3);count=len(console.commands);time.sleep(0.12)
                self.assertEqual(len(console.commands),count)
                send(7.5);wait_for(lambda:'Fader 1.1 At 75.00' in console.commands)
                e.disarm();count=len(console.commands);send(9);time.sleep(0.12)
                self.assertEqual(len(console.commands),count)
                e.arm();console.client.shutdown(socket.SHUT_RDWR)
                wait_for(lambda:not e.armed)
                count=len(console.commands)
                wait_for(lambda:e.ma_state=='ready', timeout=5)
                send(4)
                time.sleep(0.1)
                self.assertFalse(e.armed)
                self.assertEqual(len(console.commands),count)
            finally:
                e.close();console.close();sender.close()

    def test_multicast_on_selected_interface(self):
        with tempfile.TemporaryDirectory() as directory:
            e=Engine(directory)
            sender=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
            probe=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
            probe.bind(('127.0.0.1',0));port=probe.getsockname()[1];probe.close()
            try:
                show=default_show()
                show['blocks']=[block()]
                show['network'].update(psn_interface='127.0.0.1',psn_mode='multicast',psn_port=port)
                e.save(show,e.revision);e.start_input()
                sender.bind(('127.0.0.1',0))
                sender.setsockopt(socket.IPPROTO_IP,socket.IP_MULTICAST_IF,socket.inet_aton('127.0.0.1'))
                sender.setsockopt(socket.IPPROTO_IP,socket.IP_MULTICAST_LOOP,1)
                sender.sendto(encode_demo(7,(0,0,6),123),('236.10.10.10',port))
                wait_for(lambda:e.snapshot()['blocks'][0]['value']==6)
            finally:e.close();sender.close()

    def test_rejected_login_never_sends_faders(self):
        with tempfile.TemporaryDirectory() as directory:
            c=MockConsole(reject=True);e=Engine(directory)
            try:
                show=default_show();show['network'].update(ma_interface='127.0.0.1',ma_host='127.0.0.1',ma_port=c.port,ma_user='bridge')
                e.save(show,e.revision);e.connect_ma('bad')
                wait_for(lambda:e.ma_state=='error')
                self.assertEqual(c.commands,[]);self.assertFalse(e.armed)
            finally:e.close();c.close()


class PortalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.e=Engine(self.tmp.name)
        self.server=Server(('127.0.0.1',0),self.e,'test-access-key')
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base=f'http://127.0.0.1:{self.server.server_address[1]}'
        self.cookie=''

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.e.close();self.tmp.cleanup()

    def request(self,path,data=None,origin=None):
        headers={'Cookie':self.cookie}
        if origin: headers['Origin']=origin
        if data is not None: headers['Content-Type']='application/json'
        return urlopen(Request(self.base+path,data=None if data is None else json.dumps(data).encode(),headers=headers),timeout=3)

    def login(self):
        with self.request('/api/login',{'key':'test-access-key'}) as r:
            self.cookie=r.headers['Set-Cookie'].split(';')[0]

    def test_authentication_and_origin(self):
        with self.assertRaises(HTTPError) as ctx:self.request('/api/state')
        self.assertEqual(ctx.exception.code,401)
        self.login()
        with self.request('/api/state') as r:self.assertFalse(json.load(r)['armed'])
        with self.assertRaises(HTTPError) as ctx:self.request('/api/action',{'action':'demo','enabled':True},origin='http://evil.example')
        self.assertEqual(ctx.exception.code,403)

    def test_show_roundtrip_and_conflicting_editor(self):
        self.login()
        with self.request('/api/config') as r:config=json.load(r)
        config['show']['blocks']=[block()]
        with self.request('/api/config',config) as r:result=json.load(r)
        self.assertEqual(result['show']['blocks'][0]['targets'],['1.1','2.15'])
        with self.assertRaises(HTTPError) as ctx:self.request('/api/config',config)
        self.assertEqual(ctx.exception.code,409)
        with self.request('/api/export') as r:self.assertNotIn('password',r.read().decode())

    def test_invalid_payload_and_static_files(self):
        self.login()
        with self.assertRaises(HTTPError) as ctx:self.request('/api/config',{'show':None})
        self.assertEqual(ctx.exception.code,400)
        for path in ('/','/app.js','/style.css'):
            with self.request(path) as r:self.assertEqual(r.status,200)


if __name__=='__main__':unittest.main(verbosity=2)
