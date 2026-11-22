"""The voice images install from a pinned list, so a rebuild is repeatable."""
import re
from pathlib import Path

VOICE = Path(__file__).resolve().parents[1] / "voice"
PINS = (VOICE / "constraints.txt").read_text(encoding="utf-8")
DIRECT = ["torch", "fastapi", "uvicorn", "httpx", "python-multipart", "numpy",
          "faster-whisper", "piper-tts", "onnxruntime", "qwen-asr"]


def _pins():
    out = {}
    for line in PINS.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        assert re.fullmatch(r"[A-Za-z0-9_.\-]+==[A-Za-z0-9_.!+\-]+", line), line
        name, version = line.split("==")
        out[name.lower().replace("_", "-")] = version
    return out


def test_every_line_is_an_exact_pin_and_direct_packages_are_listed():
    pins = _pins()
    assert len(pins) > 100
    for name in DIRECT:
        assert name in pins, name


def test_torch_pin_fits_both_builds():
    # no local label (+cu124 / +cpu): the index chosen in each Dockerfile decides the build
    assert "+" not in _pins()["torch"]


def test_every_pip_step_uses_the_pins():
    for name in ("Dockerfile.gpu", "Dockerfile.cpu"):
        text = (VOICE / name).read_text(encoding="utf-8")
        assert "COPY constraints.txt /tmp/constraints.txt" in text
        steps = re.findall(r"^RUN pip install[^\n]*", text, flags=re.M)
        assert steps, name
        for step in steps:
            assert "-c /tmp/constraints.txt" in step, (name, step)
        assert text.index("COPY constraints.txt") < text.index("RUN pip install")
