"""Bounded PSN v2 datagram decoding, based on VYV's published wire format.

A datagram can contain only a subset of trackers in a frame. Apply those
updates immediately; never clear the other trackers when a split packet arrives.
"""
from dataclasses import dataclass, field
import math
import struct

AXES = ('x', 'y', 'z', 'rx', 'ry', 'rz')


class PacketError(ValueError):
    pass


def chunks(data):
    pos = 0
    while pos < len(data):
        if len(data) - pos < 4:
            raise PacketError('Truncated chunk header')
        ident, flags = struct.unpack_from('<HH', data, pos)
        size = flags & 0x7fff
        end = pos + 4 + size
        if end > len(data):
            raise PacketError('Chunk exceeds parent boundary')
        yield ident, bool(flags & 0x8000), data[pos + 4:end]
        pos = end


@dataclass
class Packet:
    kind: str
    timestamp: int
    frame: int
    parts: int
    system: str = ''
    names: dict = field(default_factory=dict)
    trackers: dict = field(default_factory=dict)


def decode(data):
    roots = list(chunks(data))
    if len(roots) != 1 or roots[0][0] not in (0x6755, 0x6756):
        raise PacketError('Not a PSN v2 packet')
    kind, nested, body = roots[0]
    if not nested:
        raise PacketError('PSN root must contain children')
    children = list(chunks(body))
    headers = [payload for ident, _, payload in children if ident == 0]
    if len(headers) != 1 or len(headers[0]) < 12:
        raise PacketError('Missing PSN packet header')
    timestamp, major, minor, frame, parts = struct.unpack_from('<QBBBB', headers[0])
    if major != 2 or parts == 0:
        raise PacketError('Unsupported PSN version or invalid frame count')
    packet = Packet('data' if kind == 0x6755 else 'info', timestamp, frame, parts)
    for ident, nested, payload in children:
        if kind == 0x6756 and ident == 1:
            packet.system = payload.rstrip(b'\0').decode('utf-8', errors='replace')[:256]
        elif ident == (2 if kind == 0x6756 else 1):
            if not nested:
                raise PacketError('Tracker list must contain children')
            for tracker_id, tracker_nested, tracker in chunks(payload):
                if not tracker_nested:
                    raise PacketError('Tracker must contain children')
                values = {}
                for field_id, _, value in chunks(tracker):
                    if kind == 0x6756 and field_id == 0:
                        packet.names[tracker_id] = value.rstrip(b'\0').decode('utf-8', errors='replace')[:256]
                    elif kind == 0x6755 and field_id in (0, 2):
                        if len(value) < 12:
                            raise PacketError('Truncated position/orientation')
                        vector = struct.unpack_from('<fff', value)
                        if not all(math.isfinite(v) for v in vector):
                            raise PacketError('Non-finite position/orientation')
                        values.update(zip(AXES[:3] if field_id == 0 else AXES[3:], vector))
                if kind == 0x6755:
                    packet.trackers[tracker_id] = values
    return packet


def _chunk(ident, payload, children=False):
    return struct.pack('<HH', ident, len(payload) | (0x8000 if children else 0)) + payload


def encode_demo(tracker_id, xyz, timestamp, name=None, frame=0):
    """Small PSN sender used by the bundled diagnostic simulator."""
    header = _chunk(0, struct.pack('<QBBBB', timestamp, 2, 0, frame % 256, 1))
    if name is not None:
        tracker = _chunk(tracker_id, _chunk(0, name.encode() + b'\0'), True)
        return _chunk(0x6756, header + _chunk(1, b'AxisBridge simulator\0') + _chunk(2, tracker, True), True)
    tracker = _chunk(tracker_id, _chunk(0, struct.pack('<fff', *xyz)), True)
    return _chunk(0x6755, header + _chunk(1, tracker, True), True)
