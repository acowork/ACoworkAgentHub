# SignedAttributes v2

This is the signing contract for the community registry. It extends the
existing ACowork signing scheme (Ed25519 over per-file SHA-256 digests plus a
JSON attribute blob, `core/acowork-sign` in the main repo) so that the three
things a catalog needs to trust are all inside the signature:

| Layer | What it protects | Why it matters |
|---|---|---|
| **Integrity** | the SHA-256 digest of every file in the package | the package is the bytes the publisher intended to ship |
| **Capability** | category, tier, tags, skills, permissions, capabilities | the retrieval UI can facet on these without trusting an unsigned manifest |
| **Identity** | publisher handle, key fingerprint, affiliation | a publisher cannot swap the identity after the fact |

## Current state (as surveyed in the main repo)

Facts, not assumptions — all verified against the code:

- `SignedAttributes` currently has exactly one field: `signing_time`
  (`core/acowork-sign/src/signing_block.rs`).
- The signature is Ed25519 over `concat(section_name || digest)` for each file
  (sorted by name) followed by the serialized attributes
  (`core/acowork-sign/src/sign.rs::create_signature_data`).
- The certificate carries a bare public key plus an `identity` enum
  (`Developer` / `Platform` / `CaIssued`). There is no subject, no issuer and
  no validity period.
- `identity_type` is self-declared by the signer, so it proves nothing.
- `verify_chain` returns `Ok(true)` for both `Developer` and `Platform`
  (`core/acowork-sign/src/certificate.rs`). No CA is wired up.
- The seven published `.agent` packages already contain a V2 Signing Block
  (magic `RBSign Block 42\0`), but their attributes carry no identity or
  catalog information.

Consequence: today, a package's *contents* are integrity-protected, but every
catalog claim about it comes from an unsigned manifest.

## The v2 attribute object

```json
{
  "version": 2,
  "signing_time": "2026-04-17T12:00:00Z",
  "publisher": {
    "id": "github:nicholas",
    "display_name": "Nicholas",
    "affiliation": "community",
    "self_declared": true,
    "homepage": "https://github.com/nicholas",
    "contact": "…"
  },
  "catalog": {
    "agent_id": "io.github.nicholas.research-agent",
    "version": "0.1.0",
    "category": "research",
    "tier": "core",
    "tags": ["research", "summarization", "reporting"],
    "skills": ["research-synthesis"],
    "role": "Turns a question into a sourced, structured research brief",
    "capabilities": [{"key": "question_decomposition", "description": "…"}],
    "permissions": ["FilesystemRead", "Network"]
  },
  "integrity": {
    "digest_algorithm": "sha256",
    "file_count": 12,
    "content_root": "<sha256 hex>"
  }
}
```

### Field list

| Field | Type | Required | Semantics |
|---|---|---|---|
| `version` | int | yes | Always `2`. Absent means a v1 (legacy) package. |
| `signing_time` | string | yes | RFC 3339 UTC, second precision. Informational only — not a trusted timestamp. |
| `publisher.id` | string | yes | Stable handle, `scheme:value` (e.g. `github:nicholas`). Signed, therefore unmodifiable after signing. |
| `publisher.display_name` | string | yes | Human-readable publisher name. |
| `publisher.affiliation` | string | yes | One of `community`, `acowork-maintained`, `third-party`, `unknown`. **Self-declared.** |
| `publisher.self_declared` | bool | yes | Always `true` in v2. A future `ca-issued` publisher would set `false`. |
| `publisher.homepage` | string | no | URL. |
| `publisher.contact` | string | no | Free-text contact channel. |
| `publisher.key_fingerprint` | string | no | SHA-256 hex of the raw public key. The signer key is the trust anchor; for a registry-signed build this is the registry key. |
| `catalog.agent_id` | string | yes | Must equal the manifest's `agent_id`. |
| `catalog.version` | string | yes | Must equal the manifest's `version`. |
| `catalog.category` | string | yes | Taxonomy category (see `docs/catalog.md`). |
| `catalog.tier` | string | yes | `core` / `extended` / `advanced`. |
| `catalog.tags` | string[] | yes | Sorted, unique, from the controlled vocabulary. |
| `catalog.skills` | string[] | yes | Sorted skill directory names. |
| `catalog.role` | string | yes | Must equal the manifest's `role`. |
| `catalog.capabilities` | object[] | yes | `{key, description}` pairs, sorted by key. |
| `catalog.permissions` | string[] | yes | Sorted permission types. |
| `integrity.digest_algorithm` | string | yes | Always `sha256` today. |
| `integrity.file_count` | int | yes | Number of files covered by the digest list. |
| `integrity.content_root` | string | yes | SHA-256 over the concatenated digests, in digest order. A single value a client can compare without walking the whole list. |

**Cross-field rules** (checked by `tools/pack.py` at pack time, and by
`tools/build-index.py` when the package and the manifest disagree):

- `catalog.*` must equal the corresponding `manifest.toml` field, byte for
  byte. A package whose catalog layer contradicts its manifest is rejected.
- `catalog.tier` must be consistent with `catalog.permissions`: `core` and
  `extended` forbid `Shell` and `Sandbox`; `advanced` is required for them.
- `catalog.tier = core` allows at most 3 skills; `extended` at most 10.

### Canonicalization

The attribute blob is signed as bytes, so the encoding must be deterministic.
v2 uses a canonical JSON form (a subset of RFC 8785 / JCS):

1. UTF-8, no BOM.
2. Object keys sorted by code point.
3. No insignificant whitespace — separators are exactly `,` and `:`.
4. Non-ASCII characters are emitted literally, not as `\uXXXX` escapes.
5. Only `null`, `bool`, `int`, `float`, `str`, `array`, `object` are allowed —
   and **floats are rejected**: a value whose textual form can differ between
   implementations must never appear in signed bytes.
6. Optional fields are expressed by **omitting the key**, never by `null`.

The implementation lives in `tools/signing.py::canonical_json` and is used by
both the packer and the verifier, so pack and verify cannot disagree.

### Signature payload (v2)

The v1 payload is `concat(section_name || digest)… || attrs_json`. v2 adds
domain separation and explicit lengths so a v2 payload can never be confused
with a v1 payload signed by the same key:

```
prefix        24 bytes   "ACoworkSignedAttributesV2\0"
attrs_len     u32 BE
attrs         bytes      canonical_json(attrs)
digest_count  u32 BE
per digest:
    name_len u32 BE
    name     bytes      UTF-8 section name
    digest   32 bytes   SHA-256 of the section content
```

Digests are sorted by section name (byte order), matching the existing Rust
implementation.

The Signing Block V2 binary layout (framing, certificate encoding, digest
encoding) is unchanged from the main repo; only the attribute content and the
payload construction change. `tools/signing.py` encodes and decodes it
byte-compatibly.

## Compatibility and migration

### Reading a v1 package

A package whose attributes have no `version` field is a **v1 package**.
Verifiers must not reject it — it is a validly signed package — but they must
not over-claim what it proves:

| Claim | v1 package | v2 package |
|---|---|---|
| File contents unmodified | ✅ proven | ✅ proven |
| Catalog fields are authentic | ❌ **not proven** (unsigned manifest) | ✅ proven |
| Publisher identity | ❌ **not proven** | ✅ proven (self-declared, but bound to the key) |

The index therefore marks such entries:

```json
"signature": {
  "status": "legacy-v1",
  "catalog_covered": false,
  "identity_covered": false
}
```

The website must render `legacy-v1` entries with an explicit "claims not
covered by signature" note, and must not present their category/tier/publisher
as verified.

### Upgrading a v1 package

There is no in-place upgrade — the signature covers the attributes, so the
attributes must be re-signed. The upgrade path is:

1. Re-derive the catalog layer from the manifest (`tools/pack.py` does this).
2. Sign with the v2 payload.
3. Publish the new package.

In practice this happens automatically: the publish workflow re-packs and
re-signs every agent on each release. The seven first-party packages in the
main repo are re-signed by the main repo's own pipeline when it adopts v2;
this registry does not modify them.

### Policy for new submissions

- New community packages must be `signed-v2`. The publish workflow rejects
  `legacy-v1` and `unsigned` packages.
- `legacy-v1` remains *readable* forever, so old installs keep verifying.
- A v2 package never downgrades: if a verifier cannot understand v2, it must
  say "unsupported attribute version", not "no publisher".

## No official badges

**This project does not provide an official, verified or endorsed label.**
Not as a badge, not as a tier, not as a highlight colour.

Concretely:

- `publisher.affiliation` is **self-declared metadata**. It is signed, so it
  cannot be edited after signing, but signing proves only that the holder of
  a key wrote it. It is not a trust decision and must not be rendered as one.
- `tier` is a **capability** label (`core` / `extended` / `advanced`), derived
  from the declared permissions and skill count. It says what an agent can do,
  not who vouched for it.
- The reserved `agent_id` prefixes (`com.acowork.`, `acowork.`, `official.`)
  are enforced by `tools/validate.py` so that a community agent cannot
  masquerade as a platform agent by naming itself.

The upstream site currently infers "official" from the `com.acowork.` id
prefix. That inference is unsound — anyone can pick that prefix — and this
registry does not reproduce it. The website must stop deriving trust from
the id and instead use the `signature.status` / `signature.*_covered` fields.

## Phase 3 outlook — CA and trust roots

Not implemented here. The plan:

1. **Certificate body.** Replace the bare public key with a real certificate
   carrying subject, issuer, validity period and the publisher's key
   fingerprint. Keep the raw-key form readable for v1 compatibility.
2. **Trust roots.** `verify_chain` currently returns `Ok(true)` unconditionally.
   Phase 3 introduces a trust-root store: a signed list of root certificates
   published by the platform, plus a revocation list. The root store is
   itself distributed with signature and a pinned hash.
3. **CA issuance.** A publisher obtains a CA-issued certificate for its key;
   the resulting package carries `identity = CaIssued` and
   `publisher.self_declared = false`, which is what would allow a *verified*
   publisher state — distinct from, and not a substitute for, an official
   agent badge.
4. **Revocation.** A revoked key must not be able to introduce new packages;
   already-published packages are marked revoked in the index rather than
   silently deleted.

Open questions about governance (who runs the CA, how keys are revoked, what
happens to packages signed by a key that was never registered) are tracked in
[`docs/publishing.md`](docs/publishing.md#open-questions).
