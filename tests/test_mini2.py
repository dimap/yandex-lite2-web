"""Mini 2 protocol and worker checks; fixtures never address real hardware."""
import os
import io
import json
import shlex
import shutil
import signal
import subprocess
import tempfile
import tarfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import profiles
import multi
from test_profiles import fresh_server


class Frames(unittest.TestCase):
    def test_existing_frame_maps_digits_and_separator_without_renumbering_other_models(self):
        values = [0] * 36
        values[0], values[8], values[32], values[33], values[34], values[35] = 10, 20, 30, 40, 50, 60
        native = profiles.mini2_segments_from_legacy(values)
        self.assertEqual({key: native[key] for key in ('A1', 'A2', 'DEGREE', 'PLUS1', 'PLUS2', 'MINUS')},
                         dict(A1=10, A2=20, DEGREE=30, PLUS1=40, PLUS2=50, MINUS=60))
        self.assertEqual(profiles.mini2_legacy_from_segments(native), values)

    def test_nonexistent_decimal_points_are_not_sent_to_sysfs(self):
        for index in (7, 15, 23, 31):
            frame = [0] * 36
            frame[index] = 1
            with self.assertRaisesRegex(ValueError, 'точек'):
                profiles.mini2_segments_from_legacy(frame)

    def test_named_segments_include_the_two_sysfs_only_markers(self):
        values = profiles.mini2_validate_segments(dict(PLUS3=5, PLUS4=9))
        self.assertEqual(values['PLUS3'], 5)
        self.assertEqual(values['PLUS4'], 9)
        self.assertEqual(len(values), 34)

    def test_invalid_values_and_names_are_rejected(self):
        for values in ({'A0': 1}, {'H1': 1}, {'../../brightness': 0}, {'A1': -1}, {'A1': 256},
                       {'A1': 1.2}, {'A1': float('inf')}, {'A1': float('nan')}, {'A1': True}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                profiles.mini2_validate_segments(values)

    def test_binary_clock_frames_have_32_channels_and_big_endian_duration(self):
        channels = [0] * 32
        channels[0], channels[28], channels[29], channels[30], channels[31] = 11, 22, 33, 44, 55
        blob = bytes([1] + channels) + (500).to_bytes(2, 'big')
        frame = profiles.mini2_decode_clock('example.binscled', blob)[0]
        self.assertEqual(frame['ms'], 500)
        self.assertEqual(frame['flag'], 1)
        self.assertEqual(frame['segments']['A1'], 11)
        self.assertEqual([frame['segments'][n] for n in ('PLUS1', 'PLUS2', 'MINUS', 'DEGREE')], [22, 33, 44, 55])
        self.assertEqual(len(frame['v']), 36)
        for invalid in (b'', blob[:-1], bytes([2]) + blob[1:], blob[:-2] + b'\0\0'):
            with self.assertRaises(ValueError):
                profiles.mini2_decode_clock('example.binscled', invalid)

    def test_native_ring_dump_preserves_all_six_zones(self):
        parsed = profiles.mini2_decode_ring_dump('loop\nff000000 00ff0000 0000ff00 11223300 44556600 77889900 20\n')
        self.assertTrue(parsed['loop'])
        self.assertEqual(parsed['loop_at'], 0)
        self.assertEqual(parsed['frames'][0]['c'], [[255, 0, 0], [0, 255, 0], [0, 0, 255], [17, 34, 51], [68, 85, 102], [119, 136, 153]])

    def test_native_ring_dump_keeps_intro_and_never_returns_firmware_logs(self):
        first = '01020300 ' * 6 + '100\n'
        second = '04050600 ' * 6 + '17\n'
        parsed = profiles.mini2_decode_ring_dump(
            '[2000-01-01 00:00:00.000] [info] private device metadata\n' + first + 'loop\n' + second)
        self.assertEqual(parsed, dict(frames=[dict(c=[[1, 2, 3]] * 6, ms=100),
                                             dict(c=[[4, 5, 6]] * 6, ms=17)], loop=True, loop_at=1))
        self.assertEqual(profiles.mini2_decode_ring_dump(first)['loop'], False)

    def test_bad_native_ring_dump_is_rejected_before_playback(self):
        frame = '01020300 ' * 6 + '20\n'
        invalid = ['', 'ff000000 00ff0000 0000ff00 20\n', frame.replace('01020300', '010203ff'),
                   frame.replace('20\n', '0\n'), frame.replace('20\n', '65536\n'),
                   frame.replace('20\n', '-1\n'), frame + 'loop\n', 'loop\nloop\n' + frame,
                   frame * 4097, frame + 'unexpected output\n']
        for dump in invalid:
            with self.subTest(prefix=dump[:50]), self.assertRaises(ValueError):
                profiles.mini2_decode_ring_dump(dump)

    def test_compressed_patterns_reuse_the_existing_server_decoder(self):
        raw = bytes([1] + [0] * 32) + (20).to_bytes(2, 'big')
        decoder = Mock(return_value=raw)
        self.assertEqual(profiles.mini2_decode_clock('example.binscled.zst', b'compressed', decoder)[0]['ms'], 20)
        decoder.assert_called_once_with(b'compressed')


class Volume(unittest.TestCase):
    def setUp(self):
        self.s = fresh_server()
        profiles.install(self.s, 'mini2')
        self.adb = Mock()
        self.panel = self.s.Panel(self.adb)

    def test_scale_matches_observed_quasar_levels_and_preserves_the_full_slider(self):
        observed = {10: 26, 15: 38, 20: 51, 25: 64, 30: 77, 35: 89, 40: 102, 45: 115}
        for level, raw in observed.items():
            with self.subTest(level=level):
                self.assertEqual(self.s.level_to_alsa(level), raw)
                self.assertEqual(self.s.alsa_to_level(raw), level)
        for level in range(0, 101, 5):
            self.assertEqual(self.s.alsa_to_level(self.s.level_to_alsa(level)), level)
        self.assertEqual(self.s.level_to_alsa(-10), 0)
        self.assertEqual(self.s.level_to_alsa(110), 255)
        self.assertEqual(self.s.alsa_to_level(None), 0)

    def test_overview_reads_the_same_percent_as_quasar(self):
        self.adb.shell.return_value = ('vol_master=115\nvol_bt=\n', '', 0)
        self.panel.version = Mock(return_value={})
        state = self.panel.status()
        self.assertEqual(state['vol_master'], '115')
        self.assertEqual(state['vol_level'], 45)

    def test_fallback_writes_the_profile_scale_but_connected_glagol_still_owns_volume(self):
        self.panel.glagol = SimpleNamespace(ws=None, send=Mock())
        self.panel.set_volume({'level': 30})
        self.adb.shell.assert_called_once_with("amixer cset name='Master Playback Volume' 77,77 >/dev/null")
        self.adb.reset_mock()
        self.panel.glagol.ws = object()
        self.assertEqual(self.panel.set_volume({'level': 45}), {'ok': True, 'via': 'glagol'})
        self.panel.glagol.send.assert_called_once_with({'command': 'setVolume', 'volume': .45})
        self.adb.shell.assert_not_called()

    def test_other_profiles_keep_their_original_volume_functions(self):
        for model in ('lite2', 'station2', 'mini3pro', 'mini3'):
            with self.subTest(model=model):
                server = fresh_server()
                previous = server.level_to_alsa, server.alsa_to_level
                profiles.install(server, model)
                self.assertEqual((server.level_to_alsa, server.alsa_to_level), previous)
                self.assertEqual(server.alsa_to_level(115), 35)


class Safety(unittest.TestCase):
    def setUp(self):
        self.s = fresh_server()
        profiles.install(self.s, 'mini2')
        self.adb = Mock()
        self.panel = self.s.Panel(self.adb)

    def test_bad_request_never_touches_adb_or_stops_an_existing_effect(self):
        for operation in (lambda: self.panel.clock({'segments': {'A0': 5}}),
                          lambda: self.panel.ring({'red': 256}),
                          lambda: self.panel.display_set({'mode': 'timer', 'seconds': 0}),
                          lambda: self.panel.play_clock([{'v': [0]}], 5)):
            with self.assertRaises((ValueError, self.s.AdbError)):
                operation()
        self.assertEqual(self.adb.mock_calls, [])

    def test_device_identity_is_checked_before_any_mutation(self):
        with patch.object(self.s, 'detect_model', return_value=('lite2', 'plum')):
            with self.assertRaisesRegex(self.s.AdbError, 'подтверждённой'):
                self.panel.clock({'segments': {'A1': 20}})
        self.assertEqual(self.adb.mock_calls, [])

    def test_missing_led_refuses_push_and_start(self):
        self.adb.shell.return_value = ('', '', 0)
        with patch.object(self.s, 'detect_model', return_value=('mini2', 'station-mini2')):
            with self.assertRaisesRegex(self.s.AdbError, 'светодиоды'):
                self.panel.ring({'red': 10})
        self.adb.push.assert_not_called()
        self.assertEqual(self.adb.shell.call_count, 1)

    def test_native_ring_dump_checks_device_before_running_the_firmware_utility(self):
        with patch.object(self.s, 'detect_model', return_value=('lite2', 'plum')):
            with self.assertRaisesRegex(self.s.AdbError, 'подтверждённой'):
                self.panel.ring_frames('progress.led')
        self.assertEqual(self.adb.mock_calls, [])

    def test_edited_and_cleared_legacy_frames_override_stale_native_segments(self):
        controller = Mock()
        with patch.object(profiles, 'Mini2Controller', return_value=controller):
            for level in (80, 0):
                with self.subTest(level=level):
                    values = [0] * 36
                    values[0] = level
                    self.panel.play_clock([dict(v=values, segments={'A1': 233, 'PLUS3': 255},
                                                ms=200, flag=1)], 5)
                    args, kwargs = controller.start.call_args
                    self.assertEqual(args[0], 'clock')
                    native = args[1][0]
                    self.assertEqual(native['values']['mini2:white:clockA1'], level)
                    self.assertEqual(sum(native['values'].values()), level)
                    self.assertEqual(native['ms'], 200)
                    self.assertTrue(kwargs['repeat'])
                    self.assertEqual(kwargs['loop_at'], 0)

    def test_named_only_clock_frames_still_support_all_34_outputs(self):
        controller = Mock()
        with patch.object(profiles, 'Mini2Controller', return_value=controller):
            values = dict.fromkeys(profiles.MINI2_CLOCK_SEGMENTS, 80)
            self.panel.play_clock([dict(segments=values, ms=200)], 5)
        native = controller.start.call_args[0][1][0]['values']
        self.assertEqual(set(native), set(profiles.MINI2_CLOCK_NAMES))
        self.assertEqual(set(native.values()), {80})
        self.assertFalse(controller.start.call_args[1]['repeat'])

    def test_editor_only_status_is_specific_to_mini2_without_touching_adb(self):
        self.assertTrue(self.panel.boot_status()['editor_only'])
        self.assertEqual(self.adb.mock_calls, [])
        for model in ('station2', 'mini3pro', 'mini3'):
            with self.subTest(model=model):
                server = fresh_server()
                profiles.install(server, model)
                adb = Mock()
                self.assertFalse(server.Panel(adb).boot_status().get('editor_only', False))
                self.assertEqual(adb.mock_calls, [])

    def test_existing_yam_reports_external_startup_without_enabling_writes(self):
        self.adb.serial = '192.0.2.10:5555'
        self.adb.shell.side_effect = [('installed\nrunning\nlistening\n', '', 0), ('external\n', '', 0)]
        status = self.panel.yam_status()
        self.assertTrue(status['installed'])
        self.assertTrue(status['running'])
        self.assertTrue(status['autostart'])
        self.assertTrue(status['autostart_external'])
        self.assertTrue(status['read_only'])
        self.assertIn('отдельной проверки', status['read_only_reason'])
        self.adb.push.assert_not_called()
        self.assertEqual(self.adb.shell.call_count, 2)

    def test_missing_yam_keeps_installation_unverified_and_autostart_off(self):
        self.adb.serial = '192.0.2.10:5555'
        self.adb.shell.side_effect = [('', '', 0), ('external\n', '', 0)]
        status = self.panel.yam_status()
        self.assertFalse(status['installed'])
        self.assertFalse(status['autostart'])
        self.assertTrue(status['read_only'])

    def test_ring_playback_reuses_native_decoder_and_preserves_loop_entry(self):
        frame = '01020300 ' * 6 + '100\n'
        dump = frame + 'loop\n' + frame.replace('100', '20')
        def shell(command, **kwargs):
            if command.startswith('find '):
                return self.s.PATTERNS['ring'] + '/progress.led\n', '', 0
            self.assertIn('/led_util ', command)
            self.assertIn('progress.led --dump-raw', command)
            return dump, '', 0
        self.adb.shell.side_effect = shell
        controller = Mock()
        with patch.object(self.s, 'detect_model', return_value=('mini2', 'station-mini2')), \
                patch.object(profiles, 'Mini2Controller', return_value=controller):
            self.panel.play_pattern('ring', 'progress.led', 5)
        args, kwargs = controller.start.call_args
        self.assertEqual(args[0], 'ring')
        self.assertEqual(len(args[1]), 2)
        self.assertEqual(kwargs['loop_at'], 1)
        self.assertTrue(kwargs['repeat'])
        self.adb.push.assert_not_called()
        self.adb.read_bytes.assert_not_called()

    def test_mini2_timer_uses_the_stock_opus_file(self):
        self.adb.shell.return_value = ('', '', 0)
        self.adb.read_bytes.return_value = b'stock sound'
        self.panel.sounds = Mock(return_value=['common/timer.opus'])
        self.panel.ffmpeg = 'ffmpeg'
        with tempfile.TemporaryDirectory() as directory:
            def convert(command, **kwargs):
                Path(command[-1]).write_bytes(b'RIFFfixture')
                return SimpleNamespace(returncode=0)
            with patch.object(self.s.subprocess, 'run', side_effect=convert):
                self.panel._prepare_timer_sound(Path(directory))
        self.adb.read_bytes.assert_called_once_with('/system/vendor/quasar/sounds/common/timer.opus')
        self.assertEqual(self.adb.push.call_args.args[1], '/tmp/webpanel_mini2/timer.wav')

    def test_unrelated_yam_layout_and_boot_methods_are_not_adopted(self):
        self.assertEqual(self.s.YAM_DIR, '/data/quazar/yam')
        with self.assertRaises(self.s.AdbError):
            self.panel._ensure_hook()
        self.assertFalse(self.panel.boot_status()['supported'])
        self.assertEqual(self.adb.mock_calls, [])

    def test_unsupported_http_operations_are_rejected_before_the_original_handler(self):
        s = fresh_server()
        original = s.Handler.do_POST = Mock()
        profiles.install(s, 'mini2')
        for path in ('/api/clock/boot', '/api/boot/sound', '/api/alarms', '/api/yam/start',
                     '/api/yam/install-daemon', '/api/privacy', '/api/footprint/reset'):
            request = SimpleNamespace(path=path, _guard=lambda _: False, _send=Mock())
            s.Handler.do_POST(request)
            self.assertEqual(request._send.call_args.args[0], 400)
        original.assert_not_called()

    @staticmethod
    def archive(manifest, files=None):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w:gz') as tar:
            for name, data in {'manifest.json': json.dumps(manifest).encode(), **(files or {})}.items():
                info = tarfile.TarInfo(name); info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        return stream.getvalue()

    def test_browser_backup_roundtrip_never_resets_or_writes_device_files(self):
        self.panel.footprint = Mock(return_value={'items': []})
        self.panel.footprint_reset = Mock(side_effect=AssertionError('must not reset'))
        data = self.archive({'format': 1, 'ui': {'ui.theme': 'dark', 'ov.layout': '{}', 'token': 'secret'}})
        result = self.panel.backup_restore(data)
        self.assertEqual(result['ui'], {'ui.theme': 'dark', 'ov.layout': '{}'})
        self.assertEqual(result['restored'], ['настройки браузера'])
        self.panel.footprint_reset.assert_not_called()
        self.assertEqual(self.adb.mock_calls, [])

    def test_browser_backup_export_does_not_collect_other_models_device_files(self):
        self.panel.version = Mock(return_value={'quasar_version': 'fixture'})
        self.panel.footprint = Mock(return_value={'items': []})
        name, body = self.panel.backup({'ui.theme': 'auto', 'token': 'excluded'})
        self.assertTrue(name.endswith('.tar.gz'))
        with tarfile.open(fileobj=io.BytesIO(body), mode='r:gz') as tar:
            self.assertEqual(tar.getnames(), ['manifest.json'])
            self.assertEqual(json.load(tar.extractfile('manifest.json'))['ui'], {'ui.theme': 'auto'})
        self.assertEqual(self.adb.mock_calls, [])

    def test_other_models_device_backup_and_invalid_archive_rejected_before_adb(self):
        for body in (b'not an archive', self.archive([]), self.archive({'format': 2}),
                     self.archive({'format': 1, 'ui': ['bad']}),
                     self.archive({'format': 1}, {'webpanel/boot_sound.wav': b'other model'}),
                     self.archive({'format': 1}, {'webpanel/yamd_autostart': b'1'})):
            with self.subTest(body=body[:12]), self.assertRaises(self.s.AdbError):
                self.panel.backup_restore(body)
        self.assertEqual(self.adb.mock_calls, [])

    def test_lite2_backup_still_resets_and_uses_its_original_restore(self):
        s = fresh_server(); profiles.install(s, 'lite2')
        panel = s.Panel(Mock()); panel.footprint_reset = Mock(); panel.footprint = Mock(return_value={})
        panel.boot_status = Mock(return_value={})
        result = panel.backup_restore(self.archive({'format': 1, 'ui': {'ui.theme': 'dark'}}))
        panel.footprint_reset.assert_called_once_with()
        self.assertEqual(result['ui'], {'ui.theme': 'dark'})

    def test_detector_uses_device_tree_and_does_not_accept_generic_axg(self):
        for text in ('yandex,station-mini2\0amlogic, axg\0', 'Yandex Station Mini 2\0'):
            self.adb.shell.return_value = (text, '', 0)
            self.assertEqual(self.s.detect_model(self.adb), ('mini2', 'station-mini2'))
        for text in ('amlogic, axg\0', 'Yandex Station Mini 20', 'yandex,station-mini20'):
            self.adb.shell.return_value = (text, '', 0)
            self.assertEqual(self.s.detect_model(self.adb), (None, None))



@unittest.skipIf(os.name == 'nt', 'POSIX worker lifecycle')
class Workers(unittest.TestCase):
    def test_setup_failure_before_first_frame_restores_detached_triggers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); leds = root / 'leds'; state = root / 'state'; state.mkdir()
            names = profiles.MINI2_RING_NAMES
            rows = {n: dict(max=255, value=5, trigger='timer') for n in profiles.MINI2_CLOCK_NAMES + names}
            for name in names:
                node = leds / name; node.mkdir(parents=True)
                (node / 'brightness').write_text('5\n'); (node / 'trigger').write_text('[timer] none\n')
            controller = profiles.Mini2Controller(fresh_server(), None, str(leds), str(state))
            script = controller.script('ring', names, rows, [dict(values={n: 12 for n in names}, ms=20)],
                                       'once', 0, False, None)
            first = f'echo none > "$D/{names[0]}/trigger"'
            script = script.replace(first, first + '\nexit 1', 1)
            result = subprocess.run(['sh', '-c', script], capture_output=True, timeout=3)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual((leds / names[0] / 'trigger').read_text().strip(), 'timer')
            self.assertTrue(all((leds / n / 'brightness').read_text().strip() == '5' for n in names))

    def exercise(self, expire=False, second_stop=False, mid_frame=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            leds, state, binaries = root / 'leds', root / 'state', root / 'bin'
            for path in (leds, state, binaries):
                path.mkdir()
            rows = {name: dict(value=5, max=255, trigger='timer') for name in profiles.MINI2_CLOCK_NAMES + profiles.MINI2_RING_NAMES}
            for name in rows:
                node = leds / name
                node.mkdir()
                (node / 'brightness').write_text('5\n')
                (node / 'trigger').write_text('[timer] none\n')
            # Keep waits short without relying on a host BusyBox installation.
            (binaries / 'usleep').write_text('#!/bin/sh\nsleep 0.02\n')
            (binaries / 'usleep').chmod(0o755)
            if second_stop:
                # Pause real restoration at its first ledger read, then send another stop signal.
                marker = shlex.quote(str(state / 'restoring'))
                release = shlex.quote(str(state / 'continue'))
                sed = shlex.quote(shutil.which('sed'))
                (binaries / 'sed').write_text(f'#!/bin/sh\n: > {marker}\n'
                                              f'while [ ! -f {release} ]; do sleep 0.01; done\n'
                                              f'exec {sed} "$@"\n')
                (binaries / 'sed').chmod(0o755)
            if mid_frame:
                # Stop after the next frame's ledger is written, before its LED writes begin.
                marker = shlex.quote(str(state / 'frame-started'))
                release = shlex.quote(str(state / 'continue-frame'))
                counter = shlex.quote(str(state / 'frame-count'))
                cat = shlex.quote(shutil.which('cat'))
                (binaries / 'cat').write_text(f'#!/bin/sh\nif [ "$#" -gt 0 ]; then exec {cat} "$@"; fi\n'
                                              f'{cat}\nn=0; [ ! -f {counter} ] || read -r n < {counter}\n'
                                              f'n=$((n+1)); echo "$n" > {counter}\n'
                                              f'if [ "$n" -eq 2 ]; then : > {marker}; '
                                              f'while [ ! -f {release} ]; do sleep 0.01; done; fi\n')
                (binaries / 'cat').chmod(0o755)
            s = fresh_server()
            controller = profiles.Mini2Controller(s, None, str(leds), str(state))
            names = profiles.MINI2_RING_NAMES
            frame = dict(values={name: 12 for name in names}, ms=20)
            frames = [frame, dict(values={name: 23 for name in names}, ms=20)] if mid_frame else [frame]
            script = state / 'ring.sh'
            script.write_text(controller.script('ring', names, rows, frames, 'once', 0, False, None))
            (state / 'ring.lease').write_text(str(int(time.time()) + (1 if expire else 30)))
            process = subprocess.Popen(['sh', str(script)], env={**os.environ, 'PATH': str(binaries) + os.pathsep + os.environ['PATH']},
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            (state / 'ring.pid').write_text(str(process.pid))
            try:
                deadline = time.monotonic() + 3
                # An external writer takes over after the entire initial frame is ready.
                while not (state / 'ring.ready').exists():
                    if process.poll() is not None:
                        self.fail(process.communicate()[1].decode())
                    if time.monotonic() > deadline:
                        self.fail('Worker did not apply its frame')
                    time.sleep(.01)
                if not expire:
                    # An external writer owns this channel now; reset must preserve it.
                    if not mid_frame:
                        (leds / 'mini2:blue:ring6/brightness').write_text('23\n')
                    else:
                        deadline = time.monotonic() + 3
                        while not (state / 'frame-started').exists():
                            if process.poll() is not None or time.monotonic() > deadline:
                                self.fail('Worker did not begin the next frame')
                            time.sleep(.01)
                    process.send_signal(signal.SIGTERM)
                    if mid_frame:
                        (state / 'continue-frame').touch()
                    if second_stop:
                        deadline = time.monotonic() + 3
                        while not (state / 'restoring').exists():
                            if process.poll() is not None or time.monotonic() > deadline:
                                self.fail('Worker did not begin restoration')
                            time.sleep(.01)
                        process.send_signal(signal.SIGTERM)
                        (state / 'continue').touch()
                _, errors = process.communicate(timeout=4)
                self.assertEqual(process.returncode, 0, errors.decode())
                self.assertEqual((leds / 'mini2:red:ring1/brightness').read_text().strip(), '5')
                self.assertEqual((leds / 'mini2:red:ring1/trigger').read_text().strip(), 'timer')
                self.assertEqual((leds / 'mini2:blue:ring6/brightness').read_text().strip(), '5' if expire or mid_frame else '23')
                self.assertFalse((state / 'ring.pid').exists())
                self.assertFalse(script.exists())
            finally:
                (state / 'continue').touch()
                (state / 'continue-frame').touch()
                if process.poll() is None:
                    process.kill()
                    process.communicate()

    def test_stop_restores_only_our_values_and_triggers(self):
        self.exercise()

    def test_missing_host_lease_restores_output_and_removes_worker(self):
        self.exercise(expire=True)

    def test_another_stop_during_restore_cannot_interrupt_cleanup(self):
        self.exercise(second_stop=True)

    def test_stop_during_a_frame_finishes_its_writes_before_restoring(self):
        self.exercise(mid_frame=True)


if __name__ == '__main__':
    unittest.main()
