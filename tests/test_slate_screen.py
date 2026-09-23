import copy
import json
from pathlib import Path
import struct
import tempfile
import time
import unittest

from axisbridge.engine import Engine, Conflict
from axisbridge.model import default_show, validate_show
from axisbridge.psn import decode, encode_demo
from axisbridge.slate_screen import Canvas, HoldControl, render, WIDTH, HEIGHT


class HoldTests(unittest.TestCase):
    def test_four_seconds_and_only_once_per_contact(self):
        h = HoldControl()
        h.pointer(True, 40, 35, 10, 1)
        self.assertIsNone(h.advance(13.999, 1))
        self.assertEqual(h.advance(14, 1), 'bottom')
        h.pointer(True, 40, 35, 15, 1)
        self.assertIsNone(h.advance(20, 1))
        h.pointer(False, 40, 35, 21, 1)
        h.pointer(True, 190, 35, 22, 1)
        self.assertEqual(h.advance(26, 1), 'top')

    def test_early_release_and_slide_off_cancel(self):
        for cancel in ((False, 40, 35), (True, 190, 35), (True, 40, 5)):
            h = HoldControl()
            h.pointer(True, 40, 35, 0, 1)
            h.pointer(*cancel, 3.99, 1)
            self.assertIsNone(h.advance(4, 1))
            if cancel[0]:
                h.pointer(True, 40, 35, 5, 1)
                self.assertIsNone(h.advance(10, 1))

    def test_config_change_cancels_and_requires_release(self):
        h = HoldControl()
        h.pointer(True, 40, 35, 0, 1)
        self.assertIsNone(h.advance(4, 2))
        h.pointer(True, 40, 35, 5, 2)
        self.assertIsNone(h.advance(10, 2))

    def test_no_slide_into_button_activation(self):
        h = HoldControl()
        h.pointer(True, 40, 5, 0, 1)
        h.pointer(True, 40, 35, 1, 1)
        self.assertIsNone(h.advance(5, 1))


class ScreenCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = Engine(self.temp.name)
        show = default_show()
        show['blocks'] = [{'id': str(i), 'name': f'Pod {i}', 'source': '127.0.0.1',
                          'tracker_id': i, 'axis': 'z', 'bottom': 0, 'top': 10,
                          'on_screen': i != 3, 'targets': [f'1.{i}']} for i in (1, 2, 3)]
        self.engine.save(show, self.engine.revision)
        for i in (1, 2, 3):
            self.engine.ingest('127.0.0.1', decode(encode_demo(i, (0, 0, i), i)))

    def tearDown(self):
        self.engine.close()
        self.temp.cleanup()

    def test_only_selected_blocks_capture_and_persist_atomically(self):
        revision = self.engine.revision
        self.engine.capture_screen('bottom', revision)
        self.assertEqual(self.engine.revision, revision + 1)
        saved = json.loads(self.engine.path.read_text())
        self.assertEqual([b['bottom'] for b in saved['blocks']], [1, 2, 0])
        self.assertEqual([b['on_screen'] for b in saved['blocks']], [True, True, False])

    def test_one_stale_axis_prevents_every_update(self):
        self.engine.entities['127.0.0.1/2']['times']['z'] = time.monotonic() - 5
        before = self.engine.path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'no fresh signal'):
            self.engine.capture_screen('bottom', self.engine.revision)
        self.assertEqual(self.engine.path.read_bytes(), before)
        self.assertEqual(self.engine.show['blocks'][0]['bottom'], 0)

    def test_output_revision_and_equal_endpoint_guards(self):
        before = self.engine.path.read_bytes()
        with self.assertRaises(Conflict):
            self.engine.capture_screen('bottom', self.engine.revision - 1)
        self.engine.armed = True
        with self.assertRaises(Conflict):
            self.engine.capture_screen('bottom', self.engine.revision)
        self.engine.armed = False
        self.engine.show['blocks'][1]['top'] = 2
        with self.assertRaisesRegex(ValueError, 'must differ'):
            self.engine.capture_screen('bottom', self.engine.revision)
        self.assertEqual(self.engine.path.read_bytes(), before)

    def test_disabled_and_empty_selection_rejected(self):
        self.engine.show['blocks'][1]['enabled'] = False
        with self.assertRaisesRegex(ValueError, 'disabled'):
            self.engine.capture_screen('bottom', self.engine.revision)
        for b in self.engine.show['blocks']:
            b['on_screen'] = False
        with self.assertRaisesRegex(ValueError, 'Select blocks'):
            self.engine.capture_screen('bottom', self.engine.revision)

    def test_old_shows_do_not_silently_select_blocks(self):
        show = copy.deepcopy(self.engine.show)
        for b in show['blocks']:
            del b['on_screen']
        self.assertFalse(any(b['on_screen'] for b in validate_show(show)['blocks']))
        show['blocks'][0]['on_screen'] = 'yes'
        with self.assertRaises(ValueError):
            validate_show(show)

    def test_render_dimensions_and_rotation(self):
        c = render(self.engine.screen_snapshot(), HoldControl(), 0)
        self.assertEqual(len(c.framebuffer()), WIDTH * HEIGHT * 2)
        c = Canvas()
        c.rect(0, 0, 1, 1, 0x1234)
        self.assertEqual(struct.unpack_from('<H', c.framebuffer(), (HEIGHT - 1) * 2)[0], 0x1234)


if __name__ == '__main__':
    unittest.main()
