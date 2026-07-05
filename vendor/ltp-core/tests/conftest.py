import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def real_drop() -> dict:
    """The actual published Drop #1 from drop.lucidprinciples.com."""
    return json.loads((FIXTURES / "latest.json").read_text("utf-8"))


@pytest.fixture
def publisher_pem() -> str:
    """The actual published Ed25519 public key."""
    return (FIXTURES / "ltp-publisher.pub").read_text("utf-8")


@pytest.fixture
def keypair():
    """Ephemeral Ed25519 keypair for sign/verify round-trip tests."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization

    private = Ed25519PrivateKey.generate()
    pem = private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private, pem
