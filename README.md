# ACoworkAgents

Community agent registry for ACowork. One directory per agent; every binary
artifact (`.agent` package, `index.json`, sha256 digests, signatures) is
produced by CI and is **never** committed to this repository — not on `main`,
not on a PR branch.

> Status: skeleton. `docs/` records the design decisions this repo implements.

## Why this repo exists

The ACowork main repo ships a handful of first-party agents under
`examples/*/manifest.toml`. Those are the upstream source of truth and they are
built and signed by the main repo's release pipeline.

This repo is the **community counterpart**:

| | Main repo (`examples/*`) | This repo (`agents/<owner>/<name>`) |
|---|---|---|
| Authors | ACowork core team | community contributors |
| Directory | flat, one dir per agent | owner-namespaced, one dir per agent |
| Provenance | built in-repo, signed by platform keys | built in CI, signed by the contributor's key |
| Index | `agents.json` for the vendor site | `index.json` for the community registry |

## Layout

```
ACoworkAgents/
├── README.md / CONTRIBUTING.md / CODE_OF_CONDUCT.md / SECURITY.md / LICENSE
├── .github/
│   ├── workflows/{validate.yml,publish.yml,review-gate.yml}
│   ├── ISSUE_TEMPLATE/{agent_request.yml,bug_report.yml}
│   └── PULL_REQUEST_TEMPLATE.md
├── schema/{manifest.schema.json,agent.schema.json,catalog.json}
├── agents/
│   ├── _template/{manifest.toml,README.md,avatar.svg}   # authoring template, not indexed
│   └── <owner>/<name>/{manifest.toml,README.md,skills/,avatar.svg,LICENSE}
├── tools/{validate.py,pack.py,build-index.py,signing.py}
└── docs/{publishing.md,naming.md,signed-attributes-v2.md,catalog.md}
```

## Quick start

```bash
# 1. copy the template
cp -r agents/_template agents/<your-github-handle>/<your-agent-name>

# 2. edit manifest.toml (contract: schema/manifest.schema.json)

# 3. validate locally — this is exactly what CI runs
python tools/validate.py

# 4. build a signed package + the index (needs an Ed25519 key)
python tools/pack.py agents/<owner>/<name> --out build/ --key .keys/dev.key
python tools/build-index.py --out build/index.json --packages build/

# 5. verify what you built
python tools/pack.py --verify build/<agent_id>.agent
```

Python 3.11+ is required (`tomllib`). `jsonschema` and `cryptography` are
needed for full validation and signing; `pip install jsonschema cryptography`.

## How publishing works

1. You open a PR adding `agents/<owner>/<name>/`.
2. `validate.yml` runs schema validation, packaging and a signature smoke test.
3. `review-gate.yml` requires a maintainer approval label before merge.
4. `publish.yml` runs on `main`: it packs every agent, signs it with the
   registry key, regenerates `index.json`, and publishes a Release with the
   packages and the index.

Details: [docs/publishing.md](docs/publishing.md).

## Trust model — read this before you publish

**There is no official badge in this project.** No mechanism here marks an
agent as official, verified or endorsed. `publisher.affiliation` is
self-declared metadata that is signed (so it cannot be edited after the fact)
but is **not** a trust decision, and it must never be rendered as a badge.
See [docs/signed-attributes-v2.md](docs/signed-attributes-v2.md#no-official-badges).

The `agent_id` prefixes `com.acowork.`, `acowork.` and `official.` are
reserved for the platform; the validator rejects community submissions that
use them.

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md) for the submission flow, naming rules,
signature requirements and review criteria.

- [docs/publishing.md](docs/publishing.md) — how a submission becomes a published entry
- [docs/naming.md](docs/naming.md) — directory, id and publisher naming rules
- [docs/catalog.md](docs/catalog.md) — categories, tiers, tags, search and facet rules
- [docs/signed-attributes-v2.md](docs/signed-attributes-v2.md) — the signing contract

## License

Repository: [MIT](LICENSE). Each agent may carry its own license file; see
[docs/publishing.md](docs/publishing.md#licensing).
