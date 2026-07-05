# =============================================================================
# moments_json.py — A13 round 3: resilient moments-JSON extraction.
# =============================================================================
# The moments analysis asks a model for one big JSON object. On a local brain the
# generation cap can cut the output mid-structure — nottington failed with
# `json.loads: Expecting value: line 103 column 3 (char 4290)`, ~4KB of JSON
# truncated mid-object. A strict parse throws away the WHOLE analysis over one
# truncated tail moment.
#
# extract_moments_json() recovers: strip <think>…</think>, isolate the outermost
# object, try a strict parse, then a trailing-comma cleanup, then SALVAGE — walk
# the moments[] array and keep every COMPLETE object, dropping only the truncated
# tail. A truncated response loses one moment, not the whole run.
#
# Pure logic, framework-free, so it's unit-tested without model/langgraph deps.
# =============================================================================
import json
import re

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)
_TRAILING_COMMA = re.compile(r",(\s*[\]}])")


def _strip(content: str) -> str:
    return _THINK.sub("", content or "").strip()


def _salvage_moments(blob: str):
    """Walk the moments[] array and collect every COMPLETE object, discarding a
    truncated final one. Returns {"moments": [...]} or None if nothing whole."""
    i = blob.find('"moments"')
    if i == -1:
        return None
    lb = blob.find("[", i)
    if lb == -1:
        return None
    objs = []
    depth = 0
    start = None
    in_str = False
    esc = False
    for j in range(lb + 1, len(blob)):
        c = blob[j]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
            continue
        if c == "{":
            if depth == 0:
                start = j
            depth += 1
        elif c == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    frag = blob[start:j + 1]
                    try:
                        objs.append(json.loads(frag))
                    except Exception:
                        # A trailing comma inside the last complete object, etc.
                        try:
                            objs.append(json.loads(_TRAILING_COMMA.sub(r"\1", frag)))
                        except Exception:
                            pass
                    start = None
        elif c == "]" and depth == 0:
            break
    if not objs:
        return None
    return {"moments": objs}


def extract_moments_json(content: str):
    """Best-effort parse of a moments JSON blob from an LLM response. Returns a
    dict (with a "moments" list) or None. Resilient to a truncated tail."""
    content = _strip(content)
    if not content:
        return None
    m = re.search(r"\{[\s\S]*\}", content)
    candidate = m.group() if m else content
    # 1. strict
    try:
        return json.loads(candidate)
    except Exception:
        pass
    # 2. trailing-comma cleanup (a common model tic)
    try:
        return json.loads(_TRAILING_COMMA.sub(r"\1", candidate))
    except Exception:
        pass
    # 3. salvage complete moments from a truncated array
    return _salvage_moments(content)


def tail(content: str, n: int = 200) -> str:
    """Last n chars of the raw content — logged on parse failure so the digest
    shows WHY (truncation vs. malformed vs. empty)."""
    return (content or "")[-n:]
