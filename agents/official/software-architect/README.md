# Architect

A principal-level software architect skilled in requirements analysis, system design, architecture review, module boundaries, interface contracts, scalability, security, and technical planning.

## Install

```bash
acowork install com.acowork.software-architect

Or download the signed `.agent` package from the hub and import it in
the Desktop App. The package is signed by the official channel key —
the app verifies the signature on install.

## What it does

- **architecture risk assessment** — Assess architectural risks, blast radius, reversibility, rollback paths, and operational failure modes
- **architecture strategy** — Create architecture strategies with explicit trade-offs, decision records, and migration paths
- **project participation** — Claim, publish and submit work in a project: accept a dispatched task, publish its deliverable to the project document tree, record blockers and handoffs as activity, and submit the result
- **system design review** — Review system designs for boundaries, coupling, scalability, reliability, security, and operability
- **technical planning** — Convert approved designs into implementation plans with clear module ownership, interfaces, tests, and rollout steps

## Permissions

Read them before you install:

- `FilesystemRead`
- `FilesystemWrite`
- `Network`
- `Shell`

## Skills

- `architecture-risk-assessment`
- `architecture-strategy`
- `project-participation`
- `system-design-review`
- `technical-planning`

## Provenance

- **Agent id**: `com.acowork.software-architect`
- **Version**: `1.1.1`
- **Author**: ACowork Team
- **Source**: [`agents/software-architect-agent`](https://github.com/acowork/ACowork/tree/master/agents/software-architect-agent) in the ACowork main repo
- **License**: Apache-2.0

This package is a verbatim copy of the main repo's agent source,
produced by `tools/sync-official.py`; `hub.json` names the commit
it was copied from. The main repo is the source of truth — edit
it there, then re-run the sync. Changes made directly to this
directory are overwritten by the next sync and fail CI before
that happens.
