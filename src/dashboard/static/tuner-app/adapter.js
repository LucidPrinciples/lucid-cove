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
    var badge = document.getElementById("tuneProBadge");
    if (badge) badge.hidden = level >= 5;
  }

  function showUpgradeModal() {
    var existing = document.getElementById("lt-pro-overlay");
    if (existing) { existing.remove(); return; }
    var overlay = document.createElement("div");
    overlay.id = "lt-pro-overlay";
    overlay.className = "lt-pro-overlay";
    overlay.onclick = function (e) { if (e.target === overlay) overlay.remove(); };
    overlay.innerHTML =
      '<div class="lt-pro-modal" role="dialog" aria-labelledby="lt-pro-title">' +
        '<button type="button" class="lt-pro-close" aria-label="Close">&times;</button>' +
        '<p class="lt-pro-kicker">Go Deeper</p>' +
        '<h2 id="lt-pro-title">Unlimited Tune</h2>' +
        '<p>Free is one Tune a day. Pro is unlimited Tune — same music, no extra platform.</p>' +
        '<div class="lt-pro-price"><strong>$9</strong><span> /month</span></div>' +
        '<button type="button" class="lt-pro-cta" id="tfUpgradeProBtn">Start Pro</button>' +
        '<p class="lt-pro-fine">Cancel anytime. Instant access. Your music stays free forever.</p>' +
      "</div>";
    overlay.querySelector(".lt-pro-close").onclick = function () { overlay.remove(); };
    overlay.querySelector("#tfUpgradeProBtn").onclick = function () { startTunerProCheckout(); };
    document.body.appendChild(overlay);
  }
  window.showUpgradeModal = showUpgradeModal;

  async function startTunerProCheckout() {
    var btn = document.getElementById("tfUpgradeProBtn");
    if (btn) {
      btn.disabled = true;
      btn.textContent = "Loading...";
    }
    try {
      var email = window.MC.presence && window.MC.presence.email;
      var name = (window.MC.presence && (window.MC.presence.display_name || window.MC.presence.username)) || "";
      if (!email) {
        alert("Please sign in to upgrade.");
        if (btn) { btn.disabled = false; btn.textContent = "Start Pro"; }
        return;
      }
      var ref = null;
      try {
        var refRes = await fetch("/api/account/referral-code", { credentials: "same-origin" });
        var refData = await refRes.json();
        ref = refData.ref || null;
      } catch (e) {}
      var res = await fetch("https://api.lucidcove.org/api/commerce/checkout/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          plan_type: "pro_monthly",
          email: email,
          name: name,
          ref: ref,
          success_url: window.location.origin + "/app?upgraded=pro",
          cancel_url: window.location.origin + "/app",
        }),
      });
      var data = await res.json().catch(function () { return {}; });
      var url = data && data.url;
      if (res.ok && data.success && typeof url === "string" && url.indexOf("https://") === 0) {
        window.location.href = url;
        return;
      }
      alert(data.error || "Unable to start checkout. Please try again.");
    } catch (e) {
      console.error("[tuner-app] Checkout error:", e);
      alert("Unable to connect to payment system. Please try again.");
    }
    if (btn) { btn.disabled = false; btn.textContent = "Start Pro"; }
  }
  window.startTunerProCheckout = startTunerProCheckout;

  window.MCReady = fetch("/api/presence/me", { credentials: "same-origin" })
    .then(function (r) { return r.json(); })
    .then(function (d) {
      applyPresence(d && d.presence);
      return window.MC;
    })
    .catch(function () { return window.MC; });
})();
