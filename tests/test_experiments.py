"""Experiment orchestration contracts, plus actual fixed/adaptive C transfers."""
import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import run_experiments as runner
import run_netem


class ExperimentTests(unittest.TestCase):
    def test_adaptive_silence_and_control_budgets(self):
        fixed = runner.timing('fixed', 1000, 5)
        adaptive = runner.timing('adaptive', 1000, 5)
        self.assertEqual(fixed['receiver_idle_ms'], 15000)
        self.assertEqual(fixed['max_seconds'], 600)
        self.assertEqual(adaptive['receiver_idle_ms'], 361000)
        self.assertEqual(adaptive['max_seconds'], 1800)
        self.assertEqual(adaptive['receiver_linger_ms'], fixed['receiver_linger_ms'])
        self.assertEqual(runner.timing('adaptive', 1000, 8)['receiver_idle_ms'], 541000)
        with self.assertRaises(ValueError):
            runner.timing('adaptive', 1000, 9)
        self.assertEqual(runner.timing('adaptive', 1000, 5, 2.5)['max_seconds'], 2.5)

    def test_netem_forwards_modes_and_timeout_through_namespace(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'input'
            source.write_bytes(b'hello')
            argv = ['netem', '--file', str(source), '--output', str(Path(directory) / 'out'),
                    '--modes', 'fixed', 'adaptive', '--timeout-ms', '2000', '--retries', '2',
                    '--max-seconds', '1801', '--keep-files']
            with patch.object(sys, 'argv', argv), patch.object(run_netem.platform, 'system', return_value='Linux'), \
                    patch.object(run_netem.os, 'geteuid', return_value=0), \
                    patch.object(run_netem.shutil, 'which', return_value='/tool'), \
                    patch.object(run_netem.subprocess, 'call', return_value=0) as child:
                self.assertEqual(run_netem.main(), 0)
            command = child.call_args[0][0]
            self.assertEqual(command[:2], ['unshare', '--net'])
            for flag, value in [('--timeout-ms', '2000'), ('--retries', '2'), ('--max-seconds', '1801.0')]:
                self.assertEqual(command[command.index(flag) + 1], value)
            i = command.index('--modes')
            self.assertEqual(command[i + 1:i + 3], ['fixed', 'adaptive'])
            self.assertIn('--keep-files', command)

    def test_invalid_adaptive_config_fails_before_creating_results(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'out'
            for extra in (['--timeout-ms', '120'], ['--retries', '9'], ['--max-seconds', 'nan']):
                r = subprocess.run([sys.executable, str(ROOT / 'scripts/run_experiments.py'),
                                    '--file', 'nonexistent', '--output', str(out),
                                    '--modes', 'adaptive', *extra], capture_output=True)
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertFalse(out.exists())

    def test_summary_separates_modes_and_supports_legacy_records(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = {'scenario': 'normal', 'window': 16, 'source_bytes': 10,
                        'success': True, 'sender_seconds': 1, 'goodput_Bps': 10, 'retransmissions': 0}
            records = [baseline, dict(baseline, mode='fixed'),
                       dict(baseline, mode='adaptive', success=False)]
            source = root / 'runs.jsonl'
            source.write_text(''.join(json.dumps(r) + '\n' for r in records))
            subprocess.run([sys.executable, str(ROOT / 'scripts/summarize_results.py'), str(source),
                            '--output', str(root / 'summary')], check=True, capture_output=True)
            with (root / 'summary/summary.csv').open() as f:
                groups = {r['mode']: r for r in csv.DictReader(f)}
            self.assertEqual(groups['fixed']['attempts'], '2')
            self.assertEqual(groups['adaptive']['completion_rate'], '0.0')
            self.assertEqual(groups['adaptive']['mean_goodput_Bps'], '')

    def test_trace_schema_and_empty_or_malformed_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / 'bad.csv'
            trace.write_text('schema_version,event\n2,begin\n')
            with self.assertRaises(ValueError):
                runner.trace_info(trace, 'adaptive')
            trace.write_text('')
            with self.assertRaises(ValueError):
                runner.trace_info(trace, 'fixed')

    def test_real_modes_save_separate_parameter_logs_and_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.write_bytes(bytes(range(256)) * 9)
            out = root / 'results'
            command = [sys.executable, str(ROOT / 'scripts/run_experiments.py'), '--file', str(source),
                       '--output', str(out), '--modes', 'fixed', 'adaptive', '--windows', '1', '8',
                       '--repeats', '1', '--retries', '0', '--sender', str(ROOT / 'build/rudp-send'),
                       '--receiver', str(ROOT / 'build/rudp-recv')]
            r = subprocess.run(command, capture_output=True, text=True, timeout=40)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            records = [json.loads(line) for line in (out / 'runs.jsonl').read_text().splitlines()]
            self.assertEqual(len(records), 4)
            self.assertEqual(len({r['run_id'] for r in records}), 4)
            for record in records:
                mode = record['mode']
                self.assertTrue(record['success'])
                self.assertEqual(record['source_sha256'], record['output_sha256'])
                trace = out / record['parameter_log']
                self.assertEqual(record['trace_schema'], 2 if mode == 'adaptive' else 1)
                self.assertEqual(runner.sha256(trace), record['trace_sha256'])
                self.assertEqual('--adaptive' in record['sender_command'], mode == 'adaptive')
                self.assertIn('--trace', record['sender_command'])
                self.assertGreater(record['trace_rows'], 2)
                self.assertEqual(record['timing']['max_seconds'], 1800 if mode == 'adaptive' else 600)

    def test_wall_timeout_is_saved_as_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.write_bytes(b'x' * (10 * 1024 * 1024))
            out = root / 'results'
            r = subprocess.run([sys.executable, str(ROOT / 'scripts/run_experiments.py'),
                                '--file', str(source), '--output', str(out), '--modes', 'adaptive',
                                '--windows', '1', '--repeats', '1', '--max-seconds', '0.001',
                                '--sender', str(ROOT / 'build/rudp-send'),
                                '--receiver', str(ROOT / 'build/rudp-recv')],
                               capture_output=True, timeout=15)
            self.assertEqual(r.returncode, 1, r.stderr)
            record = json.loads((out / 'runs.jsonl').read_text())
            self.assertFalse(record['success'])
            self.assertTrue(record['timed_out'])
            self.assertEqual(record['timeout_phase'], 'sender')
            self.assertGreater(record['sender_seconds'], 0)
            self.assertIsNone(record['goodput_Bps'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
