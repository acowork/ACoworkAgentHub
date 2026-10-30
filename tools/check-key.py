#!/usr/bin/env python3
"""Check that a signing key is the key the registry claims to trust.

Publishing signs every package with the secret `ACOWORK_AGENTS_SIGNING_KEY`.
Whether those packages read as "official channel" depends on that key's
fingerprint appearing in `registry/trusted-keys.json` with status `active`.

Those two facts live in different places — one in a repository secret, one in
a committed file — and nothing in the pipeline proves they agree until someone
installs a package and wonders why it is marked as signed by an unknown key.
This tool answers the question up front:

    python tools/check-key.py                      # check .keys/registry.key
    python tools/check-key.py --key path/to/seed   # check any seed file
    python tools/check-key.py --from-base64 "$ACOWORK_AGENTS_SIGNING_KEY"

It prints the fingerprint and whether it is trusted. It never prints the
private key itself.

Exit code: 0 when the key is trusted and active, 1 otherwise.
"""

from __future__ import annotations

import argparse
import base64
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signing import (  # noqa: E402  (local tool module)
    TrustError,
    load_ed25519_private_key,
    load_trusted_keys,
    public_key_fingerprint,
    resolve_trust,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_KEY = REPO_ROOT / ".keys" / "registry.key"


def fingerprint_of_bytes(raw: bytes) -> str:
    """Fingerprint of a raw 32-byte seed (or a 64-byte seed+pub pair)."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    raw = raw.strip()
    if len(raw) == 64:
        raw = raw[:32]
    if len(raw) != 32:
        raise ValueError(f"expected a 32-byte Ed25519 seed, got {len(raw)} bytes")
    key = Ed25519PrivateKey.from_private_bytes(raw)
    return public_key_fingerprint(key.public_key().public_bytes_raw())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--key",
        type=Path,
        default=None,
        help=f"seed file to check (default: {DEFAULT_KEY.relative_to(REPO_ROOT)})",
    )
    parser.add_argument(
        "--from-base64",
        metavar="VALUE",
        help="decode this base64 string instead of reading a file — use it to "
        "verify the value of the ACOWORK_AGENTS_SIGNING_KEY secret",
    )
    args = parser.parse_args(argv)

    try:
        if args.from_base64:
            fingerprint = fingerprint_of_bytes(base64.b64decode(args.from_base64))
        else:
            path = args.key or DEFAULT_KEY
            if not path.is_file():
                print(
                    f"[check-key] {path} not found.\n"
                    "Generate the official channel key with:\n"
                    "    python tools/make-ci-key.py --key-id acowork-official-YYYY-MM\n"
                    "then register its fingerprint in registry/trusted-keys.json and "
                    "base64 the seed into the ACOWORK_AGENTS_SIGNING_KEY secret.",
                    file=sys.stderr,
                )
                return 1
            key = load_ed25519_private_key(path)
            fingerprint = public_key_fingerprint(key.public_key().public_bytes_raw())
    except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
        print(f"[check-key] cannot read the key: {exc}", file=sys.stderr)
        return 1

    print(f"[check-key] fingerprint: {fingerprint}")

    try:
        trust = resolve_trust(REPO_ROOT, fingerprint)
    except TrustError as exc:
        print(f"[check-key] {exc}", file=sys.stderr)
        return 1

    # `resolve_trust` reports a trusted key as {"trusted": True, ...} with no
    # "status" field; only untrusted answers carry one.
    if trust.get("trusted"):
        print(f"[check-key] channel: {trust.get('channel')}")
        if trust.get("key_id"):
            print(f"[check-key] key id: {trust['key_id']}")
        print("[check-key] OK: packages signed with this key read as official channel.")
        return 0

    print(f"[check-key] status: {trust.get('status', 'unknown')}")
    if trust.get("key_id"):
        print(f"[check-key] key id: {trust['key_id']}")
    print(
        f"[check-key] MISMATCH: {trust.get('reason', 'this key is not trusted')}.\n"
        "Packages signed with it will be reported as signed by an unknown key. "
        "Either add its fingerprint to registry/trusted-keys.json (status: "
        "active) or set the secret to the key that is already listed there.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
