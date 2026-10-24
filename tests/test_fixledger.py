import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fixledger.ledger import Ledger, LedgerError


ROOT = Path(__file__).resolve().parents[1]


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / '.git').mkdir()
        (self.root / 'app.py').write_text('original\n')
        self.environment = dict(os.environ, PYTHONPATH=str(ROOT))
        self.environment.pop('FIXLEDGER_FILE', None)
        self.ledger = Ledger(self.root / '.fixledger.json', cwd=self.root)

    def cli(self, *arguments, input=None, cwd=None):
        return subprocess.run(
            [sys.executable, '-m', 'fixledger', *arguments], cwd=cwd or self.root,
            env=self.environment, input=input, capture_output=True, text=True,
        )

    def add(self, **changes):
        values = dict(file='app.py', symbol='load', title='Missing input validation')
        values.update(changes)
        return self.ledger.add(**values)

    def test_add_idempotence_and_stable_serialization(self):
        entry = self.add()
        expected = hashlib.sha1(b'app.py|load|missing input validation').hexdigest()[:10]
        self.assertEqual(entry['id'], expected)
        before = self.ledger.path.read_bytes()
        self.assertEqual(self.add(title=' MISSING input validation! ', note='ignored'), entry)
        self.assertEqual(self.ledger.path.read_bytes(), before)
        saved = json.loads(before)
        self.assertEqual(list(saved[expected]), sorted(saved[expected]))
        self.assertEqual(len(saved), 1)

    def test_fuzzy_check_symbol_and_file_boundaries(self):
        entry = self.add()
        self.assertEqual(self.ledger.check('app.py', 'Missing input validation fails', 'load')['id'], entry['id'])
        self.assertEqual(self.ledger.check('app.py', 'Missing input validation fails')['id'], entry['id'])
        self.assertIsNone(self.ledger.check('other.py', entry['title'], 'load'))
        self.assertIsNone(self.ledger.check('app.py', entry['title'], 'save'))
        self.assertIsNone(self.ledger.check('app.py', 'Unrelated socket leak', 'load'))

    def test_jaccard_threshold_and_empty_stored_symbol(self):
        entry = self.add(title='one two three four', symbol='')
        self.assertEqual(self.ledger.check('app.py', 'one two three five', 'load')['id'], entry['id'])
        self.assertIsNone(self.ledger.check('app.py', 'one two five six', 'load'))

    def test_fix_wontfix_reopen_preserves_identity_and_note(self):
        entry = self.add()
        for status in ('fixed', 'wontfix', 'open'):
            changed = self.ledger.close(entry['id'], status, note='reviewed')
            self.assertEqual(changed['status'], status)
            self.assertEqual(changed['first_seen'], entry['first_seen'])
            self.assertEqual(changed['note'], 'reviewed')
            self.assertEqual(changed['file_hash'], '' if status == 'open' else hashlib.sha1(b'original\n').hexdigest())
        self.assertEqual(self.ledger.list('open')[0]['id'], entry['id'])
        self.assertEqual(self.ledger.list('fixed'), [])

    def test_close_records_git_revision(self):
        (self.root / '.git').rmdir()
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        subprocess.run(['git', '-C', str(self.root), 'add', 'app.py'], check=True)
        env = dict(self.environment, GIT_AUTHOR_NAME='Release Export', GIT_AUTHOR_EMAIL='release-export',
                   GIT_COMMITTER_NAME='Release Export', GIT_COMMITTER_EMAIL='release-export')
        subprocess.run(['git', '-C', str(self.root), 'commit', '-qm', 'Fixture'], env=env, check=True)
        expected = subprocess.check_output(['git', '-C', str(self.root), 'rev-parse', '--short', 'HEAD'], text=True).strip()
        closed = self.ledger.close(self.add()['id'])
        self.assertEqual(closed['rev'], expected)

    def test_file_changed_and_deleted_are_flagged(self):
        self.ledger.close(self.add()['id'])
        self.assertFalse(self.ledger.check('app.py', 'Missing input validation')['file_changed'])
        (self.root / 'app.py').write_text('changed\n')
        checked = self.cli('check', '--file', 'app.py', '--title', 'Missing input validation')
        self.assertEqual(checked.returncode, 3)
        self.assertIn('fixed, file changed since', checked.stdout)
        (self.root / 'app.py').unlink()
        self.assertTrue(self.ledger.check('app.py', 'Missing input validation')['file_changed'])

    def test_discovery_and_overrides(self):
        nested = self.root / 'src'
        nested.mkdir()
        self.assertEqual(Ledger(cwd=nested).path, self.ledger.path)
        (self.root / '.git').rmdir()
        (self.root / '.git').write_text('gitdir: unused\n')
        self.assertEqual(Ledger(cwd=nested).root, self.root)
        with patch.dict(os.environ, {'FIXLEDGER_FILE': 'custom.json'}):
            self.assertEqual(Ledger(cwd=nested).path, nested / 'custom.json')
            self.assertEqual(Ledger('explicit.json', cwd=nested).path, nested / 'explicit.json')
        (self.root / '.git').unlink()
        real_exists = Path.exists
        inside_root = lambda p: p.name != '.git' or self.root in (p.parent, *p.parent.parents)
        with patch.object(Path, 'exists', lambda p: real_exists(p) and inside_root(p)):
            self.assertEqual(Ledger(cwd=nested).path, nested / '.fixledger.json')

    def test_absolute_and_normalized_file_paths_match(self):
        entry = self.add(file='./src/../app.py')
        self.assertEqual(self.ledger.check(str(self.root / 'app.py'), entry['title'])['id'], entry['id'])

    def test_corrupt_json_and_schema_never_overwritten(self):
        for content in ('{broken', '[]', '{"bad": {}}', '{"bad": null}'):
            self.ledger.path.write_text(content)
            result = self.cli('add', '--file', 'app.py', '--title', 'Finding')
            self.assertEqual(result.returncode, 2)
            self.assertIn('corrupt ledger', result.stderr)
            self.assertEqual(self.ledger.path.read_text(), content)
        entry = self.add_after_removing_corruption()
        content = self.ledger.load()
        content[entry['id']]['status'] = 'invalid'
        self.ledger.path.write_text(json.dumps(content))
        with self.assertRaisesRegex(LedgerError, 'corrupt ledger'):
            self.ledger.load()

    def add_after_removing_corruption(self):
        self.ledger.path.unlink()
        return self.add()

    def test_atomic_failure_preserves_old_bytes_and_cleans_temporary(self):
        self.add()
        before = self.ledger.path.read_bytes()
        with patch('fixledger.ledger.os.replace', side_effect=OSError('replace failed')):
            with self.assertRaisesRegex(OSError, 'replace failed'):
                self.add(title='Different issue')
        self.assertEqual(self.ledger.path.read_bytes(), before)
        self.assertEqual(list(self.root.glob('.fixledger-*')), [])
        self.add(title='Different issue')
        self.assertEqual(len(json.loads(self.ledger.path.read_text())), 2)

    def test_cli_exit_codes_filters_and_global_or_local_override(self):
        added = self.cli('--ledger', 'custom.json', 'add', '--file', 'app.py', '--title', 'Finding')
        self.assertEqual(added.returncode, 0, added.stderr)
        id = added.stdout.split()[0]
        known = self.cli('check', '--ledger', 'custom.json', '--file', 'app.py', '--title', 'Finding')
        self.assertEqual(known.returncode, 3)
        for command, status in (('fix', 'fixed'), ('wontfix', 'wontfix'), ('reopen', 'open')):
            result = self.cli(command, id, '--ledger', 'custom.json')
            self.assertEqual(result.returncode, 0, result.stderr)
            listing = self.cli('list', '--ledger', 'custom.json', '--status', status, '--json')
            self.assertEqual(json.loads(listing.stdout)[0]['status'], status)
        self.assertEqual(self.cli('check', '--file', 'app.py', '--title', 'New').returncode, 0)

    def test_bad_cli_input_has_clear_errors(self):
        for arguments in (('add',), ('fix', 'unknown'), ('add', '--file', '', '--title', 'x'),
                          ('add', '--file', 'app.py', '--title', '!!!')):
            result = self.cli(*arguments)
            self.assertEqual(result.returncode, 2)
            self.assertTrue(result.stderr)
            self.assertNotIn('Traceback', result.stderr)
        self.assertFalse(self.ledger.path.exists())

    def test_hook_both_events_use_input_cwd(self):
        self.ledger.close(self.add()['id'])
        self.ledger.close(self.add(title='Unsupported format')['id'], 'wontfix')
        self.add(title='Still open')
        with tempfile.TemporaryDirectory() as unrelated:
            for event in ('SessionStart', 'PreCompact'):
                result = self.cli('hook', input=json.dumps({'hook_event_name': event, 'cwd': str(self.root)}), cwd=unrelated)
                self.assertEqual(result.returncode, 0, result.stderr)
                output = json.loads(result.stdout)['hookSpecificOutput']
                self.assertEqual(output['hookEventName'], event)
                self.assertIn('do not report or fix again', output['additionalContext'])
                self.assertIn('[fixed]', output['additionalContext'])
                self.assertIn('[wontfix]', output['additionalContext'])
                self.assertNotIn('Still open', output['additionalContext'])

    def test_empty_hook_and_invalid_hook(self):
        event = json.dumps({'hook_event_name': 'SessionStart', 'cwd': str(self.root)})
        self.assertEqual(self.cli('hook', input=event).stdout, '')
        self.add()
        self.assertEqual(self.cli('hook', input=event).stdout, '')
        for payload in ('{bad', '[]', '{"hook_event_name":"SessionStart"}'):
            result = self.cli('hook', input=payload)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, '')

    def test_mcp_live_pipe_initialize_tools_and_lifecycle(self):
        process = subprocess.Popen([sys.executable, '-m', 'fixledger', 'mcp'], cwd=self.root,
                                   env=self.environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        def exchange(method, params=None, id=1):
            process.stdin.write(json.dumps(dict(jsonrpc='2.0', id=id, method=method, params=params or {})) + '\n')
            process.stdin.flush()
            return json.loads(process.stdout.readline())
        try:
            result = exchange('initialize', {'protocolVersion': '2024-11-05'})['result']
            self.assertEqual(result['serverInfo']['name'], 'fixledger')
            self.assertEqual(result['protocolVersion'], '2024-11-05')
            process.stdin.write('{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
            process.stdin.flush()
            self.assertEqual(exchange('ping', id=2), {'jsonrpc': '2.0', 'id': 2, 'result': {}})
            names = [tool['name'] for tool in exchange('tools/list')['result']['tools']]
            self.assertEqual(names, ['ledger_check', 'ledger_add', 'ledger_close', 'ledger_list'])
            def call(name, arguments):
                result = exchange('tools/call', {'name': name, 'arguments': arguments})['result']
                self.assertFalse(result['isError'])
                return json.loads(result['content'][0]['text'])
            finding = {'file': 'app.py', 'title': 'Missing input validation'}
            self.assertEqual(call('ledger_check', finding), {'known': False, 'entry': None})
            entry = call('ledger_add', finding)
            self.assertEqual(call('ledger_add', finding)['id'], entry['id'])
            for status in ('fixed', 'wontfix', 'open'):
                self.assertEqual(call('ledger_close', {'id': entry['id'], 'status': status})['status'], status)
            self.assertTrue(call('ledger_check', finding)['known'])
            self.assertEqual(call('ledger_list', {'status': 'open'})[0]['id'], entry['id'])
            process.stdin.close()
            self.assertEqual(process.wait(timeout=5), 0)
            self.assertEqual(process.stderr.read(), '')
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()

    def test_mcp_errors_are_framed_and_server_continues(self):
        requests = [
            '{bad', '[]',
            json.dumps(dict(jsonrpc='2.0', id=1, method='unknown')),
            json.dumps(dict(jsonrpc='2.0', id=2, method='tools/call', params={'name': 'ledger_add', 'arguments': {}})),
            json.dumps(dict(jsonrpc='2.0', id=3, method='tools/call', params={'name': 'ledger_close', 'arguments': {'id': 'absent', 'status': 'fixed'}})),
            json.dumps(dict(jsonrpc='2.0', id=4, method='ping')),
        ]
        result = self.cli('mcp', input='\n'.join(requests) + '\n')
        responses = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([r['error']['code'] for r in responses[:4]], [-32700, -32600, -32601, -32602])
        self.assertTrue(responses[4]['result']['isError'])
        self.assertIn('unknown id', responses[4]['result']['content'][0]['text'])
        self.assertEqual(responses[5]['result'], {})
        self.assertEqual(result.returncode, 0, result.stderr)

    def privacy_fixture(self):
        (self.root / '.git').rmdir()
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        scripts = self.root / 'scripts'
        scripts.mkdir()
        (scripts / 'no_personal.sh').write_bytes((ROOT / 'scripts' / 'no_personal.sh').read_bytes())
        (self.root / 'sample.txt').write_text('safe\n')
        subprocess.run(['git', '-C', str(self.root), 'add', 'sample.txt', 'scripts/no_personal.sh'], check=True)
        env = dict(self.environment, GIT_AUTHOR_NAME='Release Export', GIT_AUTHOR_EMAIL='release-export',
                   GIT_COMMITTER_NAME='Release Export', GIT_COMMITTER_EMAIL='release-export')
        subprocess.run(['git', '-C', str(self.root), 'commit', '-qm', 'Fixture'], env=env, check=True)
        return env

    def privacy_check(self):
        return subprocess.run(['sh', 'scripts/no_personal.sh'], cwd=self.root,
                              capture_output=True, text=True)

    def test_privacy_scanner_rejects_tracked_information_without_matching_itself(self):
        self.privacy_fixture()
        self.assertEqual(self.privacy_check().returncode, 0)
        for content in ('/' + 'ho' + 'me/user', 'tay' + 'lor', 'playnation' + 'craft',
                        'person' + chr(64) + 'example.invalid', '.'.join(['192', '0', '2', '1'])):
            with self.subTest(content=content):
                (self.root / 'sample.txt').write_text(content)
                result = self.privacy_check()
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn('sample.txt', result.stdout)

    def test_privacy_scanner_checks_history_including_coauthor_trailers(self):
        env = self.privacy_fixture()
        trailer = 'Co-Authored-By: Agent <' + 'bot' + chr(64) + 'example.invalid>'
        subprocess.run(['git', '-C', str(self.root), 'commit', '--allow-empty', '-qm', 'Build\n\n' + trailer], env=env, check=True)
        result = self.privacy_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn('git log', result.stdout)


if __name__ == '__main__':
    unittest.main()
