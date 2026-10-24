#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT HUP INT TERM
export PIP_CACHE_DIR="$work/cache"
export TMPDIR="$work"
python3 -m venv --without-pip "$work/venv"
mkdir "$work/source"
cp "$root/pyproject.toml" "$root/README.md" "$root/LICENSE" "$work/source/"
cp -R "$root/fixledger" "$work/source/"
cd "$work/source"
if ! python3 -m pip --python "$work/venv/bin/python" install --no-deps . > "$work/install.log" 2>&1; then
    cat "$work/install.log"
    exit 1
fi
cd "$work"
"$work/venv/bin/fixledger" --help
printf 'PASS fresh venv, pip install ., fixledger --help\n'
