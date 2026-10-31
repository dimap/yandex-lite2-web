"""Owner-operated colour-session APIs: fake ADB, no physical speaker."""
import copy
import json
import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import midi
import profiles
from test_profiles import fresh_server
from test_midi_hardware import probe_fixture


def display_report_fixture():
    """Synthetic observations using the accepted, public H10 layout; no speaker."""
    source=json.loads((Path(__file__).parents[1]/'docs/support/midi-clock-map.factory-9.json').read_text())
    trials=[]
    for channel in source['channels']:
        trial={'id':'fixture-'+channel['node'], 'component':'clock', 'segment':channel['node'],
               'state':'mapped','restored':True,'observation':'located','returncode':0}
        if channel['kind']=='digit_segment':
            trial['position']=f'd{channel["digit"]}-{channel["segment"]}'
        elif channel['kind']=='degree':
            trial['position']='degree'
        else:
            trial['photo_point']=channel['photo_point']
        trials.append(trial)
    return {'firmware':source['firmware'],'clock_segments':[c['node'] for c in source['channels']],
            'trials':trials}


class ColorSession(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.s = fresh_server()
        self.s.DATA_DIR = Path(tmp.name)
        profiles.install(self.s, 'midi')
        self.adb = Mock(serial='explicit-selected-speaker')
        self.adb.shell.return_value = ('', '', 0)
        self.panel = self.s.Panel(self.adb)
        self.before = midi.parse_probe(probe_fixture())
        self.panel.midi_hardware = Mock(side_effect=lambda: copy.deepcopy(self.before))
        sleep = patch.object(midi.time, 'sleep')
        sleep.start(); self.addCleanup(sleep.stop)

    def run_trial(self, component='logo', color='green'):
        return self.panel.midi_test_run({'component': component, 'color': color})['trial']

    def test_status_is_local_and_normal_hardware_flags_stay_off(self):
        self.assertEqual(self.panel.midi_tests()['trials'], [])
        self.panel.midi_hardware.assert_not_called()
        self.adb.shell.assert_not_called()
        self.assertFalse(profiles.PROFILES['midi']['light'])
        self.assertFalse(profiles.PROFILES['midi']['logo'])

    def test_saved_observations_remain_readable_offline_behind_the_existing_guard(self):
        handler=SimpleNamespace(path='/api/midi/tests',panel=self.panel,
            _guard=Mock(return_value=False),_handle=Mock(side_effect=lambda action:action()))
        self.s.Handler.do_GET(handler)
        handler._handle.assert_called_once_with(self.panel.midi_tests)
        self.panel.midi_hardware.assert_not_called();self.adb.shell.assert_not_called()
        handler._guard.return_value=True
        handler._handle=Mock()
        self.s.Handler.do_GET(handler)
        handler._handle.assert_not_called()

    def test_colour_trial_is_bounded_firmware_bound_and_does_not_claim_visible_success(self):
        trial = self.run_trial()
        source = self.adb.shell.call_args.args[0]
        self.assertIn('fixture-not-a-real-measurement', source)
        self.assertIn('sleep 2', source)
        self.assertIn('cucumber:green:logo5', source)
        self.assertIn('trap rollback 0', source)
        self.assertEqual(trial['state'], 'awaiting_observation')
        self.assertTrue(trial['restored'])
        self.assertFalse(self.panel.midi_tests()['blocked'])
        self.assertEqual(self.panel.midi_hardware.call_count, 3)

    def test_yellow_is_available_for_both_components(self):
        for component in ('ring', 'logo'):
            trial = self.run_trial(component, 'yellow')
            self.assertEqual(tuple(trial['rgb']), (8, 8, 0))
            self.assertTrue(trial['restored'])

    def test_owner_observation_persists_and_public_report_excludes_snapshots_and_target(self):
        trial = self.run_trial()
        self.panel.midi_test_observe({'id': trial['id'], 'observation': 'matched'})
        report = self.panel.midi_tests()
        self.assertEqual(report['trials'][0]['state'], 'passed')
        for value in ('explicit-selected-speaker', 'compatible', 'leds', 'before', 'target'):
            self.assertNotIn(value, json.dumps(report))
        file = self.s.DATA_DIR / '.midi_tests.json'
        self.assertEqual(file.stat().st_mode & 0o777, 0o600)
        new = midi.ColorTests(self.panel, self.s)
        self.assertEqual(new.status()['trials'][0]['observation'], 'matched')

    def test_missed_and_wrong_are_not_passed(self):
        trial = self.run_trial()
        for observation, expected in [('missed', 'not_observed'), ('wrong', 'failed')]:
            self.panel.midi_test_observe({'id': trial['id'], 'observation': observation})
            self.assertEqual(self.panel.midi_tests()['trials'][0]['state'], expected)

    def test_invalid_input_and_unverified_root_never_write(self):
        for data in (None, [], {}, {'component': [], 'color': 'red'},
                     {'component': 'clock', 'color': 'red'}, {'component': 'logo', 'color': '#ffffff'}):
            with self.subTest(data=data), self.assertRaises(self.s.AdbError):
                self.panel.midi_test_run(data)
        self.before['root'] = False
        with self.assertRaises(ValueError):
            self.run_trial()
        self.adb.shell.assert_not_called()

    def test_concurrent_click_is_rejected_before_hardware_access(self):
        self.panel.midi_tests()
        manager = self.panel._midi_color_tests
        manager.lock.acquire()
        try:
            with self.assertRaisesRegex(self.s.AdbError, 'Дождитесь'):
                self.run_trial()
        finally:
            manager.lock.release()
        self.panel.midi_hardware.assert_not_called()
        self.adb.shell.assert_not_called()

    def test_transient_brightness_mismatch_requires_two_consecutive_equal_reads(self):
        transient = copy.deepcopy(self.before)
        transient['leds']['cucumber:blue:logo0']['value'] = 94
        self.panel.midi_hardware.side_effect = [self.before, transient, self.before, self.before]
        trial = self.run_trial()
        self.assertTrue(trial['restored'])
        self.assertTrue(trial['initial_mismatch'])
        self.assertEqual(self.panel.midi_hardware.call_count, 4)

    def test_unstable_or_other_component_state_blocks_more_trials_and_cannot_be_accepted(self):
        changed = copy.deepcopy(self.before)
        changed['leds']['cucumber:red:ring0']['value'] = 99
        self.panel.midi_hardware.side_effect = [self.before, changed, self.before, changed, self.before]
        trial = self.run_trial()
        self.assertFalse(trial['restored'])
        self.assertTrue(self.panel.midi_tests()['blocked'])
        with self.assertRaises(self.s.AdbError):
            self.panel.midi_test_observe({'id': trial['id'], 'observation': 'matched'})
        writes = self.adb.shell.call_count
        with self.assertRaises(self.s.AdbError):
            self.run_trial('ring', 'red')
        self.assertEqual(self.adb.shell.call_count, writes)
        self.panel.midi_hardware.side_effect = lambda: copy.deepcopy(self.before)
        self.assertTrue(self.panel.midi_test_restore()['ok'])
        self.assertFalse(self.panel.midi_tests()['blocked'])
        self.assertIn('cat /system/vendor/quasar/version_full', self.adb.shell.call_args.args[0])

    def test_remote_error_is_recorded_without_marking_it_passed(self):
        self.adb.shell.return_value = ('', 'error', 7)
        trial = self.run_trial()
        self.assertEqual(trial['state'], 'error')
        self.assertTrue(trial['restored'])
        with self.assertRaises(self.s.AdbError):
            self.panel.midi_test_observe({'id': trial['id'], 'observation': 'matched'})

    def test_firmware_change_and_report_from_other_target_do_not_write(self):
        self.run_trial()
        self.adb.shell.reset_mock()
        self.before['firmware'] = 'different-firmware'
        with self.assertRaisesRegex(self.s.AdbError, 'Прошивка'):
            self.run_trial()
        self.adb.shell.assert_not_called()
        other = self.s.Panel(Mock(serial='other-speaker'))
        with self.assertRaisesRegex(self.s.AdbError, 'другой колонке'):
            other.midi_tests()

    def test_report_failure_before_trial_and_other_profiles_do_not_write(self):
        self.panel.midi_tests()
        with patch.object(self.panel._midi_color_tests, '_save', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.run_trial()
        self.adb.shell.assert_not_called()
        s = fresh_server(); profiles.install(s, 'mini2')
        adb = Mock(); p = s.Panel(adb)
        with self.assertRaises(s.AdbError):
            p.midi_test_run({'component':'logo', 'color':'red'})
        adb.shell.assert_not_called()

    def test_clock_catalog_contains_only_real_nodes_and_never_enables_display(self):
        report = self.panel.midi_clock_tests()
        self.assertEqual(report['clock_segments'], ['cucumber:white:clock-a1'])
        self.assertFalse(report['busy'])
        self.adb.shell.assert_not_called()
        self.assertFalse(profiles.PROFILES['midi']['clock'])

    def clock_trial(self, segment='cucumber:white:clock-a1'):
        return self.panel.midi_test_run({'component':'clock', 'segment':segment})['trial']

    def test_clock_probe_is_bound_to_actual_node_limits_firmware_and_minute_window(self):
        trial = self.clock_trial()
        self.assertTrue(trial['restored'])
        source = self.adb.shell.call_args.args[0]
        self.assertIn('clock-a1/max_brightness)" = 170', source)
        self.assertIn('fixture-not-a-real-measurement', source)
        self.assertIn('phase=$(date +%S)', source)
        self.assertIn('exit 42', source)
        self.assertIn('sleep 2', source)
        self.assertNotIn('/dev/fb', source)
        self.assertEqual(trial['brightness'], 170)
        self.assertEqual(trial['brightness_max'], 170)
        self.assertEqual((trial['pattern'], trial['off_seconds'], trial['on_seconds']), ('off-on', 1, 2))
        self.assertIn('b=$((255 * m / 255))', source)
        self.assertEqual(subprocess.run(['sh','-n'],input=source,text=True,capture_output=True).returncode,0)
        for observation in ('matched','wrong'):
            with self.assertRaises(self.s.AdbError):
                self.panel.midi_test_observe({'id':trial['id'],'observation':observation})
        for text in ('', 'x'*161):
            with self.assertRaises(self.s.AdbError):
                self.panel.midi_test_observe({'id':trial['id'],'observation':'located','description':text})
        result=self.panel.midi_test_observe({'id':trial['id'],'observation':'located','description':'Первая цифра, верхняя полоса'})
        self.assertEqual(result['trial']['state'],'mapped')
        self.assertEqual(result['trial']['description'],'Первая цифра, верхняя полоса')
        self.assertEqual(self.adb.shell.call_count,1,'observation never writes LEDs')

    def test_unknown_clock_node_is_rejected_before_any_write(self):
        for segment in ('cucumber:white:clock-a2', '/tmp/file', 'clock-a1; reboot'):
            with self.subTest(segment=segment), self.assertRaises(self.s.AdbError):
                self.clock_trial(segment)
        self.adb.shell.assert_not_called()

    def test_clock_diagram_location_persists_with_server_description(self):
        trial = self.clock_trial()
        result = self.panel.midi_test_observe({'id': trial['id'], 'observation': 'located',
            'position': 'd3-G', 'description': 'untrusted client label'})['trial']
        self.assertEqual(result['position'], 'd3-G')
        self.assertEqual(result['description'], 'Цифра 3 слева: средняя полоса')
        saved = midi.ColorTests(self.panel, self.s).status()['trials'][0]
        self.assertEqual(saved['position'], 'd3-G')
        self.assertEqual(self.adb.shell.call_count, 1, 'recording the position never writes LEDs')
        custom = self.panel.midi_test_observe({'id': trial['id'], 'observation': 'located',
            'description': 'Дополнительный значок слева'})['trial']
        self.assertNotIn('position', custom)
        self.assertEqual(custom['description'], 'Дополнительный значок слева')

    def test_invalid_clock_diagram_location_does_not_change_observation(self):
        trial = self.clock_trial()
        before = (self.s.DATA_DIR / '.midi_tests.json').read_bytes()
        for position in ('d5-A', 'clock-a1', '', None, [], {}):
            with self.subTest(position=position), self.assertRaises(self.s.AdbError):
                self.panel.midi_test_observe({'id': trial['id'], 'observation': 'located',
                    'position': position, 'description': 'Fallback must not bypass validation'})
            self.assertEqual((self.s.DATA_DIR / '.midi_tests.json').read_bytes(), before)
        self.assertEqual(self.adb.shell.call_count, 1)

    def photo_reference_fixture(self):
        # Bytes exercise the private-file integrity contract, not JPEG decoding.
        image=b'\xff\xd8fake-jpeg-for-unit-test'
        digest=hashlib.sha256(image).hexdigest()
        (self.s.DATA_DIR/('.midi-clock-reference-'+digest+'.jpg')).write_bytes(image)
        (self.s.DATA_DIR/'.midi-clock-reference.json').write_text(json.dumps({
            'source_sha256':digest,'width':1000,'height':1000,
            'crop':{'x':.1,'y':.4,'width':.8,'height':.2}}))
        self.panel.midi_tests()
        return self.panel._midi_color_tests.clock_reference()

    def test_owner_photo_point_is_bound_to_reference_geometry_and_does_not_write_hardware(self):
        reference=self.photo_reference_fixture();trial=self.clock_trial()
        result=self.panel.midi_test_observe({'id':trial['id'],'observation':'located',
            'photo_point':{'x':.251234,'y':.6,'reference':reference['id']}})['trial']
        self.assertEqual(result['photo_point'],{'x':.2512,'y':.6,'reference':reference['id']})
        report=self.panel.midi_tests()
        self.assertEqual(report['clock_references'][reference['id']]['crop'],reference['crop'])
        self.assertNotIn('data:image',json.dumps(report),'the exported report does not embed the private room photo')
        self.assertEqual(self.adb.shell.call_count,1)
        result=self.panel.midi_test_observe({'id':trial['id'],'observation':'located','position':'d1-A'})['trial']
        self.assertNotIn('photo_point',result)
        self.assertEqual(result['position'],'d1-A')

    def test_invalid_photo_points_leave_saved_observations_unchanged(self):
        reference=self.photo_reference_fixture();trial=self.clock_trial()
        before=(self.s.DATA_DIR/'.midi_tests.json').read_bytes()
        for point in (None,[],{}, {'x':True,'y':.5,'reference':reference['id']},
                      {'x':float('nan'),'y':.5,'reference':reference['id']},
                      {'x':2,'y':.5,'reference':reference['id']},
                      {'x':10**500,'y':.5,'reference':reference['id']},
                      {'x':.2,'y':.5,'reference':'old-image'}):
            with self.subTest(point=point),self.assertRaises(self.s.AdbError):
                self.panel.midi_test_observe({'id':trial['id'],'observation':'located','photo_point':point})
            self.assertEqual((self.s.DATA_DIR/'.midi_tests.json').read_bytes(),before)

    def test_missing_or_modified_photo_is_not_accepted_as_a_reference(self):
        self.assertIsNone(midi.ColorTests(self.panel,self.s).clock_reference())
        self.photo_reference_fixture()
        config=json.loads((self.s.DATA_DIR/'.midi-clock-reference.json').read_text())
        (self.s.DATA_DIR/('.midi-clock-reference-'+config['source_sha256']+'.jpg')).write_bytes(b'changed')
        self.assertIsNone(self.panel._midi_color_tests.clock_reference())

    def test_outside_map_is_retained_without_claiming_a_physical_mapping(self):
        trial = self.clock_trial()
        result = self.panel.midi_test_observe({'id':trial['id'], 'observation':'outside_map'})['trial']
        self.assertEqual(result['state'], 'unmapped')
        self.assertNotIn('position', result)
        self.assertEqual(midi.ColorTests(self.panel,self.s).status()['trials'][0]['observation'], 'outside_map')
        self.assertEqual(self.adb.shell.call_count,1)
        colour = self.run_trial()
        with self.assertRaises(self.s.AdbError):
            self.panel.midi_test_observe({'id':colour['id'], 'observation':'outside_map'})

    def test_photo_probes_use_real_catalog_and_never_automatically_map_it(self):
        for segment in (None,'cucumber:white:clock-a1'):
            trial = self.panel.midi_test_run({'component':'clock_photo','segment':segment})['trial']
            self.assertEqual(trial['state'],'photo_ready')
            self.assertTrue(trial['restored'])
            self.assertEqual(trial['channels'],1)
            self.assertEqual(trial['photo_seconds'],10)
            self.assertIn('sleep 10',self.adb.shell.call_args.args[0])
            self.assertNotIn('framebuffer',self.adb.shell.call_args.args[0])
            with self.assertRaises(self.s.AdbError):
                self.panel.midi_test_observe({'id':trial['id'],'observation':'located','position':'d1-A'})
        self.adb.shell.reset_mock()
        with self.assertRaises(self.s.AdbError):
            self.panel.midi_test_run({'component':'clock_photo','segment':'cucumber:white:clock-q5'})
        self.adb.shell.assert_not_called()

    def test_photo_verifies_all_white_channels_and_can_recover_from_its_own_snapshot(self):
        self.before['clock_segments'].append('cucumber:white:clock-h2')
        self.before['leds']['cucumber:white:clock-h2']={'value':5,'max':255,'trigger':'none'}
        changed=copy.deepcopy(self.before);changed['leds']['cucumber:white:clock-h2']['value']=0
        self.panel.midi_hardware.side_effect=[self.before]+[changed]*4
        trial=self.panel.midi_test_run({'component':'clock_photo','segment':'cucumber:white:clock-a1'})['trial']
        self.assertFalse(trial['restored'])
        self.assertTrue(self.panel.midi_tests()['blocked'])
        self.panel.midi_hardware.side_effect=lambda:copy.deepcopy(self.before)
        self.assertTrue(self.panel.midi_test_restore()['ok'])
        self.assertIn('webpanel_midi_clock_photo.lock',self.adb.shell.call_args.args[0])

    def test_clock_probe_requires_paused_playback_before_any_hardware_access(self):
        self.panel.glagol=Mock()
        self.panel.glagol.info.return_value={'state':{'playing':True}}
        with self.assertRaisesRegex(self.s.AdbError,'паузу'):
            self.clock_trial()
        self.panel.midi_hardware.assert_not_called()
        self.adb.shell.assert_not_called()

    def test_clock_verification_checks_the_selected_white_channel_and_restore_uses_its_snapshot(self):
        changed=copy.deepcopy(self.before);changed['leds']['cucumber:white:clock-a1']['value']=8
        self.panel.midi_hardware.side_effect=[self.before]+[changed]*4
        trial=self.clock_trial()
        self.assertFalse(trial['restored'])
        self.assertTrue(self.panel.midi_tests()['blocked'])
        self.panel.midi_hardware.side_effect=lambda:copy.deepcopy(self.before)
        self.assertTrue(self.panel.midi_test_restore()['ok'])
        source=self.adb.shell.call_args.args[0]
        self.assertIn('webpanel_midi_clock_test.lock',source)
        self.assertIn('clock-a1',source)
        self.assertNotIn('webpanel_midi_ring.before',source)

    def test_minute_boundary_skip_does_not_claim_a_mapping_or_block_other_trials(self):
        self.adb.shell.return_value=('','',42)
        trial=self.clock_trial()
        self.assertEqual(trial['state'],'not_run')
        self.assertTrue(trial['no_write'])
        self.assertFalse(self.panel.midi_tests()['blocked'])
        self.assertEqual(self.panel.midi_hardware.call_count,1,'no attempt to compare clocks across a skipped minute')
        with self.assertRaises(self.s.AdbError):
            self.panel.midi_test_observe({'id':trial['id'],'observation':'located','description':'не измерено'})

    def prepare_display(self):
        self.panel.midi_tests()
        manager=self.panel._midi_color_tests
        report=manager._load();report.update(display_report_fixture());manager._save(report)
        self.before['firmware']=report['firmware']
        self.before['clock_segments']=report['clock_segments']
        for name in report['clock_segments']:
            self.before['leds'][name]={'value':0,'max':255,'trigger':'none'}
        return manager

    def test_display_requires_complete_observed_firmware_bound_layout_before_hardware_access(self):
        manager=self.prepare_display();valid=manager._load()
        self.assertTrue(manager.status()['display_test_ready'])
        self.panel.midi_hardware.assert_not_called()
        variants=[]
        for key,value in [('firmware','other'),('clock_segments',valid['clock_segments'][:-1])]:
            report=copy.deepcopy(valid);report[key]=value;variants.append(report)
        for key,value in [('restored',False),('state','not_observed'),('position','d4-G')]:
            report=copy.deepcopy(valid);report['trials'][0][key]=value;variants.append(report)
        report=copy.deepcopy(valid)
        next(t for t in report['trials'] if t['segment'].endswith('-p3'))['photo_point']['x']=.2
        variants.append(report)
        for report in variants:
            manager._save(report)
            self.assertFalse(manager.status()['display_test_ready'])
            with self.assertRaises(self.s.AdbError):
                self.panel.midi_test_run({'component':'clock_display'})
        self.panel.midi_hardware.assert_not_called();self.adb.shell.assert_not_called()

    def test_display_trial_is_bounded_restored_and_owner_graded_without_enabling_clocks(self):
        self.prepare_display()
        trial=self.panel.midi_test_run({'component':'clock_display'})['trial']
        self.assertEqual(tuple(trial['display_texts']),('0123','4567','8901','12:34'))
        self.assertEqual(trial['display_seconds'],8)
        self.assertEqual(trial['state'],'awaiting_observation');self.assertTrue(trial['restored'])
        source=self.adb.shell.call_args.args[0]
        self.assertEqual(source.count('sleep 2'),4);self.assertIn('phase=$(date +%S)',source)
        self.assertIn(midi.DISPLAY_TEST_FIRMWARE,source)
        self.assertNotIn('/dev/fb',source)
        self.assertEqual(subprocess.run(['sh','-n'],input=source,text=True,capture_output=True).returncode,0)
        for observation,state in [('missed','not_observed'),('wrong','failed'),('matched','passed')]:
            result=self.panel.midi_test_observe({'id':trial['id'],'observation':observation})['trial']
            self.assertEqual(result['state'],state)
        self.assertEqual(self.adb.shell.call_count,1)
        self.assertFalse(profiles.PROFILES['midi']['clock'])

    def test_display_rechecks_full_catalog_and_verifies_every_white_channel(self):
        self.prepare_display()
        complete=copy.deepcopy(self.before)
        self.before['clock_segments']=self.before['clock_segments'][:-1]
        with self.assertRaisesRegex(self.s.AdbError,'отличается'):
            self.panel.midi_test_run({'component':'clock_display'})
        self.adb.shell.assert_not_called()
        self.before=complete
        changed=copy.deepcopy(complete);changed['leds']['cucumber:white:clock-q5']['value']=12
        self.panel.midi_hardware.side_effect=[complete]+[changed]*4
        trial=self.panel.midi_test_run({'component':'clock_display'})['trial']
        self.assertFalse(trial['restored']);self.assertTrue(self.panel.midi_tests()['blocked'])
        with self.assertRaises(self.s.AdbError):
            self.panel.midi_test_observe({'id':trial['id'],'observation':'matched'})
        self.panel.midi_hardware.side_effect=lambda:copy.deepcopy(complete)
        self.assertTrue(self.panel.midi_test_restore()['ok'])
        self.assertIn('webpanel_midi_clock_photo.lock',self.adb.shell.call_args.args[0])

    def test_display_followup_checks_cover_each_digit_position_and_stay_bounded(self):
        self.prepare_display()
        report=self.panel._midi_color_tests._load()
        layout=midi.clock_display_layout(report)
        seen={i:set() for i in range(1,5)}
        for check in ('digits_low','digits_high','degree','brightness'):
            frames=midi.clock_display_frames(self.before,layout,check)
            self.assertLessEqual(sum(seconds for seconds,_ in frames),8)
            self.assertTrue(all(set(levels)==set(report['clock_segments']) for _,levels in frames))
            trial=self.panel.midi_test_run({'component':'clock_display','check':check})['trial']
            self.assertEqual(trial['display_check'],check)
            self.assertTrue(trial['restored'])
            self.assertEqual(trial['state'],'awaiting_observation')
            if check!='brightness':
                for text in trial['display_texts']:
                    for i,char in enumerate(text.replace(':','').replace('°',''),1):seen[i].add(char)
            if check=='brightness':
                self.assertEqual([levels['cucumber:white:clock-b1'] for _,levels in frames],[20,60,100])
            if check=='degree':
                self.assertEqual([levels['cucumber:white:clock-deg'] for _,levels in frames],[0,0,60])
        self.assertTrue(all(values==set('0123456789') for values in seen.values()))
        self.assertFalse(profiles.PROFILES['midi']['clock'])

    def test_unknown_display_check_is_rejected_before_reading_or_writing_hardware(self):
        for check in ('injected',None,[],True):
            with self.subTest(check=check),self.assertRaises(self.s.AdbError):
                self.panel.midi_test_run({'component':'clock_display','check':check})
        self.panel.midi_hardware.assert_not_called();self.adb.shell.assert_not_called()

    def test_live_display_values_use_accepted_map_actual_brightness_and_firmware(self):
        manager=self.prepare_display();report=manager._load()
        self.before['leds']['cucumber:white:clock-a1']={'value':17,'max':170,'trigger':'none'}
        self.before['leds']['cucumber:white:clock-p3']['value']=128
        self.before['leds']['cucumber:white:clock-deg']['value']=255
        frame=midi.clock_display_values(self.before,report)
        self.assertEqual(len(frame),36)
        self.assertEqual((frame[0],frame[32],frame[33]),(26,255,128))
        self.assertTrue(all(frame[i]==0 for i in (7,15,23,31,35)))
        self.before['firmware']='different-firmware'
        with self.assertRaises(ValueError):midi.clock_display_values(self.before,report)

    def test_live_display_adapter_reads_accepted_map_without_changing_the_report(self):
        self.prepare_display()
        path=self.s.DATA_DIR/'.midi_tests.json';original=path.read_bytes()
        rows=[f'compatible\tyandex,cucumber','firmware\t'+midi.DISPLAY_TEST_FIRMWARE]
        rows += [f'led\t{n}\t{v["value"]}\t{v["max"]}\t[none]' for n,v in self.before['leds'].items()]
        self.adb.shell.return_value=('\n'.join(rows),'',0)
        state=self.panel.clock_state()
        self.assertEqual(len(state['v']),36);self.assertFalse(state['unverified'])
        self.assertEqual(path.read_bytes(),original)
        self.assertNotIn(' > ',self.adb.shell.call_args.args[0])

    def prepare_display_controls(self):
        manager=self.prepare_display();report=manager._load()
        report['trials'] += [dict(id='fixture-display-'+check,component='clock_display',display_check=check,
            state='passed',restored=True,returncode=0,observation='matched') for check in midi.DISPLAY_CHECKS]
        manager._save(report)
        return manager

    def test_custom_text_waits_for_all_checks_and_latest_failure_closes_controls(self):
        manager=self.prepare_display()
        with self.assertRaisesRegex(self.s.AdbError,'завершите'):
            self.panel.midi_test_run({'component':'clock_text','text':'12:34','brightness':60})
        self.panel.midi_hardware.assert_not_called();self.adb.shell.assert_not_called()
        manager=self.prepare_display_controls()
        self.assertTrue(manager.status()['display_controls_ready'])
        report=manager._load();report['trials'].append(dict(component='clock_display',display_check='degree',state='failed',restored=True))
        manager._save(report)
        self.assertFalse(manager.status()['display_controls_ready'])
        with self.assertRaises(self.s.AdbError):
            self.panel.midi_test_run({'component':'clock_text','text':'12:34','brightness':60})
        self.adb.shell.assert_not_called()

    def test_custom_text_is_bounded_restored_and_does_not_enable_persistent_modes(self):
        self.prepare_display_controls()
        trial=self.panel.midi_test_run({'component':'clock_text','text':'09:45°','brightness':20})['trial']
        self.assertEqual(trial['state'],'completed');self.assertTrue(trial['restored'])
        self.assertEqual(trial['display_texts'],['09:45°']);self.assertEqual(trial['display_seconds'],8)
        source=self.adb.shell.call_args.args[0]
        self.assertIn('sleep 8',source);self.assertNotIn('/dev/fb',source)
        self.assertEqual(subprocess.run(['sh','-n'],input=source,text=True,capture_output=True).returncode,0)
        self.assertFalse(profiles.PROFILES['midi']['clock'])

    def test_invalid_custom_text_and_brightness_do_not_touch_the_device(self):
        self.prepare_display_controls()
        for text,brightness in [('hello',60),('$(id)',60),('12:34',19),('12:34',101),('1234',True),('1234','60'),(None,60)]:
            with self.subTest(text=text,brightness=brightness),self.assertRaises(self.s.AdbError):
                self.panel.midi_test_run({'component':'clock_text','text':text,'brightness':brightness})
        self.panel.midi_hardware.assert_not_called();self.adb.shell.assert_not_called()

    def test_short_date_uses_only_lower_separator_and_temperature_keeps_leading_digits_blank(self):
        manager=self.prepare_display_controls();layout=midi.clock_display_layout(manager._load())
        date=midi.clock_text_frames(self.before,layout,'30.10',20)[0][1]
        self.assertEqual(date['cucumber:white:clock-p3'],0)
        self.assertEqual(date['cucumber:white:clock-q3'],20)
        temp=midi.clock_text_frames(self.before,layout,'  42°',20)[0][1]
        self.assertTrue(all(temp[f'cucumber:white:clock-{letter}{digit}']==0 for letter in 'abcdefg' for digit in (1,2)))
        self.assertEqual(temp['cucumber:white:clock-deg'],20)
        for mode,text in (('date','30.10'),('temp','  42°')):
            trial=self.panel.midi_test_run({'component':'clock_text','text':text,'brightness':20,'mode':mode})['trial']
            self.assertEqual(trial['state'],'completed');self.assertTrue(trial['restored'])
            self.assertEqual(trial['display_mode'],mode);self.assertEqual(trial['display_seconds'],8)
        self.panel.midi_hardware.reset_mock();self.adb.shell.reset_mock()
        for text,mode in (('    ','text'),('3.010','date'),('12:34','timer')):
            with self.assertRaises(self.s.AdbError):
                self.panel.midi_test_run({'component':'clock_text','text':text,'brightness':20,'mode':mode})
        self.panel.midi_hardware.assert_not_called();self.adb.shell.assert_not_called()

    def prepare_rgb_controls(self, component='ring', accepted=False):
        self.panel.midi_tests();manager=self.panel._midi_color_tests;report=manager._load()
        report['firmware']=self.before['firmware']
        report['trials'] += [dict(id=component+'-'+color,component=component,color=color,state='passed',
            restored=True,returncode=0,observation='matched') for color in midi.TRIAL_COLORS]
        if accepted:
            report['trials'].append(dict(id=component+'-controls',component=component+'_controls',
                state='passed',restored=True,returncode=0,observation='matched'))
        manager._save(report);return manager

    def test_short_timer_requires_display_acceptance_and_strict_bounded_input(self):
        self.prepare_display()
        with self.assertRaisesRegex(self.s.AdbError,'завершите'):
            self.panel.midi_test_run({'component':'clock_timer','seconds':8,'brightness':20})
        self.prepare_display_controls()
        for seconds,brightness in [(True,20),(7,20),(60,20),('8',20),(8,19),(8,101),(8,True)]:
            with self.subTest(seconds=seconds,brightness=brightness),self.assertRaises(self.s.AdbError):
                self.panel.midi_test_run({'component':'clock_timer','seconds':seconds,'brightness':brightness})
        self.panel.midi_hardware.assert_not_called();self.adb.shell.assert_not_called()

    def test_short_timer_restores_all_channels_and_requires_owner_observation(self):
        self.prepare_display_controls()
        trial=self.panel.midi_test_run({'component':'clock_timer','seconds':8,'brightness':20})['trial']
        self.assertEqual(trial['display_texts'],[f'00:{left:02d}' for left in range(8,-1,-1)])
        self.assertEqual(trial['display_seconds'],10);self.assertEqual(trial['timer_seconds'],8)
        self.assertEqual(trial['state'],'awaiting_observation');self.assertTrue(trial['restored'])
        source=self.adb.shell.call_args.args[0]
        self.assertEqual(source.count('sleep 1\n'),8);self.assertEqual(source.count('sleep 2\n'),1)
        self.assertIn(midi.DISPLAY_TEST_FIRMWARE,source);self.assertIn('phase=$(date +%S)',source)
        self.assertNotIn('aplay',source);self.assertNotIn('/dev/fb',source)
        self.assertEqual(subprocess.run(['sh','-n'],input=source,text=True,capture_output=True).returncode,0)
        self.assertEqual(self.panel.midi_test_observe({'id':trial['id'],'observation':'matched'})['trial']['state'],'passed')
        self.assertFalse(profiles.PROFILES['midi']['clock'])
        self.assertEqual(self.adb.shell.call_count,1)

    def test_short_timer_minute_skip_never_claims_a_completed_countdown(self):
        self.prepare_display_controls();self.adb.shell.return_value=('', '', 42)
        trial=self.panel.midi_test_run({'component':'clock_timer','seconds':8,'brightness':20})['trial']
        self.assertEqual(trial['state'],'not_run');self.assertTrue(trial['no_write'])
        self.assertFalse(self.panel.midi_tests()['blocked'])
        with self.assertRaises(self.s.AdbError):
            self.panel.midi_test_observe({'id':trial['id'],'observation':'matched'})

    def test_short_timer_restore_mismatch_blocks_new_tests_and_uses_full_group_recovery(self):
        self.prepare_display_controls()
        changed=copy.deepcopy(self.before);changed['leds']['cucumber:white:clock-q5']['value']=12
        self.panel.midi_hardware.side_effect=[self.before]+[changed]*4
        trial=self.panel.midi_test_run({'component':'clock_timer','seconds':8,'brightness':20})['trial']
        self.assertFalse(trial['restored']);self.assertTrue(self.panel.midi_tests()['blocked'])
        self.panel.midi_hardware.side_effect=lambda:copy.deepcopy(self.before)
        self.assertTrue(self.panel.midi_test_restore()['ok'])
        self.assertIn('webpanel_midi_clock_photo.lock',self.adb.shell.call_args.args[0])

    def test_rgb_controls_require_colors_then_owner_acceptance_for_the_selected_component(self):
        with self.assertRaisesRegex(self.s.AdbError,'четыре цвета'):
            self.panel.midi_test_run({'component':'ring_controls'})
        self.panel.midi_hardware.assert_not_called();self.adb.shell.assert_not_called()
        self.prepare_rgb_controls('logo')
        with self.assertRaises(self.s.AdbError):self.panel.logo({'red':8})
        trial=self.panel.midi_test_run({'component':'logo_controls'})['trial']
        self.assertEqual((trial['state'],trial['restored'],trial['target_component']),('awaiting_observation',True,'logo'))
        source=self.adb.shell.call_args.args[0]
        self.assertEqual(source.count('sleep 2'),2);self.assertEqual(source.count('sleep 1'),1)
        self.assertIn('trap rollback 0',source);self.assertIn('max_brightness',source)
        self.assertNotIn('/dev/fb',source)
        self.panel.midi_test_observe({'id':trial['id'],'observation':'matched'})
        self.assertTrue(self.panel.midi_tests()['rgb_controls']['logo']['ready'])
        self.assertFalse(self.panel.midi_tests()['rgb_controls']['ring']['ready'])
        self.adb.shell.reset_mock();self.panel.midi_hardware.reset_mock()
        self.panel.logo({'red':8});self.assertEqual(self.adb.shell.call_count,1)
        self.assertIn(self.before['firmware'],self.adb.shell.call_args.args[0])
        self.panel.logo_reset();self.assertIn('restore',self.adb.shell.call_args.args[0])
        self.adb.shell.reset_mock();self.panel.midi_hardware.reset_mock()
        for action in (lambda:self.panel.ring({'red':8}),lambda:self.panel.logo({'red':255})):
            with self.assertRaises(self.s.AdbError):action()
        self.panel.midi_hardware.assert_not_called();self.adb.shell.assert_not_called()

    def test_rgb_controls_verify_white_channels_and_keep_recovery_available(self):
        self.prepare_rgb_controls('ring')
        changed=copy.deepcopy(self.before);changed['leds']['cucumber:white:clock-a1']['value']=99
        self.panel.midi_hardware.side_effect=[self.before]+[changed]*4
        trial=self.panel.midi_test_run({'component':'ring_controls'})['trial']
        self.assertFalse(trial['restored']);self.assertTrue(self.panel.midi_tests()['blocked'])
        self.panel.midi_hardware.side_effect=lambda:copy.deepcopy(self.before)
        self.assertTrue(self.panel.midi_test_restore()['ok'])
        self.assertIn('webpanel_midi_ring.before',self.adb.shell.call_args.args[0])
        self.assertNotIn('webpanel_midi_logo.before',self.adb.shell.call_args.args[0])

    def test_rgb_controls_refuse_busy_changed_firmware_and_active_playback(self):
        manager=self.prepare_rgb_controls('logo',accepted=True)
        manager.lock.acquire()
        try:
            with self.assertRaises(self.s.AdbError):self.panel.logo({'green':8})
        finally:manager.lock.release()
        self.panel.midi_hardware.assert_not_called();self.adb.shell.assert_not_called()
        self.before['firmware']='changed'
        with self.assertRaisesRegex(self.s.AdbError,'Прошивка изменилась'):self.panel.logo({'red':8})
        self.adb.shell.assert_not_called()
        self.panel.glagol=Mock();self.panel.glagol.info.return_value={'state':{'playing':True}}
        with self.assertRaisesRegex(self.s.AdbError,'паузу'):
            self.panel.midi_test_run({'component':'logo_controls'})
        self.adb.shell.assert_not_called()

    def test_pending_rechecks_preserve_acceptance_but_explicit_mismatch_revokes_it(self):
        manager=self.prepare_rgb_controls('logo',accepted=True);report=manager._load()
        report['trials'] += [dict(id='pending-blue',component='logo',color='blue',state='awaiting_observation',restored=True),
                            dict(id='pending-control',component='logo_controls',state='awaiting_observation',restored=True)]
        manager._save(report)
        self.assertTrue(manager.status()['rgb_controls']['logo']['ready'])
        report['trials'].append(dict(id='wrong-blue',component='logo',color='blue',state='failed',observation='wrong',restored=True))
        manager._save(report)
        capability=manager.status()['rgb_controls']['logo']
        self.assertFalse(capability['ready']);self.assertTrue(capability['can_reset'])
        with self.assertRaises(self.s.AdbError):self.panel.logo({'blue':8})
        self.adb.shell.assert_not_called()
        self.panel.logo_reset()  # Prior acceptance still permits an explicit restore.

    def test_display_skip_and_playback_gate_do_not_start_a_sequence(self):
        self.prepare_display();self.panel.glagol=Mock()
        self.panel.glagol.info.return_value={'state':{'playing':True}}
        with self.assertRaisesRegex(self.s.AdbError,'паузу'):
            self.panel.midi_test_run({'component':'clock_display'})
        self.panel.midi_hardware.assert_not_called();self.adb.shell.assert_not_called()
        self.panel.glagol.info.return_value={'state':{'playing':False}}
        self.adb.shell.return_value=('','',42)
        trial=self.panel.midi_test_run({'component':'clock_display'})['trial']
        self.assertEqual(trial['state'],'not_run');self.assertTrue(trial['no_write'])
        self.assertFalse(self.panel.midi_tests()['blocked'])


class PortableClockMap(unittest.TestCase):
    def test_logical_colon_positions_work_without_the_authors_photo(self):
        report = display_report_fixture()
        for trial in report['trials']:
            for suffix, position in (('-p3', 'colon-top'), ('-q3', 'colon-bottom')):
                if trial['segment'].endswith(suffix):
                    trial.pop('photo_point', None)
                    trial['position'] = position
        _, colon = midi.clock_display_layout(report)
        self.assertEqual(colon, ['cucumber:white:clock-p3', 'cucumber:white:clock-q3'])
        trial['state'] = 'failed'
        with self.assertRaises(ValueError):
            midi.clock_display_layout(report)


if __name__ == '__main__':
    unittest.main()
