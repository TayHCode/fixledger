#!/bin/sh
set -eu
cd "$(dirname -- "$0")/.."
python3 - <<'PY'
import re
import socket
import subprocess
from pathlib import Path

patterns = [
    re.escape('/' + 'ho' + 'me/'),
    'tay' + 'lor',
    'playnation' + 'craft',
    r'[^\s<>"\x27`]*' + chr(64) + r'[^\s<>"\x27`]+',
    r'\b(?:\d{1,3}\.){3}\d{1,3}\b',
    r'\b[\w-]+\.' + 'local' + r'\b',
]
for host in {socket.gethostname(), socket.getfqdn()}:
    if host and host != 'localhost':
        patterns.append(r'(?<![\w-])' + re.escape(host) + r'(?![\w-])')
pattern = re.compile('|'.join(patterns), re.IGNORECASE)
names = subprocess.check_output(['git', 'ls-files', '-z']).decode().split('\0')
sources = [(name, name + '\n' + Path(name).read_bytes().decode('utf-8', errors='replace')) for name in names if name]
history = subprocess.check_output(['git', 'log', '--format=%an <%ae>%n%cn <%ce>%n%B']).decode()
history = re.sub(r'(?i)^(co-authored-by:.*<)noreply' + chr(64) + r'[\w.-]+(>)\s*$', r'\1\2', history, flags=re.M)
sources.append(('git log', history))
failed = False
for source, content in sources:
    for number, line in enumerate(content.splitlines(), 1):
        if pattern.search(line):
            print('FAIL personal information in {} line {}'.format(source, number))
            failed = True
if failed:
    raise SystemExit(1)
print('PASS tracked files and git log contain no personal information')
PY
