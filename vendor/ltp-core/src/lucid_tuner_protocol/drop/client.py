"""
DropClient — fetch, verify, cache, fall back.

Recommended client behavior per ltp-drop SPEC Section 8:
  1. Fetch /latest.json
  2. Verify the Ed25519 signature
  3. Verify chain integrity if the previous drop is cached
  4. Parse and use the drop
  5. Store locally for future chain verification

Offline behavior (SPEC Section 9 + repos spec):
  network fails -> last cached verified drop ("a stale drop is always
  better than no drop"). If there is no cache at all, today() raises
  DropUnavailable — for fully offline systems, use TuningProtocol local
  mode instead.
"""

import json
import logging
from datetime import date as _date
from pathlib import Path

import httpx

from .canonical import drop_hash
from .schema import DEFAULT_ALLOWED_DOMAINS, Drop
from .verify import (
    DropVerificationError,
    load_public_key,
    verify_chain,
    verify_signature,
)

logger = logging.getLogger("ltp.drop")

DEFAULT_BASE_URL = "https://drop.lucidprinciples.com"
DEFAULT_CACHE_DIR = Path.home() / ".cache" / "lucid-tuner-protocol"


class DropUnavailable(Exception):
    """No verified drop could be obtained from network or cache."""


class DropClient:
    """Subscriber client for the daily LTP Drop.

    Usage:
        import lucid_tuner_protocol as ltp
        drop = ltp.DropClient().today()
        agent_context += drop.as_context()
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        cache_dir: str | Path = DEFAULT_CACHE_DIR,
        timeout: float = 10.0,
        allowed_domains: tuple = DEFAULT_ALLOWED_DOMAINS,
        public_key_pem: str | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.cache_dir = Path(cache_dir)
        self.timeout = timeout
        self.allowed_domains = allowed_domains
        self._pinned_pem = public_key_pem
        self._public_key = None

    # ── public API ──────────────────────────────────────────────────────

    def today(self) -> Drop:
        """Fetch + verify the latest drop. Falls back to cache on failure."""
        try:
            raw = self._fetch_json("/latest.json")
            return self._verify_and_cache(raw)
        except Exception as e:
            logger.warning("Drop fetch/verify failed (%s) — trying cache", e)
            cached = self._load_cached_latest()
            if cached is not None:
                return cached
            raise DropUnavailable(
                f"no verified drop available from network or cache: {e}"
            ) from e

    latest = today  # alias

    def for_date(self, day: str | _date) -> Drop:
        """Fetch + verify a specific day's drop from the archive.

        A 404 means no drop was published that day (raises DropUnavailable).
        """
        if isinstance(day, _date):
            day = day.isoformat()
        y, m, d = day.split("-")
        try:
            raw = self._fetch_json(f"/drops/{y}/{m}/{d}.json")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise DropUnavailable(f"no drop published on {day}") from e
            raise
        return self._verify_and_cache(raw)

    def verify(self, raw: dict) -> Drop:
        """Verify + parse a drop dict obtained out of band. No caching."""
        verify_signature(raw, self._key())
        verify_chain(raw, self._cached_raw_for_sequence(raw.get("sequence", 0) - 1))
        return Drop.from_dict(raw, allowed_domains=self.allowed_domains)

    # ── internals ───────────────────────────────────────────────────────

    def _verify_and_cache(self, raw: dict) -> Drop:
        # Signature before trust — verify BEFORE schema parsing (SPEC s11).
        verify_signature(raw, self._key())
        prev = self._cached_raw_for_sequence(raw.get("sequence", 0) - 1)
        verify_chain(raw, prev)
        drop = Drop.from_dict(raw, allowed_domains=self.allowed_domains)
        self._cache_drop(raw, drop)
        return drop

    def _fetch_json(self, path: str) -> dict:
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.get(f"{self.base_url}{path}")
            resp.raise_for_status()
            return resp.json()

    def _key(self):
        if self._public_key is None:
            pem = self._pinned_pem or self._fetch_pubkey_pem()
            self._public_key = load_public_key(pem)
        return self._public_key

    def _fetch_pubkey_pem(self) -> str:
        """Publisher key, cached locally after first fetch (long TTL)."""
        key_cache = self.cache_dir / "ltp-publisher.pub"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(f"{self.base_url}/keys/ltp-publisher.pub")
                resp.raise_for_status()
                pem = resp.text
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            key_cache.write_text(pem, "utf-8")
            return pem
        except Exception:
            if key_cache.exists():
                logger.warning("Pubkey fetch failed — using cached key")
                return key_cache.read_text("utf-8")
            raise

    def _cache_drop(self, raw: dict, drop: Drop):
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(raw, ensure_ascii=True)
            (self.cache_dir / f"drop-{drop.sequence:06d}.json").write_text(payload, "utf-8")
            (self.cache_dir / "latest.json").write_text(payload, "utf-8")
        except Exception as e:
            logger.warning("Drop cache write failed (non-fatal): %s", e)

    def _load_cached_latest(self) -> Drop | None:
        path = self.cache_dir / "latest.json"
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text("utf-8"))
            # Cached drops were verified at write time; verify again if we
            # have a key (offline with no key ever seen -> trust the cache
            # we wrote ourselves).
            try:
                verify_signature(raw, self._key())
            except DropVerificationError:
                logger.error("Cached drop failed re-verification — discarding")
                return None
            except Exception:
                pass  # no key reachable; cache was verified when written
            return Drop.from_dict(raw, allowed_domains=self.allowed_domains)
        except Exception as e:
            logger.warning("Cached drop unreadable: %s", e)
            return None

    def _cached_raw_for_sequence(self, sequence: int) -> dict | None:
        if sequence < 1:
            return None
        path = self.cache_dir / f"drop-{sequence:06d}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text("utf-8"))
        except Exception:
            return None
