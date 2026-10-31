"""Signing and verification primitives for the ACoworkAgents registry.

This module is deliberately thin. It does three things and nothing else:

1. `encode_signing_block` / `decode_signing_block` — Signing Block V2
   framing, byte-compatible with `core/acowork-sign/src/signing_block.rs`
   in the ACowork main repo.
2. `signature_payload` — the exact byte string the Ed25519 signature covers.
   This MUST stay identical to `create_signature_data` in the main repo's
   `core/acowork-sign/src/sign.rs`:

       for each digest (sorted by section name):
           section_name bytes ++ digest bytes (32)
       followed by the canonical JSON of the signed attributes

3. Trust-anchor helpers — resolve a signer fingerprint against
   `registry/trusted-keys.json` to answer "did this package come through
   the official channel?".

Why the payload is NOT versioned
--------------------------------
An earlier draft of this registry proposed "SignedAttributes v2" with a
domain-separation prefix and a three-layer attribute object (identity /
capability / integrity). That design was measured against the real
`acowork-verify` binary from the main repo and rejected:

    payload = v1 layout  ......................... VALID
    payload = v2 domain-separated layout ........ VerificationFailed
    v2 attrs with `tier` flipped core->advanced .. still VALID

The third result is the important one. The main repo's `SignedAttributes`
struct has exactly one field, `signing_time`. Serde silently drops unknown
fields when deserializing, and the verifier re-serializes the *parsed*
struct before checking the signature. Any attribute beyond `signing_time`
is therefore outside the signature's coverage no matter how it is framed.

Consequences, all of which this module implements:

* The signature payload stays byte-identical to the main repo's v1 layout.
  Packages built here install and verify with the stock `acowork-verify`
  and the stock `acowork-node` install path, which hard-requires a valid
  signature outside dev mode.
* Capability metadata (category / tier / tags / skills) does not live in
  the signed attributes. It lives in `manifest.toml`, which is inside the
  package and therefore inside the digest list — so the signature covers
  it anyway, through the digest list, with no format change.
* The signed attributes carry exactly one field, `signing_time`, matching
  the main repo struct. Extra keys would be dropped on re-serialization
  and would make the signature unverifiable, so they are rejected here.

Dependencies: `cryptography` is required only when actually signing or
verifying a package.
"""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Canonical JSON
# ---------------------------------------------------------------------------

#: The only signed-attribute field the main repo understands. See the module
#: docstring: anything else would be dropped by serde and left unsigned.
SIGNED_ATTRS_FIELDS = ("signing_time",)


def canonical_json(obj: Any) -> bytes:
    """Canonical UTF-8 JSON bytes.

    Rules (a JCS/RFC 8785 subset):

    * object keys sorted by code point,
    * no insignificant whitespace,
    * non-ASCII emitted literally as UTF-8,
    * floats rejected — a signed value must not have two textual forms.

    `json.dumps(sort_keys=True)` sorts by code point, which is what we want.
    """
    _reject_unsupported(obj)
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _reject_unsupported(obj: Any) -> None:
    if isinstance(obj, float):
        raise ValueError("floats are not allowed in signed attributes")
    if isinstance(obj, dict):
        for key, value in obj.items():
            if not isinstance(key, str):
                raise ValueError(f"object key must be a string, got {type(key)!r}")
            _reject_unsupported(value)
    elif isinstance(obj, (list, tuple)):
        for value in obj:
            _reject_unsupported(value)


# ---------------------------------------------------------------------------
# Signed attributes
# ---------------------------------------------------------------------------

def build_attrs(*, signing_time: str) -> dict:
    """Build the signed attribute object.

    Deliberately a single field, matching the main repo's `SignedAttributes`
    struct. Adding fields here would silently remove them from the
    signature's coverage, because the main repo re-serializes the parsed
    struct before verifying.
    """
    if not signing_time:
        raise ValueError("signing_time is required")
    attrs = {"signing_time": signing_time}
    extra = set(attrs) - set(SIGNED_ATTRS_FIELDS)
    if extra:
        raise ValueError(f"unsupported signed attributes: {sorted(extra)}")
    return attrs


def signature_payload(digests: list[dict], attrs: dict) -> bytes:
    """Bytes the Ed25519 signature covers.

    Must stay byte-identical to `create_signature_data` in the main repo
    (`core/acowork-sign/src/sign.rs`):

        for each digest, sorted by section name (byte order):
            section_name bytes ++ digest bytes (32)
        then the canonical JSON of the signed attributes.

    Digests must be sorted by section name — the same ordering the Rust
    implementation uses, and the ordering the verifier re-derives.
    """
    attrs_bytes = canonical_json(attrs)
    out = bytearray()
    for item in sorted(digests, key=lambda d: d["section_name"].encode("utf-8")):
        name_bytes = item["section_name"].encode("utf-8")
        digest = bytes.fromhex(item["digest"]) if isinstance(item["digest"], str) else item["digest"]
        if len(digest) != 32:
            raise ValueError("digest must be 32 bytes (sha256)")
        out += name_bytes
        out += digest
    out += attrs_bytes
    return bytes(out)


# ---------------------------------------------------------------------------
# Signing Block V2 (binary, APK-v2 style)
# ---------------------------------------------------------------------------

SIGNING_BLOCK_MAGIC_V2 = b"RBSign Block 42\x00"

EOCD_SIGNATURE = b"PK\x05\x06"
EOCD_MIN_SIZE = 22
EOCD_MAX_COMMENT_SIZE = 65535

IDENTITY_DEVELOPER = 0
IDENTITY_PLATFORM = 1
IDENTITY_CA_ISSUED = 2

DIGEST_ALGO_SHA256 = 0


def encode_signing_block(
    *,
    public_key: bytes,
    signature: bytes,
    digests: list[dict],
    attrs: dict,
    identity: int = IDENTITY_DEVELOPER,
) -> bytes:
    """Serialize a Signing Block V2 (framing + content).

    Byte-compatible with `SigningBlock::to_binary()` in the main repo.
    """
    content = bytearray()
    content += struct.pack(">I", 1)  # signers count
    content += struct.pack(">I", 1)  # certificates count
    content += struct.pack(">I", len(public_key))
    content += public_key
    content += struct.pack(">B", identity)
    content += struct.pack(">B", DIGEST_ALGO_SHA256)
    content += struct.pack(">I", len(digests))
    for item in sorted(digests, key=lambda d: d["section_name"].encode("utf-8")):
        name_bytes = item["section_name"].encode("utf-8")
        digest = bytes.fromhex(item["digest"]) if isinstance(item["digest"], str) else item["digest"]
        content += struct.pack(">H", len(name_bytes))
        content += name_bytes
        content += struct.pack(">H", len(digest))
        content += digest
    content += struct.pack(">I", len(signature))
    content += signature
    attrs_bytes = canonical_json(attrs)
    content += struct.pack(">I", len(attrs_bytes))
    content += attrs_bytes

    out = bytearray()
    out += SIGNING_BLOCK_MAGIC_V2
    out += struct.pack("<Q", len(content))
    out += content
    out += struct.pack("<Q", len(content))
    out += SIGNING_BLOCK_MAGIC_V2
    return bytes(out)


def find_eocd(data: bytes) -> int:
    search_start = max(0, len(data) - (EOCD_MIN_SIZE + EOCD_MAX_COMMENT_SIZE))
    for offset in range(len(data) - EOCD_MIN_SIZE, search_start - 1, -1):
        if offset < 0:
            break
        if data[offset : offset + 4] == EOCD_SIGNATURE:
            return offset
    raise ValueError("EOCD not found")


def insert_block_before_cd(zip_data: bytes, block: bytes) -> bytes:
    """Insert `block` between the local file entries and the central directory.

    Mirrors `zip_utils::insert_block_before_cd` in the main repo: the central
    directory is copied verbatim and only the EOCD's CD offset is adjusted.
    """
    eocd_offset = find_eocd(zip_data)
    cd_offset = struct.unpack("<I", zip_data[eocd_offset + 16 : eocd_offset + 20])[0]
    cd_size = struct.unpack("<I", zip_data[eocd_offset + 12 : eocd_offset + 16])[0]
    if cd_offset + cd_size != eocd_offset:
        raise ValueError("CD size does not match EOCD position")

    delta = len(block)
    out = bytearray()
    out += zip_data[:cd_offset]
    out += block
    out += zip_data[cd_offset:eocd_offset]
    eocd = bytearray(zip_data[eocd_offset:])
    eocd[16:20] = struct.pack("<I", cd_offset + delta)
    out += eocd
    return bytes(out)


def find_signing_block(data: bytes) -> bytes | None:
    """Locate the Signing Block V2 *content* inside a signed package.

    Returns the block content only (the signers data), without the
    `[magic][size_prefix]...[size_suffix][magic]` framing. Use
    `find_signing_block_framed` when you need the bytes to re-insert.
    """
    framed = find_signing_block_framed(data)
    if framed is None:
        return None
    magic = SIGNING_BLOCK_MAGIC_V2
    start = framed.find(magic)
    size = struct.unpack("<Q", framed[start + 16 : start + 24])[0]
    return framed[start + 24 : start + 24 + size]


def find_signing_block_framed(data: bytes) -> bytes | None:
    """Locate the complete Signing Block V2, framing included.

    These are the exact bytes to pass to `insert_block_before_cd` to put a
    block back into a rebuilt package. `find_signing_block` returns only the
    content, so the two are not interchangeable.
    """
    magic = SIGNING_BLOCK_MAGIC_V2
    start = data.find(magic)
    while start != -1:
        if start + 24 <= len(data):
            size = struct.unpack("<Q", data[start + 16 : start + 24])[0]
            end = start + 24 + size + 8 + 16
            if end <= len(data) and data[end - 16 : end] == magic:
                return data[start:end]
        start = data.find(magic, start + 1)
    return None


def decode_signing_block(content: bytes) -> dict:
    """Decode the *content* of a Signing Block V2 into a plain dict."""
    cursor = 0

    def take(n: int) -> bytes:
        nonlocal cursor
        if cursor + n > len(content):
            raise ValueError("unexpected EOF in signing block")
        chunk = content[cursor : cursor + n]
        cursor += n
        return chunk

    signers_count = struct.unpack(">I", take(4))[0]
    signers = []
    for _ in range(signers_count):
        certs_count = struct.unpack(">I", take(4))[0]
        certificates = []
        for _ in range(certs_count):
            cert_len = struct.unpack(">I", take(4))[0]
            cert_data = take(cert_len)
            identity = take(1)[0]
            certificates.append({"data": cert_data, "identity": identity})
        digest_algorithm = take(1)[0]
        digests_count = struct.unpack(">I", take(4))[0]
        digests = []
        for _ in range(digests_count):
            name_len = struct.unpack(">H", take(2))[0]
            name = take(name_len).decode("utf-8")
            digest_len = struct.unpack(">H", take(2))[0]
            digest = take(digest_len)
            digests.append({"section_name": name, "digest": digest})
        signature_len = struct.unpack(">I", take(4))[0]
        signature = take(signature_len)
        attrs_len = struct.unpack(">I", take(4))[0]
        attrs_bytes = take(attrs_len)
        signers.append(
            {
                "certificates": certificates,
                "digest_algorithm": digest_algorithm,
                "digests": digests,
                "signature": signature,
                "signed_attrs_raw": attrs_bytes,
                "signed_attrs": json.loads(attrs_bytes.decode("utf-8")),
            }
        )
    return {"signers": signers}


# ---------------------------------------------------------------------------
# Digest helpers
# ---------------------------------------------------------------------------

def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def public_key_fingerprint(public_key: bytes) -> str:
    """Fingerprint of a raw public key — matches the main repo's format.

    The main repo computes SHA-256 over the raw 32-byte public key and
    hex-encodes it. `acowork-verify` prints the same value.
    """
    return hashlib.sha256(public_key).hexdigest()


def load_ed25519_private_key(path: Path):
    """Load an Ed25519 private key from a raw 32-byte seed file.

    The seed is raw bytes, not text: it may legitimately end in a byte that
    happens to look like whitespace (0x09, 0x0a, 0x0d, 0x20). Stripping the
    file would silently shorten such a key to 31 bytes and turn it into an
    unusable one - and because the bytes are random, that happened to roughly
    one seed in twenty, which made the CI self test fail intermittently.

    Only a trailing newline is removed, and only if the file is longer than
    the seed: that is the one case where a byte was added by a tool rather
    than being part of the key.
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    raw = path.read_bytes()
    if len(raw) == 33 and raw.endswith(b"\n"):
        raw = raw[:-1]
    if len(raw) == 64:
        raw = raw[:32]
    if len(raw) != 32:
        raise ValueError(f"{path}: expected a 32-byte Ed25519 seed, got {len(raw)} bytes")
    return Ed25519PrivateKey.from_private_bytes(raw)


# ---------------------------------------------------------------------------
# Trust anchors — "is this the official channel?"
# ---------------------------------------------------------------------------

#: Repository-relative location of the trusted key set.
TRUSTED_KEYS_PATH = Path("registry/trusted-keys.json")


class TrustError(Exception):
    """Raised when the trusted key set cannot answer a trust question."""


def load_trusted_keys(repo_root: Path) -> dict:
    """Load `registry/trusted-keys.json`.

    The file lists the public keys of the official distribution channel.
    A package counts as "distributed through the official channel" when it
    verifies AND its signer fingerprint appears here.

    This is a distribution-channel marker, not an endorsement of the agent's
    quality. See docs/trust-model.md.
    """
    path = repo_root / TRUSTED_KEYS_PATH
    if not path.is_file():
        raise TrustError(f"{path} not found")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TrustError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data.get("keys"), list):
        raise TrustError(f'{path}: expected a "keys" array')
    return data


def resolve_trust(repo_root: Path, fingerprint: str) -> dict:
    """Resolve a signer fingerprint against the trusted key set.

    Returns a dict describing the outcome; never raises for a simple
    "unknown signer" answer, because that is a legitimate result.

    Only keys with `status == "active"` count as trusted. A key listed with
    `status: "revoked"` is reported explicitly so an operator can see *why*
    a package is no longer official-channel, rather than getting a generic
    "unknown signer" answer.
    """
    data = load_trusted_keys(repo_root)
    revoked = None
    for key in data["keys"]:
        if key.get("fingerprint") != fingerprint:
            continue
        status = key.get("status", "active")
        if status == "active":
            return {
                "trusted": True,
                "key_id": key.get("key_id"),
                "fingerprint": fingerprint,
                "label": key.get("label"),
                "channel": data.get("channel"),
            }
        revoked = {
            "trusted": False,
            "key_id": key.get("key_id"),
            "fingerprint": fingerprint,
            "label": key.get("label"),
            "channel": None,
            "status": status,
            "reason": f"key {key.get('key_id')!r} is {status}",
        }
    if revoked is not None:
        return revoked
    return {
        "trusted": False,
        "key_id": None,
        "fingerprint": fingerprint,
        "label": None,
        "channel": None,
        "status": "unknown",
        "reason": "signer fingerprint is not in the trusted key set",
    }
