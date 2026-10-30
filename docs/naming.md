# Naming rules

Short version: **one agent per directory, owner-namespaced, kebab-case
everywhere, and the `agent_id` must be globally unique.**

## Why `agents/<owner>/<name>/`

The obvious layout is `agents/<name>/`. We use `agents/<owner>/<name>/`
instead. Reasons, in order of weight:

1. **Name squatting.** With a flat layout, `agents/code-review/` is taken by
   whoever submitted first, and the second person with a perfectly good
   `code-review` agent has to pick a worse name. Namespacing by owner makes
   collisions structurally impossible — two people can both have a
   `research-agent`.
2. **Discoverability.** `agents/github.com/octocat/…`-style ownership is
   visible in the directory tree, so a reader can tell at a glance who
   maintains what, and can filter the index by owner.
3. **Ownership transfer.** If a maintainer hands an agent to another person,
   the directory moves wholesale and the `agent_id` (which contains the owner
   segment) changes in one visible step.
4. **Collision with the upstream repo.** The main repo uses flat
   `examples/<name>/`. Keeping the community tree namespaced avoids any
   ambiguity between "the platform's `research-agent`" and "a community
   `research-agent`".

The cost — slightly longer paths and a redundant owner segment in the
`agent_id` — is small compared to a registry where the first submitter wins
every good name.

## Directory rules

```
agents/
├── _template/                # authoring template, never indexed
└── <owner>/
    └── <name>/
        ├── manifest.toml     # required
        ├── README.md         # required
        ├── avatar.svg|.png   # recommended
        ├── LICENSE           # optional
        └── skills/
            └── <skill>/SKILL.md
```

- `<owner>` — lowercase kebab-case, 2–39 chars, `[a-z0-9]([a-z0-9-]*[a-z0-9])?`.
  Use your GitHub handle or organisation name, lowercased.
- `<name>` — lowercase kebab-case, 2–49 chars, same character rules.
- The `<name>` segment **must equal** `manifest.name`.
- Directories starting with `_` are ignored by the index builder and by the
  validator's discovery. Only `_template` is reserved.
- One manifest per directory. A directory without a `manifest.toml` is not an
  agent.

## `agent_id` rules

- Globally unique across the whole ACowork ecosystem (main repo + this
  registry).
- Reverse-DNS style, lowercase, 3–64 chars: `[a-z0-9][a-z0-9.-]{2,63}`.
- The convention is `io.github.<owner>.<name>`:

  ```
  io.github.nicholas.research-agent
  io.github.acowork-community.weekly-digest
  ```

- The `agent_id` should contain the owner segment. It prevents one publisher
  from squatting an id that reads as if it belongs to someone else.
- **Reserved prefixes** — `com.acowork.`, `acowork.`, `official.` — are
  rejected by the validator for community submissions. These belong to the
  platform.
  The reason is concrete: the website currently infers "official" from the
  `com.acowork.` prefix, so anyone could claim that namespace. This registry
  does not reproduce that inference, and blocks the namespace from being
  claimed by accident or on purpose.

## Publisher handle rules

`publisher.id` is `scheme:value`:

| Scheme | Example | Meaning |
|---|---|---|
| `github` | `github:nicholas` | GitHub account or organisation |
| `gitlab` | `gitlab:someone` | GitLab account |
| `email` | `email:me@example.com` | an email address |
| `url` | `url:https://example.com/me` | any other URL |

Rules: the scheme is lowercase; the value is 2–64 characters; the value must
not contain whitespace. The handle lives in `hub.json`, not in the manifest,
and the hub requires it to match the `agents/<owner>/` directory segment — a
submission may only publish under its own account.

The handle is a **provenance record**: who to contact, whose account it is,
who to revoke. It is **not verified** — nothing here checks that the account
exists or belongs to the person it names — and it is never rendered as an
official or verified badge. There is deliberately no `affiliation` field: a
self-declared "official" value would be an unofficial official badge.

## Version rules

- Semver (`MAJOR.MINOR.PATCH`), optional pre-release and build metadata.
- `MAJOR` — breaking change to the manifest contract or the agent's
  behaviour.
- `MINOR` — new capability or skill.
- `PATCH` — wording, bug fixes, no behaviour change.
- `manifest.version` is the single source of truth for the package version.
  The index copies it; there is no second version field to keep in sync.

## Reserved words

Do not use these as an agent `name` or `name` segment — the tooling and the
website reserve them:

`index`, `json`, `admin`, `root`, `official`, `acowork`, `template`,
`new`, `edit`, `settings`, `api`, `download`, `downloads`, `avatars`,
`skills`, `schema`, `tools`, `docs`.
