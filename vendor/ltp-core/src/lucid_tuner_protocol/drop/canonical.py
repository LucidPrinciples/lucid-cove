"""
Canonical JSON — ltp-drop SPEC Section 4.

The canonical form is used for both signing and hashing:
  1. Keys sorted alphabetically at every nesting level.
  2. No whitespace.
  3. Standard JSON escaping; no raw Unicode above U+007E (ensure_ascii).
  4. The `signature` field is EXCLUDED before signing.
  5. When computing prev_drop_hash, the `signature` field IS included.

This implementation must stay byte-identical with the publisher's
(ltp-drop tools/sign-drop.py): json.dumps with sort_keys=True,
separators=(",", ":"), ensure_ascii=True.
"""

import hashlib
import json

# SHA-256 of the UTF-8 string "LTP-GENESIS-DROP" — the prev_drop_hash of
# the first drop in the chain (SPEC Section 6).
GENESIS_HASH = hashlib.sha256(b"LTP-GENESIS-DROP").hexdigest()


def canonical_json(obj: dict) -> str:
    """Serialize to canonical JSON: sorted keys, no whitespace, ASCII-escaped."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def signing_payload(drop: dict) -> bytes:
    """Canonical JSON bytes of the drop with the signature field removed."""
    unsigned = {k: v for k, v in drop.items() if k != "signature"}
    return canonical_json(unsigned).encode("utf-8")


def drop_hash(drop: dict) -> str:
    """SHA-256 of the full canonical JSON, signature INCLUDED.

    This is the value the NEXT drop carries as prev_drop_hash.
    """
    return hashlib.sha256(canonical_json(drop).encode("utf-8")).hexdigest()
