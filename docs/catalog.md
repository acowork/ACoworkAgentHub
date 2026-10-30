# Catalog: how the hub files an agent

The hub needs two browse axes: a **category** (one per agent, primary
navigation) and **labels** (many per agent, cross-cutting search and
filtering).

Neither is declared in a package. Both are derived.

## Where the data comes from

The source of truth is the package's `manifest.toml`, which mirrors the main
repo's `AgentManifest` (ADR-104). The relevant field is:

```toml
labels = ["research", "reporting", "summarization"]
```

`labels` is a main-repo field. ADR-104 states its purpose directly:

> Used for Agent-list filtering, **marketplace attributes** and grouping.
> **Not** involved in permissions, addressing or lifecycle.

Its rules are enforced by the main repo (`is_valid_label`): lowercase kebab,
2–32 characters, at most 12, no duplicates.

The main repo has **no `catalog` concept and does not need one**. The
category is the hub's own grouping, so the mapping lives here, in
`schema/catalog.json`, and nowhere else. Adding a community agent never
requires touching the main repo's manifest format.

## The derivation

```
manifest.toml
  labels = ["research", "reporting", "summarization"]
                    │
                    ▼
   schema/catalog.json  label_categories
     "research"    → "research"
     "reporting"   → "data"
     "summarization" → (unmapped)
                    │
                    ▼
   category = "research"     (first label that maps wins)
```

**Order matters.** The first label that maps to a category wins, because
that is the domain the author led with. The main repo's own examples follow
this convention — `engineering`, `product`, `project-management`,
`quality`, `documents`, `architecture` are all the leading label of their
agent.

Labels that map to nothing contribute nothing. An agent whose labels all
miss the map is filed under `other`.

### Why the first match, and not a vote

A vote (most common mapping wins) sounds fairer and behaves worse: with two
labels mapping to different categories the result depends on how many
*other* agents happen to use each label, so adding an unrelated agent could
recategorise an existing one. First-match is deterministic and explainable —
the hub can always answer "why is this agent under Research?" with "its first
label is `research`".

### Why not derive the category from `role`

The main repo's platform pipeline derives a category from `role`
(`CATEGORY_BY_ROLE` in `dev/common/sync-agent-hub.py`). That works for the
seven platform agents, whose roles are job titles that happen to be
enumerable: "Product Manager", "Project Manager", "Software Architect".

It does not scale to a community registry: `role` is a free-form display
string ("Senior Software Engineer" vs "Staff Engineer" vs "Code Reviewer"
all mean roughly the same thing), and a community author may not think of
their role as a job title at all. `labels` is the field ADR-104 designed for
exactly this job, and it is already validated by the main repo.

`role` is still **required** for community agents — it is core identity
information and the site shows it on every card. It just is not the
category key.

## Categories

`category` is the primary navigation axis. The set is closed and lives in
`schema/catalog.json`.

| Key | Label | Covers |
|---|---|---|
| `engineering` | Engineering | Builds, reviews or debugs software and infrastructure |
| `product` | Product | Strategy, discovery, roadmaps, PRDs, launch planning |
| `project` | Project | Delivery planning, coordination, estimation, tracking |
| `quality` | Quality | Testing, acceptance, review, quality assurance |
| `knowledge` | Knowledge | Documents, notes, wikis, information architecture, retrieval |
| `research` | Research | Investigation, literature review, competitive and market analysis |
| `data` | Data | Data analysis, transformation, pipelines, reporting |
| `writing` | Writing | Drafting, editing, translation, long-form content |
| `devops` | DevOps | CI/CD, deployment, monitoring, incident response |
| `design` | Design | Design systems, visual assets, design review |
| `support` | Support | Customer support, triage, service operations |
| `education` | Education | Teaching, tutoring, curriculum |
| `productivity` | Productivity | Personal and team workflow automation |
| `other` | Other | Anything that maps to no category above |

`other` is always present, so the site never has to handle "no category".

## Labels

`labels` are the author's own words, and they are **not** a closed
vocabulary. The main repo accepts any well-formed kebab slug, and so does
the hub.

The hub ships a `label_categories` map that covers the labels the main
repo's own examples use, plus the obvious neighbours. It is a starting
point, not a gate: a label that is not in the map still works — it simply
does not contribute a category, and the agent falls back to `other`.

When a community agent uses a label the map does not know, the fix is one
line in `schema/catalog.json` — either map the label, or leave it alone if
it is not a category-bearing term.

## Search and facet rules

The site merges the platform index and the community index and searches the
union. The rules:

- **Text search** matches against `agent_id`, `name`, `display_name`,
  `role`, `description`, `author`, `category_label`, every capability key
  and description, every skill name, and every permission name.
- **Multi-word queries are AND.** Every whitespace-separated token must
  appear somewhere in the searchable text.
- **Category filter** is single-select and exact: an agent is in exactly one
  category, so a category filter needs no "all of" semantics.
- **Label filter** is multi-select and OR within itself, AND with the
  category filter: "category=engineering AND (label=testing OR label=security)".
- **Facet counts** are precomputed in `index.json` under `facets`, so the
  site renders counts without walking the list.

## Adding to the taxonomy

- **A new label** that should map to an existing category: add it to
  `label_categories` in `schema/catalog.json`.
- **A new category**: add it to `categories` in `schema/catalog.json` and
  map at least one label to it. A category with no label mapping can never
  be reached, so the validator flags a mapping that points at an unknown
  category.

Both are hub-local changes. The main repo is not involved.

## What the signature covers

The category is derived from `labels`, and `labels` lives in
`manifest.toml`, which is inside the package's signed digest list. So the
category a consumer sees is traceable to bytes the channel signed. It is
still **not** a trust signal — see [trust-model.md](trust-model.md).
