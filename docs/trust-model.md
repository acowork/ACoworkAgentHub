# Trust model

This document defines what the signature on a `.agent` package means, what it
does not mean, and how a consumer decides whether to trust a package.

It supersedes the earlier `signed-attributes-v2.md` draft. That draft
proposed a versioned attribute format with three layers (identity /
capability / integrity). It was measured against the real `acowork-verify`
binary from the ACowork main repo and **rejected**. The measurements are
recorded below because they are the reason for every decision that follows.

## The two jobs of a signature here

The signature has exactly two responsibilities. It is not a general-purpose
attestation framework, and it is not a trust-scoring system.

### 1. Has the package been tampered with?

Ed25519 over a per-file SHA-256 digest list. A modified package fails
verification. This is the job the main repo's `verify_package` already does,
and it works today.

### 2. Did this package come through the official channel?

ACoworkAgents *is* the official distribution channel for agents. It signs
the packages it publishes. A consumer can therefore ask: is the signer of
this package one of the keys the official channel publishes?

The answer is carried by `registry/trusted-keys.json`, a checked-in list of
public keys and their fingerprints. A package qualifies as official-channel
when:

- its signature verifies, **and**
- its signer's public key fingerprint appears in the trusted key set.

This is a **distribution-channel marker**, not an endorsement. It says
"the official channel produced and published this artifact, and it has not
been altered since". It says nothing about whether the agent is any good.

## Why there is no "official badge"

The main repo's website currently infers officialness from the `agent_id`
prefix (`com.acowork.`). That is trivially forgeable: anyone can publish a
package named `com.acowork.sybil` and the site will treat it as official.

This registry does not introduce an equivalent mechanism, and it does not
add a trust badge of any kind:

- There is **no `tier` field at all**. The earlier design of this repo had
  one, and it was wrong twice over: it was invented rather than derived from
  a data model, and a "tier" is exactly the kind of label that reads like a
  badge. Capability information is already in `permissions`, `tools` and
  `capabilities`, where the main repo puts it.
- `publisher` in `hub.json` is a **provenance record** — who published it,
  whose account it is, who to contact and who to revoke. It is not
  verified, and it is never rendered as an official or verified badge.
- The reserved-prefix list in `schema/catalog.json` (`com.acowork.`,
  `acowork.`, `official.`) is **rejected at validation time**, so a
  community agent cannot impersonate an official id in the first place.
- The only thing the signature establishes is the two facts above. There is
  no "verified publisher" claim, no endorsement level, no quality score.

## Why the signature payload is not versioned

An earlier draft defined a "SignedAttributes v2" payload with a
domain-separation prefix (`ACoworkSignedAttributesV2\0`) and a three-layer
attribute object. Three measurements against the real
`acowork-verify.exe` from the main repo killed it:

| Package | Main repo verifier result |
|---|---|
| Same block layout, v1 payload | `Signature is VALID` |
| Same block layout, v2 domain-separated payload | `VerificationFailed` |
| v2 attributes with `tier` changed `core` → `advanced` | `Signature is VALID` |

The third row is the decisive one. The main repo's `SignedAttributes` struct
has exactly one field, `signing_time`. Serde **silently drops** unknown
fields when deserializing, and the verifier re-serializes the *parsed* struct
before checking the signature. So:

- Any attribute beyond `signing_time` is outside the signature's coverage,
  no matter how it is framed. A "capability layer inside the signature"
  would have been a promise the format could not keep.
- A changed payload layout breaks the stock verifier outright.

Since `acowork-node`'s install path **requires a valid signature** outside
dev mode, a payload the stock verifier rejects is a package that cannot be
installed. A v2 payload was therefore not an option.

## Where capability metadata actually lives

Because extra signed attributes are not protected, capability metadata does
not go in the signed attributes. It goes in `manifest.toml`:

```toml
labels    = ["research", "reporting", "summarization"]
```

`manifest.toml` is inside the package and therefore inside the digest list,
so the signature covers those fields **through the digest list** — with no
format change and no main repo modification. The protection is identical to
what the v2 design was reaching for; the mechanism is simply one that
already works.

The browse category the hub derives from those labels is therefore
traceable to signed bytes as well.

The same reasoning applies to `skills`: they are the `skills/<name>/SKILL.md`
directories, also inside the digest list.

The one thing the digest list cannot express is "the signer is the official
channel" — a package carries its own key, and self-signed packages verify
perfectly. That is what the trusted key set is for.

## The two-part verification

```
package bytes
   │
   ├─ recompute SHA-256 of every file → compare with signed digest list
   │     mismatch, or a file present that the signature does not cover
   │     → TAMPERED
   │
   ├─ Ed25519 verify over (digest list ‖ signing_time)
   │     failure → TAMPERED
   │
   └─ signer fingerprint ∈ registry/trusted-keys.json ?
         yes → OFFICIAL CHANNEL, not tampered
         no  → third-party signed, not tampered
```

`tools/pack.py --verify <package>` implements exactly this and prints the
outcome. Note the third outcome: a third-party-signed package is **not**
tampered. It is simply not from the official channel. The registry publishes
only official-channel packages, so consumers of the published index will
not encounter the third case.

## Key handling and rotation

- The registry's signing key lives in CI secrets only. It is never
  committed.
- `registry/trusted-keys.json` holds public keys and fingerprints only.
- Rotation procedure: add the new key to the trusted set, switch the CI
  secret, keep the old key listed until no published package still uses it,
  then remove it.
- A key listed in the trusted set is a statement about the distribution
  channel, not about the agent. Keys are rotated for operational reasons
  (compromise, expiry), not as a way to express trust in content.

## What this does not give you

Stated plainly, because these are the limits of the mechanism:

- **No publisher identity binding.** The signer is the channel, not the
  contributor. `publisher.id` is a self-declared string. A malicious
  contributor can put any value there; the review process is what keeps it
  honest, not the signature.
- **No endorsement.** Official channel means "the channel published it", not
  "the channel recommends it".
- **No protection against a compromised channel key.** If the signing key
  leaks, an attacker can produce packages that pass both checks. Key
  rotation and CI secret hygiene are the mitigation.
- **No protection of the *repository* itself.** The signature protects the
  published artifact. A compromised repository or a malicious merge can
  still publish a bad agent; review is the control there.

## Open questions

Carried forward from the earlier draft; see also `docs/publishing.md`.

- Do third-party agents need their own signing keys at all, so a consumer
  can tell "published by the channel" from "signed by the publisher"? The
  current model answers only the former.
- Should the trusted key set be distributed out of band (e.g. pinned in
  `acowork-node`) rather than read from this repository, so that a
  compromised repository cannot also replace the trust anchors?
- Should the index carry the signer fingerprint per entry, so a consumer
  that only has `index.json` can tell which channel signed a package
  without downloading it?
