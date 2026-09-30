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

    def test_codex_command_is_read_only_and_default_model(self):
        cmd = r.command({'auditor': 'codex', 'model': 'opus', 'codex_model': ''})
        self.assertEqual(cmd[:2], ['codex', 'exec'])
        self.assertEqual(cmd[cmd.index('--sandbox') + 1], 'read-only')
        self.assertNotIn('--model', cmd)
        self.assertNotIn('opus', cmd)
        self.assertEqual(cmd[-1], '-')
        self.assertFalse(any(word in arg for arg in cmd for word in r.FORBIDDEN))
        self.assertIn('--model', r.command({'auditor': 'codex', 'codex_model': 'gpt-x'}))
        with self.assertRaises(ValueError):
            r.command({'auditor': 'other'})

    def test_codex_help_checked_on_exec_subcommand(self):
        cmd = r.command({'auditor': 'codex'})
        help_text = b'--sandbox [possible values: read-only] --ephemeral --ignore-user-config --ignore-rules --json'
        with patch.object(r.subprocess, 'check_output', return_value=help_text) as check:
            r.verify_help(cmd)
        self.assertEqual(check.call_args[0][0], ['codex', 'exec', '--help'])
        with patch.object(r.subprocess, 'check_output', return_value=help_text.replace(b'--json', b'')):
            with self.assertRaises(ValueError):
                r.verify_help(cmd)

    def test_codex_last_agent_message(self):
        events = [{'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'first'}},
                  {'type': 'item.completed', 'item': {'type': 'command_execution', 'text': 'x'}},
                  {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'last'}}]
        output = 'noise\n' + '\n'.join(json.dumps(e) for e in events)
        self.assertEqual(r.codex_message(output), 'last')
        with self.assertRaises(ValueError):
            r.codex_message('{"type": "turn.started"}')

    def test_existing_sections_must_be_kept(self):
        old = self.body() + '## 交代・担当台帳\n| 1 | a |\n'
        with patch.object(r, 'git', return_value='abc\n'):
            with self.assertRaises(ValueError):
                r.validate(r.BEGIN + '\n' + self.body() + r.END, 'abc', old)
            r.validate(r.BEGIN + '\n' + old + r.END, 'abc', old)

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

    def integration(self, action=None, rounds=3, memo=None, lock=False, runtime="isolated", auditor='claude', settings=None):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent) as tmp:
            root = Path(tmp)
            (root / 'relay').mkdir()
            (root / 'relay/config.example').write_text(json.dumps({'developer': 'Codex', 'auditor': auditor, 'model': 'opus', 'timeout_seconds': 1, 'max_rounds': rounds, 'runtime': runtime}))
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
                if auditor == 'codex':
                    return json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': r.BEGIN + '\n' + body + r.END}})
                return r.BEGIN + '\n' + body + r.END
            def fake_git(*args):
                return '' if args[0] in ('ls-files', 'diff') else 'abc\n'
            old_cwd = Path.cwd()
            try:
                with patch.object(r, 'ROOT', root), patch.object(r, 'STATE', state), patch.object(r, 'git', side_effect=fake_git), patch.object(r, 'verify_help'), patch.object(r, 'run_child', side_effect=child), patch.object(r.subprocess, 'check_call'), patch.object(r.sys, 'argv', ['b2b', 'call', 'audit']), patch.object(r, 'codex_settings', side_effect=settings or [{}, {}]):
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

    def test_codex_audit_written_and_logged(self):
        code, _, log, _ = self.integration(auditor='codex')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(log)['codex_settings_changes'], [])

    def test_codex_settings_change_is_violation(self):
        code, _, log, body = self.integration(auditor='codex', settings=[{'config.toml': 'a'}, {'config.toml': 'b'}])
        self.assertEqual(code, 30)
        self.assertEqual(body, self.body())
        self.assertEqual(json.loads(log)['codex_settings_changes'], ['config.toml'])

    def test_normal_on_nonmac_stops(self):
        with patch.object(r.sys, 'platform', 'linux'):
            code, _, _, _ = self.integration(runtime='normal')
        self.assertEqual(code, 20)

    @unittest.skipUnless(r.sandbox_usable(), 'macOS sandbox-exec required (not usable inside another sandbox)')
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


class ProofTests(unittest.TestCase):
    """swap/accept on a throwaway git repository inside this repository. confirm is never run as a CLI here:
    only its internal matching and refusal functions are tested."""

    FLAG = "import pathlib, sys; sys.exit(int(pathlib.Path('.b2b/flag').read_text()))"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent)
        root = self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.run_git('init', '-q')
        (root / '.gitignore').write_text('.b2b/\nHANDOFF.md\nAUDIT.md\n')
        (root / 'code.txt').write_text('v1')
        self.run_git('add', '.')
        self.run_git('commit', '-qm', 'init')
        head = self.run_git('rev-parse', 'HEAD').strip()
        (root / 'HANDOFF.md').write_text('# HANDOFF\n- 状態：SWAP-PREPARED（未照合）\n')
        (root / 'AUDIT.md').write_text(
            '# AUDIT\n## 監査の位置\n- 読了位置：' + head + '\n- 合格位置：' + head + '\n- 判定：問題なし\n'
            '## 指摘\n| B2B-001 | 高 | 内容 | 対応済み |\n| B2B-002 | 低 | 内容 | 未対応 |\n'
            '## 交代・担当台帳\n| 交代番号 | 指示者 |\n| --- | --- |\n| 1 | user |\n| 2 | user |\n')
        self.state = root / '.b2b'
        self.state.mkdir()
        (self.state / 'flag').write_text('0')
        for target in (patch.object(r, 'ROOT', root), patch.object(r, 'STATE', self.state)):
            target.start()
            self.addCleanup(target.stop)
        self.config = {'developer': 'Claude (Opus)', 'auditor': 'codex'}
        self.evidence, _, _ = r.run_recorded([r.sys.executable, '-B', '-c', self.FLAG], 30, 'verify')

    def run_git(self, *args):
        return subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@example.invalid',
                               '-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=/dev/null', *args],
                              cwd=self.root, check=True, capture_output=True, text=True).stdout

    def swap(self, **kwargs):
        return r.create_swap(self.config, 'user', ['branch commit'], ['publish once'], ['no push'], **kwargs)

    def kinds(self, number, answer):
        failures, _ = r.accept_checks(number, answer, 30)
        return {kind for kind, _ in failures}

    def test_evidence_record_has_command_exit_hash_time_head(self):
        row = r.read_jsonl('evidence.jsonl')[0]
        self.assertEqual((row['id'], row['exit_code'], row['recorder']), ('E1', 0, 'relay'))
        self.assertEqual(row['head'], self.run_git('rev-parse', 'HEAD').strip())
        self.assertEqual(len(row['output_sha256']), 64)
        self.assertRegex(row['time'], r'[+-]\d\d:\d\d$')
        self.assertFalse(row['worktree_changed'])

    def test_swap_number_exceeds_ledger_and_previous_records(self):
        first, _, _ = self.swap()
        second, _, _ = self.swap()
        self.assertEqual((first['number'], second['number'], second['previous_number']), (3, 4, 3))
        self.assertTrue((self.state / 'handoff-3.json').exists())

    def test_swap_record_contents(self):
        record, passphrase, answer = self.swap()
        text = (self.state / 'handoff-3.json').read_text()
        self.assertNotIn(passphrase, text)
        self.assertRegex(passphrase, r'^B2B-S03-\d{6}$')
        self.assertEqual(record['evidence'][0]['rerun'], True)
        self.assertEqual(record['audit']['open_findings'], {'B2B-002': '未対応'})
        self.assertEqual(record['permissions'], {'active': ['branch commit'], 'used': ['publish once']})
        self.assertEqual(record['promises'], ['no push'])
        self.assertIn('uncommitted_id', record['state'])
        self.assertTrue(record['state']['handoff_sha256'] and record['state']['audit_sha256'])

    def test_swap_requires_prepared_handoff_and_no_lock(self):
        (self.root / 'HANDOFF.md').write_text('# HANDOFF\n- 状態：開発中\n')
        with self.assertRaises(ValueError):
            self.swap()
        (self.root / 'HANDOFF.md').write_text('# HANDOFF\n- 状態：SWAP-PREPARED\n')
        (self.state / 'lock').mkdir()
        with self.assertRaises(ValueError):
            self.swap()

    def test_accept_all_conditions_met(self):
        record, passphrase, answer = self.swap()
        self.assertTrue(r.record_confirmation(3, passphrase, 'tty-test'))
        with patch('builtins.print') as shown:
            self.assertEqual(r.accept(3, answer, 30), 0)
        self.assertIn('ACCEPTED', ' '.join(str(c) for c in shown.call_args_list))
        self.assertEqual(r.read_jsonl('evidence.jsonl')[-1]['rerun_of'], 'E1')
        self.assertEqual(self.kinds(3, answer), {'accepted'})

    def test_fingerprint_mismatch_refused(self):
        _, passphrase, answer = self.swap()
        r.record_confirmation(3, passphrase, 'tty-test')
        (self.root / 'code.txt').write_text('v2')
        self.assertEqual(self.kinds(3, answer), {'fingerprint'})
        (self.root / 'code.txt').write_text('v1')
        (self.root / 'HANDOFF.md').write_text('# HANDOFF\n- 状態：ACCEPTED（勝手に）\n')
        self.assertEqual(self.kinds(3, answer), {'fingerprint'})

    def test_untracked_file_changes_fingerprint(self):
        _, passphrase, answer = self.swap()
        r.record_confirmation(3, passphrase, 'tty-test')
        (self.root / 'new.txt').write_text('new')
        self.assertEqual(self.kinds(3, answer), {'fingerprint'})

    def test_evidence_mismatch_refused(self):
        _, passphrase, answer = self.swap()
        r.record_confirmation(3, passphrase, 'tty-test')
        (self.state / 'flag').write_text('1')
        with patch('builtins.print'):
            self.assertEqual(r.accept(3, answer, 30), 30)
        self.assertEqual(self.kinds(3, answer), {'evidence'})

    def test_wrong_answer_refused(self):
        _, passphrase, answer = self.swap()
        r.record_confirmation(3, passphrase, 'tty-test')
        self.assertEqual(self.kinds(3, 'zzzzzzz 99'), {'answer'})
        self.assertEqual(self.kinds(3, '  ' + answer.upper() + ' '), set())

    def test_missing_confirm_refused(self):
        _, passphrase, answer = self.swap()
        self.assertEqual(self.kinds(3, answer), {'confirm'})
        self.assertFalse(r.record_confirmation(3, passphrase + 'x', 'tty-test'))
        with patch('builtins.print'):
            self.assertEqual(r.accept(3, answer, 30), 20)
        self.assertFalse((self.state / 'accepts.jsonl').exists())

    def test_confirm_for_older_record_does_not_count(self):
        _, first_pass, _ = self.swap()
        r.record_confirmation(3, first_pass, 'tty-test')
        _, _, answer = self.swap()
        self.assertIn('confirm', self.kinds(4, answer))
        self.assertIn('latest', self.kinds(3, answer))

    def test_side_effect_check_failure_refused(self):
        (self.state / 'pr').write_text('open')
        row = r.effect_add('PR作成', 'test -f .b2b/pr', 30, 'Claude (Opus)')
        self.assertEqual((row['id'], row['check_exit_code']), ('X1', 0))
        _, passphrase, answer = self.swap()
        r.record_confirmation(3, passphrase, 'tty-test')
        (self.state / 'pr').unlink()
        self.assertEqual(self.kinds(3, answer), {'effect'})

    def test_effect_check_rejects_approval_bypass(self):
        with self.assertRaises(ValueError):
            r.effect_add('x', 'codex exec --dangerously-bypass-approvals-and-sandbox', 30, 'x')

    def test_confirm_refuses_ai_and_non_terminal(self):
        self.assertIn('CLAUDECODE', r.human_terminal_refusal({'CLAUDECODE': '1'}, True, True))
        self.assertIn('CODEX_CI', r.human_terminal_refusal({'CODEX_CI': '1'}, True, True))
        self.assertIsNotNone(r.human_terminal_refusal({}, False, True))
        self.assertIsNone(r.human_terminal_refusal({'HOME': '/x'}, True, True))


if __name__ == '__main__':
    unittest.main()
