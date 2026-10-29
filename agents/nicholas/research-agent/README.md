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
- Read access to the working directory; write access for the output brief.

## Usage

Ask the agent a research question and a time bound. It returns a Markdown
brief plus a machine-readable `sources.json` next to it.

## License

MIT - see [LICENSE](LICENSE).
