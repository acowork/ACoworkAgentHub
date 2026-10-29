# Security Policy

## Reporting a vulnerability

Please **do not open a public issue** for a security problem.

- For a vulnerability in the registry tooling (`tools/`, `schema/`, CI), open a
  private [security advisory](https://github.com/acowork/ACoworkAgents/security/advisories/new).
- For a vulnerability in an agent published here, open a private advisory too
  and mention the `agent_id` — the maintainers will pull the entry from the
  index while it is investigated.

We aim to acknowledge within 3 business days and to publish a fix or a
mitigation plan within 14 days.

## What we consider a security issue in this repo

- A way to make `tools/validate.py` accept an agent that violates the
  manifest contract (schema bypass, path traversal in the packer, an agent
  that escapes its directory when packed).
- A way to make `tools/pack.py` produce a package whose signature does not
  cover the contents it claims to cover (digest mismatch, attribute
  substitution, canonicalization collision).
- A way to make `tools/build-index.py` publish an entry that was never
  validated, or to inject content into `index.json` from a manifest field
  (the website renders manifest strings, so this is an XSS vector).
- Anything that lets a submission claim a reserved `agent_id` prefix or
  impersonate the platform.

## Trust model — what this repo does *not* do

This registry deliberately does **not**:

- mark any agent as official, verified or endorsed,
- run agent code,
- verify that a publisher is a real person or organisation,
- validate the behaviour of an agent at runtime.

A signature proves that *the bytes* were produced by the holder of a
particular private key, and that the identity/capability/integrity claims
inside the signed attributes are covered by that signature. It proves nothing
about the publisher's real-world identity, and nothing about the agent's
behaviour.

The reserved `agent_id` prefixes (`com.acowork.`, `acowork.`, `official.`)
are enforced by the validator precisely because a name alone must never be
mistaken for endorsement.

## Hardening notes for contributors

- Never commit keys. `.gitignore` already excludes `*.key`, `*.pem`,
  `.keys/`.
- Agents run with the permissions they declare. Keep the set minimal; declare
  `Shell` or `Sandbox` only when the agent genuinely cannot work without it,
  and expect extra scrutiny when you do.
- Do not fetch and execute remote code at runtime; if you need a tool, declare
  it under `[[tools]]`.
