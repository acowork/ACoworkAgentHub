#!/usr/bin/env python3
"""Where a build's clock comes from - the single source of build timestamps.

Why this exists
---------------
Two builds of the same commit used to produce different bytes, for four
independent reasons, all of them time-related:

  * `zipfile.ZipInfo` defaults `date_time` to **`time.localtime()`**, so a build
    in Shanghai and a build in UTC literally differ by eight hours of clock
    ticks baked into every entry;
  * the signed attribute `signing_time` was `datetime.now()`, and it is part of
    the signature payload, so it changed the signature and therefore the whole
    package;
  * `index.json` carried a wall-clock `generated_at`;
  * the zip's compression (zlib build) and the implicit `external_attr` /
    `create_system` defaults depend on the interpreter and library on the
    machine doing the build.

Determinism is not cosmetic here. The publish gate reuses a package when its
source digest is unchanged; if the bytes also moved with the clock, then "reuse"
and "rebuild" would be indistinguishable, `SHA256SUMS` would churn on every
publish, and nobody could tell a real content change from a rebuild. It is also
the only way to check that CI and a workstation built the same thing: compare
`SHA256SUMS` and be done.

Precedence
----------
    explicit override (--build-epoch)      ->  for tests and external builders
    SOURCE_DATE_EPOCH                      ->  the reproducible-builds standard
    git: last commit touching the path     ->  the normal case, in CI and locally
    1980-01-01                             ->  fallback (no git, untracked tree)

The git rule is per-path on purpose: an agent is stamped with the commit that
last changed *that agent*, so an unrelated commit elsewhere in the repo does not
move its bytes. The floor is 1980-01-01 because the MS-DOS timestamp in a zip
entry cannot represent anything earlier.

Scope
-----
"Same commit, same bytes" - not "same working tree, same bytes". An uncommitted
edit changes the content digest, so the gate repacks; that is correct, it is
just not deterministic in the sense above, and it should not be.
"""
from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime, timezone

#: 1980-01-01T00:00:00Z - the earliest a zip entry can be stamped.
ZIP_MIN_EPOCH = 315_532_800

ENV_VAR = "SOURCE_DATE_EPOCH"


class StampError(ValueError):
    """A supplied timestamp is unusable (bad SOURCE_DATE_EPOCH, out of range)."""


def _git_commit_epoch(path) -> int | None:
    """Seconds since epoch of the last commit touching `path`, or None."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(path), "log", "-1", "--format=%ct", "--", "."],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    out = proc.stdout.strip()
    if not out:
        return None
    try:
        return int(out)
    except ValueError:
        return None


def resolve_epoch(path=None, override: int | None = None) -> tuple[int, str]:
    """Return (epoch_seconds, origin) - `origin` is a human-readable source.

    `path` is the directory whose history should provide the stamp (an agent
    directory). Pass None for a repository-level artifact such as the index,
    which is stamped with the commit that built it.
    """
    if override is not None:
        return _clamp(int(override)), "--build-epoch"

    raw = os.environ.get(ENV_VAR, "").strip()
    if raw:
        try:
            value = int(raw)
        except ValueError:
            raise StampError(f"{ENV_VAR}={raw!r} is not an integer number of seconds")
        return _clamp(value), ENV_VAR

    if path is not None:
        found = _git_commit_epoch(path)
        if found is not None:
            return _clamp(found), "git commit time"

    return ZIP_MIN_EPOCH, "fallback 1980-01-01"


def _clamp(epoch: int) -> int:
    if epoch < ZIP_MIN_EPOCH:
        return ZIP_MIN_EPOCH
    return epoch


def utc_stamp(epoch: int) -> str:
    """`%Y-%m-%dT%H:%M:%SZ` in UTC - what goes into signed attributes."""
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def zip_date(epoch: int) -> tuple[int, int, int, int, int, int]:
    """The 6-tuple a zip entry wants. gmtime, never localtime."""
    return tuple(time.gmtime(epoch)[:6])  # type: ignore[return-code]
