# Docs

A document management agent skilled in collecting, organizing, archiving, writing, reviewing, converting, and maintaining documentation and knowledge bases.

## Install

```bash
acowork install com.acowork.document-manager

Or download the signed `.agent` package from the hub and import it in
the Desktop App. The package is signed by the official channel key —
the app verifies the signature on install.

## What it does

- **archive management** — Archive documents with versioning, retention rules, metadata, provenance, and retrieval paths
- **document collection** — Collect source documents, references, meeting notes, specs, and links with provenance and scope tracking
- **document organization** — Organize documents into clear information architecture, indexes, categories, tags, and navigation
- **document quality review** — Review documents for accuracy, completeness, clarity, structure, consistency, links, and freshness
- **document writing** — Write clear technical, product, process, and knowledge-base documentation for target audiences
- **format conversion** — Convert documentation between Markdown, HTML, plain text, structured templates, and other supported formats
- **knowledge base maintenance** — Maintain knowledge bases by deduplicating, linking, updating, deprecating, and curating content

## Permissions

Read them before you install:

- `FilesystemRead`
- `FilesystemWrite`
- `Network`

## Skills

- `archive-management`
- `document-collection`
- `document-organization`
- `document-quality-review`
- `document-writing`
- `format-conversion`
- `knowledge-base-maintenance`

## Provenance

- **Agent id**: `com.acowork.document-manager`
- **Version**: `1.0.0`
- **Author**: ACowork Team
- **Source**: [`agents/document-manager-agent`](https://github.com/acowork/ACowork/tree/master/agents/document-manager-agent) in the ACowork main repo
- **License**: Apache-2.0

This package is a verbatim copy of the main repo's agent source,
produced by `tools/sync-official.py`; `hub.json` names the commit
it was copied from. The main repo is the source of truth — edit
it there, then re-run the sync. Changes made directly to this
directory are overwritten by the next sync and fail CI before
that happens.
