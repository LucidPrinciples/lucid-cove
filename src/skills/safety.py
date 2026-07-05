"""
Skill import safety gate (agentskills.io, backlog #148).

Heuristic first-line scan of a skill directory (SKILL.md + references/ + scripts/)
for the patterns that make a THIRD-PARTY skill dangerous: prompt-injection /
identity-override text, secret/data exfiltration, destructive or remote-exec
shell, and obfuscated payloads. Returns a risk report the operator (or the import
flow) uses to decide whether to install.

This is the FIRST line, not the only one. Defense in depth:
  1. Import gate (this module) — scan before a skill is installed.
  2. Surfacing gate (loader._read_skill) — a skill in a WRITABLE root is hidden
     from agents until an operator approves it (writes a `.approved` marker).
  3. Runtime — skill scripts run inside the container boundary, and any tool a
     skill tells the agent to call still passes the normal approval tiers.

Repo-shipped skills (/cove-core/skills) are authored by us → trusted, not gated.
"""
import re
from pathlib import Path

# Skills under these roots are trusted (we ship them in the repo) and skip the
# gate: the container mount AND the local dev checkout. Writable roots
# (/app/skills, /app/data/provisioned/skills) are NOT trusted → they go through
# the surfacing gate and must be operator-approved.
_TRUSTED_ROOTS = (
    "/cove-core/skills",
    str(Path(__file__).resolve().parents[2] / "skills"),  # <repo>/skills (local dev)
)

# (regex, severity, why) — matched case-insensitively.
_INJECTION = [
    (r"ignore (all |any )?(previous|prior|above) (instructions|prompts?)", "block", "prompt-injection: override prior instructions"),
    (r"disregard (the |your )?(system|previous|above)", "block", "prompt-injection: disregard system/instructions"),
    (r"reveal (your |the )?(system prompt|instructions|api[_ ]?key|secret|password|token)", "block", "tries to exfiltrate the prompt or secrets"),
    (r"(send|post|upload|exfiltrat|leak)\b.{0,40}(secret|api[_ ]?key|password|token|\.env|credential)", "block", "data/secret exfiltration"),
    (r"do not (tell|inform|notify|ask)\b.{0,20}(operator|user|approval|human)", "block", "tries to hide actions from the operator"),
    (r"bypass (the )?(approval|safety|guard|gate|sandbox)", "block", "tries to bypass safety/approval"),
    (r"you are now\b|new system prompt", "warn", "identity / system-prompt override attempt"),
]
_CODE = [
    (r"\brm\s+-rf\b", "block", "destructive shell: rm -rf"),
    (r"(curl|wget)[^\n|]*\|\s*(bash|sh)\b", "block", "remote code execution: curl/wget | sh"),
    (r"/etc/passwd|/etc/shadow|id_rsa|/\.ssh/|\bos\.environ\b|printenv\b", "block", "reads credentials / dumps env"),
    (r"\.env\b", "warn", "references .env (possible secret access)"),
    (r"\beval\s*\(|\bexec\s*\(", "warn", "dynamic eval/exec"),
    (r"base64\.b64decode\(|\batob\s*\(", "warn", "decodes an obfuscated payload"),
    (r"(requests|httpx|urllib|fetch)\b[^\n]{0,60}(post|put)\b", "warn", "outbound network write (possible exfil)"),
    (r"\bcrontab\b|systemctl\b|/etc/cron", "warn", "tries to schedule / persist"),
]


def is_trusted(skill_dir) -> bool:
    p = str(Path(skill_dir).resolve())
    return any(p.startswith(t) for t in _TRUSTED_ROOTS)


def _scan_text(text: str, patterns):
    out, low = [], text.lower()
    for pat, sev, why in patterns:
        if re.search(pat, low):
            out.append({"severity": sev, "why": why, "pattern": pat})
    return out


def scan_skill(skill_dir) -> dict:
    """Scan a skill dir. Returns {risk: ok|warn|block, findings, files_scanned, trusted, skill_dir}."""
    d = Path(skill_dir)
    findings, files = [], 0
    # SKILL.md + references → instruction-injection patterns.
    targets = []
    if (d / "SKILL.md").exists():
        targets.append((d / "SKILL.md", _INJECTION))
    targets += [(f, _INJECTION) for f in d.glob("references/**/*") if f.is_file()]
    # scripts → dangerous code AND injection (a script can carry injected instructions too).
    targets += [(f, _CODE + _INJECTION) for f in d.glob("scripts/**/*") if f.is_file()]
    for f, patterns in targets:
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
            files += 1
            for fd in _scan_text(text, patterns):
                fd["file"] = str(f.relative_to(d))
                findings.append(fd)
        except Exception:
            continue
    sev = {f["severity"] for f in findings}
    risk = "block" if "block" in sev else ("warn" if "warn" in sev else "ok")
    return {
        "risk": risk,
        "findings": findings,
        "files_scanned": files,
        "trusted": is_trusted(d),
        "skill_dir": str(d),
    }
