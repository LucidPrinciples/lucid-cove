(function () {
  const panes = {
    app: document.getElementById("pane-app"),
    team: document.getElementById("pane-team"),
    tuner: document.getElementById("pane-tuner"),
    work: document.getElementById("pane-work"),
    observer: document.getElementById("pane-observer"),
  };

  function parsePath(pathname) {
    const path = (pathname || "/app").replace(/\/$/, "") || "/app";
    if (path === "/" || path === "/app") return { area: "app" };
    if (path === "/tune") return { area: "tuner", which: "tune" };
    if (path === "/playlists") return { area: "tuner", which: "playlists" };
    if (path === "/deeper") return { area: "tuner", which: "deeper" };
    return { area: "app" };
  }

  function titleFor(route, pathname) {
    if (route.area === "work") {
      return route.action
        ? "Action Board — Lucid Cove on Hermes"
        : "Lucid Principles — Attention";
    }
    if (route.area === "tuner") {
      if (route.which === "playlists") return "Playlists — Lucid Tuner";
      if (route.which === "deeper") return "Go Deeper — Lucid Tuner";
      return "Lucid Tuner — practice";
    }
    if (route.area === "observer") return "Observer — Lucid Cove on Hermes";
    if (route.area === "app") return "Lucid Tuner — Align Your Broadcast";
    return "Lucid Tuner";
  }

  function applyChrome() {
    document.body.classList.add("house-free");
  }

  function showHouse(pathname, search, push) {
    const route = parsePath(pathname);
    if (panes.app) panes.app.hidden = route.area !== "app";
    if (panes.team) panes.team.hidden = route.area !== "team";
    if (panes.tuner) panes.tuner.hidden = route.area !== "tuner";
    if (panes.work) panes.work.hidden = route.area !== "work";
    if (panes.observer) panes.observer.hidden = route.area !== "observer";
    applyChrome();

    document.querySelectorAll(".top-nav a").forEach((a) => {
      const href = a.getAttribute("href") || "";
      let on = false;
      if (route.area === "team") on = href === "/";
      else if (route.area === "app") on = href === "/app";
      else if (route.area === "tuner") on = href === pathname;
      a.classList.toggle("on", on);
    });

    document.title = titleFor(route, pathname);
    if (push) {
      history.pushState({ house: true }, "", pathname + (search || ""));
    }

    if (route.area === "work" && typeof showBoard === "function") {
      showBoard(route.action, false);
    }
    if (route.area === "tuner" && typeof window.showPanel === "function") {
      window.showPanel(route.which, { load: true });
    }
    if (route.area === "observer" && typeof loadObserver === "function") {
      loadObserver();
    }
  }

  window.lchGoto = function (href, push) {
    const url = new URL(href, location.origin);
    if (url.origin !== location.origin) {
      window.open(href, "_blank", "noopener");
      return;
    }
    showHouse(url.pathname, url.search, push !== false);
  };

  function isHousePath(pathname) {
    const path = pathname || "/";
    return (
      path === "/" ||
      path === "/app" ||
      path === "/work" ||
      path === "/tune" ||
      path === "/playlists" ||
      path === "/deeper" ||
      path === "/tools" ||
      path.startsWith("/tools/") ||
      path.startsWith("/observer/")
    );
  }

  document.addEventListener("click", (e) => {
    const a = e.target.closest("a");
    if (!a || a.target === "_blank" || a.hasAttribute("download")) return;
    const href = a.getAttribute("href") || "";
    if (!href.startsWith("/") || href.startsWith("//")) return;
    if (href.startsWith("/jules") || href.startsWith("/static")) return;
    let url;
    try {
      url = new URL(href, location.origin);
    } catch (_) {
      return;
    }
    if (!isHousePath(url.pathname)) return;
    e.preventDefault();
    showHouse(url.pathname, url.search, true);
  });

  function mpLookupColor(label, title) {
    const text = String(label || "").trim();
    const name = String(title || "").trim();
    if (typeof window._otFreqColor === "string" && window._otFreqColor) {
      return window._otFreqColor;
    }
    if (typeof LP !== "undefined" && LP.freq && typeof lpColor === "function") {
      const freqKey = Object.keys(LP.freq).find((k) => {
        const re = new RegExp("(^|\\s)" + k + "(\\s|$)", "i");
        return re.test(text);
      });
      if (freqKey) return lpColor(freqKey);
    }
    if (typeof lpSignalColor === "function" && /signal|ground|clear|open|rise|raw|bright|drive/i.test(text)) {
      return lpSignalColor(text);
    }
    if (typeof lpPrinciple === "function" && typeof lpPrincipleColor === "function" && name && lpPrinciple(name)) {
      return lpPrincipleColor(name);
    }
    return "";
  }

  function paintMiniPlayer() {
    const bar = document.getElementById("miniPlayer");
    const freqEl = document.getElementById("mpFreq");
    const titleEl = document.getElementById("mpTitle");
    if (!bar || !freqEl) return;
    const color = mpLookupColor(freqEl.textContent, titleEl && titleEl.textContent);
    if (!color) {
      bar.style.removeProperty("--mp-freq-color");
      document.body.style.removeProperty("--mp-freq-color");
      return;
    }
    bar.style.setProperty("--mp-freq-color", color);
    document.body.style.setProperty("--mp-freq-color", color);
    freqEl.style.color = color;
  }

  const mini = document.getElementById("miniPlayer");
  if (mini) {
    mini.addEventListener("click", (e) => {
      if (e.target.closest(".mp-btn")) return;
      if (typeof _otSyncQueueToAudio === "function") _otSyncQueueToAudio();
      const dest = typeof _otSource !== "undefined" && _otSource === "playlist"
        ? "/playlists"
        : "/tune";
      showHouse(dest, "", true);
    });
    if (typeof MutationObserver === "function") {
      new MutationObserver(paintMiniPlayer).observe(mini, {
        childList: true,
        characterData: true,
        subtree: true,
        attributes: true,
        attributeFilter: ["class"],
      });
    }
    paintMiniPlayer();
  }

  window.addEventListener("popstate", () => {
    showHouse(location.pathname, location.search, false);
  });

  document.addEventListener(
    "play",
    (e) => {
      const t = e.target;
      if (!t || t.tagName !== "AUDIO") return;
      const frame = document.getElementById("drop-frame");
      if (frame && frame.src && frame.src !== "about:blank") {
        frame.removeAttribute("src");
        frame.src = "about:blank";
      }
    },
    true
  );

  function escapeCoachText(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  const COACH_SKIM = [
    ["heart-brain coherence", "active"],
    ["constructive interference", "active"],
    ["threat detection", "old"],
    ["kinetic interrupt", "action"],
    ["reception mode", "active"],
    ["frequency", "active"],
    ["decoder", "action"],
    ["static", "old"],
  ];

  function skimCoachHtml(raw) {
    let html = escapeCoachText(raw);
    COACH_SKIM.forEach(([phrase, kind]) => {
      const re = new RegExp(phrase.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi");
      html = html.replace(
        re,
        (m) => '<span class="tf-skim-' + kind + '">' + m + "</span>"
      );
    });
    return html;
  }

  function paintCoachSkim(root) {
    const scope = root || document;
    scope.querySelectorAll(".tf-coaching-text, .tf-modal-coaching").forEach((el) => {
      if (el.getAttribute("data-skim") === "1") return;
      el.setAttribute("data-skim", "1");
      el.innerHTML = skimCoachHtml(el.textContent || "");
    });
  }

  const tunerPane = document.getElementById("pane-tuner");
  if (tunerPane && typeof MutationObserver === "function") {
    new MutationObserver(() => paintCoachSkim(tunerPane)).observe(tunerPane, {
      childList: true,
      subtree: true,
    });
  }
  paintCoachSkim(document);

  const helpOverlay = document.getElementById("help-overlay");
  const helpVideo = document.getElementById("help-video");
  const helpVideoSection = document.getElementById("help-video-section");

  function closeHelp() {
    if (helpVideo && !helpVideo.paused) {
      try { helpVideo.pause(); } catch (_) {}
    }
    if (helpOverlay) helpOverlay.hidden = true;
  }

  function openHelp() {
    if (!helpOverlay) return;
    helpOverlay.hidden = false;
  }

  document.getElementById("help-btn")?.addEventListener("click", openHelp);
  document.getElementById("help-close")?.addEventListener("click", closeHelp);
  helpOverlay?.addEventListener("click", (e) => {
    if (e.target.id === "help-overlay") closeHelp();
  });
  document.getElementById("help-start-here")?.addEventListener("click", () => {
    if (helpVideoSection) helpVideoSection.hidden = false;
    if (helpVideo) {
      helpVideo.play().catch(() => {});
    }
    helpVideoSection?.scrollIntoView({ behavior: "smooth", block: "start" });
  });
  helpOverlay?.addEventListener("click", (e) => {
    const link = e.target.closest(".help-nav-link[data-section]");
    if (!link || !helpOverlay.contains(link)) return;
    e.preventDefault();
    const section = document.getElementById(link.getAttribute("data-section") || "");
    if (section) section.scrollIntoView({ behavior: "smooth", block: "start" });
  });

  const SETTINGS_KEY = "lch_house_settings";
  const ALLOWED_SIGNALS = ["Ground", "Clear", "Open", "Rise", "Raw", "Bright", "Drive"];
  const SIGNAL_COLORS = {
    Ground: "#5ce1e6",
    Clear: "#a0ebff",
    Open: "#e0b0ff",
    Rise: "#ff6b5c",
    Raw: "#ff8c00",
    Bright: "#ffd700",
    Drive: "#20b2aa",
  };
  const ALLOWED_MIRRORS = ["scripture-tpt", "music-mirror", "tao-mirror"];
  const DEFAULT_MIRRORS = ["scripture-tpt", "music-mirror"];
  const ALLOWED_STREAMING = ["youtube", "spotify", "apple"];
  const DEFAULT_STREAMING = "youtube";
  const YT_MIRROR_PLAYLISTS = {
    peace: "PL5H3zJAeU30PkndXHCdOj2i0baf5biSiN",
    clarity: "PL5H3zJAeU30OiRi1E_IF8g_ON0ad-SAqv",
    momentum: "PL5H3zJAeU30Pkvj-CTLh8E1HbgmKGhijx",
    trust: "PL5H3zJAeU30OQhN7cyZVwJycL6C8TKbmV",
    joy: "PL5H3zJAeU30OLN20Tjmz7wi9_IZVPdZt7",
    connection: "PL5H3zJAeU30PXdOrSIrtFIAzrH-tHFozT",
    presence: "PL5H3zJAeU30M39Zr0P-SYjJbcpI_SxH89",
    resilience: "PL5H3zJAeU30PqlP7wvIixR8KSvGPmsfxM",
    courage: "PL5H3zJAeU30NOchBxs6kI6ZoKxMh6hXCw",
    gratitude: "PL5H3zJAeU30PHTbD_UlfQWgRYNy4N1vHK",
    release: "PL5H3zJAeU30MwdK_4MIikD7j21ixmw8eN",
    integration: "PL5H3zJAeU30PVrZCXFmK-ZLCE_f3zKnFR",
    boundary: "PL5H3zJAeU30N7W42clfexKbEmHTKGf3L-",
  };
  const settingsOverlay = document.getElementById("settings-overlay");
  const musicOverlay = document.getElementById("music-player-overlay");
  const reflectModal = document.getElementById("reflectModal");
  let musicListenCtx = null;
  let dropMirrorPayload = null;

  function closeOverlay(el) {
    if (el) el.hidden = true;
  }

  function openOverlay(el) {
    if (!el) return;
    el.hidden = false;
  }

  function loadHouseSettings() {
    try {
      const raw = localStorage.getItem(SETTINGS_KEY);
      const data = raw ? JSON.parse(raw) : {};
      return data && typeof data === "object" && !Array.isArray(data) ? data : {};
    } catch (_) {
      return {};
    }
  }

  function saveHouseSettings(next) {
    const cur = loadHouseSettings();
    const merged = Object.assign({ v: 1 }, cur, next);
    try {
      localStorage.setItem(SETTINGS_KEY, JSON.stringify(merged));
    } catch (_) {
      return null;
    }
    return merged;
  }

  function normalizeSignals(list) {
    const allowed = new Set(ALLOWED_SIGNALS);
    const out = [];
    (Array.isArray(list) ? list : []).forEach((raw) => {
      const name = String(raw || "").replace(/_Signal$/i, "");
      const titled = name ? name.charAt(0).toUpperCase() + name.slice(1).toLowerCase() : "";
      if (allowed.has(titled) && out.indexOf(titled) === -1) out.push(titled);
    });
    return out;
  }

  function normalizeMirrors(list) {
    const allowed = new Set(ALLOWED_MIRRORS);
    const out = [];
    (Array.isArray(list) ? list : []).forEach((raw) => {
      const id = String(raw || "").trim().toLowerCase();
      if (allowed.has(id) && out.indexOf(id) === -1) out.push(id);
    });
    return out;
  }

  function normalizeStreaming(value) {
    const id = String(value || "").trim().toLowerCase();
    return ALLOWED_STREAMING.indexOf(id) >= 0 ? id : DEFAULT_STREAMING;
  }

  function emailLooksOk(value) {
    if (!value) return true;
    return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value);
  }

  function applyHouseSettingsToMC() {
    const s = loadHouseSettings();
    window.MC = window.MC || { features: {} };
    MC.features = MC.features || {};
    MC.features.excluded_signals = normalizeSignals(s.excludedSignals);
    const mirrors = Object.prototype.hasOwnProperty.call(s, "mirrors")
      ? normalizeMirrors(s.mirrors)
      : DEFAULT_MIRRORS.slice();
    MC.features.mirror = mirrors.length > 0;
    MC.features.mirror_sources = mirrors.join(",");
    MC.features.streaming_service = normalizeStreaming(s.streamingService);
  }

  function paintSignalToggle(btn) {
    if (!btn) return;
    const on = btn.getAttribute("aria-checked") !== "false";
    const color = SIGNAL_COLORS[btn.getAttribute("data-signal")] || "var(--accent, #5ce1e6)";
    btn.style.setProperty("--toggle-on", color);
    btn.classList.toggle("is-on", on);
    btn.setAttribute("aria-checked", on ? "true" : "false");
  }

  function collectExcludedSignals() {
    const excluded = [];
    document.querySelectorAll("#settings-signal-filters .settings-toggle[data-signal]").forEach((btn) => {
      if (btn.getAttribute("aria-checked") === "false") excluded.push(btn.getAttribute("data-signal"));
    });
    return normalizeSignals(excluded);
  }

  function collectMirrorsInOrder() {
    const ids = [];
    document.querySelectorAll("#settings-mirrors .settings-mirror-row").forEach((row) => {
      const box = row.querySelector('input[name="tuning-mirror"]');
      if (box && box.checked) ids.push(box.value);
    });
    return normalizeMirrors(ids);
  }

  function fillSettingsForm() {
    const s = loadHouseSettings();
    const excluded = new Set(normalizeSignals(s.excludedSignals));
    document.querySelectorAll("#settings-signal-filters .settings-toggle[data-signal]").forEach((btn) => {
      const on = !excluded.has(btn.getAttribute("data-signal"));
      btn.setAttribute("aria-checked", on ? "true" : "false");
      paintSignalToggle(btn);
    });
    const mirrors = Object.prototype.hasOwnProperty.call(s, "mirrors")
      ? normalizeMirrors(s.mirrors)
      : DEFAULT_MIRRORS.slice();
    const enabled = new Set(mirrors);
    const list = document.getElementById("settings-mirrors");
    if (list) {
      const rows = Array.from(list.querySelectorAll(".settings-mirror-row"));
      rows.sort((a, b) => {
        const ia = mirrors.indexOf(a.getAttribute("data-mirror-id"));
        const ib = mirrors.indexOf(b.getAttribute("data-mirror-id"));
        const sa = ia === -1 ? 99 : ia;
        const sb = ib === -1 ? 99 : ib;
        return sa - sb;
      });
      rows.forEach((row) => list.appendChild(row));
    }
    document.querySelectorAll('input[name="tuning-mirror"]').forEach((box) => {
      box.checked = enabled.has(box.value);
    });
    const streaming = document.getElementById("settings-streaming-service");
    if (streaming) streaming.value = normalizeStreaming(s.streamingService);
    const status = document.getElementById("settings-save-status");
    if (status) status.textContent = "";
  }

  function collectChecked(name) {
    return Array.from(document.querySelectorAll('input[name="' + name + '"]:checked')).map((el) => el.value);
  }

  function mirrorFreqColor(freq) {
    const upper = String(freq || "").toUpperCase();
    if (typeof OT_FREQ_COLORS !== "undefined" && OT_FREQ_COLORS[upper]) return OT_FREQ_COLORS[upper];
    return "var(--mp-freq-color, var(--accent, #5ce1e6))";
  }

  function safeSpotifyId(raw) {
    const id = String(raw || "");
    return /^[A-Za-z0-9]{10,32}$/.test(id) ? id : "";
  }

  function safeYoutubeId(raw) {
    const id = String(raw || "");
    return /^[A-Za-z0-9_-]{11}$/.test(id) ? id : "";
  }

  function safeYtPlaylist(raw) {
    const id = String(raw || "");
    return /^PL[A-Za-z0-9_-]{10,}$/.test(id) ? id : "";
  }

  function pauseHouseAudio() {
    try {
      if (typeof otAudio !== "undefined" && otAudio && !otAudio.paused) otAudio.pause();
    } catch (_) {}
  }

  function closeReflect() {
    if (reflectModal) reflectModal.style.display = "none";
  }

  function openReflect(mirror, payload) {
    if (!reflectModal) return;
    const featuredHost = payload || dropMirrorPayload || {};
    const color = mirrorFreqColor(featuredHost.frequency);
    const title = document.getElementById("reflectTitle");
    const canon = document.getElementById("reflectCanon");
    const body = document.getElementById("reflectBody");
    if (title) {
      title.textContent = String(featuredHost.principle || (mirror && mirror.mirror_name) || "Reflect");
      title.style.color = color;
    }
    if (canon) {
      canon.textContent = String((mirror && mirror.mirror_name) || "") +
        (mirror && mirror.canon ? " — " + mirror.canon : "");
    }
    if (body) {
      body.replaceChildren();
      const entries = (mirror && (mirror.all_entries || mirror.entries)) || [];
      const list = entries.length ? entries : [(mirror && mirror.featured) || {}];
      list.forEach((entry) => {
        const wrap = document.createElement("div");
        wrap.className = "reflect-entry";
        wrap.style.borderLeftColor = color;
        const ref = document.createElement("div");
        ref.className = "reflect-ref";
        ref.style.color = color;
        ref.textContent = String((entry && entry.ref) || "");
        const passage = document.createElement("div");
        passage.className = "reflect-passage";
        passage.textContent = String((entry && entry.text) || "");
        const thread = document.createElement("div");
        thread.className = "reflect-thread";
        thread.textContent = String((entry && entry.thread) || "");
        wrap.appendChild(ref);
        wrap.appendChild(passage);
        if (thread.textContent) wrap.appendChild(thread);
        body.appendChild(wrap);
      });
    }
    reflectModal.style.display = "flex";
  }

  function clearMusicEmbed() {
    const host = document.getElementById("music-embed-container");
    if (host) host.replaceChildren();
    const link = document.getElementById("music-playlist-link");
    if (link) {
      link.hidden = true;
      link.removeAttribute("href");
    }
  }

  function closeMusicPlayer() {
    clearMusicEmbed();
    if (musicOverlay) musicOverlay.hidden = true;
  }

  function playlistFor(service, frequency) {
    if (service !== "youtube") return "";
    const key = String(frequency || "").toLowerCase().replace(/\s+/g, "_");
    return safeYtPlaylist(YT_MIRROR_PLAYLISTS[key] || "");
  }

  function fillMusicEmbed(service, ctx) {
    const host = document.getElementById("music-embed-container");
    const link = document.getElementById("music-playlist-link");
    if (!host) return;
    host.replaceChildren();
    const spotifyId = safeSpotifyId(ctx.spotifyId);
    const youtubeId = safeYoutubeId(ctx.youtubeId);
    const playlistId = playlistFor(service, ctx.frequency);
    const frame = document.createElement("iframe");
    frame.allow = "autoplay; clipboard-write; encrypted-media; fullscreen; picture-in-picture";
    frame.setAttribute("allowfullscreen", "");
    frame.loading = "lazy";
    let src = "";
    let height = "420";
    if (service === "spotify" && spotifyId) {
      src = "https://open.spotify.com/embed/track/" + spotifyId + "?utm_source=generator&theme=0";
      height = "152";
    } else if (service === "youtube" && youtubeId && playlistId) {
      src = "https://www.youtube.com/embed/" + youtubeId + "?list=" + playlistId + "&autoplay=1";
    } else if (service === "youtube" && youtubeId) {
      src = "https://www.youtube.com/embed/" + youtubeId + "?autoplay=1";
    } else if (service === "youtube" && playlistId) {
      src = "https://www.youtube.com/embed/videoseries?list=" + playlistId + "&autoplay=1";
    }
    if (src) {
      frame.src = src;
      frame.style.height = height + "px";
      host.appendChild(frame);
    } else {
      const fallback = document.createElement("div");
      fallback.className = "music-player-fallback";
      const label = { youtube: "YouTube Music", spotify: "Spotify", apple: "Apple Music" }[service] || "Music";
      fallback.textContent = label + " embed coming soon. Search for " +
        [ctx.artist, ctx.title].filter(Boolean).join(" — ");
      host.appendChild(fallback);
    }
    if (link) {
      if (service === "youtube" && playlistId) {
        link.href = "https://music.youtube.com/playlist?list=" + playlistId;
        link.hidden = false;
      } else if (service === "spotify" && spotifyId) {
        link.href = "https://open.spotify.com/track/" + spotifyId;
        link.hidden = false;
      } else {
        link.hidden = true;
        link.removeAttribute("href");
      }
    }
  }

  function openMusicPlayer(frequency, artist, title, spotifyId, youtubeId) {
    applyHouseSettingsToMC();
    const service = normalizeStreaming(MC.features && MC.features.streaming_service);
    musicListenCtx = {
      frequency: String(frequency || ""),
      artist: String(artist || ""),
      title: String(title || ""),
      spotifyId: String(spotifyId || ""),
      youtubeId: String(youtubeId || ""),
    };
    pauseHouseAudio();
    const freqEl = document.getElementById("music-player-freq");
    if (freqEl) {
      const freq = musicListenCtx.frequency;
      freqEl.textContent = freq ? freq + " Frequency" : "Music Mirror";
    }
    const select = document.getElementById("music-service-select");
    if (select) select.value = service;
    fillMusicEmbed(service, musicListenCtx);
    if (musicOverlay) musicOverlay.hidden = false;
  }

  window._openMusicPlayer = openMusicPlayer;

  function renderDropMirrors(rows, payload) {
    const host = document.getElementById("drop-mirrors");
    if (!host) return;
    host.replaceChildren();
    dropMirrorPayload = payload || null;
    const list = Array.isArray(rows) ? rows : [];
    if (!list.length) {
      host.hidden = true;
      return;
    }
    const color = mirrorFreqColor(payload && payload.frequency);
    list.forEach((m) => {
      const featured = (m && m.featured) || {};
      const card = document.createElement("div");
      card.className = "drop-mirror-card";
      card.style.borderLeftColor = color;
      const header = document.createElement("div");
      header.className = "drop-mirror-header";
      const title = document.createElement("div");
      title.className = "drop-mirror-name";
      title.textContent = String((m && m.mirror_name) || "");
      const cta = document.createElement("button");
      cta.type = "button";
      cta.className = "drop-mirror-cta";
      cta.style.color = color;
      if ((m && m.mirror_type) === "music") {
        cta.textContent = "Listen →";
        cta.addEventListener("click", () => {
          openMusicPlayer(
            payload && payload.frequency,
            featured.artist,
            featured.title,
            featured.spotify_id,
            featured.youtube_id
          );
        });
      } else {
        cta.textContent = "Reflect →";
        cta.addEventListener("click", () => openReflect(m, payload));
      }
      header.appendChild(title);
      header.appendChild(cta);
      const body = document.createElement("div");
      body.className = "drop-mirror-text";
      body.textContent = String(featured.text || "");
      const ref = document.createElement("span");
      ref.className = "drop-mirror-ref";
      ref.style.color = color;
      const artistTitle = [featured.artist, featured.title].filter(Boolean).join(" — ");
      ref.textContent = String(featured.ref || artistTitle || "");
      card.appendChild(header);
      if (featured.text) card.appendChild(body);
      if (ref.textContent) card.appendChild(ref);
      host.appendChild(card);
    });
    host.hidden = false;
  }

  let dropMirrorsGen = 0;
  async function loadDropMirrors() {
    const host = document.getElementById("drop-mirrors");
    if (!host) return;
    const gen = ++dropMirrorsGen;
    applyHouseSettingsToMC();
    const sources = (MC.features && MC.features.mirror_sources) || "";
    if (!sources) {
      if (gen === dropMirrorsGen) renderDropMirrors([]);
      return;
    }
    try {
      const resp = await fetch("/api/mirrors/today?sources=" + encodeURIComponent(sources));
      if (gen !== dropMirrorsGen) return;
      if (!resp.ok) throw new Error("mirrors");
      const data = await resp.json();
      if (gen !== dropMirrorsGen) return;
      renderDropMirrors(data && data.has_mirror ? data.mirrors : [], data);
    } catch (_) {
      if (gen === dropMirrorsGen) renderDropMirrors([]);
    }
  }

  function fillAccountFromPresence() {
    const ready = window.MCReady || Promise.resolve();
    ready.then(() => {
      const p = (window.MC && MC.presence) || {};
      const name = document.getElementById("settings-display-name");
      const handle = document.getElementById("settings-username");
      const email = document.getElementById("settings-email");
      const hint = document.getElementById("settings-handle-hint");
      if (name && p.display_name) name.value = String(p.display_name);
      if (handle && p.username) handle.value = String(p.username).replace(/^@/, "");
      if (email && p.email) email.value = String(p.email);
      const locked = !!(p.username && !p.needs_username);
      if (handle) {
        handle.readOnly = locked;
        handle.setAttribute("aria-readonly", locked ? "true" : "false");
      }
      if (hint) {
        hint.textContent = locked
          ? "Set at signup. This is your Lucid Tuner account."
          : "Pick a handle. This becomes your Lucid Tuner account.";
      }
    });
  }

  function loadAffiliate() {
    const host = document.getElementById("affiliates-content");
    if (!host) return;
    fetch("/api/account/affiliates", { credentials: "same-origin" })
      .then((r) => r.json().catch(() => ({})))
      .then((data) => {
        host.replaceChildren();
        if (data && data.signed_in === false) {
          host.textContent = "Sign in to view your referral link.";
          return;
        }
        const code = data && data.referral_code;
        if (!code || !/^[A-Za-z0-9_-]{4,40}$/.test(String(code))) {
          host.textContent = "Your referral code is being set up — refresh in a moment.";
          return;
        }
        const url = "https://app.lucidcove.org/r/" + code + "?to=https://app.lucidtuner.com";
        const field = document.createElement("div");
        field.className = "settings-field";
        const label = document.createElement("span");
        label.className = "settings-label";
        label.textContent = "Referral link";
        const row = document.createElement("div");
        row.className = "settings-copy-row";
        const input = document.createElement("input");
        input.className = "settings-input";
        input.readOnly = true;
        input.value = url;
        input.setAttribute("aria-label", "Referral link");
        const copy = document.createElement("button");
        copy.type = "button";
        copy.className = "settings-copy-btn";
        copy.textContent = "Copy";
        copy.addEventListener("click", () => {
          navigator.clipboard.writeText(url).then(() => {
            copy.textContent = "Copied";
            setTimeout(() => { copy.textContent = "Copy"; }, 1500);
          }).catch(() => {});
        });
        row.appendChild(input);
        row.appendChild(copy);
        field.appendChild(label);
        field.appendChild(row);
        host.appendChild(field);
      })
      .catch(() => {
        host.textContent = "Couldn’t load referral link.";
      });
  }

  function openSettings() {
    fillSettingsForm();
    fillAccountFromPresence();
    loadAffiliate();
    openOverlay(settingsOverlay);
  }

  applyHouseSettingsToMC();

  const dropOverlay = document.getElementById("drop-overlay");
  if (dropOverlay && typeof MutationObserver === "function") {
    new MutationObserver(() => {
      if (!dropOverlay.hidden) loadDropMirrors();
    }).observe(dropOverlay, { attributes: true, attributeFilter: ["hidden"] });
  }

  document.getElementById("settings-btn")?.addEventListener("click", openSettings);
  document.getElementById("settings-close")?.addEventListener("click", () => closeOverlay(settingsOverlay));
  settingsOverlay?.addEventListener("click", (e) => {
    if (e.target.id === "settings-overlay") closeOverlay(settingsOverlay);
  });
  document.getElementById("settings-save")?.addEventListener("click", () => {
    const streamingEl = document.getElementById("settings-streaming-service");
    const status = document.getElementById("settings-save-status");
    const saved = saveHouseSettings({
      excludedSignals: collectExcludedSignals(),
      mirrors: collectMirrorsInOrder(),
      streamingService: normalizeStreaming(streamingEl && streamingEl.value),
    });
    if (!saved) {
      if (status) status.textContent = "Couldn’t save on this device.";
      return;
    }
    applyHouseSettingsToMC();
    if (status) status.textContent = "Saved on this device.";
  });

  document.getElementById("settings-signal-filters")?.addEventListener("click", (e) => {
    const btn = e.target.closest(".settings-toggle[data-signal]");
    if (!btn) return;
    const turningOff = btn.getAttribute("aria-checked") !== "false";
    if (turningOff) {
      const othersOn = Array.from(
        document.querySelectorAll("#settings-signal-filters .settings-toggle[data-signal]")
      ).filter((el) => el !== btn && el.getAttribute("aria-checked") !== "false");
      if (othersOn.length === 0 && ALLOWED_SIGNALS.length) return;
    }
    btn.setAttribute("aria-checked", turningOff ? "false" : "true");
    paintSignalToggle(btn);
  });

  (function initMirrorDrag() {
    const list = document.getElementById("settings-mirrors");
    if (!list) return;
    let dragRow = null;
    list.addEventListener("dragstart", (e) => {
      const row = e.target.closest(".settings-mirror-row");
      if (!row || !list.contains(row)) return;
      dragRow = row;
      row.classList.add("is-dragging");
      if (e.dataTransfer) e.dataTransfer.effectAllowed = "move";
    });
    list.addEventListener("dragover", (e) => {
      e.preventDefault();
      const over = e.target.closest(".settings-mirror-row");
      if (!dragRow || !over || over === dragRow) return;
      const rect = over.getBoundingClientRect();
      const before = e.clientY < rect.top + rect.height / 2;
      list.insertBefore(dragRow, before ? over : over.nextSibling);
    });
    list.addEventListener("drop", (e) => e.preventDefault());
    list.addEventListener("dragend", () => {
      if (dragRow) dragRow.classList.remove("is-dragging");
      dragRow = null;
    });
  })();

  document.getElementById("settings-account-save")?.addEventListener("click", () => {
    const name = document.getElementById("settings-display-name");
    const handleEl = document.getElementById("settings-username");
    const emailEl = document.getElementById("settings-email");
    const status = document.getElementById("settings-account-status");
    const displayName = String((name && name.value) || "").trim().slice(0, 80);
    const username = String((handleEl && handleEl.value) || "").trim().replace(/^@/, "").slice(0, 40);
    const email = String((emailEl && emailEl.value) || "").trim().slice(0, 120);
    const handleLocked = !!(handleEl && handleEl.readOnly);
    if (!emailLooksOk(email)) {
      if (status) status.textContent = "Enter a valid email, or leave it blank.";
      return;
    }
    if (status) status.textContent = "Saving…";
    const payload = { display_name: displayName, email: email };
    if (!handleLocked) payload.username = username;
    fetch("/api/presence/me", {
      method: "PATCH",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })
      .then((r) => r.json().then((d) => ({ ok: r.ok, d })))
      .then(({ ok, d }) => {
        if (!ok) {
          if (status) status.textContent = (d && (d.detail || d.error)) || "Couldn’t save account.";
          return;
        }
        if (window.MC) {
          const next = { display_name: displayName, email: email };
          if (!handleLocked) next.username = username;
          MC.presence = Object.assign({}, MC.presence || {}, next);
        }
        if (status) status.textContent = "Saved to your account.";
      })
      .catch(() => {
        if (status) status.textContent = "Couldn’t save account.";
      });
  });

  const syncHouseKind = () => {
    const kind = (document.querySelector('input[name="settings-house-kind"]:checked') || {}).value;
    const cove = document.getElementById("settings-steps-cove");
    const hermes = document.getElementById("settings-steps-hermes");
    if (cove) cove.hidden = kind !== "cove";
    if (hermes) hermes.hidden = kind !== "hermes";
  };
  document.querySelectorAll('input[name="settings-house-kind"]').forEach((el) => {
    el.addEventListener("change", syncHouseKind);
  });
  syncHouseKind();

  document.getElementById("settings-get-connect-key")?.addEventListener("click", () => {
    const status = document.getElementById("settings-connect-key-status");
    const input = document.getElementById("settings-connect-key-value");
    const wrap = document.getElementById("settings-connect-key-wrap");
    const copyBtn = document.getElementById("settings-copy-connect-key");
    if (status) status.textContent = "Minting…";
    fetch("/api/account/self-host-token", {
      method: "POST",
      credentials: "same-origin",
    })
      .then((r) => r.json().then((d) => ({ ok: r.ok, d })))
      .then(({ ok, d }) => {
        if (!ok || !d.ok) {
          if (status) status.textContent = (d && (d.reason || d.error || d.detail)) || "Sign in to get a connect key.";
          return;
        }
        const key = d.connect_key || ((d.handle || "") + ":" + (d.token || d.operator_token || ""));
        if (input) input.value = key;
        if (wrap) wrap.hidden = false;
        if (copyBtn) copyBtn.hidden = false;
        if (status) status.textContent = "Copy this once. It will not be shown again.";
      })
      .catch(() => {
        if (status) status.textContent = "Couldn’t mint a connect key.";
      });
  });
  document.getElementById("settings-copy-connect-key")?.addEventListener("click", () => {
    const input = document.getElementById("settings-connect-key-value");
    const copyBtn = document.getElementById("settings-copy-connect-key");
    const val = (input && input.value) || "";
    if (!val) return;
    navigator.clipboard.writeText(val).then(() => {
      if (copyBtn) copyBtn.textContent = "Copied";
      setTimeout(() => { if (copyBtn) copyBtn.textContent = "Copy"; }, 1500);
    }).catch(() => {});
  });

  document.getElementById("reflect-close")?.addEventListener("click", closeReflect);
  reflectModal?.addEventListener("click", (e) => {
    if (e.target.id === "reflectModal") closeReflect();
  });
  document.getElementById("music-player-close")?.addEventListener("click", closeMusicPlayer);
  musicOverlay?.addEventListener("click", (e) => {
    if (e.target.id === "music-player-overlay") closeMusicPlayer();
  });
  document.getElementById("music-service-select")?.addEventListener("change", (e) => {
    const service = normalizeStreaming(e.target.value);
    saveHouseSettings({ streamingService: service });
    applyHouseSettingsToMC();
    const settingsSelect = document.getElementById("settings-streaming-service");
    if (settingsSelect) settingsSelect.value = service;
    if (musicListenCtx) fillMusicEmbed(service, musicListenCtx);
  });

  showHouse(location.pathname, location.search, false);
})();

