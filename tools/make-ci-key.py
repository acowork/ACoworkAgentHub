#!/usr/bin/env python3
"""Generate an Ed25519 keypair for the registry's signing key.

Two modes:

    # One-off bootstrap of the official channel key (run by a maintainer).
    python tools/make-ci-key.py --output-dir .keys

    # Ephemeral key for a local dry run (never published).
    python tools/make-ci-key.py --output-dir .keys --ephemeral

The private key is a raw 32-byte seed. It belongs in a CI secret and must
never be committed. The public key and its fingerprint go into
`registry/trusted-keys.json` — see registry/README.md.

The fingerprint is SHA-256 over the raw 32-byte public key, hex-encoded,
which is the same value the main repo's `acowork-verify` prints.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import signing  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--output-dir",
        default=".keys",
        help="directory to write the keypair into (default: .keys)",
    )
    ap.add_argument(
        "--ephemeral",
        action="store_true",
        help="mark the key as a throwaway local dry-run key",
    )
    ap.add_argument(
        "--key-id",
        default="acowork-official",
        help="stable identifier for this key, recorded in trusted-keys.json",
    )
    args = ap.parse_args()

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    out_dir = Path(args.output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    key = Ed25519PrivateKey.generate()
    seed = key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_key = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    key_path = out_dir / "registry.key"
    pub_path = out_dir / "registry.pub"
    key_path.write_bytes(seed)
    key_path.chmod(0o600)
    pub_path.write_bytes(public_key)

    fingerprint = signing.public_key_fingerprint(public_key)

    print(f"[make-ci-key] private key : {key_path}  (NEVER commit)")
    print(f"[make-ci-key] public key  : {pub_path}")
    print(f"[make-ci-key] fingerprint : {fingerprint}")
    print(f"[make-ci-key] key_id      : {args.key_id}")
    print()
    print("Add to registry/trusted-keys.json:")
    print(
        "  {\n"
        f'    "key_id": "{args.key_id}",\n'
        f'    "fingerprint": "{fingerprint}",\n'
        f'    "public_key": "{public_key.hex()}",\n'
        '    "label": "ACowork official channel",\n'
        '    "status": "active"\n'
        "  }"
    )
    if args.ephemeral:
        print()
        print("[make-ci-key] EPHEMERAL key: do not add it to trusted-keys.json.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
