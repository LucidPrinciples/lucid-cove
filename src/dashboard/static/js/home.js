// =============================================================================
// home.js — Home tab (calendar, approvals, tasks, projects quick-add)
// =============================================================================
// Uses MC config from core.js. No hardcoded agent names or URLs.
// =============================================================================

// ── Priority colors (frequency-derived — see LP object in core.js) ──────────
const _priColors = {
    'urgent':  'var(--red)',     // Momentum — needs action NOW
    'high':    'var(--orange)',  // Courage — face it
    'normal':  'var(--silver)',  // Trust — steady
    'low':     'rgba(128,128,128,0.3)',
};

function _isAdmin() {
    const t = MC.instance?.type || '';
    return t === 'admin' || t === 'domain' || t === 'manager';
}

async function loadHome() {
    // Cove-admin apex surface: populate the Presences stub, skip personal home.
    if (MC.coveAdminView) { loadCoveAdminPresences(); return; }
    loadHomeCalendar();
    loadHomeApprovals();
    loadHomeTasks();
}

// ── Cove-admin apex: list the Presences in this Cove (stub) ──────────────────
// Self-contained (home.js is always loaded for the home panel) so it never
// depends on the settings-only admin script being present.
async function loadCoveAdminPresences() {
    const el = document.getElementById('cove-admin-presences');
    if (!el) return;
    // Setup checklist ALSO renders here, above the presence list, so a new operator sees
    // the same nags (address / intelligence / compute / mobile) no matter whether they
    // land on their personal home or the Cove-admin apex. (Chords, 2026-07-01.)
    let setupHtml = '';
    try {
        const ob = await fetch('/api/onboarding/items').then(r => r.json());
        if (ob && (ob.steps || []).length && !ob.complete) setupHtml = _renderSetup(ob);
    } catch (e) { /* non-fatal — the presence list still renders */ }
    try {
        const data = await fetch('/api/family').then(r => r.json());
        const members = data.members || [];
        // CF-58: reuse the team page's rich family-pair renderer off the SAME /api/family source,
        // so Cove Admin IS the Presence-management surface and stays identical to the team page.
        // familyPair lives in team.js (tab-lazy-loaded, never loads on the Cove-admin apex) —
        // pull it in on demand before rendering.
        if (members.length && typeof familyPair !== 'function') {
            try { await loadScript('/static/js/team.js'); } catch (e) { console.warn('[home] team.js not found', e); }
        }
        const listHtml = !members.length
            ? '<span style="color:var(--dim);font-size:0.82rem;font-style:italic;">No Presences yet</span>'
            : (typeof familyPair === 'function'
                ? members.map(familyPair).join('')
                : '<div class="error-msg">Presence renderer not loaded</div>');
        el.innerHTML = setupHtml + listHtml;
    } catch (err) {
        el.innerHTML = setupHtml + `<div class="error-msg">${ESC(err.message || 'Failed to load Presences')}</div>`;
    }
}

// ── Upcoming Calendar Events ─────────────────────────────────────────────────

async function loadHomeCalendar() {
    const el = document.getElementById('home-upcoming');
    if (!el) return;
    try {
        const data = await fetch('/api/calendar/events?days=3').then(r => r.json());
        if (data.error) {
            el.innerHTML = `<span class="empty-msg">Calendar: ${ESC(data.error)}</span>`;
            return;
        }
        const events = (data.events || []).slice(0, 5);
        if (!events.length) {
            el.innerHTML = '<span class="empty-msg">Nothing in the next 3 days</span>';
            return;
        }

        el.innerHTML = events.map(e => {
            let time = 'All day';
            if (!e.all_day && e.start && e.start.length > 10) {
                const day = formatDate(e.start, { weekday: 'short' });
                time = `${day} ${formatTime(e.start)}`;
            } else if (e.start) {
                const d = new Date(e.start + 'T12:00:00');
                time = formatDate(e.start, { weekday: 'short', month: 'short', day: 'numeric' });
            }
            return `<div class="home-event">
                <span class="home-event-time">${ESC(time)}</span>
                <span class="home-event-title">${ESC(e.summary)}</span>
            </div>`;
        }).join('');
    } catch {
        el.innerHTML = `<span class="empty-msg">Calendar unavailable</span>`;
    }
}

// When the approvals slot is empty, an OPERATOR sees their upcoming calendar events
// (an Operator has no agent, so this slot would otherwise sit empty; calendar rides on
// Nextcloud, Operator+). A Cove/Presence HAS an agent — its approvals slot fills with
// real agent approvals, so the calendar must NOT appear here (it belongs on the Calendar
// tab, not the attention slot). Tuners have no calendar → plain message either way.
async function _renderApprovalsUpcoming(el) {
    const hasCalendar = !!(MC.tier && MC.tier.level >= 10);  // operator and up
    const hasAgent = !!(MC.config && MC.config.has_personal_agent)
        || !!(MC.tier && (MC.tier.has_agent || MC.tier.level >= 20));
    if (!hasCalendar || hasAgent) {
        el.innerHTML = '<span class="empty-msg">Nothing needs your attention</span>';
        return;
    }
    try {
        const data = await fetch('/api/calendar/events?days=7').then(r => r.json());
        const events = (data.events || []).slice(0, 5);
        if (data.error || !events.length) {
            el.innerHTML = '<span class="empty-msg">Nothing on your calendar this week</span>';
            return;
        }
        el.innerHTML = '<div class="approval-tool" style="margin-bottom:6px;">Coming up</div>' +
            events.map(e => {
                let time = 'All day';
                if (!e.all_day && e.start && e.start.length > 10) {
                    time = `${formatDate(e.start, { weekday: 'short' })} ${formatTime(e.start)}`;
                } else if (e.start) {
                    time = formatDate(e.start, { weekday: 'short', month: 'short', day: 'numeric' });
                }
                return `<div class="home-event">
                    <span class="home-event-time">${ESC(time)}</span>
                    <span class="home-event-title">${ESC(e.summary)}</span>
                </div>`;
            }).join('');
    } catch {
        el.innerHTML = '<span class="empty-msg">Nothing needs your attention</span>';
    }
}

// ── Pending Approvals ────────────────────────────────────────────────────────

async function loadHomeApprovals() {
    const el = document.getElementById('home-approvals');
    const badge = document.getElementById('home-approvals-badge');
    if (!el) return;
    try {
        // First-run onboarding cards live here too — they sit until done, the same
        // way agent-activity approvals do.
        // #D18: Also fetch recent approvals to show PR review cards for create_github_pr results
        const [obData, data, watchData, recentData] = await Promise.all([
            fetch('/api/onboarding/items').then(r => r.json()).catch(() => ({ items: [] })),
            fetch('/api/bridge/approvals').then(r => r.json()).catch(() => ({ approvals: [] })),
            fetch('/api/watcher/alerts').then(r => r.ok ? r.json() : { alerts: [] }).catch(() => ({ alerts: [] })),
            fetch('/api/approvals/recent?limit=10').then(r => r.json()).catch(() => ({ approvals: [] })),
        ]);
        const steps = obData.steps || [];
        const showSetup = steps.length > 0 && !obData.complete;
        const approvals = data.approvals || [];
        const watcherAlerts = watchData.alerts || [];
        
        // #D18: Build PR review cards from recent create_github_pr approvals
        const recentApprovals = recentData.approvals || [];
        const prCards = _buildPRCards(recentApprovals);
        
        const total = (showSetup ? 1 : 0) + approvals.length + watcherAlerts.length + prCards.length;

        if (!total) {
            // Nothing needs approval. Operators (who have a calendar) get their next
            // few events here instead of a bare empty slot; Tuners just see the message.
            if (badge) badge.classList.add('hidden');
            await _renderApprovalsUpcoming(el);
            return;
        }

        if (badge) {
            badge.textContent = total;
            badge.classList.remove('hidden');
        }

        const obHtml = showSetup ? _renderSetup(obData) : '';
        const watchHtml = watcherAlerts.map(w => `<div class="home-approval watcher-alert">
                <div class="approval-tool">${w.urgency === 'high' ? '\u26a0 ' : ''}${ESC(w.title)}</div>
                <div class="approval-desc">${ESC(w.detail || '')}</div>
                <div class="approval-actions">
                    <button class="btn-deny" onclick="dismissWatcherAlert('${ESC(w.alert_key)}', this)">Dismiss</button>
                </div>
            </div>`).join('');
        const apHtml = approvals.map(a => {
            const isSiteEdit = a.tool_name === 'site_edit_file' || a.tool_name === 'site_create_file' || a.tool_name === 'site_patch_file';
            const args = a.args || {};

            if (isSiteEdit && args.domain) {
                const domain = ESC(args.domain);
                const filePath = ESC(args.file_path || '');
                const desc = ESC(args.edit_description || args.description || '');
                const branch = ESC(args.branch || '');
                const repo = ESC(args.repo || '');
                const diffId = `diff-${a.request_id}`;

                return `<div class="home-approval site-approval">
                    <div class="approval-tool">Site Edit — ${domain}</div>
                    <div class="approval-desc">${desc}</div>
                    <div class="approval-file">${filePath} on branch <code>${branch}</code></div>
                    ${repo && branch ? `<div class="approval-diff-toggle">
                        <a href="#" onclick="loadSiteDiff('${ESC(repo)}', '${ESC(branch)}', '${diffId}'); return false;">View Diff ▼</a>
                    </div>
                    <div id="${diffId}" class="approval-diff" style="display:none;"></div>` : ''}
                    <div class="approval-actions">
                        <button class="btn-approve" onclick="respondApproval('${ESC(a.request_id)}', true, this)">Approve &amp; Deploy</button>
                        <button class="btn-deny" onclick="respondApproval('${ESC(a.request_id)}', false, this)">Deny</button>
                    </div>
                </div>`;
            }

            return `<div class="home-approval">
                <div class="approval-tool">${ESC(a.tool_name)}</div>
                <div class="approval-desc">${ESC(a.description || '').substring(0, 120)}</div>
                <div class="approval-actions">
                    <button class="btn-approve" onclick="respondApproval('${ESC(a.request_id)}', true, this)">Approve</button>
                    <button class="btn-deny" onclick="respondApproval('${ESC(a.request_id)}', false, this)">Deny</button>
                </div>
            </div>`;
        }).join('');
        const prHtml = prCards.join('');
        el.innerHTML = obHtml + watchHtml + apHtml + prHtml;
        // Birth / brain-ack messages live in Chat only — not on the set-address card
        // (that surface is action-only; read-only wake text there was confusing and
        // hid the real conversation).
    } catch {
        el.innerHTML = '<span class="empty-msg">Approvals unavailable</span>';
        if (badge) badge.classList.add('hidden');
    }
}

// Watcher alert dismiss — the alert stays dismissed even if the condition
// persists (the operator has seen it; auto-resolve handles the rest).
async function dismissWatcherAlert(alertKey, btn) {
    if (btn) { btn.disabled = true; btn.textContent = 'Dismissing\u2026'; }
    try {
        await fetch('/api/watcher/alerts/dismiss', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ alert_key: alertKey }),
        });
    } catch { /* reload re-renders the truth either way */ }
    loadHomeApprovals();
}

// ── First-run onboarding cards (persistent until done) ───────────────────────
// Dependency-gated first-run setup, shown inside Pending Approvals.
// Address-first list order (Mosswood 2124): address + intelligence open first
// (either order) → compute (GPU / video) → backup / team-tuning / mobile.
// Soft-refresh after every ack/skip so done cards clear without a full page reload.
// Jules 0113: 30s Attention refresh used to wipe an expanded address/mobile form
// mid-setup (card "collapsed in 2–3 seconds"). Remember which setup cards are open
// across re-renders so the operator can keep working.
window._setupExpanded = window._setupExpanded || {};

function _renderSetup(obData) {
    const steps = obData.steps || [];
    const dc = obData.done_count || 0, tot = obData.total || steps.length;
    let html = `<div class="onboarding-setup">
        <div class="home-section-header" style="margin-bottom:6px;">
            <span class="home-section-title" style="font-size:0.78rem;">Set up your Cove — ${dc} of ${tot}</span>
            <a href="#" onclick="openOnboardingHelp(); return false;" class="home-link">How this works</a>
        </div>`;
    steps.forEach(s => {
        if (s.done) html += _setupDoneLine(s);
        else if (s.available) html += _onboardingCardHtml(s);
        else html += _setupLockedLine(s);
    });
    // Restore expanded claim/mobile panels after the HTML is mounted.
    setTimeout(_restoreSetupExpanded, 0);
    return html + `</div>`;
}

function _markSetupExpanded(id, open) {
    try {
        window._setupExpanded = window._setupExpanded || {};
        if (open) window._setupExpanded[id] = open;  // true | 'mesh' | mode string
        else delete window._setupExpanded[id];
    } catch (e) {}
}

function _restoreSetupExpanded() {
    const exp = window._setupExpanded || {};
    // Address: if the operator had Claim open (or a host_command pending card is
    // showing), keep the form visible instead of collapsing back to the CTA.
    if (exp.claim_address) {
        const form = document.getElementById('dom-form');
        const cta = document.getElementById('dom-cta');
        const mesh = document.getElementById('addr-mesh-step');
        // Prefer the claim form when we left it open; mesh step only if that was open.
        if (exp.claim_address === 'mesh' && mesh) {
            mesh.style.display = 'block';
            if (cta) cta.style.display = 'none';
        } else if (form) {
            form.style.display = 'block';
            if (cta) cta.style.display = 'none';
            if (mesh) mesh.style.display = 'none';
        }
    }
    // Intelligence form
    if (exp.add_intelligence) {
        const f = document.getElementById('ai-form');
        const c = document.getElementById('ai-cta');
        if (f) f.style.display = 'block';
        if (c) c.style.display = 'none';
    }
    // Mobile: restore join-command panel if it was open (never auto-ack).
    if (exp.device_jules) {
        const out = document.getElementById('mesh-key-out');
        if (out && exp.device_jules_html) {
            out.style.display = 'block';
            out.innerHTML = exp.device_jules_html;
        }
    }
}

function _setupDoneLine(s) {
    // B14: the address-done card carries the domain door — where the Cove now lives
    // (signed-in operator link, new window, no auto-redirect) + the mesh-first note.
    let door = '';
    if (s.id === 'claim_address' && s.domain) {
        // Jules 2211 / 2315: fresh sign-on door at click. Host-aware close-tab copy —
        // once already on the live domain, "close this localhost tab" is wrong.
        // Jules 1827: this is a sign-on link; HTTPS cert often lags 30–90s after mark-live
        // — ERR_SSL_PROTOCOL_ERROR looks broken until Reload works (Calhoun install).
        const _dom = (s.domain || '').toLowerCase();
        const _host = (location.hostname || '').toLowerCase();
        const _onLive = _dom && (_host === _dom || _host.endsWith('.' + _dom));
        const _closeHint = _onLive
            ? " — you're already on the live address"
            : ' — this is your sign-on link (logs you in at the new address; then you can close any leftover localhost tab)';
        door = `<div style="opacity:1;margin-top:4px;font-size:0.68rem;color:var(--text);">
            Your Cove lives at <b>https://${ESC(s.domain)}</b> —
            <a href="#" onclick="try{_openMyCove(this);}catch(e){} return false;" style="color:var(--accent);font-weight:600;">Open my Cove &#8599;</a>
            <span style="color:var(--dim);">${_closeHint}</span>
            <div style="margin-top:6px;padding:8px 10px;border:1px solid var(--accent);border-radius:6px;background:rgba(255,180,60,.08);color:var(--text);font-size:0.72rem;line-height:1.45;">
                <b>First open can take a minute.</b> HTTPS is still finishing on the host after mark-live.
                If Chrome says <code>ERR_SSL_PROTOCOL_ERROR</code> / "can't provide a secure connection", wait 30–90s and hit <b>Reload</b> — that is normal, not a broken address.
            </div>
        </div>`;
    } else if (s.id === 'add_intelligence' && typeof _presenceChatDoorHref === 'function') {
        // Install-pass: real door → new window on Chat (?tab=chat). href="#" after #126
        // was a dead link. openChatWithBrainAck seeds the ack first so the new MC
        // doesn't paint an empty personal thread.
        // Jules 1825: after Open chat, operator still needs a clear pointer back to
        // Attention for the next setup card (address / compute) — door stays; nudge is
        // in the brain-ack line (wake_thread), not buried only in Chat.
        const chref = _presenceChatDoorHref();
        const agent = (typeof MC !== 'undefined' && MC.agentName) || 'Your agent';
        if (chref) door = `<div style="opacity:1;margin-top:4px;font-size:0.68rem;color:var(--text);">
            ${ESC(agent)} is awake.
            <a href="${ESC(chref)}" target="_blank" rel="noopener" style="color:var(--accent);"
               onclick="try{if(typeof openChatWithBrainAck==='function'){openChatWithBrainAck(event);} }catch(e){}">Open chat &#8599;</a>
            <span style="color:var(--dim);"> — then go back to Attention for the next setup step</span>
        </div>`;
    }
    return `<div class="home-approval onboarding-card" style="opacity:.65;padding:6px 10px;">
        <div class="approval-tool" style="color:var(--green);">✓ ${ESC(s.title)}</div>${door}
    </div>`;
}

function _setupLockedLine(s) {
    return `<div class="home-approval onboarding-card" style="opacity:.5;padding:6px 10px;">
        <div class="approval-tool">🔒 ${ESC(s.title)}</div>
        <div class="approval-desc" style="font-size:0.68rem;">Unlocks next — ${ESC(s.unlocks || '')}</div>
    </div>`;
}

function openOnboardingHelp() {
    let m = document.getElementById('onboarding-help-modal');
    if (!m) {
        m = document.createElement('div');
        m.id = 'onboarding-help-modal';
        m.style = 'position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:9999;display:flex;align-items:center;justify-content:center;padding:16px;';
        m.onclick = (e) => { if (e.target === m) m.remove(); };
        m.innerHTML = `<div style="background:var(--bg-card,#1a1a1a);border:1px solid var(--border);border-radius:10px;max-width:460px;width:100%;padding:18px;font-size:0.8rem;line-height:1.55;color:var(--text);">
            <div style="font-weight:600;font-size:0.95rem;margin-bottom:8px;">How your Cove works</div>
            <p>A <b>Cove</b> is your private family Intelligence. Setup unlocks in order so you aren't flooded with cards before the foundation is real.</p>
            <p><b>Open first (either order; address listed first):</b></p>
            <p><b>1. Set your address.</b> Claim the real door (HTTPS, voice, Matrix). Host command + mark-live can take a few minutes — then <b>Open my Cove</b> and keep working there, not on localhost.</p>
            <p><b>2. Add intelligence.</b> Connect a model (your own key, or a local one). This switches on your <b>Agent</b> and the <b>Tools</b>, including jules. Best after the door is live so chat and Connect use the real address.</p>
            <p><b>Then:</b></p>
            <p><b>3. Set up compute.</b> Choose where heavy work runs — this box's GPU (video pipeline), cloud, a rented GPU, or CPU only. Required before the rest.</p>
            <p><b>After compute:</b> Back up your Cove, optionally initiate team tuning (cost consent — skip anytime), then Connect on mobile.</p>
            <p><b>In Chat:</b> after the address is live, use the <b>Connect</b> tab to finish Matrix (Haven / family rooms).</p>
            <p><b>Connect on mobile.</b> Join-code puts the <em>phone</em> on the private mesh; your sign-in link signs <em>you</em> into the Cove. Both are needed. Then open <b>jules</b> and capture by voice anywhere.</p>
            <p style="color:var(--dim);">From there your agent helps you build, capture, and organize, and you can add family members, each with their own handle.</p>
            <div style="text-align:right;margin-top:10px;"><button class="btn-approve" onclick="document.getElementById('onboarding-help-modal').remove()">Got it</button></div>
        </div>`;
        document.body.appendChild(m);
    }
}

function _onboardingCardHtml(item) {
    const title = ESC(item.title || ''), body = ESC(item.body || '');
    if (item.id === 'connect_computer') {
        return `<div class="home-approval onboarding-card">
            <div class="approval-tool">${title}</div>
            <div class="approval-desc" style="line-height:1.55;">${body}</div>
            <div style="margin-top:12px;padding:10px 12px;background:var(--card,#111);border:1px solid var(--border);border-radius:8px;font-size:0.75rem;line-height:1.65;">
                <div style="color:var(--text);font-weight:600;margin-bottom:6px;">On a computer</div>
                <ol style="margin:0;padding-left:1.15rem;color:var(--dim);">
                    <li>Install the <strong style="color:var(--text);">Tailscale</strong> app: <a href="https://tailscale.com/download" target="_blank" rel="noopener" style="color:var(--accent,#7c5cff);">tailscale.com/download</a></li>
                    <li>Open it, choose <em>Add account</em>, tap the small arrow, pick <em>Add Account Using Alternate Server</em>, enter <code style="background:var(--bg,#000);padding:1px 5px;border-radius:3px;">headscale.lucidcove.org</code>, then <em>Add Account</em>.</li>
                    <li>A page opens whose web address ends in <code style="background:var(--bg,#000);padding:1px 5px;border-radius:3px;">/register/CODE</code>. Copy that <strong style="color:var(--text);">CODE</strong> and paste it here:</li>
                </ol>
                <div style="margin-top:8px;display:flex;flex-wrap:wrap;gap:8px;align-items:center;">
                    <input id="approve-code-input" type="text" placeholder="paste the code from the page" style="flex:1;min-width:170px;font-size:0.78rem;padding:8px 10px;background:var(--bg-card,#0c0c12);color:var(--text);border:1px solid var(--border);border-radius:6px;">
                    <button class="btn-approve" onclick="approveDevice(this)">Approve this device</button>
                </div>
                <div id="approve-out" style="display:none;margin-top:10px;font-size:0.78rem;line-height:1.5;"></div>
            </div>
            <div style="margin-top:12px;padding-top:8px;border-top:1px dashed var(--border);">
                <button class="btn-ghost" style="font-size:0.7rem;" onclick="getConnectCmd(this)">On Linux, or prefer the terminal? Show the command</button>
                <div id="connect-cmd-out" style="display:none;margin-top:10px;font-size:0.74rem;"></div>
                <div style="margin-top:8px;"><button class="btn-ghost" style="font-size:0.68rem;" onclick="ackOnboarding('connect_computer')">I'm connected — dismiss</button></div>
            </div>
        </div>`;
    }
    if (item.id === 'set_compute') {
        // Compute establishment (#12): the operator picks WHERE heavy work runs.
        // The VIDEO pipeline gates on compute.video_asr (local/cloud/external/cpu).
        //   local    → this box's GPU (video pipeline runs here)
        //   external → rented GPU (URL + grant token)
        //   cloud    → cloud ASR (Groq/OpenAI/Deepgram key required — NOT automatic)
        //   cpu      → no GPU backend (features limited, honest)
        const gpu = item.gpu || {};
        const hasGpu = !!gpu.present;
        const localBtn = hasGpu
            ? `<button class="btn-approve" onclick="setCompute('local', this)">Use this GPU ★</button>`
            : '';
        // CF-72: the choice's price tag — what a starter month costs on cloud keys
        // vs ~$0 local, computed server-side (item.cost). Purely informational.
        const costLine = (item.cost && item.cost.summary)
            ? `<div style="margin-top:6px;font-size:0.72rem;color:var(--dim);">💡 ${ESC(item.cost.summary)}</div>`
            : '';
        // Mosswood 2204: cloud ASR must require a key — previously "Use cloud" collapsed
        // to done with empty config, falsely claiming compute was set.
        return `<div class="home-approval onboarding-card">
            <div class="approval-tool">${title}${hasGpu ? ' <span style="color:var(--green);font-size:0.7rem;">● GPU found</span>' : ' <span style="color:var(--dim);font-size:0.7rem;">○ no GPU</span>'}</div>
            <div class="approval-desc" style="line-height:1.6;">${body}</div>${costLine}
            <div id="compute-cloud" style="display:none;margin-top:8px;">
                <div style="font-size:0.72rem;color:var(--dim);margin-bottom:4px;">Cloud transcription needs a provider key. Pick one and paste your key — the Cove stores it encrypted.</div>
                <select id="cloud-provider" class="settings-input" style="width:100%;margin-bottom:6px;">
                    <option value="">Choose provider…</option>
                    <option value="groq">Groq (fast, Whisper-based)</option>
                    <option value="openai">OpenAI (Whisper)</option>
                    <option value="deepgram">Deepgram (Nova)</option>
                </select>
                <input id="cloud-key" class="settings-input" type="password" placeholder="sk-… or dg_…" style="width:100%;">
                <div style="margin-top:6px;">
                    <button class="btn-approve" onclick="saveCloudKeyThenCompute(this)">Save key & use cloud</button>
                </div>
            </div>
            <div id="compute-rent" style="display:none;margin-top:8px;">
                <div style="font-size:0.72rem;color:var(--dim);margin-bottom:4px;">Paste a GPU grant: the endpoint and the code the provider gave you (e.g. from another Cove's Rent-a-GPU card).</div>
                <input id="compute-rent-url" class="settings-input" placeholder="https://voice.their-cove.lucidcove.org" style="width:100%;">
                <input id="compute-rent-token" class="settings-input" placeholder="grant code (gpugrant_…)" style="width:100%;margin-top:6px;">
                <div style="margin-top:6px;display:flex;gap:6px;flex-wrap:wrap;">
                    <button class="btn-approve" onclick="setCompute('external', this)">Use this GPU grant</button>
                    <a class="btn-ghost" href="/static/action-board/rent-gpu.html" target="_blank" rel="noopener" style="text-decoration:none;">Browse marketplace ↗</a>
                </div>
            </div>
            <div class="approval-actions" id="compute-cta" style="flex-wrap:wrap;gap:6px;">
                ${localBtn}
                <button class="btn-approve" onclick="document.getElementById('compute-cloud').style.display='block';">Cloud transcription</button>
                <button class="btn-approve" onclick="document.getElementById('compute-rent').style.display='block';">Rent a GPU</button>
                <button class="btn-ghost" onclick="setCompute('cpu', this)">CPU only for now</button>
            </div>
            <div id="compute-status" style="display:none;margin-top:6px;font-size:0.72rem;"></div>
        </div>`;
    }
    if (item.id === 'claim_address') {
        // jules 07-07: domain saved but not live yet (self-host command pending). Show the command +
        // door directly so they survive a reload — previously the step collapsed to a command-less
        // done-line the instant the domain saved, stranding the operator.
        // Prefer server host_command; fall back to last claim response so soft-refresh
        // still shows the command if pending_host_command lagged one poll.
        const _hcCmd = (item.host_command || window._pendingHostCommand || '').trim();
        if (_hcCmd) {
            const _pd = ESC(item.domain || window._pendingHostDomain || '');
            return `<div class="home-approval onboarding-card">
                <div class="approval-tool">${title}</div>
                <div class="approval-desc">One step left, on the machine hosting your Cove. Run this, then mark it live (it may ask for your password):</div>
                <code style="display:block;margin-top:6px;padding:6px;background:var(--card);border:1px solid var(--border);border-radius:4px;word-break:break-all;">${ESC(_hcCmd)}</code>
                <div style="margin-top:10px;color:var(--text);">When the command finishes, Caddy is up for <b>https://${_pd}</b> and the host can resolve the name (mesh DNS / hosts repair if needed). The TLS certificate often needs <b>another 30–90 seconds</b> after that. Then mark live — that unlocks the signed-in door:</div>
                <div style="margin-top:12px;"><button class="btn-approve" style="padding:12px 18px;font-size:0.9rem;" onclick="_addrRanCommand(this)">I ran the command — mark live</button></div>
                <div style="color:var(--dim);font-size:0.66rem;margin-top:4px;line-height:1.5;">Run the host command first and confirm it prints <code>ok</code> (not <code>host_resolve_failed</code>). Prefer mark-live after <code>curl -vI https://${_pd}/</code> shows a real HTTPS response on that machine — if curl still SSL-errors, wait and retry. The address is a <b>mesh</b> URL (Tailscale up; not a public website). Opening too early: NXDOMAIN if DNS is filtered, or <code>ERR_SSL_PROTOCOL_ERROR</code> while the cert is still issuing — wait and Reload.</div>
            </div>`;
        }
        const sub = ESC(item.cove_subdomain || '');
        // Subdomain is the recommended default: zero DNS, zero token — one click and we
        // (the hub) create the records + issue HTTPS. Own domain is the power option.
        // CF-90b (locked): the address flow is two ORDERED steps — (1) put this box on
        // the mesh, (2) claim the address (DNS points at the mesh IP). Step 1 only shows
        // when the box isn't reachable yet (no mesh IP, no owned public IP) — a rented
        // VPS or an install.sh-preflighted box skips straight to step 2.
        return `<div class="home-approval onboarding-card">
            <div class="approval-tool">${title}</div>
            <div class="approval-desc">${body}</div>
            <div id="addr-mesh-step" style="display:none;margin-top:8px;">
                <div style="font-size:0.78rem;"><strong>Step 1 — put this box on the mesh.</strong></div>
                <div style="font-size:0.72rem;color:var(--dim);margin-top:4px;line-height:1.6;">
                    Your address points at this box's private mesh IP, so your family reaches your Cove
                    from anywhere — no ports opened, nothing exposed to the public internet. Run one
                    command on this box (in your Cove folder — the installer printed it; it's the folder
                    with <code>docker-compose.yml</code>), then claim the address.
                </div>
                <div id="addr-mesh-out" style="display:none;margin-top:8px;font-size:0.72rem;"></div>
                <div class="approval-actions" style="margin-top:6px;flex-wrap:wrap;gap:6px;">
                    <button class="btn-approve" onclick="_addrMeshKey(this)">Get the join command</button>
                    <button class="btn" onclick="_addrRecheck(this)">I ran it — check again</button>
                </div>
            </div>
            <div id="dom-form" style="display:none;margin-top:8px;">
                <select id="dom-mode" class="settings-input" data-sub="${sub}" style="max-width:340px;" onchange="_domModeChange()">
                    ${sub ? `<option value="sub">Use ${sub} (recommended — zero setup)</option>` : ''}
                    <option value="own"${sub ? '' : ' selected'}>Use my own domain</option>
                </select>
                <div id="dom-sub-row" style="margin-top:6px;color:var(--dim);font-size:0.72rem;${sub ? '' : 'display:none;'}">
                    Your Cove lives at <code>${sub}</code>; each member becomes <code>their-handle.${sub}</code>. Zero setup — we handle DNS + HTTPS for you.
                </div>
                <div id="dom-own-row" style="margin-top:6px;${sub ? 'display:none;' : ''}">
                    <input id="dom-input" class="settings-input" placeholder="coolfamily.org" style="width:100%;">
                    <input id="dom-token" class="settings-input" placeholder="Cloudflare API token (optional — auto-sets DNS + HTTPS)" style="width:100%;margin-top:6px;">
                    <div style="margin-top:6px;color:var(--dim);font-size:0.7rem;line-height:1.5;">
                        Paste a Cloudflare "Edit zone DNS" token and we set DNS + HTTPS for you automatically.
                        No token? Leave it blank — after you click, we'll show the two records to add at your registrar.
                        Each member becomes <code>their-handle.{your-domain}</code>.
                    </div>
                </div>
                <div id="dom-out" style="display:none;margin-top:8px;font-size:0.72rem;"></div>
                <div style="margin-top:6px;"><button class="btn-approve" onclick="saveDomain(this)">Set address</button></div>
            </div>
            <div class="approval-actions" id="dom-cta">
                <button class="btn-approve" onclick="_addrOpen(this)">Claim your address</button>
            </div>
        </div>`;
    }
    if (item.id === 'initiate_team_tuning') {
        // Cost-aware consent before daily team auto-tune bills a cloud key.
        // Skip is fine — Cove chat/tools/personal Tune still work.
        const est = item.estimate || {};
        const summary = est.summary
            ? ESC(est.summary)
            : 'Estimate unavailable until a model is connected.';
        const sev = est.severity || '';
        const sevColor = sev === 'high' ? 'var(--orange,#e6a23c)'
            : sev === 'medium' ? 'var(--accent)'
            : sev === 'free' || sev === 'low' ? 'var(--green)'
            : 'var(--dim)';
        const modelLine = est.display
            ? `<div style="font-size:0.72rem;color:var(--dim);margin-top:4px;">Model: <code>${ESC(est.display)}</code> · ~${est.agent_count || 10} agents · ~${Math.round(((est.tokens_in||0)+(est.tokens_out||0))/1000)}k tokens/pass</div>`
            : '';
        return `<div class="home-approval onboarding-card">
            <div class="approval-tool">${title}</div>
            <div class="approval-desc" style="line-height:1.6;">${body}</div>
            <div style="margin-top:8px;padding:8px 10px;border:1px solid var(--border);border-radius:6px;background:var(--card,#111);">
                <div style="font-size:0.78rem;color:${sevColor};line-height:1.5;">💡 ${summary}</div>
                ${modelLine}
            </div>
            <div style="margin-top:8px;font-size:0.68rem;color:var(--dim);line-height:1.5;">
                Enabling starts daily auto-tune and runs the first pass now.
                Skipping leaves auto-tune off — you can enable later from this card or Settings.
                Personal Tune (you) and agent chat are never blocked.
            </div>
            <div class="approval-actions" style="flex-wrap:wrap;gap:6px;margin-top:8px;">
                <button class="btn-approve" onclick="enableTeamTuning(this)">Enable daily team tuning</button>
                <button class="btn-ghost" onclick="ackOnboarding('initiate_team_tuning')">Skip for now</button>
            </div>
            <div id="team-tune-status" style="display:none;margin-top:6px;font-size:0.72rem;"></div>
        </div>`;
    }
    if (item.id === 'jules_intro') {

        return `<div class="home-approval onboarding-card">
            <div class="approval-tool">${title}</div>
            <div class="approval-desc">${body}</div>
            <div class="approval-actions">
                <a class="btn-approve" href="/jules" target="_blank" rel="noopener" style="text-decoration:none;">Open jules ↗</a>
                <button class="btn-ghost" onclick="ackOnboarding('jules_intro')">Got it</button>
            </div>
        </div>`;
    }
    if (item.id === 'add_intelligence') {
        return `<div class="home-approval onboarding-card">
            <div class="approval-tool">${title}</div>
            <div class="approval-desc" style="line-height:1.6;">Your whole Cove team runs on one AI model &mdash; your <strong>primary</strong>. Connect a provider key (or a local model) and every agent uses it from the start, and it sticks across restarts. Your agents' memory and identity stay either way. You can set models per-agent and turn on automatic routing later.</div>
            <div id="ai-form" style="display:none;margin-top:8px;">
                <div id="ai-local" style="margin-bottom:10px;font-size:0.78rem;color:var(--dim);">Checking this machine…</div>
                <div style="font-size:0.7rem;color:var(--dim);margin-bottom:4px;">Or set your primary from a provider key:</div>
                <select id="ai-provider" class="settings-input" style="max-width:200px;">
                    <option value="">Choose provider…</option>
                    <option value="openrouter">OpenRouter (recommended — covers Claude, GPT &amp; more)</option>
                    <option value="openai">OpenAI</option>
                    <option value="google">Google</option>
                    <option value="groq">Groq</option>
                    <option value="ollama">Ollama (local — no key)</option>
                </select>
                <input id="ai-key" class="settings-input" placeholder="API key (blank for Ollama)" style="margin-top:6px;width:100%;">
                <div style="margin-top:6px;"><button class="btn-approve" onclick="saveIntelligence()">Save</button></div>
            </div>
            <div class="approval-actions" id="ai-cta">
                <button class="btn-approve" onclick="_markSetupExpanded('add_intelligence', true);document.getElementById('ai-form').style.display='block';this.parentElement.style.display='none';loadMachineProbe();">Add intelligence</button>
            </div>
        </div>`;
    }
    if (item.id === 'device_jules' || item.id === 'join_mesh') {
        // Jules 0234 rewrite: make join-code provenance obvious + separate actions
        // from body text (0231: buttons looked "jarbled" in the middle of copy).
        // Phone-first (Tailscale app). Laptop/server command is secondary.
        return `<div class="home-approval onboarding-card">
            <div class="approval-tool">${title}</div>
            <div class="approval-desc" style="line-height:1.55;">${body}</div>
            <div style="margin-top:12px;padding:10px 12px;background:var(--card,#111);border:1px solid var(--border);border-radius:8px;font-size:0.74rem;line-height:1.6;color:var(--dim);">
                <div style="color:var(--text);font-weight:600;margin-bottom:6px;">What you'll need</div>
                <div><strong style="color:var(--text);">1. Join code</strong> — puts the <em>device</em> on your Cove's private mesh so it can reach the box. Tap <b>Get a join code</b> below (or the laptop command if you're not on a phone). That is where the code comes from.</div>
                <div style="margin-top:6px;"><strong style="color:var(--text);">2. Sign-in link</strong> — signs <em>you</em> into the Cove at your live address (identity). Use <b>Open my Cove</b> on the address step after mark-live, or Settings → Devices.</div>
            </div>
            <div style="margin-top:12px;padding:10px 12px;background:var(--card,#111);border:1px solid var(--border);border-radius:8px;font-size:0.74rem;line-height:1.65;color:var(--dim);">
                <div style="color:var(--text);font-weight:600;margin-bottom:6px;">On your phone</div>
                <ol style="margin:0;padding-left:1.15rem;">
                    <li>Install the <strong style="color:var(--text);">Tailscale</strong> app.</li>
                    <li>⋯ menu (top right) → <em>Use a custom coordination server</em> → enter
                        <code style="background:var(--bg,#000);padding:1px 5px;border-radius:3px;">https://headscale.lucidcove.org</code></li>
                    <li>Sign in with the <strong style="color:var(--text);">join code from the button below</strong> and approve the device (mesh).</li>
                    <li>Open your Cove at your live address and sign in as you (identity).</li>
                    <li>Add <strong style="color:var(--text);">jules</strong> to your home screen.</li>
                </ol>
                <div style="margin-top:8px;font-size:0.68rem;">Already on another Tailscale network? Log out of that one first — this is a separate tailnet.</div>
            </div>
            <div id="mesh-key-out" style="display:none;margin-top:12px;font-size:0.74rem;"></div>
            <div class="approval-actions" style="display:flex;flex-wrap:wrap;gap:10px;margin-top:14px;padding-top:12px;border-top:1px solid var(--border);align-items:center;">
                <button class="btn-approve" onclick="getMeshKey(this)">Get a join code</button>
                <button class="btn-ghost" onclick="getMeshKey(this)" style="font-size:0.72rem;">Laptop/server command</button>
                <a class="btn-ghost" href="/jules" target="_blank" rel="noopener" style="text-decoration:none;" onclick="_markSetupExpanded('device_jules', true);">Open jules ↗</a>
            </div>
            <div style="margin-top:12px;padding-top:10px;border-top:1px dashed var(--border);">
                <button class="btn-ghost" onclick="ackOnboarding('device_jules')" style="width:100%;max-width:280px;">Mark Connect on mobile complete</button>
                <div style="color:var(--dim);font-size:0.66rem;margin-top:6px;line-height:1.45;">Copying a join code or opening jules does <b>not</b> finish this step. Mark complete only after the phone is on the mesh and signed in.</div>
            </div>
        </div>`;
    }
    if (item.id === 'protect_backup') {
        // CF-112 — the instructions ARE the nag (Chords 2026-07-04): the exact
        // GitHub private-repo + fine-grained-PAT walkthrough renders here, with
        // the config inputs inline. No ack — the card clears itself on the
        // first green run (backup_green server-side).
        const bk = item.backup || {};
        const last = bk.last || {};
        const guide = (item.guide || []).map((g, i) =>
            `<div style="margin-top:3px;"><strong style="color:var(--text);">${i + 1}.</strong> ${ESC(g)}</div>`).join('');
        const lastLine = last.ts
            ? `<div style="margin-top:6px;color:${last.ok ? 'var(--green)' : 'var(--orange)'};">Last run: ${last.ok ? '✓' : '✗'} ${ESC(last.summary || '')}</div>`
            : '';
        // #1412 — say plainly when backup isn't configured yet, so Settings shows the state
        // (not just an empty form). Configured = a repo URL AND a saved token.
        const _bkSetUp = !!(bk.remote_url && bk.has_token);
        const _bkBadge = _bkSetUp
            ? '<span style="font-size:0.6rem;text-transform:uppercase;color:var(--green,#3fb950);border:1px solid var(--green,#3fb950);border-radius:4px;padding:1px 5px;margin-left:6px;">on</span>'
            : '<span style="font-size:0.6rem;text-transform:uppercase;color:var(--orange,#e67e22);border:1px solid var(--orange,#e67e22);border-radius:4px;padding:1px 5px;margin-left:6px;">not set up</span>';
        return `<div class="home-approval onboarding-card">
            <div class="approval-tool">${title}${_bkBadge}</div>
            <div class="approval-desc">${body}</div>
            <div style="font-size:0.72rem;color:var(--dim);margin-top:8px;line-height:1.6;">${guide}</div>
            <div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:10px;">
                <input id="backup-url" class="settings-input" placeholder="https://github.com/you/my-cove-backup" value="${ESC(bk.remote_url || '')}" style="flex:1;min-width:220px;">
                <input id="backup-token" class="settings-input" type="password" placeholder="${bk.has_token ? '******** (saved — paste to replace)' : 'github_pat_…'}" style="flex:1;min-width:180px;">
            </div>
            <div id="backup-out" style="font-size:0.72rem;margin-top:6px;color:var(--dim);min-height:1em;">${lastLine}</div>
            <div class="approval-actions" style="flex-wrap:wrap;gap:6px;">
                <button class="btn-approve" onclick="saveBackupConfig(this)">Save</button>
                <button class="btn-approve" onclick="runBackupNow(this)">Back up now</button>
                <button class="btn-ghost" onclick="ackOnboarding('${ESC(item.id)}')">Skip for now</button>
            </div>
            <div style="font-size:0.7rem;color:var(--dim);margin-top:6px;">Not now? You can set this up anytime from Settings.</div>
        </div>`;
    }
    return `<div class="home-approval onboarding-card">
        <div class="approval-tool">${title}</div>
        <div class="approval-desc">${body}</div>
        <div class="approval-actions">
            <button class="btn-approve" onclick="ackOnboarding('${ESC(item.id)}')">Got it</button>
        </div>
    </div>`;
}

// ── CF-112 backup card actions ───────────────────────────────────────────────
async function saveBackupConfig(btn) {
    const url = (document.getElementById('backup-url') || {}).value || '';
    const tok = (document.getElementById('backup-token') || {}).value || '';
    const out = document.getElementById('backup-out');
    if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }
    try {
        const r = await fetch('/api/backup/config', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            // Empty token field = keep whatever is saved (masked-echo semantics server-side).
            body: JSON.stringify({ remote_url: url, token: tok || '********' }),
        });
        const d = await r.json();
        if (out) out.innerHTML = d.error
            ? `<span style="color:var(--orange);">${ESC(d.error)}</span>`
            : `<span style="color:var(--green);">Saved${d.configured ? ' — ready to back up.' : ' — still needs ' + (d.has_token ? 'the repo URL.' : 'the token.')}</span>`;
    } catch (e) {
        if (out) out.innerHTML = `<span style="color:var(--orange);">Could not save: ${ESC(e.message)}</span>`;
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = 'Save'; }
    }
}

async function runBackupNow(btn) {
    const out = document.getElementById('backup-out');
    if (btn) { btn.disabled = true; btn.textContent = 'Backing up…'; }
    try {
        const r = await fetch('/api/backup/run', { method: 'POST' });
        const d = await r.json();
        if (d.error) {
            if (out) out.innerHTML = `<span style="color:var(--orange);">${ESC(d.error)}</span>`;
            if (btn) { btn.disabled = false; btn.textContent = 'Back up now'; }
            return;
        }
        // Poll status until the run lands (backup can take a minute on first run).
        const poll = async (tries) => {
            const s = await fetch('/api/backup/status').then(x => x.json()).catch(() => ({}));
            if (s.running && tries > 0) { setTimeout(() => poll(tries - 1), 4000); return; }
            const last = s.last || {};
            if (out) out.innerHTML = last.ts
                ? `<span style="color:${last.ok ? 'var(--green)' : 'var(--orange)'};">${last.ok ? '✓' : '✗'} ${ESC(last.summary || '')}</span>`
                : '<span style="color:var(--dim);">Started — check back in a minute.</span>';
            if (btn) { btn.disabled = false; btn.textContent = 'Back up now'; }
            if (last.ok) setTimeout(() => loadHomeApprovals(), 2500); // green run clears the card
        };
        setTimeout(() => poll(45), 4000);
    } catch (e) {
        if (out) out.innerHTML = `<span style="color:var(--orange);">${ESC(e.message)}</span>`;
        if (btn) { btn.disabled = false; btn.textContent = 'Back up now'; }
    }
}

async function approveDevice(btn) {
    // Approve THIS presence's pending Tailscale device through the Cove (headscale API) — the
    // no-terminal, no-SSH path. The person pastes the code from the app's /register page.
    const inp = document.getElementById('approve-code-input');
    const out = document.getElementById('approve-out');
    const code = ((inp && inp.value) || '').trim();
    if (!code) { if (out) { out.style.display = 'block'; out.style.color = 'var(--orange)'; out.textContent = 'Paste the code from the Tailscale page first.'; } return; }
    const _label = btn ? btn.textContent : '';
    if (btn) { btn.disabled = true; btn.textContent = 'Approving…'; }
    try {
        const r = await fetch('/api/onboarding/approve-device', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key: code }) });
        const d = await r.json();
        if (out) {
            out.style.display = 'block';
            if (d.ok) {
                out.style.color = 'var(--green)';
                out.innerHTML = '✓ Approved. This device is joining your Cove now — it should connect within a few seconds. Then open your Cove and you are in.';
            } else {
                out.style.color = 'var(--orange)';
                out.textContent = d.reason || 'Could not approve that device. Check the code and try again.';
            }
        }
    } catch (e) {
        if (out) { out.style.display = 'block'; out.style.color = 'var(--orange)'; out.textContent = 'Could not reach the approval service.'; }
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = _label || 'Approve this device'; }
    }
}

async function getConnectCmd(btn) {
    // Terminal / Linux option: fetch a FRESH mesh join command (the auth key self-registers,
    // no approval). /api/onboarding/mesh-key already carries --accept-dns=true.
    const out = document.getElementById('connect-cmd-out');
    const _label = btn ? btn.textContent : '';
    if (btn) { btn.disabled = true; btn.textContent = '…'; }
    try {
        const r = await fetch('/api/onboarding/mesh-key');
        const d = await r.json();
        if (out) {
            out.style.display = 'block';
            const cmd = d.join_cmd || '';
            if (d.ok && cmd) {
                out.innerHTML = '<div style="color:var(--dim);line-height:1.6;margin-bottom:6px;">On <strong style="color:var(--text);">Linux</strong>, install then join:</div>'
                    + '<code style="display:block;padding:8px;background:var(--card);border:1px solid var(--border);border-radius:6px;word-break:break-all;font-size:0.72rem;color:var(--text);">curl -fsSL https://tailscale.com/install.sh | sh</code>'
                    + '<button class="btn" style="margin-top:4px;font-size:0.68rem;padding:3px 9px;" onclick="_copyPrev(this)">Copy</button>'
                    + '<code style="display:block;margin-top:8px;padding:8px;background:var(--card);border:1px solid var(--border);border-radius:6px;word-break:break-all;font-size:0.72rem;color:var(--text);">sudo ' + ESC(cmd) + '</code>'
                    + '<button class="btn" style="margin-top:4px;font-size:0.68rem;padding:3px 9px;" onclick="_copyPrev(this)">Copy</button>'
                    + '<div style="color:var(--dim);margin-top:8px;font-size:0.68rem;line-height:1.5;">A fresh command each time, so it never expires. On Mac or Windows the app plus Approve above is simpler.</div>';
            } else {
                out.innerHTML = '<div style="color:var(--orange);">' + ESC(d.reason || 'Could not get a command here.') + '</div>';
            }
        }
    } catch (e) {
        if (out) { out.style.display = 'block'; out.textContent = 'Could not reach the mesh service.'; }
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = _label || 'Show the command'; }
    }
}

function _copyPrev(btn) {
    const el = btn && btn.previousElementSibling;
    const text = el ? el.textContent : '';
    if (!text) return;
    const done = () => { const t = btn.textContent; btn.textContent = '✓ Copied'; setTimeout(() => { btn.textContent = t; }, 1500); };
    if (navigator.clipboard && navigator.clipboard.writeText) { navigator.clipboard.writeText(text).then(done).catch(() => {}); }
    else { try { const ta = document.createElement('textarea'); ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0'; document.body.appendChild(ta); ta.select(); document.execCommand('copy'); document.body.removeChild(ta); done(); } catch (e) {} }
}

async function getMeshKey(btn) {
    // Jules 0113: this only reveals a join command. It must NEVER ack device_jules
    // (that false-complete collapsed mobile mid-setup and showed a green check).
    // Jules 0234: surface the bare join CODE for the phone Tailscale app first;
    // laptop command is secondary so "where does the code come from?" is obvious.
    const out = document.getElementById('mesh-key-out');
    const _label = btn ? btn.textContent : '';
    if (btn) { btn.disabled = true; btn.textContent = '…'; }
    _markSetupExpanded('device_jules', true);
    try {
        const r = await fetch('/api/onboarding/mesh-key');
        const d = await r.json();
        if (out) {
            out.style.display = 'block';
            if (d.ok && (d.key || d.join_cmd)) {
                const code = d.key || '';
                const cmd = d.join_cmd || '';
                let html = '<div style="color:var(--text);font-weight:600;margin-bottom:6px;">Your join code (from this button — valid ~1h)</div>';
                if (code) {
                    html += '<div style="color:var(--dim);font-size:0.7rem;margin-bottom:4px;">Paste this into the Tailscale app on your phone when it asks to sign in / use an auth key:</div>'
                        + '<code style="display:block;padding:10px;background:var(--card);border:1px solid var(--border);border-radius:6px;word-break:break-all;font-size:0.85rem;color:var(--text);">'
                        + ESC(code) + '</code>';
                }
                if (cmd) {
                    html += '<div style="color:var(--dim);font-size:0.7rem;margin-top:10px;margin-bottom:4px;">On a laptop or server instead, run this in a terminal:</div>'
                        + '<code style="display:block;padding:8px;background:var(--card);border:1px solid var(--border);border-radius:6px;word-break:break-all;font-size:0.72rem;">'
                        + ESC(cmd) + '</code>';
                }
                out.innerHTML = html;
            } else {
                out.innerHTML = '<div style="color:var(--orange);">' + ESC(d.reason || 'Could not mint a join code here.') + '</div>'
                    + (d.instructions ? '<div style="color:var(--dim);margin-top:4px;font-size:0.95em;">' + ESC(d.instructions) + '</div>' : '');
            }
            try { window._setupExpanded.device_jules_html = out.innerHTML; } catch (e) {}
        }
    } catch (e) {
        if (out) { out.style.display = 'block'; out.textContent = 'Could not reach the mesh service.'; }
    }
    if (btn) { btn.disabled = false; btn.textContent = _label || 'Get a join code'; }
}

function _domModeChange() {
    const mode = (document.getElementById('dom-mode') || {}).value;
    const subRow = document.getElementById('dom-sub-row');
    const ownRow = document.getElementById('dom-own-row');
    if (subRow) subRow.style.display = (mode === 'own') ? 'none' : '';
    if (ownRow) ownRow.style.display = (mode === 'own') ? '' : 'none';
}

// ── CF-90b: Set Address = two ordered steps, MESH FIRST then DNS ──────────────
async function _addrOpen(btn) {
    if (btn) btn.disabled = true;
    let reach = null;
    try {
        const d = await (await fetch('/api/domain/status')).json();
        reach = d && d.reachable;
    } catch (e) { /* status unreachable — fall through to the mesh step */ }
    if (btn && btn.parentElement) btn.parentElement.style.display = 'none';
    if (reach && reach.ok) {
        _markSetupExpanded('claim_address', true);
        _addrShowClaim(reach);
    } else {
        _markSetupExpanded('claim_address', 'mesh');
        const step = document.getElementById('addr-mesh-step');
        if (step) step.style.display = 'block';
    }
}

async function _addrRanCommand(btn) {
    // jules 07-07 / reinstall 2230: operator attests they ran the host command → mark
    // the address live (in-container we can't detect the host command ran).
    // Jules 0229: stay on the Presences board after mark-live so "Open my Cove"
    // and Setup Compute appear in place — do NOT full-reload into Chat.
    // Confirm first — a plain "refresh" click used to mark live without running the
    // command and collapsed the card (Jules: never ran command, step still cleared).
    const ok = confirm(
        'Only continue after the host command finished successfully '
        + '(printed ok / host_resolve ok).\n\n'
        + 'HTTPS may still take 30–90s after that — Open my Cove can show '
        + 'ERR_SSL_PROTOCOL_ERROR until the cert is ready; wait and Reload.\n\n'
        + 'Mark the address live and refresh setup?'
    );
    if (!ok) return;
    if (btn) { btn.disabled = true; btn.textContent = '…'; }
    try { await fetch('/api/onboarding/address-live', { method: 'POST' }); } catch (e) {}
    try { _markSetupExpanded('claim_address', false); } catch (e) {}
    // Soft refresh: stay on current surface (home / Cove-admin Presences).
    try {
        if (typeof MC !== 'undefined' && MC.coveAdminView && typeof loadCoveAdminPresences === 'function') {
            await loadCoveAdminPresences();
        } else if (typeof loadHomeApprovals === 'function') {
            await loadHomeApprovals();
        } else {
            location.reload();
        }
    } catch (e) {
        location.reload();
    }
}

async function _openMyCove(btn) {
    // jules 07-07 / reinstall 2306: mint a FRESH sign-in door at CLICK time so "Open my Cove"
    // always crosses over logged in. Server refuses until the address is live (host command
    // done + mark-live) — opening early was NXDOMAIN / dead tab (Jules screenshot 7:03).
    //
    // Woods install-pass: two crash modes after gates/ack already worked:
    //  1) window.open AFTER await is popup-blocked → nothing opens (looks dead).
    //  2) a non-/p/ door (or bare token path) never hits signin_link_auth → raw error / blank.
    // Open the tab synchronously on the click, then navigate it once the door is minted.
    // If we're ALREADY on the live domain with a session, just reload — no door needed.
    const _t = btn ? btn.textContent : '';
    if (btn) { btn.style.pointerEvents = 'none'; btn.textContent = 'Opening…'; }

    // Already signed in on the claimed host? Stay put — minting a new door only risks
    // rotating tokens and opening a second tab that looks "broken" if blocked.
    try {
        const _dom = ((typeof MC !== 'undefined' && MC.config && MC.config.domain) || '').toLowerCase();
        const _host = (location.hostname || '').toLowerCase();
        if (_dom && (_host === _dom || _host.endsWith('.' + _dom))) {
            if (btn) { btn.style.pointerEvents = ''; btn.textContent = _t || 'Open my Cove ↗'; }
            location.assign(location.protocol + '//' + _dom + '/');
            return;
        }
    } catch (e) { /* fall through to mint path */ }

    // Synchronous open keeps this in the user-gesture window (popup blockers).
    let w = null;
    try { w = window.open('about:blank', '_blank'); } catch (e) { w = null; }

    let url = '';
    let err = '';
    try {
        const r = await fetch('/api/onboarding/cove-door', { method: 'POST', credentials: 'same-origin' });
        const d = await r.json().catch(() => ({}));
        url = (d && d.door) || '';
        if (!url) err = (d && d.error) || ('HTTP ' + r.status);
    } catch (e) {
        err = (e && e.message) || 'network error';
    }

    // Door shape guard: magic-link auth is ONLY /p/{token}. Bare /{token} never signs in
    // (Roos 7:03 bare path) and looks like a crash.
    if (url) {
        try {
            const u = new URL(url, location.href);
            if (!u.pathname.startsWith('/p/')) {
                err = 'Sign-in door was malformed (missing /p/). Try again, or open https://' +
                    (u.hostname || 'your-cove') + ' and sign in from Settings → Devices.';
                url = '';
            }
        } catch (e) {
            err = 'Sign-in door was malformed. Try again from this page.';
            url = '';
        }
    }

    if (btn) { btn.style.pointerEvents = ''; btn.textContent = _t || 'Open my Cove ↗'; }

    if (url) {
        if (w && !w.closed) {
            try { w.opener = null; } catch (e) {}
            w.location = url;
        } else {
            // Popup blocked — same-tab fallback so the operator still crosses over.
            location.assign(url);
        }
        return;
    }
    if (w && !w.closed) { try { w.close(); } catch (e) {} }
    alert(err || 'Your Cove address isn\'t live yet — run the host command, then click "I ran the command — mark live".');
}

function _addrShowClaim(reach) {
    const step = document.getElementById('addr-mesh-step');
    if (step) step.style.display = 'none';
    const form = document.getElementById('dom-form');
    if (form) {
        form.style.display = 'block';
        if (reach && reach.ip && !document.getElementById('addr-mesh-ok')) {
            const ok = document.createElement('div');
            ok.id = 'addr-mesh-ok';
            ok.style.cssText = 'margin-bottom:6px;font-size:0.72rem;color:var(--green);';
            ok.textContent = (reach.source === 'mesh')
                ? `✓ This box is on the mesh (${reach.ip}) — your address will point there.`
                : `✓ This box is reachable at ${reach.ip} — your address will point there.`;
            form.insertBefore(ok, form.firstChild);
        }
    }
}

async function _addrMeshKey(btn) {
    const out = document.getElementById('addr-mesh-out');
    if (btn) { btn.disabled = true; btn.textContent = '…'; }
    try {
        const r = await fetch('/api/onboarding/mesh-key');
        const d = await r.json();
        if (out) {
            out.style.display = 'block';
            const key = d.key || (((d.join_cmd || '').match(/--authkey\s+(\S+)/) || [])[1] || '');
            if (d.ok && key) {
                // Run-2 4.1/4.2: full absolute path when the box knows it (no folder
                // digging), plus a Copy button on the one-liner.
                const dir = (d.cove_dir || '').trim();
                const cmd = 'bash ' + (dir ? dir.replace(/\/$/, '') + '/' : '') + 'connect-mesh.sh ' + key;
                const intro = dir
                    ? 'On this box, run (key valid ~1h):'
                    : 'In your Cove folder on this box (the one with docker-compose.yml), run (key valid ~1h):';
                out.innerHTML = '<div style="color:var(--dim);margin-bottom:4px;">' + intro + '</div>'
                    + '<code id="mesh-join-cmd" style="display:block;padding:6px;background:var(--card);border:1px solid var(--border);border-radius:4px;word-break:break-all;">'
                    + ESC(cmd) + '</code>'
                    + '<button class="btn" style="margin-top:6px;" onclick="_copyMeshCmd(this)">Copy command</button>'
                    + '<div style="color:var(--dim);margin-top:4px;line-height:1.5;">It joins the mesh, saves the mesh IP, restarts your Cove, and re-points your address if one is set. '
                    + 'If Tailscale isn’t installed, the script shows the install command first — run that, then re-run it. Then click “I ran it — check again”.</div>';
            } else {
                out.innerHTML = '<div style="color:var(--orange);">' + ESC(d.reason || 'Could not mint a join key here.') + '</div>'
                    + (d.instructions ? '<div style="color:var(--dim);margin-top:4px;">' + ESC(d.instructions) + '</div>' : '');
            }
        }
    } catch (e) {
        if (out) { out.style.display = 'block'; out.textContent = 'Could not reach the mesh service.'; }
    }
    if (btn) { btn.disabled = false; btn.textContent = 'Get the join command'; }
}

function _copyMeshCmd(btn) {
    const el = document.getElementById('mesh-join-cmd');
    const text = el ? el.textContent : '';
    if (!text) return;
    const done = () => { if (btn) { const t = btn.textContent; btn.textContent = '✓ Copied'; setTimeout(() => { btn.textContent = t; }, 1600); } };
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done).catch(() => _copyMeshFallback(text, done));
    } else {
        _copyMeshFallback(text, done);
    }
}

function _copyMeshFallback(text, done) {
    // Non-secure contexts (plain http before HTTPS is claimed) have no
    // navigator.clipboard — textarea/execCommand still works there.
    try {
        const ta = document.createElement('textarea');
        ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
        document.body.appendChild(ta); ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
        done();
    } catch (e) { /* leave the text selectable */ }
}

async function _addrRecheck(btn) {
    if (btn) { btn.disabled = true; btn.textContent = 'Checking…'; }
    let reach = null;
    try {
        const d = await (await fetch('/api/domain/status')).json();
        reach = d && d.reachable;
    } catch (e) {}
    if (btn) { btn.disabled = false; btn.textContent = 'I ran it — check again'; }
    if (reach && reach.ok) {
        _addrShowClaim(reach);
    } else {
        const out = document.getElementById('addr-mesh-out');
        if (out) {
            out.style.display = 'block';
            out.innerHTML = '<div style="color:var(--orange);">Not on the mesh yet. The join command restarts your Cove — give it ~30s after it finishes, then check again.</div>';
        }
    }
}

async function saveDomain(btn, confirmChange) {
    const sel = document.getElementById('dom-mode') || {};
    const mode = sel.value;
    let domain, ownToken = '';
    if (mode === 'own') {
        const v = ((document.getElementById('dom-input') || {}).value || '').trim().toLowerCase()
            .replace(/^https?:\/\//, '').split('/')[0];
        if (!v) { alert('Enter your domain.'); return; }
        domain = v;
        ownToken = ((document.getElementById('dom-token') || {}).value || '').trim();
    } else {
        // Locked to the Cove's own subdomain (e.g. clearfield.lucidcove.org), from the server.
        domain = (sel.getAttribute && sel.getAttribute('data-sub') || '').trim();
        if (!domain) { alert('No Cove subdomain available — use your own domain.'); return; }
    }
    const out = document.getElementById('dom-out');
    if (out) { out.style.display = 'block'; out.innerHTML = '<div style="color:var(--dim);">Setting your address — DNS + HTTPS…</div>'; }
    if (btn) { btn.disabled = true; btn.textContent = '…'; }
    try {
        const r = await fetch('/api/domain/set', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ domain, own_dns_token: ownToken, confirm: !!confirmChange }),
        });
        const d = await r.json();
        // Changing an address that's already live: the server asks for explicit confirmation
        // before it repoints anything. Re-send with confirm only if the operator agrees.
        if (d && d.code === 'confirm_change') {
            if (out) out.style.display = 'none';
            if (btn) { btn.disabled = false; btn.textContent = 'Set address'; }
            // C2: include the Connect/Matrix caveat in the confirmation so the operator knows
            // chat identity moves only on a virgin homeserver (else conversations stay put).
            const _msg = (d.message || `Replace ${d.current_domain} with ${domain}?`)
                + (d.matrix_note ? '\n\n' + d.matrix_note : '');
            if (confirm(_msg)) {
                return saveDomain(btn, true);
            }
            return;
        }
        // CF-90b: the server refused because the box isn't reachable yet (home/NAT,
        // no mesh) — walk back to step 1 instead of showing dead DNS records.
        if (d && d.code === 'mesh_required') {
            if (out) out.style.display = 'none';
            if (btn) { btn.disabled = false; btn.textContent = 'Set address'; }
            const form = document.getElementById('dom-form');
            if (form) form.style.display = 'none';
            const step = document.getElementById('addr-mesh-step');
            if (step) step.style.display = 'block';
            const mo = document.getElementById('addr-mesh-out');
            if (mo) { mo.style.display = 'block'; mo.innerHTML = '<div style="color:var(--orange);">' + ESC(d.message || 'Put this box on the mesh first, then claim the address.') + '</div>'
                + '<div style="color:var(--dim);font-size:0.7rem;margin-top:6px;">Stuck? Ask your agent in chat and paste what you saw. They can walk you through it.</div>'; }
            return;
        }
        if (!r.ok || d.error) { alert(d.error || 'Could not set that address.'); if (out) out.style.display = 'none'; return; }
        if (out) {
            const recs = d.records || [];
            const steps = (d.next_steps || []).map(s => `<div style="margin-top:4px;">• ${ESC(s)}</div>`).join('');
            let html = '';
            if (d.fully_live) {
                // Persist the confirmation — the operator reads it, then reloads on THEIR
                // click. (A silent auto-reload used to flash this away in ~2s, so it looked
                // like a DNS panel that vanished before you could act on it.)
                // B14: the domain door — where your Cove now lives (signed-in operator link).
                // No auto-redirect; the cert may still be issuing. Other devices need the mesh first.
                // Jules 1827: make the cert lag + sign-on nature of the door prominent.
                const _door = d.door || ('https://' + d.domain);
                html = `<div style="color:var(--green);">&#10003; Address set to https://${ESC(d.domain)}. We created the DNS + certificate path for you.</div>`
                    + `<div style="margin-top:10px;color:var(--text);">Your Cove lives at <b>https://${ESC(d.domain)}</b> on the <b>private mesh</b> (not the open internet). <b>Open my Cove</b> is your sign-on link — use it once HTTPS is up on this device:</div>`
                    + `<div style="margin-top:8px;padding:8px 10px;border:1px solid var(--accent);border-radius:6px;background:rgba(255,180,60,.08);color:var(--text);font-size:0.72rem;line-height:1.45;"><b>First open can take a minute.</b> If you see <code>ERR_SSL_PROTOCOL_ERROR</code> / "can't provide a secure connection", wait 30–90s and hit <b>Reload</b> — the cert is still issuing. That is expected, not a dead address.</div>`
                    + `<a class="btn-approve" style="text-decoration:none;display:inline-block;margin-top:8px;" href="#" onclick="_openMyCove(this); return false;">Open my Cove &#8599;</a>`
                    + `<div style="color:var(--dim);font-size:0.66rem;margin-top:6px;line-height:1.5;">If the browser says the site can't be found: confirm Tailscale is connected, wait ~1 min for DNS, disable DNS rebinding filters (NextDNS/AdGuard/Private Relay) for this network, then retry. Other devices need the mesh first (Settings &rarr; Connect a device / MESH.md).</div>`
                    + `<button class="btn-approve" style="margin-top:10px;" onclick="location.reload()">Done &mdash; refresh</button>`;
            } else if (recs.length) {
                // Own domain, no token: hand back the exact records to paste — copy-paste, no files.
                const rows = recs.map(x => `<code style="display:block;padding:6px;margin-top:4px;background:var(--card);border:1px solid var(--border);border-radius:4px;word-break:break-all;">${ESC(x.type || 'A')} &nbsp; ${ESC(x.name || '')} &nbsp;→&nbsp; ${ESC(x.content || '')}</code>`).join('');
                // C1: guided-manual verify — a "Check my records" button resolves each record
                // (no registrar API) so the operator knows when their DNS has propagated.
                html = `<div style="color:var(--accent);">Saved ${ESC(d.domain)}. Add these records at your registrar:</div>${rows}${steps}`
                    + `<div style="margin-top:8px;"><button class="btn-ghost" onclick="checkMyRecords(this)">Check my records</button></div>`
                    + `<div id="rec-check-out" style="display:none;margin-top:8px;font-size:0.72rem;"></div>`;
            } else {
                // Jules 2315 + Mosswood 2202: self-host still owes the host command.
                // Soft-refresh the checklist AND paint host_command from THIS response
                // immediately — pending_host_command can lag a tick, and re-rendering
                // the claim form without the command looked like "nothing changed"
                // (install pass after #151/#152).
                _markSetupExpanded('claim_address', true);
                const _hc = (d.host_command || '').trim();
                if (_hc) {
                    try {
                        window._pendingHostCommand = _hc;
                        window._pendingHostDomain = d.domain || domain || '';
                    } catch (e) {}
                }
                if (_hc && out) {
                    const _pd = ESC(d.domain || domain || '');
                    out.style.display = 'block';
                    out.innerHTML = '<div style="color:var(--green);">&#10003; Address saved. One step left on the host:</div>'
                        + '<div style="margin-top:8px;color:var(--text);">Run this on the machine hosting your Cove, then mark live (it may ask for your password):</div>'
                        + '<code style="display:block;margin-top:6px;padding:6px;background:var(--card);border:1px solid var(--border);border-radius:4px;word-break:break-all;">'
                        + ESC(_hc) + '</code>'
                        + '<div style="margin-top:12px;"><button class="btn-approve" style="padding:12px 18px;font-size:0.9rem;" onclick="_addrRanCommand(this)">I ran the command — mark live</button></div>'
                        + '<div style="color:var(--dim);font-size:0.66rem;margin-top:4px;line-height:1.5;">Prefer mark-live after the command prints ok. HTTPS cert may need 30–90s more for <b>https://'
                        + _pd + '</b>.</div>';
                } else if (out) {
                    out.style.display = 'none';
                }
                await loadHomeApprovals();
                return;
            }
            out.innerHTML = html;
        }
        // fully_live shows a persistent "Done — refresh" button (the operator reloads when
        // ready). Records path just refreshes the approvals list so the step stays put while
        // they copy the DNS records. Host-command path already returned via loadHomeApprovals.
        const hasRecords = (d.records && d.records.length);
        if (hasRecords) { setTimeout(() => loadHomeApprovals(), 1500); }
    } catch (e) {
        alert('Could not set address: ' + e.message);
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = 'Set address'; }
    }
}

// C1: per-record "Check my records" — resolve each own-domain A record (server-side, no
// registrar API) and show ✓ / ✗ per record so the operator knows when DNS has propagated.
async function checkMyRecords(btn) {
    const out = document.getElementById('rec-check-out');
    const _label = btn ? btn.textContent : '';
    if (btn) { btn.disabled = true; btn.textContent = 'Checking…'; }
    if (out) { out.style.display = 'block'; out.innerHTML = '<div style="color:var(--dim);">Looking up your records…</div>'; }
    try {
        const d = await (await fetch('/api/domain/check-records', {
            method: 'POST', credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}),
        })).json();
        if (!d.ok) { if (out) out.innerHTML = `<div style="color:var(--orange);">${ESC(d.reason || 'Could not check right now.')}</div>`; return; }
        const rows = (d.records || []).map(r => {
            const mark = r.ok ? '<span style="color:var(--green);">✓</span>' : '<span style="color:var(--orange);">…</span>';
            const detail = r.ok
                ? `points at ${ESC(r.resolved)}`
                : (r.resolved ? `resolves to ${ESC(r.resolved)} (expected ${ESC(r.expected)})` : 'not resolving yet');
            return `<div style="margin-top:3px;">${mark} <code>${ESC(r.name)}</code> — ${detail}</div>`;
        }).join('');
        const color = d.all_ok ? 'var(--green)' : 'var(--dim)';
        if (out) out.innerHTML = rows + `<div style="margin-top:6px;color:${color};">${ESC(d.message || '')}</div>`;
    } catch (e) {
        if (out) out.innerHTML = `<div style="color:var(--orange);">Could not check records: ${ESC(e.message)}</div>`;
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = _label || 'Check my records'; }
    }
}

async function saveIntelligence() {
    const provider = (document.getElementById('ai-provider') || {}).value || '';
    const api_key = (document.getElementById('ai-key') || {}).value || '';
    if (!provider) { alert('Choose a provider.'); return; }
    if (provider !== 'ollama' && !api_key) { alert('Enter your API key (or pick Ollama for local).'); return; }
    try {
        const r = await fetch('/api/settings/model-key', {
            method: 'POST', credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ provider, api_key }),
        });
        const d = await r.json();
        if (!r.ok || d.error || d.ok === false) { alert(d.error || 'Could not save that right now.'); return; }

        // Brain is connected. Fire brain-acknowledge with cookies, then open chat
        // once the ack has landed (or timed out) so the first message is visible.
        // Install-pass: fire-and-forget + immediate switchToTab loaded chat BEFORE
        // the write finished → empty thread; co-admin saw "is awake" with no message.
        _kickBrainAcknowledgeThenOpen();
    } catch (e) { alert('Could not save: ' + e.message); }
}

// Probe THIS machine for local model servers + their installed models, so the operator picks
// a model that's actually on the box (never a hardcoded guess). The recommended one — sized
// to the detected GPU — is flagged. Cloud BYOK stays available below.
async function loadMachineProbe() {
    const out = document.getElementById('ai-local');
    if (!out) return;
    try {
        const d = await fetch('/api/system/machine-probe').then(r => r.json());
        const provs = (d.providers || []).filter(p => p.reachable && (p.models || []).length);
        const rec = d.recommendation || {};
        // JF7 — surface actionable hints from providers the app detected but couldn't reach
        // (common case: Ollama is running but bound to 127.0.0.1, so the container is
        // refused). The backend computes the exact fix — show it, don't filter it away.
        const hints = (d.providers || []).filter(p => p.hint).map(p => p.hint);
        if (!provs.length) {
            let _msg = `<div>${ESC(rec.reason || 'No local models found on this machine.')}</div>`;
            hints.forEach(h => { _msg += `<div style="color:var(--accent);font-size:0.7rem;margin-top:6px;line-height:1.45;">${ESC(h)}</div>`; });
            out.innerHTML = _msg;
            return;
        }
        let html = '<div style="color:var(--text);margin-bottom:4px;">Found on this machine:</div>';
        provs.forEach(p => (p.models || []).filter(m => m.chat !== false).forEach(m => {
            const isRec = (rec.provider === p.id && rec.model === m.name);
            html += `<div style="display:flex;align-items:center;gap:8px;margin:3px 0;flex-wrap:wrap;">
                <button class="btn-approve" style="font-size:0.72rem;" onclick="saveIntelligenceLocal('${ESC(p.id)}','${ESC(m.name)}',this)">Use</button>
                <span style="color:var(--text);">${ESC(m.name)}</span>
                <span style="color:var(--dim);font-size:0.66rem;">${ESC(p.name)}</span>
                ${isRec ? '<span style="color:var(--accent);font-size:0.66rem;">★ recommended</span>' : ''}
            </div>`;
        }));
        if (rec.reason) html += `<div style="color:var(--dim);font-size:0.66rem;margin-top:4px;">${ESC(rec.reason)}</div>`;
        out.innerHTML = html;
    } catch (e) {
        out.innerHTML = '<div>Could not check this machine.</div>';
    }
}

// Connect a specific local model picked from the probe (no key needed). Same payoff as the
// cloud path: the agent acknowledges live with the just-connected model, then we go to chat.
async function saveIntelligenceLocal(provider, model, btn) {
    // jules 1340: the button must SHOW it's working — no feedback invited double-clicks.
    const _label = btn ? btn.textContent : '';
    if (btn) { btn.disabled = true; btn.textContent = 'Connecting…'; }
    const _restore = () => { if (btn) { btn.disabled = false; btn.textContent = _label || 'Use'; } };
    try {
        const r = await fetch('/api/settings/model-key', {
            method: 'POST', credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ provider, model, api_key: '' }),
        });
        const d = await r.json();
        if (!r.ok || d.error || d.ok === false) { _restore(); alert(d.error || 'Could not save that right now.'); return; }
        // Key save already succeeded. Wait for brain-ack (capped) so chat isn't empty
        // when we open it — then mark connected. Server fallback keeps non-silent.
        if (btn) { btn.textContent = 'Waking…'; }
        await _kickBrainAcknowledgeThenOpen();
        if (btn) { btn.textContent = '✓ Connected'; }
    } catch (e) { _restore(); alert('Could not save: ' + e.message); }
}

// Open chat door: seed brain-ack (idempotent server-side), then open a new window
// on the presence chat (?tab=chat). Caps wait so a slow model never freezes the
// door; server fallback still writes on timeout paths.
async function openChatWithBrainAck(ev) {
    if (ev && typeof ev.preventDefault === 'function') ev.preventDefault();
    const href = (typeof _presenceChatDoorHref === 'function')
        ? _presenceChatDoorHref()
        : (location.origin + '/?tab=chat');
    // Install-pass: NEVER abort brain-acknowledge. AbortController cancel was
    // killing the server write mid-flight, so Open chat landed on an empty
    // thread (Jules reinstall — no ack). Race a soft timeout for UX only; leave
    // the fetch running so the canned/live ack still lands.
    const ack = fetch('/api/presence/brain-acknowledge', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
        keepalive: true,
    }).catch(() => null);
    const wait = new Promise((resolve) => setTimeout(resolve, 12000));
    try { await Promise.race([ack, wait]); } catch (e) { /* open anyway */ }
    try {
        window.open(href, '_blank', 'noopener');
    } catch (e) {
        // Popup blocked — fall through to same-tab navigation.
        try { location.href = href; } catch (e2) {}
    }
    return false;
}

// Install-pass: race fix for "Jude is awake / open chat" with an empty thread.
// Awaits brain-acknowledge (with timeout) so the first message is in the
// checkpointer before loadChat runs. credentials:same-origin so multi-presence
// cookies ride along. On timeout we still open chat + poll once so a late write
// can appear without freezing the UI forever.
async function _kickBrainAcknowledgeThenOpen() {
    // Same rule as openChatWithBrainAck: never abort the server write. Soft-wait
    // so connect UX isn't frozen forever; keepalive keeps the request alive.
    const ack = fetch('/api/presence/brain-acknowledge', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
        keepalive: true,
    }).catch(() => null);
    const wait = new Promise((resolve) => setTimeout(resolve, 20000));
    try { await Promise.race([ack, wait]); } catch (e) { /* continue */ }
    _afterIntelligenceConnected();
    // If the operator already landed on chat before the write finished (slow model),
    // reload once more shortly after so the message still shows up.
    setTimeout(() => {
        try {
            if (typeof activeTab !== 'undefined' && activeTab === 'chat' && typeof loadChat === 'function') {
                loadChat();
            }
        } catch (e) {}
    }, 1500);
    setTimeout(() => {
        try {
            if (typeof activeTab !== 'undefined' && activeTab === 'chat' && typeof loadChat === 'function') {
                loadChat();
            }
        } catch (e) {}
    }, 5000);
}

// B2 reachable-host door (mirrors presence-profile.js): only jump to the
// {handle}.{domain} subdomain when we're ALREADY on the cove domain (proves DNS resolves);
// otherwise stay on this origin with ?as= so the link is never a dead subdomain.
function _presenceChatDoorHref() {
    const dom = (typeof MC !== 'undefined' && MC.config && MC.config.domain) || '';
    const handle = (typeof MC !== 'undefined' && MC.presence && MC.presence.username) || '';
    const onDomain = dom && location.host.endsWith(dom);
    if (handle && onDomain) return location.protocol + '//' + handle + '.' + dom + '/?tab=chat';
    if (handle) return location.origin + '/?as=' + encodeURIComponent(handle) + '&tab=chat';
    return location.origin + '/?tab=chat';
}

// B2: the brain-connect moment on a CHAT-LESS surface (the Presences landing / Cove-admin
// apex has no chat tab). Turn the card that held the Use button into "✓ {Agent} is awake —
// it left you a message → Open chat", where Open chat opens the presence's OWN Mission
// Control in a NEW window (reachable-host door). The chat-unread dot (markChatUnread) rides
// along on this surface until chat is actually opened (switchToTab('chat') clears it).
function _renderBrainAwakeCard() {
    const anchor = document.getElementById('ai-form') || document.getElementById('ai-local');
    const card = anchor ? anchor.closest('.approval-card, .onboarding-card') : null;
    const agent = (typeof MC !== 'undefined' && MC.agentName) || 'Your agent';
    const href = _presenceChatDoorHref();
    const html = `<div class="approval-card brain-awake">
        <div class="approval-title">✓ ${ESC(agent)} is awake</div>
        <div class="approval-desc">It just thought for the first time and left you a message.</div>
        <a class="btn-approve" style="text-decoration:none;display:inline-block;margin-top:6px;"
           href="${ESC(href)}" target="_blank" rel="noopener"
           onclick="try{if(typeof openChatWithBrainAck==='function'){openChatWithBrainAck(event);} }catch(e){}">Open chat &#8599;</a>
    </div>`;
    if (card) { card.outerHTML = html; return true; }
    return false;
}

// After intelligence connects: drop into chat where the agent's acknowledgment is
// waiting IF a chat tab exists on this surface; otherwise (e.g. adding it from the
// Cove-admin apex, which has no chat) show the "awake — Open chat" door card (B2) and
// refresh the surface so the step clears.
function _afterIntelligenceConnected() {
    // Install-pass product rule: intelligence connect → Chat with the ack that
    // continues the wake and points at remaining setup (address if still open;
    // Connect when the door is live; then compute). Soft-refresh Attention so
    // the intelligence card clears without a full page reload.
    try {
        if (typeof loadHomeApprovals === 'function') loadHomeApprovals();
        if (typeof MC !== 'undefined' && MC.coveAdminView && typeof loadCoveAdminPresences === 'function') {
            loadCoveAdminPresences();
        }
    } catch (e) {}
    if (typeof markChatUnread === 'function') markChatUnread();
    if (typeof switchToTab === 'function') {
        try {
            const hasChat = (typeof MC !== 'undefined' && MC.tabs
                && MC.tabs.some(t => (t.id || t) === 'chat'));
            if (hasChat || (typeof MC === 'undefined')) {
                switchToTab('chat');
                // Ensure the thread paints after tab switch (loadChat is tab-driven).
                setTimeout(() => {
                    try { if (typeof loadChat === 'function') loadChat(); } catch (e) {}
                }, 200);
                return;
            }
        } catch (e) { /* fall through to door card */ }
    }
    // True chat-less surface only (e.g. Cove-admin apex): door card.
    _renderBrainAwakeCard();
    if (typeof MC !== 'undefined' && MC.coveAdminView && typeof loadCoveAdminPresences === 'function') {
        loadCoveAdminPresences(); return;
    }
    loadHomeApprovals();
}

async function enableTeamTuning(btn) {
    const st = document.getElementById('team-tune-status');
    const show = (msg, color) => {
        if (st) { st.style.display = 'block'; st.style.color = color || 'var(--dim)'; st.textContent = msg; }
    };
    if (btn) { btn.disabled = true; btn.textContent = 'Enabling…'; }
    show('Writing consent and starting the first team tune…');
    try {
        const r = await fetch('/api/onboarding/team-tuning/enable', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify({ run_now: true }),
        });
        const d = await r.json().catch(() => ({}));
        if (!r.ok || d.error || d.ok === false) {
            show(d.error || 'Could not enable team tuning.', 'var(--red,#e74c3c)');
            if (btn) { btn.disabled = false; btn.textContent = 'Enable daily team tuning'; }
            return;
        }
        const est = (d.estimate && d.estimate.summary) ? d.estimate.summary : '';
        show((d.message || 'Team auto-tune enabled.') + (est ? ' ' + est : ''), 'var(--green)');
        // Clear any skip flag path by refreshing cards — enable marks step done.
        setTimeout(() => { try { loadHomeApprovals(); } catch (e) {} }, 600);
    } catch (e) {
        show('Could not enable: ' + (e.message || e), 'var(--red,#e74c3c)');
        if (btn) { btn.disabled = false; btn.textContent = 'Enable daily team tuning'; }
    }
}

async function ackOnboarding(item) {

    // Jules 0113: mobile was false-completing when operators hit the nearby Done
    // while still reading the join command. Confirm before clearing that step.
    if (item === 'device_jules' || item === 'join_mesh') {
        const ok = confirm(
            'Mark Connect on mobile complete only after your phone is on the mesh '
            + 'and signed into this Cove.\n\nContinue?'
        );
        if (!ok) return;
    }
    try {
        await fetch('/api/onboarding/ack', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ item }),
        });
        if (item === 'device_jules' || item === 'join_mesh') {
            try {
                if (window._setupExpanded) {
                    delete window._setupExpanded.device_jules;
                    delete window._setupExpanded.device_jules_html;
                }
            } catch (e) {}
        }
        // Soft-refresh so Skip / Got it / compute clear the card without a full
        // page reload (Mosswood install: backup/tune stayed until refresh).
        if (typeof MC !== 'undefined' && MC.coveAdminView && typeof loadCoveAdminPresences === 'function') {
            await loadCoveAdminPresences();
        } else if (typeof loadHomeApprovals === 'function') {
            await loadHomeApprovals();
        }
    } catch (e) {}
}

// Compute establishment (#12): write where heavy work runs (compute.video_asr), then
// ack the step. local = this box's GPU · cloud = BYOK cloud ASR · external = a rented
// GPU (endpoint + grant token) · cpu = no GPU backend (GPU features stay limited, and
// say so). The video pipeline + other GPU features already gate on this config.
async function setCompute(choice, btn) {
    // JL-3: immediate running/progress feedback — previously the CTA only updated after
    // a full reload when ackOnboarding refreshed the card.
    const st = document.getElementById('compute-status');
    const show = (msg, color) => { if (st) { st.style.display = 'block'; st.style.color = color || 'var(--dim)'; st.textContent = msg; } };
    const cta = document.getElementById('compute-cta');
    const actionBtns = cta
        ? Array.from(cta.querySelectorAll('button'))
        : (btn ? [btn] : []);
    // Also lock the grant submit button if the rent panel is open outside cta flow.
    const rentBtn = document.querySelector('#compute-rent button.btn-approve');
    if (rentBtn && !actionBtns.includes(rentBtn)) actionBtns.push(rentBtn);
    const originals = actionBtns.map(b => ({ b, text: b.textContent, disabled: b.disabled }));
    const setBusy = (busy, activeLabel) => {
        for (const { b, text } of originals) {
            b.disabled = busy || false;
            if (busy) {
                b.textContent = (b === btn) ? (activeLabel || 'Working…') : text;
                b.style.opacity = (b === btn) ? '1' : '0.55';
            } else {
                b.textContent = text;
                b.style.opacity = '';
            }
        }
    };
    let payload;
    if (choice === 'local')      payload = { mode: 'local', url: '', token: '' };
    else if (choice === 'cloud') payload = { mode: 'cloud', url: '', token: '' };
    else if (choice === 'cpu')   payload = { mode: 'cloud', url: '', token: '' };   // default backend, no GPU/grant
    else if (choice === 'external') {
        const url = (document.getElementById('compute-rent-url') || {}).value || '';
        const token = (document.getElementById('compute-rent-token') || {}).value || '';
        if (!url.trim() || !token.trim()) { show('Paste both the endpoint and the grant code.', '#ff8c00'); return; }
        payload = { mode: 'external', url: url.trim(), token: token.trim() };
    } else return;

    const runningLabel = (choice === 'external') ? 'Using grant…'
        : (choice === 'local') ? 'Using GPU…'
        : (choice === 'cloud') ? 'Connecting…'
        : 'Saving…';
    setBusy(true, runningLabel);
    show('Setting up compute…', 'var(--dim)');
    try {
        const r = await fetch('/api/settings/compute/video_asr', {
            method: 'PUT', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const d = await r.json();
        if (!r.ok || d.error) {
            setBusy(false);
            show(d.error || 'Could not save that.', '#ff6b6b');
            return;
        }
    } catch (e) {
        setBusy(false);
        show('Could not save: ' + e.message, '#ff6b6b');
        return;
    }
    // Success path: keep busy state until the card refreshes away.
    if (btn) btn.textContent = '✓ Saved';
    show('Compute set — finishing…', 'var(--green)');
    // Mark the step done regardless of which path — compute is now established.
    // Jules 0231: after CPU/GPU choice the card sometimes stayed "busy" until a
    // manual Presences nav click. Force a soft board refresh after ack so the
    // checkmark + next unlocked step (mobile / backup / tune) appear without
    // leaving the page.
    await ackOnboarding('set_compute');
    try {
        if (typeof MC !== 'undefined' && MC.coveAdminView && typeof loadCoveAdminPresences === 'function') {
            await loadCoveAdminPresences();
        } else if (typeof loadHomeApprovals === 'function') {
            await loadHomeApprovals();
        }
    } catch (e) { /* ack already succeeded */ }
}

// Mosswood 2204: cloud ASR requires a provider key. Save the key, then set compute.
async function saveCloudKeyThenCompute(btn) {
    const provider = (document.getElementById('cloud-provider') || {}).value || '';
    const key = (document.getElementById('cloud-key') || {}).value || '';
    if (!provider) { alert('Choose a cloud provider (Groq, OpenAI, or Deepgram).'); return; }
    if (!key.trim()) { alert('Paste your API key for that provider.'); return; }
    const st = document.getElementById('compute-status');
    const show = (msg, color) => { if (st) { st.style.display = 'block'; st.style.color = color || 'var(--dim)'; st.textContent = msg; } };
    if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }
    try {
        // Save the pipeline key first
        const r = await fetch('/api/pipeline-keys', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ [provider + '_api_key']: key.trim() }),
        });
        const d = await r.json();
        if (!r.ok || d.error) {
            if (btn) { btn.disabled = false; btn.textContent = 'Save key & use cloud'; }
            show(d.error || 'Could not save key.', '#ff6b6b');
            return;
        }
        // Now set compute mode to cloud (video_asr)
        await setCompute('cloud', btn);
    } catch (e) {
        if (btn) { btn.disabled = false; btn.textContent = 'Save key & use cloud'; }
        show('Could not save: ' + e.message, '#ff6b6b');
    }
}

async function loadSiteDiff(repo, branch, containerId) {
    const el = document.getElementById(containerId);
    if (!el) return;
    if (el.style.display !== 'none') { el.style.display = 'none'; return; }
    el.style.display = 'block';
    el.innerHTML = '<span style="color:var(--dim);font-size:0.75rem;">Loading diff...</span>';
    try {
        const res = await fetch(`/api/sites/github/diff?repo=${encodeURIComponent(repo)}&head=${encodeURIComponent(branch)}`);
        const data = await res.json();
        if (data.error) {
            el.innerHTML = `<span style="color:var(--red);font-size:0.75rem;">${ESC(data.error)}</span>`;
            return;
        }
        const files = data.files || [];
        if (!files.length) {
            el.innerHTML = '<span style="color:var(--dim);font-size:0.75rem;">No changes found.</span>';
            return;
        }
        let html = '';
        files.forEach(f => {
            const statusColor = f.status === 'added' ? 'var(--green)' : f.status === 'removed' ? 'var(--red)' : 'var(--accent)';
            html += `<div class="diff-file">
                <div class="diff-file-header">
                    <span style="color:${statusColor}">${ESC(f.status)}</span>
                    <span>${ESC(f.filename)}</span>
                    <span style="color:var(--dim);font-size:0.65rem;">+${f.additions} -${f.deletions}</span>
                </div>
                ${f.patch ? `<pre class="diff-patch">${ESC(f.patch)}</pre>` : ''}
            </div>`;
        });
        el.innerHTML = html;
    } catch (e) {
        el.innerHTML = `<span style="color:var(--red);font-size:0.75rem;">Failed to load diff</span>`;
    }
}

// #D18: PR Review Card helpers
function _buildPRCards(recentApprovals) {
    // Build PR review cards from recent create_github_pr approvals.
    const cards = [];
    for (const a of recentApprovals) {
        if (a.tool_name !== 'create_github_pr' || a.status !== 'approved') continue;
        if (!a.result) continue;
        
        // Try to parse the JSON result
        let prData;
        try {
            prData = JSON.parse(a.result);
        } catch (e) {
            // Fallback: try to extract from old format string
            const match = a.result.match(/PR CREATED: #(\d+) (https:\/\/github\.com\/[^\s]+)/);
            if (!match) continue;
            prData = {
                pr_number: parseInt(match[1]),
                pr_url: match[2],
                title: a.description || 'PR',
                branch: '',
                base: 'main',
                repo: '',
                additions: 0,
                deletions: 0
            };
        }
        
        if (!prData || prData.status !== 'created') continue;
        
        const prNumber = prData.pr_number;
        const prUrl = prData.pr_url;
        const title = prData.title || 'Pull Request';
        const branch = prData.branch || '';
        const base = prData.base || 'main';
        const repo = prData.repo || '';
        const additions = prData.additions || 0;
        const deletions = prData.deletions || 0;
        
        // Extract ticket ref from title (e.g., "#D18: ...")
        const ticketMatch = title.match(/#([A-Z]*\d+)/);
        const ticketRef = ticketMatch ? ticketMatch[1] : '';
        
        const diffId = `pr-diff-${a.request_id}`;
        
        const cardHtml = `<div class="home-approval pr-review-card">
            <div class="approval-tool">PR #${prNumber}${ticketRef ? ` — ${ESC(ticketRef)}` : ''}</div>
            <div class="approval-desc"><b>${ESC(title)}</b></div>
            <div class="approval-file"><code>${ESC(branch)}</code> → <code>${ESC(base)}</code></div>
            <div class="approval-stats" style="font-size:0.75rem;color:var(--dim);margin:4px 0;">
                <span style="color:var(--green)">+${additions}</span> / <span style="color:var(--red)">-${deletions}</span>
            </div>
            ${repo && branch ? `<div class="approval-diff-toggle">
                <a href="#" onclick="loadPRDiff('${ESC(repo)}', '${ESC(base)}', '${ESC(branch)}', '${diffId}'); return false;">View Diff ▼</a>
            </div>
            <div id="${diffId}" class="approval-diff" style="display:none;"></div>` : ''}
            <div class="approval-actions">
                <a href="${ESC(prUrl)}" target="_blank" rel="noopener" class="btn-approve" style="text-decoration:none;">Open on GitHub ↗</a>
            </div>
        </div>`;
        
        cards.push(cardHtml);
    }
    return cards;
}

async function loadPRDiff(repo, base, head, containerId) {
    const el = document.getElementById(containerId);
    if (!el) return;
    if (el.style.display !== 'none') { el.style.display = 'none'; return; }
    el.style.display = 'block';
    el.innerHTML = '<span style="color:var(--dim);font-size:0.75rem;">Loading diff...</span>';
    try {
        const res = await fetch(`/api/pr/diff?repo=${encodeURIComponent(repo)}&base=${encodeURIComponent(base)}&head=${encodeURIComponent(head)}`);
        const data = await res.json();
        if (data.error) {
            el.innerHTML = `<span style="color:var(--red);font-size:0.75rem;">${ESC(data.error)}</span>`;
            return;
        }
        const files = data.files || [];
        if (!files.length) {
            el.innerHTML = '<span style="color:var(--dim);font-size:0.75rem;">No changes found.</span>';
            return;
        }
        let html = '';
        files.forEach(f => {
            const statusColor = f.status === 'added' ? 'var(--green)' : f.status === 'removed' ? 'var(--red)' : 'var(--accent)';
            html += `<div class="diff-file">
                <div class="diff-file-header">
                    <span style="color:${statusColor}">${ESC(f.status)}</span>
                    <span>${ESC(f.filename)}</span>
                    <span style="color:var(--dim);font-size:0.65rem;">+${f.additions} -${f.deletions}</span>
                </div>
                ${f.patch ? `<pre class="diff-patch">${ESC(f.patch)}</pre>` : ''}
            </div>`;
        });
        el.innerHTML = html;
    } catch (e) {
        el.innerHTML = `<span style="color:var(--red);font-size:0.75rem;">Failed to load diff</span>`;
    }
}

async function respondApproval(requestId, approved, btnEl) {
    // Use the actual clicked button (robust); fall back to a lookup for old callers.
    const actionBtn = btnEl
        || document.querySelector(`.home-approval button[onclick*="${requestId}"]`);
    const card = actionBtn ? actionBtn.closest('.home-approval') : null;
    const buttons = card ? card.querySelectorAll('button') : (actionBtn ? [actionBtn] : []);

    // Re-entry guard — an already-processing click is ignored (no double-submit).
    if (actionBtn && actionBtn.disabled) return;

    // Immediate pressed state, set BEFORE the await so the click always registers visibly.
    buttons.forEach(b => { b.disabled = true; b.style.opacity = '0.55'; });
    if (actionBtn) {
        actionBtn.dataset.origText = actionBtn.textContent;
        actionBtn.textContent = approved ? 'Approving…' : 'Denying…';
        actionBtn.style.opacity = '1';
    }

    try {
        const res = await fetch(`/api/bridge/approvals/${requestId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ approved }),
        });
        const data = await res.json();
        // The action really runs now — surface a failed execution instead of a false ✓.
        const executed = data.executed !== false;
        const resultText = (data.result || '').toString();

        if (actionBtn) {
            if (!approved) {
                actionBtn.textContent = 'Denied';
            } else if (data.site_edit) {
                actionBtn.textContent = executed ? 'Deployed ✓' : 'Approved — merge failed';
                actionBtn.style.background = executed ? 'var(--accent)' : 'var(--red)';
            } else {
                actionBtn.textContent = executed ? 'Approved ✓' : 'Approved — action failed';
                actionBtn.style.background = executed ? 'var(--accent)' : 'var(--red)';
            }
            // #D52: on real failure, surface the tool's actual result text on the card.
            if (executed === false && resultText) actionBtn.title = resultText.slice(0, 300);
        }

        // Hold the confirmation briefly, then refresh the list.
        setTimeout(() => loadHomeApprovals(), 2500);
    } catch (err) {
        if (actionBtn) {
            actionBtn.textContent = 'Error — retry';
            actionBtn.style.opacity = '1';
        }
        buttons.forEach(b => { b.disabled = false; b.style.opacity = '1'; });
    }
}

// ── Task Helpers ─────────────────────────────────────────────────────────────

function _agentInitial(assignee) {
    if (!assignee) return '';
    // Use LP Color System agent identity colors
    return lpAgentBadgeHTML(assignee);
}

function _dueDateHTML(due_date) {
    if (!due_date) return '';
    const d = new Date(due_date + 'T12:00:00');
    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const tomorrow = new Date(today); tomorrow.setDate(today.getDate() + 1);
    const dueDay = new Date(d.getFullYear(), d.getMonth(), d.getDate());

    let cls = 'due-later';
    if (dueDay < today) cls = 'due-overdue';
    else if (dueDay <= tomorrow) cls = 'due-soon';

    const label = formatDate(due_date, { month: 'short', day: 'numeric' });
    return `<span class="task-due ${cls}">${label}</span>`;
}

function _assigneeDropdownHTML(id, currentAssignee) {
    const agents = MC.agents || [];
    const operator = MC.instance?.operator || 'Operator';
    let opts = `<option value="">Unassigned</option>`;
    opts += `<option value="${ESC(operator.toLowerCase())}"${currentAssignee === operator.toLowerCase() ? ' selected' : ''}>${ESC(operator)}</option>`;
    agents.forEach(a => {
        const sel = (currentAssignee === a.id || currentAssignee === a.name?.toLowerCase()) ? ' selected' : '';
        opts += `<option value="${ESC(a.id)}"${sel}>${ESC(a.name || a.id)}</option>`;
    });
    return `<select id="${id}" class="task-select">${opts}</select>`;
}

// ── Task Row HTML ────────────────────────────────────────────────────────────

let _homeTasksExpanded = false;
let _expandedTaskId = null;

function _taskItemHTML(t) {
    const pri = t.priority || 'normal';
    const dotColor = _priColors[pri] || _priColors.normal;
    const priTitle = pri.charAt(0).toUpperCase() + pri.slice(1);

    // Row 1: priority dot + title + note icon (if has notes)
    // Row 2: agent initial (admin only) + project name dimmed (left) ... due date (right)
    const agentBadge = _isAdmin() ? _agentInitial(t.assignee) : '';
    const projectTag = t.project_name ? `<span class="task-project-tag">${ESC(t.project_name)}</span>` : '';
    const noteIcon = t.notes ? '<span class="task-note-icon" title="Has notes">&#128221;</span>' : '';
    const dueHTML = _dueDateHTML(t.due_date);

    return `<div class="task-item" onclick="toggleTaskEdit(${t.id})" data-task-id="${t.id}">
        <span class="task-pri-dot" style="background:${dotColor};" title="${ESC(priTitle)} priority"></span>
        <div class="task-info">
            <div class="task-title">${ESC(t.title || '')}${noteIcon}</div>
            <div class="task-meta-row">
                <div class="task-meta-left">
                    ${agentBadge}${projectTag}
                </div>
                ${dueHTML}
            </div>
        </div>
    </div>
    <div class="task-edit-form" id="task-edit-${t.id}" style="display:none;">
        <div class="task-edit-row">
            <label>Title</label>
            <input type="text" id="te-title-${t.id}" value="${ESC(t.title || '')}" class="task-input">
        </div>
        <div class="task-edit-row task-edit-grid">
            <div>
                <label>Status</label>
                <select id="te-status-${t.id}" class="task-select">
                    ${['pending','in_progress','blocked','review','done','cancelled'].map(s =>
                        `<option value="${s}"${t.status === s ? ' selected' : ''}>${s.replace('_',' ')}</option>`
                    ).join('')}
                </select>
            </div>
            <div>
                <label>Priority</label>
                <select id="te-pri-${t.id}" class="task-select">
                    ${['urgent','high','normal','low'].map(p =>
                        `<option value="${p}"${t.priority === p ? ' selected' : ''}>${p}</option>`
                    ).join('')}
                </select>
            </div>
        </div>
        <div class="task-edit-row task-edit-grid">
            ${_isAdmin() ? `<div>
                <label>Assignee</label>
                ${_assigneeDropdownHTML(`te-assign-${t.id}`, t.assignee || '')}
            </div>` : ''}
            <div>
                <label>Due date</label>
                <input type="date" id="te-due-${t.id}" value="${t.due_date || ''}" class="task-input">
            </div>
        </div>
        <div class="task-edit-row">
            <label>Notes</label>
            <textarea id="te-notes-${t.id}" class="task-input task-textarea" rows="2" placeholder="Optional notes...">${ESC(t.notes || '')}</textarea>
        </div>
        <div class="task-edit-actions">
            <button class="btn-small btn-save" onclick="event.stopPropagation(); saveTask(${t.id})">Save</button>
            <button class="btn-small btn-cancel" onclick="event.stopPropagation(); closeTaskEdit(${t.id})">Cancel</button>
        </div>
    </div>`;
}

function toggleTaskEdit(taskId) {
    const form = document.getElementById(`task-edit-${taskId}`);
    if (!form) return;
    if (_expandedTaskId === taskId) {
        closeTaskEdit(taskId);
        return;
    }
    if (_expandedTaskId !== null) {
        const prev = document.getElementById(`task-edit-${_expandedTaskId}`);
        if (prev) prev.style.display = 'none';
    }
    form.style.display = 'block';
    _expandedTaskId = taskId;
}

function closeTaskEdit(taskId) {
    const form = document.getElementById(`task-edit-${taskId}`);
    if (form) form.style.display = 'none';
    if (_expandedTaskId === taskId) _expandedTaskId = null;
}

async function saveTask(taskId) {
    const body = {
        title: document.getElementById(`te-title-${taskId}`)?.value || '',
        status: document.getElementById(`te-status-${taskId}`)?.value || 'pending',
        priority: document.getElementById(`te-pri-${taskId}`)?.value || 'normal',
        assignee: document.getElementById(`te-assign-${taskId}`)?.value || '',
        due_date: document.getElementById(`te-due-${taskId}`)?.value || null,
        notes: document.getElementById(`te-notes-${taskId}`)?.value || '',
    };
    try {
        const res = await fetch(`/api/tasks/${taskId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        _expandedTaskId = null;
        loadHomeTasks();
    } catch (e) {
        alert('Failed to save: ' + e.message);
    }
}

async function loadHomeTasks() {
    const el = document.getElementById('home-tasks');
    if (!el) return;
    const limit = _homeTasksExpanded ? 50 : 5;
    const statusParam = _homeTasksExpanded ? '' : '&status=pending';
    try {
        const data = await fetch(`/api/tasks?limit=${limit}${statusParam}`).then(r => r.json());
        const tasks = data.tasks || [];

        if (!tasks.length) {
            el.innerHTML = '<span class="empty-msg">No pending tasks</span>';
            return;
        }

        el.innerHTML = tasks.map(t => _taskItemHTML(t)).join('');
    } catch {
        el.innerHTML = '<span class="empty-msg">Tasks unavailable</span>';
    }
}

function loadAllHomeTasks() {
    _homeTasksExpanded = true;
    loadHomeTasks();
}

// ── Legend toggle ────────────────────────────────────────────────────────────

function toggleTaskLegend() {
    const el = document.getElementById('task-legend');
    if (el) el.style.display = el.style.display === 'none' ? 'flex' : 'none';
}

// ── Completed Tasks Modal ───────────────────────────────────────────────────

async function showCompletedTasks() {
    // Create modal if it doesn't exist
    let modal = document.getElementById('completed-tasks-modal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'completed-tasks-modal';
        modal.className = 'modal-overlay';
        modal.onclick = (e) => { if (e.target === modal) modal.style.display = 'none'; };
        document.body.appendChild(modal);
    }

    modal.innerHTML = `<div class="modal-content">
        <div class="modal-header">
            <span class="modal-title">Recently Completed</span>
            <button class="btn-cancel" onclick="document.getElementById('completed-tasks-modal').style.display='none'">Close</button>
        </div>
        <div class="modal-body"><div class="loading">Loading...</div></div>
    </div>`;
    modal.style.display = 'flex';

    try {
        const data = await fetch('/api/tasks?status=done&limit=10').then(r => r.json());
        const tasks = data.tasks || [];
        const body = modal.querySelector('.modal-body');

        if (!tasks.length) {
            body.innerHTML = '<span class="empty-msg">No completed tasks yet</span>';
            return;
        }

        body.innerHTML = tasks.map(t => {
            const completedDate = t.completed_at
                ? formatDate(t.completed_at, { month: 'short', day: 'numeric' })
                : t.updated_at
                    ? formatDate(t.updated_at, { month: 'short', day: 'numeric' })
                    : '';
            const agentBadge = _agentInitial(t.assignee);
            return `<div class="completed-task-row">
                <div class="completed-task-info">
                    <div class="completed-task-title">${ESC(t.title || '')}</div>
                    <div class="task-meta-row">
                        <div class="task-meta-left">${agentBadge}</div>
                        ${completedDate ? `<span class="task-due due-later">${completedDate}</span>` : ''}
                    </div>
                </div>
            </div>`;
        }).join('');
    } catch (e) {
        modal.querySelector('.modal-body').innerHTML = `<span class="empty-msg">Error: ${ESC(e.message)}</span>`;
    }
}

// ── New Task Quick-Add ──────────────────────────────────────────────────────

function showNewTaskForm() {
    const el = document.getElementById('new-task-form');
    if (el) {
        el.style.display = el.style.display === 'none' ? 'block' : 'none';
        if (el.style.display === 'block') {
            document.getElementById('new-task-title')?.focus();
        }
        return;
    }
}

async function createTask() {
    const titleEl = document.getElementById('new-task-title');
    const title = titleEl?.value?.trim();
    if (!title) { titleEl?.focus(); return; }

    try {
        const res = await fetch('/api/tasks', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title, status: 'pending', priority: 'normal' }),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        titleEl.value = '';
        document.getElementById('new-task-form').style.display = 'none';
        loadHomeTasks();
    } catch (e) {
        alert('Failed to create task: ' + e.message);
    }
}

// ── New Project Quick-Add ───────────────────────────────────────────────────

function showNewProjectForm() {
    const el = document.getElementById('new-project-form');
    if (el) {
        el.style.display = el.style.display === 'none' ? 'block' : 'none';
        if (el.style.display === 'block') {
            document.getElementById('new-project-title')?.focus();
        }
        return;
    }
}

async function createProject() {
    const titleEl = document.getElementById('new-project-title');
    const title = titleEl?.value?.trim();
    if (!title) { titleEl?.focus(); return; }

    const slug = title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
    try {
        const res = await fetch('/api/projects', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: title, slug, status: 'active' }),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        titleEl.value = '';
        document.getElementById('new-project-form').style.display = 'none';
        if (typeof loadProjectsTab === 'function') loadProjectsTab();
        if (typeof loadOverview === 'function') loadOverview();
    } catch (e) {
        alert('Failed to create project: ' + e.message);
    }
}

// ── Auto-refresh ────────────────────────────────────────────────────────────

setInterval(() => {
    const homePanel = document.getElementById('panel-home');
    if (homePanel && homePanel.classList.contains('active')) {
        // Jules 0113: don't hard-refresh Attention while a setup form is open —
        // that was collapsing Set address / mobile mid-flow every ~30s.
        const setupBusy = document.querySelector(
            '#dom-form[style*="block"], #addr-mesh-step[style*="block"], '
            + '#ai-form[style*="block"], #mesh-key-out[style*="block"], #compute-rent[style*="block"]'
        );
        if (setupBusy) return;
        loadHomeApprovals();
    }
}, 30000);

// Init
loadHome();
setTimeout(() => {
    const upcoming = document.getElementById('home-upcoming');
    const tasks = document.getElementById('home-tasks');
    if (upcoming && upcoming.querySelector('.loading')) loadHomeCalendar();
    if (tasks && tasks.querySelector('.loading')) loadHomeTasks();
}, 3000);
