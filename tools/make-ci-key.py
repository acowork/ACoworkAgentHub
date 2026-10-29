#!/usr/bin/env python3
"""Generate an ephemeral Ed25519 key for CI.

The key exists only for the duration of the job. It is never uploaded, never
committed and never reused — CI only needs *a* key that round-trips through
the packer and the verifier.

Usage:
    python tools/make-ci-key.py .keys/ci.key
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    out = Path(sys.argv[1])
    out.parent.mkdir(parents=True, exist_ok=True)

    key = Ed25519PrivateKey.generate()
    seed = key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    out.write_bytes(seed)
    out.chmod(0o600)
    print(f"[make-ci-key] wrote {out} (ephemeral, CI-only)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
