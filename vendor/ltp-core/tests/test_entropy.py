"""Entropy chain — LTP Protocol Spec Section 1."""

import pytest

import lucid_tuner_protocol as ltp
from lucid_tuner_protocol import entropy


async def test_index_in_pool_bounds(monkeypatch):
    """Whatever tier answers, the index is always within the pool."""
    for pool in (1, 2, 13, 244):
        idx, method = await ltp.fetch_quantum_random(pool, timeout=0.001)
        assert 0 <= idx < pool
        assert method in ("quantum", "crypto", "pseudo")


async def test_tier2_fallback_when_network_fails(monkeypatch):
    """ANU unreachable -> crypto tier, never an exception."""
    class _Boom:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): raise OSError("network down")

    monkeypatch.setattr(entropy.httpx, "AsyncClient", _Boom)
    idx, method = await ltp.fetch_quantum_random(13)
    assert 0 <= idx < 13
    assert method == "crypto"


async def test_tier1_quantum_when_api_answers(monkeypatch):
    """ANU answers -> method 'quantum', index = raw % pool."""
    class _Resp:
        status_code = 200
        def json(self): return {"success": True, "data": [65535]}

    class _Client:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, params=None):
            assert url == entropy.ANU_QRNG_URL
            assert params == {"length": 1, "type": "uint16"}
            return _Resp()

    monkeypatch.setattr(entropy.httpx, "AsyncClient", _Client)
    idx, method = await ltp.fetch_quantum_random(13)
    assert method == "quantum"
    assert idx == 65535 % 13


async def test_pool_size_validation():
    with pytest.raises(ValueError):
        await ltp.fetch_quantum_random(0)
