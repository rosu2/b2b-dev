"""Optional, local B2B audit relay. Python standard library only."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
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


def git(*args):
    return subprocess.check_output(['git', '--no-optional-locks', *args], cwd=ROOT).decode()


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


def command(config):
    cli = config['auditor']
    if cli == 'claude':
        return ['claude', '-p', '--model', config['model'], '--tools', '',
                '--permission-mode', 'plan', '--no-session-persistence',
                '--safe-mode', '--setting-sources', '', '--strict-mcp-config',
                '--mcp-config', '{}']
    # Codex CLI read-only sandbox constrains tools, but not its own runtime writes.
    # This first version refuses it rather than changing CODEX_HOME or global config.
    raise ValueError('第1段階は claude のみ対応。codex の runtime 隔離は未実装（ユーザー判断）。')


def verify_help(cmd):
    help_text = subprocess.check_output([cmd[0], '--help'], cwd=ROOT).decode()
    for flag in (arg for arg in cmd[1:] if arg.startswith('-')):
        if flag not in help_text:
            raise ValueError('実機ヘルプにないオプション: ' + flag)


def run_child(cmd, prompt, timeout, env):
    child = subprocess.Popen(cmd, cwd=ROOT, stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, env=env, start_new_session=True)
    try:
        out, err = child.communicate(prompt, timeout=timeout)
        if child.returncode:
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
    if codes[verdict] == 0:
        if any(is_open(s) for s in current.values()):
            raise ValueError('開いた指摘があるのに合格')
        passed = position(body, '合格位置').split()[0]
        if git('rev-parse', '--verify', passed + '^{commit}').strip() != head:
            raise ValueError('合格位置がHEADと違う')
    return body, verdict, codes[verdict], current


def main():
    os.chdir(ROOT)
    args = sys.argv[1:]
    dry = '--dry-run' in args
    args = [a for a in args if a != '--dry-run']
    config = json.loads((ROOT / 'relay/config.example').read_text())
    if (STATE / 'config.json').exists():
        config.update(json.loads((STATE / 'config.json').read_text()))
    audit = (ROOT / 'AUDIT.md').read_text()
    if args == ['status']:
        print('開発役:', config['developer'], '/ 監査役:', config['auditor'], config['model'])
        print('読了位置:', position(audit, '読了位置'))
        print('合格位置:', position(audit, '合格位置'))
        print('開いた指摘:', sum(is_open(s) for s in findings(audit).values()))
        if (STATE / 'log.jsonl').exists():
            print('\n'.join((STATE / 'log.jsonl').read_text().splitlines()[-3:]))
        return 0
    if args == ['log']:
        if (STATE / 'log.jsonl').exists():
            print('\n'.join((STATE / 'log.jsonl').read_text().splitlines()[-10:]))
        return 0
    if len(args) not in (2, 3) or args[:2] != ['call', 'audit']:
        print('usage: relay/b2b call audit [base..head] [--dry-run] | status | log')
        return 30
    cmd = command(config)
    if any('dangerously' in arg or 'bypassPermissions' in arg for arg in cmd):
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
    before = snapshot()
    try:
        history = [json.loads(line) for line in (STATE / 'log.jsonl').read_text().splitlines()] if (STATE / 'log.jsonl').exists() else []
        if sum(row.get('range') == scope for row in history) >= limit:
            code, verdict = 20, '要ユーザー判断（同範囲の往復上限）'
            return code
        runtime = STATE / 'runtime'
        runtime.mkdir(mode=0o700, exist_ok=True)
        tmp = STATE / 'tmp'
        tmp.mkdir(mode=0o700, exist_ok=True)
        env = os.environ.copy()
        env.update(CLAUDE_CONFIG_DIR=str(runtime), CLAUDE_CODE_TMPDIR=str(tmp),
                   DISABLE_AUTOUPDATER='1', TMPDIR=str(tmp))
        # Existing OAuth credentials may be read, never printed. Local runtime copy only.
        creds = Path(os.environ.get('CLAUDE_CONFIG_DIR', str(Path.home() / '.claude'))) / '.credentials.json'
        if creds.is_file() and not (runtime / '.credentials.json').exists():
            shutil.copyfile(creds, runtime / '.credentials.json')
            (runtime / '.credentials.json').chmod(0o600)
        source = []
        for path in git('ls-files').splitlines():
            data = (ROOT / path).read_text()
            source.append('\nFILE ' + path + '\n' + data)
        prompt = ('ユーザー承認のB2B監査。あなたはClaudeの監査役です。ツールは使わず、以下の正本と差分を監査。'
                  '監査役からの申し送りb2b-relayの完成条件、B2B-007〜009の返答も照合。'
                  '開発ファイルは編集しない。既存指摘と記録を保持した更新後AUDIT.md全文だけを'
                  f'\n{BEGIN}\nと\n{END}\nで挟んで返してください。'
                  '読了位置は対象HEADのSHA、合格なら合格位置もHEAD。判定は問題なし/直してから次へ/要ユーザー判断。'
                  '未検証の実機挙動は区別し、コードや提供証拠を評価してください。'
                  '\n対象: ' + scope + '\nHANDOFF:\n' + (ROOT / 'HANDOFF.md').read_text()
                  + '\nAUDIT:\n' + audit + '\nDIFF:\n' + git('diff', scope)
                  + '\n現在の全追跡ファイル:\n' + ''.join(source))
        output = run_child(cmd, prompt, timeout, env)
        body, verdict, code, current = validate(output, head, audit)
        after = snapshot()
        changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
        if changed:
            raise RuntimeError('監査中のファイル変更違反: ' + ', '.join(changed))
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
        print(verdict)
        return code
    except Exception as error:
        print(type(error).__name__ + ': ' + str(error))
        code, verdict = 30, 'エラーまたは違反'
        return code
    finally:
        after = snapshot()
        unexpected = [k for k in set(before) | set(after) if k != 'AUDIT.md' and before.get(k) != after.get(k)]
        row = {'time': datetime.datetime.now(datetime.timezone.utc).isoformat(),
               'caller': config['developer'], 'callee': config['auditor'], 'action': 'audit',
               'range': scope, 'verdict': verdict, 'exit_code': code,
               'duration_seconds': round(time.monotonic() - start, 2), 'unexpected_changes': unexpected}
        with (STATE / 'log.jsonl').open('a') as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')
        lock.rmdir()


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:
        print(type(error).__name__ + ': ' + str(error))
        sys.exit(20 if 'ユーザー判断' in str(error) else 30)
