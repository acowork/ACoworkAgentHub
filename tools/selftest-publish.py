#!/usr/bin/env python3
"""Self-test the publish pipeline's incremental behaviour, hermetically.

`selftest.py` proves the *verifier* rejects a tampered package. This proves the
publish pipeline's two rules, which are pure Python and need no Rust:

  1. nothing is republished when no content changed, and the gate can say *why*
     it fired (data / builder / signer);
  2. reuse never trusts bookkeeping — not the changed list, and not a digest
     file that claims a package is current.

Everything runs against a copy of the repo in a temp directory, because
`REPO_ROOT` is derived from the script's own location: mutating the real tree to
test "an agent changed" would leave the working copy dirty if a case failed
halfway, and a test that can corrupt the repo is not a test you can run in CI.

Usage:
  python tools/selftest-publish.py [--keep] [--verbose]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

SKIP_DIRS = {".git", "dist", ".ci", ".keys", "node_modules", "__pycache__"}
SKIP_FILES = {"registry.digest.json"}

failures: list[str] = []


def check(cond: bool, label: str, detail: str = "") -> bool:
    if cond:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}" + (f"\n        {detail}" if detail else ""))
        failures.append(label)
    return cond


def run_tool(root: Path, tool: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(root / "tools" / tool), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(root),
    )


def make_fixture(dest: Path) -> Path:
    def ignore(dir_path: str, names: list[str]) -> set[str]:
        skip = {n for n in names if n in SKIP_DIRS or n in SKIP_FILES}
        skip |= {n for n in names if n.endswith(".agent")}
        return skip

    shutil.copytree(REPO_ROOT, dest, ignore=ignore)
    return dest


def _load_tool(name: str):
    """Import a module from tools/, whose files are hyphenated and unimportable."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(name.replace("-", "_"),
                                                  REPO_ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def packable_agent_dirs(root: Path) -> list[Path]:
    """The agent directories `ci-pack-all.py` would actually pack, in the fixture.

    Its two-level rule (`agents/<owner>/<name>/`) is what excludes the submission
    template. Re-implementing it here would mean the test measures a directory
    set the pipeline does not use, and every count below would be off by the
    number of templates in the tree.
    """
    pack_all = _load_tool("ci-pack-all")
    return [root / d.relative_to(REPO_ROOT) for d in pack_all.agent_dirs()]


def first_editable_file(agent_dir: Path) -> Path:
    """A file that is provably part of the package digest.

    Picking any file at random would not test anything: hub-only metadata is
    not packed, so editing it changes nothing the digest covers and a case
    would "pass" by failing to trigger. The digest list itself decides.
    """
    pack = _load_tool("pack")
    listed = {d["section_name"] for d in pack.compute_digests(pack.collect_files(agent_dir))}
    for cand in sorted(agent_dir.rglob("*")):
        if cand.is_file():
            rel = cand.relative_to(agent_dir).as_posix()
            if rel in listed or str(cand) in listed:
                return cand
    raise SystemExit(f"no digest-covered file found under {agent_dir}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--keep", action="store_true", help="keep the temp fixture for inspection")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    work = Path(tempfile.mkdtemp(prefix="hubselftest"))
    root = make_fixture(work / "repo")
    keys = root / ".keys"
    (keys / "a").mkdir(parents=True, exist_ok=True)
    (keys / "b").mkdir(parents=True, exist_ok=True)

    for name in ("a", "b"):
        r = run_tool(root, "make-ci-key.py", "--output-dir", str(keys / name), "--ephemeral")
        if r.returncode != 0:
            print(f"FATAL: could not make a key:\n{r.stdout}\n{r.stderr}", file=sys.stderr)
            return 2

    key_a = keys / "a" / "registry.key"
    key_b = keys / "b" / "registry.key"
    dist1 = root / ".selftest" / "dist1"
    empty = root / ".selftest" / "empty.txt"
    empty.parent.mkdir(parents=True, exist_ok=True)
    empty.write_text("", encoding="utf-8")

    agents = packable_agent_dirs(root)
    total = len(agents)
    import tomllib

    all_ids = sorted(
        tomllib.loads((a / "manifest.toml").read_bytes().decode("utf-8"))["agent_id"] for a in agents
    )
    print(f"[selftest-publish] fixture: {total} agent(s) at {root}\n")

    # ---- 1. a cold build packs everything -------------------------------
    print("1) cold build")
    r = run_tool(root, "ci-pack-all.py", "--out", str(dist1), "--key", str(key_a))
    out = r.stdout + r.stderr
    check(r.returncode == 0, "cold build succeeds", out[-400:])
    check(f"{total} packed" in out, f"cold build packs all {total}", out[-300:])
    check("0 reused" in out, "cold build reuses nothing (no --reuse given)", out[-300:])

    # ---- 2. same key, nothing changed -> full reuse ---------------------
    print("\n2) unchanged rebuild")
    dist2 = root / ".selftest" / "dist2"
    r = run_tool(root, "ci-pack-all.py", "--out", str(dist2), "--key", str(key_a),
                 "--reuse", str(dist1), "--changed", str(empty))
    out = r.stdout + r.stderr
    check(r.returncode == 0, "reuse build succeeds", out[-400:])
    check(f"0 packed, {total} reused" in out, f"nothing repacked when nothing changed (0/{total})",
          out[-300:])
    same = all(
        (dist1 / p.name).read_bytes() == (dist2 / p.name).read_bytes()
        for p in dist1.glob("*.agent")
    )
    check(same, "reused bytes are identical to the published ones")

    # ---- 3. key rotation -> reuse refused -------------------------------
    print("\n3) signing key rotation")
    dist3 = root / ".selftest" / "dist3"
    r = run_tool(root, "ci-pack-all.py", "--out", str(dist3), "--key", str(key_b),
                 "--reuse", str(dist1), "--changed", str(empty))
    out = r.stdout + r.stderr
    check(r.returncode == 0, "rotation build succeeds", out[-400:])
    check(f"{total} packed, 0 reused" in out,
          f"every package is re-signed under a new key ({total}/{total})", out[-300:])
    check("re-signed for a new key" in out, "the refusal is reported, not silent", out[-300:])

    # ---- 4. the changed list is honoured --------------------------------
    print("\n4) one agent marked changed")
    one = agents[0]
    listed = root / ".selftest" / "one.txt"
    manifest = tomllib.loads((one / "manifest.toml").read_bytes().decode("utf-8"))
    listed.write_text(manifest["agent_id"] + "\n", encoding="utf-8")
    dist4 = root / ".selftest" / "dist4"
    r = run_tool(root, "ci-pack-all.py", "--out", str(dist4), "--key", str(key_a),
                 "--reuse", str(dist1), "--changed", str(listed))
    out = r.stdout + r.stderr
    check(r.returncode == 0, "incremental build succeeds", out[-400:])
    check(f"1 packed, {total - 1} reused" in out, "exactly one package is rebuilt", out[-300:])

    # ---- 5. the changed list LIES -------------------------------------
    print("\n5) changed list contradicts the source tree")
    target_file = first_editable_file(one)
    original = target_file.read_bytes()
    target_file.write_bytes(original + b"\n")
    dist5 = root / ".selftest" / "dist5"
    r = run_tool(root, "ci-pack-all.py", "--out", str(dist5), "--key", str(key_a),
                 "--reuse", str(dist1), "--changed", str(empty))
    out = r.stdout + r.stderr
    check(r.returncode == 0, "build with a lying list still succeeds", out[-400:])
    check(f"1 packed, {total - 1} reused" in out,
          "content check repacks the modified agent despite the empty list", out[-300:])
    target_file.write_bytes(original)

    # ---- 6. the gate: unchanged, changed, reverted ----------------------
    print("\n6) content gate")
    dg0 = root / ".selftest" / "digest0.json"
    r = run_tool(root, "registry-digest.py", "--out", str(dg0), "--packages", str(dist1),
                 "--key", str(key_a))
    check(r.returncode == 0, "baseline digest written", (r.stdout + r.stderr)[-300:])

    r = run_tool(root, "registry-digest.py", "--compare", str(dg0), "--packages", str(dist1),
                 "--key", str(key_a))
    check(r.returncode == 2, "unchanged tree -> exit 2 (no publish)",
          (r.stdout + r.stderr)[-300:])
    check("NOT NEEDED" in r.stdout + r.stderr, "the gate says so in words",
          (r.stdout + r.stderr)[-300:])

    changed_out = root / ".selftest" / "gate_changed.txt"
    target_file.write_bytes(original + b"\n")
    r = run_tool(root, "registry-digest.py", "--compare", str(dg0), "--packages", str(dist1),
                 "--key", str(key_a), "--changed-out", str(changed_out))
    body = r.stdout + r.stderr
    check(r.returncode == 0, "changed agent -> exit 0 (publish required)", body[-300:])
    names = changed_out.read_text(encoding="utf-8").split() if changed_out.exists() else []
    check(names == [manifest["agent_id"]],
          f"the gate names exactly the changed agent, got {names}", body[-300:])
    target_file.write_bytes(original)

    r = run_tool(root, "registry-digest.py", "--compare", str(dg0), "--packages", str(dist1),
                 "--key", str(key_a))
    check(r.returncode == 2, "reverting the change -> no publish again",
          (r.stdout + r.stderr)[-300:])

    # ---- 7. builder-only change ----------------------------------------
    print("\n7) builder changed, data untouched")
    builder = root / "tools" / "pack.py"
    pack_original = builder.read_bytes()
    builder.write_bytes(pack_original + b"\n# selftest: builder touched\n")
    changed_out.unlink(missing_ok=True)
    r = run_tool(root, "registry-digest.py", "--compare", str(dg0), "--packages", str(dist1),
                 "--key", str(key_a), "--changed-out", str(changed_out))
    body = r.stdout + r.stderr
    check(r.returncode == 0, "builder change alone triggers a publish", body[-300:])
    check("builder changed" in body, "and it is attributed to the builder", body[-300:])
    names = changed_out.read_text(encoding="utf-8").split() if changed_out.exists() else []
    check(sorted(names) == sorted(all_ids),
          f"a builder change rebuilds every package, got {len(names)}/{total}", body[-300:])
    builder.write_bytes(pack_original)

    # ---- 7b. signer rotation is an envelope change too -----------------
    print("\n7b) signer rotated")
    changed_out.unlink(missing_ok=True)
    r = run_tool(root, "registry-digest.py", "--compare", str(dg0), "--packages", str(dist1),
                 "--key", str(key_b), "--changed-out", str(changed_out))
    body = r.stdout + r.stderr
    check(r.returncode == 0, "a rotated key triggers a publish", body[-300:])
    names = changed_out.read_text(encoding="utf-8").split() if changed_out.exists() else []
    check(sorted(names) == sorted(all_ids),
          f"a rotated key re-signs every package, got {len(names)}/{total}", body[-300:])

    # ---- 8. line endings are not content -------------------------------
    print("\n8) platform line endings")
    builder.write_bytes(pack_original.replace(b"\r\n", b"\n"))
    r = run_tool(root, "registry-digest.py", "--compare", str(dg0), "--packages", str(dist1),
                 "--key", str(key_a))
    check(r.returncode == 2,
          "CRLF vs LF in builder code does not force a republish",
          (r.stdout + r.stderr)[-300:])
    builder.write_bytes(pack_original)

    if not args.keep:
        shutil.rmtree(work, ignore_errors=True)
    else:
        print(f"\n[selftest-publish] fixture kept at {root}")

    if failures:
        print(f"[selftest-publish] {len(failures)} FAILURE(S): " + "; ".join(failures),
              file=sys.stderr)
        return 1
    print("[selftest-publish] OK: the publish pipeline is incremental, honest about "
          "bookkeeping, and refuses to reuse a package across a key rotation")
    return 0


if __name__ == "__main__":
    sys.exit(main())
