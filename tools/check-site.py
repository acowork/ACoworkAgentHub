#!/usr/bin/env python3
"""Render the hub site in a headless browser and assert it actually works.

HTTP 200 on index.html proves nothing: the page is rendered entirely by
hub.js from data/index.json. A broken selector, a renamed field or a
facet that never renders produces a 200 with an empty page.

This script opens the built site in Chromium and checks that a real
agent list appears, that the facets are populated, that the filters
narrow the list, and that every download link points at a file that
actually exists on disk.

Usage:
    python tools/check-site.py --site dist/site

Requires: playwright (pip install playwright && playwright install chromium)

Exit codes:
    0  ok
    1  the page did not render as expected
    2  environment error (playwright missing, site not built)
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def fail(msg: str) -> None:
    print(f"[check-site] ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site", required=True, help="the built site directory")
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

    index = json.loads((site / "data" / "index.json").read_text(encoding="utf-8"))
    expected = len(index.get("agents") or [])

    errors: list[str] = []

    # The site is served over HTTP in production (Cloudflare Pages). Testing
    # it over HTTP is the only honest test of that deployment — file:// has
    # different rules (no fetch, font CORS) and would hide real breakage.
    handler = partial(SimpleHTTPRequestHandler, directory=str(site))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}/"

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})

        console_errors: list[str] = []
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: console_errors.append(str(e)))

        page.goto(base)
        page.wait_for_selector(".ah-card:not(.ah-card--skeleton)", timeout=15000)

        # 1. the cards rendered
        cards = page.locator(".ah-grid .ah-card").count()
        if cards != expected:
            errors.append(f"expected {expected} cards, rendered {cards}")

        # 2. the skeleton is gone
        if page.locator("#ah-skeleton").is_visible():
            errors.append("the loading skeleton is still visible")

        # 3. the error box is hidden
        if page.locator("#ah-error").is_visible():
            errors.append("the error box is visible: " + page.locator("#ah-error-msg").inner_text())

        # 4. the category facet is populated
        cat_chips = page.locator("#ah-cats .ah-chip").count()
        if cat_chips < 2:
            errors.append(f"category facet has {cat_chips} chip(s), expected the categories plus 'All'")

        # 5. the label facet is populated
        label_chips = page.locator("#ah-labels .ah-chip").count()
        if label_chips < 2:
            errors.append(f"label facet has {label_chips} chip(s), expected the labels plus 'Any label'")

        # 6. the count in the hero matches the index
        hero_count = page.locator("#ah-count-total").inner_text().strip()
        if hero_count != str(expected):
            errors.append(f"hero count is {hero_count!r}, expected {expected}")

        # 7. searching narrows the list
        page.fill("#ah-q", "product")
        page.wait_for_timeout(200)
        filtered = page.locator(".ah-grid .ah-card").count()
        if filtered == 0:
            errors.append("searching for 'product' matched nothing")
        elif filtered >= expected:
            errors.append(f"searching for 'product' did not narrow the list ({filtered}/{expected})")
        page.click("#ah-clear")
        page.wait_for_timeout(200)

        # 8. a category filter narrows the list
        #    Pick a category that actually has agents — the empty ones are
        #    rendered disabled, and clicking one is not a filter test.
        populated = page.locator("#ah-cats .ah-chip:not([disabled])")
        if populated.count() < 2:
            errors.append("no category chip has any agent in it")
        else:
            populated.nth(1).click()
            page.wait_for_timeout(200)
            by_cat = page.locator(".ah-grid .ah-card").count()
            if by_cat == 0 or by_cat >= expected:
                errors.append(f"the category filter did not narrow the list ({by_cat}/{expected})")
            page.click("#ah-reset")
            page.wait_for_timeout(200)

        # 9. every download link points at a file that exists
        links = page.locator(".ah-card__foot a[download]").evaluate_all(
            "els => els.map(e => e.getAttribute('href'))"
        )
        for href in links:
            if href.startswith("#"):
                errors.append(f"download link {href!r} has no target")
                continue
            if not (site / href.lstrip("./")).is_file():
                errors.append(f"download link {href!r} points at a missing file")

        # 10. no console errors
        real_errors = [e for e in console_errors if "favicon" not in e.lower()]
        if real_errors:
            errors.append("console errors: " + "; ".join(real_errors[:5]))

        if args.screenshot:
            page.screenshot(path=args.screenshot, full_page=True)
            print(f"[check-site] screenshot -> {args.screenshot}")

        # 11. the file:// fallback still renders.
        #
        # Over file:// the browser blocks fetch(), so the page must fall
        # back to the index embedded in the page. This is the path a
        # contributor hits when they double-click the built index.html —
        # it must not be an empty page.
        offline = browser.new_page()
        offline.goto((site / "index.html").as_uri())
        try:
            offline.wait_for_selector(".ah-card:not(.ah-card--skeleton)", timeout=8000)
            offline_cards = offline.locator(".ah-grid .ah-card").count()
            if offline_cards != expected:
                errors.append(
                    f"the file:// fallback rendered {offline_cards} cards, expected {expected}"
                )
        except Exception:
            errors.append("the file:// fallback rendered no cards")
        offline.close()

        browser.close()

    server.shutdown()

    if errors:
        for e in errors:
            print(f"[check-site] ERROR {e}")
        print(f"[check-site] FAILED: {len(errors)} problem(s)")
        return 1

    print(f"[check-site] OK: {expected} agents rendered, facets populated, filters work, links resolve")
    return 0


if __name__ == "__main__":
    sys.exit(main())
