# Research Agent

Turns a research question into a sourced, structured research brief.

## What it does

- **Question decomposition** - splits a broad question into answerable
  sub-questions with explicit scope and time bounds.
- **Evidence gathering** - collects evidence across sources, cross-checks it,
  and records provenance for every claim.
- **Brief composition** - writes a brief with inline citations, a confidence
  level per section, and an explicit list of open questions.

## Skills

| Skill | What it covers |
|---|---|
| `skills/research-synthesis/SKILL.md` | The end-to-end research workflow: scope, gather, cross-check, compose, review |

## Requirements

- Network access for source gathering.
- Read access to the working directory.

## Usage

Ask the agent a research question and a time bound. It returns a Markdown
brief with inline citations, a confidence level per section, and an explicit
list of open questions.

## Files

| Path | What it is |
|---|---|
| `manifest.toml` | Package declaration - a mirror of the main repo's `AgentManifest` |
| `hub.json` | Hub metadata: publisher, license, repository |
| `prompts/system.md` | The system prompt |
| `skills/research-synthesis/SKILL.md` | The research workflow |
| `assets/avatar.svg` | Avatar |

## Labels

`research` · `reporting` · `summarization`

The hub maps labels onto browse categories; the manifest never declares a
category itself.

## License

MIT - see [LICENSE](LICENSE).
