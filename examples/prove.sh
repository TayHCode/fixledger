#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT HUP INT TERM
unset FIXLEDGER_FILE
export PYTHONPATH="$root${PYTHONPATH:+:$PYTHONPATH}"
cd "$work"
git init -q
printf 'def load(): pass\n' > app.py
first=$(python3 -m fixledger add --file app.py --symbol load --title 'Missing input validation')
id=${first%% *}
second=$(python3 -m fixledger add --file app.py --symbol load --title 'Missing input validation')
test "${second%% *}" = "$id"
printf 'PASS add and duplicate share one ID\n'
code=0
python3 -m fixledger check --file app.py --symbol load --title 'Missing input validation' > check.out || code=$?
test "$code" = 3
printf 'PASS known check exits 3\n'
python3 -m fixledger fix "$id" > fix.out
code=0
python3 -m fixledger check --file app.py --symbol load --title 'Missing input validation' > check.out || code=$?
test "$code" = 3
case "$(cat check.out)" in *'[fixed]'*) ;; *) exit 1 ;; esac
printf 'PASS fixed check exits 3 and reports fixed\n'
python3 -m fixledger check --file app.py --title 'Connection pool leaks sockets' > check.out
printf 'PASS different issue exits 0\n'
