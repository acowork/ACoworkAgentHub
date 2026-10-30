# The package contract: manifest.toml vs hub.json

A community agent package has two metadata files, and the split between them
is not a matter of taste — it is forced by how the main repo treats a
manifest.

## The rule

> `manifest.toml` holds **only** fields the main repo knows.
> `hub.json` holds everything that only makes sense inside the hub.

## Why they cannot be one file

The main repo parses a manifest into `acowork_core::AgentManifest` and
re-serialises it with `to_toml()` in several places — most visibly in
`core/acowork-node/src/package/clone.rs`, which rewrites `manifest.toml`
when a user clones an agent.

`AgentManifest` does not use `#[serde(deny_unknown_fields)]`. Serde
therefore **silently drops** any field it does not know. A hub-only field
written into `manifest.toml` would therefore:

1. survive the packaging step, so the hub could read it at index time;
2. vanish the first time a user clones the agent in the app;
3. leave the hub's index describing a package that no longer contains the
   metadata it was derived from.

Putting hub metadata in `manifest.toml` would work right up until it did
not. A separate file has none of these failure modes: the main repo
extracts the whole archive and ignores `hub.json`, and `clone.rs` copies
top-level files verbatim.

`tools/interop-test/main_repo_interop.rs` pins this rule with a test that
rejects a hub-only field appearing in `manifest.toml`.

## What belongs where

### `manifest.toml` — the main repo's contract

A faithful mirror of `acowork_core::AgentManifest`. Every property in
`schema/manifest.schema.json` exists in the main repo with the same name
and type. The fields a community agent actually uses:

| Field | Why it is there |
|---|---|
| `agent_id` | Package identity. Reverse-domain. |
| `version`, `name`, `display_name`, `role` | Identity and display. `role` is required — it is core information, not decoration. |
| `description`, `author` | Attribution. |
| `runtime_version` | Which runtime the package needs. |
| `labels` | Retrieval tags (ADR-104). The hub derives the browse category from these. |
| `avatar` | Package avatar path. |
| `permissions`, `tools` | What the agent may do and which built-in tools it opts into. |
| `mcp_servers` | Recommended MCP servers. A recommendation, not a dependency. |
| `capabilities` | Outward contracts other agents address through intents. `skills` links a capability to the skills that implement it. |
| `llm`, `memory`, `config`, `resources`, `sandbox`, `skills` | Runtime defaults. |

The schema sets `additionalProperties: false`, so a typo or a stray field
fails validation instead of being silently ignored.

### `hub.json` — the hub's contract

Only four things, and each one exists because the hub has a question it
cannot answer otherwise.

| Field | The question it answers |
|---|---|
| `publisher` | **Who published this, and who do we contact?** The hub's account identity. The `id` must match the `agents/<owner>/` directory segment — a submission may only publish under its own account. |
| `license` | **What are the terms?** SPDX expression for the agent's own content. The repository `LICENSE` covers the tooling; this covers the agent. The hub refuses to publish an agent with no license. |
| `repository` | **Where is the source?** The site links to it so a user can read the code before installing, and so an agent can be forked rather than only consumed. |
| `deprecated` | **Is this still recommended?** A notice with a reason and an optional successor. The package stays published and verifiable; the site surfaces the warning. Removing it from the index instead would make it uninstallable and lose the history. |

`publisher.homepage` and the top-level `homepage` round out the link set —
the publisher's own page, and the project's documentation page.

Nothing here is a trust claim. `publisher` is provenance: who to contact,
whose account it is, who to revoke. The hub renders no official or verified
badge — see [trust-model.md](trust-model.md).

## Package layout

```
agents/<owner>/<name>/
├── manifest.toml      # main-repo contract
├── hub.json           # hub contract
├── README.md          # what the agent does, for a human deciding to install it
├── LICENSE            # optional; falls back to the repository MIT
├── prompts/           # system.md and friends
├── skills/<skill>/SKILL.md
└── assets/            # avatar and other package assets
```

`prompts/`, `skills/` and `assets/` are the main repo's package layout.
`skills/<name>/SKILL.md` must exist for every skill directory; a
`capabilities.*.skills` entry must name a skill the package really ships.

## No binaries

An agent package never carries a binary. The agent's whole capability
surface is:

1. **prompt** — `prompts/*.md`
2. **skills** — `skills/<name>/SKILL.md`
3. **built-in tools** — `[[tools]]` opt-ins of runtime-provided tools
4. **MCP** — a server the user installs separately

For (4) the package may only *recommend* an MCP server
(`[[mcp_servers]]`), and the main repo rejects an inline `stdio` form: a
package must not name an executable to run on the user's machine. Binary
capability arrives through a stdio MCP server the user installs and
activates, never inside the package.

`tools/validate.py` rejects forbidden binary extensions in the tree, and
`schema/catalog.json` carries the list.

## The mirror can drift — and that is checked

`schema/manifest.schema.json` is hand-maintained against the main repo's
Rust struct, so it can drift. `tools/interop-test/main_repo_interop.rs` is
the alarm: copied into the main repo as
`core/acowork-core/tests/hub_interop.rs`, it runs the main repo's **real**
parser and validator over every community package. A drifted mirror fails
that test instead of shipping packages the main repo would refuse to install.

```bash
cp tools/interop-test/main_repo_interop.rs \
   <main-repo>/core/acowork-core/tests/hub_interop.rs
cd <main-repo>/core
cargo test -p acowork-core --test hub_interop
```

## What is NOT in the manifest

A short list of things that look like they belong in a package declaration
and deliberately do not:

- **`category`** — the hub derives it from `labels`. See
  [catalog.md](catalog.md).
- **`tier`** — a trust level would be a badge, and this project has none.
  Capability information is already in `permissions`, `tools` and
  `capabilities`.
- **`tags`** — `labels` is the main repo's word for this, and having both
  would guarantee they drift.
- **`publisher` / `license` / `repository`** — hub-only, and they would be
  dropped on clone.
