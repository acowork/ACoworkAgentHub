#!/usr/bin/env python3
"""Build index.json for the community agent registry.

Field names and derivation rules deliberately match the main repo's
dev/common/sync-agent-hub.py, so the website pipeline can consume both
without special-casing:

    agent_id, version, name, display_name, role, description, author,
    category, category_label, runtime_version, avatar, permissions,
    high_risk_permissions, capabilities, skills, package.{file,size_bytes,sha256}

Community additions: tier, tags, publisher, source, signature.

The `signature` block reports two independent facts:
  - status: "official-channel" | "third-party" | "unsigned"
  - catalog_digest_covered: whether category/tier/tags are inside the digest list
    (true for every signed package - they live in manifest.toml)

See docs/trust-model.md for what the signature does and does not mean.

The index is generated output: it is written to --out and never committed.

Usage:
    python tools/build-index.py --out build/index.json
    python tools/build-index.py --out build/index.json --packages build/

Exit codes:
    0  ok
    1  data error (missing manifest, unknown category, missing package)
    2  environment error
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import tomllib
except ModuleNotFoundError:
    sys.exit("[build-index] FATAL: needs Python 3.11+ (tomllib). Exit 2")

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENTS_DIR = REPO_ROOT / "agents"
CATALOG_PATH = REPO_ROOT / "schema" / "catalog.json"

SCHEMA_VERSION = 2

#: Permissions that can run commands or write outside a sandbox. Mirrors
#: HIGH_RISK_PERMISSIONS in the main repo's sync-agent-hub.py.
HIGH_RISK_PERMISSIONS = {"Shell", "FilesystemWrite", "Sandbox"}


def fail(msg: str) -> None:
    print(f"[build-index] ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def git_ref(repo: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
        )
        return out.stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def read_skills(agent_dir: Path) -> list[str]:
    skills_dir = agent_dir / "skills"
    if not skills_dir.is_dir():
        return []
    return sorted(d.name for d in skills_dir.iterdir() if d.is_dir() and (d / "SKILL.md").is_file())


def discover_agent_dirs() -> list[Path]:
    """agents/<owner>/<name>/ — the _template directory is never indexed."""
    if not AGENTS_DIR.is_dir():
        return []
    found = []
    for owner_dir in sorted(AGENTS_DIR.iterdir()):
        if not owner_dir.is_dir() or owner_dir.name.startswith(("_", ".")):
            continue
        for name_dir in sorted(owner_dir.iterdir()):
            if name_dir.is_dir() and (name_dir / "manifest.toml").is_file():
                found.append(name_dir)
    return found


def build_entry(agent_dir: Path, catalog: dict, packages_dir: Path | None) -> dict:
    manifest_path = agent_dir / "manifest.toml"
    try:
        m = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        fail(f"cannot parse {manifest_path}: {exc}")

    required = [
        "agent_id", "version", "name", "display_name", "role", "description",
        "author", "category", "tier",
    ]
    missing = [k for k in required if not m.get(k)]
    if missing:
        fail(f"{agent_dir}/manifest.toml missing required field(s): {', '.join(missing)}")

    category = m["category"]
    if category not in catalog["categories"]:
        fail(
            f"{agent_dir}: category {category!r} is not in the taxonomy. "
            "Add it to schema/catalog.json — the index must not guess."
        )
    category_label = catalog["categories"][category]["label"]

    tier = m["tier"]
    if tier not in catalog["tiers"]:
        fail(f"{agent_dir}: tier {tier!r} is not in the taxonomy")

    owner = agent_dir.parent.name
    name = agent_dir.name

    avatar_out = None
    declared_avatar = m.get("avatar")
    if declared_avatar:
        candidate = agent_dir / declared_avatar
        if candidate.is_file():
            avatar_out = f"avatars/{m['agent_id']}{candidate.suffix.lower()}"
        else:
            print(
                f"[build-index] WARN  {owner}/{name}: avatar {declared_avatar!r} missing — "
                "falling back to initials tile",
                file=sys.stderr,
            )

    permissions = sorted({p.get("type") for p in m.get("permissions", []) if p.get("type")})
    if not permissions:
        print(
            f"[build-index] WARN  {owner}/{name}: manifest declares no permissions",
            file=sys.stderr,
        )

    capabilities = [
        {"key": k, "description": (v or {}).get("description", "")}
        for k, v in sorted((m.get("capabilities") or {}).items())
    ]

    publisher = m.get("publisher") or {}

    entry = {
        "agent_id": m["agent_id"],
        "version": m["version"],
        "name": m["name"],
        "display_name": m["display_name"],
        "role": m["role"],
        "description": m["description"],
        "author": m["author"],
        "category": category,
        "category_label": category_label,
        "tier": tier,
        "tags": sorted(m.get("tags", [])),
        "runtime_version": m.get("runtime_version", "unknown"),
        "avatar": avatar_out,
        "permissions": permissions,
        "high_risk_permissions": sorted(set(permissions) & HIGH_RISK_PERMISSIONS),
        "capabilities": capabilities,
        "skills": read_skills(agent_dir),
        "publisher": {
            "id": publisher.get("id", "unknown"),
            "display_name": publisher.get("display_name", m["author"]),
            "affiliation": publisher.get("affiliation", "unknown"),
            **({"contact": publisher["contact"]} if publisher.get("contact") else {}),
            **({"homepage": publisher["homepage"]} if publisher.get("homepage") else {}),
        },
        "source": {
            "owner": owner,
            "path": f"agents/{owner}/{name}",
        },
    }

    pkg_path = None
    if packages_dir is not None:
        pkg_path = packages_dir / f"{m['agent_id']}.agent"
    if pkg_path is None or not pkg_path.is_file():
        fail(
            f"{owner}/{name}: signed package {m['agent_id']}.agent not found in {packages_dir}. "
            f"Build it first: python tools/pack.py agents/{owner}/{name} --out {packages_dir}"
        )

    payload = pkg_path.read_bytes()
    if not payload:
        fail(f"{pkg_path.name} is empty")

    entry["package"] = {
        "file": f"downloads/{m['agent_id']}.agent",
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    entry["signature"] = describe_signature(pkg_path)
    return entry


def describe_signature(pkg_path: Path) -> dict:
    """Summarize the package signature for the index.

    Two independent questions, and the answer to each is reported separately:

    * was the package altered after signing? -> the signature must verify
      against the digests recomputed from the package bytes.
    * did it come through the official channel? -> the signer's fingerprint
      must appear in registry/trusted-keys.json.

    Capability metadata (category / tier / tags / skills) is covered by the
    digest list for every signed package, because it lives in manifest.toml
    inside the package. So `catalog_digest_covered` is true whenever the package is
    signed. See docs/trust-model.md.
    """
    import signing

    data = pkg_path.read_bytes()
    block_content = signing.find_signing_block(data)
    if block_content is None:
        return {"status": "unsigned", "catalog_digest_covered": False}

    block = signing.decode_signing_block(block_content)
    signer = block["signers"][0]
    fingerprint = signing.public_key_fingerprint(signer["certificates"][0]["data"])

    trust = signing.resolve_trust(REPO_ROOT, fingerprint)
    return {
        "status": "official-channel" if trust["trusted"] else "third-party",
        "key_fingerprint": fingerprint,
        "signing_time": signer["signed_attrs"].get("signing_time"),
        "catalog_digest_covered": True,
    }


def build_facets(entries: list[dict], catalog: dict) -> dict:
    """Facet counts for the catalog UI: category, tier, tag."""
    category_counts: dict[str, int] = {}
    tier_counts: dict[str, int] = {}
    tag_counts: dict[str, int] = {}
    for entry in entries:
        category_counts[entry["category"]] = category_counts.get(entry["category"], 0) + 1
        tier_counts[entry["tier"]] = tier_counts.get(entry["tier"], 0) + 1
        for tag in entry["tags"]:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

    return {
        "category": [
            {
                "key": key,
                "label": catalog["categories"][key]["label"],
                "count": category_counts.get(key, 0),
            }
            for key in sorted(catalog["categories"])
        ],
        "tier": [
            {"key": key, "label": catalog["tiers"][key]["label"], "count": tier_counts.get(key, 0)}
            for key in sorted(catalog["tiers"])
        ],
        "tag": [
            {"key": key, "count": tag_counts.get(key, 0)}
            for key in sorted(catalog["tags"])
        ],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="build/index.json", help="output path for index.json")
    ap.add_argument("--packages", default=None,
                    help="directory holding the built .agent packages (default: <out dir>/..)")
    ap.add_argument("--repo", default=None, help="repository URL recorded in the index")
    args = ap.parse_args()

    out_path = Path(args.out).resolve()
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))

    if args.packages:
        packages_dir = Path(args.packages).resolve()
    else:
        packages_dir = out_path.parent

    agent_dirs = discover_agent_dirs()
    if not agent_dirs:
        fail(f"no agent manifests under {AGENTS_DIR}")

    entries = []
    seen: dict[str, Path] = {}
    for agent_dir in agent_dirs:
        entry = build_entry(agent_dir, catalog, packages_dir)
        if entry["agent_id"] in seen:
            fail(f"duplicate agent_id {entry['agent_id']!r} in {seen[entry['agent_id']]} and {agent_dir}")
        seen[entry["agent_id"]] = agent_dir
        entries.append(entry)

    entries.sort(key=lambda e: (e["category"], e["display_name"].lower()))

    doc = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": {
            "repository": args.repo or "https://github.com/acowork/ACoworkAgents",
            "commit": git_ref(REPO_ROOT),
        },
        "counts": {
            "agents": len(entries),
            "categories": len({e["category"] for e in entries}),
            "tags": len({t for e in entries for t in e["tags"]}),
        },
        "facets": build_facets(entries, catalog),
        "agents": entries,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    total = sum(e["package"]["size_bytes"] for e in entries)
    print(
        f"[build-index] wrote {out_path} | {len(entries)} agents | "
        f"{total / 1024:.0f} KiB of packages"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
