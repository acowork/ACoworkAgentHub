# Agent submission / registry change

## What this PR does

<!-- One or two sentences. Which agent, or which part of the registry. -->

Closes #

## Agent details

| Field | Value |
|---|---|
| Agent directory | `agents/<owner>/<name>/` |
| `agent_id` | |
| Category | |
| Tier (capability) | |
| Tags | |
| License | |

## Checklist

- [ ] `python tools/validate.py` passes locally.
- [ ] `manifest.toml` validates against `schema/manifest.schema.json`.
- [ ] `category` and `tier` come from `schema/catalog.json`; `tags` come from
      the controlled vocabulary.
- [ ] The directory name matches `manifest.name`; `agent_id` is reverse-DNS
      style and does not use a reserved prefix (`com.acowork.`, `acowork.`,
      `official.`).
- [ ] `README.md` explains what the agent does, who it is for and how to use it.
- [ ] Every skill has a `skills/<name>/SKILL.md` with front matter
      (`name`, `description`).
- [ ] The avatar is a square `.png` or `.svg`.
- [ ] Declared permissions are the minimum the agent needs; `Shell` /
      `Sandbox` are only used when `tier = advanced`.
- [ ] **No binaries, `.agent` packages, `index.json`, checksum files,
      signatures or keys are included in this PR.**
- [ ] The description says what the agent actually does (no marketing
      padding).

## Review notes

<!-- Anything a reviewer should look at closely: risky permissions, unusual
     behaviour, a new dependency, a question about the category. -->
