(function () {
  function whichFromPath(pathname) {
    const path = (pathname || "/app").replace(/\/$/, "") || "/app";
    if (path === "/playlists") return "playlists";
    if (path === "/deeper") return "deeper";
    if (path === "/tune") return "tune";
    return "app";
  }

  function showPanel(which, { load } = { load: true }) {
    document.querySelectorAll(".top-nav [data-nav]").forEach((a) => {
      a.classList.toggle("on", a.getAttribute("data-nav") === which);
    });
    document.querySelectorAll(".tuner-panel").forEach((el) => {
      el.hidden = el.id !== "panel-" + which;
    });
    window.activeTab = which;
    if (!load || which === "app") return;
    // Do not wait on /api/presence/me. That fetch is MCReady, and a slow
    // response left this pane on "Loading Tune…" before loadTuneFlow ran.
    if (which === "tune" && typeof loadTuneFlow === "function") {
      loadTuneFlow();
      return;
    }
    const ready = window.MCReady || Promise.resolve();
    ready.then(() => {
      if (which === "playlists" && typeof loadPlaylistsTab === "function") {
        loadPlaylistsTab();
      }
    });
  }

  window.showPanel = showPanel;

  window.switchToTab = function (id) {
    const map = { home: "/app", tune: "/tune", playlists: "/playlists", "go-deeper": "/deeper" };
    const path = map[id];
    if (!path) return;
    if (typeof lchGoto === "function") {
      lchGoto(path);
      return;
    }
    const which = whichFromPath(path);
    if (location.pathname !== path) {
      history.pushState({ tuner: which }, "", path);
    }
    showPanel(which, { load: true });
  };

  const badge = document.getElementById("freq-badge");
  const freqText = document.getElementById("freq-text");
  const overlay = document.getElementById("drop-overlay");
  const dropFrame = document.getElementById("drop-frame");
  const dropTitle = document.getElementById("drop-title");
  let dropPlayerUrl = "https://drop.lucidprinciples.com/";

  async function openDrop() {
    if (typeof _tfFetchLatestDropTuning === "function" && typeof _tfShowTuningDetail === "function") {
      try {
        const dropTune = await _tfFetchLatestDropTuning();
        if (dropTune) {
          if (typeof window._tfNoteFieldDay === "function") window._tfNoteFieldDay();
          await _tfShowTuningDetail(Object.assign({}, dropTune, { _dropHub: true }));
          if (typeof window.paintHomeStreak === "function") window.paintHomeStreak();
          if (badge) badge.setAttribute("aria-expanded", "true");
          return;
        }
      } catch (_) {}
    }
    if (badge) badge.setAttribute("aria-expanded", "true");
  }
  function closeDrop() {
    if (overlay) overlay.hidden = true;
    if (badge) badge.setAttribute("aria-expanded", "false");
  }
  if (badge) badge.addEventListener("click", openDrop);
  const btnClose = document.getElementById("drop-close");
  if (btnClose) btnClose.addEventListener("click", closeDrop);
  if (overlay) {
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) closeDrop();
    });
  }
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && overlay && !overlay.hidden) closeDrop();
  });

  function cleanFrequencyName(raw) {
    const name = String(raw || "").trim();
    if (!name || name === "\u2014" || name === "-" || name === "\u2013") return "";
    return name;
  }

  function readSavedFrequency() {
    try {
      return cleanFrequencyName(localStorage.getItem("lt_latest_frequency"));
    } catch (e) {
      return "";
    }
  }

  function rememberFrequency(name) {
    const clean = cleanFrequencyName(name);
    if (!clean) return;
    try { localStorage.setItem("lt_latest_frequency", clean); } catch (e) {}
  }

  function applyDrop(freq, colors) {
    const name = cleanFrequencyName(freq);
    // No name yet: leave the last known tuning in place. Never paint a default.
    if (!name) return;
    if (freqText) freqText.textContent = name;
    if (dropTitle) dropTitle.textContent = name;
    document.documentElement.setAttribute("data-frequency", name);
    rememberFrequency(name);
    const fromLp = typeof lpFreqColor === "function" ? lpFreqColor(name) : {};
    const c = Object.assign({}, fromLp || {}, colors || {});
    if (c.primary) {
      document.documentElement.style.setProperty("--freq-primary", c.primary);
      document.documentElement.style.setProperty("--freq-secondary", c.secondary || c.primary);
      document.documentElement.style.setProperty("--freq-glow", c.glow || "transparent");
      document.documentElement.style.setProperty("--daily-freq", c.primary);
    }
  }
  window.applyTunerFreqChrome = applyDrop;

  async function bootFreqChrome() {
    // Last visit's latest tuning is already in the pill. Paint it again in
    // case that inline read did not run, then confirm against the Drop.
    const saved = readSavedFrequency();
    if (saved) applyDrop(saved);
    let freq = "";
    let colors = null;
    if (typeof _tfFetchLatestDropTuning === "function") {
      try {
        const drop = await _tfFetchLatestDropTuning();
        if (drop && drop.frequency) freq = drop.frequency;
      } catch (_) {}
    }
    if (!freq) {
      try {
        const r = await fetch("/api/tuning/today", { credentials: "same-origin" });
        const d = r.ok ? await r.json() : {};
        const pkg = (d && d.package) || {};
        freq = pkg.frequency || "";
        colors = pkg.frequency_colors || null;
      } catch (_) {}
    }
    if (freq) applyDrop(freq, colors);
  }
  bootFreqChrome();
})();
