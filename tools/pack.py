#!/usr/bin/env python3
"""Pack an agent directory into a signed .agent package.

The package layout and the Signing Block V2 layout are byte-compatible with
the ACowork main repo (core/acowork-sign), so packages produced here verify
with the stock `acowork-verify` and install with the stock `acowork-node`
install path, which requires a valid signature outside dev mode.

Capability metadata (category / tier / tags / skills) is not written into
the signed attributes. It lives in `manifest.toml`, which is inside the
package and therefore inside the digest list — so the signature covers it
without any format change. See docs/trust-model.md.

Usage:
    python tools/pack.py agents/<owner>/<name> --out build/
    python tools/pack.py agents/<owner>/<name> --out build/ --key .keys/registry.key
    python tools/pack.py --verify build/<agent_id>.agent

Exit codes:
    0  ok
    1  data error (bad manifest, missing key, verification failure)
    2  environment error
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import tomllib
except ModuleNotFoundError:
    sys.exit("[pack] FATAL: needs Python 3.11+ (tomllib). Exit 2")

import signing  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Directory names that never go into a distributable package.
PACKAGE_EXCLUDE_DIRS = {".git", ".github", "__pycache__", ".venv", "node_modules"}


def fail(msg: str) -> None:
    print(f"[pack] ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def collect_files(agent_dir: Path) -> list[tuple[str, Path]]:
    """Return (archive_name, source_path) pairs, sorted for reproducibility."""
    out: list[tuple[str, Path]] = []
    for path in sorted(agent_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(agent_dir)
        parts = rel.parts
        if any(part in PACKAGE_EXCLUDE_DIRS for part in parts):
            continue
        if path.suffix.lower() in {".agent", ".key", ".pem"}:
            continue
        out.append(("/".join(parts), path))
    out.sort(key=lambda item: item[0])
    return out


def compute_digests(files: list[tuple[str, Path]]) -> list[dict]:
    digests = []
    for name, path in files:
        digests.append(
            {"section_name": name, "digest": hashlib.sha256(path.read_bytes()).hexdigest()}
        )
    digests.sort(key=lambda d: d["section_name"].encode("utf-8"))
    return digests


def read_skills(agent_dir: Path) -> list[str]:
    """Skill directory names — mirrors the main repo's `read_skills`."""
    skills_dir = agent_dir / "skills"
    if not skills_dir.is_dir():
        return []
    return sorted(
        d.name for d in skills_dir.iterdir() if d.is_dir() and (d / "SKILL.md").is_file()
    )


def pack_agent(agent_dir: Path, out_dir: Path, key_path: Path | None) -> dict:
    manifest_path = agent_dir / "manifest.toml"
    if not manifest_path.is_file():
        fail(f"{agent_dir} has no manifest.toml")

    try:
        manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        fail(f"cannot parse {manifest_path}: {exc}")

    for required in ("agent_id", "version", "name", "category", "tier"):
        if not manifest.get(required):
            fail(f"{agent_dir}/manifest.toml missing required field {required!r}")

    files = collect_files(agent_dir)
    if not files:
        fail(f"{agent_dir} contains no files to pack")

    digests = compute_digests(files)
    attrs = signing.build_attrs(
        signing_time=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )

    # 1. build the clean zip
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, path in files:
            zf.writestr(name, path.read_bytes())
    clean_zip = buffer.getvalue()

    # 2. sign (or leave unsigned)
    fingerprint = None
    if key_path is None:
        print(
            "[pack] WARN  no --key given: producing an UNSIGNED package. "
            "Unsigned packages are rejected by the publish workflow.",
            file=sys.stderr,
        )
        signed_bytes = clean_zip
    else:
        private_key = signing.load_ed25519_private_key(key_path)
        from cryptography.hazmat.primitives import serialization

        public_key = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        payload = signing.signature_payload(digests, attrs)
        signature = private_key.sign(payload)
        block = signing.encode_signing_block(
            public_key=public_key,
            signature=signature,
            digests=digests,
            attrs=attrs,
        )
        signed_bytes = signing.insert_block_before_cd(clean_zip, block)
        fingerprint = signing.public_key_fingerprint(public_key)

    out_dir.mkdir(parents=True, exist_ok=True)
    agent_id = manifest["agent_id"]
    pkg_path = out_dir / f"{agent_id}.agent"
    pkg_path.write_bytes(signed_bytes)

    sha = hashlib.sha256(signed_bytes).hexdigest()
    (out_dir / f"{agent_id}.agent.sha256").write_text(f"{sha}  {agent_id}.agent\n", encoding="utf-8")

    return {
        "agent_id": agent_id,
        "version": manifest["version"],
        "package": pkg_path,
        "size_bytes": len(signed_bytes),
        "sha256": sha,
        "signed": key_path is not None,
        "signer_fingerprint": fingerprint,
        "signing_time": attrs["signing_time"],
        "file_count": len(digests),
    }


def verify_package(pkg_path: Path) -> dict:
    """Verify a .agent package produced by this repo (or the main repo).

    Answers the two questions the registry cares about:

    1. has it been tampered with?  -> the signature must verify, and every
       digested section must still match its recorded digest.
    2. did it come through the official channel? -> the signer fingerprint
       must appear in registry/trusted-keys.json.

    The signature is checked against the public key embedded in the package,
    exactly as the main repo's `verify_package` does. That proves the package
    is internally consistent and unmodified since signing; it does not by
    itself prove the signer is the official channel, which is what the
    trusted key set is for.
    """
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    data = pkg_path.read_bytes()
    block_content = signing.find_signing_block(data)
    if block_content is None:
        fail(f"{pkg_path}: no Signing Block V2 found")
    block = signing.decode_signing_block(block_content)
    signer = block["signers"][0]

    cert = signer["certificates"][0]
    public_key = cert["data"]
    if len(public_key) != 32:
        fail(f"{pkg_path}: invalid public key length {len(public_key)}")

    # Recompute the digests over the package contents and compare.
    digests = [
        {"section_name": d["section_name"], "digest": d["digest"].hex()} for d in signer["digests"]
    ]
    with zipfile.ZipFile(BytesIO(data)) as zf:
        names = sorted(
            n for n in zf.namelist() if not n.endswith("/") and n != "META-INF/SIGNING.BLOCK"
        )
        for item in digests:
            if item["section_name"] not in names:
                fail(f"{pkg_path}: section {item['section_name']!r} missing from package")
            actual = hashlib.sha256(zf.read(item["section_name"])).hexdigest()
            if actual != item["digest"]:
                fail(
                    f"{pkg_path}: digest mismatch for {item['section_name']!r} "
                    "(package has been modified since signing)"
                )
        if len(names) != len(digests):
            fail(
                f"{pkg_path}: package contains {len(names)} file(s) but the signature "
                f"covers {len(digests)} — unsigned content added"
            )

    payload = signing.signature_payload(digests, signer["signed_attrs"])
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signer["signature"], payload)
    except InvalidSignature:
        fail(f"{pkg_path}: signature verification failed")

    fingerprint = signing.public_key_fingerprint(public_key)
    trust = signing.resolve_trust(REPO_ROOT, fingerprint)

    return {
        "path": str(pkg_path),
        "signature_valid": True,
        "tamper_detected": False,
        "signer_fingerprint": fingerprint,
        "official_channel": trust["trusted"],
        "trust": trust,
        "sections_count": len(digests),
        "signed_attrs": signer["signed_attrs"],
        "signing_time": signer["signed_attrs"].get("signing_time"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("agent_dir", nargs="?", help="agent directory to pack")
    ap.add_argument("--out", default="build", help="output directory for .agent packages")
    ap.add_argument("--key", help="Ed25519 seed file (32 raw bytes) used to sign")
    ap.add_argument("--verify", help="verify an existing .agent package instead of packing")
    args = ap.parse_args()

    if args.verify:
        result = verify_package(Path(args.verify))
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    if not args.agent_dir:
        ap.error("agent_dir is required unless --verify is used")

    agent_dir = Path(args.agent_dir).resolve()
    if not agent_dir.is_dir():
        fail(f"{agent_dir} is not a directory")

    key_path = Path(args.key).resolve() if args.key else None
    if key_path and not key_path.is_file():
        fail(f"key file {key_path} not found")

    result = pack_agent(agent_dir, Path(args.out).resolve(), key_path)
    print(
        f"[pack] {result['agent_id']} v{result['version']} -> "
        f"{result['package'].name} ({result['size_bytes']} bytes, sha256 {result['sha256'][:16]}...) "
        f"signed={result['signed']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
