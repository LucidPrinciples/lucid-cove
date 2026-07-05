"""
Reference data — the tuning key library and frequency-signal mapping.

Loads the bundled lt_reference.json (generated from the protected source
tuning-keys.md — that file remains the source of truth; this JSON is its
deployment form, per LTP Protocol Spec Section 4).

Content licensing: tuning keys are exact Canon quotes by Chords of Truth,
Lucid Principles Canon, CC BY 4.0. See data/ATTRIBUTION.md.
"""

import json
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

# LTP Protocol Spec Section 3 — fixed frequency → signal type mapping.
# Each frequency has exactly one signal type. 13 frequencies, 7 signal types.
FREQUENCY_SIGNAL_MAP = {
    "PEACE": "Ground",
    "CLARITY": "Clear",
    "MOMENTUM": "Drive",
    "TRUST": "Raw",
    "JOY": "Bright",
    "CONNECTION": "Open",
    "PRESENCE": "Ground",
    "RESILIENCE": "Rise",
    "COURAGE": "Drive",
    "GRATITUDE": "Bright",
    "RELEASE": "Open",
    "INTEGRATION": "Clear",
    "BOUNDARY": "Clear",
}

SIGNAL_TYPES = ("Ground", "Clear", "Drive", "Raw", "Bright", "Open", "Rise")

# Drop spec frequency order (number 1-13)
FREQUENCY_ORDER = (
    "PEACE", "CLARITY", "MOMENTUM", "TRUST", "JOY", "CONNECTION",
    "PRESENCE", "RESILIENCE", "COURAGE", "GRATITUDE", "RELEASE",
    "INTEGRATION", "BOUNDARY",
)


@dataclass(frozen=True)
class TuningKey:
    """One Canon quote mapped to a frequency. Quote text is exact Canon —
    never paraphrase, rearrange, or alter."""
    quote: str
    principle: str
    echo_filename: str
    frequency: str
    signal_type: str


@dataclass
class Reference:
    """The runtime tuning key library."""
    frequencies: dict[str, dict] = field(default_factory=dict)
    audio_base_url: str = ""
    audio_url_pattern: str = ""
    analysis_url_pattern: str = ""

    @property
    def all_frequencies(self) -> list[str]:
        return list(self.frequencies.keys())

    def signal_type_for(self, frequency: str) -> str:
        freq = frequency.upper()
        entry = self.frequencies.get(freq)
        if entry and entry.get("signal_type"):
            return entry["signal_type"]
        return FREQUENCY_SIGNAL_MAP[freq]

    def tuning_keys_for(self, frequency: str) -> list[TuningKey]:
        freq = frequency.upper()
        entry = self.frequencies.get(freq, {})
        signal_type = self.signal_type_for(freq)
        return [
            TuningKey(
                quote=k["quote"],
                principle=k["principle"],
                echo_filename=k["echo_filename"],
                frequency=freq,
                signal_type=signal_type,
            )
            for k in entry.get("tuning_keys", [])
        ]

    def echo_audio_url(self, key: TuningKey) -> str:
        """Deterministic echo derivation (spec Section 5): the echo follows
        from principle + signal type. Never randomly selected."""
        return self.audio_url_pattern.format(
            base_url=self.audio_base_url,
            signal_type=key.signal_type,
            echo_filename=key.echo_filename,
        )

    def analysis_url_for(self, signal_type: str, echo_filename: str) -> str:
        """The echo's analysis-JSON URL (the waveform + lyrics file), using the
        configured analysis_url_pattern. Returns '' if no pattern is set so the
        caller can fall back to the audio-suffix convention."""
        if not self.analysis_url_pattern:
            return ""
        return self.analysis_url_pattern.format(
            base_url=self.audio_base_url,
            signal_type=signal_type,
            echo_filename=echo_filename,
        )

    def validate(self) -> list[str]:
        """Check the loaded data against the fixed protocol tables.
        Returns a list of problems (empty = valid)."""
        problems = []
        for freq, entry in self.frequencies.items():
            if freq not in FREQUENCY_SIGNAL_MAP:
                problems.append(f"Unknown frequency: {freq}")
                continue
            st = entry.get("signal_type")
            if st != FREQUENCY_SIGNAL_MAP[freq]:
                problems.append(
                    f"{freq}: signal_type {st!r} != spec {FREQUENCY_SIGNAL_MAP[freq]!r}"
                )
            if not entry.get("tuning_keys"):
                problems.append(f"{freq}: no tuning keys")
        missing = set(FREQUENCY_SIGNAL_MAP) - set(self.frequencies)
        if missing:
            problems.append(f"Missing frequencies: {sorted(missing)}")
        return problems


def load_reference(path: str | Path | None = None) -> Reference:
    """Load the tuning key library.

    Default: the bundled lt_reference.json (anchor="canon").
    Pass a path for a custom anchor (anchor="custom").
    """
    if path is not None:
        raw = json.loads(Path(path).read_text("utf-8"))
    else:
        raw = json.loads(
            resources.files("lucid_tuner_protocol.data")
            .joinpath("lt_reference.json")
            .read_text("utf-8")
        )
    return Reference(
        frequencies=raw.get("frequencies", {}),
        audio_base_url=raw.get("audio_base_url", ""),
        audio_url_pattern=raw.get("audio_url_pattern", ""),
        analysis_url_pattern=raw.get("analysis_url_pattern", ""),
    )
