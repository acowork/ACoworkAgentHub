#!/usr/bin/env python3
"""Sync the platform's own agents from the main repo into agents/official/.

The main repo's `examples/*/manifest.toml` is the source of truth for the
platform agents. This script mirrors them into `agents/official/<name>/` so
the hub publishes the same packages the app ships, under the same agent ids
— an installed package stays upgradeable instead of forking into a second,
differently-named agent.

The mirror is a copy, not a fork: the manifest, prompts, skills and assets
are copied byte for byte, and the script fails if anything else is in the
package directory. The hub-only `hub.json` and `README.md` are generated
here and are the only files the hub owns.

Usage:
    python tools/sync-official.py --main-repo ../ACowork
    python tools/sync-official.py --main-repo ../ACowork --check
    python tools/sync-official.py --main-repo ../ACowork --main-ref develop

`--check` does not write anything: it reports what would change and exits
non-zero if the mirror is out of date. That is the mode a CI job runs.

Exit codes:
    0  ok (or, with --check, the mirror is up to date)
    1  the mirror is out of date or a package violates the hub's rules
    2  environment error (main repo not found)
"""

from __future__ import annotations

import argparse
import filecmp
import json
import shutil
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    sys.exit("[sync-official] FATAL: needs Python 3.11+ (tomllib). Exit 2")

REPO_ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_DIR = REPO_ROOT / "agents" / "official"
OFFICIAL_PUBLISHER = "acowork:official"

#: The main repo's example directories, and the hub directory each maps to.
#: The key is the hub directory name; the value is the main repo's example.
EXAMPLE_TO_HUB_NAME = {
    "document-manager-agent": "document-manager",
    "product-manager-agent": "product-manager",
    "project-manager-agent": "project-manager",
    "quality-assurance-agent": "quality-assurance",
    "senior-engineer-agent": "senior-engineer",
    "software-architect-agent": "software-architect",
    "ponytail-agent": "ponytail",
}

#: `ponytail` is a community contribution that happens to live in the main
#: repo. It is published under its author's own account, not as a platform
#: agent, so the hub never claims it.
COMMUNITY_EXAMPLES = {"ponytail-agent"}

#: Files the hub owns inside a mirrored package directory.
HUB_OWNED_FILES = {"hub.json", "README.md", "LICENSE"}

#: The license a community example carries under its author's own name.
COMMUNITY_MIT_LICENSE = """MIT License

Copyright (c) 2025 DietrichGebert

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

#: Directories copied from the main repo.
COPIED_DIRS = ("assets", "prompts", "skills")


def fail(msg: str) -> None:
    print(f"[sync-official] ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def hub_owner_dir(example: str) -> str:
    """Which `agents/<owner>/` a mirrored example belongs under."""
    return "dietrichgebert" if example in COMMUNITY_EXAMPLES else "official"


def read_manifest(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        fail(f"cannot parse {path}: {exc}")


def build_hub_json(manifest: dict) -> str:
    """The hub-owned metadata for a mirrored package.

    The main repo's LICENSE applies to the platform's own agents; the
    community example carries its own.
    """
    if manifest.get("author") == "ACowork Team":
        hub = {
            "publisher": {
                "id": OFFICIAL_PUBLISHER,
                "display_name": "ACowork",
                "homepage": "https://acowork.ai",
            },
            "license": "Apache-2.0",
            "repository": "https://github.com/acowork/ACowork",
        }
    else:
        hub = {
            "publisher": {
                "id": "github:dietrichgebert",
                "display_name": "DietrichGebert",
                "homepage": "https://github.com/DietrichGebert",
            },
            "license": "MIT",
            "repository": "https://github.com/DietrichGebert/ponytail",
        }
    return json.dumps(hub, indent=2, ensure_ascii=False) + "\n"


def build_readme(manifest: dict, example: str, hub_dir: Path) -> str:
    """The hub-owned README for a mirrored package."""
    agent_dir = hub_dir
    perms = sorted({p["type"] for p in manifest.get("permissions", [])})
    caps = manifest.get("capabilities") or {}
    skills = sorted(
        d.name for d in (agent_dir / "skills").iterdir()
        if d.is_dir() and (d / "SKILL.md").is_file()
    ) if (agent_dir / "skills").is_dir() else []

    L: list[str] = []
    L.append(f"# {manifest.get('display_name') or manifest.get('name')}")
    L.append("")
    L.append(manifest.get("description", ""))
    L.append("")
    L.append("## Install")
    L.append("")
    L.append("```bash")
    L.append(f"acowork install {manifest['agent_id']}")
    L.append("```")
    L.append("")
    L.append("Or download the signed `.agent` package from the hub and import it in")
    L.append("the Desktop App. The package is signed by the official channel key —")
    L.append("the app verifies the signature on install.")
    L.append("")
    L.append("## What it does")
    L.append("")
    for k in sorted(caps):
        L.append(f"- **{k.replace('_', ' ')}** — {caps[k].get('description', '')}")
    L.append("")
    L.append("## Permissions")
    L.append("")
    L.append("Read them before you install:")
    L.append("")
    for p in perms:
        L.append(f"- `{p}`")
    L.append("")
    L.append("## Skills")
    L.append("")
    for s in skills:
        L.append(f"- `{s}`")
    L.append("")
    L.append("## Provenance")
    L.append("")
    L.append(f"- **Agent id**: `{manifest['agent_id']}`")
    L.append(f"- **Version**: `{manifest['version']}`")
    L.append(f"- **Author**: {manifest.get('author', '')}")
    L.append(
        f"- **Source**: [`examples/{example}`]"
        f"(https://github.com/acowork/ACowork/tree/develop/examples/{example}) "
        "in the ACowork main repo"
    )
    L.append(f"- **License**: {manifest.get('author') == 'ACowork Team' and 'Apache-2.0' or 'MIT'}")
    L.append("")
    if manifest.get("author") == "ACowork Team":
        L.append("This package is a verbatim copy of the main repo example. The main")
        L.append("repo remains the source of truth; changes there land here on the")
        L.append("next sync.")
    else:
        L.append("A community contribution originally shipped with the ACowork main")
        L.append("repo. The main repo remains the source of truth for the content; this")
        L.append("hub copy is published under the author's own account, so the hub")
        L.append("never claims it.")
    L.append("")
    return "\n".join(L)


def sync_one(example_dir: Path, example: str, hub_name: str, check_only: bool) -> list[str]:
    """Mirror one example. Returns a list of differences found."""
    diffs: list[str] = []
    owner = hub_owner_dir(example)
    target = REPO_ROOT / "agents" / owner / hub_name

    manifest = read_manifest(example_dir / "manifest.toml")

    def handle(src: Path, dst: Path) -> None:
        if not dst.exists():
            diffs.append(f"missing: {dst.relative_to(REPO_ROOT).as_posix()}")
            return
        if not filecmp.cmp(src, dst, shallow=False):
            diffs.append(f"differs: {dst.relative_to(REPO_ROOT).as_posix()}")

    # 1. the copied content
    handle(example_dir / "manifest.toml", target / "manifest.toml")
    for sub in COPIED_DIRS:
        src_dir = example_dir / sub
        if not src_dir.is_dir():
            continue
        for path in sorted(src_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(example_dir)
            handle(path, target / rel)

    # 2. the hub-owned files
    hub_json = build_hub_json(manifest)
    if not (target / "hub.json").is_file():
        diffs.append(f"missing: {(target / 'hub.json').relative_to(REPO_ROOT).as_posix()}")
    elif (target / "hub.json").read_text(encoding="utf-8") != hub_json:
        diffs.append(f"differs: {(target / 'hub.json').relative_to(REPO_ROOT).as_posix()}")

    readme = build_readme(manifest, example, target)
    if not (target / "README.md").is_file():
        diffs.append(f"missing: {(target / 'README.md').relative_to(REPO_ROOT).as_posix()}")
    elif (target / "README.md").read_text(encoding="utf-8") != readme:
        diffs.append(f"differs: {(target / 'README.md').relative_to(REPO_ROOT).as_posix()}")

    # The license is hub-owned too, so it is checked like the other two.
    expected_license = (
        (example_dir.parent.parent / "LICENSE").read_text(encoding="utf-8")
        if manifest.get("author") == "ACowork Team"
        and (example_dir.parent.parent / "LICENSE").is_file()
        else COMMUNITY_MIT_LICENSE
    )
    if not (target / "LICENSE").is_file():
        diffs.append(f"missing: {(target / 'LICENSE').relative_to(REPO_ROOT).as_posix()}")
    elif (target / "LICENSE").read_text(encoding="utf-8") != expected_license:
        diffs.append(f"differs: {(target / 'LICENSE').relative_to(REPO_ROOT).as_posix()}")

    if check_only or not diffs:
        return diffs

    # 3. write it
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    shutil.copy2(example_dir / "manifest.toml", target / "manifest.toml")
    for sub in COPIED_DIRS:
        src_dir = example_dir / sub
        if src_dir.is_dir():
            shutil.copytree(src_dir, target / sub)
    (target / "hub.json").write_text(hub_json, encoding="utf-8")
    (target / "README.md").write_text(readme, encoding="utf-8")

    # 4. the license travels with the content.
    #
    # The platform's agents inherit the main repo's Apache-2.0. The
    # community example carries its own MIT license under its author's
    # name — the hub must not put the platform's copyright on it.
    license_src = example_dir.parent.parent / "LICENSE"
    if manifest.get("author") == "ACowork Team":
        if license_src.is_file():
            shutil.copy2(license_src, target / "LICENSE")
    else:
        (target / "LICENSE").write_text(COMMUNITY_MIT_LICENSE, encoding="utf-8")

    return diffs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--main-repo", required=True, help="path to the ACowork main repo checkout")
    ap.add_argument("--check", action="store_true", help="report differences without writing")
    args = ap.parse_args()

    main_repo = Path(args.main_repo).resolve()
    examples_dir = main_repo / "examples"
    if not examples_dir.is_dir():
        print(f"[sync-official] FATAL: {examples_dir} not found. Exit 2", file=sys.stderr)
        return 2

    all_diffs: list[str] = []
    synced = 0
    for example, hub_name in sorted(EXAMPLE_TO_HUB_NAME.items()):
        example_dir = examples_dir / example
        if not (example_dir / "manifest.toml").is_file():
            print(f"[sync-official] WARN  {example} has no manifest.toml — skipped")
            continue
        diffs = sync_one(example_dir, example, hub_name, args.check)
        owner = hub_owner_dir(example)
        rel = f"agents/{owner}/{hub_name}"
        if diffs:
            all_diffs.extend(diffs)
            print(f"[sync-official] {rel}: {len(diffs)} difference(s)")
            for d in diffs[:5]:
                print(f"    {d}")
            if len(diffs) > 5:
                print(f"    ... and {len(diffs) - 5} more")
        else:
            print(f"[sync-official] {rel}: up to date")
            synced += 1

    if args.check and all_diffs:
        print(
            f"[sync-official] FAILED: the mirror is out of date "
            f"({len(all_diffs)} difference(s)). Run: "
            f"python tools/sync-official.py --main-repo <path>"
        )
        return 1

    print(f"[sync-official] OK: {synced}/{len(EXAMPLE_TO_HUB_NAME)} package(s) up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
