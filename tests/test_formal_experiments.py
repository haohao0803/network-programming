"""Formal planning, sampling integrity and actual paced UDP process lifecycle."""
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import formal_campaign
import archive_campaign
import report_campaign
import run_competition
from run_experiments import ready
from file_tools import sha256


class FormalTests(unittest.TestCase):
    def test_raw_archive_keeps_files_hashes_and_rejects_active_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)/'raw'
            root.mkdir()
            (root/'campaign.json').write_text(json.dumps({'tasks':[{'status':'finished'}]}))
            (root/'input.bin').write_bytes(b'original bytes')
            sidecar = archive_campaign.archive(root, Path(d)/'archive.tar.gz')
            info = json.loads(sidecar.read_text())
            self.assertEqual(info['files']['input.bin'],sha256(root/'input.bin'))
            self.assertEqual(info['archive_sha256'],sha256(Path(d)/'archive.tar.gz'))
            with self.assertRaises(ValueError):
                archive_campaign.archive(root, root/'recursive.tar.gz')
            (root/'campaign.json').write_text(json.dumps({'tasks':[{'status':'running'}]}))
            with self.assertRaises(ValueError):
                archive_campaign.archive(root, Path(d)/'active.tar.gz')

    def test_default_schedule_and_paired_independent_seeds(self):
        tasks = formal_campaign.schedule(Path('/study'), Path('/a'), Path('/b'), 5, 100, 'all')
        self.assertEqual(len(tasks), 35)
        self.assertEqual(sum(t['expected_runs'] for t in tasks), 110)
        self.assertEqual(len({t['id'] for t in tasks}), 35)
        self.assertEqual({t['seed'] for t in tasks}, set(range(100, 105)))
        self.assertTrue(all(not Path(t['directory']).is_absolute() for t in tasks))
        netem = [t for t in tasks if t['id'].startswith('netem')]
        self.assertNotEqual(netem[0]['command'][netem[0]['command'].index('--modes')+1],
                            netem[1]['command'][netem[1]['command'].index('--modes')+1])

    def test_plan_is_not_a_measured_result_and_formal_minimum(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root / 'input'
            source.write_bytes(b'input')
            command = [sys.executable, str(ROOT / 'scripts/formal_campaign.py'), '--file', str(source),
                       '--output', str(root / 'plan'), '--plan-only']
            r = subprocess.run(command + ['--repeats', '1'], capture_output=True)
            self.assertEqual(r.returncode, 2)
            self.assertFalse((root / 'plan').exists())
            subprocess.run(command, check=True, capture_output=True)
            campaign = json.loads((root / 'plan/campaign.json').read_text())
            self.assertTrue(campaign['plan_only'])
            self.assertEqual(campaign['expected_runs'], 110)
            self.assertTrue(all(t['status'] == 'planned' for t in campaign['tasks']))

    def row(self, repeat, success=True):
        return {'mode': 'adaptive', 'scenario': 'loss', 'window': 16, 'source_bytes': 100,
                'source_sha256': 'file', 'sender_binary_sha256': 'sender', 'receiver_binary_sha256': 'receiver',
                'campaign_repeat': repeat, 'seed': repeat, 'success': success,
                'goodput_Bps': repeat * 100 if success else None, 'sender_seconds': 1,
                'retransmissions': 0}

    def test_statistics_retain_failures_and_suppress_single_sample_interval(self):
        row = report_campaign.summarize([self.row(1)])[0]
        self.assertIsNone(row['ci95_low_Bps'])
        self.assertFalse(row['enough_independent_successes'])
        row = report_campaign.summarize([self.row(i) for i in range(1, 6)] + [self.row(6, False)])[0]
        self.assertEqual(row['attempts'], 6)
        self.assertEqual(row['confirmed'], 5)
        self.assertAlmostEqual(row['completion_rate'], 5/6)
        self.assertEqual(row['mean_goodput_Bps'], 300)
        self.assertLess(row['ci95_low_Bps'], 300)
        self.assertGreater(row['ci95_high_Bps'], 300)
        with self.assertRaises(ValueError):
            report_campaign.summarize([self.row(1), self.row(1)])
        failed = report_campaign.summarize([self.row(1, False)])[0]
        self.assertIsNone(failed['mean_goodput_Bps'])

    def test_competition_namespace_boundary(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / 'file'
            source.write_bytes(b'x')
            argv = ['competition', '--file', str(source), '--output', str(Path(d)/'out'),
                    '--mode', 'adaptive', '--load-mbps', '7', '--seed', '1']
            with patch.object(sys, 'argv', argv), patch.object(run_competition.platform, 'system', return_value='Linux'), \
                    patch.object(run_competition.os, 'geteuid', return_value=0), \
                    patch.object(run_competition.shutil, 'which', return_value='/tool'), \
                    patch.object(run_competition.subprocess, 'call', return_value=0) as call:
                self.assertEqual(run_competition.main(), 0)
                self.assertEqual(call.call_args[0][0][:2], ['unshare', '--net'])
            original_readlink = run_competition.os.readlink
            def readlink(path, *args, **kwargs):
                if str(path).startswith('/proc/'):
                    return 'same-namespace'
                return original_readlink(path, *args, **kwargs)
            with patch.object(sys, 'argv', argv + ['--inside']), \
                    patch.object(run_competition.platform, 'system', return_value='Linux'), \
                    patch.object(run_competition.os, 'geteuid', return_value=0), \
                    patch.object(run_competition.shutil, 'which', return_value='/tool'), \
                    patch.object(run_competition.os, 'readlink', side_effect=readlink), \
                    patch.object(run_competition.subprocess, 'run') as mutation:
                with self.assertRaises(SystemExit):
                    run_competition.main()
                mutation.assert_not_called()

    def test_partial_data_and_parameter_tampering_are_detected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            runs = root/'part'/'runs'
            runs.mkdir(parents=True)
            trace = runs/'trace.csv'
            trace.write_text('original raw log')
            (runs/'metadata.json').write_text(json.dumps({'sender_sha256':'sender','receiver_sha256':'receiver'}))
            r = self.row(1)
            r.update(output_sha256='file', parameter_log='trace.csv', trace_sha256=sha256(trace))
            (runs/'runs.jsonl').write_text(json.dumps(r)+'\n')
            manifest = root/'campaign.json'
            manifest.write_text(json.dumps({'tasks':[{'id':'part','directory':'part','repeat':1,
                'seed':1,'expected_runs':16,'status':'finished','exit':0}]}))
            _, records, missing = report_campaign.load_campaign(manifest)
            self.assertEqual(len(records),1)
            self.assertEqual(missing[0]['recorded'],1)
            self.assertEqual(missing[0]['expected'],16)
            trace.write_text('tampered raw log')
            with self.assertRaises(ValueError):
                report_campaign.load_campaign(manifest)

    def test_paced_udp_generator_and_sink_keep_raw_counters(self):
        with tempfile.TemporaryDirectory() as d, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as picker:
            picker.bind(('127.0.0.1', 0))
            port = picker.getsockname()[1]
            picker.close()
            root = Path(d)
            processes = []
            try:
                for role in ('sink', 'send'):
                    command = [sys.executable, str(ROOT/'scripts/competition_traffic.py'), role,
                               '--host', '127.0.0.1', '--port', str(port), '--mbps', '1',
                               '--output', str(root/(role+'.json'))]
                    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                    processes.append(process)
                    with (root/(role+'.log')).open('wb') as log:
                        ready(process, log)
                time.sleep(.35)
            finally:
                for process in reversed(processes):
                    process.terminate()
                    process.communicate(timeout=5)
            offered = json.loads((root/'send.json').read_text())
            delivered = json.loads((root/'sink.json').read_text())
            self.assertGreater(offered['payload_bytes'], 0)
            self.assertGreater(delivered['payload_bytes'], 0)
            self.assertLessEqual(delivered['payload_bytes'], offered['payload_bytes'])
            self.assertTrue((root/'send.csv').is_file())


if __name__ == '__main__':
    unittest.main(verbosity=2)
