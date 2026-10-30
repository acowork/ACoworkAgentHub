#!/usr/bin/env python3
"""Assemble the deployable hub site from the generated registry index.

The site is a static directory: no server, no build step, no npm. This
script copies the pieces the page needs into one tree that Cloudflare
Pages (or any static host) can serve as-is:

    site/
      index.html            the page
      hub.js, styles.css    the page's own assets
      assets/               logo + fonts
      data/index.json       the registry index
      downloads/*.agent     the signed packages
      avatars/*             the agent avatars

It also embeds a snapshot of the index into index.html, so the page still
renders when opened straight off disk (file://), where fetch() is blocked
by the browser. Over HTTP the page fetches data/index.json and the
snapshot is ignored.

Both copies come from the same index.json, so they cannot drift. If the
markers are ever renamed, the embedded copy would silently go stale — so
a missing marker is a hard error, not a warning.

Usage:
    python tools/build-site.py --index dist/index.json --packages dist/ --out dist/site

Exit codes:
    0  ok
    1  data error (missing index, missing package)
    2  environment error
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SITE_DIR = REPO_ROOT / "site"

#: Directories inside site/ that are build output, not source.
GENERATED_DIRS = ("data", "downloads", "avatars")


def fail(msg: str) -> None:
    print(f"[build-site] ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


#: Cards per page. Chosen so one page is ~10 KiB gzipped: the first paint
#: costs one small request no matter how large the registry grows.
PAGE_SIZE = 48

#: The fields a browser needs to draw a card *and to search*. Everything else
#: lives in the per-agent detail file. This split is by field weight against
#: the access pattern, not by category: client-side search needs the whole
#: card set, so category shards would force fetching all of them anyway.
#:
#: Deliberately excluded from the card: capability descriptions, the package
#: sha256, the signer fingerprint, source/repository/homepage paths. Those are
#: read when a specific agent is inspected, not while browsing the registry.
CARD_FIELDS = (
    "agent_id",
    "name",
    "display_name",
    "role",
    "description",
    "version",
    "category",
    "category_label",
    "labels",
    "avatar",
    "author",
    "license",
    "skills",
    "permissions",
    "high_risk_permissions",
    "deprecated",
)


def card_of(entry: dict) -> dict:
    """Project a full index entry down to the browse/search record."""
    card = {key: entry[key] for key in CARD_FIELDS if entry.get(key) not in (None, [], {})}
    publisher = entry.get("publisher") or {}
    if publisher:
        card["publisher"] = {k: publisher[k] for k in ("id", "display_name") if publisher.get(k)}
    # Cards list capability *names* so a search for "risk assessment" hits,
    # but the descriptions - the bulk of the entry - stay in the detail file.
    caps = entry.get("capabilities") or []
    if caps:
        card["capability_keys"] = [c.get("key") for c in caps if c.get("key")]
    package = entry.get("package") or {}
    if package.get("file"):
        card["file"] = package["file"]
    if package.get("size_bytes") is not None:
        card["size_bytes"] = package["size_bytes"]
    card["signature_status"] = (entry.get("signature") or {}).get("status", "unsigned")
    return card


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + "\n", encoding="utf-8"
    )


def write_data_layer(doc: dict, entries: list[dict], out_dir: Path, build_id: str,
                     registry_digest: str | None) -> dict:
    """Emit the sharded data the page loads: meta -> pages -> details.

    Shapes:
      data/meta.json          tiny, always revalidated; declares the build id
      data/pages/p<idx>.json  PAGE_SIZE cards each, in default display order
      data/details/<id>.json  one full entry, fetched when a card is opened

    meta.json is the only file fetched uncached - it is ~1 KiB and it tells
    the browser which build the rest of the URLs belong to. Pages and details
    are immutable for a given build id, so they can be cached hard.
    """
    data_dir = out_dir / "data"

    # Default display order is by name, so page 0 is exactly what an
    # unfiltered first paint shows - no re-sorting a partial list.
    ordered = sorted(
        entries,
        key=lambda e: (
            (e.get("display_name") or e.get("name") or "").lower(),
            e.get("agent_id", ""),
        ),
    )
    cards = [card_of(entry) for entry in ordered]

    pages: list[dict] = []
    for index in range(0, len(cards), PAGE_SIZE):
        chunk = cards[index:index + PAGE_SIZE]
        name = f"p{len(pages):03d}.json"
        write_json(data_dir / "pages" / name, {"agents": chunk})
        pages.append({"file": f"data/pages/{name}", "count": len(chunk)})

    for entry in ordered:
        write_json(data_dir / "details" / f"{entry['agent_id']}.json", {"agent": entry})

    meta = {
        "schema_version": int(doc.get("schema_version", 1)),
        "build": build_id,
        "registry_digest": registry_digest,
        "generated_at": doc.get("generated_at"),
        "total": len(cards),
        "page_size": PAGE_SIZE,
        "page_count": len(pages),
        "pages": pages,
        # Facets come from the published index - the page must not recount
        # them, or the UI would show numbers matching only the loaded pages.
        "facets": doc.get("facets") or {},
        "index_file": "data/index.json",
        "detail_prefix": "data/details/",
    }
    write_json(data_dir / "meta.json", meta)
    return meta


def compute_build_id(index_path: Path, site_dir: Path) -> str:
    """Content identity of this site build: registry data *and* page code.

    Two properties matter, and a naive hash of index.json gets both wrong:

    * a rebuild of unchanged content must yield the same id, otherwise every
      deploy busts the immutable cache of every shard, script and stylesheet
      and returning visitors re-download the world. `generated_at` changes on
      every run and `source.commit` changes on every commit — including commits
      that touch only docs — so both are excluded: they are provenance, not
      content. Any real change shows up in the entries themselves (version,
      sha256, labels, sizes).
    * a change to hub.js or styles.css must yield a *different* id, otherwise
      visitors pinned to an old cached script would run it against new data —
      and no amount of cache-busting on the data side fixes that.

    So the id covers the registry minus its build metadata plus the bytes of
    the files the page ships.
    """
    doc = json.loads(index_path.read_text(encoding="utf-8"))
    provenance = ("generated_at", "source")
    for key in provenance:
        doc.pop(key, None)
    h = hashlib.sha256()
    h.update(json.dumps(doc, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    for name in ("index.html", "hub.js", "styles.css", "_headers"):
        f = site_dir / name
        if f.is_file():
            # Line endings are canonicalised: `.gitattributes` normalises these
            # to LF on checkout, so a Windows working copy and a CI checkout run
            # the same page in different bytes. Hashing raw bytes would make the
            # id — and therefore every immutable asset URL — platform-dependent.
            code = f.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            h.update(b"\0" + name.encode() + b"\0" + code)
    return h.hexdigest()[:12]


def stamp_asset_versions(html: str, build_id: str) -> str:
    """Rewrite `?v=<anything>` on same-bundle asset URLs to the build id.

    hub.js and styles.css are cached forever by URL, so their version marker
    must come from the build, not from a human remembering to bump `?v=1`.
    A stale script reading new shards is a correctness bug, and an
    hand-maintained counter is exactly how that bug survives review.
    """
    def repl(m: re.Match) -> str:
        return "%s?v=%s" % (m.group(1), build_id)

    return re.sub(r'(?<!//)([\w./-]+\.(?:js|css))\?v=[^"\'\s]+', repl, html)


def agent_avatar_source(agents_dir: Path, entry: dict) -> Path | None:
    """The agent's own avatar file, resolved from its source directory.

    The index records the deployed path (`avatars/<agent_id>.<ext>`); the
    file itself lives with the agent source, so it is looked up there
    rather than in site/ (which holds no agent content).
    """
    source = entry.get("source") or {}
    path = source.get("path")
    if not path:
        return None
    agent_dir = agents_dir.parent / path
    manifest = agent_dir / "manifest.toml"
    if not manifest.is_file():
        return None
    try:
        import tomllib
    except ModuleNotFoundError:
        return None
    m = tomllib.loads(manifest.read_text(encoding="utf-8"))
    declared = m.get("avatar")
    if not declared:
        return None
    return agent_dir / declared


def lint_headers(site_root: Path, out_dir: Path) -> list[str]:
    """Fail if two `_headers` rules match the same deployed file and set the
    same header name.

    Cloudflare joins the values of every matching rule with a comma, so a
    duplicated rule silently produces `Cache-Control: no-cache, no-cache` —
    and a broad rule plus a specific one can produce a self-contradicting
    value like `no-cache, public, max-age=31536000`. The cache policy is what
    makes the sharded data layer safe to cache hard, so a silently broken
    header is a correctness problem, not a cosmetic one.

    The lint runs against the files this build actually produced, so it cannot
    be satisfied by reasoning about hypothetical URLs.
    """
    headers_file = site_root / "_headers"
    if not headers_file.is_file():
        return []

    rules_by_path: dict[str, dict[str, list[str]]] = {}
    path = None
    for line in headers_file.read_text(encoding="utf-8").splitlines():
        text = line.rstrip()
        if not text.strip() or text.lstrip().startswith("#"):
            continue
        if text.startswith((" ", "	")):
            if path is None:
                continue
            name, _, value = text.strip().partition(":")
            rules_by_path[path].setdefault(name.strip(), []).append(value.strip())
        else:
            path = text.strip()
            rules_by_path.setdefault(path, {})

    def matches(pattern: str, url_path: str) -> bool:
        if "*" in pattern:
            prefix, _, suffix = pattern.partition("*")
            return url_path.startswith(prefix) and url_path.endswith(suffix)
        return url_path == pattern

    problems: list[str] = []
    for file in sorted(out_dir.rglob("*")):
        if not file.is_file() or file.name == "_headers":
            continue
        url = "/" + file.relative_to(out_dir).as_posix()
        hit = {pat: hdrs for pat, hdrs in rules_by_path.items() if matches(pat, url)}
        counts: dict[str, int] = {}
        for hdrs in hit.values():
            for name in hdrs:
                counts[name] = counts.get(name, 0) + 1
        for name, count in counts.items():
            if count > 1:
                values = [f"{pat} -> {v}" for pat, v in
                          ((p_, h[name][0]) for p_, h in hit.items() if name in h)]
                problems.append(
                    f"{url}: `{name}` set by {count} rules ({'; '.join(values)}) — "
                    "Cloudflare joins them with a comma"
                )
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--index", required=True, help="path to the generated index.json")
    ap.add_argument("--packages", required=True, help="directory holding the signed .agent packages")
    ap.add_argument("--agents", default=None,
                    help="agents/ directory (default: the repo's agents/) — used to resolve avatar files")
    ap.add_argument("--out", required=True, help="output directory for the deployable site")
    ap.add_argument("--registry-digest", default=None,
                    help="registry.digest.json, to record which registry content this site serves")
    args = ap.parse_args()

    index_path = Path(args.index)
    packages_dir = Path(args.packages)
    out_dir = Path(args.out)
    agents_dir = Path(args.agents) if args.agents else REPO_ROOT / "agents"

    if not index_path.is_file():
        print(f"[build-site] FATAL: {index_path} not found. Exit 2", file=sys.stderr)
        return 2
    if not packages_dir.is_dir():
        print(f"[build-site] FATAL: {packages_dir} not found. Exit 2", file=sys.stderr)
        return 2

    try:
        doc = json.loads(index_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"cannot parse {index_path}: {exc}")

    entries = doc.get("agents") or []
    if not entries:
        fail(f"{index_path} contains no agents — nothing to publish")

    # 1. copy the page sources
    out_dir.mkdir(parents=True, exist_ok=True)
    # Rebuilding into a directory that already holds a previous build must not
    # leave shards or packages behind for agents that are no longer published.
    for name in GENERATED_DIRS:
        stale = out_dir / name
        if stale.is_dir():
            shutil.rmtree(stale)
    # The build id is the hash of the published index, so it changes only when
    # the registry content changes. Cached URLs therefore stay valid across
    # deploys that did not change anything — the same property the publish gate
    # enforces on the packages. It is needed here because the page's own assets
    # are versioned by it.
    build_id = compute_build_id(index_path, SITE_DIR)

    for name in ("hub.js", "styles.css"):
        shutil.copy2(SITE_DIR / name, out_dir / name)
    (out_dir / "index.html").write_text(
        stamp_asset_versions((SITE_DIR / "index.html").read_text(encoding="utf-8"), build_id),
        encoding="utf-8",
    )
    shutil.copytree(SITE_DIR / "assets", out_dir / "assets", dirs_exist_ok=True)

    # Cloudflare Pages reads `_headers` from the deployed root to set the
    # response headers — without it a browser may try to render an .agent
    # package as text instead of downloading it.
    headers = SITE_DIR / "_headers"
    if headers.is_file():
        shutil.copy2(headers, out_dir / "_headers")
    cname = SITE_DIR / "CNAME"
    if cname.is_file():
        shutil.copy2(cname, out_dir / "CNAME")

    # 2. the index
    (out_dir / "data").mkdir(exist_ok=True)
    shutil.copy2(index_path, out_dir / "data" / "index.json")

    # 3. the packages
    (out_dir / "downloads").mkdir(exist_ok=True)
    for entry in entries:
        agent_id = entry["agent_id"]
        pkg = packages_dir / f"{agent_id}.agent"
        if not pkg.is_file():
            fail(f"package for `{agent_id}` not found at {pkg}")
        shutil.copy2(pkg, out_dir / "downloads" / pkg.name)

    # 4. the avatars — the index references them by relative path, so the
    #    source is the agent's own asset file, not a copy in site/.
    (out_dir / "avatars").mkdir(exist_ok=True)
    for entry in entries:
        avatar = entry.get("avatar")
        if not avatar:
            continue
        src = agent_avatar_source(agents_dir, entry)
        if src is None or not src.is_file():
            print(f"[build-site] WARN  {entry['agent_id']}: avatar {avatar} missing — skipping")
            continue
        shutil.copy2(src, out_dir / avatar)

    # 5. the sharded data layer: meta -> pages -> details
    digest_value = None
    if args.registry_digest:
        digest_path = Path(args.registry_digest)
        if digest_path.is_file():
            digest_value = json.loads(digest_path.read_text(encoding="utf-8")).get(
                "registry_digest"
            )
        else:
            print(
                f"[build-site] WARN  registry digest {digest_path} not found — "
                "meta.json will not declare which registry content this site serves",
                file=sys.stderr,
            )
    meta = write_data_layer(doc, entries, out_dir, build_id, digest_value)

    # 6. the cache policy, checked against the files that were just written
    problems = lint_headers(SITE_DIR, out_dir)
    if problems:
        for problem in problems:
            print(f"[build-site] ERROR: {problem}", file=sys.stderr)
        fail("_headers rules collide — see docs/publishing.md#how-the-page-loads-its-data")

    total = sum(e["package"]["size_bytes"] for e in entries)
    print(
        f"[build-site] wrote {out_dir} | {len(entries)} agents | "
        f"{meta['page_count']} page shard(s) of {meta['page_size']} | build {build_id} | "
        f"{total / 1024:.0f} KiB of packages"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
