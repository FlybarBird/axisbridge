"""Slate 7's 284x76 RGB565 display and four-second calibration controls.

Geometry follows GL.iNet's gl-lvgl Slate 7 patch. No extra Python packages.
The framebuffer and evdev device are used only by the optional router runner.
"""
import os
from pathlib import Path
import select
import struct
import subprocess
import threading
import time

WIDTH, HEIGHT = 284, 76
HOLD_SECONDS = 4.0
BUTTONS = {'bottom': (4, 23, 136, 38), 'top': (144, 23, 136, 38)}

# Original compact 5x7 bitmap alphabet. One five-bit mask per row.
FONT = {
    ' ': '00 00 00 00 00 00 00', 'A': '0e 11 11 1f 11 11 11',
    'B': '1e 11 11 1e 11 11 1e', 'C': '0e 11 10 10 10 11 0e',
    'D': '1e 11 11 11 11 11 1e', 'E': '1f 10 10 1e 10 10 1f',
    'F': '1f 10 10 1e 10 10 10', 'G': '0e 11 10 17 11 11 0f',
    'H': '11 11 11 1f 11 11 11', 'I': '0e 04 04 04 04 04 0e',
    'J': '07 02 02 02 12 12 0c', 'K': '11 12 14 18 14 12 11',
    'L': '10 10 10 10 10 10 1f', 'M': '11 1b 15 15 11 11 11',
    'N': '11 19 15 13 11 11 11', 'O': '0e 11 11 11 11 11 0e',
    'P': '1e 11 11 1e 10 10 10', 'Q': '0e 11 11 11 15 12 0d',
    'R': '1e 11 11 1e 14 12 11', 'S': '0f 10 10 0e 01 01 1e',
    'T': '1f 04 04 04 04 04 04', 'U': '11 11 11 11 11 11 0e',
    'V': '11 11 11 11 11 0a 04', 'W': '11 11 11 15 15 15 0a',
    'X': '11 11 0a 04 0a 11 11', 'Y': '11 11 0a 04 04 04 04',
    'Z': '1f 01 02 04 08 10 1f', '0': '0e 11 13 15 19 11 0e',
    '1': '04 0c 04 04 04 04 0e', '2': '0e 11 01 02 04 08 1f',
    '3': '1e 01 01 0e 01 01 1e', '4': '02 06 0a 12 1f 02 02',
    '5': '1f 10 10 1e 01 01 1e', '6': '0e 10 10 1e 11 11 0e',
    '7': '1f 01 02 04 08 08 08', '8': '0e 11 11 0e 11 11 0e',
    '9': '0e 11 11 0f 01 01 0e', '.': '00 00 00 00 00 0c 0c',
    ':': '00 0c 0c 00 0c 0c 00', '-': '00 00 00 1f 00 00 00',
    '/': '01 02 02 04 08 08 10', '%': '19 19 02 04 08 13 13',
    '+': '00 04 04 1f 04 04 00', '!': '04 04 04 04 04 00 04',
    '?': '0e 11 01 02 04 00 04', '&': '0c 12 14 08 15 12 0d',
    '_': '00 00 00 00 00 00 1f', '>': '10 08 04 02 04 08 10',
}
FONT = {char: tuple(int(row, 16) for row in rows.split()) for char, rows in FONT.items()}


def rgb565(r, g, b):
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


BG = rgb565(10, 16, 22)
TEXT = rgb565(239, 246, 241)
MUTED = rgb565(150, 165, 174)
LOW = rgb565(32, 68, 93)
HIGH = rgb565(45, 77, 55)
GREEN = rgb565(178, 239, 116)
RED = rgb565(238, 96, 87)
AMBER = rgb565(245, 187, 77)


class Canvas:
    def __init__(self):
        self.pixels = [BG] * (WIDTH * HEIGHT)

    def rect(self, x, y, w, h, color):
        left, right = max(0, x), min(WIDTH, x + w)
        for row in range(max(0, y), min(HEIGHT, y + h)):
            self.pixels[row * WIDTH + left:row * WIDTH + right] = [color] * max(0, right - left)

    def text(self, x, y, value, color=TEXT, scale=1, max_chars=46):
        for i, char in enumerate(str(value).upper()[:max_chars]):
            for row, bits in enumerate(FONT.get(char, FONT['?'])):
                for col in range(5):
                    if bits & (1 << (4 - col)):
                        self.rect(x + (i * 6 + col) * scale, y + row * scale, scale, scale, color)

    def centered(self, x, y, w, value, color=TEXT, scale=1):
        self.text(x + (w - (len(value) * 6 - 1) * scale) // 2, y, value, color, scale)

    def framebuffer(self):
        # Counterclockwise rotation keeps the home screen upright on the router.
        rotated = [self.pixels[y * WIDTH + x] for x in range(WIDTH - 1, -1, -1) for y in range(HEIGHT)]
        return struct.pack('<' + 'H' * len(rotated), *rotated)

    def ppm(self):
        data = bytearray()
        for color in self.pixels:
            data.extend((((color >> 11) & 31) * 255 // 31,
                         ((color >> 5) & 63) * 255 // 63, (color & 31) * 255 // 31))
        return f'P6\n{WIDTH} {HEIGHT}\n255\n'.encode() + data


def touch_position(raw_x, raw_y):
    # Rotate the GL.iNet input calibration 180 degrees with the display.
    return raw_y, HEIGHT - 1 - raw_x


class HoldControl:
    """A release is required after every activation or cancellation."""
    def __init__(self):
        self.down = False
        self.target = None
        self.started = 0
        self.revision = None

    @staticmethod
    def hit(x, y):
        for key, (bx, by, w, h) in BUTTONS.items():
            if bx <= x < bx + w and by <= y < by + h:
                return key
        return None

    def cancel(self):
        self.target = None

    def pointer(self, down, x, y, now, revision):
        hit = self.hit(x, y)
        if not down:
            self.down = False
            self.cancel()
        elif not self.down:
            self.down = True
            self.target, self.started, self.revision = hit, now, revision
        elif hit != self.target:
            self.cancel()

    def advance(self, now, revision):
        if revision != self.revision:
            self.cancel()
        if self.target and now - self.started >= HOLD_SECONDS:
            target = self.target
            self.cancel()
            return target
        return None

    def progress(self, now):
        return min(1, max(0, (now - self.started) / HOLD_SECONDS)) if self.target else 0


def render(snapshot, hold, now, message=''):
    c = Canvas()
    for x, label, value in ((4, 'PSN', snapshot['psn']), (94, 'MA', snapshot['ma'])):
        color = GREEN if value == 'LIVE' else AMBER if value in ('WAIT', 'DEMO') else RED
        flash = value not in ('LIVE', 'DEMO') and int(now * 2) % 2 == 0
        c.rect(x, 3, 86, 16, color if flash else rgb565(32, 40, 46))
        c.text(x + 5, 7, label + ' ' + value, BG if flash else color)
    count = len(snapshot['blocks'])
    c.text(188, 7, f'{count} BLOCKS' if count != 1 else '1 BLOCK')
    for endpoint, (x, y, w, h) in BUTTONS.items():
        c.rect(x, y, w, h, LOW if endpoint == 'bottom' else HIGH)
        label = 'UPDATE LOW' if endpoint == 'bottom' else 'UPDATE HIGH'
        c.centered(x, y + 5, w, label, TEXT, 2)
        active = hold.target == endpoint
        hint = f'HOLD {max(0, HOLD_SECONDS - (now - hold.started)):.1f}S' if active else 'HOLD 4 SECONDS'
        c.centered(x, y + 23, w, hint, GREEN if active else MUTED)
        c.rect(x + 3, y + h - 4, int((w - 6) * hold.progress(now)) if active else 0, 3, GREEN)
    if not message:
        message = snapshot['reason']
        if not message and count:
            index = int(now / 3) % count
            row = snapshot['blocks'][index]
            value = '--' if row['value'] is None else f"{row['value']:.2f}"
            message = f"{index+1}/{count} {row['name']} {value}"
    c.centered(4, 66, 276, str(message)[:46], AMBER if snapshot['reason'] else MUTED)
    return c


class SlateScreen:
    def __init__(self, engine):
        self.engine = engine
        self.closed = threading.Event()
        self.thread = threading.Thread(target=self.run, name='Slate touchscreen', daemon=True)

    def start(self):
        self.thread.start()

    def close(self):
        self.closed.set()
        self.thread.join(timeout=6)

    def run(self):
        import fcntl
        fb = touch = None
        stock_running = False
        try:
            sysfs = Path('/sys/class/graphics/fb0')
            expected = {'name': 'fb_st7789p3', 'virtual_size': '76,284', 'stride': '152', 'bits_per_pixel': '16'}
            if any((sysfs / name).read_text().strip() != value for name, value in expected.items()):
                raise ValueError('Unsupported Slate display geometry')
            # Open devices and check format before taking the stock display over.
            fb = os.open('/dev/fb0', os.O_RDWR)
            info = bytearray(160)
            fcntl.ioctl(fb, 0x4600, info)
            if struct.unpack_from('6I', info) != (76, 284, 76, 284, 0, 0):
                raise ValueError('Unexpected framebuffer offsets')
            if struct.unpack_from('9I', info, 32) != (11, 5, 0, 5, 6, 0, 0, 5, 0):
                raise ValueError('Expected RGB565 framebuffer')
            touch = os.open('/dev/input/event0', os.O_RDONLY | os.O_NONBLOCK)
            stock_running = (subprocess.run(['/etc/init.d/gl_screen', 'status'], capture_output=True).returncode == 0
                             or subprocess.run(['/etc/init.d/gl_screen', 'enabled'], capture_output=True).returncode == 0)
            subprocess.run(['/etc/init.d/gl_screen', 'stop'], check=True, capture_output=True, timeout=5)
            fcntl.ioctl(touch, 0x40044590, 1)  # EVIOCGRAB; no other UI receives our touches.
            (sysfs / 'blank').write_text('0\n')
            Path('/sys/class/backlight/soc:backlight/brightness').write_text('10\n')
            self.loop(fb, touch, fcntl)
        except Exception as exc:
            print('Slate screen unavailable: ' + str(exc), flush=True)
        finally:
            if touch is not None:
                os.close(touch)
            if fb is not None:
                os.close(fb)
            if stock_running:
                subprocess.run(['/etc/init.d/gl_screen', 'start'], capture_output=True, timeout=5)

    def loop(self, fb, touch, fcntl):
        event = struct.Struct('llHHi')
        hold = HoldControl()
        # The driver's advertised ABS max of 240 is not the panel size (76x284).
        # touch_position applies the same upright orientation as the framebuffer.
        raw_x = struct.unpack('6i', fcntl.ioctl(touch, 0x80184540, bytes(24)))[0]
        raw_y = struct.unpack('6i', fcntl.ioctl(touch, 0x80184541, bytes(24)))[0]
        def pressed():
            keys = fcntl.ioctl(touch, 0x80604518, bytes(96))
            return bool(keys[330 // 8] & (1 << (330 % 8)))
        down = pressed()
        hold.down = down  # A finger already on the screen cannot start a hold.
        data = b''
        message, message_until, last_draw = '', 0, 0
        last_frame = None
        while not self.closed.is_set():
            ready, _, _ = select.select([touch], [], [], 0.02)
            snapshot = self.engine.screen_snapshot()
            if ready:
                while True:
                    try:
                        chunk = os.read(touch, event.size * 64)
                    except BlockingIOError:
                        break
                    if not chunk:
                        raise OSError('Touch input disconnected')
                    data += chunk
                while len(data) >= event.size:
                    _, _, kind, code, value = event.unpack(data[:event.size])
                    data = data[event.size:]
                    if kind == 3 and code == 0:
                        raw_x = value
                    elif kind == 3 and code == 1:
                        raw_y = value
                    elif kind == 1 and code == 330:
                        down = bool(value)
                    elif kind == 0 and code == 3:  # SYN_DROPPED
                        hold.cancel(); hold.down = True
                    elif kind == 0 and code == 0:
                        hold.pointer(down, *touch_position(raw_x, raw_y), time.monotonic(), snapshot['revision'])
            # Also inspect the kernel's current contact state before any commit.
            if not pressed():
                hold.pointer(False, 0, 0, time.monotonic(), snapshot['revision'])
            now = time.monotonic()
            if snapshot['armed']:
                hold.cancel()
            endpoint = hold.advance(now, snapshot['revision'])
            if endpoint:
                try:
                    with self.engine.operation_lock:
                        self.engine.capture_screen(endpoint, hold.revision)
                    message = ('LOW' if endpoint == 'bottom' else 'HIGH') + f" SAVED - {len(snapshot['blocks'])} BLOCKS"
                except (ValueError, OSError) as exc:
                    message = str(exc)
                message_until = time.monotonic() + 4
                snapshot = self.engine.screen_snapshot()
            if now - last_draw >= 0.1:
                frame = render(snapshot, hold, now, message if now < message_until else '').framebuffer()
                if frame != last_frame:
                    os.lseek(fb, 0, os.SEEK_SET)
                    view = memoryview(frame)
                    while view:
                        written = os.write(fb, view)
                        if written == 0:
                            raise OSError('Framebuffer write stopped')
                        view = view[written:]
                    last_frame = frame
                last_draw = now
