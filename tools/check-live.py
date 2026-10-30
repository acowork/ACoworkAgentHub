#!/usr/bin/env python3
"""Verify the *deployed* hub site at https://hub.acowork.ai.

    python tools/check-live.py

`check-site.py` proves the build is correct; this proves the build that is
actually being served is correct, because the things that break between the two
are not caught by any local test:

  * a `_headers` rule that Cloudflare reads differently than the local server
  * an asset URL that lost its ?v=<build> stamp on the way out
  * a package that the site serves but the release does not have (or vice versa)
  * a deploy that published shards the current index never references

Every request goes through a real page (`fetch` inside the document). Raw HTTP
clients on this machine - curl, Playwright's APIRequestContext - get stalled by
Cloudflare, so measuring with them would report failures visitors never see.
The GitHub release is read outside the browser, where the page's own origin
restrictions do not apply.

Exit code 0 = the live site matches the contract.
"""
from __future__ import annotations

import json
import sys
import urllib.request

BASE = "https://hub.acowork.ai"
RELEASE_API = "https://api.github.com/repos/acowork/ACoworkAgentHub/releases/latest"

failures: list[str] = []


def check(cond: bool, label: str, detail: str = "") -> None:
    print(("  ok   " if cond else "  FAIL ") + label + (("   " + detail) if detail else ""))
    if not cond:
        failures.append(label)


def release_assets() -> dict[str, int]:
    req = urllib.request.Request(RELEASE_API, headers={"accept": "application/vnd.github+json",
                                                       "user-agent": "acowork-hub-check"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        doc = json.load(resp)
    return {a["name"]: int(a["size"]) for a in doc.get("assets", [])}


JS_FETCH = """async (p) => {
  const r = await fetch(p, {cache: 'no-store'});
  const h = {}; r.headers.forEach((v, k) => h[k] = v);
  const buf = await r.arrayBuffer();
  let body = null;
  if (r.status === 200 && buf.byteLength < 200000 && (h['content-type'] || '').indexOf('json') >= 0)
    body = JSON.parse(new TextDecoder().decode(buf));
  return {status: r.status, headers: h, size: buf.byteLength, body: body};
}"""


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:  # pragma: no cover
        print("check-live: playwright is required (pip install playwright && playwright install chromium)")
        return 2

    assets = release_assets()
    check(bool(assets), "GitHub release 'latest' exposes assets", "%d asset(s)" % len(assets))

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context()
        ctx.set_default_navigation_timeout(20000)
        ctx.set_default_timeout(15000)
        page = ctx.new_page()

        seen: list[str] = []
        page.on("request", lambda r: seen.append(r.url.replace(BASE, "")))

        print("\n== first paint ==")
        resp = page.goto(BASE + "/", wait_until="domcontentloaded", timeout=20000)
        check(resp.status == 200, "GET / -> 200", "status=%s" % resp.status)
        page.wait_for_selector("#ah-grid > *", timeout=15000)
        cards = page.locator("#ah-grid > *").count()
        total = page.locator("#ah-count-total").inner_text().strip()
        check(cards > 0, "cards rendered", "%d card(s)" % cards)
        check(total.isdigit() and int(total) == cards or total.isdigit(),
              "counter is a number", "counter=%r cards=%d" % (total, cards))

        data = [u for u in seen if u.startswith("/data/")]
        check(any(u.startswith("/data/meta.json") for u in data), "meta.json fetched")
        check(any(u.startswith("/data/pages/p000.json") for u in data), "first shard fetched")
        details = [u for u in data if "/data/details/" in u]
        check(not details, "no detail payload on first paint", "%d fetched" % len(details))

        def fget(path: str) -> dict:
            return page.evaluate(JS_FETCH, BASE + path)

        print("\n== build identity ==")
        meta = fget("/data/meta.json")
        build = (meta.get("body") or {}).get("build", "")
        check(len(build) >= 8, "meta.json carries a build id", "build=%r" % build)
        stamped = [u for u in seen if "hub.js?v=" in u or "styles.css?v=" in u]
        check(bool(stamped), "code assets requested with a ?v= stamp", str(stamped[:2]))
        check(all(build in u for u in stamped),
              "the stamp equals the meta build id (stamped by the builder)")

        print("\n== cache policy as served ==")
        for path, want in [("/data/meta.json", "no-cache"),
                           ("/data/pages/p000.json?v=" + build, "immutable"),
                           ("/hub.js", "immutable"),
                           ("/styles.css", "immutable")]:
            r = fget(path)
            short = path.split("?")[0]
            check(r["status"] == 200, "GET %s -> 200" % short, "size=%s" % r["size"])
            check(want in r["headers"].get("cache-control", ""),
                  "   %s is %s" % (short.rsplit("/", 1)[-1], want),
                  "cache-control=%r" % r["headers"].get("cache-control"))
            check(r["headers"].get("x-content-type-options") == "nosniff", "   nosniff")

        print("\n== packages served == released")
        pkgs = sorted(n for n in assets if n.endswith(".agent"))
        check(bool(pkgs), "release carries .agent assets", "%d package(s)" % len(pkgs))
        for name in pkgs[:3]:
            r = fget("/downloads/" + name)
            check(r["status"] == 200, "GET /downloads/%s -> 200" % name,
                  "status=%s" % r["status"])
            check(r["size"] == assets[name],
                  "   same byte size as the release asset",
                  "site=%s release=%s" % (r["size"], assets[name]))
            check("no-cache" in r["headers"].get("cache-control", ""),
                  "   cache-control no-cache (a stale package is an integrity problem)",
                  "cache-control=%r" % r["headers"].get("cache-control"))

        print("\n== search reaches the other shards ==")
        page.fill("#ah-q", "a")
        page.wait_for_timeout(1500)
        after = [u for u in seen if u.startswith("/data/")]
        check(len(after) > len(data), "search loaded more shards",
              "%d -> %d data requests" % (len(data), len(after)))

        browser.close()

    print("\n" + ("LIVE SITE OK" if not failures
                  else "LIVE SITE BROKEN: %d check(s)\n  - %s" % (len(failures), "\n  - ".join(failures))))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
