/* Thin adapter so hub Tuner JS runs in the public Lucid Tuner shell. */
(function () {
  if (typeof window.ESC !== "function") {
    window.ESC = function (s) {
      if (s == null) return "";
      return String(s)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
    };
  }
  window.esc = window.esc || window.ESC;
  window.formatDateOnly = window.formatDateOnly || function (iso) {
    if (!iso) return "";
    return String(iso).slice(0, 10);
  };
  window._buildVersion = window._buildVersion || "tuner-app";
  window.activeTab = window.activeTab || "tune";
  var TIER_LEVELS = { free: 0, pro: 5, operator: 10, presence: 20, cove: 30 };
  window.MC = window.MC || {
    isTuner: true,
    tier: { current: "free", level: 0 },
    features: {},
    instance: { name: "Lucid Tuner" },
    tabs: [{ id: "home" }, { id: "tune" }, { id: "playlists" }, { id: "go-deeper" }],
  };
  window.showUpgradeModal = window.showUpgradeModal || function () {};

  function applyPresence(p) {
    if (!p) return;
    var level = TIER_LEVELS[p.tier] !== undefined ? TIER_LEVELS[p.tier] : 0;
    window.MC.presence = p;
    window.MC.tier = {
      current: p.tier || "free",
      level: level,
      has_agent: level >= 20,
      has_team: level >= 30,
    };
    window.MC.isTuner = level < 10;
  }

  window.MCReady = fetch("/api/presence/me", { credentials: "same-origin" })
    .then(function (r) { return r.json(); })
    .then(function (d) {
      applyPresence(d && d.presence);
      return window.MC;
    })
    .catch(function () { return window.MC; });
})();
