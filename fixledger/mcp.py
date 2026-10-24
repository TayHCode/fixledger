import json
import sys

from . import __version__
from .ledger import LedgerError


FINDING = {
    "file": {"type": "string", "minLength": 1},
    "title": {"type": "string", "minLength": 1},
    "symbol": {"type": "string"},
}
TOOLS = [
    ("ledger_check", "Check before reporting or fixing. Returns known findings and file change flags.",
     FINDING, ["file", "title"]),
    ("ledger_add", "Record a new finding. Repeated additions preserve the existing entry.",
     dict(FINDING, severity={"type": "string", "minLength": 1}, note={"type": "string"}), ["file", "title"]),
    ("ledger_close", "Mark a finding fixed or wontfix, or reopen it with status open.",
     {"id": {"type": "string", "minLength": 1}, "status": {"type": "string", "enum": ["fixed", "wontfix", "open"]},
      "note": {"type": "string"}}, ["id", "status"]),
    ("ledger_list", "List recorded findings, optionally filtered by status.",
     {"status": {"type": "string", "enum": ["fixed", "wontfix", "open"]}}, []),
]


def error_response(id, code, message):
    return {"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}}


def handle(request, ledger):
    if not isinstance(request, dict):
        return error_response(None, -32600, "Invalid Request")
    id = request.get("id")
    if (request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str)
            or isinstance(id, bool) or not isinstance(id, (str, int, float, type(None)))):
        return error_response(None, -32600, "Invalid Request")
    if "id" not in request:
        return None
    method = request["method"]
    params = request.get("params", {})
    if not isinstance(params, dict):
        return error_response(id, -32602, "params must be an object")
    if method == "initialize":
        supported = ("2024-11-05", "2025-03-26", "2025-06-18")
        version = params.get("protocolVersion")
        result = {
            "protocolVersion": version if version in supported else supported[-1],
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "fixledger", "version": __version__},
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": [
            {"name": name, "description": description, "inputSchema": {
                "type": "object", "properties": properties, "required": required, "additionalProperties": False,
            }} for name, description, properties, required in TOOLS
        ]}
    elif method == "tools/call":
        name = params.get("name")
        tool = next((tool for tool in TOOLS if tool[0] == name), None)
        if tool is None:
            return error_response(id, -32602, "unknown tool")
        arguments = params.get("arguments", {})
        properties, required = tool[2:]
        if (not isinstance(arguments, dict) or set(arguments) - set(properties)
                or set(required) - set(arguments) or any(not isinstance(v, str) for v in arguments.values())):
            return error_response(id, -32602, "invalid tool arguments")
        try:
            if name == "ledger_check":
                entry = ledger.check(**arguments)
                value = {"known": entry is not None, "entry": entry}
            elif name == "ledger_add":
                value = ledger.add(**arguments)
            elif name == "ledger_close":
                value = ledger.close(**arguments)
            else:
                value = ledger.list(**arguments)
            result = {"content": [{"type": "text", "text": json.dumps(value, sort_keys=True)}], "isError": False}
        except (LedgerError, OSError, UnicodeError) as error:
            result = {"content": [{"type": "text", "text": str(error)}], "isError": True}
    else:
        return error_response(id, -32601, "Method not found")
    return {"jsonrpc": "2.0", "id": id, "result": result}


def serve(ledger):
    for line in sys.stdin:
        try:
            request = json.loads(line)
        except ValueError:
            response = error_response(None, -32700, "Parse error")
        else:
            response = handle(request, ledger)
        if response is not None:
            print(json.dumps(response), flush=True)
