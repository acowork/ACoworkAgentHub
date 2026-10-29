# registry/

Trust anchors for the official distribution channel.

## trusted-keys.json

The list of public keys whose signatures mark a package as **distributed
through the official channel**. A consumer checks a package's signer
fingerprint against this list.

The file holds **public keys and fingerprints only**. The private signing
key never enters this repository — it lives in a CI secret.

### Fields

```json
{
  "schema_version": 1,
  "channel": "acowork-official",
  "keys": [
    {
      "key_id": "acowork-official-2025-10",
      "fingerprint": "<sha256 of the raw 32-byte public key, hex>",
      "public_key": "<raw 32-byte public key, hex>",
      "label": "ACowork official channel, key generated 2025-10",
      "status": "active"
    }
  ]
}
```

`fingerprint` is SHA-256 over the raw 32-byte public key, hex-encoded. This
matches the format the main repo's `acowork-verify` prints, so the value can
be compared directly against its output.

`status` is either `active` (counts as the official channel) or `revoked`
(does not count; kept for the record so it is clear *why* a package is no
longer official-channel).

### Bootstrap

To add a key:

```bash
# 1. Generate the keypair. The private key goes straight into a CI secret;
#    only the public key is committed.
python tools/make-ci-key.py --output-dir .keys
# -> .keys/registry.key   (private, NEVER commit)
# -> .keys/registry.pub   (public, commit the hex form)

# 2. Add the public key to registry/trusted-keys.json:
#    key_id     — a stable name for this key, e.g. acowork-official-2025-10
#    fingerprint— sha256 of the raw public key, hex
#    public_key — the raw public key, hex
#    status     — "active"

# 3. Store .keys/registry.key as the CI secret used by the publish workflow.
```

`tools/make-ci-key.py` prints the fingerprint so it can be pasted in
directly.

### Rotation

1. Generate the new key and add it to this file with `status: "active"`.
2. Switch the CI secret to the new key and publish a release.
3. Set the old key's `status` to `"revoked"` (keep it listed until no
   published package still uses it — a revoked key stops counting as the
   official channel but stays on the record).
4. Remove the old key once nothing depends on it.

### What a key means

A key in this list means: *packages signed by this key were published through
the official channel and have not been altered since.*

It does **not** mean the agent is endorsed, reviewed, or safe. It says
nothing about quality. See `docs/trust-model.md`.
