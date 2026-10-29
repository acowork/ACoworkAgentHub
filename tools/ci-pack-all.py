#!/usr/bin/env python3
"""Pack every agent in the repository and verify each resulting package.

This is the CI smoke test: it proves that every agent can be packed, signed
and verified with the current tooling. A signing bug fails the build here
instead of shipping.

Usage:
    python tools/ci-pack-all.py --out dist --key .keys/ci.key
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pack  # noqa: E402


def agent_dirs() -> list[Path]:
    agents_root = REPO_ROOT / "agents"
    if not agents_root.is_dir():
        return []
    found = []
    for owner_dir in sorted(agents_root.iterdir()):
        if not owner_dir.is_dir() or owner_dir.name.startswith(("_", ".")):
            continue
        for name_dir in sorted(owner_dir.iterdir()):
            if name_dir.is_dir() and (name_dir / "manifest.toml").is_file():
                found.append(name_dir)
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="dist", help="output directory")
    ap.add_argument("--key", required=True, help="Ed25519 seed file")
    args = ap.parse_args()

    out_dir = (REPO_ROOT / args.out).resolve()
    key_path = (REPO_ROOT / args.key).resolve()
    if not key_path.is_file():
        print(f"[ci-pack-all] ERROR: key {key_path} not found", file=sys.stderr)
        return 1

    dirs = agent_dirs()
    if not dirs:
        print("[ci-pack-all] ERROR: no agents found", file=sys.stderr)
        return 1

    for agent_dir in dirs:
        rel = agent_dir.relative_to(REPO_ROOT)
        print(f"::group::pack {rel}")
        result = pack.pack_agent(agent_dir, out_dir, key_path)
        verification = pack.verify_package(result["package"])
        if not verification.get("signature_valid"):
            print(f"[ci-pack-all] ERROR: {rel} failed verification", file=sys.stderr)
            return 1
        print(
            f"[ci-pack-all] {rel} -> {result['package'].name} "
            f"({result['file_count']} sections, "
            f"{'official-channel' if verification['official_channel'] else 'third-party'}) OK"
        )
        print("::endgroup::")

    print(f"[ci-pack-all] OK: {len(dirs)} agent(s) packed and verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
