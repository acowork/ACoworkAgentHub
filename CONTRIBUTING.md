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
   the agent you want to publish. This lets us agree on the labels before you
   write the manifest — the hub derives the browse category from them.
2. **Fork and branch** from `main`.
3. **Copy the template**:

   ```bash
   cp -r agents/_template agents/<your-github-handle>/<your-agent-name>
   ```

4. **Write the agent**:
   - `manifest.toml` — the main repo's package contract (see below),
   - `hub.json` — publisher, license, repository (see below),
   - `README.md` — what it does, who it is for, how to use it,
   - `prompts/system.md` — the system prompt,
   - `skills/<skill-name>/SKILL.md` — one file per repeatable workflow,
   - `assets/avatar.svg` — square, at least 128×128,
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

## The two metadata files

A package has **two** metadata files, and the split is not a matter of
taste — it is forced by how the main repo handles a manifest. Full rationale:
[`docs/package-contract.md`](docs/package-contract.md).

| File | Contract | Holds |
|---|---|---|
| `manifest.toml` | `schema/manifest.schema.json` | **Only** fields the main repo knows. A faithful mirror of `acowork_core::AgentManifest`. |
| `hub.json` | `schema/hub.schema.json` | Publisher, license, repository, deprecation notice. |

The main repo re-serialises the parsed manifest when a user clones an agent,
and serde silently drops unknown fields — so a hub-only field in
`manifest.toml` would vanish the first time somebody cloned the agent in the
app. Keep it in `hub.json`.

### `manifest.toml` — required fields

| Field | Meaning |
|---|---|
| `agent_id` | Globally unique, reverse-DNS style. `io.github.<you>.<name>` is the convention. |
| `version` | Semver of the agent. |
| `name` | Kebab-case machine name; must equal the directory name. |
| `display_name` | Human-readable name. |
| `role` | One line: what this agent does. **Required** — it is core identity information. |
| `description` | What it does, who it is for, when to reach for it. |
| `author` | Who wrote it. |
| `runtime_version` | Which runtime the package needs. |

Optional but recommended: `labels`, `avatar`, `identity_deps`,
`permissions`, `tools`, `mcp_servers`, `capabilities`, `llm`, `memory`,
`config`, `resources`, `sandbox`, `skills`.

**`labels`** is how the hub files your agent. It is a main-repo field
(ADR-104): lowercase kebab, 2–32 characters, at most 12. The hub maps your
labels onto a browse category — see [`docs/catalog.md`](docs/catalog.md).
Lead with your domain label, because the first label that maps wins.

Do **not** add `category`, `tier`, `tags`, `publisher`, `license` or
`repository` to the manifest. The schema rejects them.

### `hub.json` — required fields

| Field | Meaning |
|---|---|
| `publisher.id` | Stable handle, e.g. `github:your-handle`. Must match the owner directory. |
| `license` | SPDX expression for your agent's content, e.g. `MIT`. |
| `repository` | Public source URL. |

Optional: `publisher.display_name`, `publisher.homepage`, `homepage`,
`deprecated`.

## Naming rules

Full rules with rationale: [`docs/naming.md`](docs/naming.md). Short version:

- One agent per directory: `agents/<owner>/<name>/`.
- `owner` and `name` are lowercase kebab-case.
- `agent_id` must not use a reserved prefix (`com.acowork.`, `acowork.`,
  `official.`). The validator rejects these.
- `skills/<skill>/SKILL.md` — skill directory names are kebab-case.

`manifest.name` is the agent's short display label (the main repo uses values
like `"Docs"` or `"PM"`), so it does **not** have to match the directory
name. The hub never rewrites it.

## Signature requirements

Every published package must be signed. The signature does exactly two
things:

1. **Integrity** — the SHA-256 digest of every file in the package. Any
   change after signing breaks verification.
2. **Official channel** — the signer's public key fingerprint is listed in
   [`registry/trusted-keys.json`](registry/trusted-keys.json), i.e. the
   package came out of this repository's publish pipeline.

The capability claims (labels, skills, permissions, capabilities) are covered
by the signature too, because they live in `manifest.toml` and
`manifest.toml` is in the signed digest list — so the browse category the
hub derives is traceable to signed bytes. There is no separate "capability
layer" and no identity binding — see
[`docs/trust-model.md`](docs/trust-model.md) for the full contract and the
measurements behind it.

What this means in practice:

- The **publish workflow** signs the packages it builds, using the registry
  key. You do not need to manage a key to be published.
- If you *do* sign locally (e.g. you want to verify your own build), use
  `python tools/pack.py <dir> --out build/ --key <seed>` and attach nothing
  to the PR — keys never leave your machine.
- An unsigned package is rejected by the publish workflow. A package signed
  by a key outside the trusted set is published as `third-party`: valid, not
  tampered with, but not the official channel.

## Review criteria

A maintainer checks, in this order:

1. **Does it validate?** Schema, taxonomy and layout checks pass.
2. **Is the metadata honest?** Does the description say what the agent
   actually does? Do the labels describe what it really does, with the
   domain label first? Does the declared permission set match what the
   agent claims to need?
3. **Is it safe?** Any `Shell` permission, or an MCP server that reaches the
   network, gets a second look. No credentials, no network exfiltration, no
   obfuscation, no binary blobs.
4. **Is it useful?** Does it do something the existing catalogue does not?
   Duplicates get declined with a note.
5. **Is it documented?** README and SKILL files explain the workflow.

Things that get declined:

- an agent that is a thin wrapper around one shell script with no skill
  description,
- an agent whose declared permissions contradict what its skills and
  description say it needs,
- anything that resembles credential harvesting or prompt injection aimed at
  the host agent,
- an agent whose `agent_id` or directory squats on the `com.acowork.` space.

## Licensing

The repository is MIT. Each agent may ship its own `LICENSE` file; when it
does, that license applies to the agent's own content, not to the registry
machinery. Permissive licenses (MIT, Apache-2.0, BSD) are accepted without
further discussion. Copyleft and source-available licenses are accepted but
must be declared as an SPDX expression in `hub.json` (`license`) and in the
agent's `LICENSE` file so users know what they are getting. See the open
questions in [`docs/publishing.md`](docs/publishing.md#open-questions).

## Code of conduct

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).
