"""APP2: one player, Tune load state, latest-tuning pill, Tuning Key line breaks."""

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src/dashboard/static"
SHELL = STATIC / "tuner-app"
FLOW = STATIC / "js/tune-flow.js"
PANEL = STATIC / "js/tuning-panel.js"

# Names the pill must never invent. A saved or fetched tuning may be one of these.
FREQ_NAMES = (
    "Peace", "Clarity", "Momentum", "Trust", "Joy", "Connection",
    "Presence", "Resilience", "Courage", "Gratitude", "Release",
    "Integration", "Boundary",
)


def _no_hardcoded_frequency(text):
    for name in FREQ_NAMES:
        assert f'"{name}"' not in text
        assert f"'{name}'" not in text
        assert f">{name}<" not in text


def _read(path):
    return path.read_text()


def test_pill_shows_last_known_tuning_not_a_default():
    html = _read(SHELL / "index.html")
    tuner = _read(SHELL / "tuner.js")
    start = html.index('id="freq-badge"')
    end = html.index("</script>", start) + len("</script>")
    block = html[start:end]
    _no_hardcoded_frequency(block)
    assert 'id="freq-text"' in block
    assert "per account" in block
    assert "localStorage.getItem" not in block
    assert ">Peace<" not in html
    assert 'data-frequency="Peace"' not in html
    chrome = tuner[tuner.index("function cleanFrequencyName"):tuner.index("bootFreqChrome();")]
    _no_hardcoded_frequency(chrome)
    assert "lt_latest_frequency" in chrome
    assert "_tfFetchLatestDropTuning" in chrome
    assert "if (saved) applyDrop(saved)" in chrome
    assert "if (freq) applyDrop(freq, colors)" in chrome
    assert 'freq || "Peace"' not in tuner
    assert 'freqText.textContent = ""' not in tuner

    dash = _read(STATIC / "index.html")
    h0 = dash.index('id="header-freq"')
    h1 = dash.index("</script>", h0) + len("</script>")
    header = dash[h0:h1]
    _no_hardcoded_frequency(header)
    assert "mdash" not in header.lower()
    assert "per account" in header
    assert "localStorage.getItem" not in header
    core = _read(STATIC / "js/core.js")
    poll = core[core.index("function _ltCleanFrequency"):core.index("async function pollStatus")]
    _no_hardcoded_frequency(poll)
    assert "lt_latest_frequency" in poll
    assert "freq.textContent = '—'" not in poll
    assert 'freq.textContent = "—"' not in poll


def test_tune_limit_waits_for_tier_then_rerenders_once():
    flow = _read(FLOW)
    body = flow[flow.index("async function loadTuneFlow"):flow.index("function _renderTuneLanding")]
    assert "await Promise.race([window.MCReady || Promise.resolve(), new Promise(r => setTimeout(r, 3000))])" in body
    assert body.index("Promise.race") < body.index("_tfFetchPersonalHistory")
    assert "function _tfRefreshLimitChrome" in flow


def test_tune_page_does_not_wait_on_presence_or_history():
    tuner = _read(SHELL / "tuner.js")
    flow = _read(FLOW)
    assert "loadTuneFlow()" in tuner
    show = tuner[tuner.index("function showPanel"):tuner.index("window.showPanel")]
    assert "MCReady" not in show.split("loadTuneFlow()")[0].split("which === \"tune\"")[-1]
    assert "function _tfFetchPersonalHistory" in flow
    assert "AbortController" in flow
    assert "Your last tuning is still loading." in flow
    assert "Couldn't load your tunings. You can still tune." in flow
    assert "/api/tuning/history?limit=100" in flow


def test_key_slash_becomes_a_line_break():
    flow = _read(FLOW)
    panel = _read(PANEL)
    action = _read(SHELL / "action.js")
    team = _read(STATIC / "js/team.js")
    assert flow.count("split(' / ').join('<br>')") >= 3
    assert "split(' / ').join('\\n')" in panel
    assert "whiteSpace = 'pre-line'" in panel
    assert 'function keyBreak' in action
    assert "keyBreak(triad.tuning_key)" in action
    assert "split(' / ').join('<br>')" in team
    css = _read(SHELL / "action.css")
    assert "white-space: pre-line" in css


def test_one_player_keeps_place_and_playlist_open_stays_idle():
    panel = _read(PANEL)
    lists = _read(STATIC / "js/playlists.js")
    house = _read(SHELL / "house.js")
    css = _read(SHELL / "house.css")
    assert "function _otTrackIsLoaded" in panel
    assert "function _otKeepLoaded" in panel
    assert "function _otStableStart" in panel
    assert "function _otAudible" in panel
    setter = panel[panel.index("function otSetPlaylist"):panel.index("function _otDisplayTrackInfo")]
    assert "live && opts.autoplay !== true" in setter
    assert "_otPaintPreview(opts.mountId, tracks, opts)" in setter
    assert "_otHaltBuffers()" in setter
    assert "_otTrackIsLoaded" in setter
    icons = panel[panel.index("function otUpdateIcons"):panel.index("function otStartProgress")]
    assert "_otAudible()" in icons
    toggle = panel[panel.index("function otTogglePlay"):panel.index("function otNext")]
    assert "_otAudible()" in toggle
    assert "[tracks[i], tracks[j]]" not in lists
    assert "function _plShowPlaylist" in lists
    assert "autoplay: true" not in lists
    assert "otSetPlaylist(" not in lists
    assert "startIndex: 0" in lists
    assert "Math.random() * tracks.length" not in lists
    assert "showMiniPlayer()" in house
    assert "grid-row: 1 / -1" in css


_TIER_SCRIPT = r"""
const fs = require("fs");
const vm = require("vm");
const flowPath = process.env.TUNE_FLOW;

function matchDivEnd(html, start) {
  let depth = 0;
  const re = /<\/?div\b[^>]*>/g;
  re.lastIndex = start;
  let m;
  while ((m = re.exec(html))) {
    if (m[0].startsWith("</")) depth -= 1;
    else depth += 1;
    if (depth === 0) return m.index + m[0].length;
  }
  return html.length;
}

function runCase(delayMs) {
  const writes = [];
  const panel = {
    id: "panel-tune",
    _html: "",
    textContent: "",
    get innerHTML() { return this._html; },
    set innerHTML(v) {
      this._html = String(v);
      writes.push(this._html);
    },
    querySelector(sel) {
      const html = this._html || "";
      if (sel === ".tune-flow") return html.includes("tune-flow") ? this : null;
      if (sel === ".tf-tune-slow") return html.includes("tf-tune-slow") ? this : null;
      if (sel === "#tfPlayerMount .ot-player") return {};
      if (sel === ".tf-tune-now-top") {
        const start = html.indexOf('<div class="tf-tune-now-top');
        if (start < 0) return null;
        const self = this;
        return {
          replaceWith(next) {
            const end = matchDivEnd(html, start);
            self.innerHTML = html.slice(0, start) + (next.outerHTML || "") + html.slice(end);
          },
        };
      }
      return null;
    },
    querySelectorAll() { return []; },
  };
  const nodes = {};
  let resolveReady;
  const MCReady = new Promise((resolve) => { resolveReady = resolve; });
  const MC = { tier: { current: "free", level: 0 } };
  const sandbox = {
    console,
    MC,
    MCReady,
    URLSearchParams,
    AbortController,
    Date,
    Math,
    JSON,
    Promise,
    Number,
    String,
    Array,
    Object,
    encodeURIComponent,
    setTimeout(fn, ms, ...args) {
      const t = setTimeout(fn, ms, ...args);
      if (ms !== 3000) t.unref();
      return t;
    },
    clearTimeout,
    setInterval,
    clearInterval,
    location: { search: "" },
    sessionStorage: { getItem() { return null; }, removeItem() {} },
    localStorage: { getItem() { return null; }, setItem() {} },
    ESC(s) { return String(s == null ? "" : s); },
    fetch: async (url) => {
      const u = String(url);
      if (u.includes("/api/tuning/history")) {
        return {
          ok: true,
          json: async () => ({
            sessions: [{
              session_id: "sess_today",
              context: "Working / Focus",
              created_at: new Date().toISOString(),
            }],
          }),
        };
      }
      if (u.includes("/api/tuning/streak")) {
        return { ok: true, json: async () => ({ current_streak: 2 }) };
      }
      return { ok: true, json: async () => ({}) };
    },
    document: {
      getElementById(id) {
        if (id === "panel-tune") return panel;
        if (!nodes[id]) nodes[id] = { id, textContent: "", innerHTML: "", style: {}, remove() {} };
        return nodes[id];
      },
      querySelector() { return null; },
      createElement() {
        const n = { _html: "", className: "" };
        Object.defineProperty(n, "innerHTML", {
          get() { return n._html; },
          set(v) { n._html = String(v); },
        });
        Object.defineProperty(n, "firstElementChild", {
          get() { return { outerHTML: (n._html || "").trim() }; },
        });
        return n;
      },
      body: { appendChild() {} },
    },
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(flowPath, "utf8"), sandbox, { filename: flowPath });
  setTimeout(() => {
    MC.tier = { current: "pro", level: 5 };
    resolveReady();
  }, delayMs).unref();
  return sandbox.loadTuneFlow().then(() => new Promise((resolve) => {
    setTimeout(() => {
      const finalHtml = panel.innerHTML || "";
      resolve({
        lockedWrites: writes.filter((h) => h.includes("Used today") || h.includes("tf-btn-tune-now-locked")).length,
        writes: writes.length,
        final: finalHtml,
      });
    }, delayMs + 80);
  }));
}

Promise.all([runCase(400), runCase(3200)]).then(([early, late]) => {
  process.stdout.write(JSON.stringify({ early, late }));
}).catch((err) => {
  console.error(err && err.stack || err);
  process.exit(1);
});
"""


def test_slow_mcready_pro_with_today_count_is_not_locked():
    proc = subprocess.run(
        ["node", "-e", _TIER_SCRIPT],
        capture_output=True,
        text=True,
        timeout=20,
        env={**os.environ, "TUNE_FLOW": str(FLOW)},
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    # Presence returns inside the cap: the first paint is already Pro.
    assert data["early"]["lockedWrites"] == 0
    assert "Used today" not in data["early"]["final"]
    assert "tf-btn-tune-now-locked" not in data["early"]["final"]
    assert "Unlimited Tune" in data["early"]["final"]
    # Presence returns after the cap: the settled page is Pro, not locked.
    assert "Used today" not in data["late"]["final"]
    assert "tf-btn-tune-now-locked" not in data["late"]["final"]
    assert "Unlimited Tune" in data["late"]["final"]
    assert data["late"]["writes"] >= 2


def test_new_tune_copy_has_no_dash():
    flow = _read(FLOW)
    copy = flow[flow.index("const LT_COPY"):flow.index("window.LT_COPY")]
    assert "—" not in copy and "–" not in copy
    assert "tuneSlow" in copy
    assert "tuneLoadFail" in copy
