#!/usr/bin/env python3
"""Serve the built hub site locally, honouring its _headers file.

`python -m http.server` ignores `_headers`, so a local check cannot see the
cache policy — which is exactly the layer where a stale `meta.json` breaks the
page. This server reads the same file Cloudflare Pages reads and applies it to
responses, so the local test is faithful for headers too.

Pattern semantics follow Cloudflare's Pages rules (docs/_headers.html):

  /path            exact match, and matches that path with a trailing slash
  /path/*          everything under /path/
  /prefix*         the prefix itself plus anything appended to it
  *.ext            any URL whose *last* path segment ends with it
  /p?query=val     query string must match

Longest pattern wins, like CSS. It is an approximation — Cloudflare evaluates
against the full hostname and can also see request headers — but it is close
enough to catch the failure that matters: two rules setting the same header for
one file, which Cloudflare would join with a comma.

Usage:
  python tools/serve-site.py [--site dist/site] [--port 8080] [--print-headers]
"""

from __future__ import annotations

import argparse
import functools
import http.server
import re
import socketserver
import sys
import unicodedata
from pathlib import Path
from urllib.parse import urlparse


def normalise(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def split_target(target: str) -> tuple[str, str]:
    """'/a/b?x=1' -> ('/a/b', 'x=1')."""
    if "?" in target:
        path, _, query = target.partition("?")
        return path, query
    return target, ""


def compile_pattern(raw: str):
    """Return (matcher, specificity) for one _headers path pattern.

    Cloudflare's rule is that an asterisk matches any characters, so a pattern
    becomes an anchored regex with '*' -> '.*', and a trailing slash is
    optional. Query strings in a pattern must appear in the URL.
    """
    pattern = normalise(raw.strip())
    if not pattern or pattern.startswith(":"):
        return None, 0  # :host blocks and '!' security markers are not path rules
    query = ""
    if "?" in pattern:
        pattern, _, query = pattern.partition("?")
    if not pattern.startswith("/"):
        pattern = "/" + pattern  # bare '*.ext' is rooted at the host

    rx = "^" + re.escape(pattern).replace(r"\*", ".*") + "/?$"
    compiled = re.compile(rx)
    # a pattern query may itself contain wildcards: '?v=*' means 'has v='
    qrx = re.compile("^" + re.escape(query).replace(r"\*", ".*") + "$") if query else None

    def match(target: str) -> bool:
        path, tquery = split_target(normalise(target))
        if qrx and not qrx.match(tquery):
            return False
        return bool(compiled.match(path))

    return match, len(pattern)


def load_headers(site: Path) -> list[tuple[callable, int, dict[str, str]]]:
    """Parse _headers into (matcher, order, headers) in file order.

    Cloudflare's semantics (docs/pages/configuration/headers): a request that
    matches several patterns inherits the headers of *all* of them, and a
    header applied twice is joined with a comma. So there is no winner-takes-all
    here — merging is the behaviour, and a duplicate header name across two
    matching rules is a bug this function reports rather than hides.
    """
    f = site / "_headers"
    if not f.exists():
        return []
    rules: list[tuple[callable, int, dict[str, str]]] = []
    cur = None
    order = 0
    for line in f.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith((" ", "\t")):
            if cur is None or ":" not in line:
                continue
            key, _, val = line.strip().partition(":")
            cur[2][key.strip()] = val.strip()
            continue
        match, _spec = compile_pattern(line)
        if match is None:
            cur = None
            continue
        order += 1
        cur = (match, order, {})
        rules.append(cur)
    return rules


def headers_for(rules, target: str) -> dict[str, str]:
    """Merge every matching rule, file order, last value wins per header name.

    Real Pages would join duplicates with a comma; picking the last is the
    closest usable local behaviour, and duplicates are warned about so a local
    run cannot quietly differ from production.
    """
    out: dict[str, str] = {}
    seen_dup: set[str] = set()
    for match, _order, headers in rules:
        if not match(target):
            continue
        for key, val in headers.items():
            if key in out and out[key] != val:
                seen_dup.add(key)
            out[key] = val
    if seen_dup:
        sys.stderr.write("  WARN %s: %s set by more than one rule -> Pages would "
                         "join the values with a comma\n"
                         % (split_target(target)[0], ",".join(sorted(seen_dup))))
    return out


class HeaderedHandler(http.server.SimpleHTTPRequestHandler):
    rules: list = []

    def headers_for(self) -> dict[str, str]:
        return headers_for(self.rules, self.path)

    def send_head(self):
        # never serve the configuration files as pages
        path = urlparse(self.path).path.rstrip("/")
        if path in ("/_headers", "/_redirects", "/CNAME"):
            self.send_error(404, "not a page")
            return None
        return super().send_head()

    def send_response(self, code, message=None):
        # BaseHTTPRequestHandler does not keep the status anywhere we can read
        # during end_headers, so record it ourselves.
        try:
            self._status = int(code)
        except (TypeError, ValueError):
            self._status = 0
        super().send_response(code, message)

    def end_headers(self):
        # SimpleHTTPRequestHandler writes its own headers then calls this, so
        # this is the only hook that lands before the blank line.
        if getattr(self, "_status", 0) == 200:
            for key, val in self.headers_for().items():
                http.server.SimpleHTTPRequestHandler.send_header(self, key, val)
        super().end_headers()

    def log_message(self, fmt, *args):
        try:
            first = args[0] if args else ""
            if isinstance(first, str) and " HTTP/" in first:
                # log_request passes the whole request line
                parts = first.split()
                verb, target = parts[0], parts[1] if len(parts) > 1 else "/"
            elif isinstance(first, str):
                verb, target = getattr(self, "command", "REQ"), first
            else:
                verb, target = getattr(self, "command", "REQ"), getattr(self, "path", "/")
            cc = self.headers_for().get("Cache-Control", "(none)")
            sys.stderr.write("  %s %s -> %s | Cache-Control: %s\n"
                             % (verb, split_target(target)[0], getattr(self, "_status", "?"), cc))
        except Exception:
            pass  # logging must never break a response


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site", default="dist/site", help="directory to serve (default: dist/site)")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--print-headers", action="store_true",
                    help="show the parsed rules and exit (no server)")
    args = ap.parse_args()

    site = Path(args.site)
    if not (site / "index.html").exists():
        print("ERROR: %s has no index.html. Build it first:\n"
              "  python tools/build-site.py --index dist/index.json --packages dist --out dist/site"
              % site, file=sys.stderr)
        return 1

    rules = load_headers(site)
    if args.print_headers:
        # Show what Pages would actually serve, per real file in the bundle.
        print("%d rule(s) from %s/_headers\n" % (len(rules), site))
        files = sorted(p for p in site.rglob("*") if p.is_file() and p.name != "_headers")
        for p in files:
            rel = "/" + p.relative_to(site).as_posix()
            merged = headers_for(rules, rel)
            cc = merged.get("Cache-Control", "(default)")
            extra = " ".join("%s=%s" % (k, v) for k, v in merged.items() if k != "Cache-Control")
            print("  %-40s %-46s %s" % (rel, cc, extra))
        print("\n  (%d file(s))" % len(files))
        return 0

    HeaderedHandler.rules = rules
    # SimpleHTTPRequestHandler only honours `directory` as a constructor
    # argument, so bind it rather than assigning a class attribute.
    handler = functools.partial(HeaderedHandler, directory=str(site))
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", args.port), handler) as httpd:
        print("hub site: http://127.0.0.1:%d/   (%d header rule(s) active, Ctrl-C to stop)"
              % (args.port, len(rules)))
        print("watching: requests log the Cache-Control each path resolves to\n")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
