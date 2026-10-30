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
import shutil
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pack  # noqa: E402
import signing  # noqa: E402


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


def package_section_digests(pkg_path: Path) -> dict[str, str]:
    """The file digests a published package is signed over, by section name.

    The signing block is not a zip entry: it is inserted before the central
    directory, so it is located in the raw bytes (`signing.find_signing_block`)
    exactly the way the verifier and the main repo locate it.
    """
    block = signing.find_signing_block(pkg_path.read_bytes())
    if block is None:
        raise ValueError("no signing block found")
    signer = signing.decode_signing_block(block)["signers"][0]
    return {d["section_name"]: d["digest"].hex() for d in signer["digests"]}


def package_signer_fingerprint(pkg_path: Path) -> str | None:
    """Who signed a published package, as a public-key fingerprint.

    Content matching alone is not enough to reuse a package: after a key
    rotation the old package still carries exactly the right bytes, so a
    content-only check would happily publish a package signed by a key that
    `trusted-keys.json` no longer lists — every agent would read as
    "unknown signer" while the release looked untouched.
    """
    try:
        block = signing.find_signing_block(pkg_path.read_bytes())
        if block is None:
            return None
        signer = signing.decode_signing_block(block)["signers"][0]
        return signing.public_key_fingerprint(signer["certificates"][0]["data"])
    except Exception:  # noqa: BLE001 - unreadable means not reusable
        return None


def package_matches_source(pkg_path: Path, agent_dir: Path) -> bool:
    """Does this published package carry exactly the bytes in `agent_dir`?

    Reuse is decided by content, not by bookkeeping. A digest file that lies
    (stale, hand-edited, produced by a different checkout) must not be able to
    publish an old package under a new version — so the published bytes are
    compared against the source tree before anything is reused.
    """
    try:
        signed = package_section_digests(pkg_path)
    except Exception as exc:  # noqa: BLE001 - a malformed package is simply not reusable
        print(f"[ci-pack-all] WARN {pkg_path.name}: cannot read signature block ({exc})")
        return False
    current = {d["section_name"]: d["digest"] for d in pack.compute_digests(pack.collect_files(agent_dir))}
    return signed == current


def read_changed(path: Path | None) -> set[str] | None:
    """Agent ids to rebuild, or None when no list was given (build everything)."""
    if path is None:
        return None
    return {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="dist", help="output directory")
    ap.add_argument("--key", required=True, help="Ed25519 seed file")
    ap.add_argument("--changed", default=None,
                    help="file of agent ids to rebuild (one per line); others are reused. "
                         "Omit to build everything.")
    ap.add_argument("--reuse", default=None,
                    help="directory of previously published packages to reuse from")
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

    changed = read_changed(Path(args.changed) if args.changed else None)
    reuse_dir = Path(args.reuse).resolve() if args.reuse else None

    # Reuse is only ever allowed within one signing identity.
    key_fingerprint = None
    if reuse_dir is not None:
        key_fingerprint = signing.public_key_fingerprint(
            signing.load_ed25519_private_key(key_path).public_key().public_bytes_raw()
        )
        print(f"[ci-pack-all] reuse enabled; packages not signed by {key_fingerprint[:12]} "
              "will be re-signed")

    packed = 0
    reused = 0
    resigned = 0
    for agent_dir in dirs:
        rel = agent_dir.relative_to(REPO_ROOT)
        manifest = tomllib.loads((agent_dir / "manifest.toml").read_text(encoding="utf-8"))
        agent_id = manifest["agent_id"]
        target = out_dir / f"{agent_id}.agent"

        # Reuse only when the published bytes are provably this source tree.
        if reuse_dir is not None and (changed is None or agent_id not in changed):
            candidate = reuse_dir / f"{agent_id}.agent"
            if candidate.is_file() and package_matches_source(candidate, agent_dir):
                signer = package_signer_fingerprint(candidate)
                if signer != key_fingerprint:
                    print(f"[ci-pack-all] {rel}: content matches but it is signed by "
                          f"{(signer or 'no key')[:12]} != {(key_fingerprint or '')[:12]} "
                          "- re-signing")
                    resigned += 1
                else:
                    out_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(candidate, target)
                    reused += 1
                    print(f"[ci-pack-all] {rel}: reused published package (content and signer match)")
                    continue

        print(f"::group::pack {rel}")
        result = pack.pack_agent(agent_dir, out_dir, key_path)
        packed += 1
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

    print(f"[ci-pack-all] OK: {len(dirs)} agent(s) -> {packed} packed, {reused} reused"
          + (f", {resigned} re-signed for a new key" if resigned else "")
          + "; all verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
