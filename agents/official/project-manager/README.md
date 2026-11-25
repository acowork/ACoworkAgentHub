# PM

A professional project manager skilled in requirements analysis, task decomposition, progress tracking, risk assessment, and team coordination.

## Install

```bash
acowork install com.acowork.project-manager

Or download the signed `.agent` package from the hub and import it in
the Desktop App. The package is signed by the official channel key —
the app verifies the signature on install.

## What it does

- **project setup** — Bootstrap a project: record, document directory, README index, and the member roster
- **requirements analysis** — Analyze and document project requirements into a structured PRD
- **risk assessment** — Identify, evaluate, and create mitigation plans for project risks
- **task decomposition** — Break down project goals into actionable tasks with estimates and dependencies
- **task dispatch** — Assign work to an agent through the PM service: roster, start time, approval gate, and reading dispatch state

## Permissions

Read them before you install:

- `FilesystemRead`
- `FilesystemWrite`
- `Network`

## Skills

- `meeting-notes`
- `progress-tracking`
- `project-setup`
- `requirements-analysis`
- `risk-assessment`
- `sprint-planning`
- `stakeholder-communication`
- `task-decomposition`
- `task-dispatch`

## Provenance

- **Agent id**: `com.acowork.project-manager`
- **Version**: `1.2.5`
- **Author**: ACowork Team
- **Source**: [`agents/project-manager-agent`](https://github.com/acowork/ACowork/tree/master/agents/project-manager-agent) in the ACowork main repo
- **License**: Apache-2.0

This package is a verbatim copy of the main repo's agent source,
produced by `tools/sync-official.py`; `hub.json` names the commit
it was copied from. The main repo is the source of truth — edit
it there, then re-run the sync. Changes made directly to this
directory are overwritten by the next sync and fail CI before
that happens.
