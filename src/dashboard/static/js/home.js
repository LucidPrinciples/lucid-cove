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
        const [obData, data] = await Promise.all([
            fetch('/api/onboarding/items').then(r => r.json()).catch(() => ({ items: [] })),
            fetch('/api/bridge/approvals').then(r => r.json()).catch(() => ({ approvals: [] })),
        ]);
        const steps = obData.steps || [];
        const showSetup = steps.length > 0 && !obData.complete;
        const approvals = data.approvals || [];
        const total = (showSetup ? 1 : 0) + approvals.length;

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
                        <button class="btn-approve" onclick="respondApproval('${ESC(a.request_id)}', true)">Approve &amp; Deploy</button>
                        <button class="btn-deny" onclick="respondApproval('${ESC(a.request_id)}', false)">Deny</button>
                    </div>
                </div>`;
            }

            return `<div class="home-approval">
                <div class="approval-tool">${ESC(a.tool_name)}</div>
                <div class="approval-desc">${ESC(a.description || '').substring(0, 120)}</div>
                <div class="approval-actions">
                    <button class="btn-approve" onclick="respondApproval('${ESC(a.request_id)}', true)">Approve</button>
                    <button class="btn-deny" onclick="respondApproval('${ESC(a.request_id)}', false)">Deny</button>
                </div>
            </div>`;
        }).join('');
        el.innerHTML = obHtml + apHtml;
    } catch {
        el.innerHTML = '<span class="empty-msg">Approvals unavailable</span>';
        if (badge) badge.classList.add('hidden');
    }
}

// ── First-run onboarding cards (persistent until done) ───────────────────────
// Dependency-gated first-run setup, shown inside Pending Approvals: each step
// unlocks only when the prior is done. Address → Intelligence → Device+jules.
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
    return html + `</div>`;
}

function _setupDoneLine(s) {
    // B14: the address-done card carries the domain door — where the Cove now lives
    // (signed-in operator link, new window, no auto-redirect) + the mesh-first note.
    let door = '';
    if (s.id === 'claim_address' && s.domain) {
        const href = s.door || ('https://' + s.domain);
        door = `<div style="opacity:1;margin-top:4px;font-size:0.68rem;color:var(--text);">
            Your Cove lives at <b>https://${ESC(s.domain)}</b> —
            <a href="${ESC(href)}" target="_blank" rel="noopener" style="color:var(--accent);">open it &#8599;</a>
            <span style="color:var(--dim);">(other devices need your Cove's mesh first)</span>
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
            <p>A <b>Cove</b> is your private family Intelligence. Three quick steps get it running:</p>
            <p><b>1. Set your address.</b> Your Cove gets its own web address. That turns on HTTPS, so voice and the mic work, and gives everyone a clean link (you're <code>your-handle.your-address</code>).</p>
            <p><b>2. Add intelligence.</b> Connect a model (your own key, or a local one). This switches on your <b>Agent</b> and the <b>Tools</b> — including jules.</p>
            <p><b>3. Get it on your phone.</b> Join your phone to the private mesh, then open <b>jules</b> — talk anywhere and it lands in your Inbox for your agent to act on.</p>
            <p style="color:var(--dim);">That's it. From there your agent helps you build, capture, and organize — and you can add family members, each with their own handle.</p>
            <div style="text-align:right;margin-top:10px;"><button class="btn-approve" onclick="document.getElementById('onboarding-help-modal').remove()">Got it</button></div>
        </div>`;
        document.body.appendChild(m);
    }
}

function _onboardingCardHtml(item) {
    const title = ESC(item.title || ''), body = ESC(item.body || '');
    if (item.id === 'set_compute') {
        // Compute establishment (#12): the operator picks WHERE heavy work runs. Each
        // choice writes compute.video_asr via /api/settings/compute (the video pipeline
        // + other GPU features gate on it), then acks the step.
        //   local    → this box's GPU        rent → external URL + grant token
        //   cloud    → BYOK cloud ASR        cpu  → no GPU backend (features limited)
        const gpu = item.gpu || {};
        const hasGpu = !!gpu.present;
        const localBtn = hasGpu
            ? `<button class="btn-approve" onclick="setCompute('local')">Use this GPU ★</button>`
            : '';
        // CF-72: the choice's price tag — what a starter month costs on cloud keys
        // vs ~$0 local, computed server-side (item.cost). Purely informational.
        const costLine = (item.cost && item.cost.summary)
            ? `<div style="margin-top:6px;font-size:0.72rem;color:var(--dim);">💡 ${ESC(item.cost.summary)}</div>`
            : '';
        return `<div class="home-approval onboarding-card">
            <div class="approval-tool">${title}${hasGpu ? ' <span style="color:var(--green);font-size:0.7rem;">● GPU found</span>' : ' <span style="color:var(--dim);font-size:0.7rem;">○ no GPU</span>'}</div>
            <div class="approval-desc" style="line-height:1.6;">${body}</div>${costLine}
            <div id="compute-rent" style="display:none;margin-top:8px;">
                <div style="font-size:0.72rem;color:var(--dim);margin-bottom:4px;">Paste a GPU grant: the endpoint and the code the provider gave you (e.g. from another Cove's Rent-a-GPU card).</div>
                <input id="compute-rent-url" class="settings-input" placeholder="https://voice.their-cove.lucidcove.org" style="width:100%;">
                <input id="compute-rent-token" class="settings-input" placeholder="grant code (gpugrant_…)" style="width:100%;margin-top:6px;">
                <div style="margin-top:6px;display:flex;gap:6px;flex-wrap:wrap;">
                    <button class="btn-approve" onclick="setCompute('external')">Use this GPU grant</button>
                    <a class="btn-ghost" href="/static/action-board/rent-gpu.html" target="_blank" rel="noopener" style="text-decoration:none;">Browse marketplace ↗</a>
                </div>
            </div>
            <div class="approval-actions" id="compute-cta" style="flex-wrap:wrap;gap:6px;">
                ${localBtn}
                <button class="btn-approve" onclick="setCompute('cloud')">Use cloud</button>
                <button class="btn-approve" onclick="document.getElementById('compute-rent').style.display='block';">Rent a GPU</button>
                <button class="btn-ghost" onclick="setCompute('cpu')">CPU only for now</button>
            </div>
            <div id="compute-status" style="display:none;margin-top:6px;font-size:0.72rem;"></div>
        </div>`;
    }
    if (item.id === 'claim_address') {
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
            <div class="approval-desc" style="line-height:1.6;">Your Lucid Cove team runs on an AI model. The setup tour uses one from Lucid Cove. Connect your own to bring the team online. Your agents' memory and identity stay either way.</div>
            <div id="ai-form" style="display:none;margin-top:8px;">
                <div id="ai-local" style="margin-bottom:10px;font-size:0.78rem;color:var(--dim);">Checking this machine…</div>
                <div style="font-size:0.7rem;color:var(--dim);margin-bottom:4px;">Or connect a provider key:</div>
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
                <button class="btn-approve" onclick="document.getElementById('ai-form').style.display='block';this.parentElement.style.display='none';loadMachineProbe();">Add intelligence</button>
            </div>
        </div>`;
    }
    if (item.id === 'device_jules' || item.id === 'join_mesh') {
        // PHONE-FIRST: this card is titled "Connect on mobile", so lead with the phone
        // flow (the Tailscale app — a phone can't run a `tailscale up` CLI command). The
        // join-code command is the secondary path for a laptop/server. Coordination
        // server mirrors Settings' Connect-a-device block.
        return `<div class="home-approval onboarding-card">
            <div class="approval-tool">${title}</div>
            <div class="approval-desc">${body}</div>
            <div style="font-size:0.72rem;color:var(--dim);margin-top:8px;line-height:1.6;">
                <strong style="color:var(--text);">Two steps, in order — they do different things:</strong>
                <div style="margin-top:4px;"><strong style="color:var(--text);">1. Join code</strong> puts the <em>device</em> on your Cove's private network (the mesh) — so the phone can reach the box at all.</div>
                <div><strong style="color:var(--text);">2. Sign-in link</strong> signs <em>you</em> into the Cove (your identity) — so it opens as you, with your files and agent.</div>
                <div style="margin-top:4px;color:var(--dim);">Your phone needs both: network first, then identity.</div>
            </div>
            <div style="font-size:0.72rem;color:var(--dim);margin-top:8px;line-height:1.6;">
                <strong style="color:var(--text);">On your phone:</strong> install the <strong>Tailscale</strong> app, tap the <strong>⋯ menu (top right)</strong> and choose
                <em>“Use a custom coordination server”</em>, enter
                <code style="background:var(--card);padding:1px 5px;border-radius:3px;">https://headscale.lucidcove.org</code>,
                then sign in with the join code and approve the device (that's step 1 — the mesh). If Tailscale is already signed in to another network, log out first — this is a separate tailnet. Then open your Cove at your address and use your sign-in link (step 2 — your identity), and add <strong>jules</strong> to your home screen.
            </div>
            <div id="mesh-key-out" style="display:none;margin-top:8px;font-size:0.72rem;"></div>
            <div class="approval-actions" style="flex-wrap:wrap;gap:6px;">
                <button class="btn-ghost" onclick="getMeshKey(this)">On a laptop/server? Get a join command</button>
                <a class="btn-approve" href="/jules" target="_blank" rel="noopener" style="text-decoration:none;">Open jules ↗</a>
                <button class="btn-ghost" onclick="ackOnboarding('device_jules')">Done</button>
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
        return `<div class="home-approval onboarding-card">
            <div class="approval-tool">${title}</div>
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

async function getMeshKey(btn) {
    const out = document.getElementById('mesh-key-out');
    if (btn) { btn.disabled = true; btn.textContent = '…'; }
    try {
        const r = await fetch('/api/onboarding/mesh-key');
        const d = await r.json();
        if (out) {
            out.style.display = 'block';
            if (d.ok && d.join_cmd) {
                out.innerHTML = '<div style="color:var(--dim);margin-bottom:4px;">On a laptop or server, run this (valid ~1h). A phone uses the Tailscale app instead — see above.</div>'
                    + '<code style="display:block;padding:6px;background:var(--card);border:1px solid var(--border);border-radius:4px;word-break:break-all;">'
                    + ESC(d.join_cmd) + '</code>';
            } else {
                out.innerHTML = '<div style="color:var(--orange);">' + ESC(d.reason || 'Could not mint a join code here.') + '</div>'
                    + (d.instructions ? '<div style="color:var(--dim);margin-top:4px;font-size:0.95em;">' + ESC(d.instructions) + '</div>' : '');
            }
        }
    } catch (e) {
        if (out) { out.style.display = 'block'; out.textContent = 'Could not reach the mesh service.'; }
    }
    if (btn) { btn.disabled = false; btn.textContent = 'Get join code'; }
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
        _addrShowClaim(reach);
    } else {
        const step = document.getElementById('addr-mesh-step');
        if (step) step.style.display = 'block';
    }
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
            if (mo) { mo.style.display = 'block'; mo.innerHTML = '<div style="color:var(--orange);">' + ESC(d.message || 'Put this box on the mesh first, then claim the address.') + '</div>'; }
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
                const _door = d.door || ('https://' + d.domain);
                html = `<div style="color:var(--green);">&#10003; Address set to https://${ESC(d.domain)}. We handled the DNS and certificate for you &mdash; nothing to copy and nothing to do. The secure connection finishes in under a minute.</div>`
                    + `<div style="margin-top:10px;color:var(--text);">Your Cove now lives at <b>https://${ESC(d.domain)}</b> &mdash; open it there:</div>`
                    + `<a class="btn-approve" style="text-decoration:none;display:inline-block;margin-top:6px;" href="${ESC(_door)}" target="_blank" rel="noopener">Open my Cove &#8599;</a>`
                    + `<div style="color:var(--dim);font-size:0.66rem;margin-top:6px;">Your other devices reach it once they're on your Cove's mesh (add them from Settings &rarr; Connect a device).</div>`
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
                html = `<div style="color:var(--accent);">Saved ${ESC(d.domain)}.</div>${steps}`
                    + (d.host_command ? `<code style="display:block;margin-top:6px;padding:6px;background:var(--card);border:1px solid var(--border);border-radius:4px;word-break:break-all;">${ESC(d.host_command)}</code>` : '');
            }
            out.innerHTML = html;
        }
        // Refresh so the new domain takes effect everywhere (Settings, admin links, voice,
        // cloud). A full reload re-derives the whole config; skip it when we handed back
        // DNS records the operator still needs to read.
        // fully_live shows a persistent "Done — refresh" button (the operator reloads when
        // ready). Records path just refreshes the approvals list so the step stays put while
        // they copy the DNS records. Nothing auto-reloads out from under the confirmation.
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
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ provider, api_key }),
        });
        const d = await r.json();
        if (!r.ok || d.error || d.ok === false) { alert(d.error || 'Could not save that right now.'); return; }

        // The brain is connected. The personal agent — already met at the wake — now
        // ACTUALLY thinks for the first time: brain-acknowledge generates its acknowledgment
        // LIVE with the just-connected model (the real proof it works), continuing that same
        // chat thread, with a written fallback server-side so the moment is never silent. Then
        // we take them straight to chat where it's waiting. Connecting intelligence is a
        // deliberate one-time action (the card only shows when no model is set), no guard.
        // Run-2 2.8/2.9: fire the acknowledgment in the BACKGROUND — it's a model
        // generation that can run 30s+ (or hang), and awaiting it froze this flow.
        // The key save already succeeded; the ack lands in chat when it's ready.
        fetch('/api/presence/brain-acknowledge', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({}),
        }).catch(() => { /* best-effort — generated server-side with its own fallback */ });
        _afterIntelligenceConnected();
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
        if (!provs.length) {
            out.innerHTML = `<div>${ESC(rec.reason || 'No local models found on this machine.')}</div>`;
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
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ provider, model, api_key: '' }),
        });
        const d = await r.json();
        if (!r.ok || d.error || d.ok === false) { _restore(); alert(d.error || 'Could not save that right now.'); return; }
        // Run-2 2.8/2.9: the button stayed "Connecting…" because this AWAITED
        // brain-acknowledge — a model generation that can run 30s+ or hang.
        // The key save already succeeded: show it, move on; the acknowledgment
        // lands in chat when it's ready (server-side fallback keeps it non-silent).
        fetch('/api/presence/brain-acknowledge', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}),
        }).catch(() => { /* best-effort — generated server-side with its own fallback */ });
        if (btn) { btn.textContent = '✓ Connected'; }
        _afterIntelligenceConnected();
    } catch (e) { _restore(); alert('Could not save: ' + e.message); }
}

// B2 reachable-host door (mirrors presence-profile.js): only jump to the
// {handle}.{domain} subdomain when we're ALREADY on the cove domain (proves DNS resolves);
// otherwise stay on this origin with ?as= so the link is never a dead subdomain.
function _presenceChatDoorHref() {
    const dom = (typeof MC !== 'undefined' && MC.config && MC.config.domain) || '';
    const handle = (typeof MC !== 'undefined' && MC.presence && MC.presence.username) || '';
    const onDomain = dom && location.host.endsWith(dom);
    if (handle && onDomain) return location.protocol + '//' + handle + '.' + dom + '/';
    if (handle) return location.origin + '/?as=' + encodeURIComponent(handle);
    return location.origin + '/';
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
           href="${ESC(href)}" target="_blank" rel="noopener">Open chat &#8599;</a>
    </div>`;
    if (card) { card.outerHTML = html; return true; }
    return false;
}

// After intelligence connects: drop into chat where the agent's acknowledgment is
// waiting IF a chat tab exists on this surface; otherwise (e.g. adding it from the
// Cove-admin apex, which has no chat) show the "awake — Open chat" door card (B2) and
// refresh the surface so the step clears.
function _afterIntelligenceConnected() {
    if (typeof markChatUnread === 'function') markChatUnread();
    const hasChat = (typeof MC !== 'undefined' && MC.tabs
        && MC.tabs.some(t => (t.id || t) === 'chat'));
    if (hasChat && typeof switchToTab === 'function') { switchToTab('chat'); return; }
    // Chat-less surface: transform the intelligence card into the awake-door card.
    _renderBrainAwakeCard();
    if (typeof MC !== 'undefined' && MC.coveAdminView && typeof loadCoveAdminPresences === 'function') {
        loadCoveAdminPresences(); return;
    }
    loadHomeApprovals();
}

async function ackOnboarding(item) {
    try {
        await fetch('/api/onboarding/ack', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ item }),
        });
        loadHomeApprovals();
    } catch (e) {}
}

// Compute establishment (#12): write where heavy work runs (compute.video_asr), then
// ack the step. local = this box's GPU · cloud = BYOK cloud ASR · external = a rented
// GPU (endpoint + grant token) · cpu = no GPU backend (GPU features stay limited, and
// say so). The video pipeline + other GPU features already gate on this config.
async function setCompute(choice) {
    const st = document.getElementById('compute-status');
    const show = (msg, color) => { if (st) { st.style.display = 'block'; st.style.color = color || 'var(--dim)'; st.textContent = msg; } };
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
    try {
        const r = await fetch('/api/settings/compute/video_asr', {
            method: 'PUT', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const d = await r.json();
        if (!r.ok || d.error) { show(d.error || 'Could not save that.', '#ff6b6b'); return; }
    } catch (e) { show('Could not save: ' + e.message, '#ff6b6b'); return; }
    // Mark the step done regardless of which path — compute is now established.
    await ackOnboarding('set_compute');
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

async function respondApproval(requestId, approved) {
    // Find the approval card and update button states
    const card = document.querySelector(`.home-approval button[onclick*="${requestId}"]`)?.closest('.home-approval');
    const buttons = card ? card.querySelectorAll('button') : [];
    const actionBtn = approved
        ? card?.querySelector('.btn-approve')
        : card?.querySelector('.btn-deny');

    // Disable all buttons and show processing state
    buttons.forEach(b => b.disabled = true);
    if (actionBtn) {
        actionBtn.dataset.origText = actionBtn.textContent;
        actionBtn.textContent = approved ? 'Deploying...' : 'Denying...';
        actionBtn.style.opacity = '0.7';
    }

    try {
        const res = await fetch(`/api/bridge/approvals/${requestId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ approved }),
        });
        const data = await res.json();

        if (actionBtn) {
            if (approved && data.site_edit) {
                actionBtn.textContent = 'Deployed ✓';
                actionBtn.style.background = 'var(--accent)';
            } else if (approved) {
                actionBtn.textContent = 'Approved ✓';
            } else {
                actionBtn.textContent = 'Denied';
            }
        }

        // Refresh approvals after a moment so the user sees the confirmation
        setTimeout(() => loadHomeApprovals(), 2000);
    } catch (err) {
        if (actionBtn) {
            actionBtn.textContent = 'Error — retry';
            actionBtn.disabled = false;
            actionBtn.style.opacity = '1';
        }
        buttons.forEach(b => b.disabled = false);
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
