"""
Quantum entropy — ANU QRNG with crypto + pseudo fallback.

LTP Protocol Spec v1.0: every selection in the tuning pipeline uses this
3-tier chain. The entropy source IS the protocol.

Tier 1: ANU Quantum RNG (vacuum fluctuations — the real thing)
Tier 2: Cryptographic RNG (secrets module — secure but not quantum)
Tier 3: Pseudorandom (CPU-clock PRNG — last resort, should never persist)

PROTECTED FILE — see LP-Vault/Knowledge Base/ltp-protocol-spec.md
"""

import random
import secrets

import httpx

ANU_QRNG_URL = "https://qrng.anu.edu.au/API/jsonI.php"
ANU_TIMEOUT_S = 2.0  # Don't block the pipeline on a flaky API


async def fetch_quantum_random(pool_size: int) -> tuple[int, str]:
    """Select an index from a pool using quantum entropy.

    Returns (index, method) where method is 'quantum', 'crypto', or 'pseudo'.
    """
    # ── Tier 1: ANU Quantum RNG ─────────────────────────────────────────
    try:
        async with httpx.AsyncClient(timeout=ANU_TIMEOUT_S) as client:
            resp = await client.get(
                ANU_QRNG_URL,
                params={"length": 1, "type": "uint16"},
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success") and data.get("data"):
                    raw = data["data"][0]  # uint16: 0-65535
                    return raw % pool_size, "quantum"
    except Exception:
        pass  # timeout, network error, JSON parse — fall through

    # ── Tier 2: Cryptographic RNG ───────────────────────────────────────
    try:
        value = secrets.randbelow(pool_size)
        return value, "crypto"
    except Exception:
        pass

    # ── Tier 3: Pseudo-random (last resort) ─────────────────────────────
    return random.randrange(pool_size), "pseudo"
