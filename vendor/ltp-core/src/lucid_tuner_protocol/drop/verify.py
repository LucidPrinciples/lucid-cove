"""
Drop verification — ltp-drop SPEC Sections 5 and 6.

Signature before trust: never process drop content before verifying the
Ed25519 signature. Chain integrity: each drop carries the SHA-256 of the
previous drop's canonical JSON (signature included).
"""

import base64

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key

from .canonical import GENESIS_HASH, drop_hash, signing_payload


class DropVerificationError(Exception):
    """Signature or chain verification failed."""


def load_public_key(pem_text: str) -> Ed25519PublicKey:
    """Load the publisher's Ed25519 public key from PEM text.

    Comment lines (starting with #) are stripped before parsing — the
    published key file carries a human-readable header.
    """
    pem_lines = [line for line in pem_text.splitlines() if not line.startswith("#")]
    pem_bytes = "\n".join(pem_lines).strip().encode("utf-8")
    key = load_pem_public_key(pem_bytes)
    if not isinstance(key, Ed25519PublicKey):
        raise DropVerificationError("publisher key is not Ed25519")
    return key


def verify_signature(drop: dict, public_key: Ed25519PublicKey) -> None:
    """Verify the drop's Ed25519 signature. Raises DropVerificationError."""
    signature_b64 = drop.get("signature")
    if not signature_b64:
        raise DropVerificationError("drop has no signature field")
    try:
        sig_bytes = base64.b64decode(signature_b64)
    except Exception as e:
        raise DropVerificationError(f"signature is not valid base64: {e}")
    try:
        public_key.verify(sig_bytes, signing_payload(drop))
    except InvalidSignature:
        raise DropVerificationError("Ed25519 signature verification FAILED")


def verify_chain(drop: dict, prev_drop: dict | None) -> None:
    """Verify chain integrity against the previous drop.

    - sequence 1 must carry the genesis hash (SHA-256 of "LTP-GENESIS-DROP")
    - otherwise prev_drop_hash must equal the hash of the previous drop's
      full canonical JSON (signature included), and sequences must be
      consecutive
    Raises DropVerificationError on mismatch.
    """
    seq = drop.get("sequence")
    claimed = drop.get("prev_drop_hash", "")

    if seq == 1:
        if claimed != GENESIS_HASH:
            raise DropVerificationError(
                f"sequence 1 must carry the genesis hash {GENESIS_HASH}, got {claimed}"
            )
        return

    if prev_drop is None:
        return  # nothing to check against — caller has no archive

    prev_seq = prev_drop.get("sequence")
    if prev_seq != seq - 1:
        raise DropVerificationError(
            f"sequence gap: drop {seq} follows cached drop {prev_seq}"
        )
    actual = drop_hash(prev_drop)
    if claimed != actual:
        raise DropVerificationError(
            f"chain mismatch: drop {seq} claims prev hash {claimed}, "
            f"previous drop hashes to {actual}"
        )
