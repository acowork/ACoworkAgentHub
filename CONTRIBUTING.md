# Contributing to ACoworkAgents

Thank you for contributing an agent. This document is the contract: what a
valid submission looks like, how it gets reviewed, and what the maintainers
check before merging.

## The one rule that matters most

**The repository holds sources only.** Never commit:

- `.agent` packages,
- `index.json` or `agents.json`,
- sha256 checksum files,
- signatures or signing blocks,
- private keys, certificates or any `.key` / `.pem` file.

All of those are CI outputs. `tools/validate.py` fails the build if any of
them appear in an agent directory.

## Submission flow

1. **Open an issue** (`.github/ISSUE_TEMPLATE/agent_request.yml`) describing
   the agent you want to publish. This lets us agree on the category and the
   tier before you write the manifest.
2. **Fork and branch** from `main`.
3. **Copy the template**:

   ```bash
   cp -r agents/_template agents/<your-github-handle>/<your-agent-name>
   ```

4. **Write the agent**:
   - `manifest.toml` — the metadata contract (see below),
   - `README.md` — what it does, who it is for, how to use it,
   - `skills/<skill-name>/SKILL.md` — one file per repeatable workflow,
   - `avatar.svg` or `avatar.png` — square, at least 128×128,
   - `LICENSE` — optional; see [Licensing](#licensing).
5. **Validate locally**:

   ```bash
   python tools/validate.py
   ```

   This is byte-for-byte the same check CI runs. If it fails locally it will
   fail in CI.
6. **Open a PR** using the template. Fill in the checklist.
7. **Review**: a maintainer labels the PR `approved` (see
   `.github/workflows/review-gate.yml`). Merging to `main` triggers the
   publish workflow, which packs, signs and indexes every agent.

## Manifest contract

`schema/manifest.schema.json` is authoritative; `schema/catalog.json` holds
the taxonomy. Required fields:

| Field | Meaning |
|---|---|
| `agent_id` | Globally unique, reverse-DNS style. `io.github.<you>.<name>` is the convention. |
| `version` | Semver of the agent. |
| `name` | Kebab-case machine name; must equal the directory name. |
| `display_name` | Human-readable name (≤ 64 chars). |
| `role` | One line: what this agent does. |
| `description` | 20–600 chars: what it does, who it is for, when to reach for it. |
| `author` | Who wrote it. |
| `category` | One of the taxonomy categories (see `docs/catalog.md`). |
| `tier` | `core`, `extended` or `advanced` — a capability tier, **not** a trust level. |
| `tags` | 0–6 tags from the controlled vocabulary. |
| `publisher.id` | Stable handle, e.g. `github:your-handle`. |

Optional but recommended: `avatar`, `license`, `repository`, `homepage`,
`runtime_version`, `permissions`, `tools`, `capabilities`, `llm`, `memory`,
`resources`, `sandbox`.

## Naming rules

Full rules with rationale: [`docs/naming.md`](docs/naming.md). Short version:

- One agent per directory: `agents/<owner>/<name>/`.
- `owner` and `name` are lowercase kebab-case; `name` must match
  `manifest.name`.
- `agent_id` must not use a reserved prefix (`com.acowork.`, `acowork.`,
  `official.`). The validator rejects these.
- `skills/<skill>/SKILL.md` — skill directory names are kebab-case.

## Signature requirements

Every published package must be signed. The signature covers three layers:

1. **Integrity** — the SHA-256 digest of every file in the package.
2. **Capability** — the catalog claims (category, tier, tags, skills,
   permissions, capabilities).
3. **Identity** — the publisher handle, key fingerprint and self-declared
   affiliation.

The full field list, canonicalization rules and the v1 → v2 migration policy
are in [`docs/signed-attributes-v2.md`](docs/signed-attributes-v2.md).

What this means in practice:

- The **publish workflow** signs the packages it builds, using the registry
  key. You do not need to manage a key to be published.
- If you *do* sign locally (e.g. you want to verify your own build), use
  `python tools/pack.py <dir> --out build/ --key <seed>` and attach nothing
  to the PR — keys never leave your machine.
- A package whose signature does not cover the catalog layer is marked
  `legacy-v1` in the index and its publisher/catalog claims are treated as
  unverified. New submissions must be `signed-v2`.

## Review criteria

A maintainer checks, in this order:

1. **Does it validate?** Schema, taxonomy and layout checks pass.
2. **Is the metadata honest?** Does the description say what the agent
   actually does? Is the category right? Is the tier consistent with the
   declared permissions?
3. **Is it safe?** No shell-out or sandbox escape without `tier = advanced`
   and a maintainer sign-off. No credentials, no network exfiltration, no
   obfuscation, no binary blobs.
4. **Is it useful?** Does it do something the existing catalogue does not?
   Duplicates get declined with a note.
5. **Is it documented?** README and SKILL files explain the workflow.

Things that get declined:

- an agent that is a thin wrapper around one shell script with no skill
  description,
- an agent whose declared tier contradicts its permissions,
- anything that resembles credential harvesting or prompt injection aimed at
  the host agent,
- an agent whose `agent_id` or directory squats on the `com.acowork.` space.

## Licensing

The repository is MIT. Each agent may ship its own `LICENSE` file; when it
does, that license applies to the agent's own content, not to the registry
machinery. Permissive licenses (MIT, Apache-2.0, BSD) are accepted without
further discussion. Copyleft and source-available licenses are accepted but
must be declared in `manifest.license` (SPDX id) and in the agent's `LICENSE`
file so users know what they are getting. See the open questions in
[`docs/publishing.md`](docs/publishing.md#open-questions).

## Code of conduct

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).
