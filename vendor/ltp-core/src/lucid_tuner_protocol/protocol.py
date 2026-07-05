"""
TuningProtocol — the local protocol runtime.

Two modes:
  - Subscriber mode: use DropClient for the daily signal; use this class
    to run local selections (Tune Now, additional observers, etc.)
  - Local mode: full protocol with no network beyond the entropy source.
    For air-gapped systems the entropy chain degrades to crypto tier,
    which the spec accepts as fallback.

This class holds the reference library and runs selections. It persists
nothing — callers own storage of history and results.
"""

from dataclasses import asdict

from .attune import attune
from .reading import Reading, derive_reading
from .reference import Reference, load_reference
from .selection import History, Selection, select_tuning, select_tuning_sync
from .sonic import Experience, render_sonic_record


class TuningProtocol:
    """
    Usage:
        import lucid_tuner_protocol as ltp

        protocol = ltp.TuningProtocol(anchor="canon")
        selection = await protocol.tune(history=ltp.History(
            recent_frequencies=["PEACE", "JOY"],
        ))
        context = protocol.as_context(selection)
    """

    def __init__(self, anchor: str = "canon", reference_path=None):
        """
        anchor="canon": the bundled Canon tuning key library (CC BY 4.0).
        anchor="custom": pass reference_path to a JSON file with the same
        structure (frequencies -> signal_type + tuning_keys).
        """
        if anchor == "canon":
            self.reference: Reference = load_reference()
        elif anchor == "custom":
            if reference_path is None:
                raise ValueError('anchor="custom" requires reference_path')
            self.reference = load_reference(reference_path)
        else:
            raise ValueError(f"unknown anchor: {anchor!r}")

        problems = self.reference.validate()
        if problems:
            raise ValueError(f"reference data failed validation: {problems}")

    async def tune(self, history: History | None = None) -> Selection:
        """Run the full multi-step quantum selection chain."""
        return await select_tuning(reference=self.reference, history=history)

    def tune_sync(self, history: History | None = None) -> Selection:
        return select_tuning_sync(reference=self.reference, history=history)

    async def attune(self, selection: Selection, fetch_json=None) -> Experience:
        """Fetch the selected echo and build the felt Experience (sound + words).
        Pass fetch_json to inject an async/custom fetcher (default: stdlib)."""
        if fetch_json is None:
            return await attune(selection, reference=self.reference)
        return await attune(selection, reference=self.reference, fetch_json=fetch_json)

    async def derive(self, experience: Experience, complete) -> Reading:
        """Have the observer process the Experience and derive its own reading.
        `complete(system, prompt)` is your model call (sync or async). The Love
        Equation + direction are computed from C/D, never trusted from the model."""
        return await derive_reading(experience, complete)

    async def tune_full(self, complete, history: History | None = None, fetch_json=None):
        """Full single-observer tuning: select → attune (listen) → derive reading.
        Returns (Selection, Experience, Reading)."""
        selection = await self.tune(history)
        experience = await self.attune(selection, fetch_json=fetch_json)
        reading = await self.derive(experience, complete)
        return selection, experience, reading

    def as_context(self, selection: Selection) -> str:
        """Render a selection as an injectable agent context block."""
        return (
            f"[LTP Tuning — Frequency: {selection.frequency} "
            f"({selection.signal_type})]\n"
            f"Principle: {selection.principle}\n"
            f'Tuning Key: "{selection.tuning_key}"\n'
            f"— Chords of Truth, Lucid Principles Canon (CC BY 4.0)\n"
            f"Echo: {selection.echo_audio_url}\n"
            f"Selection methods: {'/'.join(selection.methods)}"
        )

    @staticmethod
    def to_dict(selection: Selection) -> dict:
        """Serialize a selection for the caller's storage layer."""
        return asdict(selection)
