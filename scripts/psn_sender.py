"""Optional diagnostic sender. Sends genuine PSN v2 UDP datagrams."""
import argparse
import math
from pathlib import Path
import socket
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from axisbridge.psn import encode_demo

parser = argparse.ArgumentParser()
parser.add_argument('--destination', default='127.0.0.1')
parser.add_argument('--port', type=int, default=56565)
parser.add_argument('--interface', default='127.0.0.1')
parser.add_argument('--tracker', type=int, default=7)
parser.add_argument('--name', default='Diagnostic truss')
parser.add_argument('--value', type=float, help='Fixed Z; omit for a 0–10 moving wave')
args = parser.parse_args()
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((args.interface, 0))
sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(args.interface))
sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
frame = 0
try:
    print('Sending PSN; Ctrl+C stops.')
    while True:
        timestamp = time.monotonic_ns() // 1000
        value = args.value if args.value is not None else 5 + 5 * math.sin(time.monotonic() / 4)
        if frame % 25 == 0:
            sock.sendto(encode_demo(args.tracker, (0,0,0), timestamp, args.name, frame), (args.destination, args.port))
        sock.sendto(encode_demo(args.tracker, (0,0,value), timestamp, frame=frame), (args.destination, args.port))
        frame += 1
        time.sleep(0.04)
except KeyboardInterrupt:
    pass
finally:
    sock.close()
