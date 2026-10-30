#!/usr/bin/env python3
"""Content digest of the published registry — the gate that decides whether
anything has to be packed, signed or released at all.

Why this exists
---------------
A push to `main` is not evidence that the registry changed. Most pushes touch
the site, the docs, or the tooling; some reformat an agent's README; some
revert a change back to bytes that are already published. Every one of those
pushes used to re-pack, re-sign and re-upload every package. That is not just
slow: it re-signs identical content with a new `signing_time`, so the published
bytes change even though the registry did not — which makes the release
history meaningless and breaks HTTP caching for no reason.

The rule the pipeline enforces: **publish only when the published bytes would
actually differ.** Published bytes are a function of three inputs, so the
digest covers all three:

    registry_digest = H(source trees, builder, signing key)

* *source trees* — the exact files that go into each package (`pack.collect_files`
  is the single definition of "what is in a package", so the digest cannot
  disagree with the packer about it).
* *builder* — the code and schema that turn a tree into a package and an index.
  A packer fix changes published bytes without changing any agent, so it must
  force a republish.
* *signing key* — the same content signed by a different key is a different
  published artifact and a different trust claim.

Usage
-----
    # write the digest of the current checkout
    python tools/registry-digest.py --out dist/registry.digest.json \
        --packages dist --key .keys/registry.key

    # compare against what is already published
    python tools/registry-digest.py --compare baseline/registry.digest.json \
        --changed-out .ci/changed.txt

`--changed-out` receives one agent_id per line: the packages to rebuild. An
empty file means "reuse every published package", which is only ever true when
the agents themselves changed. A builder or signer change lists **every** agent,
because published packages made by different code or a different key are not
reusable. A missing baseline means "full build".
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pack  # noqa: E402

#: The schema version of this digest format. Bump it when the meaning of the
#: digest changes, so an old baseline is never compared against a new scheme.
SCHEMA_VERSION = 1

#: Files whose bytes shape the published artifacts. A change here means the
#: registry must be rebuilt even if no agent changed.
BUILDER_FILES = (
    "tools/pack.py",
    "tools/buildtime.py",
    "tools/signing.py",
    "tools/build-index.py",
    "schema/agent.schema.json",
    "schema/manifest.schema.json",
)

DIGEST_FILE = "registry.digest.json"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(data: bytes) -> str:
    """Hash a text file with line endings canonicalised to LF.

    `.gitattributes` normalises these files to LF on checkout, so a working copy
    edited on Windows (CRLF) and a CI checkout (LF) contain the same program in
    different bytes. Only the *digest input* is normalised here: without it the
    builder digest — and therefore the publish gate — would flip on every
    platform switch and republish byte-identical packages.

    Package content is still hashed byte-exact by pack.collect_files, because
    those bytes ship inside the .agent file.
    """
    return sha256_bytes(data.replace(b"\r\n", b"\n").replace(b"\r", b"\n"))


def agent_dirs(agents_root: Path) -> list[Path]:
    """Every agent package directory in the hub, in a deterministic order."""
    if not agents_root.is_dir():
        return []
    found: list[Path] = []
    for owner_dir in sorted(agents_root.iterdir()):
        if not owner_dir.is_dir() or owner_dir.name.startswith(("_", ".")):
            continue
        for name_dir in sorted(owner_dir.iterdir()):
            if name_dir.is_dir() and (name_dir / "manifest.toml").is_file():
                found.append(name_dir)
    return found


def source_digest(agent_dir: Path) -> str:
    """Digest of the exact file set the packer puts into the package.

    Reusing `pack.collect_files` is the point: the gate and the packer cannot
    drift on what counts as the content of an agent.
    """
    lines = [
        f"{name}:{sha256_bytes(path.read_bytes())}" for name, path in pack.collect_files(agent_dir)
    ]
    return sha256_bytes("\n".join(sorted(lines)).encode("utf-8"))


def builder_digest() -> tuple[str, dict[str, str]]:
    files: dict[str, str] = {}
    for rel in BUILDER_FILES:
        path = REPO_ROOT / rel
        if not path.is_file():
            raise SystemExit(f"[registry-digest] builder input {rel} is missing")
        files[rel] = sha256_text(path.read_bytes())
    ordered = "\n".join(f"{rel}:{files[rel]}" for rel in BUILDER_FILES)
    return sha256_bytes(ordered.encode("utf-8")), files


def signer_fingerprint(key_path: Path | None) -> str | None:
    """Fingerprint of the signing key, or None when the key is unavailable.

    The fingerprint is part of the digest because the same content signed by a
    different key is a different published artifact *and* a different trust
    claim — a key rotation must force a republish.
    """
    if key_path is None:
        return None
    from signing import load_ed25519_private_key, public_key_fingerprint

    return public_key_fingerprint(load_ed25519_private_key(key_path).public_key().public_bytes_raw())


def registry_digest(agents: dict[str, dict], builder: str, fingerprint: str | None) -> str:
    lines = [f"{agent_id}\t{info['source_digest']}\t{info['version']}" for agent_id, info in agents.items()]
    payload = "\n".join(
        [
            f"v{SCHEMA_VERSION}",
            f"builder:{builder}",
            f"signer:{fingerprint or 'unsigned'}",
            *sorted(lines),
        ]
    )
    return sha256_bytes(payload.encode("utf-8"))


def build_digest_doc(packages_dir: Path | None, key_path: Path | None) -> dict:
    import tomllib

    builder, builder_files = builder_digest()
    agents: dict[str, dict] = {}
    for agent_dir in agent_dirs(REPO_ROOT / "agents"):
        manifest = tomllib.loads((agent_dir / "manifest.toml").read_text(encoding="utf-8"))
        agent_id = manifest.get("agent_id")
        if not agent_id:
            raise SystemExit(f"[registry-digest] {agent_dir}: manifest has no agent_id")
        entry = {
            "source_digest": source_digest(agent_dir),
            "version": str(manifest.get("version", "")),
            "path": agent_dir.relative_to(REPO_ROOT).as_posix(),
        }
        if packages_dir is not None:
            pkg = packages_dir / f"{agent_id}.agent"
            if pkg.is_file():
                entry["package_sha256"] = sha256_bytes(pkg.read_bytes())
        agents[agent_id] = entry

    fingerprint = signer_fingerprint(key_path)
    return {
        "schema_version": SCHEMA_VERSION,
        "registry_digest": registry_digest(agents, builder, fingerprint),
        "builder_digest": builder,
        "builder_files": builder_files,
        "signer_fingerprint": fingerprint,
        "agent_count": len(agents),
        "agents": dict(sorted(agents.items())),
    }


def compare(baseline_path: Path, current: dict) -> tuple[list[str], list[str]]:
    """Return (changed agent ids, human-readable reasons the registry moved).

    Reporting the component that moved matters: "registry changed" is not
    actionable, "the builder changed" or "the signer changed" is. It also
    catches the operator error of computing a digest with different inputs
    than the baseline (a missing --key flag would otherwise look like a
    content change and trigger a pointless re-sign of everything).
    """
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    reasons: list[str] = []
    if baseline.get("schema_version") != SCHEMA_VERSION:
        return sorted(current["agents"]), [
            f"digest schema {baseline.get('schema_version')} -> {SCHEMA_VERSION}: full rebuild"
        ]

    if baseline.get("builder_digest") != current["builder_digest"]:
        reasons.append(
            f"builder changed ({str(baseline.get('builder_digest'))[:12]} -> "
            f"{current['builder_digest'][:12]}): published bytes differ"
        )
    if baseline.get("signer_fingerprint") != current["signer_fingerprint"]:
        reasons.append(
            "signer differs (one side had no --key, or the key rotated) — "
            "check the invocation before treating this as a content change"
            if None in (baseline.get("signer_fingerprint"), current["signer_fingerprint"])
            else f"signer rotated: {str(current['signer_fingerprint'])[:12]}"
        )

    base_agents = baseline.get("agents", {})
    removed = sorted(set(base_agents) - set(current["agents"]))
    if removed:
        reasons.append(f"{len(removed)} agent(s) removed: {', '.join(removed[:5])}")

    changed = []
    for agent_id, info in current["agents"].items():
        prev = base_agents.get(agent_id)
        if not prev:
            changed.append(agent_id)
        elif (
            prev.get("source_digest") != info["source_digest"]
            or prev.get("version") != info["version"]
        ):
            changed.append(agent_id)
    if changed:
        reasons.append(f"{len(changed)} agent(s) changed content or version")

    # An envelope change (builder or signer) means every published package was
    # produced by code or a key that is no longer current. Reusing them would
    # leave the registry holding two generations of package forever: a builder
    # that starts emitting a new section would never reach the agents whose
    # data did not change. So the whole set is rebuilt — rare, bounded, and the
    # only way "published bytes come from this builder" stays true.
    envelope = any(r.startswith("builder changed") or r.startswith("signer rotated")
                   for r in reasons)
    if envelope:
        return sorted(current["agents"]), reasons
    return sorted(changed), reasons


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", help=f"write the digest here (e.g. dist/{DIGEST_FILE})")
    ap.add_argument("--packages", help="package directory, to record published sha256 per agent")
    ap.add_argument("--key", help="signing key, to record the signer fingerprint")
    ap.add_argument("--compare", metavar="BASELINE", help="baseline digest file to diff against")
    ap.add_argument("--changed-out", help="write changed agent ids here, one per line")
    args = ap.parse_args(argv)

    key_path = Path(args.key) if args.key else None
    if key_path is not None and not key_path.is_absolute():
        key_path = REPO_ROOT / key_path
    packages_dir = Path(args.packages) if args.packages else None

    doc = build_digest_doc(packages_dir, key_path)

    if args.compare:
        baseline_path = Path(args.compare)
        if not baseline_path.is_file():
            print(
                f"[registry-digest] no baseline at {baseline_path} — full build required",
                file=sys.stderr,
            )
            changed, reasons = sorted(doc["agents"]), ["no previous published digest"]
        else:
            changed, reasons = compare(baseline_path, doc)
        if args.changed_out:
            out = Path(args.changed_out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text("".join(f"{a}\n" for a in changed), encoding="utf-8")
        prev_digest = None
        if baseline_path.is_file():
            try:
                prev_digest = json.loads(baseline_path.read_text(encoding="utf-8")).get(
                    "registry_digest"
                )
            except json.JSONDecodeError:
                prev_digest = None

        # The single authoritative answer: identical digest means the published
        # bytes would be identical, so there is nothing to pack, sign or upload.
        publish = prev_digest != doc["registry_digest"]
        print(f"[registry-digest] digest: {doc['registry_digest']}")
        print(f"[registry-digest] baseline: {prev_digest or '(none)'}")
        for reason in reasons:
            print(f"[registry-digest] why: {reason}")
        print(
            f"[registry-digest] {len(changed)} of {doc['agent_count']} agent(s) need packing; "
            f"publish {'REQUIRED' if publish else 'NOT NEEDED'}"
        )
        # Exit code 2 = "nothing to publish", so a workflow can branch on it
        # without parsing prose. 0 = publish required.
        return 0 if publish else 2

    if args.out:
        out = Path(args.out)
        if not out.is_absolute():
            out = REPO_ROOT / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"[registry-digest] wrote {out.relative_to(REPO_ROOT)}")

    print(
        f"[registry-digest] {doc['agent_count']} agent(s), "
        f"builder {doc['builder_digest'][:12]}, digest {doc['registry_digest'][:12]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
