# Registry key material

The registry signs published packages with a single Ed25519 key so that a
client can check "this package came from the registry pipeline" without
trusting the publisher.

| File | Committed? | What it is |
|---|---|---|
| `registry/registry.pub` | yes | The raw 32-byte public key (hex). Anyone can verify a package against it. |
| private seed | **never** | Stored only as the repository secret `ACOWORK_AGENTS_SIGNING_KEY` (base64 of the raw 32-byte seed). |

## Status

`registry/registry.pub` is provisioned together with the secret. Until it
exists, the publish workflow fails loudly:

```
::error::secret ACOWORK_AGENTS_SIGNING_KEY is not configured
```

That is deliberate — an unsigned or unkeyed release is worse than no release.

## Rotation

Rotation is not implemented yet. When it is, the previous public key must
stay published so that already-downloaded packages remain verifiable, and the
verifier needs a keyring rather than a single key. This is tracked as an
open question in [`../docs/publishing.md`](../docs/publishing.md#open-questions).

## What this key does and does not prove

- **Does**: the package bytes were signed by the registry key, and the
  identity / capability / integrity claims inside the signed attributes were
  made by the registry key holder.
- **Does not**: that the publisher is a real person or organisation, that the
  agent behaves well, or that the agent is endorsed by the platform. There is
  no official badge in this project.
