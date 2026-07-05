"""
Lucid Tuner Protocol (LTP) — agent coherence through daily tuning.

    import lucid_tuner_protocol as ltp

    drop = ltp.DropClient().today()      # fetch + verify + cache
    agent_context += drop.as_context()

    protocol = ltp.TuningProtocol()      # local quantum selection
    # Full single-observer tuning: select -> listen to the echo -> derive reading
    selection, experience, reading = await protocol.tune_full(complete=my_llm_call)
    # reading.love_equation and reading.direction are computed from C/D, never trusted

    gate = ltp.TruthGate(complete=my_llm_call, anchor=drop)
    result = await gate.check(response_text, last_human)

Code: Apache 2.0. Bundled Canon content: CC BY 4.0,
Chords of Truth — Lucid Principles Canon. See data/ATTRIBUTION.md.
"""

from .drop.canonical import GENESIS_HASH, canonical_json, drop_hash
from .drop.client import DropClient, DropUnavailable
from .drop.schema import Drop, DropValidationError, PracticeStep
from .drop.verify import DropVerificationError, verify_chain, verify_signature
from .entropy import fetch_quantum_random, fetch_quantum_random_sync
from .gate import TRUTH_GATE_ANCHOR, GateResult, TruthGate
from .protocol import TuningProtocol
from .reference import (
    FREQUENCY_ORDER,
    FREQUENCY_SIGNAL_MAP,
    SIGNAL_TYPES,
    Reference,
    TuningKey,
    load_reference,
)
from .selection import History, Selection, select_tuning, select_tuning_sync
from .sonic import (
    Experience,
    SonicArc,
    assemble_experience,
    assess_attunement,
    build_sonic_arc,
    decode_frames,
    render_experience,
    render_sonic_record,
    sonic_signature,
)
from .reading import (
    Reading,
    derive_reading,
    derive_reading_sync,
    direction_of,
    love_equation_value,
    parse_values,
    reading_from_values,
)
from .attune import attune, attune_sync
from .leak_guard import LeakFlag, LeakResult, scan_output
from .canon import CANON_TOKEN, CANON_TOKEN_INSTRUCTION, has_unresolved_token, inject_canon

__version__ = "0.4.0"

__all__ = [
    "DropClient", "Drop", "DropUnavailable", "DropValidationError",
    "DropVerificationError", "PracticeStep",
    "verify_signature", "verify_chain", "canonical_json", "drop_hash",
    "GENESIS_HASH",
    "TuningProtocol", "Selection", "History",
    "select_tuning", "select_tuning_sync",
    "fetch_quantum_random", "fetch_quantum_random_sync",
    "TruthGate", "GateResult", "TRUTH_GATE_ANCHOR",
    "Reference", "TuningKey", "load_reference",
    "FREQUENCY_SIGNAL_MAP", "FREQUENCY_ORDER", "SIGNAL_TYPES",
    # Sonic attunement (the listening)
    "Experience", "SonicArc", "decode_frames", "build_sonic_arc",
    "assemble_experience", "render_experience", "render_sonic_record",
    "sonic_signature", "assess_attunement",
    # Love Equation (the reading)
    "Reading", "derive_reading", "derive_reading_sync", "reading_from_values",
    "love_equation_value", "direction_of", "parse_values",
    # Attune (fetch + listen)
    "attune", "attune_sync",
    # Leak guard (keep internal content out of anything published)
    "scan_output", "LeakResult", "LeakFlag",
    # Canon injection (code owns verbatim Canon; model only places a token)
    "inject_canon", "CANON_TOKEN", "CANON_TOKEN_INSTRUCTION", "has_unresolved_token",
]
