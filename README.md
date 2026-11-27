# ACoworkBotHub

Community bot registry for ACowork, and the source of
**hub.acowork.ai**. One directory per bot; every binary artifact
(`.bot` package, `index.json`, sha256 digests, signatures) is
produced by CI and is **never** committed to this repository — not on `main`,
not on a PR branch.

## Why this repo exists

The ACowork main repo ships a handful of first-party bots under
`bots/<name>-bot/`. Those are the upstream source of truth and they are
built and signed by the main repo's release pipeline.

This repo is the **community counterpart**:

| | Main repo (`bots/<name>-bot/`) | This repo (`bots/<owner>/<name>`) |
|---|---|---|
| Authors | ACowork core team | community contributors |
| Directory | flat, one dir per bot | owner-namespaced, one dir per bot |
| Provenance | built in-repo, signed by platform keys | built in CI, signed by the channel key |
| Index | built by the main repo | `index.json` for the community registry |

The platform's own bots are mirrored here under `bots/official/`, keeping
their original `com.acowork.*` ids, so an installed package stays
upgradeable instead of forking into a second, differently-named bot. The
main repo remains their source of truth.

**`bots/official/` is a generated directory.** `tools/sync-official.py`
writes it, byte for byte, from a named commit in the main repo, and records
that commit in each package's `hub.json`. Nothing in it is hand-edited: the
next sync overwrites the edit, and CI fails on it before that happens. A
content change belongs in the main repo; the sync then opens its own PR here.
Two checks ask two different questions:

| Check | Question | Effect |
|---|---|---|
| `sync-official.py --check` | does the mirror still match the commit it claims? | **blocks** — a hand-edit in the generated area |
| `sync-official.py --main-ref origin/master --check --report-only` | has master moved past what we publish? | a notice — it must not block an unrelated hub PR |

## The release loop

```
pull request ─▶ validate + review ─▶ merge to main
                                          │
                                          ▼
                              publish.yml (one build)
                                   │            │
                                   ▼            ▼
                          GitHub Release    hub.acowork.ai
                       (index.json, .bot)    (the page)
```

The Release and the website come from the **same build**, so the page, the
index and the downloadable packages are always the same snapshot. Full walkthrough:
[docs/publishing.md](docs/publishing.md).

## The one rule that shapes everything

**A package's `manifest.toml` mirrors the main repo's `AgentManifest` and
nothing else.** Every field in it exists in the main repo with the same name
and type. Hub-specific metadata lives in `hub.json`, next to it.

The main repo re-serialises the parsed manifest when a user clones a bot,
and serde silently drops fields it does not know — so a hub-only field
written into `manifest.toml` would vanish the first time somebody cloned the
bot in the app. A separate file has no such failure mode.

Full rationale: [docs/package-contract.md](docs/package-contract.md).

## Layout

```
ACoworkBotHub/
├── README.md / CONTRIBUTING.md / CODE_OF_CONDUCT.md / SECURITY.md / LICENSE
├── .github/
│   ├── workflows/{validate.yml,publish.yml,review-gate.yml}
│   ├── ISSUE_TEMPLATE/{agent_request.yml,bug_report.yml}
│   └── PULL_REQUEST_TEMPLATE.md
├── schema/
│   ├── manifest.schema.json   # mirror of the main repo's AgentManifest
│   ├── hub.schema.json        # hub-only metadata
│   ├── agent.schema.json      # one index.json entry
│   └── catalog.json           # hub's categories + the label -> category map
├── bots/
│   ├── _template/                      # authoring template, not indexed
│   ├── official/<name>/                # the platform's own agents
│   └── <owner>/<name>/
│       ├── manifest.toml               # main-repo contract
│       ├── hub.json                    # hub contract
│       ├── README.md / LICENSE
│       ├── prompts/  skills/  assets/
├── site/                       # the hub.acowork.ai page (static, no build step)
│   ├── index.html  hub.js  styles.css
│   ├── _headers  CNAME
│   └── assets/
├── tools/{validate.py,pack.py,build-index.py,build-site.py,signing.py,...}
├── registry/
│   ├── trusted-keys.json        # the official channel's public keys
│   └── official-publishers.json # which ids the platform publishes
└── docs/
```

## Quick start

```bash
# 1. copy the template
cp -r bots/_template bots/<your-handle>/<your-agent-name>

# 2. edit manifest.toml (contract: schema/manifest.schema.json)
#    and hub.json (contract: schema/hub.schema.json)

# 3. validate locally — this is exactly what CI runs
python tools/validate.py

# 4. build a signed package + the index (needs an Ed25519 key)
python tools/pack.py bots/<owner>/<name> --out build/ --key .keys/dev.key
python tools/build-index.py --out build/index.json --packages build/

# 5. verify what you built
python tools/pack.py --verify build/<agent_id>.bot
```

Python 3.11+ is required (`tomllib`). `jsonschema` and `cryptography` are
needed for full validation and signing; `pip install jsonschema cryptography`.

## How a bot is filed

The hub has two browse axes and **neither is declared in a package**:

- **category** — one per bot, the primary navigation. Derived by mapping
  the package's `labels` through `schema/catalog.json`.
- **labels** — many per bot, the cross-cutting filter. The author's own
  words, taken straight from `manifest.toml`.

```toml
labels = ["research", "reporting", "summarization"]
#          │
#          └── first label that maps wins -> category "research"
```

`labels` is a main-repo field (ADR-104), designed for exactly this use. The
category mapping lives only in this repo, because only the hub needs it.
Details: [docs/catalog.md](docs/catalog.md).

## How publishing works

1. You open a PR adding `bots/<owner>/<name>/`.
2. `validate.yml` runs schema validation, packaging, a signature smoke test,
   the main repo's own manifest parser over your package, and a headless
   browser check of the site it produces.
3. `review-gate.yml` requires a maintainer approval label before merge.
4. `publish.yml` runs on `main`: it packs every bot, signs it with the
   channel key, regenerates `index.json`, builds the site, and publishes both
   a GitHub Release (packages + index) and the site at `hub.acowork.ai`.

Details: [docs/publishing.md](docs/publishing.md).

## Trust model — read this before you publish

**There is no official badge in this project.** No mechanism here marks an
bot as official, verified or endorsed, and none is planned. What the
signature establishes is exactly two things: the package was not altered
after signing, and it was signed by the official channel (its signer
fingerprint is in [`registry/trusted-keys.json`](registry/trusted-keys.json)).

`publisher` in `hub.json` is a provenance record — who to contact, whose
account it is — and is never rendered as a badge. See
[docs/trust-model.md](docs/trust-model.md).

The `agent_id` prefixes `com.acowork.`, `acowork.` and `official.` are
reserved for the platform: a community submission using them is rejected. The
platform's own bots are the exception, and they are pinned by id in
[`registry/official-publishers.json`](registry/official-publishers.json) —
claiming the platform identity takes a reviewed change to that file, not just
a directory name.

## No binaries

A bot package never carries a binary. Its whole capability surface is
prompt, skills, built-in tools and MCP — and MCP arrives as a server the
user installs separately, never as an executable inside the package. The
main repo rejects inline stdio MCP declarations for the same reason, and
this repo's validator rejects binary extensions in the tree.

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md) for the submission flow, naming rules,
signature requirements and review criteria.

- [docs/package-contract.md](docs/package-contract.md) — what goes in manifest.toml vs hub.json, and why
- [docs/catalog.md](docs/catalog.md) — categories, labels, search and facet rules
- [docs/publishing.md](docs/publishing.md) — how a submission becomes a published entry
- [docs/deployment.md](docs/deployment.md) — running the pipeline locally, and how the site reaches Cloudflare Pages
- [docs/naming.md](docs/naming.md) — directory, id and publisher naming rules
- [docs/trust-model.md](docs/trust-model.md) — what the signature means and does not mean

## License

Repository: [MIT](LICENSE). Each bot declares its own license in
`hub.json`; see [docs/publishing.md](docs/publishing.md#licensing).
