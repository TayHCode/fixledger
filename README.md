# fixledger

Keep a repository ledger of findings so a coding agent can check what was already reported or fixed.
Python 3.9 or later is required. The package has no runtime dependencies.

## Install

From the checkout, run:

```sh
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install .
fixledger --help
```

## Record and check findings

Run `check` before reporting a finding or changing code:

```sh
fixledger check --file app.py --symbol load --title 'Missing input validation'
fixledger add --file app.py --symbol load --title 'Missing input validation' --severity high
fixledger list
```

`add` prints the ID and preserves an existing entry when you add the same finding again.
Use that ID to change its status:

```sh
fixledger fix ID --note 'Validate input before parsing'
fixledger wontfix ID --note 'Accepted behavior'
fixledger reopen ID
fixledger list --status fixed --json
```

`check` exits with code 0 for a new finding and 3 for a known finding, including closed findings.
Bad arguments, unknown IDs, unreadable files, and corrupt ledgers produce an error on stderr and exit with code 2.
A corrupt ledger is never overwritten.

IDs are the first ten hexadecimal characters of SHA-1 over `file|symbol|normalized title`.
File paths are normalized relative to the repository root. Titles use lowercase word tokens with punctuation removed.
Fuzzy checks require the same file, matching symbols or an empty symbol, and title token Jaccard similarity of at least 0.6.
An exact ID takes precedence over fuzzy matches.

Closing a finding records its file's SHA-1 and the short Git revision when available.
Checking a fixed finding after its file changes prints `fixed, file changed since` and still exits with code 3.
A missing file or unavailable revision is stored as an empty string.
Reopening clears the closing hash and revision.

The ledger is `.fixledger.json` in the nearest ancestor containing `.git`, including a Git worktree's `.git` file.
Outside a repository, it uses the current directory.
Override the path with `--ledger PATH` before or after the subcommand, or with `FIXLEDGER_FILE`.
Relative override paths use the current directory. Source file paths always use the repository root.
Writes replace the ledger atomically with sorted JSON keys. Use one writer at a time.
The supplied `.gitignore` keeps the ledger local.

## Prove the workflow

From the checkout root, run:

```sh
sh examples/prove.sh
```

Output:

```text
PASS add and duplicate share one ID
PASS known check exits 3
PASS fixed check exits 3 and reports fixed
PASS different issue exits 0
```

The script checks the full sequence in a temporary Git repository and removes it on exit.
Run the other acceptance checks with:

```sh
python3 -m unittest discover -s tests
sh scripts/fresh_install.sh
sh scripts/no_personal.sh
```

The installation check uses a temporary copy and virtual environment outside the checkout, then removes both.
The privacy check scans tracked filenames, tracked file contents, and Git author, committer, and message history.
Email addresses in coauthor trailers also fail this check.

## Add Claude Code hooks

After installing `fixledger` on the command search path, merge these hooks into `.claude/settings.json`:

```json
{
  "hooks": {
    "SessionStart": [
      {"hooks": [{"type": "command", "command": "fixledger hook"}]}
    ],
    "PreCompact": [
      {"hooks": [{"type": "command", "command": "fixledger hook"}]}
    ]
  }
}
```

The hook reads the event's JSON from stdin and uses its `cwd` to find the ledger.
For SessionStart and PreCompact it returns `hookSpecificOutput` with the event name and an `additionalContext` reminder listing fixed and wontfix findings.
An empty ledger or a ledger with only open findings produces no output.

## Add the MCP server

Merge this into the repository's `.mcp.json`:

```json
{
  "mcpServers": {
    "fixledger": {
      "command": "fixledger",
      "args": ["mcp"]
    }
  }
}
```

The server exchanges one JSON-RPC message per line over stdio. Stdout contains only protocol responses.
It handles `initialize`, `notifications/initialized`, `ping`, `tools/list`, and `tools/call`.
The tools accept these arguments:

| Tool | Required | Optional |
| --- | --- | --- |
| `ledger_check` | `file`, `title` | `symbol` |
| `ledger_add` | `file`, `title` | `symbol`, `severity`, `note` |
| `ledger_close` | `id`, `status` | `note` |
| `ledger_list` | None | `status` |

`status` is `fixed`, `wontfix`, or `open`. `ledger_check` returns `known` and `entry`, including a `file_changed` flag.
Tool results contain JSON text. Operation failures set `isError` and leave the server running.

## Give the agent a rule

```text
Before reporting or fixing an issue, run fixledger check or ledger_check.
If it is known, read its status and note before acting. Do not repeat closed findings.
If a fixed finding's file changed, inspect it before deciding whether to reopen it.
Add new findings with fixledger add or ledger_add as you go.
Close findings as fixed or wontfix when resolved, and reopen confirmed regressions.
```

The ledger stores findings supplied by you or your agent. It does not detect bugs or prove that a fix is correct.
This MVP excludes symbol resolution, synchronization, a web interface, and automatic settings installation.
Licensed under MIT. See LICENSE.

## Limitations

- The ledger is a local file; there is no locking for concurrent writers.
- Fuzzy matching compares title words only, so reworded findings can be missed.
- Python 3.9 or later is required.
