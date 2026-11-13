#!/usr/bin/env python3
"""Mirror the platform's own agents from the main repo into this repository.

The main repo's `agents/<name>-agent/` is the single source of truth for the
platform agents. This script projects those directories into
`agents/official/<name>/` (and the one community package into
`agents/dietrichgebert/<name>/`) byte for byte, so the hub publishes exactly
what the app ships, under the same `agent_id` — an installed package stays
upgradeable instead of forking into a second, differently-named agent.

THE MIRROR IS GENERATED. Never hand-edit `agents/official/**`: the next sync
overwrites you, and `--check` turns your edit red before then. A content
change belongs in the main repo; a hub-only change belongs in the files this
script owns (`hub.json`, `README.md`, `LICENSE` are all generated here too).

Every mirrored package records what it claims to be a copy of, in
`hub.json`:

    "mirror": {
      "source_repo":    "acowork/ACowork",
      "source_path":    "agents/product-manager-agent",
      "source_commit":  "<full sha the bytes were copied from>"
    }

That claim is what makes the two checks below different questions instead of
one noisy one:

  * `--check`                  — does the mirror match the commit it claims?
                                 (a hand-edit alarm; blocks publishing)
  * `--check --main-ref master` — is master ahead of what we publish?
                                 (a sync-me alarm; report-only)

Content is always read from a committed ref through `git archive`, never from
a working tree: an uncommitted edit in someone else's checkout cannot leak
into a published package.

Usage:
    python tools/sync-official.py --main-repo ../ACowork
    python tools/sync-official.py --main-repo ../ACowork --check
    python tools/sync-official.py --main-repo ../ACowork --main-ref <sha>
    python tools/sync-official.py --print-claims
    python tools/sync-official.py --main-repo ../ACowork --main-ref master \\
        --check --report-only

Exit codes:
    0  ok (or, with --report-only, differences found but not treated as one)
    1  the mirror does not match its claim, or a package violates hub rules
    2  environment error (main repo missing, not a git checkout, ref absent)
"""

from __future__ import annotations

import argparse
import filecmp
import io
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import NamedTuple

try:
    import tomllib
except ModuleNotFoundError:
    sys.exit("[sync-official] FATAL: needs Python 3.11+ (tomllib). Exit 2")

REPO_ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_PUBLISHER = "acowork:official"

#: Where the agent sources live in the main repo, and what the repo's own
#: license file is called — the two paths this script needs from upstream.
SOURCE_DIRNAME = "agents"
LICENSE_FILENAME = "LICENSE"

#: The main repo's agent directories, and the hub directory each maps to.
#: An explicit whitelist, not a glob: a new directory in the main repo must
#: not become a published package by accident.
MAIN_TO_HUB_NAME = {
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
COMMUNITY_PACKAGES = {"ponytail-agent"}

#: The manifest author string that marks a package as the platform's own.
PLATFORM_AUTHOR = "ACowork Team"

#: The upstream repository, as recorded in every generated hub.json.
SOURCE_REPO = "acowork/ACowork"
SOURCE_REPO_URL = "https://github.com/acowork/ACowork"

#: The license a community package carries under its author's own name.
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

#: Directories copied from the main repo, verbatim.
COPIED_DIRS = ("assets", "prompts", "skills")


class Diff(NamedTuple):
    """One difference between the mirror and upstream, tagged by what it affects.

    `package` — a byte that goes into the published `.agent`. This is the one
    that decides whether a user installs what the main repo ships.
    `owned`   — hub-generated metadata (hub.json, README.md, LICENSE).
    """

    kind: str
    text: str


def fail(msg: str, code: int = 1) -> None:
    print(f"[sync-official] ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def hub_owner_dir(pkg: str) -> str:
    """Which `agents/<owner>/` a mirrored package belongs under."""
    return "dietrichgebert" if pkg in COMMUNITY_PACKAGES else "official"


def read_manifest(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        fail(f"cannot parse {path}: {exc}")


def git(main_repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(main_repo), *args], capture_output=True, text=True
    )


def resolve_ref(main_repo: Path, ref: str) -> str:
    """The full commit sha `ref` points at. Exit 2 when it does not exist."""
    out = git(main_repo, "rev-parse", "--verify", f"{ref}^{{commit}}")
    if out.returncode != 0:
        fail(
            f"{main_repo} has no commit for '{ref}' "
            f"({out.stderr.strip().splitlines()[-1] if out.stderr.strip() else 'unresolvable'}). "
            "In CI, fetch it first: "
            f"git -C {main_repo} fetch --depth 1 origin {ref}",
            code=2,
        )
    return out.stdout.strip()


def materialize(main_repo: Path, sha: str, tmp_root: Path) -> Path:
    """Export the paths this script needs at commit `sha` into `tmp_root`.

    `git archive` gives a clean tree of exactly those paths, so neither the
    main repo's working tree nor its build artifacts can influence a mirror.
    """
    paths = [LICENSE_FILENAME] + [f"{SOURCE_DIRNAME}/{pkg}" for pkg in MAIN_TO_HUB_NAME]
    out = subprocess.run(
        ["git", "-C", str(main_repo), "archive", "--format=tar", sha, *paths],
        capture_output=True,
    )
    if out.returncode != 0:
        fail(
            f"git archive {sha} failed: {out.stderr.decode(errors='replace').strip()}",
            code=2,
        )
    dest = tmp_root / sha
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(out.stdout)) as tar:
        tar.extractall(path=dest, filter="data")
    return dest


def recorded_claim(target: Path) -> str | None:
    """The `mirror.source_commit` a package already claims, if any."""
    hub_path = target / "hub.json"
    if not hub_path.is_file():
        return None
    try:
        hub = json.loads(hub_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return (hub.get("mirror") or {}).get("source_commit")


def build_hub_json(manifest: dict, pkg: str, source_commit: str) -> str:
    """The hub-owned metadata for a mirrored package.

    The platform's own agents carry the main repo's Apache-2.0; the community
    package carries its own. `mirror` is what makes the generated tree
    auditable: it names the exact upstream commit the bytes came from.
    """
    official = manifest.get("author") == PLATFORM_AUTHOR
    if official:
        hub: dict = {
            "publisher": {
                "id": OFFICIAL_PUBLISHER,
                "display_name": "ACowork",
                "homepage": "https://acowork.ai",
            },
            "license": "Apache-2.0",
            "repository": SOURCE_REPO_URL,
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
    hub["mirror"] = {
        "source_repo": SOURCE_REPO,
        "source_path": f"{SOURCE_DIRNAME}/{pkg}",
        "source_commit": source_commit,
    }
    return json.dumps(hub, indent=2, ensure_ascii=False) + "\n"


def build_readme(manifest: dict, pkg: str, source_dir: Path) -> str:
    """The hub-owned README for a mirrored package.

    The skill list is read from the SOURCE tree, not from the mirror: the
    README is generated before the mirror is rewritten, so reading the target
    here would describe the previous sync and leave the generator needing two
    passes to reach a fixed point.
    """
    agent_dir = source_dir
    perms = sorted({p["type"] for p in manifest.get("permissions", [])})
    caps = manifest.get("capabilities") or {}
    skills = sorted(
        d.name for d in (agent_dir / "skills").iterdir()
        if d.is_dir() and (d / "SKILL.md").is_file()
    ) if (agent_dir / "skills").is_dir() else []

    official = manifest.get("author") == PLATFORM_AUTHOR
    L: list[str] = []
    L.append(f"# {manifest.get('display_name') or manifest.get('name')}")
    L.append("")
    L.append(manifest.get("description", ""))
    L.append("")
    L.append("## Install")
    L.append("")
    L.append("```bash")
    L.append(f"acowork install {manifest['agent_id']}")
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
        f"- **Source**: [`{SOURCE_DIRNAME}/{pkg}`]"
        f"({SOURCE_REPO_URL}/tree/master/{SOURCE_DIRNAME}/{pkg}) "
        "in the ACowork main repo"
    )
    L.append(f"- **License**: {'Apache-2.0' if official else 'MIT'}")
    L.append("")
    if official:
        L.append("This package is a verbatim copy of the main repo's agent source,")
        L.append("produced by `tools/sync-official.py`; `hub.json` names the commit")
        L.append("it was copied from. The main repo is the source of truth — edit")
        L.append("it there, then re-run the sync. Changes made directly to this")
        L.append("directory are overwritten by the next sync and fail CI before")
        L.append("that happens.")
    else:
        L.append("A community contribution originally shipped with the ACowork main")
        L.append("repo. The main repo is the source of truth for the content; this")
        L.append("hub copy is published under the author's own account, so the hub")
        L.append("never claims it.")
    L.append("")
    return "\n".join(L)


def sync_one(
    src_root: Path, pkg: str, hub_name: str, check_only: bool, source_commit: str
) -> list[Diff]:
    """Mirror one package. Returns the differences found, tagged by kind.

    The tag is not decoration. `package` diffs are bytes that end up inside
    the published `.agent`, so they are the only thing that can make a user
    install something the main repo has already replaced. `owned` diffs are
    hub-generated metadata — and `hub.json` necessarily differs whenever you
    compare against a different ref, because it records the ref. Counting
    those as drift would make the "upstream has moved" signal fire on every
    run and teach everyone to ignore it.
    """
    diffs: list[Diff] = []
    pkg_dir = src_root / SOURCE_DIRNAME / pkg
    owner = hub_owner_dir(pkg)
    target = REPO_ROOT / "agents" / owner / hub_name

    manifest = read_manifest(pkg_dir / "manifest.toml")
    official = manifest.get("author") == PLATFORM_AUTHOR

    def handle(src: Path, dst: Path) -> None:
        rel = dst.relative_to(REPO_ROOT).as_posix()
        if not dst.exists():
            diffs.append(Diff("package", f"missing: {rel}"))
            return
        if not filecmp.cmp(src, dst, shallow=False):
            diffs.append(Diff("package", f"differs: {rel}"))

    def handle_owned(name: str, expected: str) -> None:
        rel = (target / name).relative_to(REPO_ROOT).as_posix()
        path = target / name
        if not path.is_file():
            diffs.append(Diff("owned", f"missing: {rel}"))
        elif path.read_text(encoding="utf-8") != expected:
            diffs.append(Diff("owned", f"differs: {rel}"))

    # 1. the copied content
    handle(pkg_dir / "manifest.toml", target / "manifest.toml")
    for sub in COPIED_DIRS:
        src_dir = pkg_dir / sub
        if not src_dir.is_dir():
            continue
        for path in sorted(src_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(pkg_dir)
            handle(path, target / rel)

    # 2. the hub-owned files, and the claim they carry
    hub_json = build_hub_json(manifest, pkg, source_commit)
    readme = build_readme(manifest, pkg, pkg_dir)
    handle_owned("hub.json", hub_json)
    handle_owned("README.md", readme)

    # The license is hub-owned too, so it is checked like the other two.
    #
    # The platform's agents inherit the main repo's Apache-2.0. The community
    # package carries its own MIT license under its author's name — the hub
    # must not put the platform's copyright on it.
    license_src = src_root / LICENSE_FILENAME
    handle_owned(
        LICENSE_FILENAME,
        license_src.read_text(encoding="utf-8")
        if official and license_src.is_file()
        else COMMUNITY_MIT_LICENSE,
    )

    if check_only or not diffs:
        return diffs

    # 3. write it — the whole directory, so a deleted skill cannot linger in
    #    the mirror the way it would linger in an append-only copy.
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    shutil.copy2(pkg_dir / "manifest.toml", target / "manifest.toml")
    for sub in COPIED_DIRS:
        src_dir = pkg_dir / sub
        if src_dir.is_dir():
            shutil.copytree(src_dir, target / sub)
    (target / "hub.json").write_text(hub_json, encoding="utf-8")
    (target / "README.md").write_text(readme, encoding="utf-8")

    # 4. the license travels with the content.
    if official:
        if license_src.is_file():
            shutil.copy2(license_src, target / LICENSE_FILENAME)
    else:
        (target / LICENSE_FILENAME).write_text(COMMUNITY_MIT_LICENSE, encoding="utf-8")

    return diffs


def claims() -> list[str]:
    """The distinct commits the hub currently claims to mirror."""
    found: list[str] = []
    for pkg, hub_name in MAIN_TO_HUB_NAME.items():
        claim = recorded_claim(REPO_ROOT / "agents" / hub_owner_dir(pkg) / hub_name)
        if claim and claim not in found:
            found.append(claim)
    return found


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--main-repo", help="path to the ACowork main repo git checkout")
    ap.add_argument(
        "--check", action="store_true", help="report differences without writing"
    )
    ap.add_argument(
        "--main-ref",
        help="upstream ref to mirror (default: the commit recorded in each "
        "package's hub.json, or the main repo's HEAD when there is none)",
    )
    ap.add_argument(
        "--report-only",
        action="store_true",
        help="with --check: print differences as warnings and exit 0 — for the "
        "'master has moved' signal, which must not block a hub PR",
    )
    ap.add_argument(
        "--print-claims",
        action="store_true",
        help="list the commits the hub claims, one per line, and stop",
    )
    args = ap.parse_args()

    if args.print_claims:
        for sha in claims():
            print(sha)
        return 0

    if not args.main_repo:
        fail("--main-repo is required unless --print-claims is used", code=2)
    main_repo = Path(args.main_repo).resolve()
    if git(main_repo, "rev-parse", "--git-dir").returncode != 0:
        fail(f"{main_repo} is not a git checkout", code=2)

    tmp = Path(tempfile.mkdtemp(prefix="sync-official-"))
    all_diffs: list[str] = []
    lagging: list[str] = []
    synced = 0
    try:
        # One ref can be shared by every package (a sync run); per-package
        # claims are the norm once only some agents were re-synced.
        default_ref = args.main_ref
        per_ref_cache: dict[str, Path] = {}

        for pkg, hub_name in sorted(MAIN_TO_HUB_NAME.items()):
            owner = hub_owner_dir(pkg)
            rel = f"agents/{owner}/{hub_name}"
            target = REPO_ROOT / "agents" / owner / hub_name

            ref = default_ref or recorded_claim(target) or "HEAD"
            sha = resolve_ref(main_repo, ref)
            if sha not in per_ref_cache:
                per_ref_cache[sha] = materialize(main_repo, sha, tmp)
            src_root = per_ref_cache[sha]

            if not (src_root / SOURCE_DIRNAME / pkg / "manifest.toml").is_file():
                fail(
                    f"{pkg} is in MAIN_TO_HUB_NAME but has no manifest.toml at "
                    f"{SOURCE_DIRNAME}/{pkg} in {sha[:12]} — the whitelist and the "
                    "main repo disagree, and guessing here would publish nothing"
                )

            diffs = sync_one(src_root, pkg, hub_name, args.check, sha)
            if diffs:
                all_diffs.extend(f"{rel}: {d.text}" for d in diffs)
                if any(d.kind == "package" for d in diffs):
                    lagging.append(rel)
                print(f"[sync-official] {rel}: {len(diffs)} difference(s) vs {sha[:12]}")
                for d in diffs[:5]:
                    print(f"    [{d.kind}] {d.text}")
                if len(diffs) > 5:
                    print(f"    ... and {len(diffs) - 5} more")
            else:
                print(f"[sync-official] {rel}: up to date with {sha[:12]}")
                synced += 1

        # The two questions, answered differently on purpose.
        if args.check and args.report_only:
            # "Has upstream moved past what we publish?" Only package bytes
            # count here: hub.json records the commit it was made from, so it
            # differs against any other ref by construction, and counting it
            # would fire on every run and teach everyone to ignore the signal.
            if lagging:
                print(
                    f"::warning title=Published bytes lag {args.main_ref}::"
                    f"{len(lagging)} package(s) differ from {args.main_ref} in "
                    "content that ships. Re-sync: python tools/sync-official.py "
                    f"--main-repo <path> --main-ref {args.main_ref}, then open a "
                    "mirror PR."
                )
                print(f"[sync-official] NOTE: {len(lagging)} package(s) behind {args.main_ref}")
            else:
                print(f"[sync-official] OK: published bytes match {args.main_ref}")
            return 0

        if all_diffs and args.check:
            print(
                "[sync-official] FAILED: the mirror does not match the commit it "
                f"claims ({len(all_diffs)} difference(s)). Either the generated area "
                "was hand-edited, or the claim in hub.json is wrong. Fix it by "
                "re-running the sync from the commit you mean: "
                "python tools/sync-official.py --main-repo <path>"
            )
            return 1

        print(f"[sync-official] OK: {synced}/{len(MAIN_TO_HUB_NAME)} package(s) in sync")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
