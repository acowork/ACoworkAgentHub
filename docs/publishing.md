# The release loop

How a submission becomes a published, signed, downloadable agent — and how
that reaches a user.

## The whole loop

```
  contributor                this repo                    hub.acowork.ai
       │                        │                              │
       │  pull request          │                              │
       ├───────────────────────▶│                              │
       │                        │                              │
       │   validate.yml         │  schema, taxonomy, layout,  │
       │   review-gate.yml      │  pack+sign+verify smoke test │
       │                        │  maintainer approval          │
       │  merged into main      │                              │
       │                        │  publish.yml                 │
       │                        │    content gate (digest)     │
       │                        │    pack only what changed    │
       │                        │    sign with the channel key │
       │                        │    build index.json          │
       │                        │    build the site (shards)   │
       │                        │    render it in a browser    │
       │                        │      │                       │
       │                        │      ├─▶ GitHub Release       │
       │                        │      │   (index.json,          │
       │                        │      │    *.agent, SHA256SUMS)  │
       │                        │      │                       │
       │                        │      └─▶ Cloudflare Pages ──────┼──▶ hub.acowork.ai
       │                        │                              │
       │                        │                              │  user downloads
       │                        │                              │  .agent package
       │                        │                              │
       │                        │  Desktop App verifies the signature
       │                        │  on install (acowork-node)    │
       ▼                        ▼                              ▼
```

Everything binary is produced by `publish.yml`. The repository never contains
a package, a checksum, a signature or an index — not on a branch either.

## The content gate

**A push that does not change the published bytes does not publish anything.**
This is a rule, not an optimisation. Re-signing identical content on every push
produces new artifacts for no reason: it makes the release history meaningless
(a "what changed since last week" question with no answer), it invalidates every
cached download URL, and it hides real changes in noise.

Published bytes are a function of three inputs, so `tools/registry-digest.py`
hashes all three:

| Input | Why it moves the published bytes |
|---|---|
| the source tree of each agent | the obvious one — prompts, skills, manifest, avatar |
| the builder (`pack.py`, `signing.py`, `build-index.py`, the schemas) | a packer fix changes every package while changing no agent |
| the signing key | the same content signed by a different key is a different artifact *and* a different trust claim |

Consequences the pipeline relies on:

- **revert does not republish.** Editing a prompt and reverting it yields the
  same digest, so nothing is signed again.
- **a builder-only change republishes without re-packing.** The digest says
  "publish", the per-agent list says "no agent changed", so the published
  packages are reused and only the index and site are rebuilt.
- **reuse is proven by content, not by bookkeeping.** `ci-pack-all.py` compares
  the signed section digests inside a published package against the current
  source tree before reusing it. A stale or hand-edited changed-list cannot
  publish an old package under a new version.

Both halves are tested on every pull request by `validate.yml`, because a gate
only exercised at release time is a gate that fails silently — either always
republishing, or never.

## The two outputs of one build

`publish.yml` produces two things from **one** run:

| Output | Where it goes | What it is |
|---|---|---|
| the site | `hub.acowork.ai` (Cloudflare Pages) | the page, the shards, and every `.agent` package under `downloads/` |
| the rolling Release | `github.com/acowork/ACoworkAgentHub/releases/tag/latest` | the same snapshot for machine consumers: `index.json`, `*.agent`, `SHA256SUMS`, `registry.digest.json` |

There is **one** release, `latest`, updated in place. Earlier this workflow
created a dated release per publish (`v2026-10-30-<sha>`), each carrying every
package: with N agents and M publishes that is N×M×2 assets accumulating
forever, and a release page listing thousands of files. Assets that a build no
longer produces are deleted, so the release always mirrors the current
snapshot rather than its whole history.

They come from the same `dist/`, so the page, the index and the downloadable
packages are always the same snapshot. A user who reads a description on the
page downloads exactly that package.

## Why the site lives in this repo

The site source is in `site/` here, not in the marketing-site repo. Three
reasons:

1. **One build, one artifact.** The page and the packages are produced by the
   same job. If the page lived elsewhere, the site would have to fetch a
   Release and could show a description for a package that has since been
   replaced.
2. **The data source is here.** `data/index.json` is a generated artifact of
   this repo's pipeline. Copying it to another repo to render it adds a copy
   step and a way for the two to disagree.
3. **The publish loop is here.** Adding an agent is a pull request to this
   repo; the page updates when the release does. The marketing site stays
   about the product.

The site is deliberately plain: one HTML file, one CSS file, one JS file, no
build step, no npm dependency. It is a directory of files that any static
host can serve.

## How the page loads its data

**The first paint is sized by the page, never by the registry.** `index.html`
embeds no copy of the data, and the browser does not fetch the full index.
`tools/build-site.py` writes three tiers instead:

| File | Size | When it is fetched |
|---|---|---|
| `data/meta.json` | ~1 KiB | every page load, uncached — it declares the build id |
| `data/pages/p###.json` | 48 cards each, ~10 KiB | page 0 on first paint, later pages as the user scrolls |
| `data/details/<agent_id>.json` | one full entry | when that card is opened |

The split is by **field weight against the access pattern**, not by category.
A card carries everything needed to draw it *and to search it* (identity,
description, labels, category, skill and capability **names**, permissions,
size, channel status) — about 214 B gzipped. The heavy fields (capability
descriptions, package sha256, signer fingerprint, source links) live only in
the detail file.

Category sharding would not work here: search is client-side, so a query has
to see every record. Splitting by category would force fetching every shard
anyway, while making the common case (browse page 0) harder.

Two rules follow from this and are enforced by `tools/check-site.py`:

- searching may load every shard (it needs every card), but the **DOM** is
  windowed at 96 cards, so a query matching thousands of agents cannot freeze
  the tab;
- opening one agent's detail must fetch that agent's file and nothing else.

The shipped registry is one shard, so the assertions above are run against a
**synthetic 130-agent registry** built by the same `build-site.py`. A paging
test that never has two pages proves nothing.

`data/index.json` is still deployed: it is the machine endpoint for CLI
consumers and for anyone who wants the whole registry in one document. The
browser never reads it, and `check-site.py` fails if it does.

## What `publish.yml` does, step by step

1. Validates every agent (`tools/validate.py --strict`) — the same check a
   contributor runs locally.
2. Restores the registry signing key from the `ACOWORK_AGENTS_SIGNING_KEY`
   secret.
3. Computes the registry digest and compares it with the published baseline
   (`tools/registry-digest.py --compare`) — unchanged content stops here.
4. Packs and signs only the agents whose content changed
   (`tools/ci-pack-all.py --changed --reuse`), then verifies **every** package,
   reused or new. A signing bug must fail the release, not ship.
5. Runs the tamper-detection self test (`tools/selftest.py`) — proves the
   signature actually detects an altered package.
6. Builds `index.json` (`tools/build-index.py`) and validates it against
   `schema/agent.schema.json`.
7. **Runs the main repo's own manifest parser over every package.** The hub's
   schema is a *mirror* of the main repo's `AgentManifest`, and a mirror can
   drift. This step is what stops the hub from publishing a package the app
   would refuse to install.
8. Builds the site (`tools/build-site.py`) and **renders it in a headless
   browser** (`tools/check-site.py`), including a synthetic 130-agent registry
   to prove the shards page correctly. A 200 on `index.html` proves nothing —
   the page is drawn by JavaScript from the shard set.
9. Updates the rolling `latest` release in place: uploads the current packages,
   index, checksums and digest, then deletes assets this build no longer
   produces. Skipped entirely when the content gate says nothing changed.
10. Deploys `dist/site` to Cloudflare Pages — skipped when the release already
    records this commit as deployed.

Steps 7 and 8 are the interesting ones. Both exist because the obvious
check ("the file is there, the server returns 200") does not actually test
the thing that matters: that the package installs, and that the page shows
the packages.

## The registry key

- The Ed25519 seed is a repository secret (`ACOWORK_AGENTS_SIGNING_KEY`), a
  raw 32-byte file.
- The public key and its fingerprint are **published** in
  [`registry/trusted-keys.json`](../registry/trusted-keys.json), so anyone
  can check whether a package came through the official channel.
- A published package's signature proves two things: *these exact bytes were
  signed by the official channel key* and *they have not been altered since*.
  It does not prove the publisher is a real person, and it says nothing about
  the agent's quality. See [`trust-model.md`](trust-model.md).

## Secrets the pipeline needs

| Secret | Used for | Required |
|---|---|---|
| `ACOWORK_AGENTS_SIGNING_KEY` | signing the packages (base64 of the raw 32-byte seed) | yes |
| `CLOUDFLARE_API_TOKEN` | deploying the site | yes |
| `CLOUDFLARE_ACCOUNT_ID` | deploying the site | yes |

The Cloudflare Pages project is `acowork-agent-hub`; its custom domain is
`hub.acowork.ai` (set in `site/CNAME`).

To fill in `ACOWORK_AGENTS_SIGNING_KEY`, base64 the seed that generated the
fingerprint in [`registry/trusted-keys.json`](../registry/trusted-keys.json)
— the key lives in the gitignored `.keys/registry.key` on the machine that
created it:

```sh
python -c "import base64,sys; sys.stdout.write(base64.b64encode(open('.keys/registry.key','rb').read()).decode())"
```

Paste the output as the secret value. If the seed and `trusted-keys.json`
disagree, publishing "succeeds" but every package is reported as signed by
an unknown key — check the fingerprint before you blame the verifier:

```sh
python tools/check-key.py        # seed -> fingerprint -> trusted-keys.json
```

If the Cloudflare secrets are missing, the deploy step fails loudly rather
than silently leaving the site on a stale build.

## The website's role

The main site (`acowork.ai`) keeps its own `agents/` page for the platform
agents that ship with ACowork. That page links to `hub.acowork.ai`; the hub
page links back. Neither site fetches the other's data — the platform page
is generated from the main repo's `examples/`, the hub page from this
repo's `agents/`.

The two registries are joined by the agent id, not by a merged index: the
platform agents re-published here keep their original `com.acowork.*` ids,
so an installed package stays upgradeable instead of forking into a second,
differently-named agent.

## Reproducing a release locally

```bash
# a throwaway key — never published, and NOT in trusted-keys.json
python tools/make-ci-key.py --output-dir .keys/ci --ephemeral

python tools/validate.py --check-template --strict
python tools/ci-pack-all.py --out dist --key .keys/ci/registry.key
python tools/selftest.py --key .keys/ci/registry.key
python tools/build-index.py --out dist/index.json --packages dist/
python tools/check-index.py dist/index.json schema/agent.schema.json
python tools/build-site.py --index dist/index.json --packages dist/ --out dist/site
python tools/check-site.py --site dist/site
```

Then serve it:

```bash
cd dist/site && python -m http.server 8000
```

A local run with an ephemeral key marks every package `third-party` in the
index, because the ephemeral key is not in `trusted-keys.json`. That is the
correct result — it is exactly what a package signed by an unknown key means.

## Open questions

- **Third-party licenses.** The hub currently accepts an agent whose
  `hub.json` declares any SPDX expression, including a proprietary one. The
  platform's own agents are Apache-2.0. Whether the hub should require an
  OSI-approved license is undecided.
- **CA governance.** Not implemented. The trigger for revisiting it is an
  agent that ships executable content, a third-party mirror site, or the
  need to distinguish publishers from each other.
- **Key rotation.** `trusted-keys.json` has a `status` field per key and a
  `revoked` value is defined, but nothing automates rotation or the
  revocation of a compromised key.
- **Yanked packages.** A package can be deprecated (kept in the index with
  a notice) or removed (gone from the index). There is no "yanked" state
  that keeps the package installable but marks it as withdrawn.
- **Where the trust anchor belongs.** The trust anchor currently lives in
  this repository. If the repository itself is compromised, the anchor can
  be replaced along with it. Pinning it in `acowork-node` — the thing that
  actually verifies — would be stronger, and is the natural place to
  revisit.
