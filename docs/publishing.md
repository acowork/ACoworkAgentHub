# Publishing

How a submission becomes a published, downloadable, signed entry.

## The pipeline

```
PR opened
   │
   ├─ validate.yml ── schema + taxonomy + layout + pack + sign/verify smoke test
   │
   ├─ review-gate.yml ── needs a maintainer `approved` label
   │
   ▼
merged into main
   │
   └─ publish.yml ── pack every agent → sign → build index.json → GitHub Release
```

Everything binary is produced by `publish.yml`. The repository never contains
a package, a checksum, a signature or an index.

## What `publish.yml` does

1. Checks out `main`.
2. Installs Python 3.11 + `jsonschema` + `cryptography`.
3. Runs `python tools/validate.py` — the same check contributors run locally.
4. For each `agents/<owner>/<name>/`:
   - `python tools/pack.py <dir> --out dist/ --key "$REGISTRY_KEY"` — packs and
     signs with the registry key,
   - `python tools/pack.py --verify dist/<agent_id>.agent` — verifies the
     package it just produced (a signing bug must fail the release, not ship).
5. Runs `python tools/build-index.py --out dist/index.json --packages dist/`.
6. Publishes a GitHub Release (`v<date>-<short sha>`) with:
   - `index.json`,
   - every `<agent_id>.agent` package,
   - every `<agent_id>.agent.sha256` checksum file,
   - the per-agent `<agent_id>.attrs.json` signed-attribute blobs.

The release is the single distribution point for the community registry.

## The registry key

- The Ed25519 seed is a repository secret (`ACOWORK_AGENTS_SIGNING_KEY`), a
  raw 32-byte file.
- The corresponding public key is **published** in the repository
  (`registry/registry.pub`, see `registry/README.md`) so anyone can verify
  that a package was signed by the registry key.
- A published package's signature proves: *the registry signed these exact
  bytes, and these are the identity/capability claims that were signed.*
  It does not prove the publisher is a real person. See
  [`signed-attributes-v2.md`](signed-attributes-v2.md).

## How the website consumes this repo

**Recommendation: the website's CI pulls the Release artifacts.** Concretely,
the website workflow downloads the latest `index.json` and the `.agent`
packages from the registry's GitHub Release, verifies each package's sha256
against the published `.sha256` file, and writes them into
`agents/data/agents.json`, `agents/downloads/` and `agents/avatars/`.

Why this and not the alternatives:

- **Release artifacts vs. submodule** — a submodule pins the *source*, but the
  site needs the *artifacts*. The artifacts exist only after CI signs them, so
  the site would have to run the registry's build pipeline itself, duplicating
  the signing key handling in a second repository. A submodule would also put
  binary files into the website repository's history, which this project
  explicitly avoids.
- **Release artifacts vs. running the registry's pipeline in the site CI** —
  same objection, plus it splits the trust story across two repositories.
- **Release artifacts vs. committing `index.json` to the site repo** — the
  index is a build output with a `generated_at` stamp; committing it means
  constant diffs and a merge conflict on every agent update.
- **Release artifacts vs. a live API** — the site is deliberately
  zero-build and zero-dependency; an API would add a runtime dependency and a
  failure mode that a static JSON file does not have.

The field names in `index.json` are deliberately aligned with the main repo's
`dev/common/sync-agent-hub.py` output, so the site can merge the two
catalogues with a simple concatenation rather than a translation layer.

### Merging with the first-party catalogue

The main repo publishes `agents.json` for the platform agents; this repo
publishes `index.json` for community agents. The website merges them:

```js
const all = [...platformAgents.agents, ...communityAgents.agents];
```

The only field that needs care is `tier`: the main repo's `tier` means
"official vs community" (provenance), while this repo's `tier` means
capability level (`core` / `extended` / `advanced`). **They are different
axes and must not share a field name.** See the open questions below.

## Consumption contract for `index.json`

- `schema_version: 2`.
- `agents[]` entries validate against `schema/agent.schema.json`.
- `agents[].package.sha256` is the sha256 of the `.agent` file named by
  `agents[].package.file`, relative to the release's `downloads/` directory.
- `agents[].signature.status` is one of `signed-v2`, `legacy-v1`, `unsigned`.
- `facets` carries precomputed counts; see [`catalog.md`](catalog.md#facet-rules).

## Licensing

- The registry machinery (tooling, schemas, docs) is MIT — see `LICENSE`.
- Each agent may carry its own `LICENSE` file. When present, that license
  governs the agent's own content (prompts, skills, avatar) and nothing else.
- `manifest.license` must carry the SPDX identifier, e.g. `MIT`,
  `Apache-2.0`, `BSD-3-Clause`, `GPL-3.0-only`, `CC0-1.0`.
- Permissive licenses are accepted without extra review. Copyleft and
  source-available licenses are accepted but must be declared in both
  `manifest.license` and the agent's `LICENSE` file.

## Versioning and deprecation

- The registry has no version tags of its own; releases are dated
  (`v2026-04-17-a1b2c3d`).
- An agent is **deprecated** by adding `deprecated = true` to its manifest
  (a schema change, tracked as an open question). Deprecated agents stay in
  the index and are excluded from search results by default.
- An agent is **removed** by deleting its directory in a PR. History keeps the
  record; the `agent_id` is not reissued.

## Open questions

These are unresolved. Items marked **[assumed]** were decided by default in
this round so the pipeline could be built; they are expected to be revisited.

1. **Third-party licenses** **[assumed: accepted, must be declared]** — should
   the registry accept copyleft or source-available agent licenses, or
   restrict agents to permissive licenses only? Current policy accepts them
   with a declaration.
2. **CA governance** **[assumed: out of scope for this round]** — who runs the
   publisher CA, what is the root store's distribution and pinning story, and
   what is the revocation procedure? The v2 attribute format has room for it
   (`publisher.self_declared`, `identity = CaIssued`), but no governance
   model exists yet.
3. **The `tier` field collision** **[assumed: rename on the website side]** —
   the platform catalogue and the community catalogue use `tier` for
   different things. Proposal: rename the platform's to `provenance` and keep
   `tier` as capability level everywhere. Not yet agreed.
4. **Deprecated flag** **[assumed: `deprecated = true` in the manifest]** —
   needs a schema addition and index handling.
5. **Registry key rotation** **[assumed: not yet supported]** — packages
   signed by a rotated-away key should remain verifiable. Requires a keyring
   in the verifier.
6. **Per-agent signature requirement** **[assumed: the registry signs]** —
   today the registry key signs every package, and the publisher's own key
   only appears as an optional `publisher.key_fingerprint`. If contributors
   should sign their own packages instead, the release flow changes
   materially (the registry would need to accept an externally produced
   package).
7. **Rate limits and spam** **[assumed: label-based review only]** — no
   automated abuse controls yet.
8. **Yanked packages** **[assumed: removal only]** — a package that turns out
   to be malicious should be pulled from the index without deleting history;
   the mechanism (a revocation list consumed by the website) is undefined.
