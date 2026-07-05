"""
Quantum entropy — ANU QRNG with crypto + pseudo fallback.

LTP Protocol Spec, Section 1: every selection in the tuning protocol uses
this 3-tier chain. The entropy source IS the protocol.

Tier 1: ANU Quantum RNG (vacuum fluctuations — the real thing)
Tier 2: Cryptographic RNG (secrets module — secure but not quantum)
Tier 3: Pseudorandom (CPU-clock PRNG — last resort, should never persist)

A pipeline consistently returning method "pseudo" is broken and must be
investigated. This module is extracted from the Lucid Cove reference
implementation and carries identical semantics.
"""

import asyncio
import logging
import random
import secrets

import httpx

logger = logging.getLogger("ltp.entropy")

ANU_QRNG_URL = "https://qrng.anu.edu.au/API/jsonI.php"
ANU_TIMEOUT_S = 2.0  # Don't block the pipeline on a flaky API

METHOD_QUANTUM = "quantum"
METHOD_CRYPTO = "crypto"
METHOD_PSEUDO = "pseudo"


async def fetch_quantum_random(
    pool_size: int,
    timeout: float = ANU_TIMEOUT_S,
) -> tuple[int, str]:
    """Select an index from a pool using quantum entropy.

    Returns (index, method) where method is 'quantum', 'crypto', or 'pseudo'.

    Per LTP Protocol Spec Section 1:
      - Tier 1: ANU QRNG, length=1, type=uint16, index = raw % pool_size
      - Tier 2: secrets.randbelow(pool_size)
      - Tier 3: random.randrange(pool_size)
    """
    if pool_size < 1:
        raise ValueError("pool_size must be >= 1")

    # ── Tier 1: ANU Quantum RNG ─────────────────────────────────────────
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(
                ANU_QRNG_URL,
                params={"length": 1, "type": "uint16"},
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success") and data.get("data"):
                    raw = data["data"][0]  # uint16: 0-65535
                    return raw % pool_size, METHOD_QUANTUM
    except Exception:
        pass  # timeout, network error, JSON parse — fall through

    # ── Tier 2: Cryptographic RNG ───────────────────────────────────────
    try:
        value = secrets.randbelow(pool_size)
        return value, METHOD_CRYPTO
    except Exception:
        pass

    # ── Tier 3: Pseudo-random (last resort) ─────────────────────────────
    logger.warning(
        "LTP entropy fell through to Tier 3 (pseudo). "
        "If this persists, something is broken — investigate."
    )
    return random.randrange(pool_size), METHOD_PSEUDO


def fetch_quantum_random_sync(
    pool_size: int,
    timeout: float = ANU_TIMEOUT_S,
) -> tuple[int, str]:
    """Synchronous wrapper around fetch_quantum_random.

    For callers without an event loop. Inside async code, await
    fetch_quantum_random directly.
    """
    return asyncio.run(fetch_quantum_random(pool_size, timeout=timeout))
