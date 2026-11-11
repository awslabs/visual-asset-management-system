# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Git tags signed with an AWS KMS asymmetric key, in the standard SSH signature format (SSHSIG).

``build.signing.tags: true`` in vra.yaml makes `vra build` create release tags whose signature comes from the same
KMS key that signs the bundle manifest (key spec ECC_NIST_P256, signing algorithm ECDSA_SHA_256). The private key
never leaves KMS. Deploy and promote verify the tag with ``kms:Verify`` and refuse an unsigned or invalid tag.

The tag is a normal git signed tag (``gpg.format=ssh``): anyone can check it offline with
``git -c gpg.ssh.allowedSignersFile=<file> verify-tag <tag>``, using the public key vra writes to
``<work>/signatures/<buildId>.tag-signer.pub`` (allowed-signers line: ``vra-release namespaces="git" <key>``).

Format reference: OpenSSH PROTOCOL.sshsig. No third-party crypto library is needed: KMS signs and verifies; this
module only encodes and decodes the SSH and DER structures.
"""

from __future__ import annotations

import base64
import hashlib
import struct

MAGIC = b"SSHSIG"
NAMESPACE = "git"
HASH_ALG = "sha512"
KEY_TYPE = "ecdsa-sha2-nistp256"
CURVE = "nistp256"
BEGIN = "-----BEGIN SSH SIGNATURE-----"
END = "-----END SSH SIGNATURE-----"
# DER of the AlgorithmIdentifier {id-ecPublicKey, prime256v1}
P256_SPKI_PREFIX = bytes.fromhex("3059301306072a8648ce3d020106082a8648ce3d030107034200")


def _string(b: bytes | str) -> bytes:
    if isinstance(b, str):
        b = b.encode()
    return struct.pack(">I", len(b)) + b


def _mpint(n: int) -> bytes:
    if n == 0:
        return _string(b"")
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    if raw[0] & 0x80:
        raw = b"\x00" + raw
    return _string(raw)


class _Reader:
    def __init__(self, data: bytes):
        self.data, self.pos = data, 0

    def take(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise ValueError("truncated SSH structure")
        out = self.data[self.pos:self.pos + n]
        self.pos += n
        return out

    def string(self) -> bytes:
        (n,) = struct.unpack(">I", self.take(4))
        return self.take(n)

    def uint32(self) -> int:
        return struct.unpack(">I", self.take(4))[0]


def point_from_spki(der: bytes) -> bytes:
    """Uncompressed EC point (65 bytes) from a P-256 SubjectPublicKeyInfo (what kms:GetPublicKey returns)."""
    if not der.startswith(P256_SPKI_PREFIX) or len(der) != len(P256_SPKI_PREFIX) + 65 or der[-65] != 4:
        raise ValueError("signing key must be a KMS ECC_NIST_P256 key (SubjectPublicKeyInfo of P-256)")
    return der[-65:]


def public_key_blob(point: bytes) -> bytes:
    return _string(KEY_TYPE) + _string(CURVE) + _string(point)


def public_key_line(point: bytes, comment: str = "vra-release") -> str:
    return f"{KEY_TYPE} {base64.b64encode(public_key_blob(point)).decode()} {comment}"


def signed_data(message: bytes, namespace: str = NAMESPACE) -> bytes:
    """The blob the key signs (PROTOCOL.sshsig section 'Signed Data')."""
    return MAGIC + _string(namespace) + _string(b"") + _string(HASH_ALG) + _string(hashlib.sha512(message).digest())


def der_to_rs(der: bytes) -> tuple[int, int]:
    """ECDSA-Sig-Value ::= SEQUENCE { r INTEGER, s INTEGER } (what kms:Sign returns for ECDSA)."""
    def length(buf: bytes, i: int) -> tuple[int, int]:
        n = buf[i]
        if n < 0x80:
            return n, i + 1
        k = n & 0x7F
        return int.from_bytes(buf[i + 1:i + 1 + k], "big"), i + 1 + k
    if der[0] != 0x30:
        raise ValueError("not a DER ECDSA signature")
    _, i = length(der, 1)
    vals = []
    for _ in range(2):
        if der[i] != 0x02:
            raise ValueError("not a DER ECDSA signature")
        n, i = length(der, i + 1)
        vals.append(int.from_bytes(der[i:i + n], "big"))
        i += n
    return vals[0], vals[1]


def rs_to_der(r: int, s: int) -> bytes:
    def integer(v: int) -> bytes:
        raw = v.to_bytes(max(1, (v.bit_length() + 7) // 8), "big")
        if raw[0] & 0x80:
            raw = b"\x00" + raw
        return b"\x02" + _len(len(raw)) + raw
    body = integer(r) + integer(s)
    return b"\x30" + _len(len(body)) + body


def _len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(raw)]) + raw


def armor(point: bytes, der_signature: bytes, namespace: str = NAMESPACE) -> str:
    r, s = der_to_rs(der_signature)
    sig_blob = _string(KEY_TYPE) + _string(_mpint(r) + _mpint(s))
    blob = (MAGIC + struct.pack(">I", 1) + _string(public_key_blob(point)) + _string(namespace) + _string(b"")
            + _string(HASH_ALG) + _string(sig_blob))
    b64 = base64.b64encode(blob).decode()
    lines = [b64[i:i + 70] for i in range(0, len(b64), 70)]
    return "\n".join([BEGIN, *lines, END]) + "\n"


def parse_armor(text: str) -> dict:
    """Decode an armored SSH signature: {point, namespace, hashAlg, derSignature}."""
    body = text.strip()
    if not (body.startswith(BEGIN) and body.endswith(END)):
        raise ValueError("not an SSH signature block")
    blob = base64.b64decode("".join(body[len(BEGIN):-len(END)].split()))
    rd = _Reader(blob)
    if rd.take(6) != MAGIC or rd.uint32() != 1:
        raise ValueError("unsupported SSH signature version")
    pk = _Reader(rd.string())
    if pk.string() != KEY_TYPE.encode() or pk.string() != CURVE.encode():
        raise ValueError(f"tag signature key is not {KEY_TYPE}")
    point = pk.string()
    namespace = rd.string().decode()
    rd.string()  # reserved
    hash_alg = rd.string().decode()
    sig = _Reader(rd.string())
    if sig.string() != KEY_TYPE.encode():
        raise ValueError("unexpected signature algorithm")
    inner = _Reader(sig.string())
    r = int.from_bytes(inner.string(), "big")
    s = int.from_bytes(inner.string(), "big")
    return {"point": point, "namespace": namespace, "hashAlg": hash_alg, "derSignature": rs_to_der(r, s)}


def split_signed_tag(raw: str) -> tuple[str, str | None]:
    """Payload and armored signature of a git tag object (``git cat-file tag``)."""
    i = raw.find(BEGIN)
    if i < 0:
        return raw, None
    return raw[:i], raw[i:]


def kms_public_point(kms, key_id: str) -> bytes:
    return point_from_spki(kms.get_public_key(KeyId=key_id)["PublicKey"])


def kms_sign(kms, key_id: str, payload: bytes) -> bytes:
    """DER ECDSA signature of the SSHSIG signed data; KMS hashes it with SHA-256 (MessageType RAW)."""
    data = signed_data(payload)
    return kms.sign(KeyId=key_id, Message=data, MessageType="RAW", SigningAlgorithm="ECDSA_SHA_256")["Signature"]


def kms_verify(kms, key_id: str, payload: bytes, armored: str) -> dict:
    """Verify a tag signature against the KMS key: the embedded key must be the KMS key and kms:Verify must pass."""
    sig = parse_armor(armored)
    if sig["namespace"] != NAMESPACE or sig["hashAlg"] not in ("sha512", "sha256"):
        return {"valid": False, "reason": f"namespace {sig['namespace']!r} / hash {sig['hashAlg']!r}"}
    if sig["point"] != kms_public_point(kms, key_id):
        return {"valid": False, "reason": "the tag was signed by another key"}
    digest = hashlib.sha512(payload).digest() if sig["hashAlg"] == "sha512" else hashlib.sha256(payload).digest()
    data = MAGIC + _string(NAMESPACE) + _string(b"") + _string(sig["hashAlg"]) + _string(digest)
    ok = kms.verify(KeyId=key_id, Message=data, MessageType="RAW", Signature=sig["derSignature"],
                    SigningAlgorithm="ECDSA_SHA_256").get("SignatureValid")
    return {"valid": bool(ok), **({} if ok else {"reason": "kms:Verify rejected the signature"})}
