// =============================================================================
// connect.js — "Connect" channel in the Chat tab (#137 Phase 3)
// =============================================================================
// The real Matrix client for the Connect channel, built on matrix-js-sdk (the
// vendored bundle at /static/js/vendor/matrix-js-sdk.min.js, lazy-loaded on first
// open). This REPLACES the 2026-06-18 hand-rolled fetch+poll spike.
//
// Scalable design (see Reference/matrix-layer-architecture.md):
//   - Engine:  matrix-js-sdk — real /sync (live), room/space state. No polling.
//   - Auth:    SSO via /api/matrix/token — no login box; opens authenticated.
//   - Structure: Matrix Spaces — Coves/Havens render as a grouped tree.
//   - UI:      native, IN the chat panel (hides the agent chat area, keeps the
//              tab row). No body overlay.
//   - Agents:  server-side bots (separate, Phase 4) — this is the human surface.
// Encryption is OFF at launch (rooms created unencrypted); the SDK can enable it
// later without touching this client.
// =============================================================================
(function () {
  const TOKEN_ENDPOINT = '/api/matrix/token';
  const SDK_URL = (function () {
    // Derive the vendor path from this script's own src so it works under any mount.
    const me = document.currentScript && document.currentScript.src;
    if (me) return me.replace(/connect\.js(\?.*)?$/, 'vendor/matrix-js-sdk.min.js');
    return '/static/js/vendor/matrix-js-sdk.min.js';
  })();

  let client = null;          // the live matrix-js-sdk client (kept alive once started)
  let started = false;        // sync has reached PREPARED at least once
  let starting = false;       // guard against double-start
  let activeRoomId = '';
  let sdkLoading = null;      // Promise for the lazy bundle load
  let mode = 'chats';         // 'chats' (Spaces/rooms) | 'market' (open commons)
  let chatsBlockedMsg = null; // set once Matrix is known unavailable (stops token re-fetch)
  let tokenRetryAt = 0;       // epoch ms before which we must NOT re-fetch the token
  let tokenBackoff = 2000;    // current cooldown; doubles on failure, resets on success
  let retryTimer = null;      // scheduled auto-retry after backoff / warm-up
  let syncWatchdog = null;    // hard timeout waiting for PREPARED
  let marketLoading = false;  // guard against concurrent catalog fetches
  let mktFilters = { q: '', skill: '', archetype: '', rail: '', status: '', category: '' };  // marketplace search state
  let mktCfg = null;      // /api/market/config — launch-rail UI flags (credits hidden at launch)
  let mktLast = [];       // last search result (for category chips + client-side filters)
  // The future public repo's issues page — the "take it over" claim ledger (Chords 7/4:
  // point at lucid-cove NOW so nothing needs swapping at export; 404s until the repo exists).
  const MKT_REPO_URL = 'https://github.com/LucidPrinciples/lucid-cove/issues';
  let mktTimer = null;
  let sellImageUrl = '';      // uploaded product-image URL for the Sell form

  function esc(s) { const d = document.createElement('div'); d.textContent = s == null ? '' : String(s); return d.innerHTML; }

  // ── Invite handling + send backoff (pure helpers, batch-10 #1 / T9) ──────────
  // T9 find: Connect listed invite-membership rooms as if joined. The server holds
  // only a STRIPPED invite (no room version), so sendTextMessage fails with
  // "unsupported room version ''" and the SDK retried the same event forever.
  // Fix: gate composing behind an explicit join, auto-join only our OWN steward's
  // rooms (same homeserver), and bound the send retries.
  function serverOf(mxid) { const i = (mxid || '').indexOf(':'); return i === -1 ? '' : mxid.slice(i + 1); }
  // Same-homeserver invite = our own steward (the Cove/Family spaces) → safe to
  // auto-join. A different server = a cross-Cove/Haven invite → explicit Accept.
  function isOwnServerInvite(inviterId, myId) {
    const a = serverOf(inviterId), b = serverOf(myId);
    return !!a && !!b && a === b;
  }
  // Exponential backoff for send retries: bounded attempts, capped delay. Mirrors
  // the batch-9 token cooldown so a dead room can never hammer forever.
  const SEND_MAX_ATTEMPTS = 4;
  function sendBackoffMs(attempt) { return Math.min(1000 * Math.pow(2, attempt), 15000); }

  // ── Lazy-load the matrix-js-sdk bundle (1.2M) only when Connect is first opened ──
  function loadSdk() {
    if (window.mxcs) return Promise.resolve();
    if (sdkLoading) return sdkLoading;
    sdkLoading = new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = SDK_URL;
      s.onload = () => resolve();
      s.onerror = () => reject(new Error('Could not load the Matrix engine.'));
      document.head.appendChild(s);
    });
    return sdkLoading;
  }

  // ── Styles (MC-themed, in-panel; no fixed overlay) ──
  function injectStyles() {
    if (document.getElementById('connect-styles')) return;
    const css = `
    #connect-panel{flex:1;display:flex;flex-direction:column;min-height:0;overflow:hidden;background:var(--bg,#0B1022);color:var(--text,#F6F1E7)}
    .cx-modebar{display:flex;gap:6px;padding:8px 12px;border-bottom:1px solid var(--border,#23304d);flex:0 0 auto}
    .cx-modebtn{background:none;border:1px solid var(--border,#23304d);color:var(--text,#F6F1E7);border-radius:8px;padding:5px 16px;font-size:0.82rem;font-weight:600;cursor:pointer;opacity:.7}
    .cx-modebtn:hover{opacity:.9}
    .cx-modebtn.active{opacity:1;background:rgba(92,225,230,0.14);border-color:var(--daily-freq,#5ce1e6);color:var(--daily-freq,#5ce1e6)}
    .cx-content{flex:1;min-height:0;overflow:hidden}
    .cx-content.chats{display:flex}
    .cx-content.market{display:block;overflow-y:auto;padding:14px}
    .cx-wallet{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:14px;padding:10px 14px;border:1px solid var(--border,#23304d);border-radius:12px;background:rgba(255,255,255,0.03)}
    @media (max-width:560px){
      .cx-wallet{flex-direction:column;align-items:stretch;gap:8px}
      .cx-wallet .cx-bal{text-align:center}
      .cx-wbtns{flex-wrap:wrap;justify-content:center}
      .cx-wbtns .cx-wbtn{font-size:0.72rem;padding:5px 9px}
    }
    .cx-agent-chip{display:flex;align-items:center;gap:7px;margin:6px 0 2px;font-size:0.82rem;opacity:.92}
    .cx-agent-avatar{width:22px;height:22px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;font-size:0.72rem;font-weight:700;background:rgba(92,225,230,0.16);border:1px solid var(--daily-freq,#5ce1e6);color:var(--daily-freq,#5ce1e6);overflow:hidden}
    .cx-agent-avatar img{width:100%;height:100%;object-fit:cover;border-radius:50%}
    .cx-bal{font-weight:600;font-size:0.9rem}
    .cx-wbtns{display:flex;gap:8px}
    .cx-wbtn{background:transparent;color:var(--daily-freq,#5ce1e6);border:1px solid var(--daily-freq,#5ce1e6);border-radius:8px;padding:5px 14px;font-weight:700;font-size:0.8rem;cursor:pointer}
    .cx-sellform{max-width:460px;display:flex;flex-direction:column;gap:12px}
    .cx-sell-h{display:flex;align-items:center;gap:12px;font-weight:600;margin-bottom:4px}
    .cx-back{background:none;border:none;color:var(--daily-freq,#5ce1e6);cursor:pointer;font-size:0.85rem;padding:0}
    .cx-sellform label{display:flex;flex-direction:column;gap:5px;font-size:0.8rem;opacity:.85}
    .cx-sellform input,.cx-sellform textarea,.cx-sellform select{background:rgba(255,255,255,0.04);border:1px solid var(--border,#23304d);border-radius:8px;padding:8px 10px;color:inherit;font:inherit}
    .sf-unit{opacity:.55}
    .cx-sell-actions{display:flex;align-items:center;gap:12px;margin-top:4px}
    .cx-mine-actions{display:flex;gap:8px;margin-top:8px;flex-wrap:wrap}
    .cx-mine-actions .cx-wbtn{font-size:0.78rem;padding:4px 10px}
    .cx-mine-status{font-size:0.76rem;opacity:.85;margin-top:5px}
    .sf-status{font-size:0.8rem;opacity:.8}
    .cx-seller-link{color:var(--daily-freq,#5ce1e6);cursor:pointer;text-decoration:none}
    .cx-seller-link:hover{text-decoration:underline}
    .cx-modal-overlay{position:fixed;inset:0;background:rgba(0,0,0,.6);display:flex;align-items:flex-start;justify-content:center;z-index:9999;overflow-y:auto;padding:40px 16px}
    .cx-modal{position:relative;max-width:560px;width:100%;background:var(--bg,#0B1022);border:1px solid var(--border,#23304d);border-radius:16px;padding:22px}
    .cx-modal-x{position:absolute;top:12px;right:14px;background:none;border:none;color:inherit;font-size:1.4rem;cursor:pointer;opacity:.6}
    .cx-prof-head{display:flex;align-items:center;gap:14px;margin-bottom:10px}
    .cx-prof-agent{display:flex;align-items:center;gap:12px;margin:12px 0 6px;padding-top:12px;border-top:1px solid var(--border,#23304d)}
    .cx-ava{width:56px;height:56px;border-radius:50%;object-fit:cover;background:rgba(255,255,255,.06)}
    .cx-ava.sm{width:40px;height:40px}
    .cx-ava-ph{display:inline-block}
    .cx-prof-name{font-weight:700;font-size:1.05rem}
    .cx-prof-handle{font-size:0.8rem;opacity:.55}
    .cx-prof-aname{font-weight:600}
    .cx-prof-ameta{font-size:0.78rem;opacity:.6}
    .cx-prof-bio{font-size:0.9rem;opacity:.85;line-height:1.45;margin:6px 0}
    .cx-chips{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0}
    .cx-chip{font-size:0.72rem;border:1px solid var(--border,#23304d);border-radius:999px;padding:3px 10px;opacity:.85}
    .cx-prof-offers-h{font-weight:600;font-size:0.8rem;letter-spacing:.05em;text-transform:uppercase;opacity:.6;margin:16px 0 8px}
    .cx-flabel{display:block;font-size:0.8rem;opacity:.85;margin:10px 0 4px}
    .cx-flabel input,.cx-flabel textarea{width:100%;background:rgba(255,255,255,0.04);border:1px solid var(--border,#23304d);border-radius:8px;padding:8px 10px;color:inherit;font:inherit;margin-top:4px}
    .cx-chipsel{display:flex;flex-direction:column;gap:8px;margin:6px 0}
    .cx-chipgroup-h{font-size:0.7rem;opacity:.5;text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px}
    .cx-chip-tog{cursor:pointer;margin:0 4px 4px 0;display:inline-block}
    .cx-chip-tog.on{background:var(--daily-freq,#5ce1e6);color:#0B1022;border-color:var(--daily-freq,#5ce1e6)}
    .cx-avaedit{display:flex;align-items:center;gap:12px;margin:4px 0 2px}
    .cx-avaedit input[type=file]{font-size:0.78rem;opacity:.8}
    .cx-search{display:flex;gap:8px;margin-bottom:10px}
    .cx-search input{flex:1;background:rgba(255,255,255,0.04);border:1px solid var(--border,#23304d);border-radius:8px;padding:9px 12px;color:inherit;font:inherit}
    .cx-search select{background:rgba(255,255,255,0.04);border:1px solid var(--border,#23304d);border-radius:8px;padding:0 10px;color:inherit;font:inherit}
    .cx-facets{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px}
    .cx-facet{cursor:pointer}
    .cx-facet.on{background:var(--daily-freq,#5ce1e6);color:#0B1022;border-color:var(--daily-freq,#5ce1e6)}
    .cx-market{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px}
    .cx-card{border:1px solid var(--border,#23304d);border-radius:12px;padding:14px;background:rgba(255,255,255,0.02);display:flex;flex-direction:column;gap:6px}
    .cx-card-img{width:100%;height:120px;object-fit:cover;border-radius:8px;margin-bottom:6px}
    .cx-card-ph{display:flex;align-items:center;justify-content:center;position:relative}
    .cx-ph-mono{font-size:1.9rem;font-weight:700;letter-spacing:.06em;color:rgba(246,241,231,.6)}
    .cx-ph-tag{position:absolute;bottom:6px;right:9px;font-size:0.58rem;letter-spacing:.07em;text-transform:uppercase;opacity:.5}
    .cx-card-title{font-weight:600;font-size:0.98rem}
    .cx-card-seller{font-size:0.74rem;opacity:.55}
    .cx-card-desc{font-size:0.85rem;opacity:.8;line-height:1.4;margin:2px 0 4px}
    .cx-tiers{display:flex;flex-direction:column;gap:6px;margin-top:auto}
    .cx-tier{display:flex;align-items:center;justify-content:space-between;gap:8px;font-size:0.85rem}
    .cx-tier-name{opacity:.9}
    .cx-buy{background:var(--daily-freq,#5ce1e6);color:#0B1022;border:none;border-radius:8px;padding:5px 14px;font-weight:700;font-size:0.8rem;cursor:pointer}
    .cx-buy:disabled{opacity:.6;cursor:default}
    .cx-visit{text-decoration:none;text-align:center}
    .cx-ext-tag{font-size:0.62rem;opacity:.55;font-weight:600;vertical-align:middle}
    .cx-card-wanted{border-style:dashed;opacity:.92}
    .cx-wanted-cta{font-size:0.8rem;font-style:italic;opacity:.75;margin:4px 0 8px;line-height:1.4}
    .cx-build{background:transparent;border:1px solid var(--daily-freq,#5ce1e6);color:var(--daily-freq,#5ce1e6)}
    .cx-status{font-size:0.6rem;font-weight:600;padding:2px 7px;border-radius:5px;white-space:nowrap;vertical-align:middle;background:rgba(255,255,255,0.07);opacity:.85}
    .cx-st-available{color:var(--green,#4caf50)}
    .cx-st-needs_agent{color:var(--blue,#5b9bd5)}
    .cx-st-coming_soon{color:var(--dim,#8a93a6)}
    .cx-st-wanted{color:var(--purple,#b07cd8)}
    .cx-st-building{color:var(--yellow,#d4a843)}
    .cx-st-active{color:var(--green,#4caf50)}
    .cx-card-needsagent{border-color:var(--blue,#5b9bd5)}
    .cx-card-building{border-color:var(--yellow,#d4a843)}
    .connect-sidebar{width:240px;min-width:180px;border-right:1px solid var(--border,#23304d);display:flex;flex-direction:column;background:rgba(255,255,255,0.02)}
    .connect-shead{padding:11px 14px;font-weight:600;font-size:0.72rem;letter-spacing:.06em;text-transform:uppercase;color:var(--daily-freq,#5ce1e6);border-bottom:1px solid var(--border,#23304d);display:flex;justify-content:space-between;align-items:center}
    .connect-add{background:none;border:1px solid var(--border,#23304d);color:var(--text,#F6F1E7);border-radius:7px;font-size:0.72rem;padding:3px 8px;cursor:pointer;opacity:.85}
    .connect-add:hover{opacity:1;border-color:var(--daily-freq,#5ce1e6)}
    .connect-tree{flex:1;overflow-y:auto;padding:4px 0}
    .connect-space{padding:9px 14px 4px;font-size:0.68rem;letter-spacing:.05em;text-transform:uppercase;opacity:.55;font-weight:700}
    .connect-room{padding:9px 14px 9px 22px;cursor:pointer;font-size:0.9rem;display:flex;justify-content:space-between;align-items:center;gap:6px;border-left:3px solid transparent}
    .connect-room:hover{background:rgba(255,255,255,0.04)}
    .connect-room.active{background:rgba(92,225,230,0.12);border-left-color:var(--daily-freq,#5ce1e6)}
    .connect-room .cr-name{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    .connect-room .cr-unread{background:var(--daily-freq,#5ce1e6);color:#0B1022;font-size:0.66rem;font-weight:700;border-radius:9px;padding:1px 6px;flex:0 0 auto}
    .connect-main{flex:1;display:flex;flex-direction:column;min-width:0}
    .connect-rtitle{padding:12px 16px;font-weight:600;border-bottom:1px solid var(--border,#23304d);font-size:0.95rem;display:flex;justify-content:space-between;align-items:center}
    .connect-rtitle .crt-sub{font-size:0.72rem;opacity:.5;font-weight:400}
    .connect-timeline{flex:1;overflow-y:auto;padding:14px 16px;display:flex;flex-direction:column;gap:8px}
    .cx-msg{max-width:78%;padding:8px 12px;border-radius:12px;background:rgba(255,255,255,0.05);font-size:0.9rem;line-height:1.4;word-wrap:break-word;overflow-wrap:anywhere}
    .cx-msg.mine{align-self:flex-end;background:rgba(92,225,230,0.16)}
    .cx-msg .cx-sender{font-size:0.72rem;opacity:0.65;margin-bottom:2px}
    .connect-compose{display:flex;gap:8px;padding:12px 16px;border-top:1px solid var(--border,#23304d)}
    .connect-compose input{flex:1;background:rgba(255,255,255,0.06);border:1px solid var(--border,#23304d);border-radius:10px;padding:10px 12px;color:var(--text,#F6F1E7);font-size:0.9rem}
    .connect-compose button{background:var(--daily-freq,#5ce1e6);color:#0B1022;border:none;border-radius:10px;padding:10px 16px;font-weight:600;cursor:pointer}
    .connect-compose button:disabled{opacity:.4;cursor:default}
    .connect-empty{margin:auto;opacity:0.6;font-size:0.9rem;text-align:center;padding:24px;line-height:1.5}
    .cx-spin{margin:auto;opacity:.7;font-size:0.9rem;text-align:center;padding:24px}
    /* Invite (unaccepted) room badge + Accept bar (batch-10 #1). */
    .connect-room .cr-invite{background:var(--warn,#e0a53c);color:#0B1022;font-size:0.62rem;font-weight:700;text-transform:uppercase;letter-spacing:.04em;border-radius:9px;padding:1px 7px;flex:0 0 auto}
    .cx-invite{margin:auto;text-align:center;padding:24px;display:flex;flex-direction:column;gap:14px;align-items:center}
    .cx-invite-msg{opacity:.8;font-size:0.95rem;line-height:1.5}
    .cx-invite-accept{background:var(--daily-freq,#5ce1e6);color:#0B1022;border:none;border-radius:8px;padding:8px 22px;font-weight:700;cursor:pointer}
    .cx-invite-accept:disabled{opacity:.5;cursor:default}
    /* "Couldn't deliver — retry" affordance after send backoff gives up (batch-10 #1). */
    .cx-send-notice{margin:8px 0 2px;padding:8px 12px;background:rgba(224,60,60,.12);border:1px solid rgba(224,60,60,.4);border-radius:8px;font-size:0.82rem;color:var(--danger,#e86464);text-align:center}
    .cx-send-notice .cx-retry-send{background:none;border:1px solid currentColor;color:inherit;border-radius:6px;padding:2px 12px;margin-left:6px;cursor:pointer;font-weight:600}
    /* Back arrow in the room title — only visible on mobile (room view). */
    .cx-back{display:none;background:none;border:none;color:var(--daily-freq,#5ce1e6);font-size:1.4rem;line-height:1;cursor:pointer;padding:0 12px 0 0;margin:0}
    /* Mobile: show EITHER the room list OR the conversation, not both. */
    @media (max-width:640px){
      .connect-sidebar{width:100%;min-width:0;border-right:none}
      #connect-panel .connect-main{display:none}
      #connect-panel.show-room .connect-sidebar{display:none}
      #connect-panel.show-room .connect-main{display:flex}
      #connect-panel.show-room .cx-back{display:inline-flex;align-items:center}
    }`;
    const st = document.createElement('style'); st.id = 'connect-styles'; st.textContent = css;
    document.head.appendChild(st);
  }

  // ── Mount: hide the agent chat area, render Connect in its place (keeps tab row) ──
  // Hide the agent-chat chrome while Connect is open (keep the agent-selector row
  // so the user can switch back). Includes the DAY/DEEP sub-tabs and the thread
  // progress bar, which belong to the agent chat, not Connect.
  const HIDE_SELECTORS = ['.chat-messages', '.activity-live', '.chat-mode-bar', '.chat-input-area', '#subchannel-tabs', '#chat-progress-bar'];
  let hidden = [];

  function mountPanel() {
    const anchor = document.querySelector('.chat-messages');
    if (!anchor) return null;
    const parent = anchor.parentElement;
    // Hide the agent chat elements (remember their prior display to restore).
    hidden = [];
    HIDE_SELECTORS.forEach(sel => {
      parent.querySelectorAll(sel).forEach(el => { hidden.push([el, el.style.display]); el.style.display = 'none'; });
    });
    let panel = document.getElementById('connect-panel');
    if (!panel) {
      panel = document.createElement('div');
      panel.id = 'connect-panel';
      parent.insertBefore(panel, anchor); // sits where the messages were, after the toolbar
    }
    panel.style.display = 'flex';
    panel.classList.remove('show-room'); // mobile opens on the conversation list
    return panel;
  }

  function unmountPanel() {
    const panel = document.getElementById('connect-panel');
    if (panel) panel.style.display = 'none';
    hidden.forEach(([el, disp]) => { el.style.display = disp || ''; });
    hidden = [];
  }

  function setPanelHTML(html) {
    const panel = document.getElementById('connect-panel');
    if (panel) panel.innerHTML = html;
  }

  // Message into the Chats content only (so Market stays usable independently).
  function chatsMsg(msg) {
    const t = document.getElementById('cx-tree');
    if (t) t.innerHTML = '<div class="connect-empty">' + esc(msg) + '</div>';
    const tl = document.getElementById('cx-timeline');
    if (tl) tl.innerHTML = '<div class="connect-empty">Switch to Market to browse the Haven.</div>';
  }

  // Stage line while Connecting — Quietgrove hung 10–15m on a silent spinner.
  // Always show where we are + elapsed so a stall is diagnosable without DevTools.
  function chatsProgress(stage, t0) {
    const ms = Math.max(0, Date.now() - (t0 || Date.now()));
    const sec = (ms / 1000).toFixed(1);
    try { console.info('[connect]', stage, sec + 's'); } catch (_) { /* noop */ }
    const t = document.getElementById('cx-tree');
    if (t) {
      t.innerHTML = '<div class="cx-spin">Connecting…</div>'
        + '<div class="connect-empty" style="margin-top:8px;font-size:0.78rem;opacity:.75">'
        + esc(stage) + ' · ' + sec + 's</div>';
    }
  }

  function clearConnectTimers() {
    if (retryTimer) { clearTimeout(retryTimer); retryTimer = null; }
    if (syncWatchdog) { clearTimeout(syncWatchdog); syncWatchdog = null; }
  }

  // Schedule a real auto-retry. Old path said "retrying shortly" and never did —
  // operator sat on Connecting… until they left and came back (Quietgrove 10–15m).
  function scheduleConnectRetry(delayMs, reason) {
    clearConnectTimers();
    const wait = Math.max(500, delayMs | 0);
    const when = new Date(Date.now() + wait);
    const waitSec = Math.ceil(wait / 1000);
    chatsMsg((reason || 'Connect is warming up') + ' — retrying in ' + waitSec + 's…');
    retryTimer = setTimeout(() => {
      retryTimer = null;
      if (started || starting) return;
      ensureChats();
    }, wait);
    try { console.info('[connect] retry scheduled', wait + 'ms', reason || '', when.toISOString()); } catch (_) {}
  }

  // ── SSO: token from the MC session → start the SDK client. Renders into the
  // Chats content ONLY, never the whole panel, so Market works for Operators
  // without a Matrix account (501 just shows a note in Chats). ──
  async function ensureChats() {
    if (started) { if (mode === 'chats') renderChats(); return; }
    if (starting) return;
    // Backoff guard (run-3): a tight token re-fetch on every renderChats tripped
    // Dendrite's login rate limit (M_LIMIT_EXCEEDED), which then looked like a broken
    // Connect. Honor a cooldown window before hitting /api/matrix/token again —
    // AND actually re-enter when the window ends (scheduleConnectRetry).
    if (Date.now() < tokenRetryAt) {
      scheduleConnectRetry(tokenRetryAt - Date.now(), 'Connect is warming up');
      return;
    }
    clearConnectTimers();
    starting = true;
    const t0 = Date.now();
    chatsProgress('Loading chat engine', t0);

    try { await loadSdk(); } catch (e) {
      starting = false;
      chatsMsg('Could not load the chat engine.');
      return;
    }

    let cfg;
    chatsProgress('Signing in to chat', t0);
    try {
      const r = await fetch(TOKEN_ENDPOINT, { credentials: 'same-origin' });
      if (r.status === 501) {
        starting = false;
        chatsBlockedMsg = "Chat isn't set up for this Presence yet. The Market still works.";
        chatsMsg(chatsBlockedMsg);
        return;
      }
      if (r.status === 429) {
        // Rate limited — back off (exponential up to 30s) and REALLY retry.
        starting = false;
        tokenRetryAt = Date.now() + tokenBackoff;
        const wait = tokenBackoff;
        tokenBackoff = Math.min(tokenBackoff * 2, 30000);
        scheduleConnectRetry(wait, 'Connect is warming up (busy)');
        return;
      }
      if (!r.ok) throw new Error('token ' + r.status);
      cfg = await r.json();
      tokenBackoff = 2000;  // clean token → reset the cooldown ladder
      try {
        console.info('[connect] token ok', ((Date.now() - t0) / 1000).toFixed(1) + 's',
          'hs=' + (cfg.homeserver || ''), 'user=' + (cfg.user_id || ''));
      } catch (_) {}
    } catch (e) {
      starting = false;
      tokenRetryAt = Date.now() + tokenBackoff;
      const wait = tokenBackoff;
      tokenBackoff = Math.min(tokenBackoff * 2, 30000);
      scheduleConnectRetry(wait, "Couldn't reach chat right now");
      return;
    }

    try {
      // #205 — a no-domain / mesh Cove bakes MATRIX_PUBLIC_URL as http://localhost:{port};
      // "localhost" means nothing to a remote browser. Point it at the host we loaded
      // from (the matrix port rides along). Domain Coves (https://matrix.{domain}) untouched.
      let _hs = cfg.homeserver || '';
      try {
        const u = new URL(_hs);
        if (u.hostname === 'localhost' || u.hostname === '127.0.0.1') {
          u.hostname = location.hostname;
          _hs = u.toString().replace(/\/+$/, '');
        }
      } catch (_) { /* leave _hs as-is */ }
      chatsProgress('Opening homeserver ' + (_hs || '(unknown)'), t0);
      client = window.mxcs.createClient({
        baseUrl: _hs, accessToken: cfg.access_token,
        userId: cfg.user_id, deviceId: cfg.device_id || undefined, timelineSupport: true,
      });
    } catch (e) {
      starting = false;
      chatsMsg('Chat engine error.');
      return;
    }

    const { ClientEvent, RoomEvent } = window.mxcs;
    client.on(ClientEvent.Sync, (state) => {
      try { console.info('[connect] sync state', state, ((Date.now() - t0) / 1000).toFixed(1) + 's'); } catch (_) {}
      if (state === 'PREPARED' && !started) {
        started = true;
        starting = false;
        clearConnectTimers();
        if (mode === 'chats') renderChats();
      } else if (!started && (state === 'ERROR' || state === 'STOPPED')) {
        // Surface a real failure instead of spinning forever on a dead /sync.
        starting = false;
        clearConnectTimers();
        tokenRetryAt = Date.now() + tokenBackoff;
        const wait = tokenBackoff;
        tokenBackoff = Math.min(tokenBackoff * 2, 30000);
        scheduleConnectRetry(wait, 'Chat sync hit ' + state);
      } else if (!started && state) {
        chatsProgress('Sync: ' + state, t0);
      }
    });
    client.on(RoomEvent.Timeline, (event, room) => {
      if (!started || mode !== 'chats') return;
      if (room && room.roomId === activeRoomId) renderTimeline();
      renderTree();
    });

    // Hard watchdog: never sit on Connecting… for 10–15 minutes with no signal.
    // If PREPARED never arrives, stop, show the stage we stalled on, and auto-retry.
    const SYNC_TIMEOUT_MS = 45000;
    syncWatchdog = setTimeout(() => {
      syncWatchdog = null;
      if (started) return;
      starting = false;
      try {
        if (client && typeof client.stopClient === 'function') client.stopClient();
      } catch (_) { /* noop */ }
      tokenRetryAt = Date.now() + tokenBackoff;
      const wait = tokenBackoff;
      tokenBackoff = Math.min(tokenBackoff * 2, 30000);
      scheduleConnectRetry(wait, 'Chat sync timed out after ' + Math.round(SYNC_TIMEOUT_MS / 1000) + 's');
    }, SYNC_TIMEOUT_MS);

    chatsProgress('Starting live sync', t0);
    try {
      await client.startClient({ initialSyncLimit: 30, lazyLoadMembers: true });
    } catch (e) {
      starting = false;
      clearConnectTimers();
      tokenRetryAt = Date.now() + tokenBackoff;
      const wait = tokenBackoff;
      tokenBackoff = Math.min(tokenBackoff * 2, 30000);
      scheduleConnectRetry(wait, 'Chat sync failed to start');
    }
  }

  // ── Render the full client shell, then populate ──
  // Mode shell: Chats (private, invite-based) | Market (open Haven-wide commons).
  function renderClient() {
    setPanelHTML(`
      <div class="cx-modebar">
        <button class="cx-modebtn" data-mode="chats">Chats</button>
        <button class="cx-modebtn" data-mode="market">Market</button>
      </div>
      <div id="cx-content" class="cx-content"></div>`);
    const panel = document.getElementById('connect-panel');
    if (panel) panel.querySelectorAll('.cx-modebtn').forEach(b => b.onclick = () => setMode(b.dataset.mode));
    setMode(mode);
  }

  function setMode(m) {
    mode = m;
    document.querySelectorAll('.cx-modebtn').forEach(b => b.classList.toggle('active', b.dataset.mode === m));
    if (m === 'market') renderMarket(); else renderChats();
  }

  function renderChats() {
    const c = document.getElementById('cx-content');
    if (!c) return;
    c.className = 'cx-content chats';
    c.innerHTML = `
      <div class="connect-sidebar">
        <div class="connect-shead"><span>Connect</span><button class="connect-add" id="cx-add" title="Invite by handle">+ Add</button></div>
        <div id="cx-tree" class="connect-tree"></div>
      </div>
      <div class="connect-main">
        <div id="cx-rtitle" class="connect-rtitle"><span>Select a conversation</span></div>
        <div id="cx-timeline" class="connect-timeline"><div class="connect-empty">Pick a conversation on the left.</div></div>
        <div class="connect-compose">
          <input id="cx-input" placeholder="Message…" disabled>
          <button id="cx-send" disabled>Send</button>
        </div>
      </div>`;
    const add = document.getElementById('cx-add');
    if (add) add.onclick = promptInvite;
    const send = document.getElementById('cx-send');
    const input = document.getElementById('cx-input');
    if (send) send.onclick = sendMsg;
    if (input) input.onkeydown = (e) => { if (e.key === 'Enter') sendMsg(); };
    if (client && started) { renderTree(); if (activeRoomId) renderTimeline(); }
    else if (chatsBlockedMsg) { chatsMsg(chatsBlockedMsg); }  // known unavailable — don't re-fetch
    else {
      const t = document.getElementById('cx-tree'); if (t) t.innerHTML = '<div class="cx-spin">Connecting…</div>';
      ensureChats();  // lazy Matrix init (idempotent); degrades into chatsMsg on 501/error
    }
  }

  // ── Market: the OPEN Haven-wide commons (wallet + search + browse + buy + sell) ──
  async function renderMarket() {
    const c = document.getElementById('cx-content');
    if (!c) return;
    c.className = 'cx-content market';
    // Launch-rail flags (C1): at launch credits/wallet/hire are hidden — fixed-price
    // checkout is the only visible rail, so no wallet bar, no Top Up, no rail picker.
    if (!mktCfg) {
      try { mktCfg = await (await fetch('/api/market/config', { credentials: 'same-origin' })).json(); }
      catch (e) { mktCfg = { credits_enabled: false, wallet_visible: false, hire_visible: false }; }
    }
    const showWallet = !!(mktCfg && mktCfg.wallet_visible);
    // Shell: (wallet bar when credits are on) + a search bar + the (search-driven) body.
    c.innerHTML =
      '<div class="cx-wallet">' +
      (showWallet ? '<span class="cx-bal" id="cx-bal">Wallet: …</span>' : '<span class="cx-bal" style="opacity:.75">Lucid Cove Haven Market</span>') +
      '<span class="cx-wbtns"><button class="cx-wbtn" id="cx-mine">My Offerings</button>' +
      '<button class="cx-wbtn" id="cx-library">My Library</button>' +
      '<button class="cx-wbtn" id="cx-profile">Edit Profile</button>' +
      (showWallet ? '<button class="cx-wbtn" id="cx-topup">Top Up</button>' : '') +
      '<button class="cx-wbtn" id="cx-addwanted" title="Add an idea to the board — something the Haven should have">＋ Add to board</button>' +
      '<button class="cx-wbtn" id="cx-sell">Sell</button></span></div>' +
      '<div class="cx-search"><input id="cx-q" placeholder="Search the Haven — skills, names, offerings…" value="' + esc(mktFilters.q) + '">' +
      (mktCfg && mktCfg.credits_enabled
        ? '<select id="cx-rail"><option value="">Any rail</option><option value="credits">LPC</option><option value="stripe">Cash</option></select>'
        : '') +
      '</div>' +
      '<div id="cx-facets" class="cx-facets"></div>' +
      '<div id="cx-marketbody"><div class="cx-spin">Loading the market…</div></div>';
    const tu = document.getElementById('cx-topup'); if (tu) tu.onclick = topUpWallet;
    const sl = document.getElementById('cx-sell'); if (sl) sl.onclick = renderSell;
    const pe = document.getElementById('cx-profile'); if (pe) pe.onclick = editProfile;
    const lb = document.getElementById('cx-library'); if (lb) lb.onclick = renderLibrary;
    const mn = document.getElementById('cx-mine'); if (mn) mn.onclick = renderMine;
    const aw = document.getElementById('cx-addwanted'); if (aw) aw.onclick = addToBoard;
    const q = document.getElementById('cx-q');
    if (q) q.oninput = () => { clearTimeout(mktTimer); mktFilters.q = q.value; mktTimer = setTimeout(doMarketSearch, 300); };
    const rail = document.getElementById('cx-rail');
    if (rail) { rail.value = mktFilters.rail; rail.onchange = () => { mktFilters.rail = rail.value; doMarketSearch(); }; }
    if (showWallet) loadWallet();
    doMarketSearch();
  }

  async function doMarketSearch() {
    const body = document.getElementById('cx-marketbody');
    if (!body) return;
    const p = new URLSearchParams();
    if (mktFilters.q) p.set('q', mktFilters.q);
    if (mktFilters.skill) p.set('skill', mktFilters.skill);
    if (mktFilters.archetype) p.set('archetype', mktFilters.archetype);
    if (mktFilters.rail) p.set('rail', mktFilters.rail);
    let data;
    try {
      const r = await fetch('/api/market/search?' + p.toString(), { credentials: 'same-origin' });
      if (!r.ok) throw new Error('search ' + r.status);
      data = await r.json();
    } catch (e) {
      body.innerHTML = '<div class="connect-empty">Market unavailable right now.</div>';
      return;
    }
    mktLast = data.listings || [];
    renderFacetBar(data.facets || {}, mktLast);
    // Client-side status + category filters: real products vs To-Build vs Coming-soon-from-LP.
    let listings = mktLast;
    if (mktFilters.status === 'real') listings = listings.filter(l => _mktGroup(l) === 'real');
    if (mktFilters.status === 'tobuild') listings = listings.filter(l => _mktGroup(l) === 'tobuild');
    if (mktFilters.status === 'coming') listings = listings.filter(l => _mktGroup(l) === 'coming');
    if (mktFilters.category) listings = listings.filter(l => (l.category || '') === mktFilters.category);
    if (!listings.length) {
      const anyFilter = mktFilters.q || mktFilters.skill || mktFilters.archetype || mktFilters.rail || mktFilters.status || mktFilters.category;
      body.innerHTML = anyFilter
        ? '<div class="connect-empty">No matches. Try clearing a filter.</div>'
        : '<div class="connect-empty">Marketplace — coming soon.<br>Tools and offerings from across the Haven will appear here.</div>';
      return;
    }
    body.innerHTML = '<div class="cx-market">' + listings.map(renderCard).join('') + '</div>';
    wireMarketEls(body);
  }

  // Which shelf a listing sits on: real (get/buy it now) · tobuild (wanted — the
  // community claim board) · coming (first-party stubs LP is building).
  function _mktGroup(l) {
    // first_party FIRST: LP stubs sit at status 'wanted' too, but they're the
    // build team's line ("Coming from LP"), not community take-over cards.
    if (l.kind === 'first_party' && !(l.tiers && l.tiers.length)) return 'coming';
    if (l.kind === 'wanted' || l.status === 'wanted') return 'tobuild';
    if (l.status === 'building') return 'tobuild';
    return 'real';
  }

  // The maker's face on a first-party card: agent avatar chip ("Arthur is building
  // this"). v1 = initial-circle; when the profile mirror serves cross-Cove agent
  // avatars this same chip takes the <img> without layout change.
  function agentChip(l) {
    const ag = String(l.agent_owner || '').split('.')[0].replace(/[^a-z0-9]/gi, '');
    if (!ag) return '';
    const name = ag[0].toUpperCase() + ag.slice(1);
    return `<div class="cx-agent-chip" data-agent="${esc(ag)}"><span class="cx-agent-avatar">${esc(ag[0].toUpperCase())}</span><span><b>${esc(name)}</b> is building this</span></div>`;
  }

  // Lazy chip upgrade (jules 2331): if the agent has a mirrored profile, swap the
  // initial-circle for their real avatar and make the chip tap through to their
  // profile. No profile → the circle stays and nothing is clickable (no dead taps).
  const _agProfiles = {};
  async function enhanceAgentChip(ch) {
    const ag = ch.dataset.agent;
    if (!ag) return;
    if (!(ag in _agProfiles)) {
      try {
        const r = await fetch('/api/profile/' + encodeURIComponent(ag), { credentials: 'same-origin' });
        _agProfiles[ag] = r.ok ? await r.json() : null;
      } catch (e) { _agProfiles[ag] = null; }
    }
    const p = _agProfiles[ag];
    if (!p) return;
    const av = (p.agent && (p.agent.agent_avatar_url || p.agent.avatar_url)) ||
               p.agent_avatar_url || '';
    if (av) {
      const c = ch.querySelector('.cx-agent-avatar');
      if (c) c.innerHTML = '<img src="' + esc(av) + '" alt="">';
    }
    ch.style.cursor = 'pointer';
    ch.title = 'View profile';
    ch.onclick = () => openProfile(ag);
  }

  function renderFacetBar(facets, listings) {
    const el = document.getElementById('cx-facets');
    if (!el) return;
    const pill = (label, kind, val) => {
      const on = mktFilters[kind] === val;
      return `<span class="cx-chip cx-facet${on ? ' on' : ''}" data-kind="${kind}" data-val="${esc(val)}">${esc(label)}</span>`;
    };
    let html = '';
    // Status chips first — the real-vs-planned lens (Chords 7/4).
    html += pill('● Available now', 'status', 'real');
    html += pill('🔧 To build', 'status', 'tobuild');
    html += pill('◌ Coming from LP', 'status', 'coming');
    // Category chips from what's actually on the shelf.
    const cats = Array.from(new Set((listings || []).map(l => l.category).filter(Boolean))).sort();
    cats.slice(0, 12).forEach(cat => html += pill(cat, 'category', cat));
    (facets.skills || []).slice(0, 24).forEach(s => html += pill(s, 'skill', s));
    (facets.archetypes || []).forEach(a => html += pill(a, 'archetype', a));
    el.innerHTML = html;
    el.querySelectorAll('.cx-facet').forEach(ch => ch.onclick = () => {
      const kind = ch.dataset.kind, val = ch.dataset.val;
      mktFilters[kind] = (mktFilters[kind] === val) ? '' : val;  // toggle
      doMarketSearch();
    });
  }

  async function loadWallet() {
    try {
      const r = await fetch('/api/credits/me', { credentials: 'same-origin' });
      if (!r.ok) return;
      const d = await r.json();
      const el = document.getElementById('cx-bal');
      if (el) el.textContent = 'Wallet: ' + (d.balance || 0) + ' ' + (d.code || 'LPC') +
        ' ($' + (Number(d.usd) || 0).toFixed(2) + ')';
    } catch (e) { /* non-fatal */ }
  }

  async function topUpWallet() {
    const usd = parseFloat(prompt('Top up how many dollars of LPC? (min $1)', '20'));
    if (!usd || usd < 1) return;
    try {
      const me = await (await fetch('/api/credits/me', { credentials: 'same-origin' })).json();
      const email = (window.MC && MC.presence && MC.presence.email) || '';
      const r = await fetch('https://api.lucidcove.org/api/commerce/credits/checkout', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ handle: me.handle, email: email, usd: usd,
          success_url: location.origin + '/?topped_up=1', cancel_url: location.origin + '/' }),
      });
      const d = await r.json();
      if (d.success && d.url) location.href = d.url; else alert(d.error || 'Could not start top-up.');
    } catch (e) { alert('Top-up is unavailable right now.'); }
  }

  // Card image, or a deterministic gradient+monogram placeholder when there's none —
  // keeps the grid even and looks intentional (colors derive from the title, so a
  // listing always gets the same tile). #175.
  function hashHue(s) { let h = 0; for (let i = 0; i < (s || '').length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0; return h % 360; }
  function cardImg(l) {
    if (l.image_url) return `<img class="cx-card-img" src="${esc(l.image_url)}" alt="">`;
    const title = l.title || '';
    const h1 = hashHue(title), h2 = (h1 + 38) % 360;
    const words = title.trim().split(/\s+/).filter(Boolean);
    const mono = (words.slice(0, 2).map(w => w[0]).join('') || '◇').toUpperCase();
    const tag = l.category || (l.product_type && l.product_type !== 'download' ? l.product_type : '');
    return `<div class="cx-card-img cx-card-ph" style="background:linear-gradient(135deg,hsl(${h1},42%,30%),hsl(${h2},48%,17%))">` +
      `<span class="cx-ph-mono">${esc(mono)}</span>${tag ? `<span class="cx-ph-tag">${esc(tag)}</span>` : ''}</div>`;
  }

  // Unified capability status (#190) — same taxonomy as the Action Board cards,
  // so the Market lens speaks the same language. Derived client-side from the
  // listing + the viewer's tier.
  const _MKT_BADGE = { available: 'Available', needs_agent: 'Needs agent',
    coming_soon: 'Coming soon', wanted: 'Build this', building: 'Building', active: 'Owned' };
  function _mktStatus(l) {
    const hasAgent = !!(window.MC && MC.tier && (MC.tier.has_agent || (MC.tier.level || 0) >= 20));
    if (l.status === 'building') return 'building';
    if (l.kind === 'wanted') return 'wanted';
    if (l.kind === 'first_party' && !(l.tiers && l.tiers.length)) return 'coming_soon';
    if (l.requires_agent && !hasAgent) return 'needs_agent';
    return 'available';
  }
  function mktBadge(l) {
    const s = _mktStatus(l);
    return _MKT_BADGE[s] ? `<span class="cx-status cx-st-${s}">${_MKT_BADGE[s]}</span>` : '';
  }

  function renderCard(l) {
    const sellerHtml = l.seller_handle
      ? `by <a class="cx-seller-link" data-profile="${esc(l.seller_handle)}">@${esc(l.seller_handle)}</a>`
      : 'by ' + esc(l.seller || '—');
    // #128 — the trust mark: the seller was tuning-compliant when they published.
    const tunedBadge = l.tuned_safe
      ? '<span class="cx-tuned" title="Seller is tuning — a Tuned + Safe offering">✓ Tuned + Safe</span>'
      : '';
    // External / linked offering — discovery + link-out (the seller fulfills on their own
    // store). No in-app transaction; just send the buyer there.
    if (l.link_url) {
      return `<div class="cx-card">
        ${cardImg(l)}
        <div class="cx-card-title">${esc(l.title)} <span class="cx-ext-tag">↗ store</span></div>
        <div class="cx-card-seller">${sellerHtml}</div>
        ${l.description ? '<div class="cx-card-desc">' + esc(l.description) + '</div>' : ''}
        <div class="cx-tiers"><a class="cx-buy cx-visit" href="${esc(l.link_url)}" target="_blank" rel="noopener noreferrer">Visit store →</a></div>
      </div>`;
    }
    // "Wanted" gap — nobody's built it yet. The door is the GitHub issue (M46):
    // fork → build on a branch → PR referencing the issue. No dead clicks — when
    // the issue doesn't exist yet, the button links the repo itself.
    if (l.kind === 'wanted') {
      const cat = l.category ? `<span class="cx-ext-tag">${esc(l.category)}</span>` : '';
      const buildHref = l.issue_url || l.spec_ref || MKT_REPO_URL;
      return `<div class="cx-card cx-card-wanted">
        ${cardImg(l)}
        <div class="cx-card-title">${esc(l.title)} ${cat}</div>
        ${l.description ? '<div class="cx-card-desc">' + esc(l.description) + '</div>' : ''}
        <div class="cx-wanted-cta">Nobody's built this yet — take it over and it's yours to sell in the Haven.</div>
        <div class="cx-tiers"><a class="cx-buy cx-build" href="${esc(buildHref)}" target="_blank" rel="noopener noreferrer">Build this ↗</a></div>
      </div>`;
    }
    // Claimed and being built — stays on the shelf showing who's on it (read-only).
    if (l.status === 'building') {
      const who = l.seller_handle
        ? `<a class="cx-seller-link" data-profile="${esc(l.seller_handle)}">@${esc(l.seller_handle)}</a>`
        : 'someone';
      const cat = l.category ? `<span class="cx-ext-tag">${esc(l.category)}</span>` : '';
      return `<div class="cx-card cx-card-building">
        ${cardImg(l)}
        <div class="cx-card-title">${esc(l.title)} ${cat} ${mktBadge(l)}</div>
        <div class="cx-wanted-cta">Being built by ${who} — not ready yet.</div>
      </div>`;
    }
    // First-party anchor LP is building — a stub, not claimable. The card shows
    // its MAKER: the build-team agent who owns this tool (Chords 7/4 — the
    // marketplace as the team's visible workshop).
    if (l.kind === 'first_party' && !(l.tiers && l.tiers.length)) {
      return `<div class="cx-card">
        ${cardImg(l)}
        <div class="cx-card-title">${esc(l.title)} ${mktBadge(l)}</div>
        ${l.description ? '<div class="cx-card-desc">' + esc(l.description) + '</div>' : ''}
        ${agentChip(l)}
        <div class="cx-tiers"><span class="cx-ext-tag">Coming from Lucid Principles</span></div>
      </div>`;
    }
    // LP's built-in mirrors ship WITH every Cove (Chords 7/4): the card presents
    // as installed and points at Settings → Mirrors where they already live.
    // Shortcut list for launch — replaced by real entitlement checks when the
    // premium unlock wires up.
    const COVE_INCLUDED = { 'music-mirror': 1, 'tao-mirror': 1, 'scripture-mirror': 1 };
    if (COVE_INCLUDED[l.slug]) {
      return `<div class="cx-card">
        ${cardImg(l)}
        <div class="cx-card-title">${esc(l.title)} <span class="cx-status cx-st-active">✓ Included</span> ${tunedBadge}</div>
        <div class="cx-card-seller">${sellerHtml}</div>
        ${l.description ? '<div class="cx-card-desc">' + esc(l.description) + '</div>' : ''}
        <div class="cx-tiers"><span class="cx-ext-tag">Already in your Cove — manage it in Settings → Mirrors</span></div>
      </div>`;
    }
    const creditsOn = !!(mktCfg && mktCfg.credits_enabled);
    const tiers = (l.tiers || []).filter(t => creditsOn || t.settlement !== 'credits').map(t => {
      const credit = t.settlement === 'credits' && t.price_credits > 0;
      const price = credit ? (t.price_credits + ' LPC')
        : (t.price_cents > 0 ? '$' + (t.price_cents / 100).toFixed(2) : 'Free');
      const label = (credit || t.price_cents > 0) ? 'Buy' : 'Get';
      return `<div class="cx-tier"><span class="cx-tier-name">${esc(t.name)} · ${price}</span>` +
        `<button class="cx-buy" data-tier="${t.tier_id}">${label}</button></div>`;
    }).join('');
    // #169 — Hire (pay-now-deliver-later, escrow) is available against any
    // seller; commission custom work beyond the fixed-price tiers.
    const hireBtn = (l.seller_handle && mktCfg && mktCfg.hire_visible)
      ? `<button class="cx-buy cx-hire" data-hire="${esc(l.seller_handle)}" data-hire-title="${esc(l.title)}" style="background:transparent;border:1px solid var(--daily-freq,#5ce1e6);color:var(--daily-freq,#5ce1e6)">Hire</button>`
      : '';
    const _st = _mktStatus(l);
    return `<div class="cx-card${_st === 'needs_agent' ? ' cx-card-needsagent' : ''}">
      ${cardImg(l)}
      <div class="cx-card-title">${esc(l.title)} ${mktBadge(l)} ${tunedBadge}</div>
      <div class="cx-card-seller">${sellerHtml}</div>
      ${l.description ? '<div class="cx-card-desc">' + esc(l.description) + '</div>' : ''}
      <div class="cx-tiers">${tiers}${hireBtn}</div>
    </div>`;
  }

  // Wire buy + seller-profile links inside a freshly-rendered container.
  function wireMarketEls(root) {
    const r = root || document;
    r.querySelectorAll('.cx-buy[data-tier]').forEach(b => b.onclick = () => buyTier(parseInt(b.dataset.tier, 10), b));
    // Wanted cards are now LINKS (issue/repo) — only wire legacy claim BUTTONS (data-claim).
    r.querySelectorAll('.cx-build[data-claim]').forEach(b => b.onclick = () => claimListing(parseInt(b.dataset.claim, 10), b));
    r.querySelectorAll('.cx-seller-link').forEach(a => a.onclick = () => openProfile(a.dataset.profile));
    r.querySelectorAll('.cx-hire[data-hire]').forEach(b => b.onclick = () => hireSeller(b.dataset.hire, b.dataset.hireTitle, b));
    r.querySelectorAll('.cx-agent-chip[data-agent]').forEach(ch => enhanceAgentChip(ch));
  }

  // #169 — commission a seller: holds your LPC in escrow until they deliver.
  async function hireSeller(sellerHandle, refTitle, btn) {
    const amount = parseInt(prompt(`Hire @${sellerHandle} — how many LPC to escrow? (released on delivery)`, '500'), 10);
    if (!amount || amount <= 0) return;
    const title = prompt('What are you hiring them for?', refTitle || 'Custom work') || refTitle || 'Custom work';
    const orig = btn.textContent; btn.disabled = true; btn.textContent = '…';
    try {
      const r = await fetch('/api/hire/request', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ seller_handle: sellerHandle, amount_credits: amount, title }),
      });
      const d = await r.json().catch(() => ({}));
      if (r.status === 402) { alert('Not enough LPC to escrow that — use Top Up first.'); }
      else if (!r.ok) { alert('Could not start the hire (' + r.status + ').'); }
      else { alert(`Hired. ${amount} LPC held in escrow for "${title}". Release it from your orders once they deliver.`); }
    } catch (e) { /* ignore */ }
    btn.textContent = orig; btn.disabled = false;
  }

  // ── Stock the board (M46): a wanted/idea card the whole Haven can see and take
  //    over. Renders an inline form in the market body; posts through the Cove to
  //    the hub (fleet-gated there — member Coves without the secret get a polite 403).
  function addToBoard() {
    const body = document.getElementById('cx-marketbody');
    if (!body) return;
    body.innerHTML =
      '<div class="cx-card" style="max-width:560px;margin:0 auto">' +
      '<div class="cx-card-title">Add to the board</div>' +
      '<div class="cx-card-desc">An idea the Haven should have — free, for sale, or for someone to build. It shows as a "Build this" card until it exists.</div>' +
      '<div style="display:flex;flex-direction:column;gap:8px;margin-top:10px">' +
      '<input id="aw-title" placeholder="Title (e.g. Family recipe keeper skill)" style="padding:8px 10px">' +
      '<input id="aw-category" placeholder="Category (e.g. skills, personas, flows, mirrors, gpu, services)" style="padding:8px 10px">' +
      '<textarea id="aw-desc" rows="3" placeholder="What it is + why it matters (this is the pitch a builder sees)" style="padding:8px 10px"></textarea>' +
      '<input id="aw-spec" placeholder="Spec / details link (optional)" style="padding:8px 10px">' +
      '<div style="display:flex;gap:8px"><button class="cx-buy" id="aw-save">Add it</button>' +
      '<button class="cx-wbtn" id="aw-cancel">Cancel</button></div>' +
      '<div id="aw-msg" style="font-size:12px;opacity:.8"></div></div></div>';
    document.getElementById('aw-cancel').onclick = doMarketSearch;
    document.getElementById('aw-save').onclick = async () => {
      const title = document.getElementById('aw-title').value.trim();
      const msg = document.getElementById('aw-msg');
      if (!title) { msg.textContent = 'A title is the one required thing.'; return; }
      msg.textContent = 'Adding…';
      try {
        const r = await fetch('/api/market/wanted', {
          method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            title: title,
            category: document.getElementById('aw-category').value.trim() || null,
            description: document.getElementById('aw-desc').value.trim(),
            spec_ref: document.getElementById('aw-spec').value.trim() || null,
          }),
        });
        const d = await r.json().catch(() => ({}));
        if (!r.ok || d.ok === false) {
          msg.textContent = r.status === 403
            ? 'This Cove can’t stock the shared board (hub-gated).'
            : 'Could not add it (' + r.status + ').';
          return;
        }
        msg.textContent = '✓ On the board.';
        setTimeout(doMarketSearch, 700);
      } catch (e) { msg.textContent = 'Could not reach the hub.'; }
    };
  }

  // Claim a "wanted" gap → it becomes your draft listing to build + sell.
  async function claimListing(listingId, btn) {
    const orig = btn.textContent;
    btn.disabled = true; btn.textContent = '…';
    try {
      const r = await fetch('/api/market/claim', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ listing_id: listingId }),
      });
      if (!r.ok) { btn.textContent = orig; btn.disabled = false; alert('Could not claim this (' + r.status + ').'); return; }
      btn.textContent = '✓ Yours — build it';
      alert("It's yours to build. Find it under your offerings (status: draft), finish it, set a price, and publish.");
    } catch (e) { btn.textContent = orig; btn.disabled = false; }
  }

  // ── Edit your Presence presentation (manage side; same /api/profile/me the Team
  //    page will also surface). Operator avatar/bio/skills + agent avatar. ──
  async function editProfile() {
    // #176: the unified Presence page is the single editor now.
    if (window.PresenceProfile) return window.PresenceProfile.openMe({ edit: true });
    let p;
    try { p = await (await fetch('/api/profile/me', { credentials: 'same-origin' })).json(); }
    catch (e) { alert('Could not load your profile.'); return; }
    const op = p.operator || {}, ag = p.agent || {}, tax = p.taxonomy || {};
    const selected = new Set((op.skills || []).map(s => String(s)));
    const chipRows = Object.keys(tax).map(group =>
      `<div class="cx-chipgroup"><div class="cx-chipgroup-h">${esc(group)}</div>` +
      tax[group].map(s => `<span class="cx-chip cx-chip-tog${selected.has(s) ? ' on' : ''}" data-skill="${esc(s)}">${esc(s)}</span>`).join('') +
      '</div>').join('');
    const m = document.createElement('div');
    m.className = 'cx-modal-overlay';
    m.onclick = (e) => { if (e.target === m) m.remove(); };
    m.innerHTML = `<div class="cx-modal">
      <button class="cx-modal-x" title="Close">&times;</button>
      <div class="cx-prof-name" style="margin-bottom:2px;">Your Presence</div>
      <div class="cx-prof-handle" style="margin-bottom:12px;">@${esc(p.handle)}${ag.name ? ' · ' + esc(ag.name) + (ag.cove ? ' ' + esc(ag.cove) : '') : ''}</div>
      <div class="cx-flabel">Your photo</div>
      <div class="cx-avaedit">
        ${op.avatar_url ? `<img id="pf-ava-img" class="cx-ava sm" src="${esc(op.avatar_url)}" alt="">` : '<div id="pf-ava-img" class="cx-ava sm cx-ava-ph"></div>'}
        <input type="file" id="pf-ava-file" accept="image/png,image/jpeg,image/webp,image/gif">
      </div>
      <label class="cx-flabel">Bio<textarea id="pf-bio" rows="3" placeholder="Who you are, what you're about…">${esc(op.bio || '')}</textarea></label>
      <div class="cx-flabel">Agent avatar</div>
      <div class="cx-avaedit">
        ${ag.avatar_url ? `<img id="pf-aava-img" class="cx-ava sm" src="${esc(ag.avatar_url)}" alt="">` : '<div id="pf-aava-img" class="cx-ava sm cx-ava-ph"></div>'}
        <input type="file" id="pf-aava-file" accept="image/png,image/jpeg,image/webp,image/gif">
      </div>
      <div class="cx-flabel">Skills</div>
      <div class="cx-chipsel">${chipRows}</div>
      <div class="cx-sell-actions">
        <button class="cx-wbtn" id="pf-save">Save</button>
        <span id="pf-status" class="sf-status"></span>
      </div>
    </div>`;
    document.body.appendChild(m);
    m.querySelector('.cx-modal-x').onclick = () => m.remove();
    m.querySelectorAll('.cx-chip-tog').forEach(ch => ch.onclick = () => {
      const s = ch.dataset.skill;
      if (selected.has(s)) { selected.delete(s); ch.classList.remove('on'); }
      else { selected.add(s); ch.classList.add('on'); }
    });
    // Avatar uploads persist immediately (separate from the bio/skills Save).
    const wireUpload = (kind, fileId, imgId) => {
      const fi = m.querySelector('#' + fileId);
      if (!fi) return;
      fi.onchange = async () => {
        const f = fi.files && fi.files[0];
        if (!f) return;
        const fd = new FormData(); fd.append('file', f);
        const status = m.querySelector('#pf-status'); status.textContent = 'Uploading…';
        try {
          const r = await fetch('/api/profile/avatar?kind=' + kind, { method: 'POST', credentials: 'same-origin', body: fd });
          if (!r.ok) { status.textContent = 'Upload failed (' + r.status + ').'; return; }
          const d = await r.json();
          const img = m.querySelector('#' + imgId);
          if (img && d.url) {
            if (img.tagName === 'IMG') { img.src = d.url + '?t=' + Date.now(); }
            else { const ni = document.createElement('img'); ni.id = imgId; ni.className = 'cx-ava sm'; ni.src = d.url; img.replaceWith(ni); }
          }
          status.textContent = '✓ Photo updated';
          loadWallet();  // harmless refresh; keeps the bar current
        } catch (e) { status.textContent = 'Upload failed — try again.'; }
      };
    };
    wireUpload('operator', 'pf-ava-file', 'pf-ava-img');
    wireUpload('agent', 'pf-aava-file', 'pf-aava-img');
    m.querySelector('#pf-save').onclick = async () => {
      const status = m.querySelector('#pf-status');
      status.textContent = 'Saving…';
      try {
        const r = await fetch('/api/profile/me', {
          method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            bio: m.querySelector('#pf-bio').value.trim(),
            skills: Array.from(selected),
          }),
        });
        if (!r.ok) { status.textContent = 'Save failed (' + r.status + ').'; return; }
        status.textContent = '✓ Saved';
        setTimeout(() => m.remove(), 600);
      } catch (e) { status.textContent = 'Save failed — try again.'; }
    };
  }

  // ── A Presence profile (Operator + Agent + offerings), linked from a card ──
  async function openProfile(handle) {
    // #176: the unified Presence page is the single showcase now.
    if (window.PresenceProfile) return window.PresenceProfile.open(handle);
    let p;
    try {
      const r = await fetch('/api/profile/' + encodeURIComponent(handle), { credentials: 'same-origin' });
      if (!r.ok) throw new Error('profile ' + r.status);
      p = await r.json();
    } catch (e) { alert('That profile is unavailable right now.'); return; }
    const op = p.operator || {}, ag = p.agent || {};
    const chips = (arr) => (arr || []).map(s => `<span class="cx-chip">${esc(s)}</span>`).join('');
    const agentLine = [ag.archetype, ag.frequency].filter(Boolean).join(' · ');
    const offers = (p.offerings || []).length
      ? '<div class="cx-market">' + p.offerings.map(renderCard).join('') + '</div>'
      : '<div class="connect-empty" style="padding:8px">No offerings yet.</div>';
    const m = document.createElement('div');
    m.className = 'cx-modal-overlay'; m.id = 'cx-profile-modal';
    m.onclick = (e) => { if (e.target === m) m.remove(); };
    m.innerHTML = `<div class="cx-modal">
      <button class="cx-modal-x" title="Close">&times;</button>
      <div class="cx-prof-head">
        ${op.avatar_url ? `<img class="cx-ava" src="${esc(op.avatar_url)}" alt="">` : '<div class="cx-ava cx-ava-ph"></div>'}
        <div><div class="cx-prof-name">${esc(op.name || ('@' + p.handle))}</div>
          <div class="cx-prof-handle">@${esc(p.handle)}</div></div>
      </div>
      ${op.bio ? `<div class="cx-prof-bio">${esc(op.bio)}</div>` : ''}
      ${chips(op.skills) ? `<div class="cx-chips">${chips(op.skills)}</div>` : ''}
      ${ag.name ? `<div class="cx-prof-agent">
        ${ag.avatar_url ? `<img class="cx-ava sm" src="${esc(ag.avatar_url)}" alt="">` : '<div class="cx-ava sm cx-ava-ph"></div>'}
        <div><div class="cx-prof-aname">${esc(ag.name)}${ag.cove ? (' ' + esc(ag.cove)) : ''}</div>
          <div class="cx-prof-ameta">${esc(agentLine || 'Agent')}${ag.tuning_key ? (' · ' + esc(ag.tuning_key)) : ''}</div></div></div>` : ''}
      ${chips(ag.skills) ? `<div class="cx-chips">${chips(ag.skills)}</div>` : ''}
      <div class="cx-prof-offers-h">Offerings</div>
      ${offers}
    </div>`;
    document.body.appendChild(m);
    m.querySelector('.cx-modal-x').onclick = () => m.remove();
    wireMarketEls(m);
  }

  async function buyTier(tierId, btn) {
    const orig = btn.textContent;
    btn.disabled = true; btn.textContent = '…';
    try {
      const r = await fetch('/api/market/buy', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tier_id: tierId }),
      });
      if (r.status === 402) {  // not enough LPC
        btn.textContent = orig; btn.disabled = false;
        alert('Not enough LPC for this — use Top Up to add credits.');
        return;
      }
      const d = await r.json();
      if (d.checkout_url) { window.location.href = d.checkout_url; return; }   // Stripe-direct rail
      if (d.granted) {                                                          // free or credit rail
        btn.textContent = '✓ Owned';
        if (d.rail === 'credits') loadWallet();
        // Appointment fulfillment → open a thread with the seller to set a time.
        if (d.fulfillment === 'appointment' && d.seller_handle) {
          btn.textContent = '✓ Booked';
          if (window.coordinateAppointment) window.coordinateAppointment(d.seller_handle, d.item_title);
        }
        return;
      }
      btn.textContent = orig; btn.disabled = false;
    } catch (e) { btn.textContent = 'Retry'; btn.disabled = false; }
  }

  // ── Sell: list one of your offerings (manage your storefront) ──
  function renderSell() {
    const body = document.getElementById('cx-marketbody');
    if (!body) return;
    body.innerHTML = `
      <div class="cx-sellform">
        <div class="cx-sell-h"><button class="cx-back" id="cx-sellback">← Market</button><span>List an offering</span></div>
        <label>Title<input id="sf-title" placeholder="e.g. Stoic Mirror"></label>
        <label>Description<textarea id="sf-desc" rows="2" placeholder="What is it?"></textarea></label>
        <label>Settle in
          <select id="sf-settle">
            ${(mktCfg && mktCfg.credits_enabled) ? '<option value="credits">Lucid Principles Credits (micro/digital)</option>' : ''}
            <option value="stripe">Cash via Stripe ($12 minimum)</option>
            <option value="external">External — link to my own store</option>
          </select></label>
        <label id="sf-pricewrap">Price <span class="sf-unit" id="sf-unit">(US cents, e.g. 1200 = $12)</span>
          <input id="sf-price" type="number" min="0" value="1200"></label>
        <label id="sf-linkwrap" style="display:none">Store link
          <input id="sf-link" placeholder="https://my-store.com/product"></label>
        <div id="sf-connect" class="sf-status"></div>
        <label>Image <span class="sf-unit">(optional, ≤1MB)</span>
          <input type="file" id="sf-image" accept="image/png,image/jpeg,image/webp,image/gif"></label>
        <div id="sf-image-status" class="sf-status"></div>
        <div class="cx-sell-actions">
          <button class="cx-wbtn" id="sf-submit">Publish</button>
          <span id="sf-status" class="sf-status"></span>
        </div>
      </div>`;
    const back = document.getElementById('cx-sellback'); if (back) back.onclick = renderMarket;
    const settle = document.getElementById('sf-settle');
    const unit = document.getElementById('sf-unit');
    if (settle) settle.onchange = () => {
      const ext = settle.value === 'external';
      document.getElementById('sf-pricewrap').style.display = ext ? 'none' : '';
      document.getElementById('sf-linkwrap').style.display = ext ? '' : 'none';
      if (!ext) unit.textContent = settle.value === 'credits' ? '(LPC, e.g. 100 = $1)' : '(US cents, e.g. 5000 = $50)';
      maybeShowConnectHint(settle.value);
    };
    sellImageUrl = '';
    const imgIn = document.getElementById('sf-image');
    if (imgIn) imgIn.onchange = async () => {
      const f = imgIn.files && imgIn.files[0]; if (!f) return;
      const st = document.getElementById('sf-image-status'); st.textContent = 'Uploading…';
      const fd = new FormData(); fd.append('file', f);
      try {
        const r = await fetch('/api/profile/image', { method: 'POST', credentials: 'same-origin', body: fd });
        if (!r.ok) { st.textContent = (r.status === 400 ? 'Too large (≤1MB) or wrong type.' : 'Upload failed (' + r.status + ').'); return; }
        sellImageUrl = (await r.json()).url || '';
        st.textContent = '✓ Image attached';
      } catch (e) { st.textContent = 'Upload failed — try again.'; }
    };
    const sub = document.getElementById('sf-submit'); if (sub) sub.onclick = submitListing;
  }

  // ── Your Library — everything you own (purchased/installed), grouped by type.
  //    This is the same set the Action Board's Tools / Flows tabs surface under
  //    "Installed / Purchased" (alongside the Cove standards). ──
  async function renderLibrary() {
    const body = document.getElementById('cx-marketbody');
    if (!body) return;
    document.getElementById('cx-facets').innerHTML = '';
    body.innerHTML = '<div class="cx-spin">Loading your library…</div>';
    let items = [];
    try {
      const r = await fetch('/api/market/library', { credentials: 'same-origin' });
      if (r.ok) items = (await r.json()).items || [];
    } catch (e) { body.innerHTML = '<div class="connect-empty">Library unavailable right now.</div>'; return; }
    const GROUPS = [
      ['tool', 'Tools'], ['flow', 'Creation Flows'], ['persona', 'Agent Personas'],
      ['mirror', 'Mirrors'], ['skill', 'Skills'], ['external', 'Linked'],
    ];
    const labelFor = (t) => (GROUPS.find(g => g[0] === t) || [null, 'Other'])[1];
    const byGroup = {};
    items.forEach(it => { const k = labelFor(it.product_type); (byGroup[k] = byGroup[k] || []).push(it); });
    const head = '<div class="cx-sell-h"><button class="cx-back" id="cx-libback">← Market</button>' +
      '<span>Your Library — what you own</span></div>';
    if (!items.length) {
      body.innerHTML = head + '<div class="connect-empty" style="padding:8px">Nothing yet. Buy a tool or flow in the Market and it shows up here — and in your Action Board.</div>';
      document.getElementById('cx-libback').onclick = renderMarket;
      return;
    }
    let html = head;
    Object.keys(byGroup).forEach(group => {
      html += `<div class="cx-prof-offers-h">${esc(group)}</div><div class="cx-market">` +
        byGroup[group].map(it => `<div class="cx-card">
          <div class="cx-card-title">${esc(it.title)}${it.kind === 'first_party' ? ' <span class="cx-ext-tag">LP</span>' : ''}</div>
          ${it.description ? '<div class="cx-card-desc">' + esc(it.description) + '</div>' : ''}
          <div class="cx-tiers">${it.link_url
            ? `<a class="cx-buy cx-visit" href="${esc(it.link_url)}" target="_blank" rel="noopener noreferrer">Open ↗</a>`
            : '<span class="cx-ext-tag">Owned · available in your Action Board</span>'}</div>
        </div>`).join('') + '</div>';
    });
    body.innerHTML = html;
    document.getElementById('cx-libback').onclick = renderMarket;
    wireMarketEls(body);
  }

  // ── My Offerings — your own listings, with manage actions (#175) ──
  let _mineItems = [];
  let _editImageUrl = '';   // current image for the offering being edited
  async function renderMine() {
    const body = document.getElementById('cx-marketbody');
    if (!body) return;
    document.getElementById('cx-facets').innerHTML = '';
    body.innerHTML = '<div class="cx-spin">Loading your offerings…</div>';
    try {
      const r = await fetch('/api/market/mine', { credentials: 'same-origin' });
      _mineItems = r.ok ? ((await r.json()).listings || []) : [];
    } catch (e) { body.innerHTML = '<div class="connect-empty">Couldn\'t load your offerings.</div>'; return; }
    const head = '<div class="cx-sell-h"><button class="cx-back" id="cx-mineback">← Market</button>' +
      '<span>Your offerings</span></div>';
    if (!_mineItems.length) {
      body.innerHTML = head + '<div class="connect-empty" style="padding:8px">Nothing yet. Hit Sell to list something, or claim a "wanted" gap with Build this.</div>';
      document.getElementById('cx-mineback').onclick = renderMarket;
      return;
    }
    const cards = _mineItems.map(l => {
      const t = (l.tiers || [])[0] || {};
      const price = t.settlement === 'credits' && t.price_credits ? (t.price_credits + ' LPC')
        : (t.price_cents ? '$' + (t.price_cents / 100).toFixed(2) : (l.product_type === 'external' ? 'external' : 'free'));
      const badge = l.status === 'draft' ? '<span class="cx-ext-tag">draft</span>'
        : l.status === 'active' ? '<span class="cx-ext-tag">live</span>'
        : l.status === 'inactive' ? '<span class="cx-ext-tag">unlisted</span>'
        : '<span class="cx-ext-tag">' + esc(l.status) + '</span>';
      const acts = [];
      if (l.status !== 'active') acts.push(`<button class="cx-wbtn" data-pub="${l.id}">Publish</button>`);
      acts.push(`<button class="cx-wbtn" data-edit="${l.id}">Edit</button>`);
      if (l.status === 'active') acts.push(`<button class="cx-wbtn" data-unlist="${l.id}">Unlist</button>`);
      return `<div class="cx-card">
        ${cardImg(l)}
        <div class="cx-card-title">${esc(l.title)}</div>
        <div class="cx-card-seller">${badge} · ${esc(price)}</div>
        ${l.description ? '<div class="cx-card-desc">' + esc(l.description) + '</div>' : ''}
        <div class="cx-mine-actions">${acts.join('')}</div>
        <div class="cx-mine-status" id="cx-mine-st-${l.id}"></div>
      </div>`;
    }).join('');
    body.innerHTML = head + '<div class="cx-market">' + cards + '</div>';
    document.getElementById('cx-mineback').onclick = renderMarket;
    body.querySelectorAll('[data-pub]').forEach(b => b.onclick = () => mineSetStatus(b.dataset.pub, 'active', b));
    body.querySelectorAll('[data-unlist]').forEach(b => b.onclick = () => mineSetStatus(b.dataset.unlist, 'inactive', b));
    body.querySelectorAll('[data-edit]').forEach(b => b.onclick = () => renderEditOffering(_mineItems.find(x => String(x.id) === b.dataset.edit)));
  }

  async function mineSetStatus(listingId, status, btn) {
    const st = document.getElementById('cx-mine-st-' + listingId);
    const orig = btn.textContent; btn.disabled = true; btn.textContent = '…';
    try {
      const r = await fetch('/api/market/mine/status', {
        method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ listing_id: parseInt(listingId, 10), status }),
      });
      if (!r.ok) {
        btn.disabled = false; btn.textContent = orig;
        if (st) st.textContent = r.status === 400
          ? 'Set a price first (Edit) — and connect Stripe for cash tiers.'
          : 'Could not update (' + r.status + ').';
        return;
      }
      renderMine();
    } catch (e) { btn.disabled = false; btn.textContent = orig; }
  }

  function renderEditOffering(l) {
    if (!l) return;
    const body = document.getElementById('cx-marketbody');
    if (!body) return;
    const t = (l.tiers || [])[0] || {};
    const settle = t.settlement || (l.product_type === 'external' ? 'external' : 'credits');
    const priceVal = settle === 'credits' ? (t.price_credits || 0) : (t.price_cents || 0);
    _editImageUrl = l.image_url || '';
    body.innerHTML = `
      <div class="cx-sellform">
        <div class="cx-sell-h"><button class="cx-back" id="cx-editback">← Offerings</button><span>Edit offering</span></div>
        <label>Title<input id="ef-title" value="${esc(l.title || '')}"></label>
        <label>Description<textarea id="ef-desc" rows="2">${esc(l.description || '')}</textarea></label>
        <label>Settle in
          <select id="ef-settle">
            <option value="credits"${settle === 'credits' ? ' selected' : ''}>Lucid Principles Credits</option>
            <option value="stripe"${settle === 'stripe' ? ' selected' : ''}>Cash via Stripe</option>
            <option value="external"${settle === 'external' ? ' selected' : ''}>External — link to my store</option>
          </select></label>
        <label id="ef-pricewrap"${settle === 'external' ? ' style="display:none"' : ''}>Price <span class="sf-unit" id="ef-unit"></span>
          <input id="ef-price" type="number" min="0" value="${priceVal}"></label>
        <label id="ef-linkwrap"${settle === 'external' ? '' : ' style="display:none"'}>Store link
          <input id="ef-link" value="${esc(l.link_url || '')}"></label>
        <label>Image <span class="sf-unit">(optional, ≤1MB)</span>
          <input type="file" id="ef-image" accept="image/png,image/jpeg,image/webp,image/gif"></label>
        <img id="ef-image-preview" class="cx-card-img" alt="" style="max-width:140px;border-radius:8px;${l.image_url ? '' : 'display:none'}" src="${esc(l.image_url || '')}">
        <div id="ef-image-status" class="sf-status"></div>
        <div id="ef-connect" class="sf-status"></div>
        <div class="cx-sell-actions"><button class="cx-wbtn" id="ef-save">Save</button>
          <span id="ef-status" class="sf-status"></span></div>
      </div>`;
    document.getElementById('cx-editback').onclick = renderMine;
    const efImg = document.getElementById('ef-image');
    if (efImg) efImg.onchange = async () => {
      const f = efImg.files && efImg.files[0]; if (!f) return;
      const st = document.getElementById('ef-image-status'); st.textContent = 'Uploading…';
      const fd = new FormData(); fd.append('file', f);
      try {
        const r = await fetch('/api/profile/image', { method: 'POST', credentials: 'same-origin', body: fd });
        if (!r.ok) { st.textContent = (r.status === 400 ? 'Too large (≤1MB) or wrong type.' : 'Upload failed (' + r.status + ').'); return; }
        _editImageUrl = (await r.json()).url || _editImageUrl;
        const pv = document.getElementById('ef-image-preview');
        if (pv && _editImageUrl) { pv.src = _editImageUrl + '?t=' + Date.now(); pv.style.display = ''; }
        st.textContent = '✓ Image attached';
      } catch (e) { st.textContent = 'Upload failed — try again.'; }
    };
    const settleSel = document.getElementById('ef-settle');
    const unit = document.getElementById('ef-unit');
    const setUnit = () => { unit.textContent = settleSel.value === 'credits' ? '(LPC, 100 = $1)' : '(US cents, 5000 = $50)'; };
    setUnit();
    settleSel.onchange = () => {
      const ext = settleSel.value === 'external';
      document.getElementById('ef-pricewrap').style.display = ext ? 'none' : '';
      document.getElementById('ef-linkwrap').style.display = ext ? '' : 'none';
      if (!ext) setUnit();
      const ec = document.getElementById('ef-connect');
      if (settleSel.value === 'stripe') maybeShowConnectHintEl(ec); else ec.textContent = '';
    };
    document.getElementById('ef-save').onclick = () => saveEditOffering(l);
  }

  async function maybeShowConnectHintEl(el) {
    if (!el) return;
    el.textContent = 'Checking Stripe payouts…';
    const cs = await stripeConnectStatus();
    if (cs.onboarded) { el.textContent = '✓ Stripe payouts connected.'; return; }
    connectPrompt(el);
  }

  async function saveEditOffering(l) {
    const status = document.getElementById('ef-status');
    const settle = document.getElementById('ef-settle').value;
    const isExt = settle === 'external';
    const price = parseInt(document.getElementById('ef-price').value, 10) || 0;
    const link = isExt ? (document.getElementById('ef-link').value || '').trim() : '';
    if (isExt && !/^https?:\/\//.test(link)) { status.textContent = 'Enter your store link (https://…).'; return; }
    if (settle === 'stripe') {
      const cs = await stripeConnectStatus();
      if (!cs.onboarded) { connectPrompt(status, 'Cash sales need Stripe payouts. '); return; }
    }
    const payload = {
      listing_id: l.id,
      title: (document.getElementById('ef-title').value || '').trim(),
      description: (document.getElementById('ef-desc').value || '').trim(),
      image_url: _editImageUrl || '',
      tier: { settlement: settle, price_credits: settle === 'credits' ? price : 0, price_cents: settle === 'stripe' ? price : 0 },
    };
    if (isExt) { payload.product_type = 'external'; payload.delivery_ref = link; }
    else if (l.product_type === 'external') { payload.product_type = 'download'; payload.delivery_ref = ''; }
    status.textContent = 'Saving…';
    try {
      const r = await fetch('/api/market/mine/edit', {
        method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!r.ok) { status.textContent = 'Save failed (' + r.status + ').'; return; }
      status.textContent = '✓ Saved';
      setTimeout(renderMine, 500);
    } catch (e) { status.textContent = 'Save failed — try again.'; }
  }

  // ── Stripe Connect (#178): a cash/Stripe tier needs the seller onboarded for payouts ──
  async function stripeConnectStatus() {
    try {
      const r = await fetch('/api/market/connect/status', { credentials: 'same-origin' });
      if (r.ok) return await r.json();
    } catch (e) { /* fall through */ }
    return { connected: false, onboarded: false };
  }

  async function startStripeConnect(el) {
    if (el) el.textContent = 'Opening Stripe onboarding…';
    try {
      const r = await fetch('/api/market/connect/onboard', { method: 'POST', credentials: 'same-origin' });
      if (!r.ok) { if (el) el.textContent = 'Could not start Stripe onboarding (' + r.status + ').'; return; }
      const d = await r.json();
      if (d.url) { window.open(d.url, '_blank', 'noopener'); if (el) el.textContent = 'Finish onboarding in the new tab, then Publish again.'; }
      else if (el) el.textContent = 'Stripe onboarding link unavailable.';
    } catch (e) { if (el) el.textContent = 'Stripe onboarding failed — try again.'; }
  }

  function connectPrompt(el, lead) {
    el.textContent = (lead || 'Cash sales need Stripe payouts connected. ');
    const b = document.createElement('button');
    b.className = 'cx-wbtn'; b.textContent = 'Connect Stripe';
    b.onclick = () => startStripeConnect(el);
    el.appendChild(b);
  }

  async function maybeShowConnectHint(settleVal) {
    const el = document.getElementById('sf-connect'); if (!el) return;
    if (settleVal !== 'stripe') { el.textContent = ''; return; }
    el.textContent = 'Checking Stripe payouts…';
    const cs = await stripeConnectStatus();
    if (cs.onboarded) { el.textContent = '✓ Stripe payouts connected.'; return; }
    connectPrompt(el);
  }

  async function submitListing() {
    const title = (document.getElementById('sf-title').value || '').trim();
    const desc = (document.getElementById('sf-desc').value || '').trim();
    const settle = document.getElementById('sf-settle').value;
    const price = parseInt(document.getElementById('sf-price').value, 10) || 0;
    const status = document.getElementById('sf-status');
    if (!title) { status.textContent = 'A title is required.'; return; }
    const slug = title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') + '-' + Date.now().toString(36);
    const isExternal = settle === 'external';
    const link = isExternal ? (document.getElementById('sf-link').value || '').trim() : '';
    if (isExternal && !/^https?:\/\//.test(link)) { status.textContent = 'Enter your store link (https://…).'; return; }
    const tier = {
      name: 'Standard', entitlement_key: slug + ':standard', settlement: settle,
      price_credits: settle === 'credits' ? price : 0,
      price_cents: settle === 'stripe' ? price : 0,
    };
    // Cash/Stripe tier → seller must have Connect payouts onboarded first (#178).
    if (settle === 'stripe') {
      const cs = await stripeConnectStatus();
      if (!cs.onboarded) {
        connectPrompt(status, 'To sell for cash, connect Stripe payouts first. ');
        return;
      }
    }
    status.textContent = 'Publishing…';
    try {
      const r = await fetch('/api/market/sell', {
        method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ slug, title, description: desc,
          product_type: isExternal ? 'external' : 'download',
          delivery_ref: isExternal ? link : undefined,
          image_url: sellImageUrl || undefined, tiers: [tier] }),
      });
      if (!r.ok) {
        // 400 on a cash tier almost always = Connect onboarding incomplete — offer it.
        if (r.status === 400 && settle === 'stripe') {
          connectPrompt(status, 'Stripe payouts aren’t set up yet. ');
        } else {
          status.textContent = 'Could not publish (' + r.status + ').';
        }
        return;
      }
      status.textContent = '✓ Listed. Back to Market…';
      setTimeout(renderMarket, 700);
    } catch (e) { status.textContent = 'Publish failed — try again.'; }
  }

  // ── Spaces tree: group rooms under their Space (Cove / Haven), orphans under Direct ──
  function buildTree() {
    const rooms = client.getRooms();
    const spaces = rooms.filter(r => r.isSpaceRoom && r.isSpaceRoom());
    const byId = {}; rooms.forEach(r => { byId[r.roomId] = r; });

    const childOf = {};   // spaceId -> [room,...]
    const claimed = new Set();
    spaces.forEach(sp => {
      const kids = [];
      const evs = sp.currentState ? sp.currentState.getStateEvents('m.space.child') : [];
      (evs || []).forEach(ev => {
        const cid = ev.getStateKey && ev.getStateKey();
        if (cid && byId[cid] && !byId[cid].isSpaceRoom()) { kids.push(byId[cid]); claimed.add(cid); }
      });
      childOf[sp.roomId] = kids;
    });

    // Orphan (non-space, unclaimed) rooms -> "Direct"
    const orphans = rooms.filter(r => !(r.isSpaceRoom && r.isSpaceRoom()) && !claimed.has(r.roomId));

    const groups = [];
    spaces.sort((a, b) => roomName(a).localeCompare(roomName(b)));
    spaces.forEach(sp => {
      if ((childOf[sp.roomId] || []).length) groups.push({ label: roomName(sp), rooms: childOf[sp.roomId] });
    });
    if (orphans.length) groups.push({ label: 'Direct', rooms: orphans });
    return groups;
  }

  function roomName(room) {
    return (room && room.name) ? room.name : (room ? room.roomId.split(':')[0].replace('!', '') : '');
  }

  function renderTree() {
    const box = document.getElementById('cx-tree');
    if (!box || !client) return;
    const groups = buildTree();
    if (!groups.length) { box.innerHTML = '<div class="connect-empty">No conversations yet.<br>Use + Add to invite someone.</div>'; return; }
    box.innerHTML = groups.map(g => {
      const rows = g.rooms.slice().sort((a, b) => roomName(a).localeCompare(roomName(b))).map(r => {
        const unread = r.getUnreadNotificationCount ? (r.getUnreadNotificationCount() || 0) : 0;
        const invited = r.getMyMembership && r.getMyMembership() === 'invite';
        const badge = invited ? '<span class="cr-invite">invite</span>'
          : (unread ? '<span class="cr-unread">' + unread + '</span>' : '');
        return `<div class="connect-room${r.roomId === activeRoomId ? ' active' : ''}" data-id="${esc(r.roomId)}">
            <span class="cr-name">${esc(roomName(r))}</span>${badge}
          </div>`;
      }).join('');
      return `<div class="connect-space">${esc(g.label)}</div>${rows}`;
    }).join('');
    box.querySelectorAll('.connect-room').forEach(el => el.onclick = () => openRoom(el.dataset.id));
  }

  function openRoom(id) {
    activeRoomId = id;
    document.querySelectorAll('.connect-room').forEach(e => e.classList.toggle('active', e.dataset.id === id));
    const room = client.getRoom(id);
    const title = document.getElementById('cx-rtitle');
    if (title) {
      title.innerHTML = '<span style="display:flex;align-items:center;min-width:0"><button class="cx-back" id="cx-back" title="Back to conversations">‹</button><span style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">' + esc(roomName(room)) + '</span></span>';
      const back = document.getElementById('cx-back');
      if (back) back.onclick = () => { const p = document.getElementById('connect-panel'); if (p) p.classList.remove('show-room'); };
    }
    // Mobile: switch from the room list to the conversation view.
    const panel = document.getElementById('connect-panel');
    if (panel) panel.classList.add('show-room');
    const inp = document.getElementById('cx-input'); const snd = document.getElementById('cx-send');
    // Invite-membership rooms hold only a stripped invite server-side — composing
    // would fail forever. Gate it: auto-join our own steward's rooms, otherwise
    // show an explicit Accept bar. (batch-10 #1 / T9)
    const membership = room && room.getMyMembership ? room.getMyMembership() : 'join';
    if (membership === 'invite') {
      if (inp) inp.disabled = true;
      if (snd) snd.disabled = true;
      const inviter = inviteSender(room);
      if (isOwnServerInvite(inviter, client.getUserId())) acceptInvite(id, inviter);
      else renderAcceptBar(id, inviter);
      return;
    }
    if (inp) inp.disabled = false; if (snd) snd.disabled = false;
    renderTimeline();
    if (room && client.sendReadReceipt) {
      const evs = room.getLiveTimeline().getEvents();
      if (evs.length) { try { client.sendReadReceipt(evs[evs.length - 1]); } catch (e) {} }
    }
  }

  // The invite event carries the inviter as its sender.
  function inviteSender(room) {
    try {
      const ev = room.currentState.getStateEvents('m.room.member', client.getUserId());
      return ev && ev.getSender ? ev.getSender() : '';
    } catch (e) { return ''; }
  }

  // Join an invited room, passing the inviter's server as a via hint so the join
  // can be routed over federation. On success the membership flips to 'join' and
  // we re-open the room (now composable).
  async function acceptInvite(roomId, inviter) {
    const tl = document.getElementById('cx-timeline');
    if (tl) tl.innerHTML = '<div class="connect-empty">Joining…</div>';
    try {
      const via = serverOf(inviter);
      await client.joinRoom(roomId, via ? { viaServers: [via] } : undefined);
      renderTree();
      openRoom(roomId);
    } catch (e) {
      if (tl) {
        tl.innerHTML = '<div class="connect-empty">Couldn\'t join this conversation yet.<br>'
          + '<button class="cx-invite-accept" id="cx-inv-retry">Try again</button></div>';
        const b = document.getElementById('cx-inv-retry');
        if (b) b.onclick = () => acceptInvite(roomId, inviter);
      }
    }
  }

  // Cross-Cove/Haven invite: show who invited you + an Accept button before composing.
  function renderAcceptBar(roomId, inviter) {
    const tl = document.getElementById('cx-timeline');
    if (!tl) return;
    const who = (inviter || '').split(':')[0].replace('@', '') || 'Someone';
    tl.innerHTML = '<div class="cx-invite">'
      + '<div class="cx-invite-msg">' + esc(who) + ' invited you to this conversation.</div>'
      + '<button class="cx-invite-accept" id="cx-inv-accept">Accept</button></div>';
    const b = document.getElementById('cx-inv-accept');
    if (b) b.onclick = () => { b.disabled = true; acceptInvite(roomId, inviter); };
  }

  function renderTimeline() {
    const tl = document.getElementById('cx-timeline');
    if (!tl || !client || !activeRoomId) return;
    const room = client.getRoom(activeRoomId);
    if (!room) return;
    // Don't clobber the Accept bar on invite rooms when a sync fires renderTimeline.
    if (room.getMyMembership && room.getMyMembership() === 'invite') return;
    const me = client.getUserId();
    const events = room.getLiveTimeline().getEvents()
      .filter(e => e.getType() === 'm.room.message' && e.getContent() && e.getContent().body);
    const nearBottom = tl.scrollHeight - tl.scrollTop - tl.clientHeight < 80;
    tl.innerHTML = events.map(e => {
      const mine = e.getSender() === me;
      const who = (e.getSender() || '').split(':')[0].replace('@', '');
      return `<div class="cx-msg${mine ? ' mine' : ''}">${mine ? '' : '<div class="cx-sender">' + esc(who) + '</div>'}${esc(e.getContent().body)}</div>`;
    }).join('') || '<div class="connect-empty">No messages yet. Say hello.</div>';
    if (nearBottom) tl.scrollTop = tl.scrollHeight;
  }

  async function sendMsg() {
    const inp = document.getElementById('cx-input');
    if (!inp || !activeRoomId || !client) return;
    const body = inp.value.trim();
    if (!body) return;
    inp.value = '';
    trySend(activeRoomId, body, 0);
  }

  // Send with bounded exponential backoff. After SEND_MAX_ATTEMPTS the message is
  // NOT retried again automatically — a "couldn't deliver — retry" affordance is
  // shown instead (no infinite hammer; mirrors the token cooldown).
  async function trySend(roomId, body, attempt) {
    try {
      await client.sendTextMessage(roomId, body);
      clearSendNotice(roomId);
    } catch (e) {
      if (attempt + 1 < SEND_MAX_ATTEMPTS) {
        setTimeout(() => trySend(roomId, body, attempt + 1), sendBackoffMs(attempt));
      } else {
        showSendNotice(roomId, body);
      }
    }
  }

  function showSendNotice(roomId, body) {
    if (roomId !== activeRoomId) return;
    const tl = document.getElementById('cx-timeline');
    if (!tl) return;
    let n = document.getElementById('cx-send-notice');
    if (!n) { n = document.createElement('div'); n.id = 'cx-send-notice'; n.className = 'cx-send-notice'; tl.appendChild(n); }
    n.innerHTML = 'Couldn\'t deliver your message. <button class="cx-retry-send">Retry</button>';
    const b = n.querySelector('.cx-retry-send');
    if (b) b.onclick = () => { n.remove(); trySend(roomId, body, 0); };
    tl.scrollTop = tl.scrollHeight;
  }

  function clearSendNotice(roomId) {
    if (roomId !== activeRoomId) return;
    const n = document.getElementById('cx-send-notice');
    if (n) n.remove();
  }

  async function promptInvite() {
    if (!client) return;
    const input = window.prompt('Start a private chat — invite by @handle (e.g. @sam):');
    if (!input || !input.trim()) return;
    let target = input.trim();
    // A bare @handle is resolved through the registrar to the right federated Matrix
    // id (their Cove's homeserver, or the shared app) — so you just type @friend and it
    // works cross-Cove. A full @user:server id is used as-is.
    if (target.indexOf(':') === -1) {
      const h = target.replace(/^@/, '');
      try {
        const r = await fetch('/api/registry/resolve/handle/' + encodeURIComponent(h));
        if (!r.ok) { window.alert('No one found with @' + h + '. Double-check the handle.'); return; }
        const d = await r.json();
        target = d.matrix_user || '';
        if (!target) { window.alert('@' + h + ' hasn\'t set up chat yet — ask them to open Connect once.'); return; }
      } catch (e) { window.alert('Could not look up @' + h + ' right now.'); return; }
    } else if (target.charAt(0) !== '@') {
      target = '@' + target;
    }
    try {
      const room = await client.createRoom({ invite: [target], is_direct: true, preset: window.mxcs.Preset ? window.mxcs.Preset.TrustedPrivateChat : 'trusted_private_chat' });
      if (room && room.room_id) { renderTree(); openRoom(room.room_id); }
    } catch (e) {
      window.alert('Could not start that conversation: ' + (e.message || e));
    }
  }

  // Start (or open) a 1:1 thread with someone by @handle, seeding an optional first
  // message. Resolves a bare @handle through the registrar (cross-Cove). Used by the
  // appointment fulfillment loop (#190).
  async function startDirectChatByHandle(handle, prefill) {
    if (!client) return false;
    let target = (handle || '').trim();
    if (!target) return false;
    if (target.indexOf(':') === -1) {
      const h = target.replace(/^@/, '');
      try {
        const r = await fetch('/api/registry/resolve/handle/' + encodeURIComponent(h));
        if (!r.ok) { window.alert('No one found with @' + h + '.'); return false; }
        const d = await r.json();
        target = d.matrix_user || '';
        if (!target) { window.alert('@' + h + " hasn't set up chat yet."); return false; }
      } catch (e) { window.alert('Could not look up @' + h + '.'); return false; }
    } else if (target.charAt(0) !== '@') { target = '@' + target; }
    try {
      const room = await client.createRoom({ invite: [target], is_direct: true, preset: window.mxcs && window.mxcs.Preset ? window.mxcs.Preset.TrustedPrivateChat : 'trusted_private_chat' });
      if (room && room.room_id) {
        renderTree(); openRoom(room.room_id);
        if (prefill) { try { await client.sendTextMessage(room.room_id, prefill); } catch (e) { /* leave empty */ } }
        return true;
      }
    } catch (e) { window.alert('Could not start that conversation: ' + (e.message || e)); }
    return false;
  }

  // Appointment fulfillment (#190): after buying a call/session, open a thread with the
  // seller seeded with a "let's set a time" message. Calendar is handled by the operator/
  // agent from there → the scheduled event surfaces on Attention.
  window.coordinateAppointment = async function (sellerHandle, title) {
    window.openConnect();
    try { if (typeof ensureChats === 'function') await ensureChats(); } catch (e) { /* client may already be up */ }
    const t = title || 'your session';
    await startDirectChatByHandle(sellerHandle,
      `Hi! I just booked "${t}". A couple of times that work for me: ___ . What works for you?`);
  };

  // ── Public hooks (names kept from the spike; messaging.js calls these) ──
  window.openConnect = function () {
    injectStyles();
    const panel = mountPanel();
    if (!panel) return;
    // mark Connect tab active, deactivate agent tabs
    document.querySelectorAll('#agent-selector .agent-tab').forEach(t => t.classList.remove('active'));
    const cb = document.getElementById('cx-connect-tab'); if (cb) cb.classList.add('active');
    // Render the mode shell immediately so Market is reachable without waiting on
    // (or even having) Matrix. Chats lazy-inits the SDK via ensureChats().
    renderClient();
  };

  window.closeConnect = function () {
    unmountPanel();
    const cb = document.getElementById('cx-connect-tab'); if (cb) cb.classList.remove('active');
    // Client keeps syncing in the background (so unread/notifications stay live).
  };

  // Open Connect straight into the Market (optionally the "My Offerings" sub-view).
  // Used by the Action Board build→list loop (#190 S4) after a claim.
  window.openConnectMarket = function (sub) {
    window.openConnect();
    setTimeout(() => {
      try {
        renderMarket();
        if (sub === 'offerings' && typeof renderMine === 'function') renderMine();
      } catch (e) { /* leave them in the market shell */ }
    }, 0);
  };

  // ── Inject the "Connect" button into the agent-selector row ──
  window.addConnectTab = function () {
    const bar = document.getElementById('agent-selector');
    if (!bar || document.getElementById('cx-connect-tab')) return;
    const sep = document.createElement('span');
    sep.id = 'cx-sep';
    sep.style.cssText = 'margin-left:auto;width:1px;align-self:stretch;background:var(--border,#23304d);margin:6px 10px;flex:0 0 auto;';
    bar.appendChild(sep);
    const btn = document.createElement('button');
    btn.id = 'cx-connect-tab';
    btn.className = 'agent-tab cx-tab';
    btn.textContent = 'Connect';
    btn.onclick = function (e) { e.preventDefault(); e.stopPropagation(); window.openConnect(); };
    bar.appendChild(btn);
  };

  // connect.js usually loads after messaging.js built the agent row; add the button
  // once that row exists (poll briefly), same as before.
  (function tryAdd(n) {
    if (document.getElementById('agent-selector')) { window.addConnectTab(); return; }
    if (n > 0) setTimeout(function () { tryAdd(n - 1); }, 300);
  })(20);
})();
