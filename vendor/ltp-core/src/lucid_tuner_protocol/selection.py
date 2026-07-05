"""
Multi-step quantum selection chain — LTP Protocol Spec Section 2.

Tuning selection is not a single random pick. It is a sequence of
independent quantum rolls, each narrowing the field:

  Roll 1: frequency  (excluding the observer's recent history, typically 5)
  Roll 2: principle  (within the frequency; recency filtered, coverage weighted)
  Roll 3: tuning key (within the principle; recency filtered)

Three independent entropy calls minimum. Never collapse the rolls into
one. The independence of each selection is part of the protocol — each
is a separate moment of quantum collapse.

The echo is then DERIVED, not rolled: it follows deterministically from
principle + signal type (spec Section 5).

This module holds no state and touches no storage. Callers pass history
in and persist results out.
"""

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field

from .entropy import fetch_quantum_random
from .reference import Reference, TuningKey, load_reference


@dataclass(frozen=True)
class Selection:
    """The result of one full selection chain."""
    frequency: str
    signal_type: str
    principle: str
    tuning_key: str
    echo_filename: str
    echo_audio_url: str
    methods: tuple[str, str, str]  # entropy method per roll

    @property
    def fully_quantum(self) -> bool:
        return all(m == "quantum" for m in self.methods)


@dataclass
class History:
    """Observer history used for recency filtering. All optional."""
    recent_frequencies: list[str] = field(default_factory=list)
    recent_principles: list[str] = field(default_factory=list)
    recent_tuning_keys: list[str] = field(default_factory=list)
    principle_usage_counts: dict[str, int] = field(default_factory=dict)


async def select_tuning(
    reference: Reference | None = None,
    history: History | None = None,
    entropy=fetch_quantum_random,
) -> Selection:
    """Run the full multi-step quantum selection chain.

    `entropy` is injectable for testing only — production always uses the
    3-tier chain from entropy.py (spec Section 1: no exceptions).
    """
    ref = reference or load_reference()
    h = history or History()

    # ── Roll 1: frequency (recency-excluded; reset pool if all excluded) ──
    recent_set = {f.upper() for f in h.recent_frequencies}
    available_freqs = [f for f in ref.all_frequencies if f.upper() not in recent_set]
    if not available_freqs:
        available_freqs = ref.all_frequencies
    f_idx, f_method = await entropy(len(available_freqs))
    frequency = available_freqs[f_idx]

    # ── Group tuning keys by principle within this frequency ──
    keys_by_principle: dict[str, list[TuningKey]] = defaultdict(list)
    for key in ref.tuning_keys_for(frequency):
        keys_by_principle[key.principle].append(key)
    principle_list = list(keys_by_principle.keys())
    if not principle_list:
        raise ValueError(f"No tuning keys for frequency {frequency!r} in reference data")

    # ── Roll 2: principle (recency filtered + coverage weighted) ──
    filtered_principles = [p for p in principle_list if p not in h.recent_principles]
    if not filtered_principles:
        filtered_principles = principle_list  # safety: never empty

    if h.principle_usage_counts and len(filtered_principles) > 1:
        # Favor underrepresented principles: weight = (max_count + 1) - count
        max_count = max(h.principle_usage_counts.values())
        weighted_pool: list[str] = []
        for p in filtered_principles:
            count = h.principle_usage_counts.get(p, 0)
            weight = max((max_count + 1) - count, 1)
            weighted_pool.extend([p] * weight)
        p_idx, p_method = await entropy(len(weighted_pool))
        principle = weighted_pool[p_idx]
    else:
        p_idx, p_method = await entropy(len(filtered_principles))
        principle = filtered_principles[p_idx]

    # ── Roll 3: tuning key (recency filtered) ──
    principle_keys = keys_by_principle[principle]
    filtered_keys = [k for k in principle_keys if k.quote not in h.recent_tuning_keys]
    if not filtered_keys:
        filtered_keys = principle_keys
    q_idx, q_method = await entropy(len(filtered_keys))
    key = filtered_keys[q_idx]

    # ── Echo derivation: deterministic, never rolled (spec Section 5) ──
    return Selection(
        frequency=frequency,
        signal_type=key.signal_type,
        principle=principle,
        tuning_key=key.quote,
        echo_filename=key.echo_filename,
        echo_audio_url=ref.echo_audio_url(key),
        methods=(f_method, p_method, q_method),
    )


def select_tuning_sync(
    reference: Reference | None = None,
    history: History | None = None,
) -> Selection:
    """Synchronous wrapper for callers without an event loop."""
    return asyncio.run(select_tuning(reference=reference, history=history))
