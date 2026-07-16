"""Open-source cleanliness guards (#126 / #65 / #66 safety net).

Pure text scans of the source tree — no imports of app code, no DB, no deps, so
these run anywhere (CI, a fresh clone, the sandbox). They go red the moment a
founder value, a hardcoded founder host, or an undocumented env var sneaks back
into the runtime code. They protect the 2026-06-22 founder-value scrub.

#SEC5/#99 retired provision_overlay.py — no longer in SKIP_FILES.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
PROVISION = REPO / "provision"
DOCKER = REPO / "docker"
STATIC = SRC / "dashboard" / "static"

# Legacy/founder artifacts pending their own GATED cleanup — do not let new code
# join this list:
#  - jag-clearfield.yaml: batch8 #14 REMOVED this from git (git rm; moved to
#    ClearfieldCove). Kept here only because the autopilot sandbox can't unlink
#    the working-tree copy (EPERM); once Chords' deploy checks out the clean tree
#    the file is gone and this entry is a harmless no-op — DROP it then.
#  - provision_overlay.py + family.config.example.yaml: RETIRED under #SEC5/#99.
SKIP_FILES = {"jag-clearfield.yaml"}

# Static assets whose removal is confirmation-gated (CF-108). batch8 #14 REMOVED
# jason.png from git (git rm; the founder Cove keeps its live copy in instance
# data). Kept here only because the autopilot sandbox can't unlink the working-tree
# copy (EPERM); post-deploy the clean tree has no jason.png and this is a no-op —
# DROP it then.
SKIP_STATIC_NAMES = {"jason.png"}

# Place / host names that should never appear in runtime source.
FOUNDER_PLACE_HOST = re.compile(r"covington|garriotte|lphomebase|lp-homebase|tail570223", re.I)

# "jason"/"chords" as identifiers (avoids false positives like "power chords").
FOUNDER_IDENT = re.compile(r"\b(jason|chords)\b", re.I)

# Documented, intentional exceptions — (filename, substring that must be on the line).
# receiver.py keeps a legacy `chords` tuning-key fallback (operator-first) until the
# VPS publisher emits the key as "operator"; see receiver.py operator_tuning.
IDENT_EXCEPTIONS = [
    ("receiver.py", 'get("chords")'),
]


def _runtime_py_files():
    for p in SRC.rglob("*.py"):
        if p.name in SKIP_FILES or "__pycache__" in p.parts:
            continue
        yield p


# The C7 root cause (CF-108): the guard scanned only src/*.py, so founder values
# in docker/ and provision/ escaped every sweep. Scan those trees too.
_EXTENDED_EXT = {".py", ".sh", ".sql", ".yaml", ".yml", ".caddy", ".md", ".json"}


def _extended_scan_files():
    for base in (PROVISION, DOCKER):
        for p in base.rglob("*"):
            if (not p.is_file() or p.name in SKIP_FILES
                    or "__pycache__" in p.parts):
                continue
            if p.suffix.lower() in _EXTENDED_EXT:
                yield p


def _allowed_ident(path: Path, line: str) -> bool:
    return any(path.name == f and sub in line for f, sub in IDENT_EXCEPTIONS)


class TestNoFounderValues:
    def test_no_founder_place_or_host_names_in_runtime_src(self):
        hits = []
        for p in _runtime_py_files():
            for i, line in enumerate(p.read_text(errors="ignore").splitlines(), 1):
                if FOUNDER_PLACE_HOST.search(line):
                    hits.append(f"{p.relative_to(REPO)}:{i}: {line.strip()}")
        assert not hits, "Founder place/host names in runtime src:\n" + "\n".join(hits)

    def test_no_founder_identifiers_in_runtime_src(self):
        hits = []
        for p in _runtime_py_files():
            for i, line in enumerate(p.read_text(errors="ignore").splitlines(), 1):
                if FOUNDER_IDENT.search(line) and not _allowed_ident(p, line):
                    hits.append(f"{p.relative_to(REPO)}:{i}: {line.strip()}")
        assert not hits, "Founder identifiers (jason/chords) in runtime src:\n" + "\n".join(hits)

    def test_no_founder_values_in_provision_or_docker(self):
        hits = []
        for p in _extended_scan_files():
            for i, line in enumerate(p.read_text(errors="ignore").splitlines(), 1):
                if FOUNDER_PLACE_HOST.search(line) or (
                        FOUNDER_IDENT.search(line) and not _allowed_ident(p, line)):
                    hits.append(f"{p.relative_to(REPO)}:{i}: {line.strip()}")
        assert not hits, "Founder values in provision//docker/:\n" + "\n".join(hits)

    def test_no_founder_names_in_static_filenames(self):
        pat = re.compile(r"jason|chords|covington|garriotte|lphomebase", re.I)
        hits = [str(p.relative_to(REPO)) for p in STATIC.rglob("*")
                if p.is_file() and pat.search(p.name)
                and p.name not in SKIP_STATIC_NAMES]
        assert not hits, "Founder-named static assets:\n" + "\n".join(hits)

    def test_no_founder_getenv_defaults(self):
        # Catches the exact pattern that leaked the founder NC account, e.g.
        # os.getenv("NEXTCLOUD_USER", "chords"). Defaults must be neutral or empty.
        bad = re.compile(r"getenv\([^)]*['\"](?:chords|jason|covington)['\"]", re.I)
        hits = []
        for p in _runtime_py_files():
            for i, line in enumerate(p.read_text(errors="ignore").splitlines(), 1):
                if bad.search(line):
                    hits.append(f"{p.relative_to(REPO)}:{i}: {line.strip()}")
        assert not hits, "Founder values as getenv defaults:\n" + "\n".join(hits)


class TestEnvExampleCoverage:
    def test_env_example_documents_every_runtime_env_var(self):
        # Every os.getenv/os.environ key used in src must be documented in
        # .env.example, so the example never drifts behind the code (#65).
        key = re.compile(r"os\.(?:getenv|environ\.get)\(\s*['\"]([A-Z0-9_]+)['\"]")
        key_bracket = re.compile(r"os\.environ\[\s*['\"]([A-Z0-9_]+)['\"]")
        used = set()
        for p in SRC.rglob("*.py"):
            if "__pycache__" in p.parts:
                continue
            t = p.read_text(errors="ignore")
            used |= set(key.findall(t)) | set(key_bracket.findall(t))

        documented = set()
        for line in (REPO / ".env.example").read_text().splitlines():
            m = re.match(r"\s*([A-Z0-9_]+)=", line)
            if m:
                documented.add(m.group(1))

        # System vars not configured via .env.
        ALLOW = {"HOME"}
        missing = used - documented - ALLOW
        assert not missing, (
            "env vars used in src/ but not documented in .env.example: "
            + ", ".join(sorted(missing))
        )
