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
    const ready = window.MCReady || Promise.resolve();
    ready.then(() => {
      if (which === "tune" && typeof loadTuneFlow === "function") {
        loadTuneFlow();
      } else if (which === "playlists" && typeof loadPlaylistsTab === "function") {
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

  function openDrop() {
    if (dropFrame && (!dropFrame.src || dropFrame.src === "about:blank")) {
      dropFrame.src = dropPlayerUrl;
    }
    if (overlay) overlay.hidden = false;
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

  function applyDrop(freq, colors) {
    const name = freq || "Peace";
    if (freqText) freqText.textContent = name;
    if (dropTitle) dropTitle.textContent = name;
    document.documentElement.setAttribute("data-frequency", name);
    const c = colors || {};
    if (c.primary) {
      document.documentElement.style.setProperty("--freq-primary", c.primary);
      document.documentElement.style.setProperty("--freq-secondary", c.secondary || c.primary);
      document.documentElement.style.setProperty("--freq-glow", c.glow || "transparent");
      document.documentElement.style.setProperty("--daily-freq", c.primary);
    }
  }

  fetch("/api/tuning/today", { credentials: "same-origin" })
    .then((r) => (r.ok ? r.json() : {}))
    .then((d) => {
      const pkg = (d && d.package) || {};
      applyDrop(pkg.frequency || "Peace");
    })
    .catch(() => {});
})();
