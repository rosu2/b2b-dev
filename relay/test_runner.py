import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('runner', Path(__file__).with_name('runner.py'))
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


class RelayTests(unittest.TestCase):
    def body(self, state='対応済み', verdict='問題なし'):
        return ('# AUDIT\n## 監査の位置\n- 読了位置：abc\n- 合格位置：abc\n- 判定：'
                + verdict + '\n## 指摘\n| B2B-007 | 中 | 内容 | ' + state
                + ' |\n## 未解決・ユーザーに回す判断\n## 監査の記録\n')

    def test_valid(self):
        body = self.body()
        with patch.object(r, 'git', return_value='abc\n'):
            result = r.validate(r.BEGIN + '\n' + body + r.END, 'abc', body)
        self.assertEqual(result[2], 0)

    def test_open_cannot_pass(self):
        body = self.body('未対応')
        with patch.object(r, 'git', return_value='abc\n'):
            with self.assertRaises(ValueError):
                r.validate(r.BEGIN + '\n' + body + r.END, 'abc', body)

    def test_missing_finding(self):
        old = self.body()
        body = old.replace('| B2B-007 | 中 | 内容 | 対応済み |', '')
        with patch.object(r, 'git', return_value='abc\n'):
            with self.assertRaises(ValueError):
                r.validate(r.BEGIN + '\n' + body + r.END, 'abc', old)

    def test_boundary_rejected(self):
        with self.assertRaises(ValueError):
            r.validate('wrong', 'abc', '')

    def test_findings_with_escaped_pipe(self):
        self.assertEqual(r.findings('| B2B-007 | 中 | a \\| b | 未対応 |'), {'B2B-007': '未対応'})

    def test_codex_stops_for_user(self):
        with self.assertRaises(ValueError):
            r.command({'auditor': 'codex'})

    def test_timeout(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            r.run_child(['python3', '-c', 'import time; time.sleep(5)'], '', 0.05, None)

    def test_snapshot_includes_untracked_and_ignores_runtime(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent) as tmp:
            root = Path(tmp)
            with patch.object(r, 'ROOT', root):
                (root / '.b2b').mkdir()
                (root / '.b2b/log').write_text('runtime')
                (root / 'new').write_text('before')
                first = r.snapshot()
                (root / 'new').write_text('after')
                self.assertNotEqual(first, r.snapshot())
                self.assertNotIn('.b2b/log', first)

    def integration(self, action=None, rounds=3, memo=None, lock=False, runtime="isolated"):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent) as tmp:
            root = Path(tmp)
            (root / 'relay').mkdir()
            (root / 'relay/config.example').write_text(json.dumps({'developer': 'Codex', 'auditor': 'claude', 'model': 'opus', 'timeout_seconds': 1, 'max_rounds': rounds, 'runtime': runtime}))
            (root / 'AUDIT.md').write_text(self.body())
            (root / 'HANDOFF.md').write_text('handoff')
            state = root / '.b2b'
            state.mkdir()
            if memo:
                (state / 'state.json').write_text(json.dumps(memo))
            if lock:
                (state / 'lock').mkdir()
            def child(*args):
                if action == 'audit-violation':
                    (root / 'AUDIT.md').write_text('unauthorized')
                if action == 'violation':
                    (root / 'new').write_text('violation')
                body = self.body('未対応', '直してから次へ') if action == 'reopen' else self.body()
                return r.BEGIN + '\n' + body + r.END
            def fake_git(*args):
                return '' if args[0] in ('ls-files', 'diff') else 'abc\n'
            old_cwd = Path.cwd()
            try:
                with patch.object(r, 'ROOT', root), patch.object(r, 'STATE', state), patch.object(r, 'git', side_effect=fake_git), patch.object(r, 'verify_help'), patch.object(r, 'run_child', side_effect=child), patch.object(r.subprocess, 'check_call'), patch.object(r.sys, 'argv', ['b2b', 'call', 'audit']):
                    first = r.main()
                    second = r.main() if action == 'limit' else None
                if not lock:
                    self.assertFalse((state / 'lock').exists())
                log = (state / 'log.jsonl').read_text() if (state / 'log.jsonl').exists() else ''
                body = (root / 'AUDIT.md').read_text()
                return first, second, log, body
            finally:
                import os
                os.chdir(old_cwd)

    def test_actual_result_written_and_logged(self):
        code, _, log, _ = self.integration()
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(log)['unexpected_changes'], [])

    def test_violation_prevents_audit_update(self):
        code, _, log, body = self.integration('violation')
        self.assertEqual(code, 30)
        self.assertEqual(body, self.body())
        self.assertEqual(json.loads(log)['unexpected_changes'], ['new'])

    def test_round_limit(self):
        first, second, _, _ = self.integration('limit', rounds=1)
        self.assertEqual((first, second), (0, 20))

    def test_second_reopen_stops(self):
        memo = {'last': {'B2B-007': '対応済み'}, 'reopens': {'B2B-007': 1}}
        code, _, _, body = self.integration('reopen', memo=memo)
        self.assertEqual(code, 20)
        self.assertIn('- 判定：要ユーザー判断', body)

    def test_duplicate_lock(self):
        code, _, _, _ = self.integration(lock=True)
        self.assertEqual(code, 30)

    def test_nonpass_cannot_advance_passed_position(self):
        body = self.body('未対応', '直してから次へ').replace('- 合格位置：abc', '- 合格位置：def')
        with patch.object(r, 'git', return_value='abc\n'):
            with self.assertRaises(ValueError):
                r.validate(r.BEGIN + '\n' + body + r.END, 'abc', self.body())

    def test_audit_direct_write_restored_and_logged(self):
        code, _, log, body = self.integration('audit-violation')
        self.assertEqual(code, 30)
        self.assertEqual(body, self.body())
        self.assertIn('AUDIT.md', json.loads(log)['unexpected_changes'])

    def test_snapshot_failure_releases_lock(self):
        with patch.object(r, 'snapshot', side_effect=OSError('unreadable')):
            code, _, log, _ = self.integration()
        self.assertEqual(code, 30)
        self.assertEqual(json.loads(log)['comparison_error'], 'OSError')

    def test_normal_on_nonmac_stops(self):
        with patch.object(r.sys, 'platform', 'linux'):
            code, _, _, _ = self.integration(runtime='normal')
        self.assertEqual(code, 20)

    @unittest.skipUnless(r.sys.platform == 'darwin', 'macOS sandbox required')
    def test_normal_policy_allows_cache_and_denies_settings(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent) as tmp:
            normal = Path(tmp)
            policy = r.sandbox_policy(normal)
            for name in ('skills', 'agents', 'commands', 'plugins', 'hooks', 'rules'):
                (normal / name).mkdir()
            targets = ['CLAUDE.md', 'keybindings.json', 'settings.json', '.claude.json']
            targets += [name + '/test' for name in ('skills', 'agents', 'commands', 'plugins', 'hooks', 'rules')]
            for target in targets:
                path = normal / target
                code = 'from pathlib import Path; Path(' + repr(str(path)) + ').write_text("x")'
                result = subprocess.run(['/usr/bin/sandbox-exec', '-p', policy, 'python3', '-B', '-c', code], capture_output=True)
                self.assertNotEqual(result.returncode, 0, target)
                self.assertFalse(path.exists(), target)
            cache = normal / 'cache'
            code = 'from pathlib import Path; Path(' + repr(str(cache)) + ').write_text("x")'
            result = subprocess.run(['/usr/bin/sandbox-exec', '-p', policy, 'python3', '-B', '-c', code], capture_output=True)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(cache.read_text(), 'x')


if __name__ == '__main__':
    unittest.main()
