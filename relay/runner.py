"""Optional, local B2B audit relay. Python standard library only."""
import datetime
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / '.b2b'
BEGIN = 'B2B_AUDIT_BEGIN'
END = 'B2B_AUDIT_END'
FORBIDDEN = ('dangerously', 'bypassPermissions', '--approve-for-me', 'danger-full-access',
             'workspace-write', '--full-auto', '--yolo')
# Codex settings that change later behaviour. The CLI's own state (sessions, logs, caches, auth) is allowed.
CODEX_PROTECTED = ['config.toml', 'AGENTS.md', 'AGENTS.override.md', 'hooks.json',
                   'rules', 'skills', 'agents', 'plugins', 'prompts']
# Environment markers set by AI agent shells. confirm refuses to run under any of them.
AI_ENV = ('CLAUDECODE', 'CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_CODE_SESSION_ID', 'CODEX_CI',
          'CODEX_SESSION_ID', 'CODEX_THREAD_ID', 'CODEX_SANDBOX', 'CODEX_SANDBOX_NETWORK_DISABLED')
FINGERPRINT = ('head', 'branch', 'uncommitted_id', 'handoff_sha256', 'audit_sha256')


class UserDecision(RuntimeError):
    pass


def git_bytes(*args):
    return subprocess.check_output(['git', '--no-optional-locks', *args], cwd=ROOT)


def git(*args):
    return git_bytes(*args).decode()


def local_time():
    now = datetime.datetime.now().astimezone()
    return now.isoformat(timespec='seconds'), now.tzname()


def position(text, label):
    match = re.search(r'^- ' + label + r'[^：\n]*：\s*([^\n]+)', text, re.M)
    return match.group(1).strip() if match else ''


def findings(text):
    result = {}
    for line in text.splitlines():
        cells = re.split(r'(?<!\\)\|', line)
        if len(cells) >= 6 and re.fullmatch(r'B2B-\d+', cells[1].strip()):
            result[cells[1].strip()] = cells[-2].strip()
    return result


def is_open(value):
    return value in ('未対応', 'ユーザー判断', 'ユーザー判断待ち')


def headings(text):
    return [line for line in text.splitlines() if line.startswith('## ')]


def snapshot():
    """Include ignored/untracked files and git metadata; exclude only relay runtime."""
    result = {}
    for folder, dirs, files in os.walk(ROOT, followlinks=False):
        if Path(folder) == ROOT:
            dirs[:] = [d for d in dirs if d != '.b2b']
        for name in dirs[:] + files:
            path = Path(folder) / name
            rel = str(path.relative_to(ROOT))
            if path.is_symlink():
                result[rel] = 'link:' + os.readlink(path)
                if name in dirs:
                    dirs.remove(name)
            elif path.is_file():
                result[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def codex_settings(home):
    """Digest Codex settings files so a change made during the audit is detected."""
    result = {}
    for name in CODEX_PROTECTED + sorted(p.name for p in home.glob('*.config.toml')):
        path = home / name
        if path.is_symlink():
            result[name] = 'link:' + os.readlink(path)
        elif path.is_file():
            result[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        elif path.is_dir():
            for folder, dirs, files in os.walk(path, followlinks=False):
                if Path(folder) == home / 'skills':
                    dirs[:] = [d for d in dirs if d != '.system']  # bundled by the CLI itself
                for item in dirs[:] + files:
                    child = Path(folder) / item
                    rel = str(child.relative_to(home))
                    if child.is_symlink():
                        result[rel] = 'link:' + os.readlink(child)
                    elif child.is_file():
                        result[rel] = hashlib.sha256(child.read_bytes()).hexdigest()
    return result


def sandbox_policy(normal):
    quote = lambda path: json.dumps(str(path))
    protected = ['CLAUDE.md', 'keybindings.json', 'settings.json', 'settings.local.json', '.claude.json']
    directories = ['skills', 'agents', 'commands', 'plugins', 'hooks', 'rules']
    policy = ('(version 1)(allow default)(deny file-write*)'
              '(allow file-write* (subpath ' + quote(normal) + ')'
              ' (subpath ' + quote(STATE) + ') (literal "/dev/null"))'
              '(deny file-write* (literal ' + quote(Path.home() / '.claude.json') + ')')
    policy += ''.join(' (literal ' + quote(normal / name) + ')' for name in protected)
    policy += ''.join(' (subpath ' + quote(normal / name) + ')' for name in directories)
    return policy + ' (regex #".*/settings(\\.local)?\\.json$"))'


def sandbox_usable():
    probe = ['/usr/bin/sandbox-exec', '-p', '(version 1)(allow default)', '/usr/bin/true']
    return sys.platform == 'darwin' and Path(probe[0]).exists() and subprocess.run(probe, capture_output=True).returncode == 0


def command(config):
    cli = config['auditor']
    if cli == 'claude':
        return ['claude', '-p', '--model', config['model'], '--tools', '',
                '--permission-mode', 'plan', '--no-session-persistence',
                '--safe-mode', '--setting-sources', '', '--strict-mcp-config',
                '--mcp-config', '{"mcpServers":{}}']
    if cli == 'codex':
        # read-only: model shell commands cannot write. No -m unless codex_model is set: CLI default model.
        cmd = ['codex', 'exec', '--sandbox', 'read-only', '--ephemeral',
               '--ignore-user-config', '--ignore-rules', '--json']
        if config.get('codex_model'):
            cmd += ['--model', config['codex_model']]
        return cmd + ['-']
    raise ValueError('auditorはclaudeまたはcodex')


def verify_help(cmd):
    head = cmd[:2] if cmd[0] == 'codex' else cmd[:1]
    help_text = subprocess.check_output([*head, '--help'], cwd=ROOT).decode()
    rest = cmd[len(head):]
    for index, arg in enumerate(rest):
        if arg.startswith('-') and arg != '-' and arg not in help_text:
            raise ValueError('実機ヘルプにないオプション: ' + arg)
        if index and rest[index - 1] == '--sandbox' and arg not in help_text:
            raise ValueError('実機ヘルプにないsandbox値: ' + arg)


def codex_message(output):
    text = None
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        item = event.get('item') or {}
        if event.get('type') == 'item.completed' and item.get('type') == 'agent_message':
            text = item.get('text')
    if text is None:
        raise ValueError('Codexの最終メッセージがない')
    return text


def run_child(cmd, prompt, timeout, env):
    child = subprocess.Popen(cmd, cwd=ROOT, stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, env=env, start_new_session=True)
    try:
        out, err = child.communicate(prompt, timeout=timeout)
        if child.returncode:
            if 'not logged in' in (out + err).lower():
                raise UserDecision('監査役CLIで認証が必要。設定は変更せずユーザー判断で停止。')
            # Do not expose stderr that could include credentials or private settings.
            raise RuntimeError('CLI失敗: exit=' + str(child.returncode))
        return out
    finally:
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
        for pipe in (child.stdin, child.stdout, child.stderr):
            if pipe is not None:
                pipe.close()


def validate(output, head, old):
    if output.count(BEGIN) != 1 or output.count(END) != 1:
        raise ValueError('AUDIT出力の境界が不正')
    body = output.split(BEGIN, 1)[1].split(END, 1)[0].strip() + '\n'
    if not body.startswith('# AUDIT'):
        raise ValueError('AUDIT見出しが不正')
    for section in ('## 監査の位置', '## 指摘', '## 未解決・ユーザーに回す判断', '## 監査の記録'):
        if section not in body:
            raise ValueError('AUDIT必須節がない: ' + section)
    missing = [h for h in headings(old) if h not in headings(body)]
    if missing:
        raise ValueError('既存の節が出力から消えている: ' + ', '.join(missing))
    read = position(body, '読了位置').split()[0]
    if git('rev-parse', '--verify', read + '^{commit}').strip() != head:
        raise ValueError('読了位置が監査対象HEADと違う')
    verdict = position(body, '判定')
    codes = {'問題なし': 0, '直してから次へ': 10, '要ユーザー判断': 20}
    if verdict not in codes:
        raise ValueError('判定が不正')
    current = findings(body)
    if not set(findings(old)).issubset(current):
        raise ValueError('既存指摘が出力から消えている')
    allowed = {'未対応', 'ユーザー判断待ち', '対応済み', '受け入れ', '却下', 'ユーザー決定済み'}
    if any(state not in allowed for state in current.values()):
        raise ValueError('指摘の状態が不正')
    if codes[verdict] != 0 and position(body, '合格位置').split()[0] != position(old, '合格位置').split()[0]:
        raise ValueError('非合格の判定で合格位置が変わっている')
    if codes[verdict] == 0:
        if any(is_open(s) for s in current.values()):
            raise ValueError('開いた指摘があるのに合格')
        passed = position(body, '合格位置').split()[0]
        if git('rev-parse', '--verify', passed + '^{commit}').strip() != head:
            raise ValueError('合格位置がHEADと違う')
    return body, verdict, codes[verdict], current


def read_jsonl(name):
    path = STATE / name
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def append_jsonl(name, row):
    STATE.mkdir(mode=0o700, exist_ok=True)
    with (STATE / name).open('a') as stream:
        stream.write(json.dumps(row, ensure_ascii=False) + '\n')


def file_sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def worktree_state():
    """Machine-measured identity of the working state (SKILL rule 8, plus the ignored HANDOFF/AUDIT)."""
    digest = hashlib.sha256(git_bytes('diff', 'HEAD', '--binary'))
    for raw in sorted(p for p in git_bytes('ls-files', '--others', '--exclude-standard', '-z').split(b'\0') if p):
        path = ROOT / raw.decode()
        data = os.readlink(path).encode() if path.is_symlink() else path.read_bytes()
        digest.update(raw + b'\0' + hashlib.sha256(data).digest())
    return {'head': git('rev-parse', 'HEAD').strip(),
            'branch': git('branch', '--show-current').strip(),
            'uncommitted_id': digest.hexdigest(),
            'uncommitted_files': git('status', '--porcelain', '--untracked-files=all').splitlines(),
            'handoff_sha256': file_sha256(ROOT / 'HANDOFF.md'),
            'audit_sha256': file_sha256(ROOT / 'AUDIT.md')}


def run_recorded(argv, timeout, purpose, rerun_of=None):
    """Run a command and append the machine record to .b2b/evidence.jsonl."""
    before = worktree_state()
    started, zone = local_time()
    try:
        done = subprocess.run(argv, cwd=ROOT, capture_output=True, timeout=timeout)
        code, out, err = done.returncode, done.stdout, done.stderr
    except subprocess.TimeoutExpired as error:
        code, out, err = None, error.stdout or b'', error.stderr or b''
    after = worktree_state()
    row = {'id': 'E' + str(len(read_jsonl('evidence.jsonl')) + 1), 'time': started, 'timezone': zone,
           'command': argv, 'exit_code': code, 'timed_out': code is None,
           'output_sha256': hashlib.sha256(b'stdout\0' + out + b'\0stderr\0' + err).hexdigest(),
           'output_bytes': len(out) + len(err), 'head': before['head'], 'branch': before['branch'],
           'uncommitted_id': before['uncommitted_id'],
           'worktree_changed': any(before[k] != after[k] for k in FINGERPRINT),
           'recorder': 'relay', 'purpose': purpose, 'rerun_of': rerun_of}
    append_jsonl('evidence.jsonl', row)
    return row, out, err


def check_effect(check, timeout):
    try:
        done = subprocess.run(['/bin/sh', '-c', check], cwd=ROOT, capture_output=True, timeout=timeout)
        return done.returncode, done.stdout + done.stderr
    except subprocess.TimeoutExpired:
        return None, b''


def effect_add(description, check, timeout, recorder):
    if any(word in check for word in FORBIDDEN):
        raise ValueError('承認を省くオプションは禁止')
    code, out = check_effect(check, timeout)
    stamp, zone = local_time()
    state = worktree_state()
    row = {'id': 'X' + str(len(read_jsonl('side-effects.jsonl')) + 1), 'time': stamp, 'timezone': zone,
           'description': description, 'check': check, 'check_exit_code': code,
           'check_output_sha256': hashlib.sha256(out).hexdigest(),
           'head': state['head'], 'branch': state['branch'], 'recorder': recorder}
    append_jsonl('side-effects.jsonl', row)
    return row


def ledger_numbers(audit):
    if '## 交代・担当台帳' not in audit:
        return []
    section = audit.split('## 交代・担当台帳', 1)[1].split('\n## ', 1)[0]
    numbers = []
    for line in section.splitlines():
        cells = line.split('|')
        if len(cells) >= 3 and cells[1].strip().isdigit():
            numbers.append(int(cells[1].strip()))
    return numbers


def handoff_numbers():
    return sorted(int(m.group(1)) for p in STATE.glob('handoff-*.json')
                  if (m := re.fullmatch(r'handoff-(\d+)\.json', p.name)))


def next_swap_number(audit):
    numbers = handoff_numbers() + ledger_numbers(audit)
    numbers += [row['switch'] for row in read_jsonl('log.jsonl') if isinstance(row.get('switch'), int)]
    return max(numbers, default=0) + 1


def salted(salt, text):
    return hashlib.sha256((salt + '\0' + text).encode()).hexdigest()


def normalize(answer):
    return ' '.join(answer.split()).lower()


def resolve(ref):
    try:
        return git('rev-parse', '--verify', ref.split()[0] + '^{commit}').strip()
    except (subprocess.CalledProcessError, IndexError):
        return None


def questions(state, read, passed, open_count, marked, effects):
    result = [('HEADのコミットSHAの先頭7文字と、再実行印つき証拠の数を空白区切りで',
               state['head'][:7] + ' ' + str(len(marked)))]
    if passed:
        result.append(('合格位置のコミットSHAの先頭7文字と、開いた指摘の数を空白区切りで',
                       passed[:7] + ' ' + str(open_count)))
    if read:
        result.append(('読了位置のコミットSHAの先頭7文字と、記録された外部副作用の数を空白区切りで',
                       read[:7] + ' ' + str(len(effects))))
    return result


def running_relays():
    try:
        rows = subprocess.check_output(['ps', '-Ao', 'pid=,command='], text=True).splitlines()
    except (OSError, subprocess.CalledProcessError):
        return None
    return [row.strip() for row in rows
            if 'relay/runner.py' in row and row.split()[0] != str(os.getpid())]


# Baton touch (planned) and relief (emergency) both end in ACCEPTED through the same swap checks.
PREPARED_STATES = ('BATON-READY', 'SWAP-PREPARED')


def create_swap(config, by, granted=(), used=(), promises=(), rerun=None):
    """Write .b2b/handoff-<n>.json. Returns (record, passphrase, answer); the CLI never prints the answer."""
    handoff = (ROOT / 'HANDOFF.md').read_text() if (ROOT / 'HANDOFF.md').exists() else ''
    if not any(name in position(handoff, '状態') for name in PREPARED_STATES):
        raise ValueError('HANDOFFの状態がBATON-READYでもSWAP-PREPAREDでもない。先に降りる開発役が保存する')
    if (STATE / 'lock').exists():
        raise ValueError('.b2b/lockがある。監査の実行中に交代しない')
    audit = (ROOT / 'AUDIT.md').read_text()
    state = worktree_state()
    evidence = read_jsonl('evidence.jsonl')
    if rerun is None:
        latest = {}
        for row in evidence:
            if row.get('head') == state['head'] and row.get('uncommitted_id') == state['uncommitted_id']:
                latest[json.dumps(row['command'])] = row['id']
        rerun = sorted(latest.values(), key=lambda i: int(i[1:]))
    known = {row['id'] for row in evidence}
    if set(rerun) - known:
        raise ValueError('存在しない証拠: ' + ', '.join(sorted(set(rerun) - known)))
    effects = read_jsonl('side-effects.jsonl')
    items = findings(audit)
    open_items = {key: value for key, value in items.items() if is_open(value)}
    read, passed = resolve(position(audit, '読了位置')), resolve(position(audit, '合格位置'))
    number = next_swap_number(audit)
    passphrase = 'B2B-S%02d-%06d' % (number, secrets.randbelow(10 ** 6))
    question, answer = secrets.choice(questions(state, read, passed, len(open_items), rerun, effects))
    salt = secrets.token_hex(16)
    stamp, zone = local_time()
    record = {
        'number': number, 'previous_number': number - 1,
        'time': stamp, 'timezone': zone, 'instructed_by': by, 'issued_by': 'relay',
        'before': {'developer': config['developer'], 'auditor': config['auditor']},
        'after': {'developer': config['auditor'], 'auditor': config['developer']},
        'state': state,
        'audit': {'read': read, 'passed': passed, 'open_findings': open_items, 'findings_total': len(items)},
        'evidence': [dict(row, rerun=row['id'] in rerun) for row in evidence],
        'side_effects': effects,
        'permissions': {'active': list(granted), 'used': list(used)},
        'promises': list(promises),
        'lock_present': False, 'running_relays': running_relays(),
        'passphrase_salt': salt, 'passphrase_sha256': salted(salt, passphrase),
        'question': question, 'answer_sha256': salted(salt, normalize(answer)),
    }
    STATE.mkdir(mode=0o700, exist_ok=True)
    with (STATE / ('handoff-%d.json' % number)).open('x') as stream:
        stream.write(json.dumps(record, ensure_ascii=False, indent=2) + '\n')
    append_jsonl('log.jsonl', {'time': stamp, 'timezone': zone, 'action': 'swap', 'switch': number,
                               'caller': config['developer'], 'instructed_by': by,
                               'rerun': rerun, 'side_effects': len(effects)})
    return record, passphrase, answer


def load_swap(number):
    path = STATE / ('handoff-%d.json' % number)
    if not path.exists():
        raise ValueError('交代記録がない: ' + path.name)
    return json.loads(path.read_text()), file_sha256(path)


def human_terminal_refusal(env, stdin_tty, stdout_tty):
    """confirm is for the user at a terminal. Returns a reason to refuse, or None."""
    markers = [name for name in AI_ENV if env.get(name)]
    if markers:
        return 'AIエージェントの環境からは実行できない（' + ', '.join(markers) + '）'
    if not (stdin_tty and stdout_tty):
        return '対話terminalからだけ実行できる'
    return None


def passphrase_matches(record, passphrase):
    return hmac.compare_digest(salted(record['passphrase_salt'], passphrase.strip()), record['passphrase_sha256'])


def record_confirmation(number, passphrase, tty):
    record, digest = load_swap(number)
    stamp, zone = local_time()
    matched = passphrase_matches(record, passphrase)
    append_jsonl('log.jsonl', {'time': stamp, 'timezone': zone, 'action': 'confirm', 'switch': number,
                               'matched': matched})
    if matched:
        append_jsonl('confirmations.jsonl', {'number': number, 'time': stamp, 'timezone': zone,
                                             'handoff_file_sha256': digest, 'tty': tty})
    return matched


def accept_checks(number, answer, timeout):
    """Return (failures, reruns). Every failure blocks ACCEPTED; nothing here writes HANDOFF."""
    record, digest = load_swap(number)
    failures = []
    if handoff_numbers()[-1] != number:
        failures.append(('latest', '最新の交代記録ではない'))
    if any(row.get('number') == number for row in read_jsonl('accepts.jsonl')):
        failures.append(('accepted', 'すでに受け入れ済み'))
    if not any(row.get('number') == number and row.get('handoff_file_sha256') == digest
               for row in read_jsonl('confirmations.jsonl')):
        failures.append(('confirm', 'ユーザーのconfirmがない（ユーザー本人がterminalで実行する）'))
    if (STATE / 'lock').exists():
        failures.append(('lock', '.b2b/lockがある'))
    state = worktree_state()
    for key in FINGERPRINT:
        if state[key] != record['state'][key]:
            failures.append(('fingerprint', '状態指紋の不一致: ' + key))
    if not hmac.compare_digest(salted(record['passphrase_salt'], normalize(answer)), record['answer_sha256']):
        failures.append(('answer', '確認の問いの答えが違う'))
    reruns = []
    if failures:
        return failures, reruns
    for row in (r for r in record['evidence'] if r['rerun']):
        result, _, _ = run_recorded(row['command'], timeout, 'accept %d' % number, row['id'])
        reruns.append(result)
        if result['exit_code'] != row['exit_code']:
            failures.append(('evidence', '%s の再実行終了コード %s ≠ 記録 %s'
                             % (row['id'], result['exit_code'], row['exit_code'])))
    for row in record['side_effects']:
        code, _ = check_effect(row['check'], timeout)
        if code != 0:
            failures.append(('effect', '%s の確認コマンドが失敗（終了 %s）' % (row['id'], code)))
    after = worktree_state()
    if any(after[key] != state[key] for key in FINGERPRINT):
        failures.append(('fingerprint', '再実行中に状態が変わった'))
    return failures, reruns


def accept(number, answer, timeout):
    failures, reruns = accept_checks(number, answer, timeout)
    stamp, zone = local_time()
    record, _ = load_swap(number)
    append_jsonl('log.jsonl', {'time': stamp, 'timezone': zone, 'action': 'accept', 'switch': number,
                               'accepted': not failures, 'failures': [kind for kind, _ in failures],
                               'reruns': [row['id'] for row in reruns]})
    if failures:
        print('ACCEPTED不可。HANDOFFをACCEPTEDにしないこと。')
        for _, reason in failures:
            print('- ' + reason)
        return 20 if all(kind == 'confirm' for kind, _ in failures) else 30
    append_jsonl('accepts.jsonl', {'number': number, 'time': stamp, 'timezone': zone,
                                   'reruns': [row['id'] for row in reruns]})
    print('受け入れ条件をすべて実測で満たした。HANDOFFの状態に次の行を書く：')
    print('- 状態：ACCEPTED（%s・%s %s・交代%d・relay accept：指紋一致・証拠%d件再実行一致・副作用%d件確認・confirm済み）'
          % (record['after']['developer'], stamp, zone, number, len(reruns), len(record['side_effects'])))
    return 0


def confirm_main(number, passphrase):
    # Human only. AI agents (developer or auditor) must never run this, including in tests.
    refusal = human_terminal_refusal(os.environ, sys.stdin.isatty(), sys.stdout.isatty())
    if refusal:
        print('confirmを拒否：' + refusal + '。ユーザー本人が自分のterminalで実行してください。')
        return 30
    record, _ = load_swap(number)
    print('交代%d：開発 %s／監査 %s → 開発 %s／監査 %s、HEAD %s（%s）、記録 %s'
          % (number, record['before']['developer'], record['before']['auditor'],
             record['after']['developer'], record['after']['auditor'],
             record['state']['head'][:7], record['state']['branch'], record['time']))
    if input('この交代をあなた自身が確認しますか [y/N]: ').strip().lower() != 'y':
        print('確認しませんでした。')
        return 20
    if not record_confirmation(number, passphrase, os.ttyname(0)):
        print('合言葉が一致しません。')
        return 30
    print('ユーザー確認を記録しました（照合の記録であり、認証ではありません）。')
    return 0


def options(args, repeatable, single):
    values, rest = {name: [] for name in repeatable}, []
    values.update({name: None for name in single})
    index = 0
    while index < len(args):
        name = args[index]
        if name in values and index + 1 < len(args):
            if name in repeatable:
                values[name].append(args[index + 1])
            else:
                values[name] = args[index + 1]
            index += 2
        else:
            rest.append(name)
            index += 1
    return values, rest


def proof_main(config, args):
    timeout = int(config['timeout_seconds'])
    if args[0] == 'verify' and len(args) > 1:
        argv = args[2:] if args[1] == '--' else args[1:]
        if not argv:
            raise ValueError('usage: relay/b2b verify [--] <コマンド> [引数...]')
        row, out, err = run_recorded(argv, timeout, 'verify')
        sys.stdout.write(out.decode(errors='replace'))
        sys.stdout.write(err.decode(errors='replace'))
        print('証拠 %s：終了 %s、出力sha256 %s、HEAD %s、時刻 %s %s%s'
              % (row['id'], row['exit_code'], row['output_sha256'][:16], row['head'][:7], row['time'],
                 row['timezone'], '、作業ツリーが変化' if row['worktree_changed'] else ''))
        return 0 if row['exit_code'] == 0 else 10
    if args[:2] == ['effect', 'add'] and len(args) == 4:
        row = effect_add(args[2], args[3], timeout, config['developer'])
        print('副作用 %s を記録。確認コマンド終了 %s' % (row['id'], row['check_exit_code']))
        return 0 if row['check_exit_code'] == 0 else 10
    if args[0] == 'swap':
        values, rest = options(args[1:], ('--granted', '--used', '--promise', '--rerun'), ('--by',))
        if rest or not values['--by']:
            raise ValueError('usage: relay/b2b swap --by <指示者> [--granted 権限] [--used 権限] '
                             '[--promise 約束] [--rerun 証拠ID]')
        record, passphrase, _ = create_swap(config, values['--by'], values['--granted'], values['--used'],
                                            values['--promise'], values['--rerun'] or None)
        number = record['number']
        print('交代記録：.b2b/handoff-%d.json（前番号 %d）' % (number, record['previous_number']))
        print('合言葉（relayが乱数で生成。照合用で認証ではない）：' + passphrase)
        print('ユーザー本人が自分のterminalで：relay/b2b confirm %d <合言葉>（AIは実行しない）' % number)
        print('新しい開発役への確認の問い：' + record['question'])
        print('新しい開発役：relay/b2b accept %d --answer "<答え>"' % number)
        return 0
    if args[0] == 'confirm' and len(args) == 3 and args[1].isdigit():
        return confirm_main(int(args[1]), args[2])
    if args[0] == 'accept' and len(args) == 4 and args[1].isdigit() and args[2] == '--answer':
        return accept(int(args[1]), args[3], timeout)
    raise ValueError('usage: relay/b2b verify <コマンド> | effect add <説明> <確かめるコマンド> | swap --by <指示者> '
                     '| confirm <番号> <合言葉>（ユーザー専用） | accept <番号> --answer <答え>')


def audit_prompt(config, scope, audit, source):
    auditor = config['auditor']
    tools = ('ツールは使わず' if auditor == 'claude' else
             'read-only sandboxの読み取りコマンドは使ってよいが（gitには--no-optional-locks）、ファイルは書かず、主に')
    evidence = (STATE / 'evidence.jsonl').read_text() if (STATE / 'evidence.jsonl').exists() else ''
    effects = (STATE / 'side-effects.jsonl').read_text() if (STATE / 'side-effects.jsonl').exists() else ''
    return ('ユーザー承認のB2B監査。あなたは' + auditor + 'の監査役です。' + tools + '以下の正本と差分を監査。'
            'HANDOFFの現在の作業の完成条件と「監査への返答」も照合。'
            '開発ファイルは編集しない。既存の指摘・節・交代台帳・監査の記録を消さずに保持した更新後AUDIT.md全文だけを'
            f'\n{BEGIN}\nと\n{END}\nで挟んで返してください。'
            '読了位置は対象HEADのSHA、合格なら合格位置もHEAD。判定は問題なし/直してから次へ/要ユーザー判断。'
            'relayが記録した証拠（evidence.jsonl）は機械測定、HANDOFFの報告は自己申告として区別し、'
            '未検証の実機挙動は区別してコードや提供証拠を評価してください。'
            '\n対象: ' + scope + '\n作業状態（relay実測）: ' + json.dumps(worktree_state(), ensure_ascii=False)
            + '\nHANDOFF:\n' + (ROOT / 'HANDOFF.md').read_text()
            + '\nAUDIT:\n' + audit + '\nDIFF:\n' + git('diff', scope)
            + '\nEVIDENCE（relay記録）:\n' + evidence + '\nSIDE-EFFECTS（relay記録）:\n' + effects
            + '\n現在の全追跡ファイル:\n' + ''.join(source))


def main():
    os.chdir(ROOT)
    args = sys.argv[1:]
    config = json.loads((ROOT / 'relay/config.example').read_text())
    if (STATE / 'config.json').exists():
        config.update(json.loads((STATE / 'config.json').read_text()))
    if args and args[0] in ('verify', 'effect', 'swap', 'confirm', 'accept'):
        return proof_main(config, args)
    dry = '--dry-run' in args
    args = [a for a in args if a != '--dry-run']
    audit = (ROOT / 'AUDIT.md').read_text()
    if args == ['status']:
        model = config['model'] if config['auditor'] == 'claude' else (config.get('codex_model') or 'CLI既定')
        print('開発役:', config['developer'], '/ 監査役:', config['auditor'], model)
        print('読了位置:', position(audit, '読了位置'))
        print('合格位置:', position(audit, '合格位置'))
        print('開いた指摘:', sum(is_open(s) for s in findings(audit).values()))
        if handoff_numbers():
            number = handoff_numbers()[-1]
            confirmed = any(row.get('number') == number for row in read_jsonl('confirmations.jsonl'))
            accepted = any(row.get('number') == number for row in read_jsonl('accepts.jsonl'))
            print('最新の交代記録: %d（confirm %s / accept %s）' % (number, '済み' if confirmed else '未',
                                                             '済み' if accepted else '未'))
        if (STATE / 'log.jsonl').exists():
            print('\n'.join((STATE / 'log.jsonl').read_text().splitlines()[-3:]))
        return 0
    if args == ['log']:
        if (STATE / 'log.jsonl').exists():
            print('\n'.join((STATE / 'log.jsonl').read_text().splitlines()[-10:]))
        return 0
    if len(args) not in (2, 3) or args[:2] != ['call', 'audit']:
        print('usage: relay/b2b call audit [base..head] [--dry-run] | status | log | verify | effect add '
              '| swap | confirm（ユーザー専用） | accept')
        return 30
    cmd = command(config)
    if any(word in arg for arg in cmd for word in FORBIDDEN):
        raise ValueError('承認を省くオプションは禁止')
    verify_help(cmd)
    head = git('rev-parse', 'HEAD').strip()
    requested = args[2] if len(args) == 3 else position(audit, '読了位置').split()[0] + '..HEAD'
    if '..' not in requested or '...' in requested:
        raise ValueError('範囲は base..head で指定する')
    base, target = requested.split('..')
    base = git('rev-parse', '--verify', base + '^{commit}').strip()
    target = git('rev-parse', '--verify', target + '^{commit}').strip()
    if target != head:
        raise ValueError('監査終点は現在のHEADに限定')
    subprocess.check_call(['git', '--no-optional-locks', 'merge-base', '--is-ancestor', base, head], cwd=ROOT)
    scope = base + '..' + head
    if dry:
        print(shlex.join(cmd))
        print('範囲:', scope, '/ timeout:', config['timeout_seconds'])
        return 0
    timeout = int(config['timeout_seconds'])
    limit = int(config['max_rounds'])
    if timeout <= 0 or limit <= 0:
        raise ValueError('時間上限と往復上限は正の数')
    STATE.mkdir(mode=0o700, exist_ok=True)
    lock = STATE / 'lock'
    try:
        lock.mkdir()
    except FileExistsError:
        print('二重起動または残存lock。実行中でないかユーザーが確認してください。')
        return 30
    start = time.monotonic()
    code, verdict = 30, 'エラーまたは違反'
    before = None
    observed_changes = []
    audit_written = False
    codex_home = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
    settings_before = None
    settings_changes = None  # None: not compared (distinct from [] = compared, unchanged)
    settings_error = None

    def compare_settings():
        nonlocal settings_changes
        settings_after = codex_settings(codex_home)
        settings_changes = sorted(k for k in set(settings_before) | set(settings_after)
                                  if settings_before.get(k) != settings_after.get(k))
        return settings_changes

    try:
        before = snapshot()
        history = [json.loads(line) for line in (STATE / 'log.jsonl').read_text().splitlines()] if (STATE / 'log.jsonl').exists() else []
        if sum(row.get('range') == scope for row in history) >= limit:
            raise UserDecision('同範囲の往復上限')
        tmp = STATE / 'tmp'
        tmp.mkdir(mode=0o700, exist_ok=True)
        env = os.environ.copy()
        env.update(CLAUDE_CODE_TMPDIR=str(tmp), DISABLE_AUTOUPDATER='1', TMPDIR=str(tmp))
        runtime_mode = config.get('runtime', 'isolated')
        if config['auditor'] == 'codex':
            # The runtime setting is for Claude. Codex keeps its own state in CODEX_HOME (allowed);
            # its settings are digested before and after the run instead of OS enforcement.
            settings_before = codex_settings(codex_home)
        elif runtime_mode == 'isolated':
            runtime = STATE / 'runtime'
            runtime.mkdir(mode=0o700, exist_ok=True)
            env['CLAUDE_CONFIG_DIR'] = str(runtime)
            creds = Path(os.environ.get('CLAUDE_CONFIG_DIR', str(Path.home() / '.claude'))) / '.credentials.json'
            if creds.is_file() and not (runtime / '.credentials.json').exists():
                shutil.copyfile(creds, runtime / '.credentials.json')
                (runtime / '.credentials.json').chmod(0o600)
        elif runtime_mode == 'normal':
            if sys.platform != 'darwin' or not Path('/usr/bin/sandbox-exec').exists():
                raise UserDecision('通常runtimeの設定保護はmacOSのみ対応。ユーザー判断で停止。')
            if not sandbox_usable():
                raise UserDecision('sandbox-execを適用できない（既にsandbox内など）。設定保護なしでは起動せずユーザー判断で停止。')
            normal = Path(env.get('CLAUDE_CONFIG_DIR', str(Path.home() / '.claude'))).resolve()
            policy = sandbox_policy(normal)
            cmd = ['/usr/bin/sandbox-exec', '-p', policy, *cmd]
        else:
            raise ValueError('runtimeはisolatedまたはnormal')
        source = []
        for path in git('ls-files').splitlines():
            data = (ROOT / path).read_text()
            source.append('\nFILE ' + path + '\n' + data)
        output = run_child(cmd, audit_prompt(config, scope, audit, source), timeout, env)
        after = snapshot()
        changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
        observed_changes = changed
        if changed:
            raise RuntimeError('監査中のファイル変更違反: ' + ', '.join(changed))
        if settings_before is not None:
            if compare_settings():
                raise RuntimeError('監査中のCodex設定変更: ' + ', '.join(settings_changes))
            output = codex_message(output)
        body, verdict, code, current = validate(output, head, audit)
        memo_path = STATE / 'state.json'
        memo = json.loads(memo_path.read_text()) if memo_path.exists() else {'last': findings(audit), 'reopens': {}}
        for key, state in current.items():
            if is_open(state) and key in memo['last'] and not is_open(memo['last'][key]):
                memo['reopens'][key] = memo['reopens'].get(key, 0) + 1
        memo['last'] = current
        if any(n >= 2 for n in memo['reopens'].values()):
            body = re.sub(r'^- 判定：.*$', '- 判定：要ユーザー判断', body, flags=re.M)
            # A newly reopened finding cannot advance the passed position.
            body = re.sub(r'^- 合格位置[^：\n]*：.*$', '- 合格位置：' + position(audit, '合格位置'), body, flags=re.M)
            code, verdict = 20, '要ユーザー判断（同じ指摘の再オープン2回）'
        memo_path.write_text(json.dumps(memo, ensure_ascii=False))
        (STATE / 'audit.next').write_text(body)
        os.replace(STATE / 'audit.next', ROOT / 'AUDIT.md')
        audit_written = True
        print(verdict)
    except UserDecision as error:
        print(str(error))
        code, verdict = 20, '要ユーザー判断（' + str(error) + '）'
    except Exception as error:
        print(type(error).__name__ + ': ' + str(error))
        code, verdict = 30, 'エラーまたは違反'
    finally:
        comparison_error = None
        try:
            try:
                after = snapshot()
                if before is not None:
                    extra = [k for k in set(before) | set(after)
                             if not (k == 'AUDIT.md' and audit_written)
                             and before.get(k) != after.get(k)]
                    observed_changes = sorted(set(observed_changes) | set(extra))
                if 'AUDIT.md' in observed_changes and not audit_written:
                    (ROOT / 'AUDIT.md').write_text(audit)
            except Exception as error:
                comparison_error = type(error).__name__
                code, verdict = 30, 'エラーまたは違反（前後比較失敗）'
            # Failure paths (CLI error, auth stop, timeout, repository violation) still compare Codex settings.
            if settings_before is not None and settings_changes is None:
                try:
                    if compare_settings():
                        code, verdict = 30, 'エラーまたは違反（Codex設定変更）'
                        print('監査中のCodex設定変更: ' + ', '.join(settings_changes))
                except Exception as error:
                    settings_error = type(error).__name__
                    code, verdict = 30, 'エラーまたは違反（Codex設定の比較失敗）'
            stamp, zone = local_time()
            row = {'time': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                   'local_time': stamp, 'timezone': zone,
                   'caller': config['developer'], 'callee': config['auditor'], 'action': 'audit',
                   'range': scope, 'verdict': verdict, 'exit_code': code,
                   'duration_seconds': round(time.monotonic() - start, 2),
                   'unexpected_changes': observed_changes, 'comparison_error': comparison_error}
            if config['auditor'] == 'codex':
                row['codex_settings_changes'] = settings_changes
                row['codex_settings_error'] = settings_error
            with (STATE / 'log.jsonl').open('a') as stream:
                stream.write(json.dumps(row, ensure_ascii=False) + '\n')
        finally:
            lock.rmdir()
        if comparison_error:
            print('前後比較が失敗。ログに記録しlockを解放。')
    return code


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:
        print(type(error).__name__ + ': ' + str(error))
        sys.exit(20 if 'ユーザー判断' in str(error) else 30)
