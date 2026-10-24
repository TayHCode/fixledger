import hashlib
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path


STATUSES = ("open", "fixed", "wontfix")
FIELDS = {
    "id", "file", "symbol", "title", "severity", "status", "note",
    "first_seen", "updated", "rev", "file_hash",
}


class LedgerError(ValueError):
    pass


def text(value, name, required=False):
    if not isinstance(value, str) or "\x00" in value:
        raise LedgerError("{} must be text without null bytes".format(name))
    if required and not value.strip():
        raise LedgerError("{} must not be empty".format(name))
    return value


def repo_root(cwd):
    for directory in (cwd, *cwd.parents):
        if (directory / ".git").exists():
            return directory
    return cwd


def normalized_title(title):
    return " ".join(re.findall(r"\w+", title.casefold()))


class Ledger:
    def __init__(self, path=None, cwd=None):
        cwd = Path(cwd or Path.cwd()).resolve()
        self.root = repo_root(cwd)
        selected = path or os.environ.get("FIXLEDGER_FILE")
        self.path = (cwd / selected).resolve() if selected else self.root / ".fixledger.json"

    def load(self):
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        try:
            entries = json.loads(raw)
            if not isinstance(entries, dict):
                raise ValueError("expected an object keyed by finding ID")
            for key, entry in entries.items():
                if not isinstance(entry, dict) or set(entry) != FIELDS:
                    raise ValueError("invalid entry fields")
                if any(not isinstance(value, str) for value in entry.values()):
                    raise ValueError("entry values must be strings")
                if entry["id"] != key or not re.fullmatch(r"[0-9a-f]{10}", key):
                    raise ValueError("invalid finding ID")
                if entry["status"] not in STATUSES:
                    raise ValueError("invalid finding status")
                for name in ("file", "title", "severity"):
                    text(entry[name], name, required=True)
                for name in ("first_seen", "updated"):
                    datetime.fromisoformat(entry[name])
                if entry["file_hash"] and not re.fullmatch(r"[0-9a-f]{40}", entry["file_hash"]):
                    raise ValueError("invalid file hash")
            return entries
        except (ValueError, TypeError) as error:
            raise LedgerError("corrupt ledger: {}".format(error)) from error

    def save(self, entries):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".fixledger-", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(entries, stream, indent=2, sort_keys=True, ensure_ascii=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def finding(self, file, title, symbol=""):
        file = text(file, "file", required=True)
        title = text(title, "title", required=True)
        symbol = text(symbol, "symbol").strip()
        file = os.path.relpath(self.root / file, self.root)
        normalized = normalized_title(title)
        if not normalized:
            raise LedgerError("title must contain a word")
        key = hashlib.sha1("|".join((file, symbol, normalized)).encode("utf-8")).hexdigest()[:10]
        return key, file, title.strip(), symbol

    def add(self, file, title, symbol="", severity="medium", note=""):
        key, file, title, symbol = self.finding(file, title, symbol)
        text(severity, "severity", required=True)
        text(note, "note")
        entries = self.load()
        if key not in entries:
            now = datetime.now(timezone.utc).isoformat()
            entries[key] = dict(
                id=key, file=file, symbol=symbol, title=title, severity=severity,
                status="open", note=note, first_seen=now, updated=now, rev="", file_hash="",
            )
            self.save(entries)
        return entries[key]

    def file_hash(self, file):
        try:
            with (self.root / file).open("rb") as stream:
                digest = hashlib.sha1()
                for block in iter(lambda: stream.read(65536), b""):
                    digest.update(block)
                return digest.hexdigest()
        except FileNotFoundError:
            return ""

    def result(self, entry):
        return dict(entry, file_changed=(
            entry["status"] == "fixed" and self.file_hash(entry["file"]) != entry["file_hash"]
        ))

    def check(self, file, title, symbol=""):
        key, file, title, symbol = self.finding(file, title, symbol)
        entries = self.load()
        if key in entries:
            return self.result(entries[key])
        tokens = set(normalized_title(title).split())
        matches = []
        for entry in entries.values():
            if entry["file"] != file or (symbol and entry["symbol"] and symbol != entry["symbol"]):
                continue
            other = set(normalized_title(entry["title"]).split())
            score = len(tokens & other) / len(tokens | other)
            if score >= 0.6:
                matches.append((score, entry["id"], entry))
        return self.result(max(matches, key=lambda match: match[:2])[2]) if matches else None

    def close(self, id, status="fixed", note=None):
        text(id, "id", required=True)
        if status not in STATUSES:
            raise LedgerError("status must be fixed, wontfix, or open")
        if note is not None:
            text(note, "note")
        entries = self.load()
        if id not in entries:
            raise LedgerError("unknown id: {}".format(id))
        entry = entries[id]
        entry["status"] = status
        entry["updated"] = datetime.now(timezone.utc).isoformat()
        entry["rev"] = ""
        entry["file_hash"] = ""
        if status != "open":
            entry["file_hash"] = self.file_hash(entry["file"])
            try:
                process = subprocess.run(
                    ["git", "-C", str(self.root), "rev-parse", "--short", "HEAD"],
                    capture_output=True, text=True, check=False,
                )
                if process.returncode == 0:
                    entry["rev"] = process.stdout.strip()
            except FileNotFoundError:
                pass
        if note is not None:
            entry["note"] = note
        self.save(entries)
        return entry

    def list(self, status=None):
        if status is not None and status not in STATUSES:
            raise LedgerError("status must be fixed, wontfix, or open")
        return [entry for _, entry in sorted(self.load().items()) if status is None or entry["status"] == status]


def describe(entry):
    status = entry["status"]
    if entry.get("file_changed"):
        status += ", file changed since"
    location = entry["file"] + (":" + entry["symbol"] if entry["symbol"] else "")
    result = "{} [{}] {} {}: {}".format(entry["id"], status, entry["severity"], location, entry["title"])
    return result + ("; " + entry["note"] if entry["note"] else "")
