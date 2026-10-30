# Deploying the Agent Hub

Two environments, two jobs. Locally you reproduce the pipeline to prove a
change is correct; in the cloud `publish.yml` runs the same pipeline and the
content gate decides whether anything is published at all.

There is no manual deploy in the normal case. If you find yourself deploying by
hand, something in this document is wrong or the pipeline is broken.

---

## 1. Local

### Prerequisites

| Tool | Needed for | Check |
|---|---|---|
| Python 3.11+ | everything (`tomllib` requires 3.11) | `python --version` |
| `jsonschema`, `cryptography` | validation, packing, signing | `pip install jsonschema cryptography` |
| Playwright + Chromium | `tools/check-site.py` renders the page for real | `pip install playwright && python -m playwright install --with-deps chromium` |
| Rust + a main-repo checkout | the interop guard only | see §1.5 |

### 1.1 A signing key

Local builds never need the real channel key. Make a throwaway one:

```sh
python tools/make-ci-key.py --output-dir .keys/ci --ephemeral
```

`.keys/` is gitignored; an ephemeral key signs packages that verify as
`third-party` rather than `official-channel`, which is correct — the official
fingerprint lives in `registry/trusted-keys.json` and only the release
pipeline holds that seed.

To build with the **real** channel key locally (only to reproduce a publish
exactly), point at `.keys/registry.key` instead and confirm it is the trusted
key:

```sh
python tools/check-key.py          # seed -> fingerprint -> trusted-keys.json
```

### 1.2 The full chain, in pipeline order

```sh
python tools/validate.py --check-template --strict     # every manifest
python tools/ci-pack-all.py --out dist --key .keys/ci/registry.key
python tools/selftest.py --key .keys/ci/registry.key   # tampering is detected
python tools/selftest-publish.py                       # incremental packing + the gate
python tools/build-index.py --out dist/index.json --packages dist/
python tools/check-index.py dist/index.json schema/agent.schema.json
python tools/registry-digest.py --out dist/registry.digest.json --packages dist --key .keys/ci/registry.key
python tools/build-site.py --index dist/index.json --packages dist --out dist/site --registry-digest dist/registry.digest.json
python tools/check-site.py --site dist/site --fixture-count 130
```

`check-site.py` is the one that matters for the page: it opens the site in a
real browser, and it builds a **synthetic 130-agent registry** with the same
builder to prove the shards page correctly. The shipped registry is a single
shard, so without the fixture every paging assertion would be vacuous.

`selftest-publish.py` is the one that matters for the pipeline: it copies the
repo to a temp directory, makes two throwaway keys, and proves the incremental
rules end to end — cold build, unchanged rebuild reusing everything, a changed
list that lies, a key rotation, a builder-only change, and line endings. It
never touches your working tree, which is the point: the earlier version of
those checks edited `agents/**` in place and restored it with `git checkout`.

### 1.3 View it locally

```sh
python tools/serve-site.py --site dist/site --port 8080
```

Then open <http://127.0.0.1:8080>. `file://` cannot work: the page loads its
data as shards over HTTP and deliberately embeds no copy of the registry.

Use this server instead of `python -m http.server`, because it reads the same
`_headers` file Cloudflare Pages reads and applies it to every response,
printing the resolved policy per request:

```
  GET /data/meta.json          -> 200 | Cache-Control: no-cache
  GET /data/pages/p000.json    -> 200 | Cache-Control: public, max-age=31536000, immutable
  GET /downloads/x.agent       -> 200 | Cache-Control: no-cache
```

That is the part of the deployment a plain static server cannot show, and it is
where a stale-cache bug hides. To see the whole table without serving anything:

```sh
python tools/serve-site.py --site dist/site --print-headers
```

The emulation is approximate — Pages matches against the full hostname and can
also see request headers — but the semantics that matter are reproduced: a
request matching several patterns inherits **all** their headers, and a header
applied twice is joined with a comma (the server warns when that happens, and
`tools/build-site.py` fails the build on it).

### 1.4 Exercise the content gate

The gate is the rule "no content change, no publish". Test it without a
release:

```sh
# unchanged tree -> exit 2, "publish NOT NEEDED"
python tools/registry-digest.py --compare dist/registry.digest.json \
    --packages dist --key .keys/ci/registry.key; echo "exit=$?"

# touch one agent -> exit 0 and it names exactly that agent
printf '\n' >> agents/official/product-manager/prompts/system.md
python tools/registry-digest.py --compare dist/registry.digest.json \
    --packages dist --key .keys/ci/registry.key --changed-out /tmp/changed.txt
git checkout agents/official/product-manager/prompts/system.md
```

And that reuse is decided by content, not by the changed list — an empty list
claiming "nothing changed" must still repack a modified agent:

```sh
: > /tmp/none.txt
printf '\n' >> agents/official/product-manager/prompts/system.md
python tools/ci-pack-all.py --out /tmp/reuse --key .keys/ci/registry.key \
    --reuse dist --changed /tmp/none.txt | tail -1
# -> "1 packed, N-1 reused" (the modified agent is repacked anyway)
git checkout agents/official/product-manager/prompts/system.md
```

And that a **key rotation is never treated as "unchanged"**: content matching
alone would reuse a package signed by the old key, so every agent would read as
"unknown signer" under a rotated trust anchor. Point `--key` at a different key
and reuse must refuse:

```sh
python tools/make-ci-key.py --output-dir /tmp/key2 --ephemeral
python tools/ci-pack-all.py --out /tmp/rot --key /tmp/key2/registry.key \
    --reuse dist --changed /tmp/none.txt | tail -1
# -> "N packed, 0 reused, N re-signed for a new key"
```

All three of these cases — plus the builder-only change and the line-ending
rule — are asserted permanently by `python tools/selftest-publish.py`, which
runs them against a copy of the repo instead of your working tree.

### 1.5 Build identity

Every immutable URL in the site is versioned by one value, `build_id`, written
into `data/meta.json` and stamped into `index.html`. It is the hinge of the
whole caching design, so `check-site.py` pins its three properties:

| Change | build_id | Why |
|---|---|---|
| `generated_at`, `source.commit` | **unchanged** | provenance is not content; a docs-only commit must not make every returning visitor re-download every shard, script and stylesheet |
| an agent's registry entry | changed | the data the page renders moved |
| `hub.js`, `styles.css`, `_headers` | changed | these are cached for a year by URL; an id that ignored them would keep visitors running an old script against new data |

Rebuild and confirm the id is stable while nothing real changed:

```sh
python tools/build-site.py --index dist/index.json --packages dist --out .ci/rebuild | grep -o "build [0-9a-f]*"
python tools/build-site.py --index dist/index.json --packages dist --out .ci/rebuild | grep -o "build [0-9a-f]*"
```

Two different ids from two identical builds means the id picked up provenance
again, and every deploy is now a cache-bust.

### 1.6 Interop against the main repo

The hub's schema is a *mirror* of the main repo's `AgentManifest`; only the
real parser can say whether a package would install.

```sh
git clone --depth 1 https://github.com/acowork/ACowork ../main-repo
cp tools/interop-test/main_repo_interop.rs ../main-repo/core/acowork-core/tests/hub_interop.rs
cd ../main-repo/core
ACOWORK_HUB_DIR=$PWD/../../ACoworkAgentHub/agents cargo test -p acowork-core --test hub_interop
```

`ACOWORK_HUB_DIR` is not optional: the guard **fails** rather than skipping when
it cannot find its subject. A guard that passes because it found nothing is
worse than no guard.

---

## 2. Cloud

### 2.1 One-time setup

**a. Cloudflare Pages project.** Create it once; `publish.yml` uploads to it
but does not create it.

```sh
npx --yes wrangler@3 pages project create acowork-agent-hub --production-branch main
```

Or in the dashboard: **Workers & Pages → Create → Pages → Connect to Git**,
then immediately switch to direct upload — see §2.2 for why the hub is *not*
git-integrated.

**b. Custom domain.** *Project → Custom domains → Set up → `hub.acowork.ai`*.
If `acowork.ai` is on Cloudflare DNS, Pages creates the `CNAME` and proxies it;
the repo's `site/CNAME` records the same name so a rebuilt bundle keeps the
binding. If the zone lives elsewhere, add:

```
hub   CNAME   acowork-agent-hub.pages.dev   (proxied / DNS-only per your zone)
```

With Cloudflare proxying on, set the SSL mode to **Full (strict)** — Pages
certificates are origin certificates, and "Flexible" loops.

**c. Repository secrets** (`Settings → Secrets and variables → Actions`):

| Secret | Value |
|---|---|
| `ACOWORK_AGENTS_SIGNING_KEY` | base64 of the raw 32-byte seed whose fingerprint is in `registry/trusted-keys.json` |
| `CLOUDFLARE_API_TOKEN` | token scoped to *Cloudflare Pages: Edit* on the account |
| `CLOUDFLARE_ACCOUNT_ID` | the account id (shown in the Pages URL) |

Produce the key value locally, then paste it:

```sh
python -c "import base64,sys;sys.stdout.write(base64.b64encode(open('.keys/registry.key','rb').read()).decode())"
```

Before pushing, prove locally that this value is the trusted key — a mismatch
publishes packages that read as "signed by an unknown key":

```sh
python tools/check-key.py --from-base64 "<the value above>"
```

`publish.yml` re-runs that check on every release, so a wrong secret fails the
job instead of shipping a registry nobody trusts.

### 2.2 Why the hub is uploaded, not git-integrated

The main marketing site is git-integrated Pages: what you commit is what is
served. The hub cannot be, because its deployable directory is a *build
output* containing signed packages, and Pages' build environment does not have
the signing key. So:

- `publish.yml` builds `dist/site` and uploads it with `wrangler pages deploy`;
- the GitHub repo holds only source — no package, index, checksum or signature;
- the rolling `latest` release carries the same snapshot for machine consumers
  and doubles as the baseline the content gate diffs against.

The consequence to remember: **the site and the release are the same build.**
Deploying one without the other is how a page ends up describing a package
that no longer exists.

### 2.3 Routine publish

Push to `main`. That is the whole procedure. The pipeline then:

1. validates manifests, clones the main repo, checks the official mirror;
2. restores the signing key and checks it against the trust anchor;
3. downloads the current release (baseline digest + packages to reuse);
4. computes `registry_digest = H(source trees, builder, signing key)`;
5. **stops here if the digest is unchanged** — no packing, no signing, no
   upload, and no deploy if the commit is already the deployed one;
6. otherwise packs only changed agents, reusing published packages whose
   signed content matches the source;
7. verifies every package, runs the interop guard, builds the index and the
   sharded site, renders it in a headless browser;
8. updates the `latest` release in place and deletes assets this build no
   longer produces;
9. deploys `dist/site` to Pages and records the deployed commit.

What you read afterwards is the **Report the gate outcome** step in the job
summary:

```
- registry publish: true
- agents packed: 1
- site deploy: true
- registry digest: 2a80cb016e649c31
```

### 2.4 Verify a deploy

Headers are a Cloudflare feature, so they can only be verified in production:

```sh
curl -sI https://hub.acowork.ai/data/meta.json  | grep -i cache-control   # no-cache
curl -sI https://hub.acowork.ai/data/pages/p000.json | grep -i cache-control  # immutable
curl -sI https://hub.acowork.ai/downloads/com.acowork.pm.agent | grep -iE "cache|disposition"
```

Then confirm the page and the registry agree:

```sh
curl -s https://hub.acowork.ai/data/meta.json | python -m json.tool | head -20
# `registry_digest` must equal the digest in the job summary and in the release
```

And that nothing republished when it should not: re-run `publish.yml`
(workflow_dispatch) without changing any file — the gate must report
`registry publish: false` and `site deploy: false`. **If a no-op push publishes
anything, the gate is broken** and that is a bug worth stopping on.

### 2.5 Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| `Restore the registry signing key` fails | secret missing | add `ACOWORK_AGENTS_SIGNING_KEY` (§2.1c) |
| `Check the signing key against the trust anchor` fails | secret and `trusted-keys.json` disagree | set the secret to the listed key, or register the new fingerprint |
| Packages install but read as "unknown signer" | same as above, discovered late | `python tools/check-key.py` |
| `Deploy the site to Cloudflare Pages` fails | Cloudflare secrets missing | add both; the step fails loudly rather than leaving a stale site |
| `Interop check` fails | the main repo's parser rejects a hub package | the mirror drifted or a hub-only field leaked into `manifest.toml` — move it to `hub.json` |
| `Check the official-agent mirror` fails | main repo changed `examples/*` | re-sync: `python tools/sync-official.py --main-repo ../main-repo` |
| Page renders empty in production | shards 404, or `_headers` caching an old `meta.json` | check §2.4 header output |
| A removed agent still shows on the site | stale shard in an old deployment alias | a fresh deploy replaces the bundle; if it persists, the release prune did not run |

### 2.6 Rollback

The site is a snapshot per deploy, so rollback is a redeploy of an earlier
build:

```sh
git checkout <good-sha>
python tools/build-site.py --index dist/index.json --packages dist --out dist/site
npx --yes wrangler@3 pages deploy dist/site --project-name acowork-agent-hub --branch main
```

For the registry itself, prefer **fix-forward**: the rolling release carries one
snapshot, so an "unpublish" means republishing from a commit whose `agents/`
tree is what you want. The signed packages for that commit are reproducible
from source plus the channel key — which is exactly what the content gate
records in `registry.digest.json`.

### 2.7 Reachability note

`hub.acowork.ai` is served by Cloudflare Pages. The `*.pages.dev` alias has no
mainland-China point of presence and cannot be ICP-filed; the custom domain
inherits whatever the zone's proxying gives you. Since this site's purpose is
**downloading packages**, if mainland users matter, that is a product decision
to make deliberately rather than discover — the same caveat the main site's
README records.

---

## 3. What is *not* deployed

Nothing binary is committed. `dist/`, `.ci/`, `.keys/` and every `.agent` file
are gitignored. If you ever find yourself committing a package to make a deploy
work, the pipeline is missing a step and that step is the fix.
