// =============================================================================
// core.js — Config-driven bootstrap for cove-core Mission Control
// =============================================================================
// Reads /api/config on load, builds tabs + panels dynamically.
// No hardcoded agent names, tab lists, or channel names.
//
// Extracted modules (load before core.js via <script> tags):
//   lp-colors.js     — LP Color System, ESC, debug overlay, all lp* helpers
//   core-datetime.js  — Timezone utilities (getTimezone, formatDate, etc.)
// On-demand modules (loaded in boot()):
//   upgrade.js        — Tier ladder, upgrade modal, Stripe checkout
//   onboarding.js     — Interactive walkthrough for new users
// =============================================================================

// ── Affiliate referral capture (#169) ───────────────────────────────────────
// The start of the funnel: when someone arrives via a ?ref=<handle> link, stash the
// referrer's handle so it survives across pages and into signup/upgrade. It's read
// back at the Cove-signup checkout and carried to the registry as referred_by
// (validated + set-once there). Runs immediately on load — capture must beat any
// on-demand module. Stored in a cookie (90 days) so it persists across the redirect
// to Stripe and back.
(function captureRef() {
    try {
        // Store the ref AS-IS — it may be an LP-XXXXXX referral code (the live link
        // format, resolved server-side) or a bare @handle. Don't lowercase: codes are
        // case-sensitive in the link. The registry resolves code-or-handle at signup.
        const ref = new URLSearchParams(location.search).get('ref');
        if (ref && /^[A-Za-z0-9_.-]{1,40}$/.test(ref)) {
            const exp = new Date(Date.now() + 90 * 864e5).toUTCString();
            document.cookie = 'lp_ref=' + encodeURIComponent(ref) +
                '; expires=' + exp + '; path=/; SameSite=Lax';
        }
    } catch (e) { /* non-fatal */ }
})();

// Read the stored referrer handle (set by captureRef), or '' if none.
window.lpStoredRef = function () {
    const m = document.cookie.match(/(?:^|;\s*)lp_ref=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : '';
};

// ── Global state (shared across tab scripts) ────────────────────────────────
let activeTab = '';
let activeBoard = 'attention';  // 'attention' or 'action'

// Board definitions — which tabs belong to each board
// Action Board tabs are tier-aware: Tuner only gets Flows (as "Actions")
const BOARD_CONFIG = {
    attention: {
        // Priority tabs for bottom nav (first N shown, rest in More).
        // Match top bar: Attention (home) far left, then Chat, then the rest.
        priority: ['home', 'chat', 'projects', 'calendar'],
        switchTo: 'action',
        switchIcon: '⚡',
        switchLabel: 'Action',
    },
    action: {
        // Action Board tabs — these are virtual tabs with their own panels
        // Full set for Operator+; Tuner gets filtered to ab-flows only
        tabs: [
            { id: 'ab-actions', label: 'Actions' },
            { id: 'ab-links', label: 'Links' },
            { id: 'ab-flows', label: 'Flows' },
            { id: 'ab-tools', label: 'Tools' },
        ],
        priority: ['chat', 'ab-actions', 'ab-links', 'ab-flows', 'ab-tools'],
        switchTo: 'attention',
        switchIcon: '👁',
        switchLabel: 'Attention',
    }
};

// Get Action Board tabs filtered by tier
function _getActionTabs() {
    if (MC.isTuner) {
        // Tuner only: single Actions tab (frequency-based action options)
        return [{ id: 'ab-flows', label: 'Actions' }];
    }
    // Operator/Presence/Cove: full action board (Actions, Links, Flows, Tools)
    return BOARD_CONFIG.action.tabs;
}

// ── Shared utility functions (used by tab scripts) ──────────────────────────
const MOBILE_BP = 768;
function isMobile() { return window.innerWidth <= MOBILE_BP; }

function esc(s) {
    if (!s) return '';
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
}

function avatarPath(name) {
    if (!name) return '/static/avatars/unknown.png';
    return '/static/avatars/' + name.toLowerCase().replace(/\s+/g, '-') + '.png';
}

function firstName(name) {
    if (!name) return '';
    return name.split(' ')[0];
}

function statusDotHTML(status) {
    const colors = { active: 'var(--green)', online: 'var(--green)', idle: 'var(--yellow)', offline: 'var(--red)', planned: 'var(--dim)' };
    const c = colors[status] || 'var(--dim)';
    return `<span style="width:6px;height:6px;border-radius:50%;background:${c};display:inline-block;"></span>`;
}

function updateApprovalBadge(approvals) {
    // Update desktop tab badge
    const dashTab = document.querySelector('.tab[data-tab="home"]');
    if (dashTab) {
        const existing = dashTab.querySelector('.badge');
        if (existing) existing.remove();
        if (approvals && approvals.length > 0) {
            dashTab.insertAdjacentHTML('beforeend', ` <span class="badge">${approvals.length}</span>`);
        }
    }
    // Also update home-approvals-badge if it exists
    const badge = document.getElementById('home-approvals-badge');
    if (badge) {
        if (approvals && approvals.length > 0) {
            badge.textContent = approvals.length;
            badge.classList.remove('hidden');
        } else {
            badge.classList.add('hidden');
        }
    }
}

// Global config — set on boot, read by all tab scripts
let MC = {
    instance: {},
    channels: {},
    defaultChannel: 'day',
    tabs: [],
    agents: [],
    agentName: 'Agent',
    agentEmoji: '',
    operatorName: 'Operator',
    accentColor: '#4a9eff',
};

// Voice (jules) backend URL — single resolver for voice.js + settings-voice.js so no
// file guesses the host by swapping a subdomain (#205-voice). Reads MC.voice from
// /api/config. kind: 'http' (TTS base) | 'ws' (realtime STT base). '' => disabled.
MC.voiceUrl = function (kind) {
    const sub = () => {  // founder-Cove fallback: voice.{domain} by subdomain swap
        const host = location.hostname.replace(/^[^.]+\./, 'voice.');
        const proto = kind === 'ws' ? (location.protocol === 'https:' ? 'wss:' : 'ws:') : location.protocol;
        return `${proto}//${host}`;
    };
    const v = MC.voice;
    if (!v) return sub();                 // legacy backend (no voice in config)
    if (v.enabled === false) return '';   // explicitly off
    // URLs follow the host you're ON. When viewing locally (http, localhost, or a bare
    // IP), use the same-host voice port — so setting a public address never breaks voice
    // on the box. On the real domain (https) fall through to voice.{domain}.
    const h = location.hostname;
    const localish = location.protocol === 'http:' || h === 'localhost' || h === '127.0.0.1'
        || /^\d+\.\d+\.\d+\.\d+$/.test(h);
    const sameHost = () => {
        const proto = kind === 'ws' ? (location.protocol === 'https:' ? 'wss:' : 'ws:') : location.protocol;
        return `${proto}//${h}:${v.same_host_port}`;
    };
    if (v.same_host_port && localish) return sameHost();
    const u = kind === 'ws' ? v.ws : v.http;
    if (u) return u.replace(/\/+$/, '');
    if (v.same_host_port) return sameHost();
    return sub();
};

// Timezone utilities — see core-datetime.js (loaded before this file)

// =============================================================================
// Bootstrap — fetch config, build UI, load tab scripts
// =============================================================================
async function boot() {
    try {
        // Forward the on-box door override (?as=stuart) so the server scopes host_context
        // to that manager MC even on localhost, where the subdomain can't.
        const _asDoor = new URLSearchParams(location.search).get('as');
        const res = await fetch('/api/config' + (_asDoor ? ('?as=' + encodeURIComponent(_asDoor)) : ''));
        const config = await res.json();

        // Cache-busting: store build version for loadScript()
        window._buildVersion = config.build_version || '';

        MC.config = config;  // Full config — accessible for nextcloud_public_url, etc.
        // Host-based routing: which "door" this subdomain is. kind: cove|handle|manager.
        MC.hostContext = config.host_context || { kind: 'cove', label: null, match: true };
        // stuart.{cove} = steward/admin view, but ONLY for a matched operator session.
        MC.adminView = MC.hostContext.kind === 'manager' && MC.hostContext.match !== false;
        // {cove}.{domain} apex + admin session = the Cove-admin surface (presence
        // list stub). Server-authoritative. Two equivalent admin signals, either grants
        // the apex view: host_context.cove_admin (kind===cove AND cove_role===admin) OR
        // the top-level is_cove_admin flag (account id in admin_ids, core.py:234) at the
        // bare cove root — CF-14 wires the latter, which previously had no frontend
        // consumer, so an admin-by-admin_ids whose cove_role hasn't migrated still gets in.
        // Purely additive: can only ENABLE the view for a real cove admin, never disable it.
        MC.coveAdminView = MC.hostContext.cove_admin === true
            || (MC.hostContext.kind === 'cove' && MC.config && MC.config.is_cove_admin === true);
        MC.instance = config.instance || {};
        MC.channels = config.channels || {};
        MC.defaultChannel = config.default_channel || 'day';
        MC.tabs = config.tabs || [];
        MC.agents = config.agents || [];

        if (window._mcDebugLog) {
            window._mcDebugLog('[CONFIG] tabs=' + JSON.stringify((config.tabs || []).map(function(t) { return t.id || t; })) +
                ' channels=' + JSON.stringify(Object.keys(config.channels || {})) +
                ' agents=' + (config.agents || []).length);
        }

        MC.agentId = MC.agents[0]?.id || '';
        MC.agentName = MC.agents[0]?.name || MC.instance.name || 'Agent';
        MC.agentEmoji = MC.agents[0]?.emoji || '';
        MC.operatorName = MC.instance.operator || 'Operator';
        MC.accentColor = MC.instance.accent_color || '#4a9eff';
        // ── Tier system ─────────────────────────────────────────────────
        // Backend sends: config.tier (current tier info), config.tabs (filtered),
        // config.all_tabs (full set), config.tab_tiers (tier requirement per tab).
        // Tier levels: free=0, operator=10, presence=20, cove=30.
        MC.tier = config.tier || {};
        MC.voice = config.voice || null;  // null => legacy backend: keep subdomain fallback
        MC.features = config.features || {};
        MC._allTabs = config.all_tabs || config.tabs || [];
        MC._tabTiers = config.tab_tiers || {};
        MC._tabTierMax = config.tab_tier_max || {};
        const TIER_LEVELS = { free: 0, pro: 5, operator: 10, presence: 20, cove: 30 };

        // Admin tier override via URL param — filters from full tab set
        const _tierOverride = new URLSearchParams(location.search).get('tier');
        if (_tierOverride && TIER_LEVELS[_tierOverride] !== undefined) {
            MC.tier.current = _tierOverride;
            MC.tier.level = TIER_LEVELS[_tierOverride];
            MC.tier.has_agent = MC.tier.level >= 20;
            MC.tier.has_team = MC.tier.level >= 30;
            MC.tier.upgrade_available = MC.tier.level < 30;
            // Re-filter tabs from full set at the overridden tier level
            // Checks both floor (minimum tier) and ceiling (max tier)
            MC.tabs = MC._allTabs.filter(function(t) {
                var tabId = t.id || t;
                var required = MC._tabTiers[tabId];
                if (required === undefined) required = 10;
                if (MC.tier.level < required) return false;
                var maxTier = MC._tabTierMax[tabId];
                if (maxTier !== undefined && MC.tier.level > maxTier) return false;
                return true;
            });
        }

        // isTuner = free tier (tuning-first stripped UI)
        // Everything else uses tier.level for progressive feature checks
        // Default to 30 (Cove) when tier not reported — matches Python get_container_tier() default.
        // All P620 containers (Stuart, Atlas, Haven MC) are Cove; only VPS shared container sends explicit tier.
        if (MC.tier.level === undefined) MC.tier.level = 30;
        MC.isTuner = MC.tier.level < 10;
        // isOperator = no-agent tier (free or operator). Controls simplified UI.
        MC.isOperator = MC.tier.level < 20;
        MC.isCove = MC.tier.level >= 30;

        // Apply branding — header shows "Name Family" (e.g. "Stuart Cove")
        const hhSuffix = MC.instance.family_name ? ` ${MC.instance.family_name}` : '';
        const fullName = MC.agentName + hhSuffix;
        document.title = `${fullName} — Mission Control`;

        // Lucid Cove logo — always present in header, clickable as home/refresh
        const logoEl = document.getElementById('header-logo');
        logoEl.innerHTML = '<svg viewBox="0 0 500 440" xmlns="http://www.w3.org/2000/svg"><path d="M 75,370 Q 75,120 250,45 Q 425,120 425,370" fill="none" stroke="#4682b4" stroke-width="12" stroke-linecap="butt"/><path d="M 150,370 Q 150,190 250,120 Q 350,190 350,370" fill="none" stroke="#5ce1e6" stroke-width="24" stroke-linecap="butt"/><line x1="69" y1="370" x2="431" y2="370" stroke="#4682b4" stroke-width="7" stroke-linecap="butt"/><circle cx="250" cy="272" r="15" fill="#5ce1e6"/></svg>';
        logoEl.style.cursor = 'pointer';
        logoEl.title = 'Home';
        logoEl.addEventListener('click', () => window.location.reload());

        // Agent symbol — custom SVG if set, falls back to emoji
        const symbolEl = document.getElementById('header-symbol');
        const agentSymbolSvg = MC.agents[0]?.symbol_svg || MC.instance.agent_symbol_svg || '';
        if (agentSymbolSvg) {
            symbolEl.innerHTML = agentSymbolSvg;
        } else {
            symbolEl.textContent = MC.agentEmoji;
        }

        // ── Per-user identity (multi-Presence shared container) ──────
        // Fetch /api/presence/me to get the logged-in user's name + tier.
        // Falls back gracefully if not in multi mode or not authenticated.
        MC.presence = null;
        try {
            const meRes = await fetch('/api/presence/me');
            const meData = await meRes.json();
            if (meData.authenticated && meData.presence) {
                MC.presence = meData.presence;
                // Per-user tier overrides container tier — BUT URL ?tier= wins for testing
                if (!_tierOverride) {
                    const userTier = meData.presence.tier || 'free';
                    const userLevel = TIER_LEVELS[userTier] ?? 0;
                    MC.tier.current = userTier;
                    MC.tier.level = userLevel;
                    MC.tier.has_agent = userLevel >= 20;
                    MC.tier.has_team = userLevel >= 30;
                    MC.tier.upgrade_available = userLevel < 30;
                    MC.isTuner = userLevel < 10;
                    MC.isOperator = userLevel < 20;
                }
            }
        } catch(e) { console.warn('[core] presence/me fetch failed:', e.message); }

        // ── Re-filter tabs for the user's actual tier ──────────────────
        // MC.tabs was set from config (full list). Now that we know the
        // user's tier from /api/presence/me, filter using the same logic
        // as the admin override: check floor (minimum) and ceiling (max).
        // Skip if ?tier= URL override is active — it already filtered.
        if (!_tierOverride && MC._allTabs.length && MC._tabTiers) {
            MC.tabs = MC._allTabs.filter(function(t) {
                var tabId = t.id || t;
                var required = MC._tabTiers[tabId];
                if (required === undefined) required = 10;
                if ((MC.tier.level || 0) < required) return false;
                var maxTier = MC._tabTierMax[tabId];
                if (maxTier !== undefined && (MC.tier.level || 0) > maxTier) return false;
                return true;
            });
        }

        // ── Check for post-upgrade redirect (?upgraded=<tier>) ──────────
        const _upgradeParam = new URLSearchParams(location.search).get('upgraded');
        if (_upgradeParam) {
            // Clean URL
            const _cleanUrl = location.pathname;
            history.replaceState(null, '', _cleanUrl);
            // Tier-specific welcome messages
            var _upgradeMessages = {
                pro: '<strong>Welcome to Pro!</strong> You now have unlimited tunings.',
                operator: '<strong>Welcome to Operator!</strong> Cloud storage and calendar are ready.',
                presence: '<strong>Welcome to Presence!</strong> Your full AI companion is live.',
                cove: '<strong>Order received!</strong> We\'re provisioning your Cove. It\'ll be ready within 24 hours, and we\'ll email your setup link to get started.',
            };
            var _upgradeMsg = _upgradeMessages[_upgradeParam] || '<strong>Upgrade complete!</strong> Your account has been updated.';
            // Show success toast after UI renders
            setTimeout(function() {
                const toast = document.createElement('div');
                toast.className = 'upgrade-toast';
                toast.innerHTML = _upgradeMsg;
                document.body.appendChild(toast);
                setTimeout(function() { toast.classList.add('show'); }, 50);
                setTimeout(function() { toast.classList.remove('show'); setTimeout(function() { toast.remove(); }, 400); }, 5000);
            }, 1500);
        }

        // ── Check if migrated user needs to choose a username ─────────
        if (MC.presence && MC.presence.needs_username) {
            setTimeout(function() {
                const overlay = document.createElement('div');
                overlay.className = 'username-prompt-overlay';
                overlay.innerHTML = `
                    <div class="username-prompt-card">
                        <div class="username-prompt-title">Welcome to Lucid Cove</div>
                        <p class="username-prompt-text">Choose a username. This is your public handle.</p>
                        <div class="username-prompt-input-row">
                            <span style="color:var(--dim);font-size:0.9rem;">@</span>
                            <input type="text" id="choose-username-input" class="username-prompt-input"
                                   placeholder="your-name" autocapitalize="none" autocorrect="off"
                                   pattern="[a-z0-9][a-z0-9_-]{1,28}[a-z0-9]" maxlength="30">
                        </div>
                        <div class="username-prompt-hint">Lowercase, 3-30 characters. Letters, numbers, hyphens.</div>
                        <div class="username-prompt-error" id="choose-username-error"></div>
                        <button class="username-prompt-btn" id="choose-username-btn">Save Username</button>
                        <button class="username-prompt-skip" id="choose-username-skip">I'll do this later</button>
                    </div>`;
                document.body.appendChild(overlay);
                setTimeout(function() { overlay.classList.add('show'); }, 50);

                var input = document.getElementById('choose-username-input');
                var btn = document.getElementById('choose-username-btn');
                var skip = document.getElementById('choose-username-skip');
                var errEl = document.getElementById('choose-username-error');

                btn.addEventListener('click', async function() {
                    var val = (input.value || '').trim().toLowerCase();
                    errEl.textContent = '';
                    if (val.length < 3) { errEl.textContent = 'Must be at least 3 characters'; return; }
                    if (!/^[a-z0-9][a-z0-9_-]*[a-z0-9]$/.test(val)) { errEl.textContent = 'Lowercase letters, numbers, hyphens only'; return; }
                    btn.disabled = true;
                    btn.textContent = 'Saving...';
                    try {
                        var res = await fetch('/api/presence/me', {
                            method: 'PATCH',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ username: val })
                        });
                        var data = await res.json();
                        if (!res.ok) { errEl.textContent = data.detail || 'Username taken or invalid'; btn.disabled = false; btn.textContent = 'Save Username'; return; }
                        MC.presence.username = val;
                        MC.presence.needs_username = false;
                        overlay.classList.remove('show');
                        setTimeout(function() { overlay.remove(); }, 300);
                        // Update header handle display
                        var handleEl = document.querySelector('.mc-handle');
                        if (handleEl) handleEl.textContent = '@' + val;
                    } catch(e) { errEl.textContent = 'Network error'; btn.disabled = false; btn.textContent = 'Save Username'; }
                });
                skip.addEventListener('click', function() {
                    overlay.classList.remove('show');
                    setTimeout(function() { overlay.remove(); }, 300);
                });
            }, 800);
        }

        if (MC.coveAdminView) {
            // {cove}.{domain} apex opened by an admin — the Cove-admin surface.
            const coveName = MC.instance.family_name || MC.instance.name || 'Cove';
            document.getElementById('header-name').textContent = coveName;
            document.getElementById('header-status').textContent = 'Cove Admin';
            document.title = `${coveName} — Cove Admin`;
            logoEl.classList.add('tuner-logo-visible');
            // Cove Admin has no Action Board — its tabs don't apply here. Hide the switch. (jules 1243)
            const _bs = document.getElementById('board-switch');
            if (_bs) _bs.style.display = 'none';
        } else if (MC.adminView) {
            // Manager subdomain (e.g. stuart.{cove}) — the steward/admin view of the Cove.
            const lbl = MC.hostContext.label || 'steward';
            const mgrName = lbl.charAt(0).toUpperCase() + lbl.slice(1);
            document.getElementById('header-name').textContent = mgrName + hhSuffix;
            document.getElementById('header-status').textContent = 'Steward · Admin';
            document.title = `${mgrName} — Cove Admin`;
            logoEl.classList.add('tuner-logo-visible');
        } else if (MC.isOperator) {
            // No-agent tiers (Free/Pro/Operator): user identity + @handle
            const userName = MC.presence?.display_name || MC.instance.operator || 'Lucid Cove';
            const handle = MC.presence?.username ? `@${MC.presence.username}` : '';
            document.getElementById('header-name').textContent = userName;
            document.getElementById('header-status').textContent = handle;
            document.title = `${userName} — Lucid Cove`;
            logoEl.classList.add('tuner-logo-visible');
        } else if (MC.presence) {
            // Agent tier in shared container: agent name + @handle
            const presAgentName = MC.presence.agent_name || MC.agentName;
            const presFullName = presAgentName + hhSuffix;
            const presHandle = MC.presence.username ? `@${MC.presence.username}` : '';
            document.getElementById('header-name').textContent = presFullName;
            document.getElementById('header-status').textContent = presHandle;
            document.title = `${presFullName} — Mission Control`;
        } else {
            // Standalone MC: admin/domain shows archetype, personal shows @handle
            document.getElementById('header-name').textContent = fullName;
            const instType = MC.instance?.type || '';
            if (instType === 'admin' || instType === 'domain' || instType === 'manager') {
                document.getElementById('header-status').textContent = MC.agents[0]?.archetype || '';
            } else {
                const opHandle = MC.instance?.operator_handle || '';
                document.getElementById('header-status').textContent = opHandle ? `@${opHandle}` : '';
            }
        }

        // Set accent color as CSS variable
        document.documentElement.style.setProperty('--accent', MC.accentColor);

        // Cove-admin apex: replace the personal tab set with the admin nav
        // (Presences · Haven · Cove Settings). Preserve the real home/settings
        // tab objects (so their scripts still load), relabel, and insert Haven.
        if (MC.coveAdminView) {
            const _base = (MC._allTabs && MC._allTabs.length) ? MC._allTabs : MC.tabs;
            const _pick = (id) => {
                const t = _base.find(x => (x.id || x) === id);
                return t ? { ...(typeof t === 'string' ? { id: t } : t) } : { id };
            };
            const _homeTab = _pick('home'); _homeTab.label = 'Presences';
            const _setTab = _pick('settings'); _setTab.label = 'Cove Settings';
            MC.tabs = [_homeTab, { id: 'haven', label: 'Haven' }, _setTab];
        }

        // Build tabs + panels (Action Board available at all tiers)
        buildTabs();
        _buildActionBoardPanels();
        _buildDetailPanels();
        _buildBottomNav();

        // #PERF-MC1: resolve landing tab BEFORE loading scripts so cold boot only
        // fetches what the first paint needs (not every panel JS on the roster).
        const savedTab = sessionStorage.getItem('mc_active_tab');
        let firstTab = (savedTab && MC.tabs.some(t => (t.id || t) === savedTab))
            ? savedTab
            : (MC.tabs[0]?.id || MC.tabs[0] || 'home');
        // Manager/admin subdomain (stuart.{cove}) opens to the Team (Cove management) view.
        if (MC.adminView && MC.tabs.some(t => (t.id || t) === 'team')) firstTab = 'team';
        // jules 07-07: an explicit ?tab= (e.g. the "Open chat" door) wins — land where it points.
        // Action Board tabs (ab-*) are virtual — not in MC.tabs — so accept them too.
        // Used by Backlog/Jules × close with ?return=links → /?tab=ab-links.
        const _wantTab = new URLSearchParams(location.search).get('tab');
        const _abTabIds = (_getActionTabs() || []).map(t => t.id);
        if (_wantTab) {
            if (MC.tabs.some(t => (t.id || t) === _wantTab)) {
                firstTab = _wantTab;
            } else if (_abTabIds.indexOf(_wantTab) !== -1) {
                firstTab = _wantTab;
            }
        }

        // Shell + first-tab scripts only (parallel). Remaining tabs load on switch
        // or idle-prefetch after first paint — DERP/hotspot paths die on full fan-out.
        await loadBootShellScripts(firstTab);
        await ensureTabScripts(firstTab);

        if (_abTabIds.indexOf(firstTab) !== -1 && typeof switchBoard === 'function') {
            switchBoard('action');
        }
        await switchToTab(firstTab);

        // Background: warm the rest once the browser is idle (does not block UI).
        _scheduleIdleTabPrefetch(firstTab);

        // Auto-show onboarding for first-time Tuner users
        if (MC.isTuner && !(MC.features && MC.features.onboarding_seen)) {
            setTimeout(_startOnboarding, 800);
        }

        // Reflect modal (Content Mirrors deep view — global, used by home + tuning)
        const reflectModal = document.createElement('div');
        reflectModal.id = 'reflectModal';
        reflectModal.className = 'reflect-overlay';
        reflectModal.style.display = 'none';
        reflectModal.onclick = (e) => { if (e.target === reflectModal) reflectModal.style.display = 'none'; };
        reflectModal.innerHTML = `<div class="reflect-modal">
            <div class="reflect-header">
                <button class="reflect-close" onclick="document.getElementById('reflectModal').style.display='none'">&times;</button>
                <div class="reflect-title" id="reflectTitle"></div>
                <div class="reflect-canon" id="reflectCanon"></div>
            </div>
            <div class="reflect-body" id="reflectBody"></div>
        </div>`;
        document.body.appendChild(reflectModal);

        // Canon lyrics modal — triggered by tapping principle on Attention Home
        const lyricsModal = document.createElement('div');
        lyricsModal.id = 'lyricsModal';
        lyricsModal.className = 'reflect-overlay';
        lyricsModal.style.display = 'none';
        lyricsModal.onclick = (e) => { if (e.target === lyricsModal) lyricsModal.style.display = 'none'; };
        lyricsModal.innerHTML = `<div class="reflect-modal lyrics-modal">
            <div class="reflect-header">
                <button class="reflect-close" onclick="document.getElementById('lyricsModal').style.display='none'">&times;</button>
                <div class="reflect-title" id="lyricsTitle"></div>
                <div class="reflect-canon" id="lyricsStage"></div>
            </div>
            <div class="reflect-body" id="lyricsBody"></div>
        </div>`;
        document.body.appendChild(lyricsModal);

        // Start status polling
        pollStatus();
        setInterval(pollStatus, 120_000);

    } catch (e) {
        document.getElementById('header-name').textContent = 'Connection Error';
        document.getElementById('header-status').textContent = e.message;
        document.getElementById('conn-dot').className = 'status-dot mobile-hide red';
        console.error('Boot failed:', e);
    }
}

// =============================================================================
// Build tab bar and panel containers from config
// =============================================================================
function buildTabs() {
    const bar = document.getElementById('tab-bar');
    const container = document.getElementById('panel-container');
    bar.innerHTML = '';
    container.innerHTML = '';

    // At Operator+, tune/playlists/go-deeper are accessed via Latest Tuning
    // card links — not nav tabs. Panels still get created so switchToTab works.
    const tierLevel = MC.tier?.level ?? 30;
    const _navHidden = (tierLevel >= 10) ? new Set(['tune', 'playlists', 'go-deeper']) : new Set();

    MC.tabs.forEach((tab, idx) => {
        const id = tab.id || tab;
        // home tab displays as "Attention" — the Attention Board's main view
        const label = (id === 'home' && !MC.coveAdminView) ? 'Attention' : (tab.label || id.charAt(0).toUpperCase() + id.slice(1));
        const isFirst = idx === 0;

        // Tab button — skip nav button for tabs hidden at this tier
        if (!_navHidden.has(id)) {
            const btn = document.createElement('button');
            btn.className = `tab${isFirst ? ' active' : ''}`;
            btn.dataset.tab = id;
            btn.textContent = label;
            btn.addEventListener('click', () => switchToTab(id));
            bar.appendChild(btn);
        }

        // Panel container — always created so switchToTab works from links
        const panel = document.createElement('section');
        panel.id = `panel-${id}`;
        panel.className = `panel${isFirst ? ' active' : ''}`;

        // Default inner HTML for known panel types
        panel.innerHTML = _defaultPanelHTML(id);
        container.appendChild(panel);
    });
}

// =============================================================================
// Action Board panels — built alongside Attention tabs, hidden by default
// =============================================================================
function _buildActionBoardPanels() {
    const container = document.getElementById('panel-container');
    const abTabs = _getActionTabs();

    abTabs.forEach(tab => {
        const panel = document.createElement('section');
        panel.id = `panel-${tab.id}`;
        panel.className = 'panel ab-panel';
        panel.innerHTML = _defaultPanelHTML(tab.id);
        container.appendChild(panel);
    });
}

// =============================================================================
// Help modal (#HELP1 — TOC + pages, agent-aware capability guide)
// =============================================================================
// Help is the "how to use this system" doorway. Daily tuning + mirrors stay on
// Attention; Go Deeper holds framework depth. Help links across without
// swallowing either surface. Getting Started tour is left as-is (tuner-skewed).

function _helpIsStewardSurface() {
    // Steward/admin Help is the manager door (stuart.{cove}) or Cove-admin apex.
    // Presence MC (handle door / personal agent) is never steward Help — even when
    // the logged-in operator is also a cove admin (their presence agent is Knight, etc.).
    try {
        if (typeof MC === 'undefined') return false;
        if (MC.adminView === true) return true;
        if (MC.coveAdminView === true) return true;
        const kind = (MC.hostContext && MC.hostContext.kind) || '';
        if (kind === 'manager') return true;
        const instType = (MC.instance && MC.instance.type) || '';
        if ((instType === 'admin' || instType === 'manager') && !MC.presence) return true;
    } catch (e) {}
    return false;
}

function _helpAgentName() {
    // Same rule as chat labels (messaging.js): presence.agent_name is ONLY for the
    // personal-agent surface. On steward/admin doors, prefer the container steward
    // (MC.agentName / agents[0]) so Help says "Me & Stuart", not the operator's
    // personal agent (e.g. Knight) just because they are logged in.
    try {
        if (typeof MC !== 'undefined') {
            if (_helpIsStewardSurface()) {
                if (MC.agentName) return String(MC.agentName);
                if (MC.agents && MC.agents[0] && MC.agents[0].name) return String(MC.agents[0].name);
            }
            if (MC.presence && MC.presence.agent_name) return String(MC.presence.agent_name);
            if (MC.agentName) return String(MC.agentName);
        }
    } catch (e) {}
    return 'your agent';
}

function _helpEsc(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function _helpBackRow() {
    return `<button type="button" class="help-back" onclick="helpShowPage('hub')">&larr; All topics</button>`;
}

function _helpPageShell(title, inner) {
    return `${_helpBackRow()}
      <div class="help-section" style="margin-top:0;padding-top:0;border-top:none;">
        <div class="help-section-title">${title}</div>
        ${inner}
      </div>`;
}

function _helpHubHtml() {
    const agent = _helpEsc(_helpAgentName());
    const steward = _helpIsStewardSurface();
    const lead = steward
        ? `How to run this Cove with <strong style="color:var(--text);">${agent}</strong> — family logistics, the build team, and the boards that keep the house moving. A door into the deeper practice stays open when you want it.`
        : `How to use this Cove — with <strong style="color:var(--text);">${agent}</strong> day to day, and a door into the deeper practice when you want it.`;
    const togetherSub = steward
        ? `Your lane with ${agent} — coordination, team, boards, and house ops`
        : `Lists, calendar, projects, files — things you can try in Chat`;
    return `
      <div class="help-section" style="margin-top:0;padding-top:0;border-top:none;">
        <p class="help-page-lead">${lead}</p>
        <ul class="help-toc">
          <li>
            <button type="button" class="help-toc-btn" onclick="closeHelp(); (async function(){ try { await loadScriptBasenames(['onboarding','upgrade']); } catch(e){} if (typeof _startOnboarding==='function') _startOnboarding(); })();">
              Getting Started
              <span class="help-toc-sub">Short tour of the basics</span>
            </button>
          </li>
          <li>
            <button type="button" class="help-toc-item" onclick="helpShowPage('together')">
              Me &amp; ${agent} — what we can do together
              <span class="help-toc-sub">${togetherSub}</span>
            </button>
          </li>
          <li>
            <button type="button" class="help-toc-item" onclick="helpShowPage('signin')">
              Signing in &amp; your devices
              <span class="help-toc-sub">Sign-in links, new devices, staying signed in</span>
            </button>
          </li>
          <li>
            <button type="button" class="help-toc-item" onclick="helpShowPage('invite')">
              Adding someone to your Cove
              <span class="help-toc-sub">Invites and first landing</span>
            </button>
          </li>
          <li>
            <button type="button" class="help-toc-item" onclick="helpShowPage('everyday')">
              Everyday use
              <span class="help-toc-sub">Chat, Attention, tuning on the home board</span>
            </button>
          </li>
          <li>
            <button type="button" class="help-toc-item" onclick="helpShowPage('deeper')">
              Go Deeper &amp; the practice
              <span class="help-toc-sub">Where Help meets Tuning, mirrors, and the framework</span>
            </button>
          </li>
          <li>
            <button type="button" class="help-toc-item" onclick="helpShowPage('trouble')">
              If something will not load
              <span class="help-toc-sub">Sign-in loops and brief outages</span>
            </button>
          </li>
          <li>
            <button type="button" class="help-toc-item" onclick="helpShowPage('glossary')">
              Glossary
              <span class="help-toc-sub">Operator, Presence, Cove, Haven, Tuning…</span>
            </button>
          </li>
          <li>
            <button type="button" class="help-toc-item" onclick="helpShowPage('marketplace')">
              Marketplace
              <span class="help-toc-sub">Details coming soon</span>
            </button>
          </li>
          <li>
            <button type="button" class="help-toc-item" onclick="helpShowPage('contact')">
              Send a message
              <span class="help-toc-sub">Questions, feedback, ideas</span>
            </button>
          </li>
        </ul>
      </div>`;
}

function _helpTogetherPresenceHtml(agent) {
    // Personal agent basics — Presence MC only. Keep this list short; deepen later.
    return _helpPageShell(
        `Me &amp; ${agent} — what we can do together`,
        `<p class="help-page-lead">You do not have to invent the product. Open <strong style="color:var(--text);">Chat</strong> (Day for quick things, Deep for longer work) and try any of these. ${agent} acts in <em>your</em> space — your lists, calendar, projects, and files.</p>
        <ul class="help-cap-list">
          <li><strong>Quick lists</strong> — groceries, errands, ideas. Create a list, add lines, check them off, rename, pin, or archive the list when you are done.
            <span class="help-cap-try">“Make a groceries list and add milk and eggs.”</span></li>
          <li><strong>Projects &amp; tasks</strong> — name a plan, add tasks, set priority or due dates, update status, archive the project when it is finished.
            <span class="help-cap-try">“Start a project called Book Promotion and add a task to outline the launch plan.”</span></li>
          <li><strong>Calendar</strong> — schedule, reschedule, or cancel events on your calendar.
            <span class="help-cap-try">“Schedule dentist Thursday at 2pm for an hour.”</span></li>
          <li><strong>Links board</strong> — pin a useful URL, update the card, or remove it.
            <span class="help-cap-try">“Pin https://example.com on my Links board as Launch notes.”</span></li>
          <li><strong>Files</strong> — drop material in Inbox; ask ${agent} to read, organize, or draft from what is there.
            <span class="help-cap-try">“What’s in my Inbox?” / “Read that spec and summarize it.”</span></li>
          <li><strong>Memory</strong> — remember preferences and decisions across sessions.
            <span class="help-cap-try">“Remember I prefer morning meetings.”</span></li>
          <li><strong>Research</strong> — look things up and turn sources into a short brief when you need a decision.
            <span class="help-cap-try">“Compare these two options and give me a bottom line.”</span></li>
          <li><strong>Sites</strong> — if your presence works on websites, ${agent} can help with working files under Sites (deploy and public publish still follow your Cove’s approval rules).
            <span class="help-cap-try">“Show me the files for my site.”</span></li>
        </ul>
        <p class="help-muted" style="margin-top:12px;">Family-wide infrastructure, the build team, and Cove-level boards stay with the steward. If something needs the whole Cove, ask ${agent} to escalate — that is working as designed.</p>
        <p class="help-muted">Want the <em>why</em> behind the system? See <button type="button" class="help-linkish" onclick="helpShowPage('deeper')">Go Deeper &amp; the practice</button>.</p>`
    );
}

function _helpTogetherStewardHtml(agent) {
    // Admin / steward door — lane is Cove ops + build team, not personal lists.
    return _helpPageShell(
        `Me &amp; ${agent} — what we can do together`,
        `<p class="help-page-lead">${agent} is the family steward. Open <strong style="color:var(--text);">Chat</strong> (Day for quick ops, Deep when the work needs room) for Cove-level work — schedules, the board, the build team, and the infrastructure everything else rides on. Personal lists and calendars live with each Presence’s own agent.</p>
        <ul class="help-cap-list">
          <li><strong>Family coordination</strong> — logistics, schedules, and the operational details that need someone paying attention.
            <span class="help-cap-try">“What’s on the calendar this week?” / “Park that and take this breakage first.”</span></li>
          <li><strong>Board &amp; queue</strong> — pull tickets, move lanes, mark done only after merge and deploy, keep NOW honest.
            <span class="help-cap-try">“What’s in NOW?” / “Pull #MESH3 and put it on the queue.”</span></li>
          <li><strong>Build team</strong> — Archimedes, Arthur, Gabe, Ezra, Julian, Iris, Vera, Soren. Delegate a scoped brief; you still approve pushes and PRs.
            <span class="help-cap-try">“Delegate CF-5 to Archimedes with a tight brief.”</span></li>
          <li><strong>Approvals &amp; ship path</strong> — git, PRs, and gated actions show on Attention; nothing public goes out without your yes.
            <span class="help-cap-try">“Open a PR for this branch.” / “Status of the last deploy?”</span></li>
          <li><strong>Presences &amp; access</strong> — invites, sign-in links, mesh join guidance, who is already in the Cove.
            <span class="help-cap-try">“Walk me through inviting someone and landing them in Chat.”</span></li>
          <li><strong>House files &amp; specs</strong> — Inbox, Working, Specs, and Sources on the steward side of the vault.
            <span class="help-cap-try">“What’s in Working/Specs?” / “Summarize the mesh-performance spec.”</span></li>
          <li><strong>System health</strong> — services, endpoints, and “is the box okay?” checks when something feels off.
            <span class="help-cap-try">“Is Mission Control healthy?” / “Any containers unhappy?”</span></li>
          <li><strong>Peer lanes</strong> — Mercer owns commerce; your personal agent owns your private life. ${agent} coordinates the Cove and does not swallow either.
            <span class="help-cap-try">“That’s Mercer’s domain — flag it for him.” / “Send that to my personal agent.”</span></li>
        </ul>
        <p class="help-muted" style="margin-top:12px;">Each family member’s Presence has its own agent and its own Help list (lists, calendar, personal projects). This page is the steward lane only.</p>
        <p class="help-muted">Want the <em>why</em> behind the system? See <button type="button" class="help-linkish" onclick="helpShowPage('deeper')">Go Deeper &amp; the practice</button>.</p>`
    );
}

function _helpTogetherHtml() {
    const agent = _helpEsc(_helpAgentName());
    if (_helpIsStewardSurface()) return _helpTogetherStewardHtml(agent);
    return _helpTogetherPresenceHtml(agent);
}

function _helpSigninHtml() {
    return _helpPageShell(
        'Signing in &amp; your devices',
        `<p style="font-size:0.82rem;color:var(--dim);line-height:1.6;margin-bottom:8px;">Your <strong style="color:var(--text);">sign-in link</strong> is how you get into the Cove on any device. Find it in <strong style="color:var(--text);">Settings &rarr; Devices &amp; Access &rarr; Sign-in link</strong>. Open it on a phone, laptop, or another browser and it signs that device in as you &mdash; it stays signed in for about 90 days, so bookmark it.</p>
        <p style="font-size:0.82rem;color:var(--dim);line-height:1.6;margin-bottom:8px;">The plain Cove address (or an &ldquo;Open MC&rdquo; button) only works on a device that is <em>already</em> signed in. To add a <strong style="color:var(--text);">new</strong> device, open a sign-in link on it first &mdash; that is the step that logs you in.</p>
        <p style="font-size:0.82rem;color:var(--dim);line-height:1.6;">Each person has their own sign-in link. When your Cove has a public address you do <strong style="color:var(--text);">not</strong> need Tailscale or the mesh for this &mdash; just open the link.</p>`
    );
}

function _helpInviteHtml() {
    return _helpPageShell(
        'Adding someone to your Cove',
        `<p style="font-size:0.82rem;color:var(--dim);line-height:1.6;margin-bottom:8px;">As an admin, go to <strong style="color:var(--text);">Settings &rarr; Admin</strong> and use <strong style="color:var(--text);">Invite by link</strong>. Send them the invite.</p>
        <p style="font-size:0.82rem;color:var(--dim);line-height:1.6;margin-bottom:8px;">They open it on the device they will use, name their agent and set up their Presence, and land in chat. The invite is one-time &mdash; it is used up once they finish, so it is normal that reopening the same invite later says it cannot be opened.</p>
        <p style="font-size:0.82rem;color:var(--dim);line-height:1.6;">To get them onto another device later, they open <strong style="color:var(--text);">Settings &rarr; Devices &amp; Access &rarr; Sign-in link</strong> on a device they are already in, then open that link on the new one.</p>`
    );
}

function _helpEverydayHtml() {
    const agent = _helpEsc(_helpAgentName());
    return _helpPageShell(
        'Everyday use',
        `<p style="font-size:0.82rem;color:var(--dim);line-height:1.6;margin-bottom:8px;"><strong style="color:var(--text);">Chat</strong> with ${agent} in the <strong style="color:var(--text);">Day</strong> channel for quick, everyday things, or the <strong style="color:var(--text);">Deep</strong> channel for focused work.</p>
        <p style="font-size:0.82rem;color:var(--dim);line-height:1.6;margin-bottom:8px;"><strong style="color:var(--text);">Attention</strong> (home) keeps what matters in front of you: quick lists, latest tuning, projects, and tasks. Approvals show here when something needs a yes from you.</p>
        <p style="font-size:0.82rem;color:var(--dim);line-height:1.6;margin-bottom:8px;">The <strong style="color:var(--text);">Action Board</strong> holds guided workflows, things to approve, and tools. Use the board toggle when you need that surface.</p>
        <p style="font-size:0.82rem;color:var(--dim);line-height:1.6;">For a full menu of what you can ask ${agent} to do, open <button type="button" class="help-linkish" onclick="helpShowPage('together')">Me &amp; ${agent}</button>.</p>`
    );
}

function _helpDeeperHtml() {
    const agent = _helpEsc(_helpAgentName());
    // Operator+ hides the go-deeper tab; Latest Tuning on Attention is the daily door.
    const tierLevel = (typeof MC !== 'undefined' && MC.tier && typeof MC.tier.level === 'number') ? MC.tier.level : 99;
    const hasGoDeeperTab = tierLevel < 10;
    const deeperCta = hasGoDeeperTab
        ? `<button type="button" class="help-toc-btn" onclick="closeHelp(); if (typeof switchToTab==='function') switchToTab('go-deeper');">Open Go Deeper</button>`
        : `<button type="button" class="help-toc-btn" onclick="closeHelp(); if (typeof openOperatorTuning==='function') openOperatorTuning();">Open today’s tuning</button>`;
    return _helpPageShell(
        'Go Deeper &amp; the practice',
        `<p class="help-page-lead">This system’s foundation is the practice — not only the tools. Help shows you how to operate with ${agent}. The framework is where you learn why the day is shaped the way it is.</p>
        <p style="font-size:0.82rem;color:var(--dim);line-height:1.6;margin-bottom:8px;"><strong style="color:var(--text);">On Attention</strong> — Latest Tuning and your mirrors stay front and center so the day’s frequency is never buried. That is enough to keep the practice visible.</p>
        <p style="font-size:0.82rem;color:var(--dim);line-height:1.6;margin-bottom:8px;"><strong style="color:var(--text);">Go Deeper</strong> — the doorway into the Lucid Principles, the Canon, the music, and how Lucid Cove sits on that foundation. Use it when you want the fuller picture; come back to Help when you want “how do I do X with ${agent}?”</p>
        <p style="font-size:0.82rem;color:var(--dim);line-height:1.6;margin-bottom:12px;">They stay intertwined on purpose: operate here, understand there, tune every day on the home board.</p>
        ${deeperCta}
        <p class="help-muted" style="margin-top:12px;">Outside links: <a href="https://lucidprinciples.com/canon" target="_blank" rel="noopener" style="color:var(--accent,#5ce1e6);">The Canon</a> · <a href="https://lucidprinciples.com" target="_blank" rel="noopener" style="color:var(--accent,#5ce1e6);">Lucid Principles</a> · <a href="https://lucidcove.org" target="_blank" rel="noopener" style="color:var(--accent,#5ce1e6);">About Lucid Cove</a></p>`
    );
}

function _helpTroubleHtml() {
    return _helpPageShell(
        'If something will not load',
        `<p style="font-size:0.82rem;color:var(--dim);line-height:1.6;margin-bottom:8px;"><strong style="color:var(--text);">A page will not open, or you are asked to sign in:</strong> open your sign-in link again (Settings &rarr; Devices &amp; Access), or ask your admin for a fresh one. The plain address needs an active sign-in.</p>
        <p style="font-size:0.82rem;color:var(--dim);line-height:1.6;margin-bottom:8px;"><strong style="color:var(--text);">Nothing loads at all:</strong> the Cove may be briefly offline &mdash; usually a home-internet blip. It comes back on its own within a minute or two, so wait a moment and try again.</p>
        <p style="font-size:0.82rem;color:var(--dim);line-height:1.6;">Still stuck? <button type="button" class="help-linkish" onclick="helpShowPage('contact')">Send a message</button>.</p>`
    );
}

function _helpGlossaryHtml() {
    return _helpPageShell(
        'Glossary',
        `<dl class="help-glossary">
          <dt>Operator</dt>
          <dd>You. The person running this Cove. Every action, tuning, and decision flows through the Operator.</dd>
          <dt>Agent</dt>
          <dd>A specialized intelligence on your team. Each agent has a role, tools, and a daily tuning. Stuart is the steward agent that coordinates your Cove.</dd>
          <dt>Presence</dt>
          <dd>A personal intelligence. One agent, personal tools, portable between Coves. Your own Presence is your individual interface to the system.</dd>
          <dt>Cove</dt>
          <dd>Your full team and infrastructure. A steward agent plus up to 9 specialists, shared tools, and up to 8 Presences. This is your operational unit.</dd>
          <dt>Haven</dt>
          <dd>A collective of Coves. Families, partners, or collaborators who choose to share a network.</dd>
          <dt>Creation Flow</dt>
          <dd>A guided workflow for building something specific. New agent setup, new Cove provisioning, product creation. Flows walk you through it step by step.</dd>
          <dt>Action Board</dt>
          <dd>Your operational dashboard. Pending tasks, scheduled actions, and quick access to everything that needs your attention.</dd>
          <dt>Tuning</dt>
          <dd>Daily alignment practice. Each agent receives a tuning frequency (one of 22 Lucid Principles) with a coaching prompt and practice. The Operator gets one too.</dd>
          <dt>Content Mirror</dt>
          <dd>A curated mapping from your daily tuning frequency to passages in another tradition. Scripture, philosophy, poetry. Complements the framework through your own lens.</dd>
          <dt>Go Deeper</dt>
          <dd>The in-app doorway into the Lucid Principles framework and philosophy. Complements Help (how to operate) and Attention (today’s tuning).</dd>
        </dl>`
    );
}

function _helpMarketplaceHtml() {
    return _helpPageShell(
        'Marketplace',
        `<p class="help-page-lead">Details coming soon.</p>
        <p class="help-muted">Skills, extensions, and shared capabilities will land here. For now, focus on Chat with your agent and the topics under Help — that is the short-term path.</p>`
    );
}

function _helpContactHtml() {
    return _helpPageShell(
        'Send a Message',
        `<p class="help-contact-desc">Questions, feedback, or ideas? Send a message directly to the team.</p>
        <form class="help-contact-form" onsubmit="submitContactForm(event)">
          <textarea class="help-contact-input" id="help-contact-message" placeholder="What's on your mind?" rows="3" maxlength="5000"></textarea>
          <div class="help-contact-actions">
            <button type="submit" class="help-contact-btn" id="help-contact-btn">Send</button>
            <span class="help-contact-status" id="help-contact-status"></span>
          </div>
        </form>`
    );
}

function helpShowPage(page) {
    const body = document.getElementById('help-modal-body');
    const titleEl = document.querySelector('.help-modal-title');
    if (!body) return;
    const pages = {
        hub: () => { if (titleEl) titleEl.textContent = 'Help'; return _helpHubHtml(); },
        together: () => { if (titleEl) titleEl.textContent = 'Me & ' + _helpAgentName(); return _helpTogetherHtml(); },
        signin: () => { if (titleEl) titleEl.textContent = 'Help'; return _helpSigninHtml(); },
        invite: () => { if (titleEl) titleEl.textContent = 'Help'; return _helpInviteHtml(); },
        everyday: () => { if (titleEl) titleEl.textContent = 'Help'; return _helpEverydayHtml(); },
        deeper: () => { if (titleEl) titleEl.textContent = 'Help'; return _helpDeeperHtml(); },
        trouble: () => { if (titleEl) titleEl.textContent = 'Help'; return _helpTroubleHtml(); },
        glossary: () => { if (titleEl) titleEl.textContent = 'Help'; return _helpGlossaryHtml(); },
        marketplace: () => { if (titleEl) titleEl.textContent = 'Help'; return _helpMarketplaceHtml(); },
        contact: () => { if (titleEl) titleEl.textContent = 'Help'; return _helpContactHtml(); },
    };
    const render = pages[page] || pages.hub;
    body.innerHTML = render();
    try { body.scrollTop = 0; } catch (e) {}
}

function openHelp(page) {
    helpShowPage(page || 'hub');
    const overlay = document.getElementById('help-overlay');
    if (overlay) overlay.classList.add('open');
}
function closeHelp() {
    const overlay = document.getElementById('help-overlay');
    if (overlay) overlay.classList.remove('open');
}

async function submitContactForm(e) {
    e.preventDefault();
    const btn = document.getElementById('help-contact-btn');
    const status = document.getElementById('help-contact-status');
    const textarea = document.getElementById('help-contact-message');
    if (!btn || !textarea) return;
    const message = textarea.value.trim();

    if (!message) return;

    btn.disabled = true;
    btn.textContent = 'Sending...';
    if (status) status.textContent = '';

    try {
        const res = await fetch('/api/contact', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: message }),
        });
        const data = await res.json();

        if (res.ok && data.ok) {
            if (status) {
                status.textContent = 'Sent!';
                status.style.color = 'var(--daily-freq, #5ce1e6)';
            }
            textarea.value = '';
        } else if (status) {
            status.textContent = data.detail || data.error || 'Failed to send.';
            status.style.color = '#e74c3c';
        }
    } catch (err) {
        if (status) {
            status.textContent = 'Connection error. Try again.';
            status.style.color = '#e74c3c';
        }
    }

    btn.disabled = false;
    btn.textContent = 'Send';
}

// =============================================================================
// Board switching — Attention ↔ Action
// =============================================================================
async function switchBoard(board) {
    if (MC.coveAdminView) return;  // no Action Board in the Cove-admin surface (jules 1243)
    const target = board || (activeBoard === 'attention' ? 'action' : 'attention');

    // Don't close flow overlays — board switch only swaps tab bars now.
    // The overlay stays until user clicks Back or a specific tab.

    // #PERF-MC1: action-board.js is no longer on cold shell — pull it when Action opens.
    if (target === 'action') {
        try {
            await loadScriptBasenames(['action-board']);
        } catch (e) {
            console.warn('[core] action-board load failed', e);
        }
    }

    activeBoard = target;

    const config = BOARD_CONFIG[target];

    // Update the switch button to point to the OTHER board
    const switchBtn = document.getElementById('board-switch-icon');
    const switchLbl = document.getElementById('board-switch-label');
    if (switchBtn) switchBtn.textContent = config.switchIcon;
    if (switchLbl) switchLbl.textContent = config.switchLabel;

    // Update header subtitle with board indicator
    const status = document.getElementById('header-status');
    if (status) {
        if (MC.isOperator) {
            const handle = MC.presence?.username ? `@${MC.presence.username}` : '';
            status.textContent = target === 'action' ? 'Actions' : handle;
        } else if (MC.presence) {
            // Agent tier with presence login: restore @handle
            const handle = MC.presence.username ? `@${MC.presence.username}` : '';
            status.textContent = target === 'action' ? 'Action Board' : handle;
        } else {
            // Standalone MC: admin/domain/manager restores archetype, personal restores @handle
            const instType = MC.instance?.type || '';
            if (instType === 'admin' || instType === 'domain' || instType === 'manager') {
                const archetype = MC.agents[0]?.archetype || '';
                status.textContent = target === 'action' ? 'Action Board' : archetype;
            } else {
                const opHandle = MC.instance?.operator_handle || '';
                status.textContent = target === 'action' ? 'Action Board' : (opHandle ? `@${opHandle}` : '');
            }
        }
    }

    // Show/hide tab bar: Tuner keeps tabs visible (single board), all others swap
    const tabBar = document.getElementById('tab-bar');
    if (MC.isTuner) {
        // Tuner: tabs always visible on both boards
        if (tabBar) tabBar.style.display = '';
    } else {
        if (tabBar) tabBar.style.display = target === 'attention' ? '' : 'none';
    }

    // Show/hide Action Board tab bar
    const actionTabs = _getActionTabs();
    let abTabBar = document.getElementById('ab-tab-bar');
    if (!abTabBar && target === 'action') {
        // Build Action Board tab bar on first switch
        // Tuner: single tab, no sub-tab bar needed — go straight to content
        if (actionTabs.length > 1) {
            abTabBar = document.createElement('nav');
            abTabBar.className = 'tab-bar ab-tab-bar';
            abTabBar.id = 'ab-tab-bar';
            actionTabs.forEach((tab, idx) => {
                const btn = document.createElement('button');
                btn.className = `tab${idx === 0 ? ' active' : ''}`;
                btn.dataset.tab = tab.id;
                btn.textContent = tab.label;
                btn.addEventListener('click', () => switchToTab(tab.id));
                abTabBar.appendChild(btn);
            });
            // Add Chat tab to Action Board bar (only if tier has chat)
            if (MC.tabs.some(t => (t.id || t) === 'chat')) {
                const chatBtn = document.createElement('button');
                chatBtn.className = 'tab';
                chatBtn.dataset.tab = 'chat';
                chatBtn.textContent = 'Chat';
                chatBtn.addEventListener('click', () => switchToTab('chat'));
                abTabBar.insertBefore(chatBtn, abTabBar.firstChild);
            }

            tabBar.parentNode.insertBefore(abTabBar, tabBar.nextSibling);
        }
    }
    if (abTabBar) abTabBar.style.display = target === 'action' ? 'flex' : 'none';

    // Tuner only: board switch navigates to content (Action = dedicated page)
    if (MC.isTuner) {
        if (target === 'action') {
            switchToTab(actionTabs[0].id);
        } else {
            const homeTab = MC.tabs.find(t => (t.id || t) === 'home') ? 'home' : (MC.tabs[0]?.id || MC.tabs[0]);
            switchToTab(homeTab);
        }
    }
    // All other tiers: just toggle tab bars, don't change the current panel

    // Rebuild bottom nav for the active board
    _buildBottomNav();
}

// =============================================================================
// Mobile bottom navigation bar
// =============================================================================
function _buildBottomNav() {
    // Remove any existing bottom nav
    const existing = document.querySelector('.bottom-nav');
    if (existing) existing.remove();

    // SVG icon definitions (compact, 20x20 viewBox)
    const icons = {
        home: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg>',
        chat: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>',
        reports: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>',
        tuning: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>',
        projects: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="3" width="20" height="14" rx="2" ry="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>',
        team: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>',
        calendar: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>',
        files: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>',
        memory: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>',
        system: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="2" width="20" height="8" rx="2" ry="2"/><rect x="2" y="14" width="20" height="8" rx="2" ry="2"/><line x1="6" y1="6" x2="6.01" y2="6"/><line x1="6" y1="18" x2="6.01" y2="18"/></svg>',
        settings: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9v1a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>',
        tune: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M12 2v4m0 12v4m-7.07-3.93l2.83-2.83m8.48-8.48l2.83-2.83M2 12h4m12 0h4m-3.93 7.07l-2.83-2.83M6.76 6.76L3.93 3.93"/></svg>',
        tuning: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M12 2v4m0 12v4m-7.07-3.93l2.83-2.83m8.48-8.48l2.83-2.83M2 12h4m12 0h4m-3.93 7.07l-2.83-2.83M6.76 6.76L3.93 3.93"/></svg>',
        playlists: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>',
        'go-deeper': '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>',
        affiliates: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="8.5" cy="7" r="4"/><line x1="20" y1="8" x2="20" y2="14"/><line x1="23" y1="11" x2="17" y2="11"/></svg>',
        more: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/></svg>',
        'ab-actions': '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>',
        'ab-links': '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>',
        'ab-flows': '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 11 12 14 22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>',
        'ab-tools': '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>',
    };

    // Pick which tabs go in the bottom bar (first N + More for overflow).
    // Board-aware: Action board shows its own tabs, Attention shows tier-specific tabs.
    // #1629 tried "show every tab + horizontal scroll" — that squashed the bar on
    // real phones and removed More. Restore capped primary slots + More menu.
    const boardConfig = BOARD_CONFIG[activeBoard] || BOARD_CONFIG.attention;
    const actionTabs = _getActionTabs();

    const bottomItems = [];
    const tierLevel = MC.tier?.level || 0;
    let priority;
    let allTabIds;

    if (activeBoard === 'action' && !MC.isOperator) {
        // Action board (Presence/Cove): show action tabs in bottom nav
        allTabIds = actionTabs.map(t => t.id);
        // Add chat to action board nav (only if tier has chat access)
        const hasChat = MC.tabs.some(t => (t.id || t) === 'chat');
        if (hasChat) allTabIds.unshift('chat');
        priority = hasChat
            ? (boardConfig.priority || ['chat', ...actionTabs.map(t => t.id)])
            : actionTabs.map(t => t.id);
    } else {
        // Attention board: show tier-specific attention tabs
        allTabIds = MC.tabs.map(t => t.id || t);
        if (MC.isTuner) {
            // Tuner (free): 4 bottom tabs + More (Affiliates, Settings)
            priority = ['home', 'tune', 'playlists', 'go-deeper', 'affiliates', 'settings'];
        } else if (tierLevel < 20) {
            // Operator: 4 bottom tabs + More (Affiliates, Settings)
            priority = ['home', 'projects', 'calendar', 'reports', 'affiliates', 'settings'];
        } else if (tierLevel < 30) {
            // Presence: Attention | Chat | Projects | Calendar (match top bar)
            priority = ['home', 'chat', 'projects', 'calendar'];
        } else {
            // Cove+: Attention | Chat | Projects | Calendar (match top bar)
            priority = boardConfig.priority || ['home', 'chat', 'projects', 'calendar'];
        }
    }

    // Cap primary bottom slots; overflow goes in More (Action board keeps room for its tabs).
    // Operator+ still hides tune/playlists/go-deeper (Latest Tuning card path).
    const maxBottom = (activeBoard === 'action') ? 10 : (MC.isTuner || tierLevel < 20) ? 4 : 5;
    const hiddenFromMore = (tierLevel >= 10) ? new Set(['tune', 'playlists', 'go-deeper']) : new Set();
    const shown = new Set();
    for (const id of priority) {
        if (allTabIds.includes(id) && bottomItems.length < maxBottom && !shown.has(id) && !hiddenFromMore.has(id)) {
            bottomItems.push(id);
            shown.add(id);
        }
    }

    // Remaining tabs go in "More"
    const moreItems = allTabIds.filter(id => !shown.has(id) && !hiddenFromMore.has(id));

    const nav = document.createElement('nav');
    nav.className = 'bottom-nav';

    bottomItems.forEach((id, i) => {
        // Look up label from MC.tabs first, then from AB virtual tabs
        const tab = MC.tabs.find(t => (t.id || t) === id);
        const abTab = BOARD_CONFIG.action.tabs.find(t => t.id === id);
        // home tab displays as "Attention" — the Attention Board's main view
        const label = id === 'home' ? 'Attention' : (tab?.label || abTab?.label || id.charAt(0).toUpperCase() + id.slice(1));
        const icon = icons[id] || icons.more;
        // No active tab when Operator is on action board (showing attention tabs, none selected)
        const isActive = (activeBoard === 'action' && MC.isOperator) ? false : (i === 0);
        nav.innerHTML += `<button class="nav-item${isActive ? ' active' : ''}" data-tab="${id}" onclick="switchToTab('${id}')">
            ${icon}<span>${ESC(label)}</span>
        </button>`;
    });

    // More button (if there are remaining tabs)
    if (moreItems.length > 0) {
        nav.innerHTML += `<button class="nav-item" data-tab="more" onclick="_toggleMoreMenu()">
            ${icons.more}<span>More</span>
        </button>`;
    }

    document.body.appendChild(nav);
    _applyChatUnreadDot();   // re-show the unread dot after the nav rebuilds

    // Build more menu panel (hidden by default)
    const oldMore = document.getElementById('more-menu-panel');
    if (oldMore) oldMore.remove();
    if (moreItems.length > 0) {
        const morePanel = document.createElement('div');
        morePanel.id = 'more-menu-panel';
        morePanel.className = 'panel';
        let moreHTML = '<div class="panel-scroll"><div class="more-menu">';
        moreItems.forEach(id => {
            const tab = MC.tabs.find(t => (t.id || t) === id);
            const abTab = BOARD_CONFIG.action.tabs.find(t => t.id === id);
            const label = tab?.label || abTab?.label || id.charAt(0).toUpperCase() + id.slice(1);
            const icon = icons[id] || icons.more;
            moreHTML += `<button class="more-item" onclick="switchToTab('${id}'); _closeMoreMenu();">
                ${icon}<div>${ESC(label)}</div>
            </button>`;
        });
        moreHTML += '</div></div>';
        morePanel.innerHTML = moreHTML;
        document.getElementById('panel-container').appendChild(morePanel);
    }
}

function _updateBottomNav(activeTab) {
    document.querySelectorAll('.bottom-nav .nav-item').forEach(b => {
        b.classList.toggle('active', b.dataset.tab === activeTab);
    });
    // Close more menu if open
    _closeMoreMenu();
}

// ── Chat unread dot ──────────────────────────────────────────────────────────
// A small persistent dot on the Chat nav item (no number) that stays until the
// operator opens chat. Set when the personal agent posts on its own — the
// post-brain-connect "thanks for the brain" message (home.js). Survives reloads.
function _applyChatUnreadDot() {
    let on = false;
    try { on = localStorage.getItem('lp_chat_unread') === '1'; } catch (e) {}
    const btn = document.querySelector('.bottom-nav .nav-item[data-tab="chat"]');
    if (!btn) return;
    let dot = btn.querySelector('.nav-unread-dot');
    if (on) {
        if (!dot) {
            dot = document.createElement('span');
            dot.className = 'nav-unread-dot';
            dot.style.cssText = 'position:absolute;top:6px;right:calc(50% - 16px);width:8px;height:8px;border-radius:50%;background:var(--accent,#5ce1e6);box-shadow:0 0 6px var(--accent,#5ce1e6);';
            btn.style.position = 'relative';
            btn.appendChild(dot);
        }
    } else if (dot) {
        dot.remove();
    }
}
function markChatUnread() {
    try { localStorage.setItem('lp_chat_unread', '1'); } catch (e) {}
    _applyChatUnreadDot();
}
function clearChatUnread() {
    try { localStorage.removeItem('lp_chat_unread'); } catch (e) {}
    _applyChatUnreadDot();
}

// upgrade.js, onboarding.js — loaded on-demand (see boot)

let _tabBeforeMore = '';

function _toggleMoreMenu() {
    const morePanel = document.getElementById('more-menu-panel');
    if (!morePanel) return;
    const isOpen = morePanel.classList.contains('active');
    if (!isOpen) {
        _tabBeforeMore = activeTab || 'home';
        // Hide all panels (clear classes + inline styles)
        document.querySelectorAll('.panel').forEach(p => {
            p.classList.remove('active', 'active-grid', 'active-flex');
            p.style.display = '';
        });
        document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
        // Show more menu
        morePanel.classList.add('active');
        // Update bottom nav
        document.querySelectorAll('.bottom-nav .nav-item').forEach(b => {
            b.classList.toggle('active', b.dataset.tab === 'more');
        });
    } else {
        _closeMoreMenu();
    }
}

function _closeMoreMenu() {
    const morePanel = document.getElementById('more-menu-panel');
    if (morePanel) {
        morePanel.classList.remove('active');
    }
    // Return to the tab that was active before More was opened
    if (_tabBeforeMore) {
        switchToTab(_tabBeforeMore);
        _tabBeforeMore = '';
    }
}


// =============================================================================
// Dynamic script loading
// =============================================================================
// #PERF-MC1: track loaded script paths so we never double-inject (redeclaration).
const _loadedScripts = new Set();
const _scriptInflight = new Map(); // src -> Promise

function loadScript(src) {
    // Normalize to path without query for the cache key
    const key = src.split('?')[0];
    if (_loadedScripts.has(key)) return Promise.resolve();
    if (_scriptInflight.has(key)) return _scriptInflight.get(key);

    const p = new Promise((resolve, reject) => {
        // Already in DOM from a prior partial boot?
        const existing = document.querySelector(`script[data-mc-src="${key}"]`);
        if (existing) {
            _loadedScripts.add(key);
            resolve();
            return;
        }
        const s = document.createElement('script');
        s.dataset.mcSrc = key;
        // Append build version for cache-busting (set from /api/config)
        const v = window._buildVersion || '';
        s.src = v ? `${key}?v=${v}` : key;
        s.onload = () => {
            _loadedScripts.add(key);
            _scriptInflight.delete(key);
            if (window._mcDebugScripts) window._mcDebugScripts[key] = 'loaded';
            if (window._mcDebugLog) window._mcDebugLog('[SCRIPT OK] ' + key);
            resolve();
        };
        s.onerror = (e) => {
            _scriptInflight.delete(key);
            if (window._mcDebugScripts) window._mcDebugScripts[key] = 'FAILED';
            if (window._mcDebugLog) window._mcDebugLog('[SCRIPT FAIL] ' + key);
            reject(e);
        };
        document.body.appendChild(s);
    });
    _scriptInflight.set(key, p);
    return p;
}

/** Parallel load of many /static/js/*.js basenames (no .js). Failures are soft. */
async function loadScriptBasenames(names) {
    const list = [];
    const seen = new Set();
    for (const n of names || []) {
        if (!n || seen.has(n)) continue;
        seen.add(n);
        list.push(n);
    }
    await Promise.all(list.map(async (script) => {
        try {
            await loadScript(`/static/js/${script}.js`);
        } catch {
            console.warn(`[core] No script found: ${script}.js`);
        }
    }));
}

/** Scripts required for first paint on a given landing tab (home is the common case). */
function _shellScriptBasenames(firstTab) {
    // #PERF-MC1 cold shell: only what Attention home needs for first paint.
    // action-board (~127KB) loads when the operator opens Action board (or lands
    // on an ab-* tab). onboarding/upgrade load with Tuners or on first use.
    const shell = ['quick-list'];
    const id = firstTab || 'home';
    if (typeof id === 'string' && id.indexOf('ab-') === 0) {
        shell.push('action-board');
    }
    if (MC.isTuner) {
        shell.push('upgrade', 'onboarding');
    }
    // presence-profile is Team/Market only — load with those tabs, not cold boot.
    if (id === 'team' || id === 'market' || (typeof id === 'string' && id.indexOf('presence') === 0)) {
        shell.push('presence-profile');
    }
    return shell;
}

function _tabScriptBasenames(tabId) {
    if (!tabId) return [];
    // Action Board virtual tabs (ab-*) live in action-board.js (shell).
    if (typeof tabId === 'string' && tabId.indexOf('ab-') === 0) return [];
    const tab = (MC.tabs || []).find(t => (t.id || t) === tabId);
    if (!tab) {
        // Fallback: assume /static/js/{id}.js for unknown ids (haven, etc.)
        return [tabId];
    }
    let scripts = tab.scripts || [tab.script || (tab.id || tab)];
    if (!Array.isArray(scripts)) scripts = [scripts];
    // Chat tab historically listed chat/voice/manager-chat; messaging.js is the real file.
    const out = [];
    for (const s of scripts) {
        if (!s) continue;
        if (s === 'chat') out.push('messaging');
        else out.push(s);
    }
    // Connect is injected into chat scripts server-side; ensure mapping stays.
    return out;
}

async function loadBootShellScripts(firstTab) {
    await loadScriptBasenames(_shellScriptBasenames(firstTab));
}

async function ensureTabScripts(tabId) {
    const names = _tabScriptBasenames(tabId);
    // Team/Market also need presence-profile (#176).
    if (tabId === 'team' || tabId === 'market') names.push('presence-profile');
    // Action Board virtual tabs need action-board.js (no longer cold-shell).
    if (typeof tabId === 'string' && tabId.indexOf('ab-') === 0) names.push('action-board');
    await loadScriptBasenames(names);
}

/** Legacy name — loads ALL tab scripts (tests / debug). Prefer ensureTabScripts. */
async function loadTabScripts() {
    const names = [];
    for (const tab of (MC.tabs || [])) {
        names.push.apply(names, _tabScriptBasenames(tab.id || tab));
    }
    names.push('presence-profile');
    await loadScriptBasenames(names);
}

function _scheduleIdleTabPrefetch(skipTabId) {
    // #PERF-MC1: do NOT prefetch the full tab roster on constrained links.
    // Idle-prefetch of every panel JS re-flooded DERP right after first paint
    // and felt like a multi-minute hang. Warm only Chat (next most common tab)
    // after a long delay; everything else loads on switch via ensureTabScripts.
    const warm = () => {
        (async () => {
            const rest = (MC.tabs || []).map(t => t.id || t).filter(Boolean);
            const want = [];
            if (rest.indexOf('chat') !== -1 && skipTabId !== 'chat') want.push('chat');
            for (const id of want) {
                try { await ensureTabScripts(id); } catch (e) { /* ignore */ }
            }
        })();
    };
    // Long delay so first-paint APIs (approvals, lists) finish without competition.
    setTimeout(warm, 12000);
}

// =============================================================================
// Tab switching
// =============================================================================
let _lastSwitchTime = 0;
async function switchToTab(tabName) {
    // Debounce: block same-tab re-fire within 200ms (prevents double-tap reload)
    const now = Date.now();
    if (tabName === activeTab && (now - _lastSwitchTime) < 200) return;
    _lastSwitchTime = now;

    // Ensure this tab's JS is present before loaders run (#PERF-MC1 lazy boot).
    try {
        await ensureTabScripts(tabName);
    } catch (e) {
        console.warn('[core] ensureTabScripts failed for', tabName, e);
    }

    // Opening the agent chat clears the persistent unread dot (the wake/brain-connect
    // message has now been seen).
    if (tabName === 'chat' && typeof clearChatUnread === 'function') clearChatUnread();

    // Disconnect live log stream when leaving System tab
    if (activeTab === 'system' && typeof disconnectLogStream === 'function') disconnectLogStream();
    // Stop JW auto-refresh when leaving Reports
    if (typeof stopJwAutoRefresh === 'function') stopJwAutoRefresh();

    activeTab = tabName;
    try { sessionStorage.setItem('mc_active_tab', tabName); } catch(e) {}

    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.panel').forEach(p => {
        p.classList.remove('active', 'active-grid', 'active-flex');
        p.style.display = '';  // Clear any inline display overrides from detail panels
    });

    // Close more menu if open
    _closeMoreMenu();

    // Close any open flow overlay — clicking a tab navigates away from it
    if (typeof closeFlowOverlay === 'function') closeFlowOverlay(true);

    // Highlight in both tab bars (Attention and Action Board)
    document.querySelectorAll(`.tab[data-tab="${tabName}"]`).forEach(b => b.classList.add('active'));

    const panel = document.getElementById(`panel-${tabName}`);
    if (panel) panel.classList.add('active');

    // Update bottom nav highlight
    if (typeof _updateBottomNav === 'function') _updateBottomNav(tabName);

    // Trigger load functions for known tabs
    const loaders = {
        home: () => {
            if (typeof loadHome === 'function') loadHome();
            // The Cove-admin apex 'home' is the Presences panel (loadHome branches to
            // loadCoveAdminPresences). The personal-home loaders below target elements
            // that don't exist there — running them throws + breaks the admin render.
            if (!MC.coveAdminView) {
                if (typeof loadOverview === 'function') loadOverview();
                if (typeof loadQuickLists === 'function') loadQuickLists();
                if (typeof _tfUpdateHomeButton === 'function') _tfUpdateHomeButton();
                if (typeof _renderUpgradeCTA === 'function') _renderUpgradeCTA();
            }
        },
        haven: () => {
            if (typeof loadHaven === 'function') loadHaven();
        },
        chat: () => {
            // ONLY the agentless public-app operator gets Connect as their chat surface.
            // A real Cove operator HAS an agent → show their agent chat, never route to
            // Connect just because a tier didn't land at >=20 (same robust gate as the
            // chat render + settings). This was opening the empty Matrix layer instead of
            // the agent chat for self-host operators.
            const _agentless = !!(MC.config && MC.config.is_public_app)
                && !(MC.config && MC.config.has_personal_agent)
                && !(MC.tier && MC.tier.level >= 20);
            if (_agentless && typeof window.openConnect === 'function') { window.openConnect(); return; }
            // Manager MC: supervisory mode may have been skipped at script inject
            // (#PERF-MC1). Enter it now that Chat is active.
            if (MC.adminView === true && typeof initStewardChat === 'function') {
                try { initStewardChat(); } catch (e) { console.warn('[core] initStewardChat', e); }
                return;
            }
            // Re-load so messages posted after boot (the brain-connect "thanks") appear.
            if (typeof loadChat === 'function') loadChat();
            if (typeof scrollToBottom === 'function') scrollToBottom();
        },
        calendar: () => typeof loadCalendar === 'function' && loadCalendar(),
        files: () => typeof loadFiles === 'function' && loadFiles('/'),
        tasks: () => typeof loadTasks === 'function' && loadTasks(),
        tuning: () => typeof loadTuning === 'function' && loadTuning(),
        joulework: () => typeof loadJouleWork === 'function' && loadJouleWork(),
        reports: () => {
            if (typeof loadTuning === 'function') loadTuning();
        },
        memory: () => typeof loadMemory === 'function' && loadMemory(),
        tune: () => typeof loadTuneFlow === 'function' && loadTuneFlow(),
        playlists: () => typeof loadPlaylistsTab === 'function' && loadPlaylistsTab(),
        affiliates: () => typeof loadAffiliates === 'function' && loadAffiliates(),
        settings: () => typeof loadSettings === 'function' && loadSettings(),
        team: () => typeof loadTeam === 'function' && loadTeam(),
        projects: () => typeof loadProjectsTab === 'function' && loadProjectsTab(),
        system: () => typeof loadSystem === 'function' && loadSystem(),
        'ab-actions': () => typeof loadABActions === 'function' && loadABActions(),
        'ab-links': () => typeof loadABLinks === 'function' && loadABLinks(),
        'ab-flows': () => typeof loadABFlows === 'function' && loadABFlows(),
        'ab-tools': () => typeof loadABTools === 'function' && loadABTools(),
    };

    const loader = loaders[tabName];
    if (loader) loader();

    // Debug: report chat tab state when switching to it
    if (tabName === 'chat' && window._mcDebugLog) {
        var cp = document.getElementById('panel-chat');
        var ct = document.getElementById('channel-tabs');
        var wi = document.getElementById('welcome-text');
        var ci = document.getElementById('chat-input');
        var mb = document.getElementById('chat-mic');
        window._mcDebugLog('[TAB→CHAT] panel=' + (cp ? cp.className : 'MISSING') +
            ' display=' + (cp ? getComputedStyle(cp).display : '?') +
            ' channelTabs=' + (ct ? ct.children.length + 'btns,display=' + getComputedStyle(ct).display : 'MISSING') +
            ' welcome=' + (wi ? '"' + wi.textContent + '"' : 'MISSING') +
            ' input=' + (ci ? 'YES' : 'MISSING') +
            ' mic=' + (mb ? 'YES' : 'MISSING'));
    }
}

// =============================================================================
// Reports sub-tab switching
// =============================================================================
function switchReportsSub(subName) {
    // Toggle sub-tab buttons
    document.querySelectorAll('#reports-sub-tabs .sub-tab').forEach(b => b.classList.remove('active'));
    const btn = document.querySelector(`#reports-sub-tabs .sub-tab[data-sub="${subName}"]`);
    if (btn) btn.classList.add('active');

    // Toggle sub-panels
    document.querySelectorAll('.reports-sub').forEach(p => {
        p.style.display = 'none';
        p.classList.remove('active');
    });
    const panel = document.getElementById(`reports-sub-${subName}`);
    if (panel) {
        panel.style.display = '';
        panel.classList.add('active');
    }

    // Load content
    if (subName === 'tuning' && typeof loadTuning === 'function') loadTuning();
    if (subName === 'joulework' && typeof loadJouleWork === 'function') loadJouleWork();
}

// =============================================================================
// Status polling
// =============================================================================
async function _pollTuningBadge(freq, sub) {
    // Shared helper: populate header freq badge from tuning data
    try {
        // All tiers use /api/tuning/operator — single endpoint, flat response shape.
        // Cache-bust: v= on deploy, d= daily
        const _cb = `v=${window._buildVersion || ''}&d=${new Date().toISOString().slice(0,10)}`;
        const tuning = await fetch('/api/tuning/operator?' + _cb).then(r => r.ok ? r.json() : null);
        if (tuning && tuning.has_tuning && tuning.frequency) {
            window._todayTuning = tuning;  // stash for home tab
            freq.textContent = tuning.frequency;
            const fc = lpFreqColor(tuning.frequency);
            freq.style.color = fc.primary;
            freq.style.borderColor = fc.primary;
            const r2 = parseInt(fc.primary.slice(1,3),16)||0, g2 = parseInt(fc.primary.slice(3,5),16)||0, b2 = parseInt(fc.primary.slice(5,7),16)||0;
            freq.style.background = `rgba(${r2},${g2},${b2},0.08)`;
            // Don't overwrite subtitle — archetype (agent tiers) or @handle
            // (Presence login) are set at boot and should stay. The principle
            // is already visible in the frequency badge.
        } else {
            freq.textContent = '—';
        }
    } catch {
        freq.textContent = '—';
    }
}

async function pollStatus() {
    try {
        const data = await fetch('/api/status').then(r => r.json());
        const dot = document.getElementById('conn-dot');
        const freq = document.getElementById('header-freq');
        const sub = document.getElementById('header-status');

        dot.className = 'status-dot mobile-hide green';

        const nameEl = document.getElementById('header-name');
        if (nameEl && MC.isCove && !MC.presence) {
            // Standalone Cove MC only: construct display name from agent + family.
            // Skip for multi-presence (MC.presence set) — name was set at boot from user's agent.
            const hhSfx = MC.instance.family_name ? ` ${MC.instance.family_name}` : '';
            nameEl.textContent = MC.agentName + hhSfx;
        }

        // Badge always shows LT's orchestrated frequency — the human-facing
        // daily tuning signal. Agent echoes are backend; the badge is for operators.
        await _pollTuningBadge(freq, sub);
    } catch {
        // /api/status failed (e.g. tables don't exist on Operator/shared tier)
        // Still get LT daily tuning for the header freq badge
        const dot = document.getElementById('conn-dot');
        const freq = document.getElementById('header-freq');
        const sub = document.getElementById('header-status');
        if (dot) dot.className = 'status-dot mobile-hide green';
        await _pollTuningBadge(freq, sub);
    }
}

// #1626: agent wake pop-in — show presence first_message on set-address surfaces.
// Shared by home onboarding + settings Cove-admin address panel. ESC lives in lp-colors.js.
async function _mountAgentWakeCard(containerId) {
    const el = document.getElementById(containerId);
    if (!el) return;
    try {
        const r = await fetch('/api/agent/first-message');
        if (!r.ok) return;
        const d = await r.json();
        const msg = (d && d.first_message) ? String(d.first_message).trim() : '';
        if (!msg) return;
        const name = (d && d.name) ? String(d.name).trim()
            : ((typeof MC !== 'undefined' && MC.agentName) || 'Your agent');
        el.innerHTML = `<div class="approval-card brain-awake" style="margin:8px 0 10px;padding:10px 12px;border:1px solid var(--border);border-radius:8px;background:rgba(126,184,218,0.06);">
            <div class="approval-title" style="font-weight:600;font-size:0.85rem;">${ESC(name)} is awake</div>
            <div class="approval-desc" style="margin-top:4px;font-size:0.8rem;line-height:1.5;color:var(--text);white-space:pre-wrap;">${ESC(msg)}</div>
        </div>`;
    } catch (e) { /* leave surface clean if the wake message is unavailable */ }
}

// =============================================================================
// Boot
// =============================================================================
boot();
