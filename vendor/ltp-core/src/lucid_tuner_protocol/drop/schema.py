"""
Drop schema — ltp-drop SPEC Section 2.

Validation rules implemented here:
  - Required fields and types per declared schema_version
  - Field length limits (security: Section 11)
  - Enum constraints (13 frequencies, 7 signal types)
  - Finite floats only
  - URL domain restriction (Section 3): URLs in a drop must point to
    allowed domains; reject otherwise
  - Drops are DATA ONLY. Nothing in a drop is ever executed or rendered
    as HTML by this library.
"""

import math
from dataclasses import dataclass, field
from urllib.parse import urlparse

from ..reference import FREQUENCY_ORDER, SIGNAL_TYPES

SUPPORTED_MAJOR = 1

# SPEC Section 3: drops may reference the drop domain and the canonical
# foundation audio host. audio.lucidprinciples.com is the active echo host
# (schema 1.1+); audio.lucidtuner.com is retained so the immutable historical
# drops (#1-#7, schema 1.0) still verify.
DEFAULT_ALLOWED_DOMAINS = (
    "drop.lucidprinciples.com",
    "audio.lucidprinciples.com",
    "audio.lucidtuner.com",
)

_FREQ_NAMES = {f.capitalize() for f in FREQUENCY_ORDER} | set(FREQUENCY_ORDER)


class DropValidationError(ValueError):
    """The drop JSON does not conform to the SPEC."""


@dataclass(frozen=True)
class PracticeStep:
    step: int
    title: str
    instruction: str


@dataclass(frozen=True)
class Drop:
    """A parsed, validated drop. Construct via Drop.from_dict()."""
    schema_version: str
    drop_date: str
    sequence: int
    frequency_number: int
    frequency_name: str
    signal_type: str
    tuning_key_text: str
    tuning_key_source_song: str
    tuning_key_source_year: int
    tuning_key_attribution: str
    echo_id: str
    echo_audio_url: str
    echo_signature: dict
    love_equation: dict
    context_block: str
    practice: tuple
    publisher: str
    signature: str
    prev_drop_hash: str
    tuning_day: int | None = None
    raw: dict = field(default_factory=dict, repr=False, compare=False)

    # ── API ──────────────────────────────────────────────────────────────

    def as_context(self) -> str:
        """The ready-to-inject agent context block, with attribution."""
        return (
            f"[LTP Drop {self.drop_date} — Frequency {self.frequency_number}/13: "
            f"{self.frequency_name} ({self.signal_type})]\n"
            f'Tuning Key: "{self.tuning_key_text}"\n'
            f"— {self.tuning_key_attribution}\n\n"
            f"{self.context_block}"
        )

    @property
    def love_equation_value(self) -> float:
        """dE/dt = beta x (C - D) x E"""
        le = self.love_equation
        return round(le["beta"] * (le["C"] - le["D"]) * le["E"], 4)

    # ── Parsing + validation ─────────────────────────────────────────────

    @classmethod
    def from_dict(
        cls,
        data: dict,
        allowed_domains: tuple = DEFAULT_ALLOWED_DOMAINS,
    ) -> "Drop":
        _require(isinstance(data, dict), "drop must be a JSON object")

        version = _string(data, "schema_version", 12)
        major = _major_version(version)
        _require(
            major == SUPPORTED_MAJOR,
            f"unsupported schema major version: {version!r}",
        )

        drop_date = _string(data, "drop_date", 10)
        _require(
            len(drop_date) == 10 and drop_date[4] == "-" and drop_date[7] == "-",
            f"drop_date must be YYYY-MM-DD: {drop_date!r}",
        )

        sequence = _int(data, "sequence")
        _require(sequence >= 1, "sequence must be >= 1")

        freq = data.get("frequency")
        _require(isinstance(freq, dict), "frequency must be an object")
        f_number = _int(freq, "number")
        _require(1 <= f_number <= 13, "frequency.number must be 1-13")
        f_name = _string(freq, "name", 20)
        _require(f_name in _FREQ_NAMES, f"unknown frequency name: {f_name!r}")
        _require(_int(freq, "of") == 13, "frequency.of must be 13")

        signal_type = _string(data, "signal_type", 10)
        _require(signal_type in SIGNAL_TYPES, f"unknown signal_type: {signal_type!r}")

        tk = data.get("tuning_key")
        _require(isinstance(tk, dict), "tuning_key must be an object")
        tk_text = _string(tk, "text", 500)
        tk_song = _string(tk, "source_song", 100)
        tk_year = _int(tk, "source_year")
        _require(2011 <= tk_year <= 2017, "tuning_key.source_year must be 2011-2017")
        tk_attr = _string(tk, "attribution", 200)

        echo = data.get("echo")
        _require(isinstance(echo, dict), "echo must be an object")
        echo_id = _string(echo, "id", 32)
        audio_url = _string(echo, "audio_url", 200)
        _check_url(audio_url, allowed_domains)
        echo_sig = echo.get("signature")
        _require(isinstance(echo_sig, dict), "echo.signature must be an object")
        for k in ("E_analog", "beta_analog", "C_analog", "D_analog"):
            _finite(echo_sig, k)

        le = data.get("love_equation")
        _require(isinstance(le, dict), "love_equation must be an object")
        for k in ("E", "beta", "C", "D"):
            _finite(le, k)

        context_block = _string(data, "context_block", 2000)

        # v1.1 additive fields — required when the drop declares >= 1.1,
        # accepted when present on drops declaring 1.0 (per SPEC Section 7,
        # unknown/extra fields are ignored, not rejected).
        tuning_day = None
        if "tuning_day" in data:
            tuning_day = _int(data, "tuning_day")
            _require(tuning_day >= 1, "tuning_day must be positive")

        practice: list[PracticeStep] = []
        if "practice" in data:
            steps = data["practice"]
            _require(isinstance(steps, list) and len(steps) <= 5, "practice must be an array, max 5")
            for s in steps:
                practice.append(PracticeStep(
                    step=_int(s, "step"),
                    title=_string(s, "title", 100),
                    instruction=_string(s, "instruction", 500),
                ))

        publisher = _string(data, "publisher", 100)
        signature = _string(data, "signature", 120)
        prev_hash = _string(data, "prev_drop_hash", 64)
        _require(
            len(prev_hash) == 64 and all(c in "0123456789abcdef" for c in prev_hash),
            "prev_drop_hash must be 64-char lowercase hex",
        )

        return cls(
            schema_version=version,
            drop_date=drop_date,
            sequence=sequence,
            frequency_number=f_number,
            frequency_name=f_name,
            signal_type=signal_type,
            tuning_key_text=tk_text,
            tuning_key_source_song=tk_song,
            tuning_key_source_year=tk_year,
            tuning_key_attribution=tk_attr,
            echo_id=echo_id,
            echo_audio_url=audio_url,
            echo_signature=dict(echo_sig),
            love_equation=dict(le),
            context_block=context_block,
            practice=tuple(practice),
            publisher=publisher,
            signature=signature,
            prev_drop_hash=prev_hash,
            tuning_day=tuning_day,
            raw=data,
        )


# ── helpers ──────────────────────────────────────────────────────────────

def _require(cond: bool, msg: str):
    if not cond:
        raise DropValidationError(msg)


def _string(obj: dict, key: str, max_len: int) -> str:
    val = obj.get(key)
    _require(isinstance(val, str) and val != "", f"{key} must be a non-empty string")
    _require(len(val) <= max_len, f"{key} exceeds max length {max_len}")
    return val


def _int(obj: dict, key: str) -> int:
    val = obj.get(key)
    _require(isinstance(val, int) and not isinstance(val, bool), f"{key} must be an integer")
    return val


def _finite(obj: dict, key: str) -> float:
    val = obj.get(key)
    _require(isinstance(val, (int, float)) and not isinstance(val, bool), f"{key} must be a number")
    _require(math.isfinite(val), f"{key} must be finite")
    return float(val)


def _check_url(url: str, allowed_domains: tuple):
    parsed = urlparse(url)
    _require(parsed.scheme == "https", f"URL must be https: {url!r}")
    _require(
        parsed.hostname in allowed_domains,
        f"URL domain {parsed.hostname!r} not in allowed domains {allowed_domains}",
    )


def _major_version(version: str) -> int:
    try:
        return int(version.split(".")[0])
    except (ValueError, IndexError):
        raise DropValidationError(f"invalid schema_version: {version!r}")
