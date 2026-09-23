import copy
import json
from pathlib import Path
import struct
import tempfile
import time
import unittest
from unittest.mock import patch

from axisbridge.engine import Engine, Conflict
from axisbridge.model import default_show, validate_show
from axisbridge.psn import decode, encode_demo
from axisbridge.slate_screen import Canvas, HoldControl, TouchInput, capture_message, render, touch_position, WIDTH, HEIGHT


class HoldTests(unittest.TestCase):
    def test_slate_tracking_releases_with_latched_touch_key(self):
        h = HoldControl(); touch = TouchInput(h, tracking=True)
        def frame(events, now):
            for kind, code, value in events + [(0, 0, 0)]:
                touch.feed(kind, code, value, now, 1)
        frame([(3, 0, 35), (3, 1, 243), (3, 57, 0), (1, 330, 1)], 0)
        self.assertEqual(h.target, 'bottom')
        self.assertIsNone(h.advance(3.999, 1))
        frame([(3, 57, -1)], 3.999)  # Firmware sends no BTN_TOUCH=0.
        self.assertIsNone(h.advance(4, 1))
        frame([(3, 57, 0)], 5)  # Coordinates need not change between contacts.
        self.assertEqual(h.advance(9, 1), 'bottom')
        self.assertIsNone(h.advance(20, 1))
        frame([(3, 57, -1)], 21)
        frame([(3, 1, 93), (3, 57, 0)], 22)
        self.assertEqual(h.advance(26, 1), 'top')

    def test_input_overrun_cancels_until_a_confirmed_release(self):
        h = HoldControl(); touch = TouchInput(h, tracking=True, raw_x=35, raw_y=243)
        for event in ((3, 57, 0), (0, 0, 0)):
            touch.feed(*event, 0, 1)
        touch.feed(0, 3, 0, 3.9, 1)
        touch.feed(3, 57, -1, 3.9, 1)  # Ignore events before the resync boundary.
        touch.feed(0, 0, 0, 3.9, 1)
        touch.feed(3, 57, 0, 4, 1)
        touch.feed(0, 0, 0, 4, 1)
        self.assertIsNone(h.advance(10, 1))
        for event in ((3, 57, -1), (0, 0, 0), (3, 57, 0), (0, 0, 0)):
            touch.feed(*event, 11, 1)
        self.assertEqual(h.advance(15, 1), 'bottom')

    def test_single_touch_key_fallback(self):
        h = HoldControl(); touch = TouchInput(h, tracking=False, raw_x=35, raw_y=243)
        touch.feed(1, 330, 1, 0, 1); touch.feed(0, 0, 0, 0, 1)
        self.assertEqual(h.advance(4, 1), 'bottom')
        touch.feed(1, 330, 0, 5, 1); touch.feed(0, 0, 0, 5, 1)
        self.assertFalse(h.down)

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

    def test_upright_touch_coordinates_match_buttons(self):
        self.assertEqual(touch_position(75, 283), (0, 0))
        self.assertEqual(touch_position(0, 0), (283, 75))
        self.assertEqual(HoldControl.hit(*touch_position(35, 243)), 'bottom')
        self.assertEqual(HoldControl.hit(*touch_position(35, 93)), 'top')


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

    def test_revision_guard_while_live(self):
        before = self.engine.path.read_bytes()
        self.engine.armed = True
        with self.assertRaises(Conflict):
            self.engine.capture_screen('bottom', self.engine.revision - 1)
        self.assertEqual(self.engine.path.read_bytes(), before)

    def test_live_capture_applies_immediately_without_resetting_other_blocks(self):
        self.engine.ma_state = 'ready'
        self.engine.armed = True
        now = time.monotonic()
        self.engine.mark_sent(self.engine.pending_commands(now), now)
        other_sent = copy.deepcopy(self.engine.last_sent['1.3'])
        other_smoothed = self.engine.smoothed['3']
        self.engine.capture_screen('bottom', self.engine.revision)
        self.assertTrue(self.engine.armed)
        self.assertEqual(self.engine.ma_state, 'ready')
        self.assertEqual(self.engine.last_sent['1.3'], other_sent)
        self.assertEqual(self.engine.smoothed['3'], other_smoothed)
        self.assertEqual([c[2] for c in self.engine.pending_commands(time.monotonic())],
                         ['Fader 1.1 At 0.00', 'Fader 1.2 At 0.00'])
        for i in (1, 2):
            self.engine.ingest('127.0.0.1', decode(encode_demo(i, (0, 0, i + 5), 100 + i)))
        self.engine.capture_screen('top', self.engine.revision)
        self.assertTrue(self.engine.armed)
        self.assertEqual([c[2] for c in self.engine.pending_commands(time.monotonic())],
                         ['Fader 1.1 At 100.00', 'Fader 1.2 At 100.00'])

    def test_high_skips_selected_block_at_low_while_output_is_live(self):
        self.check_partial_capture('top', 0, 'Low', '100.00')

    def test_low_skips_selected_block_at_high_while_output_is_live(self):
        self.check_partial_capture('bottom', 10, 'High', '0.00')

    def check_partial_capture(self, endpoint, stationary_value, opposite_label, level):
        self.engine.ingest('127.0.0.1', decode(encode_demo(2, (0, 0, stationary_value), 100)))
        self.engine.ma_state = 'ready'; self.engine.armed = True
        now = time.monotonic()
        self.engine.mark_sent(self.engine.pending_commands(now), now)
        before = copy.deepcopy(self.engine.show)
        sent = copy.deepcopy(self.engine.last_sent)
        smoothed = copy.deepcopy(self.engine.smoothed)
        revision = self.engine.revision
        result = self.engine.capture_screen(endpoint, revision)
        self.assertEqual(result['capture']['updated'], ['1'])
        self.assertEqual(result['capture']['skipped'], [{'id': '2', 'name': 'Pod 2', 'reason': 'still at ' + opposite_label}])
        self.assertEqual(self.engine.revision, revision + 1)
        self.assertTrue(self.engine.armed)
        self.assertEqual(result['show']['blocks'][1:], before['blocks'][1:])
        saved = json.loads(self.engine.path.read_text())
        self.assertEqual(saved['blocks'][0][endpoint], 1)
        self.assertEqual(saved['blocks'][1:], before['blocks'][1:])
        for ident in ('2', '3'):
            self.assertEqual(self.engine.last_sent['1.' + ident], sent['1.' + ident])
            self.assertEqual(self.engine.smoothed[ident], smoothed[ident])
        self.assertEqual([c[2] for c in self.engine.pending_commands(time.monotonic())], ['Fader 1.1 At ' + level])
        label = 'LOW' if endpoint == 'bottom' else 'HIGH'
        self.assertEqual(capture_message(result['capture']), label + ': 1 SAVED / 1 SKIPPED')

    def test_high_all_unchanged_does_not_write_or_reset_output(self):
        self.check_all_unchanged('top', (0, 10), ['still at Low', 'High unchanged'])

    def test_low_all_unchanged_does_not_write_or_reset_output(self):
        self.check_all_unchanged('bottom', (10, 0), ['still at High', 'Low unchanged'])

    def check_all_unchanged(self, endpoint, values, reasons):
        for i, value in enumerate(values, 1):
            self.engine.ingest('127.0.0.1', decode(encode_demo(i, (0, 0, value), 100)))
        self.engine.ma_state = 'ready'; self.engine.armed = True
        now = time.monotonic()
        self.engine.mark_sent(self.engine.pending_commands(now), now)
        before = self.engine.path.read_bytes()
        revision = self.engine.revision
        sent = copy.deepcopy(self.engine.last_sent)
        smoothed = copy.deepcopy(self.engine.smoothed)
        with patch('axisbridge.engine.atomic_json') as write:
            result = self.engine.capture_screen(endpoint, revision)
            write.assert_not_called()
        self.assertEqual(result['capture']['updated'], [])
        self.assertEqual([b['reason'] for b in result['capture']['skipped']], reasons)
        self.assertEqual(self.engine.path.read_bytes(), before)
        self.assertEqual(self.engine.revision, revision)
        self.assertEqual(self.engine.last_sent, sent)
        self.assertEqual(self.engine.smoothed, smoothed)
        self.assertTrue(self.engine.armed)
        label = 'LOW' if endpoint == 'bottom' else 'HIGH'
        self.assertEqual(capture_message(result['capture']), label + ': 0 SAVED / 2 SKIPPED')

    def test_high_still_rejects_stale_disabled_and_conflicting_selection(self):
        before = self.engine.path.read_bytes()
        revision = self.engine.revision
        with self.assertRaises(Conflict):
            self.engine.capture_screen('top', revision - 1)
        self.engine.entities['127.0.0.1/2']['times']['z'] = time.monotonic() - 5
        with self.assertRaisesRegex(ValueError, 'no fresh signal'):
            self.engine.capture_screen('top', revision)
        self.engine.show['blocks'][1]['enabled'] = False
        with self.assertRaisesRegex(ValueError, 'disabled'):
            self.engine.capture_screen('top', revision)
        self.assertEqual(self.engine.path.read_bytes(), before)
        self.assertEqual(self.engine.revision, revision)
        self.assertEqual(self.engine.show['blocks'][0]['top'], 10)

    def test_high_partial_capture_disk_failure_leaves_everything_unchanged(self):
        self.engine.ingest('127.0.0.1', decode(encode_demo(2, (0, 0, 0), 100)))
        self.engine.armed = True
        self.engine.last_sent['1.1'] = {'level': 10, 'at': 1}
        before = copy.deepcopy(self.engine.show)
        revision = self.engine.revision
        with patch('axisbridge.engine.atomic_json', side_effect=OSError('Disk error')):
            with self.assertRaisesRegex(OSError, 'Disk error'):
                self.engine.capture_screen('top', revision)
        self.assertEqual(self.engine.show, before)
        self.assertEqual(json.loads(self.engine.path.read_text()), before)
        self.assertEqual(self.engine.revision, revision)
        self.assertEqual(self.engine.last_sent['1.1'], {'level': 10, 'at': 1})
        self.assertTrue(self.engine.armed)

    def test_selection_can_change_while_live_and_only_updates_flags(self):
        self.engine.armed = True
        self.engine.last_sent['1.1'] = {'level': 10, 'at': 1}
        before = copy.deepcopy(self.engine.show)
        result = self.engine.select_screen_blocks(['3'], self.engine.revision)
        self.assertTrue(self.engine.armed)
        self.assertEqual(self.engine.last_sent['1.1'], {'level': 10, 'at': 1})
        for b in before['blocks']:
            b['on_screen'] = b['id'] == '3'
        self.assertEqual(result['show'], before)
        self.assertEqual(json.loads(self.engine.path.read_text()), before)
        revision = self.engine.revision
        for bad in (None, '3', [3], ['unknown'], ['3', '3']):
            with self.assertRaises(ValueError):
                self.engine.select_screen_blocks(bad, revision)
        with self.assertRaises(Conflict):
            self.engine.select_screen_blocks([], revision - 1)
        self.assertEqual(self.engine.revision, revision)

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
        c.rect(WIDTH - 1, HEIGHT - 1, 1, 1, 0x5678)
        self.assertEqual(struct.unpack_from('<H', c.framebuffer(), (WIDTH - 1) * HEIGHT * 2)[0], 0x1234)
        self.assertEqual(struct.unpack_from('<H', c.framebuffer(), (HEIGHT - 1) * 2)[0], 0x5678)


if __name__ == '__main__':
    unittest.main()
