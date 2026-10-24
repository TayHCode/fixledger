import argparse
import json
import sys

from . import __version__
from .ledger import Ledger, LedgerError, STATUSES, describe


def parser():
    root = argparse.ArgumentParser(description="Remember findings before reporting or fixing them again.")
    root.add_argument("--version", action="version", version=__version__)
    root.add_argument("--ledger", help="ledger path; defaults to FIXLEDGER_FILE or the repository root")
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("add", "check", "fix", "wontfix", "reopen", "list", "hook", "mcp"):
        command = commands.add_parser(name)
        command.add_argument("--ledger", default=argparse.SUPPRESS, help="override the ledger path")
        if name in ("add", "check"):
            command.add_argument("--file", required=True)
            command.add_argument("--symbol", default="")
            command.add_argument("--title", required=True)
        if name == "add":
            command.add_argument("--severity", default="medium")
            command.add_argument("--note", default="")
        if name in ("fix", "wontfix", "reopen"):
            command.add_argument("id")
            command.add_argument("--note")
        if name == "list":
            command.add_argument("--status", choices=STATUSES)
            command.add_argument("--json", action="store_true")
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "hook":
            from .hook import run
            run(args.ledger)
        elif args.command == "mcp":
            from .mcp import serve
            serve(Ledger(args.ledger))
        else:
            ledger = Ledger(args.ledger)
            if args.command == "add":
                print(describe(ledger.add(args.file, args.title, args.symbol, args.severity, args.note)))
            elif args.command == "check":
                entry = ledger.check(args.file, args.title, args.symbol)
                print(describe(entry) if entry else "new finding")
                return 3 if entry else 0
            elif args.command == "list":
                entries = ledger.list(args.status)
                if args.json:
                    print(json.dumps(entries, indent=2, sort_keys=True))
                else:
                    for entry in entries:
                        print(describe(entry))
            else:
                status = {"fix": "fixed", "wontfix": "wontfix", "reopen": "open"}[args.command]
                print(describe(ledger.close(args.id, status, args.note)))
        return 0
    except (LedgerError, OSError, UnicodeError) as error:
        print("fixledger: {}".format(error), file=sys.stderr)
        return 2
