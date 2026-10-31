"""Executes generated shell against temporary sysfs, never a physical station."""
import importlib.util
import os
from pathlib import Path
import re
import signal
import subprocess
import shlex
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import midi
import profiles
from test_profiles import fresh_server


def probe_fixture():
    rows = ['uid\t0', 'compatible\tyandex,cucumber-dvt2 yandex,cucumber amlogic,a5',
            'model\tYandex Cucumber DVT2', 'firmware\tfixture-not-a-real-measurement',
            'kernel\t5.4.180', 'arch\taarch64']
    for component in midi.COMPONENTS:
        rows += [f'led\t{n}\t17\t255\tnone [ledfb-{component}] timer' for n in midi.names_for(component)]
    rows += ['led\tcucumber:white:clock-a1\t10\t170\tnone [timer]',
             'fb\tfb0\tring\t24,1\t24', 'fb\tfb1\tlogo\t6,1\t24']
    return '\n'.join(rows) + '\n'


class MidiProbe(unittest.TestCase):
    def test_reviewed_trials_require_verified_firmware_root_and_all_real_nodes(self):
        hw = midi.parse_probe(probe_fixture())
        variants = [None, {}, {**hw, 'root': None}, {**hw, 'uid': '1000'},
                    {**hw, 'firmware': ''}, {**hw, 'firmware': '?'},
                    {**hw, 'compatible': 'yandex,yandexmidi'}, {**hw, 'leds': {}}]
        for value in variants:
            with self.subTest(value=value is None), self.assertRaises(ValueError):
                midi.reviewed_trial_scripts(value)
        scripts = midi.reviewed_trial_scripts(hw)
        self.assertEqual(len(scripts), 9)
        for name, source in scripts.items():
            self.assertIn('fixture-not-a-real-measurement', source)
            self.assertIn('test "$(cat /system/vendor/quasar/version_full)"', source)
            self.assertEqual(subprocess.run(['sh', '-n'], input=source, text=True, capture_output=True).returncode, 0)

    def test_shell_guard_requires_root_and_exact_cucumber_compatible(self):
        with tempfile.TemporaryDirectory() as directory:
            compatible = Path(directory) / 'compatible'
            for value, uid, expected in [('yandex,cucumber\0amlogic,a5\0', 0, 0),
                                          ('yandex,cucumber-dvt2\0', 0, 0),
                                          ('yandex,cucumberish\0', 0, 1),
                                          ('yandex,yandexmidi\0', 0, 1),
                                          ('yandex,cucumber\0', 1000, 1)]:
                compatible.write_bytes(value.encode())
                script = f'id() {{ echo {uid}; }}\n' + midi.IDENTITY_CHECK.replace('/proc/device-tree/compatible', shlex.quote(str(compatible)))
                done = subprocess.run(['sh'], input=script, text=True, capture_output=True, timeout=5)
                self.assertEqual(done.returncode, expected, done.stderr)

    def test_inventory_separates_logo_ring_and_unmapped_clock(self):
        hw = midi.parse_probe(probe_fixture())
        self.assertTrue(hw['root'])
        self.assertEqual(hw['components'], {'ring': True, 'logo': True})
        self.assertEqual(hw['clock_segments'], ['cucumber:white:clock-a1'])
        self.assertFalse(hw['clock_mapping_verified'])
        self.assertEqual(hw['framebuffers']['fb0']['size'], '24,1')

    def test_rejects_foreign_model_duplicate_and_corrupt_metadata(self):
        for output in (probe_fixture().replace('yandex,cucumber-dvt2 yandex,cucumber', 'yandex,yandexmidi'),
                       probe_fixture() + 'led\tcucumber:red:ring0\t0\t255\t[none]\n',
                       probe_fixture().replace('\t17\t255\t', '\t256\t255\t', 1),
                       probe_fixture().replace('cucumber:red:ring0', 'cucumber:red:../../other', 1)):
            with self.subTest(output=output[:70]), self.assertRaises(ValueError):
                midi.parse_probe(output)

    def test_missing_node_or_trigger_is_not_a_usable_component(self):
        for output in (probe_fixture().replace('led\tcucumber:red:ring0\t17\t255\tnone [ledfb-ring] timer\n', ''),
                       probe_fixture().replace('[ledfb-ring]', 'ledfb-ring')):
            hw = midi.parse_probe(output)
            self.assertFalse(hw['components']['ring'])
            self.assertTrue(hw['components']['logo'])

    def test_live_state_preserves_all_zones_without_fabricated_clock_values(self):
        s = fresh_server()
        profiles.install(s, 'midi')
        adb = Mock()
        adb.shell.return_value = (probe_fixture(), '', 0)
        p = s.Panel(adb)
        state = p.clock_state()
        self.assertIsNone(state['v'])
        self.assertEqual(len(state['ring_all']), 24)
        self.assertEqual(len(state['logo']), 6)
        self.assertEqual(len(state['ring']), 3)
        self.assertNotIn('/dev/fb', adb.shell.call_args.args[0])

    def test_both_rgb_components_are_gated_before_adb(self):
        s = fresh_server()
        profiles.install(s, 'midi')
        p = s.Panel(Mock())
        for name, args in [('ring', ({'red': 8},)), ('ring_reset', ()),
                           ('logo', ({'red': 8},)), ('logo_reset', ())]:
            with self.subTest(method=name), self.assertRaises(s.AdbError):
                getattr(p, name)(*args)
        p.adb.shell.assert_not_called()

    def test_enabled_adapter_is_per_worker_and_uses_only_cucumber(self):
        s = fresh_server()
        with patch.dict(profiles.PROFILES['midi'], light=True, logo=True):
            profiles.install(s, 'midi')
        p, other = s.Panel(Mock()), s.Panel(Mock())
        p.logo({'blue': 8})
        command = p.adb.shell.call_args.args[0]
        self.assertIn('cucumber:blue:logo5', command)
        self.assertNotIn('cucumber:blue:ring0/brightness', command)
        self.assertNotIn('/dev/fb', command)
        self.assertNotIn('kill', command)
        p.logo_reset()
        self.assertIn('if kill -0 "$owner"', p.adb.shell.call_args.args[0])
        other.adb.shell.assert_not_called()


class MidiSysfs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='midi-sysfs ')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.leds = self.root / 'leds'
        self.leds.mkdir()
        for component in midi.COMPONENTS:
            for i, name in enumerate(midi.names_for(component)):
                d = self.leds / name
                d.mkdir()
                (d / 'brightness').write_text(str(i % 25) + '\n')
                (d / 'max_brightness').write_text('170\n' if i % 2 else '255\n')
                (d / 'trigger').write_text(f'none [ledfb-{component}] timer\n')

    def test_ordinary_control_sequence_restores_first_snapshot_and_isolates_components(self):
        probe=midi.parse_probe(probe_fixture());probe.update(self.photo_fixture())
        for kind in midi.COMPONENTS:
            for name in midi.names_for(kind):
                d=self.leds/name
                probe['leds'][name]={'value':int((d/'brightness').read_text()),
                    'max':int((d/'max_brightness').read_text()),'trigger':'ledfb-'+kind}
        firmware=self.root/'firmware';firmware.write_text(probe['firmware'])
        def state():
            result={}
            for name in probe['leds']:
                d=self.leds/name;raw=(d/'trigger').read_text().strip();active=re.findall(r'\[([^]]+)\]',raw)
                result[name]=(int((d/'brightness').read_text()),active[0] if active else raw)
            return result
        for component in midi.COMPONENTS:
            with self.subTest(component=component):
                trace=self.root/'trace';trace.unlink(missing_ok=True)
                first=midi.names_for(component)[:3]
                # A trigger write selects one token; sysfs still lists every
                # available token on read. Emulate that between the steps.
                prefix='D='+shlex.quote(str(self.leds))+'\ndate() { echo 10; }\nsleep() {\n'
                prefix += '\n'.join([
                    'for kind in ring logo; do', 'for p in "$D"/cucumber:*:"$kind"*/trigger; do',
                    't=$(cat "$p")', 'case "$t" in none|ledfb-ring|ledfb-logo|timer)',
                    'for candidate in none "ledfb-$kind" timer; do',
                    'if [ "$candidate" = "$t" ]; then printf "[%s] " "$candidate"; else printf "%s " "$candidate"; fi',
                    'done > "$p";; esac', 'done', 'done', ''])
                prefix += 'printf "%s %s %s\\n" '+' '.join('"$(cat '+shlex.quote(str(self.leds/n/'brightness'))+')"' for n in first)+' >> '+shlex.quote(str(trace))+'\n}\n'
                source=midi.rgb_control_trial_script(probe,component,leds=str(self.leds),state_dir=str(self.root),identity='true',firmware_path=str(firmware))
                before=state()
                result=subprocess.run(['sh'],input=prefix+source,text=True,capture_output=True,timeout=15)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(state(),before)
                self.assertEqual(trace.read_text().splitlines(),['8 0 0','0 0 0','0 5 0'])
                snapshot=self.root/f'webpanel_midi_{component}.before'
                self.assertFalse(snapshot.exists())
                snapshot.write_text('existing manual session')
                result=subprocess.run(['sh'],input=prefix+source,text=True,capture_output=True,timeout=15)
                self.assertNotEqual(result.returncode,0)
                self.assertEqual(snapshot.read_text(),'existing manual session')
                self.assertEqual(state(),before)
                snapshot.unlink()

    def run_script(self, component, rgb=None, prefix='', **kwargs):
        script = midi.led_script(component, rgb, leds=str(self.leds), state_dir=str(self.root), identity='true', **kwargs)
        done = subprocess.run(['sh'], input=prefix + script, text=True, capture_output=True, timeout=15)
        # A sysfs trigger write selects one token; reading still lists every
        # available token and brackets the active one. Regular files need this
        # part of the kernel interface emulated between requests.
        for kind in midi.COMPONENTS:
            for name in midi.names_for(kind):
                trigger = self.leds / name / 'trigger'
                if trigger.exists() and trigger.read_text().strip() in ('none', f'ledfb-{kind}', 'timer'):
                    active = trigger.read_text().strip()
                    trigger.write_text(' '.join(f'[{t}]' if t == active else t for t in ('none', f'ledfb-{kind}', 'timer')) + '\n')
        return done

    def brightness(self, component):
        return [(self.leds / n / 'brightness').read_text() for n in midi.names_for(component)]

    def photo_fixture(self):
        suffixes = [f'{letter}{digit}' for letter in 'abcdefg' for digit in range(1,5)] + ['deg']
        suffixes += [f'{letter}{digit}' for letter in 'hlm' for digit in (1,2)]
        suffixes += [f'{letter}{digit}' for letter in 'pq' for digit in range(1,6)]
        probe={'clock_segments':[], 'leds':{}}
        for index,suffix in enumerate(suffixes):
            name='cucumber:white:clock-'+suffix
            d=self.leds/name;d.mkdir(exist_ok=True)
            maximum=170 if index%2 else 255
            value=index%17;trigger='timer' if index%2 else 'none'
            for key,text in [('brightness',str(value)),('max_brightness',str(maximum)),
                             ('trigger','none [timer]' if trigger=='timer' else '[none] timer')]:
                (d/key).write_text(text+'\n')
            probe['clock_segments'].append(name)
            probe['leds'][name]={'value':value,'max':maximum,'trigger':trigger}
        return probe

    def assert_photo_restored(self,probe):
        for name,led in probe['leds'].items():
            self.assertEqual((self.leds/name/'brightness').read_text(),str(led['value'])+'\n',name)
            self.assertEqual((self.leds/name/'trigger').read_text(),led['trigger']+'\n',name)
        self.assertFalse((self.root/'webpanel_midi_clock_photo.lock').exists())

    def display_source(self,probe):
        from test_midi_color_tests import display_report_fixture
        layout=midi.clock_display_layout(display_report_fixture())
        frames=midi.clock_display_frames(probe,layout)
        return midi._clock_group_script(probe,frames,leds=str(self.leds),state_dir=str(self.root),identity='true')

    def test_display_frames_cover_digits_and_colon_keep_other_elements_dark_and_restore(self):
        probe=self.photo_fixture();ring=self.brightness('ring');logo=self.brightness('logo')
        source=self.display_source(probe)
        hold=('echo FRAME; while read -r n v m t; do printf "%s %s\\n" "$n" "$(cat "$D/$n/brightness")"; '
              'printf "[none] timer\\n" > "$D/$n/trigger"; done < "$K/state"')
        done=subprocess.run(['sh'],input=source.replace('sleep 2',hold),text=True,capture_output=True,timeout=8)
        self.assertEqual(done.returncode,0,done.stderr)
        frames=[dict(line.split() for line in frame.strip().splitlines()) for frame in done.stdout.split('FRAME\n')[1:]]
        self.assertEqual(len(frames),4)
        expected=(('abcdef','bc','abdeg','abcdg'),('bcfg','acdfg','acdefg','abc'),
                  ('abcdefg','abcdfg','abcdef','bc'),('bc','abdeg','abcdg','bcfg'))
        for index,(frame,digits) in enumerate(zip(frames,expected)):
            active={f'cucumber:white:clock-{letter}{digit}' for digit,letters in enumerate(digits,1) for letter in letters}
            if index==3:active.update(('cucumber:white:clock-p3','cucumber:white:clock-q3'))
            self.assertEqual(len(frame),45)
            for name,led in probe['leds'].items():
                self.assertEqual(int(frame[name]),(60*led['max']+50)//100 if name in active else 0,(index,name))
        self.assert_photo_restored(probe)
        self.assertEqual(self.brightness('ring'),ring);self.assertEqual(self.brightness('logo'),logo)

    def test_display_interruptions_restore_and_sigkill_recovers_the_full_snapshot(self):
        for sig in (signal.SIGTERM,signal.SIGKILL):
            with self.subTest(signal=sig):
                probe=self.photo_fixture();source=self.display_source(probe)
                source=source.replace('sleep 2','while read -r n v m t; do printf "[none] timer\\n" > "$D/$n/trigger"; done < "$K/state"\nsleep 2')
                self.run_interrupted(source,sig)
                if sig==signal.SIGKILL:
                    self.assertTrue((self.root/'webpanel_midi_clock_photo.lock/state').exists())
                    recover=midi.clock_photo_script(probe,recover_stale=True,leds=str(self.leds),state_dir=str(self.root),identity='true')
                    done=subprocess.run(['sh'],input=recover,text=True,capture_output=True,timeout=5)
                    self.assertEqual(done.returncode,0,done.stderr)
                self.assert_photo_restored(probe)

    def test_group_rejects_unbounded_or_incomplete_frames_before_writes(self):
        probe=self.photo_fixture();levels={n:60 for n in probe['clock_segments']}
        for frames in ([],[(11,levels)],[(6,levels),(6,levels)],[(True,levels)],
                       [(2,{**levels,'foreign':60})],[(2,{n:101 for n in levels})]):
            with self.subTest(frames=len(frames)),self.assertRaises(ValueError):
                midi._clock_group_script(probe,frames)

    def test_timer_frames_count_down_each_second_keep_other_indicators_dark_and_restore(self):
        from test_midi_color_tests import display_report_fixture
        probe=self.photo_fixture();ring=self.brightness('ring');logo=self.brightness('logo')
        layout=midi.clock_display_layout(display_report_fixture())
        frames=midi.clock_timer_frames(probe,layout,8,20)
        source=midi._clock_group_script(probe,frames,leds=str(self.leds),state_dir=str(self.root),identity='true')
        hold=('echo FRAME; while read -r n v m t; do printf "%s %s\\n" "$n" "$(cat "$D/$n/brightness")"; '
              'printf "[none] timer\\n" > "$D/$n/trigger"; done < "$K/state"')
        done=subprocess.run(['sh'],input=source.replace('sleep 1',hold).replace('sleep 2',hold),text=True,capture_output=True,timeout=10)
        self.assertEqual(done.returncode,0,done.stderr)
        shown=[dict(line.split() for line in frame.strip().splitlines()) for frame in done.stdout.split('FRAME\n')[1:]]
        self.assertEqual(len(shown),9);self.assertEqual(sum(s for s,_ in frames),10)
        for left,frame in zip(range(8,-1,-1),shown):
            _,levels=midi.clock_text_frames(probe,layout,f'00:{left:02d}',20)[0]
            self.assertEqual({n:int(v) for n,v in frame.items()},
                             {n:(p*probe['leds'][n]['max']+50)//100 for n,p in levels.items()})
            self.assertEqual(int(frame['cucumber:white:clock-deg']),0)
        self.assert_photo_restored(probe)
        self.assertEqual(self.brightness('ring'),ring);self.assertEqual(self.brightness('logo'),logo)

    def test_photo_lights_all_45_real_nodes_and_can_dim_one_without_touching_rgb(self):
        ring=self.brightness('ring');logo=self.brightness('logo')
        for dim in (None,'cucumber:white:clock-h1'):
            with self.subTest(dim=dim):
                probe=self.photo_fixture()
                source=midi.clock_photo_script(probe,dim,leds=str(self.leds),state_dir=str(self.root),identity='true')
                hold=('while read -r n v m t; do printf "%s %s\\n" "$n" "$(cat "$D/$n/brightness")"; '
                      'printf "[none] timer\\n" > "$D/$n/trigger"; done < "$K/state"')
                done=subprocess.run(['sh'],input=source.replace('sleep 10',hold),text=True,capture_output=True,timeout=5)
                self.assertEqual(done.returncode,0,done.stderr)
                actual=dict(line.split() for line in done.stdout.splitlines())
                self.assertEqual(len(actual),45)
                for name,led in probe['leds'].items():
                    percent=10 if name==dim else 60
                    self.assertEqual(int(actual[name]),(percent*led['max']+50)//100)
                self.assert_photo_restored(probe)
                self.assertEqual(self.brightness('ring'),ring)
                self.assertEqual(self.brightness('logo'),logo)

    def test_photo_interruptions_restore_all_white_channels_and_sigkill_is_recoverable(self):
        for sig in (signal.SIGTERM,signal.SIGKILL):
            with self.subTest(signal=sig):
                probe=self.photo_fixture()
                source=midi.clock_photo_script(probe,leds=str(self.leds),state_dir=str(self.root),identity='true')
                source=source.replace('sleep 10','while read -r n v m t; do printf "[none] timer\\n" > "$D/$n/trigger"; done < "$K/state"\nsleep 10')
                self.run_interrupted(source,sig)
                if sig==signal.SIGKILL:
                    self.assertTrue((self.root/'webpanel_midi_clock_photo.lock/state').exists())
                    recover=midi.clock_photo_script(probe,recover_stale=True,leds=str(self.leds),state_dir=str(self.root),identity='true')
                    done=subprocess.run(['sh'],input=recover,text=True,capture_output=True,timeout=5)
                    self.assertEqual(done.returncode,0,done.stderr)
                self.assert_photo_restored(probe)

    def test_photo_recovery_refuses_live_owner_and_duplicate_or_foreign_snapshot_nodes(self):
        probe=self.photo_fixture();lock=self.root/'webpanel_midi_clock_photo.lock';lock.mkdir()
        lines=[f'{n} {led["value"]} {led["max"]} {led["trigger"]}\n' for n,led in probe['leds'].items()]
        (lock/'state').write_text(''.join(lines));(lock/'owner').write_text(str(os.getpid()))
        source=midi.clock_photo_script(probe,recover_stale=True,leds=str(self.leds),state_dir=str(self.root),identity='true')
        done=subprocess.run(['sh'],input=source,text=True,capture_output=True,timeout=5)
        self.assertNotEqual(done.returncode,0)
        dead=subprocess.run(['sh','-c','echo $$'],text=True,capture_output=True,check=True).stdout
        (lock/'owner').write_text(dead)
        for invalid in (lines[:1]+lines[:1]+lines[2:], ['cucumber:red:ring0 0 255 none\n']+lines[1:]):
            (lock/'state').write_text(''.join(invalid))
            done=subprocess.run(['sh'],input=source,text=True,capture_output=True,timeout=5)
            self.assertNotEqual(done.returncode,0)
            self.assertTrue((lock/'state').exists())
        (lock/'state').write_text(''.join(lines))
        done=subprocess.run(['sh'],input=source,text=True,capture_output=True,timeout=5)
        self.assertEqual(done.returncode,0,done.stderr)
        self.assert_photo_restored(probe)

    def snapshot(self, component):
        return self.root / f'webpanel_midi_{component}.before'

    def assert_native_triggers(self, component):
        for name in midi.names_for(component):
            value = (self.leds / name / 'trigger').read_text().strip()
            match = re.search(r'\[([^]]+)\]', value)
            self.assertEqual(match[1] if match else value, f'ledfb-{component}', name)

    def test_set_repeated_set_and_restore_each_channel_and_trigger(self):
        before = self.brightness('ring')
        untouched = self.brightness('logo')
        for rgb in ([255, 0, 128], [8, 16, 0]):
            done = self.run_script('ring', rgb)
            self.assertEqual(done.returncode, 0, done.stderr)
            for i, name in enumerate(midi.names_for('ring')):
                maximum = int((self.leds / name / 'max_brightness').read_text())
                self.assertEqual(int((self.leds / name / 'brightness').read_text()), (rgb[i % 3] * maximum + 127) // 255)
        self.assertEqual(self.brightness('logo'), untouched)
        done = self.run_script('ring')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.brightness('ring'), before)
        self.assertFalse(self.snapshot('ring').exists())
        for n in midi.names_for('ring'):
            self.assertEqual((self.leds / n / 'trigger').read_text(), 'none [ledfb-ring] timer\n')

    def test_logo_snapshot_and_reset_never_touch_ring(self):
        before = self.brightness('logo')
        ring = self.brightness('ring')
        self.assertEqual(self.run_script('logo', [8, 0, 0]).returncode, 0)
        self.assertEqual(self.brightness('ring'), ring)
        self.assertEqual(self.run_script('logo').returncode, 0)
        self.assertEqual(self.brightness('logo'), before)

    def test_missing_last_node_aborts_before_first_write(self):
        before = self.brightness('ring')
        last = self.leds / midi.names_for('ring')[-1] / 'trigger'
        last.unlink()
        self.assertNotEqual(self.run_script('ring', [8, 0, 0]).returncode, 0)
        self.assertEqual(self.brightness('ring'), before)
        self.assertFalse(self.snapshot('ring').exists())

    def test_corrupt_snapshot_aborts_set_and_reset_without_any_write(self):
        before = self.brightness('logo')
        self.snapshot('logo').write_text('cucumber:red:logo0 0 255 $(touch injected)\n')
        for rgb in ([8, 0, 0], None):
            self.assertNotEqual(self.run_script('logo', rgb).returncode, 0)
            self.assertEqual(self.brightness('logo'), before)
        self.assertFalse((self.root / 'injected').exists())

    def test_changed_maximum_refuses_restore_and_preserves_snapshot(self):
        self.assertEqual(self.run_script('logo', [8, 0, 0]).returncode, 0)
        before = self.brightness('logo')
        (self.leds / midi.names_for('logo')[-1] / 'max_brightness').write_text('16\n')
        self.assertNotEqual(self.run_script('logo').returncode, 0)
        self.assertEqual(self.brightness('logo'), before)
        self.assertTrue(self.snapshot('logo').exists())

    def test_existing_lock_and_foreign_identity_do_not_write(self):
        before = self.brightness('ring')
        lock = self.root / 'webpanel_midi_ring.before.lock'
        lock.mkdir()
        self.assertNotEqual(self.run_script('ring', [8, 0, 0]).returncode, 0)
        lock.rmdir()
        script = midi.led_script('ring', [8, 0, 0], leds=str(self.leds), state_dir=str(self.root), identity='false')
        done = subprocess.run(['sh'], input=script, text=True, capture_output=True, timeout=5)
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(self.brightness('ring'), before)
        self.assertFalse(self.snapshot('ring').exists())

    def test_invalid_values_are_rejected_before_shell_generation(self):
        for rgb in ([], [0, 1], [0, 0, 256], ['0', 0, 0], [True, 0, 0], [0, 0, -1]):
            with self.subTest(rgb=rgb), self.assertRaises(ValueError):
                midi.led_script('ring', rgb)
        with self.assertRaises(ValueError):
            midi.led_script('ring; touch injected', [0, 0, 0])

    def test_single_clock_segment_test_restores_without_guessing_36_segment_map(self):
        d = self.leds / 'cucumber:white:clock-a1'
        d.mkdir()
        for name, value in [('brightness', '13'), ('max_brightness', '170'), ('trigger', 'none [timer]')]:
            (d / name).write_text(value + '\n')
        script = midi.clock_segment_test_script(d.name, leds=str(self.leds), state_dir=str(self.root), identity='true')
        done = subprocess.run(['sh'], input=script, text=True, capture_output=True, timeout=5)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual((d / 'brightness').read_text(), '13\n')
        self.assertEqual((d / 'trigger').read_text(), 'timer\n')
        self.assertFalse((self.root / 'webpanel_midi_clock_test.lock').exists())

    def test_bright_clock_probe_uses_native_maximum_and_restores_original_state(self):
        d = self.leds / 'cucumber:white:clock-a1'
        d.mkdir()
        for name, value in [('brightness', '13'), ('max_brightness', '170'), ('trigger', 'none [timer]')]:
            (d / name).write_text(value + '\n')
        script = midi.clock_segment_test_script(d.name, brightness=255, seconds=2,
            leds=str(self.leds), state_dir=str(self.root), identity='true')
        # Observe the actual writes on simulated sysfs before the EXIT restore.
        script = script.replace('sleep 2', 'cat "$D/brightness"; cat "$D/trigger"')
        done = subprocess.run(['sh'], input=script, text=True, capture_output=True, timeout=5)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.splitlines(), ['170', 'none'])
        self.assertEqual((d / 'brightness').read_text(), '13\n')
        self.assertEqual((d / 'trigger').read_text(), 'timer\n')
        self.assertFalse((self.root / 'webpanel_midi_clock_test.lock').exists())
        for brightness in (0, 256, -1, True, '255'):
            with self.subTest(brightness=brightness), self.assertRaises(ValueError):
                midi.clock_segment_test_script(d.name, brightness=brightness)

    def test_clock_blink_is_visible_for_lit_and_dark_segments_and_preserves_other_channels(self):
        d = self.leds / 'cucumber:white:clock-a1'; d.mkdir()
        neighbor = self.leds / 'cucumber:white:clock-a2'; neighbor.mkdir()
        (neighbor / 'brightness').write_text('99\n')
        (neighbor / 'trigger').write_text('none [timer]\n')
        ring = self.brightness('ring'); logo = self.brightness('logo')
        for initial in (0, 170):
            with self.subTest(initial=initial):
                for key, value in [('brightness', str(initial)), ('max_brightness', '170'), ('trigger', 'none [timer]')]:
                    (d / key).write_text(value + '\n')
                source = midi.clock_segment_test_script(d.name, brightness=255, seconds=2, off_seconds=1,
                    leds=str(self.leds), state_dir=str(self.root), identity='true')
                source = source.replace('sleep 1', 'cat "$D/brightness"').replace('sleep 2', 'cat "$D/brightness"')
                done = subprocess.run(['sh'], input=source, text=True, capture_output=True, timeout=5)
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertEqual(done.stdout.splitlines(), ['0', '170'])
                self.assertEqual((d / 'brightness').read_text(), f'{initial}\n')
                self.assertEqual((d / 'trigger').read_text(), 'timer\n')
                self.assertEqual((neighbor / 'brightness').read_text(), '99\n')
                self.assertEqual((neighbor / 'trigger').read_text(), 'none [timer]\n')
                self.assertEqual(self.brightness('ring'), ring)
                self.assertEqual(self.brightness('logo'), logo)
                self.assertFalse((self.root / 'webpanel_midi_clock_test.lock').exists())
        for duration in (-1, 2, True, '1', None):
            with self.subTest(off_seconds=duration), self.assertRaises(ValueError):
                midi.clock_segment_test_script(d.name, off_seconds=duration)
        with self.assertRaises(ValueError):
            midi.clock_segment_test_script(d.name, seconds=3, off_seconds=1)

    def test_bounded_trial_restores_both_brightness_and_active_triggers(self):
        before = self.brightness('logo')
        script = midi.rgb_trial_script('logo', seconds=1, leds=str(self.leds), state_dir=str(self.root), identity='true')
        # Emulate sysfs trigger selection within one shell session: real sysfs
        # continues listing available triggers after each selection.
        script = script.replace('sleep 1', 'for f in "$S"; do while read -r n v m t; do printf "[none] %s timer\\n" "$t" > ' + shlex.quote(str(self.leds)) + '/"$n"/trigger; done < "$f"; done')
        done = subprocess.run(['sh'], input=script, text=True, capture_output=True, timeout=5)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.brightness('logo'), before)
        self.assertFalse(self.snapshot('logo').exists())

    def test_trial_refuses_an_existing_operator_snapshot(self):
        before = self.brightness('ring')
        self.snapshot('ring').write_text('existing operator state\n')
        script = midi.rgb_trial_script('ring', leds=str(self.leds), state_dir=str(self.root), identity='true')
        done = subprocess.run(['sh'], input=script, text=True, capture_output=True, timeout=5)
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(self.brightness('ring'), before)
        self.assertEqual(self.snapshot('ring').read_text(), 'existing operator state\n')

    def test_interrupted_clock_trial_has_a_validated_explicit_restore(self):
        name = 'cucumber:white:clock-a1'
        d = self.leds / name
        d.mkdir()
        for key, value in [('brightness', '4'), ('max_brightness', '170'), ('trigger', '[none] timer')]:
            (d / key).write_text(value + '\n')
        lock = self.root / 'webpanel_midi_clock_test.lock'
        lock.mkdir()
        owner = subprocess.run(['sh', '-c', 'echo $$'], text=True, capture_output=True, check=True).stdout
        (lock / 'owner').write_text(owner)
        snapshot = lock / 'state'
        script = midi.clock_segment_restore_script(name, leds=str(self.leds), state_dir=str(self.root), identity='true')
        snapshot.write_text('cucumber:red:ring0 13 170 timer\n')
        done = subprocess.run(['sh'], input=script, text=True, capture_output=True, timeout=5)
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual((d / 'brightness').read_text(), '4\n')
        self.assertTrue(snapshot.exists())
        snapshot.write_text(f'{name} 13 170 timer\n')
        done = subprocess.run(['sh'], input=script, text=True, capture_output=True, timeout=5)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual((d / 'brightness').read_text(), '13\n')
        self.assertEqual((d / 'trigger').read_text(), 'timer\n')
        self.assertFalse(lock.exists())

    def fault_prefix(self, component, fail_restore=False):
        # Fail one actual printf midway through apply(), after an atomic
        # snapshot has been published. A second fault exercises failed rollback.
        first = len(midi.names_for(component)) + 8
        failures = f'{first}|{first + 3}' if fail_restore else str(first)
        return ('midi_calls=0\nprintf() {\n'
                '  midi_calls=$((midi_calls + 1))\n'
                f'  case "$midi_calls" in {failures}) echo injected-write-failure >&2; return 73;; esac\n'
                '  command printf "$@"\n}\n')

    def test_write_failure_rolls_back_all_channels_and_keeps_recoverable_snapshot(self):
        before = self.brightness('ring')
        done = self.run_script('ring', [8, 0, 0], prefix=self.fault_prefix('ring'))
        self.assertNotEqual(done.returncode, 0)
        self.assertIn('injected-write-failure', done.stderr)
        self.assertEqual(self.brightness('ring'), before)
        self.assertTrue(self.snapshot('ring').exists())
        self.assert_native_triggers('ring')
        self.assertEqual(self.run_script('ring').returncode, 0)
        self.assertFalse(self.snapshot('ring').exists())

    def test_failed_rollback_preserves_snapshot_for_successful_retry(self):
        before = self.brightness('logo')
        done = self.run_script('logo', [8, 0, 0], prefix=self.fault_prefix('logo', fail_restore=True))
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(done.stderr.count('injected-write-failure'), 2)
        self.assertTrue(self.snapshot('logo').exists())
        self.assertNotEqual(self.brightness('logo'), before)
        self.assertEqual(self.run_script('logo').returncode, 0)
        self.assertEqual(self.brightness('logo'), before)
        self.assert_native_triggers('logo')

    def run_interrupted(self, source, signum):
        ready = self.root / 'ready'
        ready.unlink(missing_ok=True)
        # Emulate the available-trigger list, then block in a real child sleep.
        prefix = ('sleep() {\n'
                  '  if [ -n "${S:-}" ] && [ -f "$S" ]; then\n'
                  '    while read -r n v m t; do printf "[none] %s timer\\n" "$t" > ' + shlex.quote(str(self.leds)) + '/"$n"/trigger; done < "$S"\n'
                  '  fi\n'
                  '  if [ -n "${D:-}" ] && [ -f "$D/trigger" ]; then echo "[none] timer" > "$D/trigger"; fi\n'
                  '  command sleep 10 >/dev/null 2>&1 & midi_wait=$!\n'
                  '  echo "$midi_wait" > ' + shlex.quote(str(ready)) + '\n'
                  '  wait "$midi_wait"\n}\n')
        proc = subprocess.Popen(['sh'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, start_new_session=True)
        try:
            proc.stdin.write(prefix + source)
            proc.stdin.close(); proc.stdin = None
            deadline = time.monotonic() + 5
            while not ready.exists() and proc.poll() is None and time.monotonic() < deadline:
                time.sleep(.01)
            if not ready.exists():
                out, err = proc.communicate(timeout=2)
                self.fail('trial did not reach holding period: ' + out + err)
            os.killpg(proc.pid, signum)
            out, err = proc.communicate(timeout=8)
            self.assertNotEqual(proc.returncode, 0, out + err)
        finally:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.communicate(timeout=5)

    def test_catchable_signals_automatically_restore_rgb_and_clock(self):
        before = self.brightness('ring')
        for sig in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=sig):
                source = midi.rgb_trial_script('ring', leds=str(self.leds), state_dir=str(self.root), identity='true')
                self.run_interrupted(source, sig)
                self.assertEqual(self.brightness('ring'), before)
                self.assert_native_triggers('ring')
                self.assertFalse(self.snapshot('ring').exists())
                self.assertFalse((self.root / 'webpanel_midi_ring.before.lock').exists())
                # Reconstitute the kernel's read representation for the next run.
                self.run_script('logo')
        name = 'cucumber:white:clock-a1'
        d = self.leds / name; d.mkdir()
        for key, value in [('brightness', '13'), ('max_brightness', '170'), ('trigger', 'none [timer]')]:
            (d / key).write_text(value + '\n')
        source = midi.clock_segment_test_script(name, brightness=255, seconds=2, off_seconds=1,
            leds=str(self.leds), state_dir=str(self.root), identity='true')
        self.run_interrupted(source, signal.SIGTERM)
        self.assertEqual((d / 'brightness').read_text(), '13\n')
        self.assertEqual((d / 'trigger').read_text(), 'timer\n')
        self.assertFalse((self.root / 'webpanel_midi_clock_test.lock').exists())

    def test_sigkill_keeps_rgb_snapshot_and_explicit_restore_succeeds(self):
        before = self.brightness('ring')
        source = midi.rgb_trial_script('ring', leds=str(self.leds), state_dir=str(self.root), identity='true')
        self.run_interrupted(source, signal.SIGKILL)
        self.assertTrue(self.snapshot('ring').exists())
        self.assertNotEqual(self.brightness('ring'), before)
        self.assertEqual(self.run_script('ring', recover_stale=True).returncode, 0)
        self.assertEqual(self.brightness('ring'), before)

    def test_stale_lock_can_be_recovered_but_live_owner_lock_is_never_removed(self):
        before = self.brightness('logo')
        self.assertEqual(self.run_script('logo', [8, 0, 0]).returncode, 0)
        lock = self.root / 'webpanel_midi_logo.before.lock'; lock.mkdir()
        (lock / 'owner').write_text(str(os.getpid()) + '\n')
        current = self.brightness('logo')
        self.assertNotEqual(self.run_script('logo', recover_stale=True).returncode, 0)
        self.assertEqual(self.brightness('logo'), current)
        self.assertTrue((lock / 'owner').exists())
        dead = subprocess.run(['sh', '-c', 'echo $$'], text=True, capture_output=True, check=True).stdout
        (lock / 'owner').write_text(dead)
        self.assertEqual(self.run_script('logo', recover_stale=True).returncode, 0)
        self.assertEqual(self.brightness('logo'), before)
        self.assertFalse(lock.exists())

    def test_sigkill_during_rgb_write_leaves_dead_owner_lock_that_restore_can_recover(self):
        before = self.brightness('ring')
        at = len(midi.names_for('ring')) + 7
        prefix = ('midi_calls=0\nprintf() { midi_calls=$((midi_calls + 1)); command printf "$@"; '
                  f'if [ "$midi_calls" -eq {at} ]; then kill -KILL "$$"; fi; }}\n')
        done = self.run_script('ring', [8, 0, 0], prefix=prefix)
        self.assertNotEqual(done.returncode, 0)
        lock = self.root / 'webpanel_midi_ring.before.lock'
        self.assertTrue((lock / 'owner').exists())
        self.assertTrue(self.snapshot('ring').exists())
        self.assertNotEqual(self.brightness('ring'), before)
        self.assertNotEqual(self.run_script('ring').returncode, 0)
        self.assertEqual(self.run_script('ring', recover_stale=True).returncode, 0)
        self.assertEqual(self.brightness('ring'), before)
        self.assertFalse(lock.exists())

    def test_sigkill_clock_trial_retains_state_for_explicit_restore(self):
        name = 'cucumber:white:clock-a1'
        d = self.leds / name; d.mkdir()
        for key, value in [('brightness', '13'), ('max_brightness', '170'), ('trigger', 'none [timer]')]:
            (d / key).write_text(value + '\n')
        source = midi.clock_segment_test_script(name, brightness=255, seconds=2, off_seconds=1,
            leds=str(self.leds), state_dir=str(self.root), identity='true')
        self.run_interrupted(source, signal.SIGKILL)
        lock = self.root / 'webpanel_midi_clock_test.lock'
        self.assertTrue((lock / 'state').exists())
        self.assertTrue((lock / 'owner').exists())
        self.assertNotEqual((d / 'brightness').read_text(), '13\n')
        source = midi.clock_segment_restore_script(name, leds=str(self.leds), state_dir=str(self.root), identity='true')
        done = subprocess.run(['sh'], input=source, text=True, capture_output=True, timeout=5)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual((d / 'brightness').read_text(), '13\n')
        self.assertEqual((d / 'trigger').read_text(), 'timer\n')
        self.assertFalse(lock.exists())


class ReadonlyAudit(unittest.TestCase):
    def test_transport_failure_remains_unknown_and_never_starts_recovery(self):
        spec = importlib.util.spec_from_file_location('midi_readonly', Path(__file__).resolve().parents[1] / 'tools/midi_readonly.py')
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        runner = Mock(return_value=SimpleNamespace(returncode=1, stdout='', stderr='device not found'))
        connector = Mock(side_effect=ConnectionRefusedError())
        result = tool.audit('192.0.2.1:5555', runner=runner, connector=connector)
        self.assertIsNone(result['hardware'])
        self.assertEqual(result['adb'], 'unavailable')
        self.assertEqual(runner.call_args.args[0], ['adb', '-s', '192.0.2.1:5555', 'shell', midi.READ_SCRIPT])
        self.assertEqual(runner.call_count, 1)
        self.assertNotIn('input', runner.call_args.kwargs)

    def test_new_runtime_module_is_shipped_by_all_packagers(self):
        root = Path(__file__).resolve().parents[1]
        for path in ('Dockerfile', '.github/build-windows-zip.sh'):
            self.assertIn('midi.py', (root / path).read_text())
        self.assertIn('midi.py', fresh_server().PanelUpdater.FILES)
