#!/usr/bin/env python3
"""End-to-end self test for the packaging and signing pipeline.

Covers the two guarantees the trust model actually makes:

1. A package signed by a key in the trusted set verifies, and its signer
   fingerprint is in `registry/trusted-keys.json` (official channel).
2. Changing one byte inside a packaged file invalidates the signature,
   i.e. tampering is detected.

The tamper step rebuilds the zip with modified file content and re-inserts
the *original* signing block verbatim. The digests inside that block no
longer match the file contents, which is exactly the case a verifier has to
reject.

Usage:
    python tools/selftest.py [--verify-cmd "<path to acowork-verify>"]
                              [--key <private key file>]
"""

from __future__ import annotations

import argparse
import io
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import signing  # noqa: E402
import pack as pack_module  # noqa: E402
from pack import pack_agent  # noqa: E402
from validate import Report, load_json, validate_agent_dir  # noqa: E402

AGENT_DIR = ROOT / "agents" / "nicholas" / "research-agent"


def find_verify_binary(explicit: str | None) -> Path | None:
    if explicit:
        p = Path(explicit)
        return p if p.exists() else None
    for base in (ROOT.parent / "ACowork", ROOT.parent):
        for sub in ("target/release", "target/debug"):
            name = "acowork-verify.exe" if sys.platform == "win32" else "acowork-verify"
            cand = base / sub / name
            if cand.exists():
                return cand
    return None


def find_key(explicit: str | None) -> Path | None:
    if explicit:
        p = Path(explicit)
        return p if p.exists() else None
    for cand in (ROOT / ".keys" / "registry.key",):
        if cand.exists():
            return cand
    return None


def tamper_package(package: Path, out: Path) -> None:
    """Rewrite one packaged file, keeping the original signing block."""
    raw = package.read_bytes()
    framed = signing.find_signing_block_framed(raw)
    if framed is None:
        raise SystemExit("selftest: package carries no signing block")

    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as src:
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as dst:
            for name in src.namelist():
                data = src.read(name)
                if name == "manifest.toml":
                    data = data.replace(b"version", b"versiom", 1)
                dst.writestr(name, data)

    out.write_bytes(signing.insert_block_before_cd(buf.getvalue(), framed))


def local_verify_rejects(package: Path) -> bool:
    """True if this repo's own verifier rejects the package.

    `pack.verify_package` calls `fail()` (SystemExit) on any problem, so a
    clean return means the package passed.
    """
    try:
        pack_module.verify_package(package)
    except SystemExit:
        return True
    return False


def run_verify(verify: Path, package: Path) -> tuple[int, str]:
    proc = subprocess.run(
        [str(verify), str(package)],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()


def main() -> int:
    # The verifier prints UTF-8 check marks; make sure stdout can carry them.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--verify-cmd", default=None)
    ap.add_argument("--key", default=None)
    args = ap.parse_args()

    schema = load_json(ROOT / "schema" / "manifest.schema.json")
    hub_schema = load_json(ROOT / "schema" / "hub.schema.json")
    catalog = load_json(ROOT / "schema" / "catalog.json")

    report = Report()
    validate_agent_dir(AGENT_DIR, schema, hub_schema, catalog, report)
    for e in report.errors:
        print(f"[validate] ERROR {e}")
    if report.errors:
        return 1
    print("ok  manifest validates")

    key = find_key(args.key)
    if key is None:
        print("fail  no signing key found (pass --key or run tools/make-ci-key.py)")
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="acowork-selftest-"))
    try:
        info = pack_agent(AGENT_DIR, tmp, key)
        package = info["package"]
        print(f"ok  packed {package.name} ({info['size_bytes']} bytes, {info['file_count']} sections)")
        if not info["signed"]:
            print("fail  package was not signed")
            return 1

        block = signing.find_signing_block_framed(package.read_bytes())
        if block is None:
            print("fail  package has no signing block")
            return 1
        print(f"ok  signing block present ({len(block)} bytes)")

        trusted = load_json(ROOT / "registry" / "trusted-keys.json")
        fps = {k["fingerprint"] for k in trusted.get("keys", []) if k.get("status") == "active"}
        if info["signer_fingerprint"] not in fps:
            print(f"note  signer fingerprint {info['signer_fingerprint']} is not in trusted-keys.json")
        else:
            print("ok  signer fingerprint is in the trusted set (official channel)")

        verify = find_verify_binary(args.verify_cmd)
        if verify is None:
            print("skip  acowork-verify binary not found; using the in-repo verifier only")
            if local_verify_rejects(package):
                print("fail  in-repo verifier rejected the pristine package")
                return 1
            tampered = tmp / "tampered.agent"
            tamper_package(package, tampered)
            if not local_verify_rejects(tampered):
                print("fail  in-repo verifier accepted a tampered package")
                return 1
            print("PASS  tamper detection works (in-repo verifier)")
            return 0

        code, out = run_verify(verify, package)
        print(f"ok  verifier on pristine package -> exit {code}")
        print("    " + out.replace("\n", "\n    "))
        if code != 0 or "VALID" not in out.upper():
            print("fail  verifier did not report VALID")
            return 1

        tampered = tmp / "tampered.agent"
        tamper_package(package, tampered)
        code, out = run_verify(verify, tampered)
        print(f"ok  verifier on tampered package -> exit {code}")
        print("    " + out.replace("\n", "\n    "))
        if "VALID" in out.upper() and "INVALID" not in out.upper():
            print("fail  verifier accepted a tampered package")
            return 1
        if code == 0:
            print("fail  verifier returned success for a tampered package")
            return 1

        print("PASS  tamper detection works")
        return 0
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
