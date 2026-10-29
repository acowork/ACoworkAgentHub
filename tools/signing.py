"""Shared helpers for ACoworkAgents tooling.

Three concerns live here so that validate.py / pack.py / build-index.py agree:

1. `canonical_json`  — the canonical serialization used by SignedAttributes v2.
2. `build_v2_attrs`  — construction of the v2 signed-attribute object.
3. Signing Block V2 encoding / decoding — byte-compatible with
   `core/acowork-sign/src/signing_block.rs` in the ACowork main repo.

Everything here is dependency-light: `cryptography` is required only when
actually signing or verifying a package.
"""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 1. Canonical JSON (SignedAttributes v2)
# ---------------------------------------------------------------------------

def canonical_json(obj: Any) -> bytes:
    r"""Canonical UTF-8 JSON bytes for SignedAttributes v2.

    Rules (mirrors RFC 8785 / JCS for the subset we use):

    * object keys are sorted by code point,
    * no insignificant whitespace (`,` and `:` separators only),
    * non-ASCII characters are emitted literally as UTF-8 (not \uXXXX),
    * only `null`, `bool`, `int`, `float`, `str`, `list`, `dict` are allowed,
      and floats are rejected outright — signed attributes must not carry
      values whose textual form can differ between implementations,
    * `None` must be expressed by omitting the key, never by `null`.

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
# 2. SignedAttributes v2
# ---------------------------------------------------------------------------

ATTRS_VERSION = 2

#: Domain separation prefix for the v2 signature payload. A v2 payload can
#: never be confused with a v1 payload (digests || attrs_json) even if the
#: same key signs both.
V2_PAYLOAD_PREFIX = b"ACoworkSignedAttributesV2\x00"


def build_v2_attrs(
    *,
    signing_time: str,
    publisher: dict,
    catalog: dict,
    integrity: dict,
) -> dict:
    """Assemble a SignedAttributes v2 object.

    The three layers are deliberately separate:

    * `publisher` — identity layer: who claims to have published this.
    * `catalog`   — capability layer: what the agent claims to be, so that a
      catalog UI can trust category / tier / tags / skills without trusting
      the unsigned manifest alone.
    * `integrity` — completeness layer: which digest algorithm and how many
      files the signature covers.
    """
    attrs = {
        "version": ATTRS_VERSION,
        "signing_time": signing_time,
        "publisher": publisher,
        "catalog": catalog,
        "integrity": integrity,
    }
    _reject_unsupported(attrs)
    return attrs


def v2_signature_payload(digests: list[dict], attrs: dict) -> bytes:
    """Bytes that the Ed25519 signature covers for a v2 package.

    Layout (all integers big-endian):

        prefix          24 bytes  "ACoworkSignedAttributesV2\0"
        attrs_len       u32      length of the canonical attribute JSON
        attrs           bytes    canonical_json(attrs)
        digest_count    u32
        per digest:
            name_len   u32      length of the section name in bytes
            name       bytes    UTF-8 section name
            digest     32 bytes SHA-256 of the section content

    Digests must be sorted by section name (byte order) — the same ordering
    the Rust implementation uses.
    """
    attrs_bytes = canonical_json(attrs)
    out = bytearray(V2_PAYLOAD_PREFIX)
    out += struct.pack(">I", len(attrs_bytes))
    out += attrs_bytes
    out += struct.pack(">I", len(digests))
    for item in sorted(digests, key=lambda d: d["section_name"].encode("utf-8")):
        name_bytes = item["section_name"].encode("utf-8")
        digest = bytes.fromhex(item["digest"]) if isinstance(item["digest"], str) else item["digest"]
        if len(digest) != 32:
            raise ValueError("digest must be 32 bytes (sha256)")
        out += struct.pack(">I", len(name_bytes))
        out += name_bytes
        out += digest
    return bytes(out)


# ---------------------------------------------------------------------------
# 3. Signing Block V2 (binary, APK-v2 style)
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
    """Locate the Signing Block V2 payload inside a signed package."""
    magic = SIGNING_BLOCK_MAGIC_V2
    start = data.find(magic)
    while start != -1:
        if start + 24 <= len(data):
            size = struct.unpack("<Q", data[start + 16 : start + 24])[0]
            end = start + 24 + size + 8 + 16
            if end <= len(data) and data[end - 16 : end] == magic:
                return data[start + 24 : start + 24 + size]
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
        sig_len = struct.unpack(">I", take(4))[0]
        signature = take(sig_len)
        attrs_len = struct.unpack(">I", take(4))[0]
        attrs_bytes = take(attrs_len)
        signers.append(
            {
                "certificates": certificates,
                "digest_algorithm": digest_algorithm,
                "digests": digests,
                "signature": signature,
                "signed_attrs": json.loads(attrs_bytes.decode("utf-8")),
                "signed_attrs_raw": attrs_bytes,
            }
        )
    return {"signers": signers}


# ---------------------------------------------------------------------------
# 4. Digest helpers
# ---------------------------------------------------------------------------

def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def public_key_fingerprint(public_key: bytes) -> str:
    """Fingerprint of a raw public key — matches the main repo's format."""
    return hashlib.sha256(public_key).hexdigest()


def load_ed25519_private_key(path: Path):
    """Load an Ed25519 private key from a raw 32-byte seed file."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    raw = path.read_bytes().strip()
    if len(raw) == 64:
        raw = raw[:32]
    if len(raw) != 32:
        raise ValueError(f"{path}: expected a 32-byte Ed25519 seed, got {len(raw)} bytes")
    return Ed25519PrivateKey.from_private_bytes(raw)
