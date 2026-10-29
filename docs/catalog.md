# Catalog taxonomy, search and facets

The retrieval UI needs a stable vocabulary. This document defines it; the
machine-readable source of truth is [`schema/catalog.json`](../schema/catalog.json),
and `schema/manifest.schema.json` mirrors the same enums.
`tools/validate.py` fails if the two drift apart.

## Fields

| Field | Kind | Source | Signed? |
|---|---|---|---|
| `category` | closed enum | manifest | ✅ via the `manifest.toml` digest |
| `tier` | closed enum | manifest | ✅ via the `manifest.toml` digest |
| `tags` | closed vocabulary, ≤ 6 | manifest | ✅ via the `manifest.toml` digest |
| `skills` | derived | `skills/*/SKILL.md` directory names | ✅ via the per-file digest list |
| `capabilities` | derived | `manifest.capabilities` | ✅ via the `manifest.toml` digest |
| `permissions` | derived | `manifest.permissions` | ✅ via the `manifest.toml` digest |

Every one of them lives inside `manifest.toml` (skills are additionally
covered file by file). `manifest.toml` is in the signed digest list, so all
of these fields are inside the signature's coverage without any extra
signing machinery. A publisher cannot advertise `core` while shipping
`Shell` permissions without breaking the signature. See
[`trust-model.md`](trust-model.md) for the details.

## Categories

`category` is the primary navigation axis. One value per agent.

| Key | Label | Covers |
|---|---|---|
| `engineering` | Engineering | Designing, building or reviewing software and infrastructure |
| `product` | Product | Strategy, discovery, roadmaps, PRDs, launch planning |
| `project` | Project | Delivery planning, coordination, estimation, progress tracking |
| `quality` | Quality | Testing, acceptance, review, quality assurance |
| `knowledge` | Knowledge | Documents, notes, wikis, information architecture, retrieval |
| `research` | Research | Investigation, literature review, competitive and market analysis |
| `data` | Data | Data analysis, transformation, pipelines, reporting |
| `writing` | Writing | Drafting, editing, translation, long-form content |
| `devops` | DevOps | CI/CD, deployment, monitoring, incident response |
| `design` | Design | Design systems, visual assets, design review support |
| `support` | Support | Customer support, triage, service operations |
| `education` | Education | Teaching, tutoring, curriculum work |
| `productivity` | Productivity | Personal and team workflow automation |
| `other` | Other | Anything that does not fit the above |

Rules:

- Exactly one category. An agent that spans two domains picks the one that
  matches its **primary job**, and uses `tags` for the second dimension.
- Unknown categories are rejected at build time. The index never guesses.
- `other` is a last resort; a maintainer may ask you to pick a real one.

## Tiers

`tier` is a **capability** tier: how much the agent can do, derived from its
declared permissions and skill count. It is not a trust level and not an
endorsement.

| Tier | Constraint |
|---|---|
| `core` | ≤ 3 skills; no `Shell`, no `Sandbox` |
| `extended` | ≤ 10 skills; no `Shell`, no `Sandbox`; may write inside its workspace |
| `advanced` | no skill limit; may declare `Shell` / `Sandbox` |

`advanced` requires maintainer review. The constraint table is generated from
`schema/catalog.json` so the UI and the validator cannot disagree.

## Tags

`tags` are the secondary facet. Values come from a controlled vocabulary — one
tag, one meaning, so facet counts stay meaningful.

Current vocabulary: `automation`, `code-review`, `code-generation`,
`compliance`, `data-analysis`, `data-pipeline`, `debugging`, `documentation`,
`education`, `evaluation`, `git`, `incident-response`, `integration`,
`knowledge-base`, `language`, `llm`, `migration`, `monitoring`, `notes`,
`planning`, `release`, `reporting`, `research`, `retrieval`, `security`,
`summarization`, `testing`, `translation`, `workflow`.

Rules:

- 0–6 tags, unique, lowercase.
- If a needed tag is missing, propose it in a PR that adds it to
  `schema/catalog.json` **and** `schema/manifest.schema.json`. The validator
  keeps the two in sync — a PR that updates only one is rejected.
- Tags are never a search-ranking hack. If the description already says it,
  a tag that nobody filters on is noise.

## Skills

`skills` is derived, not declared: it is the list of directories under
`skills/` that contain a `SKILL.md`. The index builder and the signer both
compute it from the filesystem, so it cannot drift from the package contents.

A skill is a repeatable workflow: one directory, one `SKILL.md` with YAML
front matter (`name`, `description`) and a procedure. See
`agents/nicholas/research-agent/skills/research-synthesis/SKILL.md` for the
shape.

## Search rules

The website's search is client-side over `index.json`. The rules the client
must implement, so that behaviour is reproducible:

1. **Tokenisation** — the query is lowercased and split on whitespace. No
   stemming, no phrase search, no operators.
2. **Matching** — a token matches if it is a substring of the haystack.
3. **AND semantics** — an entry matches the query only if **every** token
   matches. This is what users expect from a multi-word search.
4. **Haystack** — in this order:
   `agent_id`, `display_name`, `name`, `role`, `description`, `author`,
   `category_label`, `category`, `tier`, then every `capability.key`,
   `capability.description`, `skill` name, and `permission`.
   Publisher display name is included but ranked low (see below).
5. **Field priority for ranking** — when several entries match, sort by:
   1. exact match on `display_name` or `agent_id`,
   2. prefix match on `display_name`,
   3. match on `role` / `category_label` / `skill`,
   4. match in `description` or `capabilities`,
   5. match on `publisher.display_name` only.

   Publisher-name matches rank last deliberately: a publisher search must not
   outrank a functional match.

## Facet rules

The index carries precomputed facet counts so the client does not have to
recompute them:

```json
"facets": {
  "category": [{"key": "engineering", "label": "Engineering", "count": 4}, …],
  "tier":     [{"key": "core", "label": "Core", "count": 5}, …],
  "tag":      [{"key": "research", "count": 2}, …]
}
```

- `category` and `tier` facets list **all** taxonomy values, including those
  with `count: 0`, so the filter UI is stable as the catalogue grows.
- `tag` lists only tags that at least one agent carries.
- Counts are computed over the **full** catalogue, not over the current
  filtered subset. A facet count that changes when you click another facet is
  confusing; the client shows the total, and (optionally) the count within
  the current filter as a secondary number.
- Combining filters is a logical AND: one category AND one tier AND any
  number of tags AND the text query.

## Adding to the taxonomy

1. Edit `schema/catalog.json` (categories, tiers or tags).
2. Edit `schema/manifest.schema.json` to mirror the same enum.
3. Run `python tools/validate.py` — it fails if the two disagree.
4. Open a PR explaining why the new value is needed and how it differs from
   the existing ones.

A new category needs a maintainer decision (it changes the primary
navigation); a new tag is usually accepted if it is clearly distinct.
