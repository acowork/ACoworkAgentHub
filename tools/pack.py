#!/usr/bin/env python3
"""Pack an agent directory into a signed .agent package.

The package layout and the Signing Block V2 layout are byte-compatible with
the ACowork main repo (core/acowork-sign), so packages produced here can be
verified by acowork-verify once the main repo understands SignedAttributes v2
(see docs/signed-attributes-v2.md).

Usage:
    python tools/pack.py agents/<owner>/<name> --out build/
    python tools/pack.py agents/<owner>/<name> --out build/ --key .keys/dev.key
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


def build_catalog_layer(manifest: dict, skills: list[str]) -> dict:
    """The capability layer of SignedAttributes v2.

    These are the claims the catalog UI needs to trust: which category the
    agent belongs to, how capable it is, and what it can do. Because they are
    inside the signed attributes, a publisher cannot advertise an agent as
    `core` while shipping `Shell` permissions.
    """
    permissions = sorted({p.get("type") for p in manifest.get("permissions", []) if p.get("type")})
    capabilities = [
        {"key": key, "description": (value or {}).get("description", "")}
        for key, value in sorted((manifest.get("capabilities") or {}).items())
    ]
    return {
        "agent_id": manifest["agent_id"],
        "version": manifest["version"],
        "category": manifest["category"],
        "tier": manifest["tier"],
        "tags": sorted(manifest.get("tags", [])),
        "skills": skills,
        "role": manifest["role"],
        "capabilities": capabilities,
        "permissions": permissions,
    }


def build_publisher_layer(manifest: dict) -> dict:
    """The identity layer of SignedAttributes v2.

    `affiliation` is a self-declared statement, signed so it cannot be edited
    after the fact — but it is NOT a trust decision and must never be rendered
    as an official badge. See docs/signed-attributes-v2.md.
    """
    publisher = manifest.get("publisher", {})
    layer = {
        "id": publisher["id"],
        "display_name": publisher["display_name"],
        "affiliation": publisher.get("affiliation", "unknown"),
        "self_declared": True,
    }
    for optional in ("contact", "homepage"):
        if publisher.get(optional):
            layer[optional] = publisher[optional]
    return layer


def pack_agent(agent_dir: Path, out_dir: Path, key_path: Path | None) -> dict:
    manifest_path = agent_dir / "manifest.toml"
    if not manifest_path.is_file():
        fail(f"{agent_dir} has no manifest.toml")

    try:
        manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        fail(f"cannot parse {manifest_path}: {exc}")

    for required in ("agent_id", "version", "name", "category", "tier", "publisher"):
        if not manifest.get(required):
            fail(f"{agent_dir}/manifest.toml missing required field {required!r}")

    skills_dir = agent_dir / "skills"
    skills = []
    if skills_dir.is_dir():
        skills = sorted(
            d.name for d in skills_dir.iterdir() if d.is_dir() and (d / "SKILL.md").is_file()
        )

    files = collect_files(agent_dir)
    if not files:
        fail(f"{agent_dir} contains no files to pack")

    digests = compute_digests(files)

    attrs = signing.build_v2_attrs(
        signing_time=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        publisher=build_publisher_layer(manifest),
        catalog=build_catalog_layer(manifest, skills),
        integrity={
            "digest_algorithm": "sha256",
            "file_count": len(digests),
            "content_root": hashlib.sha256(
                b"".join(bytes.fromhex(d["digest"]) for d in digests)
            ).hexdigest(),
        },
    )

    # 1. build the clean zip
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, path in files:
            zf.writestr(name, path.read_bytes())
    clean_zip = buffer.getvalue()

    # 2. sign (or leave unsigned)
    if key_path is None:
        print(
            "[pack] WARN  no --key given: producing an UNSIGNED package. "
            "Unsigned packages are rejected by the publish workflow.",
            file=sys.stderr,
        )
        signed_bytes = clean_zip
        public_key = b""
        signature = b""
    else:
        private_key = signing.load_ed25519_private_key(key_path)
        from cryptography.hazmat.primitives import serialization

        public_key = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        payload = signing.v2_signature_payload(digests, attrs)
        signature = private_key.sign(payload)
        block = signing.encode_signing_block(
            public_key=public_key,
            signature=signature,
            digests=digests,
            attrs=attrs,
        )
        signed_bytes = signing.insert_block_before_cd(clean_zip, block)

    out_dir.mkdir(parents=True, exist_ok=True)
    agent_id = manifest["agent_id"]
    pkg_path = out_dir / f"{agent_id}.agent"
    pkg_path.write_bytes(signed_bytes)

    sha = hashlib.sha256(signed_bytes).hexdigest()
    (out_dir / f"{agent_id}.agent.sha256").write_text(f"{sha}  {agent_id}.agent\n", encoding="utf-8")
    (out_dir / f"{agent_id}.attrs.json").write_bytes(signing.canonical_json(attrs) + b"\n")

    return {
        "agent_id": agent_id,
        "version": manifest["version"],
        "package": pkg_path,
        "size_bytes": len(signed_bytes),
        "sha256": sha,
        "signed": key_path is not None,
        "attrs": attrs,
    }


def verify_package(pkg_path: Path) -> dict:
    """Verify a .agent package produced by this repo (or the main repo)."""
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

    attrs = signer["signed_attrs"]
    version = attrs.get("version", 1)

    # Recompute the digests from the package contents.
    with zipfile.ZipFile(pkg_path) as zf:
        names = sorted(zf.namelist())
        computed = {
            name: hashlib.sha256(zf.read(name)).hexdigest()
            for name in names
            if name != "META-INF/SIGNING.BLOCK"
        }

    signed_digests = {d["section_name"]: d["digest"].hex() for d in signer["digests"]}
    if signed_digests != computed:
        missing = sorted(set(signed_digests) ^ set(computed))
        changed = sorted(n for n in set(signed_digests) & set(computed) if signed_digests[n] != computed[n])
        fail(f"{pkg_path}: digest mismatch (missing/extra: {missing}; changed: {changed})")

    ver = Ed25519PublicKey.from_public_bytes(public_key)
    if version >= 2:
        payload = signing.v2_signature_payload(signer["digests"], attrs)
    else:
        # Legacy v1 payload: concatenated (name, digest) pairs followed by the
        # raw attribute JSON as stored in the block.
        payload = b""
        for item in signer["digests"]:
            payload += item["section_name"].encode("utf-8") + item["digest"]
        payload += signer["signed_attrs_raw"]

    try:
        ver.verify(signature=signer["signature"], data=payload)
    except InvalidSignature:
        fail(f"{pkg_path}: signature verification FAILED")

    return {
        "agent_id": attrs.get("catalog", {}).get("agent_id", "unknown"),
        "attrs_version": version,
        "key_fingerprint": signing.public_key_fingerprint(public_key),
        "signing_time": attrs.get("signing_time"),
        "sections": len(signer["digests"]),
        "valid": True,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("agent_dir", nargs="?", help="agent directory to pack")
    ap.add_argument("--out", default="build", help="output directory (default: build)")
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
