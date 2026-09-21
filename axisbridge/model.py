import copy
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import tempfile
import uuid
from .psn import AXES


def default_show():
    return {'version': 1, 'name': 'Untitled show', 'network': {
        'psn_interface': '', 'psn_mode': 'multicast', 'psn_group': '236.10.10.10',
        'psn_port': 56565, 'psn_source': '', 'ma_interface': '', 'ma_host': '',
        'ma_port': 30000, 'ma_user': '', 'rate_hz': 25, 'timeout_ms': 1000,
        'deadband': 0.1}, 'blocks': []}


def ipv4(value, label, optional=False):
    if optional and value == '':
        return value
    try:
        address = ipaddress.IPv4Address(value)
        if address.is_unspecified or address.is_multicast or str(address) == '255.255.255.255':
            raise ValueError()
        return str(address)
    except (ValueError, TypeError):
        raise ValueError(f'{label} must be a specific IPv4 address')


def number(value, label, low=None, high=None, integer=False):
    if isinstance(value, bool):
        raise ValueError(f'{label} must be a number')
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError(f'{label} must be a number')
    if not math.isfinite(result) or (low is not None and result < low) or (high is not None and result > high):
        raise ValueError(f'{label} is outside the allowed range')
    if integer and result != int(result):
        raise ValueError(f'{label} must be a whole number')
    return int(result) if integer else result


def validate_show(raw):
    if not isinstance(raw, dict) or raw.get('version') != 1:
        raise ValueError('Unsupported show format (expected version 1)')
    result = default_show()
    result['name'] = str(raw.get('name', '')).strip()[:100] or 'Untitled show'
    net = raw.get('network', {})
    if not isinstance(net, dict):
        raise ValueError('Invalid network settings')
    n = result['network']
    for key in n:
        if key in net:
            n[key] = net[key]
    for key in ('psn_interface', 'psn_source', 'ma_interface', 'ma_host'):
        n[key] = ipv4(n[key], key.replace('_', ' '), optional=True)
    if n['psn_mode'] not in ('multicast', 'unicast'):
        raise ValueError('Choose multicast or unicast')
    try:
        if not ipaddress.IPv4Address(n['psn_group']).is_multicast:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError('PSN group must be an IPv4 multicast address')
    for key in ('psn_port', 'ma_port'):
        n[key] = number(n[key], key, 1, 65535, True)
    n['rate_hz'] = number(n['rate_hz'], 'Output rate', 1, 40, True)
    n['timeout_ms'] = number(n['timeout_ms'], 'Signal timeout', 100, 30000, True)
    n['deadband'] = number(n['deadband'], 'Deadband', 0, 10)
    n['ma_user'] = str(n['ma_user']).strip()
    if len(n['ma_user']) > 64 or any(c in n['ma_user'] for c in '\r\n;"\\'):
        raise ValueError('MA username contains unsupported characters')
    rows = raw.get('blocks', [])
    if not isinstance(rows, list) or len(rows) > 128:
        raise ValueError('A show supports up to 128 control blocks')
    ids, targets = set(), set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Invalid control block')
        ident = str(row.get('id') or uuid.uuid4())
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', ident) or ident in ids:
            raise ValueError('Block IDs must be unique')
        ids.add(ident)
        source = row.get('source', '')
        if source != 'demo':
            source = ipv4(source, 'Entity source', optional=True)
        tracker = row.get('tracker_id')
        if tracker is not None:
            tracker = number(tracker, 'Tracker ID', 0, 65535, True)
        axis = row.get('axis', 'z')
        if axis not in AXES:
            raise ValueError('Choose X, Y, Z, RX, RY, or RZ')
        bottom = None if row.get('bottom') is None else number(row['bottom'], 'Bottom')
        top = None if row.get('top') is None else number(row['top'], 'Top')
        if bottom is not None and top is not None and abs(top - bottom) < 1e-9:
            raise ValueError('Top and bottom positions must be different')
        enabled = row.get('enabled', True)
        if not isinstance(enabled, bool):
            raise ValueError('Enabled must be true or false')
        outputs = row.get('targets', [])
        if not isinstance(outputs, list) or len(outputs) > 64:
            raise ValueError('A block supports up to 64 faders')
        clean_targets = []
        for target in outputs:
            if not isinstance(target, str) or not re.fullmatch(r'[1-9][0-9]{0,3}\.[1-9][0-9]{0,2}', target):
                raise ValueError('Use page.executor for faders, e.g. 1.1, 1.2, 2.15')
            if target in clean_targets or (enabled and target in targets):
                raise ValueError(f'Fader {target} is assigned more than once; disable the other block first')
            clean_targets.append(target)
            if enabled:
                targets.add(target)
        result['blocks'].append({'id': ident, 'name': str(row.get('name', 'Control block'))[:100],
            'source': source, 'tracker_id': tracker, 'axis': axis, 'bottom': bottom, 'top': top,
            'enabled': enabled, 'targets': clean_targets,
            'smoothing_ms': number(row.get('smoothing_ms', 0), 'Smoothing', 0, 10000, True)})
    return result


def normalize(value, bottom, top):
    if bottom is None or top is None or abs(top - bottom) < 1e-9:
        raise ValueError('Capture different bottom and top positions')
    if not all(math.isfinite(v) for v in (value, bottom, top)):
        raise ValueError('Non-finite calibration')
    return max(0.0, min(100.0, (value - bottom) * 100.0 / (top - bottom)))


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name, suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
