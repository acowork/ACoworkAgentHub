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
       │                        │    pack every agent           │
       │                        │    sign with the channel key  │
       │                        │    build index.json           │
       │                        │    build the site             │
       │                        │    render it in a browser     │
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

## The two outputs of one build

`publish.yml` produces two things from **one** run:

| Output | Where it goes | What it is |
|---|---|---|
| GitHub Release | `github.com/acowork/ACoworkAgentHub/releases` | the machine-readable distribution point: `index.json`, every `.agent` package, `SHA256SUMS` |
| the site | `hub.acowork.ai` (Cloudflare Pages) | the same snapshot, rendered as a page |

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

## What `publish.yml` does, step by step

1. Validates every agent (`tools/validate.py --strict`) — the same check a
   contributor runs locally.
2. Restores the registry signing key from the `ACOWORK_AGENTS_SIGNING_KEY`
   secret.
3. Packs and signs every agent (`tools/ci-pack-all.py`), then verifies each
   package it just produced. A signing bug must fail the release, not ship.
4. Runs the tamper-detection self test (`tools/selftest.py`) — proves the
   signature actually detects an altered package.
5. Builds `index.json` (`tools/build-index.py`) and validates it against
   `schema/agent.schema.json`.
6. **Runs the main repo's own manifest parser over every package.** The hub's
   schema is a *mirror* of the main repo's `AgentManifest`, and a mirror can
   drift. This step is what stops the hub from publishing a package the app
   would refuse to install.
7. Builds the site (`tools/build-site.py`) and **renders it in a headless
   browser** (`tools/check-site.py`). A 200 on `index.html` proves nothing —
   the page is rendered by JavaScript from `index.json`.
8. Publishes the GitHub Release.
9. Deploys `dist/site` to Cloudflare Pages.

Steps 6 and 7 are the interesting ones. Both exist because the obvious
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
