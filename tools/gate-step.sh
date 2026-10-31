#!/usr/bin/env bash
# The publish gate: decide whether anything has to be packed, signed and
# released, and say so on stdout.
#
# Why this is a script and not a block of YAML
# --------------------------------------------
# The gate's answer is carried by an exit code: `registry-digest.py --compare`
# exits 2 for "nothing to publish" and 0 for "publish required". GitHub runs
# every `run:` block under `bash -e`, which aborts at the first non-zero exit.
# So the exit code has to be captured in the same statement that produces it,
# and the logic has to be somewhere it can be tested.
#
# It was a block of YAML once. `selftest-publish.py` called
# `registry-digest.py` directly, so it never saw the `bash -e` that GitHub
# applies - and the workflow failed on every push that did not change the
# registry, because "nothing to publish" was read as a crash. The test was
# green and the pipeline was broken, for the same reason a path spelled
# `detA` passes on Windows: the test measured a different thing from the one
# that ships.
#
# Now the logic lives here, and `selftest-publish.py` runs *this file* under
# `bash -e` - the same shell, the same flags, the same exit codes.
#
# Usage:
#   GITHUB_OUTPUT=/path/to/output bash tools/gate-step.sh
#
# Environment:
#   GITHUB_OUTPUT  file to append `publish=` and `changed_count=` to
#   FORCE          "true" to ignore the content gate and republish everything
#   PYTHON         the interpreter to use (default: the first of `python`,
#                  `python3` that exists on PATH)
#
# The interpreter is also read from the file .ci/python-path, if it exists,
# and from $PYTHON. That file is how the self test points the script at the
# exact interpreter running the test: an environment variable is not
# dependable across the Windows/Unix boundary that a shell script sits on,
# but a file is.
#
# When neither is given, the interpreter is chosen the way the workflow
# expects it: `python` from PATH. In CI that is the 3.11 that
# `actions/setup-python` installed, which is what the tools need (they use
# `tomllib`, added in 3.11).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

: "${GITHUB_OUTPUT:=/dev/null}"
FORCE="${FORCE:-false}"

mkdir -p .ci

if [ -f .ci/python-path ]; then
  PYTHON="$(cat .ci/python-path)"
elif [ -n "${PYTHON:-}" ]; then
  :
else
  if command -v python >/dev/null 2>&1; then
    PYTHON=python
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON=python3
  else
    echo "::error::no python interpreter on PATH" >&2
    exit 1
  fi
fi

# The tools need Python 3.11 or newer (they use `tomllib`). Saying so here
# is better than failing later with a confusing error about a missing
# module.
if ! "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
  echo "::error::$PYTHON is older than 3.11 - the registry tools need tomllib" >&2
  exit 1
fi

"$PYTHON" tools/registry-digest.py --out .ci/current.digest.json \
    --key .keys/registry.key

if [ -f .ci/registry.digest.json ]; then
  # `set +e` around the call: exit 2 is a normal answer here, not a failure.
  set +e
  "$PYTHON" tools/registry-digest.py --compare .ci/registry.digest.json \
      --key .keys/registry.key --changed-out .ci/changed.txt
  rc=$?
  set -e
else
  echo "no baseline digest - full build"
  rc=0
fi

if [ "$FORCE" = "true" ]; then
  echo "::notice::force requested - ignoring the content gate"
  rc=0
  rm -f .ci/changed.txt   # no list = rebuild every agent
fi

if [ "$rc" -eq 2 ]; then
  publish=false
  echo "::notice::the published bytes would be identical - nothing to publish"
elif [ "$rc" -ne 0 ]; then
  echo "::error::the content gate failed with exit code $rc"
  exit "$rc"
else
  publish=true
fi

changed_count="$( [ -f .ci/changed.txt ] && wc -l < .ci/changed.txt || echo all )"

# The site is deployed only when its content or its code moved. The commit
# that was deployed last is kept as a release asset; if it is this commit,
# the site is already up to date.
deployed="$(cat .ci/deployed-commit 2>/dev/null || echo none)"
if [ -n "${GITHUB_SHA:-}" ] && [ "$deployed" = "${GITHUB_SHA}" ]; then
  deploy=false
else
  deploy=true
fi

{
  echo "publish=$publish"
  echo "changed_count=$changed_count"
  echo "deploy=$deploy"
} >> "$GITHUB_OUTPUT"

echo "gate: publish=$publish changed_count=$changed_count deploy=$deploy"
