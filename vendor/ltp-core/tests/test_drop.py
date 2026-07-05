"""Drop verification against the REAL published drop + synthetic chains."""

import copy
import json

import pytest

import lucid_tuner_protocol as ltp
from lucid_tuner_protocol.drop.canonical import signing_payload
from lucid_tuner_protocol.drop.verify import load_public_key


# ── Canonical JSON (SPEC Section 4) ──────────────────────────────────────

def test_canonical_json_rules():
    obj = {"b": 1.0, "a": {"z": "é", "y": [1, 2]}}
    s = ltp.canonical_json(obj)
    assert s == '{"a":{"y":[1,2],"z":"\\u00e9"},"b":1.0}'
    assert " " not in s


def test_genesis_hash_value():
    import hashlib
    assert ltp.GENESIS_HASH == hashlib.sha256(b"LTP-GENESIS-DROP").hexdigest()


# ── Real published drop ──────────────────────────────────────────────────

def test_real_drop_signature_verifies(real_drop, publisher_pem):
    key = load_public_key(publisher_pem)
    ltp.verify_signature(real_drop, key)  # raises on failure


def test_real_drop_carries_genesis_hash(real_drop):
    assert real_drop["sequence"] == 1
    ltp.verify_chain(real_drop, None)  # sequence 1 -> genesis check


def test_real_drop_parses_and_validates(real_drop):
    drop = ltp.Drop.from_dict(real_drop)
    assert drop.frequency_name == "Boundary"
    assert drop.signal_type == "Clear"
    assert drop.tuning_day == 123
    assert len(drop.practice) == 3
    assert "Lucid Principles Canon" in drop.tuning_key_attribution
    ctx = drop.as_context()
    assert drop.tuning_key_text in ctx
    assert drop.context_block in ctx


def test_real_drop_tampered_signature_fails(real_drop, publisher_pem):
    key = load_public_key(publisher_pem)
    tampered = copy.deepcopy(real_drop)
    tampered["context_block"] = "injected content"
    with pytest.raises(ltp.DropVerificationError):
        ltp.verify_signature(tampered, key)


# ── Schema validation (SPEC Sections 2, 11) ──────────────────────────────

def test_rejects_foreign_domain(real_drop):
    bad = copy.deepcopy(real_drop)
    bad["echo"]["audio_url"] = "https://evil.example.com/echo.mp3"
    with pytest.raises(ltp.DropValidationError, match="domain"):
        ltp.Drop.from_dict(bad)


def test_rejects_http_url(real_drop):
    bad = copy.deepcopy(real_drop)
    bad["echo"]["audio_url"] = "http://drop.lucidprinciples.com/e.mp3"
    with pytest.raises(ltp.DropValidationError, match="https"):
        ltp.Drop.from_dict(bad)


def test_rejects_oversized_context_block(real_drop):
    bad = copy.deepcopy(real_drop)
    bad["context_block"] = "x" * 2001
    with pytest.raises(ltp.DropValidationError, match="max length"):
        ltp.Drop.from_dict(bad)


def test_rejects_unknown_signal_type(real_drop):
    bad = copy.deepcopy(real_drop)
    bad["signal_type"] = "Loud"
    with pytest.raises(ltp.DropValidationError):
        ltp.Drop.from_dict(bad)


def test_rejects_unsupported_major_version(real_drop):
    bad = copy.deepcopy(real_drop)
    bad["schema_version"] = "2.0"
    with pytest.raises(ltp.DropValidationError, match="major"):
        ltp.Drop.from_dict(bad)


def test_rejects_nonfinite_floats(real_drop):
    bad = copy.deepcopy(real_drop)
    bad["love_equation"]["C"] = float("inf")
    with pytest.raises(ltp.DropValidationError, match="finite"):
        ltp.Drop.from_dict(bad)


# ── Sign/verify round trip + chain (SPEC Sections 5, 6) ──────────────────

def _sign(drop: dict, private_key) -> dict:
    import base64
    unsigned = {k: v for k, v in drop.items() if k != "signature"}
    sig = private_key.sign(signing_payload(unsigned))
    signed = dict(unsigned)
    signed["signature"] = base64.b64encode(sig).decode()
    return signed


def test_sign_verify_roundtrip(real_drop, keypair):
    private, pem = keypair
    signed = _sign(real_drop, private)
    ltp.verify_signature(signed, load_public_key(pem))


def test_chain_verification(real_drop, keypair):
    private, _ = keypair
    drop1 = _sign(real_drop, private)

    drop2 = copy.deepcopy(real_drop)
    drop2["sequence"] = 2
    drop2["drop_date"] = "2026-06-11"
    drop2["prev_drop_hash"] = ltp.drop_hash(drop1)
    drop2 = _sign(drop2, private)

    ltp.verify_chain(drop2, drop1)  # valid chain

    # Tampering with drop1 breaks the chain
    tampered1 = copy.deepcopy(drop1)
    tampered1["context_block"] = "rewritten history"
    with pytest.raises(ltp.DropVerificationError, match="chain"):
        ltp.verify_chain(drop2, tampered1)

    # Sequence gaps are rejected
    drop3 = copy.deepcopy(drop2)
    drop3["sequence"] = 4
    with pytest.raises(ltp.DropVerificationError, match="gap"):
        ltp.verify_chain(drop3, drop2)
