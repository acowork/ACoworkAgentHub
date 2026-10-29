#!/usr/bin/env python3
"""Validate a generated index.json against schema/agent.schema.json.

Usage:
    python tools/check-index.py dist/index.json schema/agent.schema.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2

    try:
        import jsonschema
    except ModuleNotFoundError:
        print("[check-index] ERROR: jsonschema is not installed", file=sys.stderr)
        return 2

    index_path = Path(sys.argv[1])
    schema_path = Path(sys.argv[2])

    doc = json.loads(index_path.read_text(encoding="utf-8"))
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)

    failed = False
    for entry in doc.get("agents", []):
        errors = sorted(validator.iter_errors(entry), key=lambda e: list(e.absolute_path))
        for err in errors:
            loc = "/".join(str(p) for p in err.absolute_path) or "<root>"
            print(f"[check-index] ERROR {entry.get('agent_id', '?')}: {loc}: {err.message}")
            failed = True

    if failed:
        return 1

    print(f"[check-index] OK: {len(doc.get('agents', []))} entries valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
