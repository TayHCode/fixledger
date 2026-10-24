import json
import sys

from .ledger import Ledger, LedgerError, describe, text


def run(path=None):
    try:
        event = json.load(sys.stdin)
    except ValueError as error:
        raise LedgerError("invalid hook JSON") from error
    if not isinstance(event, dict):
        raise LedgerError("hook input must be an object")
    name = event.get("hook_event_name")
    if name not in ("SessionStart", "PreCompact"):
        return
    cwd = text(event.get("cwd"), "cwd", required=True)
    entries = [entry for entry in Ledger(path, cwd=cwd).list() if entry["status"] != "open"]
    if not entries:
        return
    context = (
        "Recorded findings: do not report or fix again; call check before reporting/fixing, add new findings.\n"
        + "\n".join(describe(entry) for entry in entries)
    )
    print(json.dumps({"hookSpecificOutput": {"hookEventName": name, "additionalContext": context}}))
