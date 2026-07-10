// =============================================================================
// Manager MC — Supervisory Chat View
// =============================================================================
// When the MC is a manager (instance.type === 'admin' or 'manager'), Chat shows
// read-only Presence tabs instead of interactive channels. Each tab lists that
// Presence's manager channel threads. Clicking a thread opens a read-only viewer.
// Depends on chat.js being loaded first (uses formatMessage, ESC, formatDate, etc.)
// =============================================================================

let _stewardMode = false;
let _stewardPresences = [];
let _activePresence = null;
let _viewingThread = false;
let _activeManager = 'stuart';      // level 2: stuart | mercer
let _activeChannelTier = 'day';     // level 3: day | deep

function _isManagerMC() {
    // Manager/admin MCs use the supervisory read-only chat view. Trigger on the
    // host-context admin flag (stuart.{cove} → MC.adminView) as well as an explicit
    // instance.type — these were out of sync, so the read-only view never fired.
    const t = MC.instance?.type || '';
    return t === 'admin' || t === 'manager' || MC.adminView === true;
}

async function initStewardChat() {
    _stewardMode = true;

    // Hide interactive elements — steward MC is read-only
    const inputArea = document.querySelector('.chat-input-area');
    const modeBar = document.querySelector('.chat-mode-bar');
    const activityLive = document.getElementById('activity-live');
    const progressBar = document.getElementById('chat-progress-bar');

    if (inputArea) inputArea.style.display = 'none';
    if (modeBar) modeBar.style.display = 'none';
    if (activityLive) activityLive.style.display = 'none';
    if (progressBar) progressBar.style.display = 'none';

    // Show loading state
    const box = document.getElementById('chat-messages');
    if (box) box.innerHTML = '<div class="loading">Loading Presence threads...</div>';

    // Fetch Presence-grouped threads
    try {
        const res = await fetch('/api/threads/by-presence');
        const data = await res.json();

        if (data.error) {
            if (box) box.innerHTML = `<span class="empty">Error: ${ESC(data.error)}</span>`;
            return;
        }

        _stewardPresences = data.presences || [];

        if (_stewardPresences.length === 0) {
            if (box) box.innerHTML = '<div class="chat-welcome"><div class="welcome-icon">\u{1F3E0}</div><div class="welcome-text">No Presence conversations yet.</div></div>';
            return;
        }

        // Build Presence tabs
        _buildPresenceTabs();

        // Load first Presence's threads
        _switchPresence(_stewardPresences[0]);

    } catch (e) {
        console.error('Failed to load steward threads:', e);
        if (box) box.innerHTML = `<span class="empty">Failed to load: ${ESC(e.message)}</span>`;
    }
}

function _buildPresenceTabs() {
    const container = document.getElementById('channel-tabs');
    if (!container) return;
    container.innerHTML = '';

    const tabBar = document.createElement('div');
    tabBar.className = 'agent-selector';
    tabBar.id = 'presence-selector';

    _stewardPresences.forEach(p => {
        const btn = document.createElement('button');
        btn.className = `agent-tab${p === _activePresence ? ' active' : ''}`;
        btn.dataset.presenceName = p.name;
        btn.textContent = p.name;
        btn.addEventListener('click', () => _switchPresence(p));
        tabBar.appendChild(btn);
    });

    container.appendChild(tabBar);
}

function _switchPresence(presence) {
    _activePresence = presence;
    _viewingThread = false;
    _activeManager = 'stuart';
    _activeChannelTier = 'day';

    // Level 1 — Presence tab highlights
    document.querySelectorAll('#presence-selector .agent-tab').forEach(t => {
        t.classList.toggle('active', t.dataset.presenceName === presence.name);
    });

    _renderManagerView(presence);
}

// Levels 2 & 3: the selected Presence's Stuart/Mercer threads, by Day/Deep.
// Hierarchy: Presence (tabs above) -> Stuart|Mercer -> Day|Deep -> read-only thread.
function _renderManagerView(presence) {
    const box = document.getElementById('chat-messages');
    if (!box) return;
    box.innerHTML = '';

    // Level 2 — manager tabs (Stuart / Mercer)
    const mgrBar = document.createElement('div');
    mgrBar.className = 'agent-selector steward-subnav';
    [['stuart', 'Stuart'], ['mercer', 'Mercer']].forEach(([id, label]) => {
        const b = document.createElement('button');
        b.className = `agent-tab${_activeManager === id ? ' active' : ''}`;
        b.textContent = label;
        b.addEventListener('click', () => { _activeManager = id; _renderManagerView(presence); });
        mgrBar.appendChild(b);
    });
    box.appendChild(mgrBar);

    // Level 3 — Day / Deep tabs (standard subchannel styling, matches normal chat)
    const tierBar = document.createElement('div');
    tierBar.className = 'subchannel-tabs steward-subnav';
    [['day', 'DAY'], ['deep', 'DEEP']].forEach(([id, label]) => {
        const b = document.createElement('button');
        b.className = `channel-tab${_activeChannelTier === id ? ' active' : ''}`;
        b.textContent = label;
        b.addEventListener('click', () => { _activeChannelTier = id; _renderManagerView(presence); });
        tierBar.appendChild(b);
    });
    box.appendChild(tierBar);

    // Read-only thread for presence + manager + tier
    const tv = document.createElement('div');
    tv.id = 'steward-thread-view';
    box.appendChild(tv);

    const channel = `${_activeManager}-${_activeChannelTier}`;
    // jules 1648: show the CHANNEL'S HISTORY, not just one thread — every thread
    // for this channel, newest first, selectable.
    const channelThreads = (presence.threads || [])
        .filter(t => (t.channel || '') === channel)
        .sort((a, b) => _stewardTsMs(b.updated_at || b.created_at) - _stewardTsMs(a.updated_at || a.created_at));
    const mgrLabel = _activeManager.charAt(0).toUpperCase() + _activeManager.slice(1);
    if (!channelThreads.length) {
        tv.innerHTML = `<div class="chat-welcome"><div class="welcome-icon">\u{1F4AC}</div><div class="welcome-text">No ${mgrLabel} · ${_activeChannelTier.toUpperCase()} conversation from ${ESC(presence.name)} yet.</div></div>`;
        return;
    }
    if (channelThreads.length > 1) {
        const hist = document.createElement('div');
        hist.className = 'subchannel-tabs steward-subnav';
        hist.style.flexWrap = 'wrap';
        channelThreads.forEach((t, i) => {
            const b = document.createElement('button');
            b.className = `channel-tab${i === 0 ? ' active' : ''}`;
            const when = t.updated_at || t.created_at;
            b.textContent = (when ? formatDate(when) : `Thread ${channelThreads.length - i}`)
                + (t.status && t.status !== 'active' ? ` · ${t.status}` : '');
            b.addEventListener('click', () => {
                hist.querySelectorAll('.channel-tab').forEach(x => x.classList.remove('active'));
                b.classList.add('active');
                _openStewardThreadReader(t.thread_id, presence, tv);
            });
            hist.appendChild(b);
        });
        box.insertBefore(hist, tv);
    }
    _openStewardThreadReader(channelThreads[0].thread_id, presence, tv);
}

// Parse DB/ISO timestamps consistently as UTC. Bare "YYYY-MM-DD HH:MM:SS"
// strings parse as LOCAL time while ISO message timestamps carry a zone —
// hours of skew that dumped every activity block above the chat (jules 1648).
function _stewardTsMs(v) {
    if (!v) return 0;
    let s = String(v);
    if (!/[zZ]$|[+-]\d\d:?\d\d$/.test(s)) s = s.replace(' ', 'T') + 'Z';
    const t = new Date(s).getTime();
    return isNaN(t) ? 0 : t;
}

async function _openStewardThreadReader(threadId, presence, target) {
    _viewingThread = true;
    const box = target || document.getElementById('chat-messages');
    if (!box) return;

    box.innerHTML = '<div class="loading">Loading thread...</div>';

    try {
        const res = await fetch(`/api/threads/${encodeURIComponent(threadId)}/history`);
        const data = await res.json();

        if (data.error) {
            box.innerHTML = `<span class="empty">Error: ${ESC(data.error)}</span>`;
            return;
        }

        box.innerHTML = '';

        // Read-only label — navigation is handled by the Presence / Stuart-Mercer /
        // Day-Deep tabs above, so no back button here.
        const meta = data.thread || {};
        // #D25 (c): an archived thread whose checkpointer state was pruned returns no live
        // messages. Show the stored message_count (the real length) when we have it, not 0.
        const liveCount = data.messages ? data.messages.length : 0;
        const shownCount = liveCount || (meta.message_count || 0);
        const readOnly = document.createElement('div');
        readOnly.className = 'steward-readonly-label';
        readOnly.textContent = `Read-only · ${meta.status || 'active'} · ${shownCount} messages`;
        box.appendChild(readOnly);

        // Messages
        if (!data.messages || data.messages.length === 0) {
            // #D25 (c): fall back to the stored summary of the archived conversation
            // instead of a bare "no messages" over a thread that clearly had some.
            if (meta.summary) {
                const sum = document.createElement('div');
                sum.className = 'steward-thread-summary';
                const h = document.createElement('div');
                h.className = 'steward-summary-label';
                h.textContent = 'Archived — summary of this conversation:';
                const body = document.createElement('div');
                body.className = 'steward-summary-body';
                body.textContent = meta.summary;
                sum.appendChild(h);
                sum.appendChild(body);
                box.appendChild(sum);
                return;
            }
            const empty = document.createElement('span');
            empty.className = 'empty';
            empty.textContent = 'No messages in this thread.';
            box.appendChild(empty);
            return;
        }

        const activity = (data.activity || []).slice();
        let actIdx = 0;

        function renderInlineActivity(record) {
            if (!record.steps || !record.steps.length) return;
            const details = document.createElement('details');
            details.className = 'activity-collapsed';
            const summary = document.createElement('summary');
            summary.textContent = `${record.step_count} steps`;
            details.appendChild(summary);
            const wrap = document.createElement('div');
            wrap.className = 'activity-steps-wrap';
            record.steps.forEach(s => {
                const step = document.createElement('div');
                step.className = 'activity-step';
                step.textContent = s;
                wrap.appendChild(step);
            });
            details.appendChild(wrap);
            box.appendChild(details);
        }

        data.messages.forEach(msg => {
            const role = msg.role === 'human' ? 'user' : 'assistant';
            const ts = msg.timestamp || null;

            // Insert activity records that precede this message. UTC-normalized
            // comparison (jules 1648): bare DB timestamps used to parse as local
            // time, skewing hours earlier than the ISO message timestamps — which
            // stacked every step block on top with the chat underneath.
            if (role === 'assistant' && ts) {
                const msgTime = _stewardTsMs(ts);
                while (actIdx < activity.length) {
                    const actTime = _stewardTsMs(activity[actIdx].recorded_at);
                    if (actTime <= msgTime) {
                        renderInlineActivity(activity[actIdx]);
                        actIdx++;
                    } else break;
                }
            }

            // Display names — Presence (operator) for the user side, the MANAGER
            // (Stuart/Mercer) for the assistant side — never the presence's own agent.
            const _mgrName = _activeManager || 'stuart';
            const roleLabel = role === 'user'
                ? (presence.name || MC.operatorName).toUpperCase()
                : _mgrName.toUpperCase();

            const div = document.createElement('div');
            div.className = `message ${role}`;

            // Header
            let headerHTML = `<div class="msg-header"><span class="msg-header-left"><span class="role-label">${ESC(roleLabel)}</span>`;
            if (msg.model && role !== 'user') {
                let shortModel = msg.model;
                if (shortModel.includes('/')) shortModel = shortModel.split('/').pop();
                if (shortModel.length > 20) shortModel = shortModel.substring(0, 20);
                headerHTML += `<span class="msg-model">${ESC(shortModel)}</span>`;
            }
            headerHTML += `</span>`;
            if (ts) {
                headerHTML += `<span class="msg-timestamp">${formatDate(ts)}</span>`;
            }
            headerHTML += `</div>`;

            // Thinking block
            let thinkHTML = '';
            if (msg.thinking && role !== 'user') {
                thinkHTML = `<details class="thinking-block"><summary>Thinking</summary><div class="thinking-content">${formatMessage(msg.thinking)}</div></details>`;
            }

            div.innerHTML = headerHTML + thinkHTML + `<div class="msg-body">${formatMessage(msg.content)}</div>`;
            box.appendChild(div);
        });

        // Remaining activity
        while (actIdx < activity.length) {
            renderInlineActivity(activity[actIdx]);
            actIdx++;
        }

        box.scrollTop = 0;

    } catch (e) {
        box.innerHTML = `<span class="empty">Failed to load thread: ${ESC(e.message)}</span>`;
    }
}

// =============================================================================
// Auto-init: if this is a manager MC, switch to supervisory mode
// =============================================================================
// This runs after chat.js has already called initChat(). If this is a manager MC,
// initStewardChat() completely rebuilds the chat area in read-only supervisory mode.
// =============================================================================
try {
    if (_isManagerMC()) {
        initStewardChat();
        if (window._mcDebugLog) window._mcDebugLog('[CHAT] Manager MC — supervisory mode');
    }
} catch (e) {
    console.error('initStewardChat failed:', e);
    if (window._mcDebugLog) window._mcDebugLog('[CHAT ERR] initStewardChat: ' + e.message);
}
