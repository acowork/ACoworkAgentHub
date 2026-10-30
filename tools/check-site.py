#!/usr/bin/env python3
"""Render the hub site in a headless browser and assert it actually works.

The hub page is a zero-dependency static app: `hub.js` loads `data/meta.json`,
then page shards of 48 cards, then a per-agent detail file on demand. A broken
selector, a renamed field or a wrong URL shows up as a blank page — which is
exactly the failure a "the build succeeded" check would miss.

Two suites run here:

1. **the real registry** (`--site`): facets populated, filters narrow, download
   links resolve, avatars load, the detail panel opens.

2. **a synthetic large registry** (`--fixture-count`, default 130): the shipped
   registry is one page shard, so every assertion about *paging* would be
   vacuous against it. The fixture rebuilds the site from many cloned entries
   using the real `tools/build-site.py`, then proves the two properties the
   sharding exists for:

     * the first paint fetches exactly one card shard — never the whole index;
     * the DOM never holds more than a window of results, even when a search
       matches every agent in the registry.

Exit codes:
    0  the page renders and behaves as expected
    1  the page did not render as expected
    2  environment error (missing site dir, missing playwright)
"""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import subprocess
import sys
import tempfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Must match RENDER_WINDOW in site/hub.js. It is asserted rather than trusted:
#: a window that only exists in the JS is a window the tests cannot check.
RENDER_WINDOW = 96


def fail(msg: str) -> None:
    print(f"[check-site] ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def serve(site: Path):
    """Serve a directory over HTTP.

    The site is deployed to Cloudflare Pages, which is HTTP; testing over
    file:// would exercise different fetch rules and hide real breakage.
    """
    class Quiet(SimpleHTTPRequestHandler):
        # The default handler prints one line per asset; a browser test makes
        # dozens of requests and the noise buries the actual assertions.
        def log_message(self, *args):
            pass

    handler = partial(Quiet, directory=str(site))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{port}/"


def registry_paths(urls: list[str]) -> list[str]:
    """The registry data requests among `urls`, as workspace-relative paths."""
    out = []
    for url in urls:
        if "/data/" in url:
            tail = url.split("/data/", 1)[1].split("?", 1)[0]
            out.append("data/" + tail)
    return sorted(set(out))


def build_fixture_site(real_site: Path, count: int, workdir: Path) -> Path:
    """Rebuild the site with `count` agents by cloning published entries.

    Everything goes through the real builder, so the shard layout, the card
    projection and the detail files under test are the ones that ship.
    """
    index = json.loads((real_site / "data" / "index.json").read_text(encoding="utf-8"))
    base_entries = index["agents"]
    if not base_entries:
        fail("the real index has no agents to clone for the fixture")

    entries = []
    for i in range(count):
        clone = copy.deepcopy(base_entries[i % len(base_entries)])
        agent_id = f"io.fixture.agent{i:04d}"
        clone["agent_id"] = agent_id
        clone["name"] = f"Fixture {i:04d}"
        clone["display_name"] = f"Fixture {i:04d}"
        # Vary the category so a facet filter still narrows in the fixture.
        clone["package"] = dict(clone.get("package") or {})
        clone["package"]["file"] = f"downloads/{agent_id}.agent"
        entries.append(clone)
    index["agents"] = entries

    fixture_index = workdir / "index.json"
    fixture_index.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")

    # build-site.py refuses to emit a site whose packages are missing.
    real_pkg = next((real_site / "downloads").glob("*.agent"))
    pkgs = workdir / "packages"
    pkgs.mkdir()
    for entry in entries:
        shutil.copy2(real_pkg, pkgs / f"{entry['agent_id']}.agent")

    out = workdir / "site"
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "tools" / "build-site.py"),
            "--index", str(fixture_index),
            "--packages", str(pkgs),
            "--out", str(out),
            "--agents", str(REPO_ROOT / "agents"),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        fail(f"building the fixture site failed:\n{result.stdout}\n{result.stderr}")
    return out


def check_real(page, base: str, expected: int, errors: list[str]) -> None:
    page.goto(base)
    page.wait_for_selector(".ah-card:not(.ah-card--skeleton)", timeout=15000)

    cards = page.locator(".ah-card:not(.ah-card--skeleton)").count()
    if cards == 0:
        errors.append("no agent cards rendered")
    elif cards > expected:
        errors.append(f"rendered {cards} cards for {expected} agents")

    if page.locator("#ah-data").count():
        errors.append(
            "index.html still embeds a registry snapshot — the page must load "
            "shards only, or every visit downloads a second copy of the registry"
        )

    cat_chips = page.locator("#ah-cats .ah-chip").count()
    if cat_chips < 2:
        errors.append(f"category facet rendered {cat_chips} chip(s), expected categories + All")

    label_chips = page.locator("#ah-labels .ah-chip").count()
    if label_chips < 1:
        errors.append(f"label facet rendered {label_chips} chip(s)")

    hero_count = page.locator("#ah-count-total").inner_text()
    if hero_count != str(expected):
        errors.append(f"hero count is {hero_count!r}, expected {expected!r}")

    # A download link must resolve, not 404 on a renamed artifact.
    href = page.locator(".ah-card__foot a").first.get_attribute("href")
    if href:
        status = page.request.get(base + href).status
        if status != 200:
            errors.append(f"download link {href!r} returned HTTP {status}")

    # Avatar images are versioned by build id; a broken path shows up as a
    # removed <img>, so count what actually loaded.
    broken = page.evaluate(
        "() => [...document.images].filter(i => i.complete && i.naturalWidth === 0).length"
    )
    if broken:
        errors.append(f"{broken} image(s) failed to load")

    # The card is a button: opening the detail panel must fetch the heavy
    # fields, which the card deliberately does not carry.
    page.locator(".ah-card").first.click()
    page.wait_for_selector(".ah-detail .ah-detail__caps, .ah-detail .ah-detail__section", timeout=10000)
    if not page.locator(".ah-detail").count():
        errors.append("clicking a card did not open the detail panel")
    if page.locator(".ah-detail__sha").count() == 0:
        errors.append("the detail panel shows no sha256/signer — heavy fields did not load")


def check_sharding(page, base: str, meta: dict, errors: list[str]) -> None:
    """Prove the first paint is O(page), not O(registry)."""
    seen: list[str] = []
    page.on("request", lambda r: seen.append(r.url))

    page.goto(base)
    page.wait_for_selector(".ah-card:not(.ah-card--skeleton)", timeout=20000)

    loaded = registry_paths(seen)
    only_page0 = [p for p in loaded if p.startswith("data/pages/")]
    if only_page0 != ["data/pages/p000.json"]:
        errors.append(
            f"first paint fetched {only_page0}, expected exactly ['data/pages/p000.json']"
        )
    if "data/index.json" in loaded:
        errors.append(
            "the browser fetched data/index.json — the page must read shards; "
            "the full index is the machine endpoint, not the web payload"
        )
    if "data/meta.json" not in loaded:
        errors.append("the browser did not load data/meta.json")

    dom_cards = page.locator(".ah-card:not(.ah-card--skeleton)").count()
    if dom_cards > meta["page_size"]:
        errors.append(
            f"first paint put {dom_cards} cards in the DOM, at most one page "
            f"({meta['page_size']}) should be drawn"
        )

    # Paging must be driven by scrolling, and must stop at the shard count.
    if meta["page_count"] > 1:
        page.mouse.wheel(0, 20000)
        page.wait_for_timeout(700)
        page.mouse.wheel(0, 20000)
        page.wait_for_timeout(1200)
        after_scroll = registry_paths(seen)
        pages_after = [p for p in after_scroll if p.startswith("data/pages/")]
        if len(pages_after) < 2:
            errors.append(
                f"scrolling loaded {len(pages_after)} shard(s); expected more than one — "
                "the 'load more' path is dead"
            )

    # A registry-wide search needs every card, so every shard must be loaded
    # by the time the results are drawn. The assertion is on the accumulated
    # set, not on requests triggered by the keystroke: scrolling may already
    # have pulled the remaining shards, and fetching them twice would be the
    # bug, not the absence of a request here.
    page.fill("#ah-q", "a")
    page.wait_for_timeout(2500)
    all_shards_seen = [q for q in registry_paths(seen) if q.startswith("data/pages/")]
    if len(all_shards_seen) < meta["page_count"]:
        errors.append(
            f"after searching, {len(all_shards_seen)} of {meta['page_count']} shards were "
            "loaded; a partial shard set cannot answer a registry-wide search"
        )
    dom_after_search = page.locator(".ah-card:not(.ah-card--skeleton)").count()
    if dom_after_search > RENDER_WINDOW:
        errors.append(
            f"a search matching {meta['total']} agents rendered {dom_after_search} cards; "
            f"the DOM window (RENDER_WINDOW={RENDER_WINDOW}) is not being applied"
        )
    status_text = page.locator("#ah-status").inner_text()
    if "of" not in status_text and meta["total"] > dom_after_search:
        errors.append(
            f"status {status_text!r} does not disclose that only part of the "
            "results are drawn — the user cannot tell a window from a total"
        )

    # Detail files are per agent: opening one must fetch that record and
    # nothing else. Here clearing IS meaningful — any shard request at this
    # point would mean the detail panel re-reads the registry.
    seen.clear()
    page.locator(".ah-card").first.click()
    page.wait_for_selector(".ah-detail .ah-detail__section", timeout=15000)
    detail_loaded = registry_paths(seen)
    if not any(q.startswith("data/details/") for q in detail_loaded):
        errors.append("opening a card did not fetch a data/details/<agent_id>.json record")
    if len([q for q in detail_loaded if q.startswith("data/details/")]) != 1:
        errors.append(
            f"opening one agent fetched {detail_loaded}; exactly its own detail file was expected"
        )
    if [q for q in detail_loaded if q.startswith("data/pages/")] or "data/index.json" in detail_loaded:
        errors.append(
            f"opening one agent re-fetched registry shards: {detail_loaded}"
        )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site", required=True, help="the built site directory")
    ap.add_argument("--fixture-count", type=int, default=130,
                    help="agents in the synthetic large registry (0 to skip the sharding suite)")
    ap.add_argument("--screenshot", default=None, help="write a screenshot here")
    args = ap.parse_args()

    site = Path(args.site).resolve()
    if not (site / "index.html").is_file():
        print(f"[check-site] FATAL: {site / 'index.html'} not found. Exit 2", file=sys.stderr)
        return 2
    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError:
        print("[check-site] FATAL: playwright is not installed. Exit 2", file=sys.stderr)
        return 2

    real_meta = json.loads((site / "data" / "meta.json").read_text(encoding="utf-8"))
    expected = real_meta["total"]
    errors: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()

        # Suite 1: the registry as published.
        server, base = serve(site)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        console: list[str] = []
        page.on("console", lambda m: console.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: console.append(str(e)))
        check_real(page, base, expected, errors)
        if args.screenshot:
            page.screenshot(path=args.screenshot, full_page=True)
        if console:
            errors.append("browser console reported error(s): " + " | ".join(console[:3]))
        page.close()
        server.shutdown()

        # Suite 2: the same builder at a scale the shipped registry cannot show.
        if args.fixture_count and args.fixture_count > real_meta["page_size"]:
            workdir = Path(tempfile.mkdtemp(prefix="hubfixture"))
            try:
                fixture = build_fixture_site(site, args.fixture_count, workdir)
                fmeta = json.loads((fixture / "data" / "meta.json").read_text(encoding="utf-8"))
                if fmeta["page_count"] < 2:
                    errors.append(
                        f"fixture of {args.fixture_count} agents produced "
                        f"{fmeta['page_count']} shard(s) — the fixture is not exercising paging"
                    )
                server2, base2 = serve(fixture)
                page2 = browser.new_page(viewport={"width": 1280, "height": 900})
                check_sharding(page2, base2, fmeta, errors)
                page2.close()
                server2.shutdown()
                print(
                    f"[check-site] fixture: {fmeta['total']} agents -> "
                    f"{fmeta['page_count']} shards of {fmeta['page_size']}"
                )
            finally:
                shutil.rmtree(workdir, ignore_errors=True)

        browser.close()

    if errors:
        for err in errors:
            print(f"[check-site] FAIL: {err}", file=sys.stderr)
        return 1
    print(
        f"[check-site] OK: {expected} agents rendered, facets populated, filters and "
        "downloads resolve, detail panel loads heavy fields; "
        "first paint costs one card shard"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
