#!/usr/bin/env python3
"""Check that every path literal in the repo names a real file with the exact case.

Why this exists
---------------
The publish self test once read `.selftest/detA` while creating `.selftest/deta`.
On Windows and macOS the two are the same file, so the test passed on the
developer's machine. On Linux they are different, so CI failed with a
`FileNotFoundError` that looked like a missing package rather than a typo.

That failure mode is invisible to any test that runs on a case-insensitive
filesystem, so it has to be caught by inspection rather than by execution.
This tool is that inspection: it extracts the path-like literals from every
tracked file and checks each one against the real directory listing.

A mismatch is reported as an error, never a warning. A case-insensitive
filesystem is exactly the environment where these bugs are born, so on such a
filesystem this tool is the only line of defence.

Usage:
    python tools/check-paths.py [--quiet]
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Directories that never contain a checked-in source file. A literal naming one
# of them is a runtime path (a temp dir, a CI artifact), not a repo path, so
# there is nothing to compare it against.
RUNTIME_ROOTS = {
    "dist", ".keys", ".ci", "tmp", "temp", "node_modules", "__pycache__",
    "site-packages", "build",
}

# Literals that are obviously not paths.
SKIP_LITERAL = re.compile(r"^(\$|\{|<|/|https?:|mailto:|data:)")

# A quoted token that could be a path. Kept deliberately broad: the tool must
# not need to know which literals are paths, only whether the ones that are
# spelled with the wrong case.
TOKEN = re.compile(r"""["']([^"'\n]{1,200})["']""")

# A path-like token: has a slash, or ends in a known file extension.
PATHISH = re.compile(
    r"(?:^|/)[\w.\-]+(?:/[\w.\-]+)*/?$"
)


def tracked_files() -> list[str]:
    """Every source file in the repo, relative to its root.

    The list comes from `git ls-files` when git is available, because that is
    exactly the set of files that will be committed. When it is not (a copy of
    the tree, an unpacked release), the tree is walked instead - the tool must
    not silently check nothing.
    """
    out = subprocess.run(
        ["git", "ls-files"], cwd=str(REPO_ROOT),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    files = [f for f in out.stdout.splitlines() if f]
    if files:
        return files
    walked: list[str] = []
    for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
        dirnames[:] = [d for d in dirnames
                       if d not in {".git", "dist", ".keys", "__pycache__", "node_modules"}]
        for name in filenames:
            rel = Path(dirpath, name).relative_to(REPO_ROOT)
            walked.append(rel.as_posix())
    return walked


def is_pathish(token: str) -> bool:
    if not token or SKIP_LITERAL.match(token):
        return False
    if " " in token:
        return False
    if not PATHISH.match(token):
        return False
    # Only look at things that look like a repo-relative path with a
    # directory component or a known extension.
    if "/" not in token and "." not in token:
        return False
    if "/" not in token:
        ext = token.rsplit(".", 1)[-1].lower()
        return ext in {
            "py", "yml", "yaml", "json", "toml", "md", "html", "css", "js",
            "txt", "svg", "lock", "ini", "cfg", "png", "woff", "woff2",
        }
    head = token.split("/")[0]
    if head in RUNTIME_ROOTS:
        return False
    return True


def real_case(parent: Path, name: str) -> str | None:
    """The actual spelling of `name` inside `parent`, or None if absent."""
    try:
        entries = os.listdir(parent)
    except (NotADirectoryError, FileNotFoundError):
        return None
    for e in entries:
        if e.lower() == name.lower():
            return e
    return None


def first_case_mismatch(base: Path, rel_path: str) -> str | None:
    """Describe the first component of `rel_path` that is mis-cased, if any.

    Walks the path one component at a time. A component that exists but is
    spelled differently is the bug. A component that does not exist at all is
    a different problem (a path that is simply not in the repo) and is left
    alone - but the walk continues into the nearest existing ancestor, because
    a path like `Schema/agent.schema.json` fails at the very first component
    while `schema/Agent.schema.json` fails at the last one.
    """
    parts = rel_path.split("/")
    cur = base
    for i, part in enumerate(parts):
        if not cur.is_dir():
            return None
        actual = real_case(cur, part)
        if actual is None:
            # This component does not exist in any spelling. Nothing more to
            # say about the case of the ones below it.
            return None
        if actual != part:
            where = cur.relative_to(base)
            return (f"the file is actually called '{actual}'"
                    + (f" (in '{where}')" if str(where) != "." else ""))
        cur = cur / actual
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    problems: list[str] = []
    checked = 0

    for rel in tracked_files():
        p = REPO_ROOT / rel
        if p.suffix.lower() in {".png", ".agent", ".jpg", ".jpeg", ".woff", ".woff2", ".zip"}:
            continue
        try:
            txt = p.read_text(encoding="utf-8")
        except Exception:
            continue
        for lineno, line in enumerate(txt.splitlines(), 1):
            for m in TOKEN.finditer(line):
                tok = m.group(1)
                if not is_pathish(tok):
                    continue
                checked += 1
                bad = first_case_mismatch(REPO_ROOT, tok)
                if bad is not None:
                    problems.append(f"{rel}:{lineno}: '{tok}' -> {bad}")

    if problems:
        print(f"[check-paths] {len(problems)} case mismatch(es):", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        print("[check-paths] A path that differs only in case works on Windows and "
              "macOS and fails on Linux. Fix the spelling.", file=sys.stderr)
        return 1

    if not args.quiet:
        print(f"[check-paths] OK: {checked} path literal(s) across "
              f"{len(tracked_files())} tracked file(s) match the real names exactly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
