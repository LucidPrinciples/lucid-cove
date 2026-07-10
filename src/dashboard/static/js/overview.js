// =============================================================================
// Stuart Mission Control — Overview tab
// =============================================================================

// =============================================================================
// Overview tab
// =============================================================================
async function loadOverview() {
    loadOverviewTuning();
    loadApprovals();
    loadHomeProjects();
    // Tasks handled by home.js → loadHomeTasks() into #home-tasks
}

async function loadAgentRoster() {
    const el = document.getElementById('agentRoster');
    try {
        const res = await fetch('/api/status');
        const data = await res.json();

        // API returns agents as object keyed by id: {"stuart": {...}, "mercer": {...}}
        const agentsObj = data.agents || {};
        const agents = Object.entries(agentsObj);

        if (!agents.length) {
            el.innerHTML = '<span class="empty">No agents registered</span>';
            return;
        }

        el.innerHTML = agents.map(([id, a]) => {
            const name = a.name || id.charAt(0).toUpperCase() + id.slice(1);
            const archetype = a.archetype || a.role || '';
            const status = a.status || 'active';
            const freq = a.last_frequency || '';
            const echoNum = a.last_echo_num || '';
            const tunedAt = a.last_tuned_at || '';
            const tunedStr = tunedAt ? formatDateOnly(tunedAt) : '';

            return `<div class="agent-card" onclick="showAgentDetail('${esc(id)}')" style="cursor:pointer;" title="View ${esc(name)} details">
                <img class="agent-thumb" src="${avatarPath(name)}" alt="${name}" onerror="this.style.display='none'">
                <div class="agent-info">
                    <div class="agent-name">${esc(name)}</div>
                    ${archetype ? `<div class="agent-archetype">${esc(archetype)}</div>` : ''}
                    <div class="agent-meta">
                        <span class="status-badge ${status}">${statusDotHTML(status)} ${esc(status)}</span>
                        ${freq ? `<span class="freq-badge">${esc(freq)}</span>` : ''}
                        ${echoNum ? `<span class="echo-badge">Echo #${esc(String(echoNum))}</span>` : ''}
                        ${tunedStr ? `<span class="date-dim">${tunedStr}</span>` : ''}
                    </div>
                </div>
            </div>`;
        }).join('');
    } catch (e) {
        el.innerHTML = `<span class="empty">Error loading agents: ${esc(e.message)}</span>`;
    }
}


// =============================================================================
// Agent Detail Page
// =============================================================================
async function showAgentDetail(agentId) {
    // Hide all panels, show agent detail
    document.querySelectorAll('.panel').forEach(p => {
        p.classList.remove('active-grid', 'active-flex');
        p.style.display = 'none';
    });
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    const detail = document.getElementById('panel-agent-detail');
    detail.style.display = 'block';
    detail.classList.add('active-grid');

    // Reset
    document.getElementById('agp-name').textContent = agentId;
    document.getElementById('agp-role').textContent = 'Loading...';
    document.getElementById('agp-model').textContent = '';
    document.getElementById('agp-stats').innerHTML = '<span class="empty">Loading...</span>';

    try {
        const res = await fetch(`/api/agents/${agentId}`);
        const d = await res.json();
        if (d.error) {
            document.getElementById('agp-stats').innerHTML = `<span class="empty">Error: ${esc(d.error)}</span>`;
            return;
        }

        // ── Hero Profile ──────────────────────────────────────────
        const firstName = (d.display_name || agentId).split(' ')[0];
        const tuning = d.tuning || {};
        const freqColor = typeof lpColor === 'function' && tuning.frequency
            ? lpColor(tuning.frequency) : 'var(--accent)';

        // Build hero section
        const heroEl = document.getElementById('agp-hero');
        if (heroEl) {
            const agentBadge = lpAgentBadgeHTML(agentId);
            const tuningKeyText = d.tuning_key || '';
            const personaLines = d.persona
                ? d.persona.split('\n')
                    .filter(l => l.trim())
                    .filter(l => !l.startsWith('#'))
                    .filter(l => !/\*\*[A-Za-z\s]+:\*\*/.test(l))
                    .filter(l => !/^-{2,}$/.test(l.trim()))
                    .slice(0, 2).join(' ')
                : '';

            // Latest echo for clickable freq badge
            const latestEcho = (d.echoes && d.echoes.length) ? d.echoes[0] : null;
            const freqBadge = tuning.frequency && typeof lpFreqBadgeHTML === 'function'
                ? lpFreqBadgeHTML(tuning.frequency) : '';
            const freqClick = latestEcho && latestEcho.id
                ? `onclick="showEchoDetail(${latestEcho.id})" style="cursor:pointer;" title="View today's tuning"`
                : '';

            heroEl.innerHTML = `
                <div class="agp-hero-top">
                    <img class="agp-hero-avatar" src="${avatarPath(d.display_name || agentId)}" alt="${esc(firstName)}"
                         onerror="this.style.display='none'">
                    <div class="agp-hero-info">
                        <div class="agp-hero-name-row">
                            <span class="agp-hero-badge">${agentBadge}</span>
                            <span class="agp-hero-name">${esc(firstName)}</span>
                        </div>
                        <div class="agp-hero-archetype">${esc(d.archetype || '')}</div>
                        ${freqBadge ? `<div class="agp-hero-freq" ${freqClick}>${freqBadge}</div>` : ''}
                        ${personaLines ? `<div class="agp-hero-persona">${esc(personaLines)}</div>` : ''}
                    </div>
                </div>
                ${tuningKeyText ? `<div class="agp-hero-key">
                    <span class="agp-layer-label">Archetype Key</span>
                    <div class="agp-key-text">${esc(tuningKeyText)}</div>
                </div>` : ''}`;
            heroEl.style.display = '';
        }

        // Hide old header elements (kept for backward compat, but hero replaces them)
        const oldName = document.getElementById('agp-name');
        const oldRole = document.getElementById('agp-role');
        const oldModel = document.getElementById('agp-model');
        const oldAvatar = document.getElementById('agp-avatar');
        const oldPersona = document.getElementById('agp-persona');
        const oldBadge = document.getElementById('agp-badge');
        if (oldName) oldName.style.display = 'none';
        if (oldRole) oldRole.style.display = 'none';
        if (oldModel) oldModel.style.display = 'none';
        if (oldAvatar) oldAvatar.style.display = 'none';
        if (oldPersona) oldPersona.style.display = 'none';
        if (oldBadge) oldBadge.style.display = 'none';

        // ── Stats (model info moved here) ────────────────────────
        let statsHtml = '';
        statsHtml += `<div class="agp-stat-row"><span class="agp-stat-label">ID</span><span>${esc(agentId)}</span></div>`;
        statsHtml += `<div class="agp-stat-row"><span class="agp-stat-label">Status</span><span>${statusDotHTML(d.status)} ${esc(d.status || 'active')}</span></div>`;
        statsHtml += `<div class="agp-stat-row"><span class="agp-stat-label">Working model</span><span id="agp-model-primary-slot" style="word-break:break-all;">${esc(d.model || '')}</span></div>`;
        statsHtml += `<div class="agp-stat-row"><span class="agp-stat-label">↳ fallback</span><span id="agp-model-fallback-slot" style="word-break:break-all;">${esc(d.fallback || '')}</span></div>`;
        statsHtml += `<div class="agp-stat-row"><span class="agp-stat-label">Tuning model</span><span id="agp-tuning-primary-slot" style="word-break:break-all;"></span></div>`;
        statsHtml += `<div class="agp-stat-row"><span class="agp-stat-label">↳ fallback</span><span id="agp-tuning-fallback-slot" style="word-break:break-all;"></span></div>`;
        statsHtml += `<div class="agp-stat-row"><span class="agp-stat-label">Echo Count</span><span>${tuning.echo_count || 0}</span></div>`;
        if (tuning.last_tuned_at) {
            statsHtml += `<div class="agp-stat-row"><span class="agp-stat-label">Last Tuned</span><span>${formatDate(tuning.last_tuned_at)}</span></div>`;
        }
        if (d.can_delegate_to && d.can_delegate_to.length) {
            statsHtml += `<div class="agp-stat-row"><span class="agp-stat-label">Delegates to</span><span>${d.can_delegate_to.map(esc).join(', ')}</span></div>`;
        }
        document.getElementById('agp-stats').innerHTML = statsHtml;

        // Replace model text with working + tuning dropdowns (async, non-blocking)
        _loadModelDropdowns(agentId);

        // JouleWork stats
        const jw = d.jw_stats || {};
        let jwHtml = '';
        jwHtml += `<div class="agp-stat-row"><span class="agp-stat-label">Total Calls</span><span>${jw.total_calls || 0}</span></div>`;
        jwHtml += `<div class="agp-stat-row"><span class="agp-stat-label">Total Tokens</span><span>${(jw.total_tokens || 0).toLocaleString()}</span></div>`;
        jwHtml += `<div class="agp-stat-row"><span class="agp-stat-label">Avg Duration</span><span>${jw.avg_duration ? Math.round(jw.avg_duration) + 'ms' : '—'}</span></div>`;
        jwHtml += `<div class="agp-stat-row"><span class="agp-stat-label">Success Rate</span><span style="color:var(--green);">${jw.success_rate != null ? (jw.success_rate * 100).toFixed(1) + '%' : '—'}</span></div>`;
        document.getElementById('agp-jw').innerHTML = jwHtml;

        // Boundaries
        const boundEl = document.getElementById('agp-boundaries');
        if (d.boundaries && d.boundaries.length) {
            boundEl.innerHTML = d.boundaries.map(b =>
                `<div style="padding:4px 0;border-bottom:1px solid var(--border);font-size:0.82rem;color:var(--dim);">${esc(b)}</div>`
            ).join('');
        } else {
            boundEl.innerHTML = '<span class="empty">No boundaries defined</span>';
        }

        // Echoes
        const echoEl = document.getElementById('agp-echoes');
        if (d.echoes && d.echoes.length) {
            let tbl = '<table><tr><th>#</th><th>Type</th><th>Frequency</th><th>Principle</th><th>L(E)</th><th>When</th></tr>';
            d.echoes.forEach(e => {
                // Show '—' when null or 0 (0 = self-tuned, no audio data from LT)
                const leVal = e.love_equation;
                const eq = (leVal != null && leVal !== 0) ? Number(leVal).toFixed(3) : '—';
                const eqColor = (leVal != null && leVal < 0) ? 'var(--red)' : 'var(--green)';
                const when = e.tuned_at ? formatDateOnly(e.tuned_at) : '';
                const isLT = e.echo_type === 'LT-guided';
                const typeBadge = isLT
                    ? '<span style="background:var(--green);color:#000;padding:1px 6px;border-radius:3px;font-size:0.7rem;">LT</span>'
                    : '<span style="background:var(--border);color:var(--dim);padding:1px 6px;border-radius:3px;font-size:0.7rem;">Self</span>';
                const clickable = e.id ? `onclick="showEchoDetail(${e.id})" style="cursor:pointer;"` : '';
                tbl += `<tr ${clickable}>
                    <td style="color:var(--dim);">${e.echo_num || ''}</td>
                    <td>${typeBadge}</td>
                    <td>${typeof lpFreqBadgeHTML === 'function' ? lpFreqBadgeHTML(e.frequency || '') : `<span class="freq-badge">${esc(e.frequency || '')}</span>`}</td>
                    <td style="color:${typeof lpColor === 'function' ? lpColor(e.frequency || '') : 'inherit'};">${esc(e.principle || '')}</td>
                    <td style="color:${eqColor};">${eq}</td>
                    <td style="color:var(--dim);">${when}</td>
                </tr>`;
            });
            tbl += '</table>';
            echoEl.innerHTML = tbl;
        } else {
            echoEl.innerHTML = '<span class="empty">No echoes yet</span>';
        }

        // Pending tasks for this agent
        loadAgentTasks(agentId);
        // #D22: read-only activity feed (queue, last turn, today's echo, delegations)
        loadAgentActivity(agentId);

        // Tools
        loadAgentTools(agentId);

        // Channels
        const chanEl = document.getElementById('agp-channels');
        if (d.channels && d.channels.length) {
            chanEl.innerHTML = d.channels.map(c =>
                `<div style="padding:6px 0;border-bottom:1px solid var(--border);font-size:0.82rem;">
                    <strong style="color:var(--accent);">${esc(c.name)}</strong>
                    <span style="color:var(--dim);margin-left:8px;">${esc(c.description || '')}</span>
                </div>`
            ).join('');
        } else {
            chanEl.innerHTML = '<span class="empty">No channels configured</span>';
        }

    } catch (e) {
        document.getElementById('agp-stats').innerHTML = `<span class="empty">Error: ${esc(e.message)}</span>`;
    }
}

async function loadAgentTasks(agentId) {
    const el = document.getElementById('agp-tasks');
    try {
        const res = await fetch(`/api/tasks?assignee=${encodeURIComponent(agentId)}&limit=20`);
        const data = await res.json();
        const tasks = data.tasks || [];
        if (!tasks.length) {
            el.innerHTML = '<span class="empty">No pending tasks</span>';
            return;
        }
        const statusColors = {
            'pending': 'var(--dim)', 'in_progress': 'var(--yellow)',
            'blocked': 'var(--red)', 'review': 'var(--purple)', 'done': 'var(--green)',
        };
        const priClassMap = { 'urgent': 'p1', 'high': 'p2', 'normal': 'p3', 'low': 'p4' };
        const priLabelMap = { 'urgent': 'URG', 'high': 'HIGH', 'normal': 'NORM', 'low': 'LOW' };
        el.innerHTML = tasks.map(t => {
            const dotColor = statusColors[t.status] || 'var(--dim)';
            const priClass = priClassMap[t.priority] || 'p4';
            const priLabel = priLabelMap[t.priority] || 'LOW';
            const projLink = t.project_id
                ? `<span class="link-like" onclick="showProjectDetail(${t.project_id})" style="color:var(--accent);cursor:pointer;">P-${t.project_id}</span>`
                : '';
            return `<div class="task-item">
                <span class="task-status-dot" style="background:${dotColor};" title="${esc(t.status || '')}"></span>
                <div class="task-info">
                    <div class="task-title">${esc(t.title || '')}</div>
                    <div class="task-meta">
                        <span>${esc(t.status || 'pending')}</span>
                        <span class="priority-badge ${priClass}" style="padding:1px 5px;">${priLabel}</span>
                        ${projLink}
                    </div>
                </div>
            </div>`;
        }).join('');
    } catch (e) {
        el.innerHTML = `<span class="empty">Error: ${esc(e.message)}</span>`;
    }
}

// #D22: read-only activity feed for a build-team agent — assembled from existing
// tables by /api/agents/{id}/activity. Gives the team LEGIBILITY without a chat tab.
async function loadAgentActivity(agentId) {
    const el = document.getElementById('agp-activity');
    if (!el) return;
    try {
        const res = await fetch(`/api/agents/${encodeURIComponent(agentId)}/activity`);
        const d = await res.json();
        const row = (label, value) =>
            `<div style="display:flex;justify-content:space-between;gap:10px;padding:5px 0;border-bottom:1px solid var(--border);font-size:0.82rem;">
                <span style="color:var(--dim);">${esc(label)}</span><span style="text-align:right;">${value}</span></div>`;
        const chip = (text, color) =>
            `<span style="display:inline-block;padding:1px 7px;border-radius:9px;font-size:0.68rem;background:${color || 'var(--border)'};color:#0a0a0f;font-weight:700;">${esc(text)}</span>`;
        const fmtWhen = (s) => s ? String(s).replace('T', ' ').slice(0, 16) : '—';
        let html = '';

        // Last turn + today's echo
        const lt = d.last_turn;
        html += row('Last turn', lt ? `${fmtWhen(lt.at)} · ${lt.steps ?? 0} steps` : '<span class="empty">no turns yet</span>');
        const e = d.echo_today;
        html += row("Today's echo", e
            ? `${esc(e.frequency || '')} · β=${(e.love_equation != null ? Number(e.love_equation).toFixed(2) : '—')}`
            : chip('not tuned today', 'var(--yellow)'));

        // Assigned queue items
        if (d.queue && d.queue.length) {
            html += '<div style="margin-top:8px;color:var(--dim);font-size:0.72rem;text-transform:uppercase;letter-spacing:.06em;">Queue</div>';
            html += d.queue.map(q =>
                `<div style="padding:4px 0;font-size:0.82rem;">${esc(q.source || '')} ${esc(q.title || '')} ${chip(q.status, 'var(--purple)')}${q.pr_url ? ` <a href="${esc(q.pr_url)}" target="_blank">PR</a>` : ''}</div>`
            ).join('');
        }

        // Current tasks
        if (d.tasks && d.tasks.length) {
            html += '<div style="margin-top:8px;color:var(--dim);font-size:0.72rem;text-transform:uppercase;letter-spacing:.06em;">Tasks</div>';
            html += d.tasks.map(t =>
                `<div style="padding:4px 0;font-size:0.82rem;">${esc(t.title || '')} ${chip(t.status, 'var(--yellow)')}</div>`
            ).join('');
        }

        // Delegations (brief received + report-back)
        if (d.delegations && d.delegations.length) {
            const phaseColor = { replied: 'var(--green)', failed: 'var(--red)', dispatched: 'var(--yellow)' };
            html += '<div style="margin-top:8px;color:var(--dim);font-size:0.72rem;text-transform:uppercase;letter-spacing:.06em;">Delegations</div>';
            html += d.delegations.map(x =>
                `<div style="padding:5px 0;font-size:0.82rem;border-bottom:1px solid var(--border);">
                    <div>${chip(x.phase, phaseColor[x.phase])} ${esc(x.brief || '')}</div>
                    ${x.report_back ? `<div style="color:var(--dim);margin-top:3px;font-size:0.76rem;">${esc(x.report_back)}</div>` : ''}
                </div>`
            ).join('');
        }

        el.innerHTML = html || '<span class="empty">No recent activity</span>';
    } catch (e) {
        el.innerHTML = `<span class="empty">Error: ${esc(e.message)}</span>`;
    }
}

// =============================================================================
// Agent Tools Registry
// =============================================================================
// Each agent can have tools — specialized interfaces they own.
// Tools are defined here for now; will move to agent.yaml config later.
// Format: agentId → [{ name, description, url, icon }]

const AGENT_TOOLS = {
    'julian': [
        {
            name: 'jules',
            description: 'Voice transcription — tap, talk, save to vault',
            // Served by this Cove's MC at /jules (origin-relative — works on any Cove,
            // mesh or domain). No hardcoded founder voice host (#205-voice/#203).
            url: `${location.origin}/jules`,
            icon: '🎙'
        }
    ]
};

function loadAgentTools(agentId) {
    const el = document.getElementById('agp-tools');
    const resolvedId = typeof _resolveAgentId === 'function' ? _resolveAgentId(agentId) : agentId.toLowerCase();
    const tools = AGENT_TOOLS[resolvedId] || [];

    if (!tools.length) {
        el.innerHTML = '<span class="empty">No tools yet</span>';
        return;
    }

    const ncUser = MC.presence?.nc_username || '';
    el.innerHTML = tools.map(t => {
        let toolUrl = t.url || '';
        // Append ?presence={nc_username} for per-Presence routing (e.g. jules → NC inbox)
        if (toolUrl && ncUser) {
            toolUrl += (toolUrl.includes('?') ? '&' : '?') + 'presence=' + encodeURIComponent(ncUser);
        }
        return `
        <div class="tool-item" style="display:flex;align-items:center;gap:12px;padding:10px 0;border-bottom:1px solid var(--border);">
            <span style="font-size:1.4rem;flex-shrink:0;">${t.icon || '🔧'}</span>
            <div style="flex:1;min-width:0;">
                <div style="font-weight:600;font-size:0.88rem;color:var(--text);">${esc(t.name)}</div>
                <div style="font-size:0.75rem;color:var(--dim);margin-top:2px;">${esc(t.description)}</div>
            </div>
            ${toolUrl ? `<a href="${esc(toolUrl)}" target="_blank" rel="noopener"
                style="flex-shrink:0;padding:6px 14px;border:1px solid var(--accent);border-radius:6px;color:var(--accent);font-size:0.78rem;text-decoration:none;white-space:nowrap;"
                >Launch →</a>` : ''}
        </div>`;
    }).join('');
}

function backToOverview() {
    // Clear all panels
    document.querySelectorAll('.panel').forEach(p => {
        p.classList.remove('active-grid', 'active-flex');
        p.style.display = '';
    });
    // Return to Team tab (agents live there now)
    activeTab = 'team';
    document.querySelectorAll('.tab').forEach(t => {
        t.classList.toggle('active', t.dataset.tab === 'team');
    });
    const teamPanel = document.getElementById('panel-team');
    teamPanel.classList.add('active-grid');
    loadTeam();
    if (otAudio && otAudio.src) showMiniPlayer();
}

async function loadOverviewTuning() {
    const el = document.getElementById('overviewTuning');
    if (!el) return;  // element absent on this view (e.g. Cove-admin apex) — don't crash
    const openLink = document.getElementById('openTuningLink');
    try {
        // All tiers use /api/tuning/operator — single endpoint, flat response shape.
        // Cache-bust: v= on deploy, d= daily
        const _ovCB = `v=${window._buildVersion || ''}&d=${new Date().toISOString().slice(0,10)}`;
        const res = await fetch('/api/tuning/operator?' + _ovCB);
        const data = await res.json();
        if (data.error) {
            el.innerHTML = `<span class="empty">${esc(data.error)}</span>`;
            return;
        }

        if (!data.has_tuning) {
            el.innerHTML = `
                <div class="tuning-block">
                    <div class="tuning-label">Status</div>
                    <div class="tuning-value">${esc(data.note || 'No tuning available yet')}</div>
                </div>`;
            return;
        }

        const freq = data.frequency || '--';
        const principle = data.principle || '--';
        const signalType = (data.signal_type || '').replace(/_/g, ' ');

        // Show the "Open Tuning" link
        if (openLink) openLink.style.display = 'inline';

        const fColor = typeof lpColor === 'function' ? lpColor(freq) : '#5ce1e6';
        const sColor = typeof lpSignalColor === 'function' ? lpSignalColor(data.signal_type) : null;
        // Set daily frequency color as CSS custom properties — global signal delivery
        // --accent drives all UI chrome (tabs, buttons, links) so it shifts with the tuning
        document.documentElement.style.setProperty('--accent', fColor);
        document.documentElement.style.setProperty('--daily-freq', fColor);
        document.documentElement.style.setProperty('--daily-freq-glow', fColor + '66');
        document.documentElement.style.setProperty('--daily-freq-subtle', fColor + '20');
        document.documentElement.style.setProperty('--daily-freq-border', fColor + '35');
        // Set daily signal type color — secondary ambient mood layer
        // Signal = background feel, Frequency = foreground presence
        const sigColor = sColor || fColor;
        document.documentElement.style.setProperty('--daily-signal', sigColor);
        document.documentElement.style.setProperty('--daily-signal-glow', sigColor + '4D');
        document.documentElement.style.setProperty('--daily-signal-subtle', sigColor + '14');
        document.documentElement.style.setProperty('--daily-signal-border', sigColor + '2E');
        el.innerHTML = `
            <div class="tuning-block">
                <div class="tuning-label">Frequency</div>
                <div class="tuning-value freq" style="color:${fColor};">${esc(String(freq))}</div>
            </div>
            <div class="tuning-block tuning-tappable" onclick="_openHomeLyrics('${esc(String(principle))}', '${fColor}')">
                <div class="tuning-label">Principle</div>
                <div class="tuning-value" style="color:${fColor};">${esc(String(principle))} <span class="tuning-tap-hint">tap for lyrics</span></div>
            </div>
            <div class="tuning-block">
                <div class="tuning-label">Tuning Key</div>
                <div class="tuning-value" style="color:${fColor};">${esc(String(data.tuning_key || '--'))}</div>
            </div>
            ${signalType ? `<div class="tuning-block">
                <div class="tuning-label">Signal</div>
                <div class="tuning-value"${sColor ? ` style="color:${sColor};"` : ''}>${esc(signalType)}</div>
            </div>` : ''}`;

        // Tune + Playlists links in header row (all tiers — cove-core level)
        // Same panels/scripts at every tier — just different nav entry points
        const hdr = document.getElementById('tuningHeaderLinks');
        if (hdr) {
            hdr.innerHTML = `
                <a href="#" class="tuning-link" style="color:${fColor};" onclick="switchToTab('tune'); return false;">Tune</a>
                <span class="tuning-link-sep">&middot;</span>
                <a href="#" class="tuning-link" style="color:${fColor};" onclick="switchToTab('playlists'); return false;">Playlists</a>`;
        }
        // Mirror content or setup prompt
        if (MC.features?.mirror) {
            // Mirrors enabled — show them
            _loadHomeMirror(el, fColor);
        } else if (!MC.features?.mirror_prompt_dismissed) {
            // No mirrors, not dismissed — show setup prompt
            _renderMirrorPrompt(el, fColor);
        }
    } catch (e) {
        el.innerHTML = `<span class="empty">Could not load tuning</span>`;
    }
}

async function _loadHomeMirror(parentEl, freqColor) {
    try {
        // Pass user's enabled mirrors so backend knows which to load
        // Cache-bust: v= changes on deploy (new YAML), d= changes daily (new tuning)
        const mirrorSources = MC.features?.mirror_sources || MC.features?.mirror_source || '';
        const cacheBust = `v=${window._buildVersion || ''}&d=${new Date().toISOString().slice(0,10)}`;
        const mirrorParam = mirrorSources
            ? `?sources=${encodeURIComponent(mirrorSources)}&${cacheBust}`
            : `?${cacheBust}`;
        const res = await fetch('/api/mirrors/today' + mirrorParam);
        const data = await res.json();
        if (!data.has_mirror) return;

        // Multi-mirror: stack each enabled mirror
        const mirrors = data.mirrors || [data]; // backward compat
        mirrors.forEach((m, idx) => {
            const featured = m.featured;
            if (!featured) return;

            const mirrorDiv = document.createElement('div');
            mirrorDiv.className = 'home-mirror';
            mirrorDiv.style.borderLeftColor = freqColor + '50';

            if (m.mirror_type === 'music') {
                // Music mirror — lyric quote + artist/title + Listen CTA
                const artist = featured.artist || '';
                const title = featured.title || '';
                const lyric = featured.text || '';
                const freq = data.frequency || '';
                const spotifyId = featured.spotify_id || '';
                const youtubeId = featured.youtube_id || '';

                mirrorDiv.innerHTML = `
                    <div class="home-mirror-header">
                        <span class="home-mirror-name">${esc(m.mirror_name)}</span>
                        <a href="#" class="home-mirror-reflect" style="color:${freqColor};" onclick="_openMusicPlayer('${_escAttr(freq)}', '${_escAttr(artist)}', '${_escAttr(title)}', '${_escAttr(spotifyId)}', '${_escAttr(youtubeId)}'); return false;">Listen &rarr;</a>
                    </div>
                    <div class="home-mirror-text" style="font-style:italic;">${esc(lyric)}</div>
                    <span class="home-mirror-ref" style="color:${freqColor};">${esc(artist)} &mdash; ${esc(title)}</span>`;
            } else {
                // Text mirror (scripture, etc.)
                mirrorDiv.innerHTML = `
                    <div class="home-mirror-header">
                        <span class="home-mirror-name">${esc(m.mirror_name)}</span>
                        ${m.entry_count > 1 ? `<a href="#" class="home-mirror-reflect" style="color:${freqColor};" onclick="_openHomeReflect(${idx}); return false;">Reflect &rarr;</a>` : ''}
                    </div>
                    <div class="home-mirror-text">${esc(featured.text)}</div>
                    <span class="home-mirror-ref" style="color:${freqColor};">${esc(featured.ref)}</span>`;
            }

            parentEl.appendChild(mirrorDiv);
        });

        // Store for Reflect modal and music player (all mirrors)
        window._homeMirrorData = data;
        window._homeMirrorMirrors = mirrors;
        window._homeMirrorColor = freqColor;
    } catch (e) {
        console.warn('[mirror] Home mirror load failed:', e.message);
    }
}

// ── Streaming service (home card) ──────────────────────────────────────────

async function _setStreamingServiceHome(value) {
    MC.features = MC.features || {};
    MC.features.streaming_service = value;
    try {
        await fetch('/api/settings/features', {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ streaming_service: value }),
        });
    } catch (e) {
        console.warn('[streaming] Save failed:', e.message);
    }
}

// ── Mirror setup prompt (dismissable) ──────────────────────────────────────

function _renderMirrorPrompt(parentEl, freqColor) {
    const prompt = document.createElement('div');
    prompt.className = 'mirror-prompt';
    prompt.id = 'mirrorSetupPrompt';
    prompt.innerHTML = `
        <div class="mirror-prompt-content">
            <a href="#" class="mirror-prompt-link" style="color:${freqColor};" onclick="_openMirrorSetupModal('${freqColor}'); return false;">
                &#9734; Add a Tuning Mirror
            </a>
            <span class="mirror-prompt-desc">See a daily reflection alongside your tuning</span>
        </div>
        <button class="mirror-prompt-dismiss" onclick="_dismissMirrorPrompt()" title="Dismiss">&times;</button>`;
    parentEl.appendChild(prompt);
}

async function _dismissMirrorPrompt() {
    const el = document.getElementById('mirrorSetupPrompt');
    if (el) el.remove();
    MC.features = MC.features || {};
    MC.features.mirror_prompt_dismissed = true;
    try {
        await fetch('/api/settings/features', {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mirror_prompt_dismissed: true }),
        });
    } catch (e) {
        console.warn('[mirror] Failed to save dismiss:', e.message);
    }
}

async function _openMirrorSetupModal(freqColor) {
    window._homeMirrorColor = freqColor;

    // Fetch registry (uses settings.js cache if loaded, else fetch directly)
    let registry = [];
    try {
        if (typeof _getMirrorRegistry === 'function') {
            registry = await _getMirrorRegistry();
        } else {
            const res = await fetch('/api/mirrors/registry');
            const data = await res.json();
            registry = data.mirrors || [];
        }
    } catch (e) { console.warn('[overview] Mirror registry fetch failed:', e.message); }

    const currentSources = (MC.features?.mirror_sources || '').split(',').map(s => s.trim()).filter(Boolean);
    const curService = MC.features?.streaming_service || 'youtube';
    const hasMusicMirror = currentSources.some(id => { const m = registry.find(r => r.id === id); return m && m.type === 'music'; });

    const mirrorItems = registry.filter(m => m.available !== false).map(m => {
        const checked = currentSources.includes(m.id);
        return `<div class="mirror-setup-item">
            <div class="mirror-setup-info">
                <span class="mirror-setup-label">${m.name}</span>
                <span class="mirror-setup-desc">${m.description ? m.description.substring(0, 80) + (m.description.length > 80 ? '...' : '') : ''}</span>
            </div>
            <div class="mirror-setup-toggle" onclick="_toggleMirrorSetup('${m.id}', this)" data-mirror-id="${m.id}">
                <span class="mirror-toggle-track${checked ? ' active' : ''}" style="${checked ? 'background:' + freqColor : ''}">
                    <span class="mirror-toggle-thumb${checked ? ' active' : ''}"></span>
                </span>
            </div>
        </div>`;
    }).join('');

    // Create modal overlay
    const modal = document.createElement('div');
    modal.className = 'mirror-setup-modal-overlay';
    modal.id = 'mirrorSetupModal';
    modal.onclick = function(e) { if (e.target === modal) _closeMirrorSetupModal(); };
    modal.innerHTML = `
        <div class="mirror-setup-modal">
            <div class="mirror-setup-header">
                <span class="mirror-setup-title">Tuning Mirrors</span>
                <button class="mirror-setup-close" onclick="_closeMirrorSetupModal()">&times;</button>
            </div>
            <p class="mirror-setup-explain">A mirror reflects your daily tuning through another canon. Enable one or more below.</p>
            <div class="mirror-setup-list">${mirrorItems}</div>
            <div class="mirror-setup-streaming">
                <span class="mirror-setup-label">Music service</span>
                <span class="mirror-setup-desc" style="display:block;margin:2px 0 6px;">Where the Music mirror plays from.</span>
                <select class="tuning-streaming-select" onchange="_setStreamingServiceHome(this.value)">
                    <option value="youtube"${curService === 'youtube' ? ' selected' : ''}>YouTube Music</option>
                    <option value="spotify"${curService === 'spotify' ? ' selected' : ''}>Spotify</option>
                    <option value="apple"${curService === 'apple' ? ' selected' : ''}>Apple Music</option>
                </select>
            </div>
            <button class="mirror-setup-enable-btn" onclick="_enableMirrorsFromSetup()" style="width:100%;padding:12px;margin-top:12px;border:none;border-radius:8px;background:var(--accent);color:#fff;font-size:0.95rem;font-weight:600;cursor:pointer;">Enable Mirrors</button>
            <p class="mirror-setup-note">You can change these anytime in Settings.</p>
        </div>`;
    document.body.appendChild(modal);
}

function _closeMirrorSetupModal() {
    const modal = document.getElementById('mirrorSetupModal');
    if (modal) modal.remove();

    // Refresh the mirror section on overview to reflect any changes
    const tuningEl = document.getElementById('overviewTuning');
    if (tuningEl && MC.features?.mirror) {
        // Mirrors are enabled — reload them
        const mirrorEls = tuningEl.querySelectorAll('.home-mirror-card, .mirror-prompt');
        mirrorEls.forEach(el => el.remove());
        const fColor = window._homeMirrorColor || 'var(--accent)';
        _loadHomeMirror(tuningEl, fColor);
    } else if (tuningEl && !MC.features?.mirror) {
        // All mirrors disabled — show prompt if not dismissed
        const mirrorEls = tuningEl.querySelectorAll('.home-mirror-card');
        mirrorEls.forEach(el => el.remove());
    }
}

async function _enableMirrorsFromSetup() {
    // Collect whatever sources are currently toggled on in the modal
    const modal = document.getElementById('mirrorSetupModal');
    if (!modal) return;

    const activeToggles = modal.querySelectorAll('.mirror-toggle-track.active');
    const sources = [];
    activeToggles.forEach(track => {
        const toggleDiv = track.closest('.mirror-setup-toggle');
        if (toggleDiv) sources.push(toggleDiv.dataset.mirrorId);
    });

    if (sources.length === 0) {
        // Nothing selected — populate defaults from registry
        try {
            let registry = [];
            if (typeof _getMirrorRegistry === 'function') {
                registry = await _getMirrorRegistry();
            } else {
                const res = await fetch('/api/mirrors/registry');
                const data = await res.json();
                registry = data.mirrors || [];
            }
            registry.filter(m => m.default).forEach(m => sources.push(m.id));
        } catch (e) { /* fallback below */ }
        if (sources.length === 0) sources.push('scripture-tpt', 'music-mirror');
    }

    const newSources = sources.join(',');
    MC.features = MC.features || {};
    MC.features.mirror = true;
    MC.features.mirror_sources = newSources;
    MC.features.mirror_library = newSources;
    MC.features.mirror_prompt_dismissed = true;

    try {
        await fetch('/api/settings/features', {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                mirror: true,
                mirror_sources: newSources,
                mirror_library: newSources,
                mirror_prompt_dismissed: true,
            }),
        });
    } catch (e) {
        console.warn('[mirror] Enable from setup failed:', e.message);
    }

    _closeMirrorSetupModal();
}

async function _toggleMirrorSetup(mirrorId, toggleEl) {
    const track = toggleEl.querySelector('.mirror-toggle-track');
    const thumb = toggleEl.querySelector('.mirror-toggle-thumb');
    const isActive = track.classList.contains('active');

    // Parse current sources
    let sources = (MC.features?.mirror_sources || '').split(',').map(s => s.trim()).filter(Boolean);

    if (isActive) {
        sources = sources.filter(s => s !== mirrorId);
        track.classList.remove('active');
        track.style.background = '';
        thumb.classList.remove('active');
    } else {
        if (!sources.includes(mirrorId)) sources.push(mirrorId);
        track.classList.add('active');
        track.style.background = 'var(--accent)';
        thumb.classList.add('active');
    }

    // Also update mirror_library (two-layer system)
    let library = (MC.features?.mirror_library || '').split(',').map(s => s.trim()).filter(Boolean);
    if (isActive) {
        // Removing — take out of library too
        library = library.filter(s => s !== mirrorId);
    } else {
        if (!library.includes(mirrorId)) library.push(mirrorId);
    }

    const newValue = sources.join(',');
    const newLibrary = library.join(',');
    const mirrorOn = sources.length > 0;

    MC.features = MC.features || {};
    MC.features.mirror_sources = newValue;
    MC.features.mirror_library = newLibrary;
    MC.features.mirror = mirrorOn;
    if (mirrorOn) MC.features.mirror_prompt_dismissed = true;

    try {
        const saveData = {
            mirror_sources: newValue,
            mirror_library: newLibrary,
            mirror: mirrorOn,
        };
        if (mirrorOn) saveData.mirror_prompt_dismissed = true;
        await fetch('/api/settings/features', {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(saveData),
        });
    } catch (e) {
        console.warn('[mirror] Toggle save failed:', e.message);
    }
}

// Escape for use inside single-quoted JS string attributes (onclick, etc.)
function _escAttr(s) {
    if (!s) return '';
    return s.replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/"/g, '&quot;');
}

function _openHomeReflect(mirrorIdx) {
    const data = window._homeMirrorData;
    const mirrors = window._homeMirrorMirrors;
    if (!data || !mirrors) return;
    const fc = window._homeMirrorColor || 'var(--accent)';

    // Use specific mirror if index provided, otherwise first
    const m = mirrors[mirrorIdx || 0] || mirrors[0];
    // Track reflect click
    _ovTrackEvent('mirror_reflect_click', { frequency: data.frequency || '', principle: data.principle || '', echo_name: m?.mirror_name || '' });
    if (!m) return;

    const modal = document.getElementById('reflectModal');
    if (!modal) return;

    document.getElementById('reflectTitle').textContent = data.principle;
    document.getElementById('reflectTitle').style.color = fc;
    document.getElementById('reflectCanon').textContent =
        m.mirror_name + ' — ' + (m.canon || '');

    document.getElementById('reflectBody').innerHTML = m.all_entries.map(e =>
        '<div class="reflect-entry" style="border-left-color:' + fc + '40;">' +
            '<div class="reflect-ref" style="color:' + fc + ';">' + esc(e.ref) + '</div>' +
            '<div class="reflect-passage">' + esc(e.text) + '</div>' +
            '<div class="reflect-thread">' + esc(e.thread) + '</div>' +
        '</div>'
    ).join('');

    modal.style.display = 'flex';
}

// ── Activity Tracking (fire-and-forget) ────────────────────────────────
function _ovTrackEvent(eventType, extra) {
    const body = Object.assign({ event_type: eventType }, extra || {});
    try {
        fetch('/api/tuning/event', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
    } catch (e) { /* silent */ }
}

// ── Music Mirror Player ─────────────────────────────────────────────────

// Playlist IDs per frequency per service — populated after playlists are created
// Format: { spotify: { peace: 'PLAYLIST_ID', ... }, youtube: { peace: 'PLAYLIST_ID', ... }, apple: { peace: 'PLAYLIST_ID', ... } }
var MUSIC_MIRROR_PLAYLISTS = {
    spotify: {},
    youtube: { peace: 'PL5H3zJAeU30PkndXHCdOj2i0baf5biSiN', clarity: 'PL5H3zJAeU30OiRi1E_IF8g_ON0ad-SAqv', momentum: 'PL5H3zJAeU30Pkvj-CTLh8E1HbgmKGhijx', trust: 'PL5H3zJAeU30OQhN7cyZVwJycL6C8TKbmV', joy: 'PL5H3zJAeU30OLN20Tjmz7wi9_IZVPdZt7', connection: 'PL5H3zJAeU30PXdOrSIrtFIAzrH-tHFozT', presence: 'PL5H3zJAeU30M39Zr0P-SYjJbcpI_SxH89', resilience: 'PL5H3zJAeU30PqlP7wvIixR8KSvGPmsfxM', courage: 'PL5H3zJAeU30NOchBxs6kI6ZoKxMh6hXCw', gratitude: 'PL5H3zJAeU30PHTbD_UlfQWgRYNy4N1vHK', release: 'PL5H3zJAeU30MwdK_4MIikD7j21ixmw8eN', integration: 'PL5H3zJAeU30PVrZCXFmK-ZLCE_f3zKnFR', boundary: 'PL5H3zJAeU30N7W42clfexKbEmHTKGf3L-' },
    apple: {},
};

function _openMusicPlayer(frequency, artist, title, spotifyId, youtubeId) {
    // Track mirror listen click
    _ovTrackEvent('mirror_listen_click', { frequency: frequency, echo_name: artist + ' - ' + title });
    // Default to YouTube so Listen plays immediately — no "choose a service" gate on
    // first listen. Users switch service from the player dropdown or in Settings.
    const service = MC.features?.streaming_service || 'youtube';
    _launchMusicModal(service, frequency, artist, title, spotifyId, youtubeId);
}

function _showStreamingPicker(frequency, artist, title, spotifyId, youtubeId) {
    let existing = document.getElementById('streaming-picker-overlay');
    if (existing) { existing.remove(); return; }

    const fc = window._homeMirrorColor || 'var(--accent)';
    const sid = _escAttr(spotifyId || '');
    const yid = _escAttr(youtubeId || '');

    const overlay = document.createElement('div');
    overlay.id = 'streaming-picker-overlay';
    overlay.className = 'upgrade-overlay';
    overlay.onclick = function(e) { if (e.target === overlay) overlay.remove(); };

    overlay.innerHTML =
        '<div class="upgrade-modal" style="max-width:360px;">' +
            '<button class="upgrade-close" onclick="document.getElementById(\'streaming-picker-overlay\').remove()">&times;</button>' +
            '<div class="upgrade-modal-icon" style="color:' + fc + '">' +
                '<svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="10"/><polygon points="10,8 16,12 10,16" fill="currentColor" stroke="none"/></svg>' +
            '</div>' +
            '<h2 class="upgrade-modal-title" style="font-size:1.1rem;">Choose Your Music Service</h2>' +
            '<p class="upgrade-modal-sub" style="margin-bottom:1.2rem;">Pick where you listen. You can change this anytime in Settings.</p>' +
            '<div style="display:flex;flex-direction:column;gap:8px;">' +
                '<button class="streaming-pick-btn" onclick="_selectStreamingService(\'youtube\',\'' + _escAttr(frequency) + '\',\'' + _escAttr(artist) + '\',\'' + _escAttr(title) + '\',\'' + sid + '\',\'' + yid + '\')" style="background:#FF0000;color:#fff;">' +
                    '<svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.546 12 3.546 12 3.546s-7.505 0-9.377.504A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.504 9.376.504 9.376.504s7.505 0 9.377-.504a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z"/></svg>' +
                    ' YouTube Music' +
                '</button>' +
                '<button class="streaming-pick-btn" onclick="_selectStreamingService(\'spotify\',\'' + _escAttr(frequency) + '\',\'' + _escAttr(artist) + '\',\'' + _escAttr(title) + '\',\'' + sid + '\',\'' + yid + '\')" style="background:#1DB954;color:#fff;">' +
                    '<svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M12 0C5.4 0 0 5.4 0 12s5.4 12 12 12 12-5.4 12-12S18.66 0 12 0zm5.521 17.34c-.24.359-.66.48-1.021.24-2.82-1.74-6.36-2.101-10.561-1.141-.418.122-.779-.179-.899-.539-.12-.421.18-.78.54-.9 4.56-1.021 8.52-.6 11.64 1.32.42.18.479.659.301 1.02zm1.44-3.3c-.301.42-.841.6-1.262.3-3.239-1.98-8.159-2.58-11.939-1.38-.479.12-1.02-.12-1.14-.6-.12-.48.12-1.021.6-1.141C9.6 9.9 15 10.561 18.72 12.84c.361.181.54.78.241 1.2zm.12-3.36C15.24 8.4 8.82 8.16 5.16 9.301c-.6.179-1.2-.181-1.38-.721-.18-.601.18-1.2.72-1.381C8.64 5.801 15.6 6.081 20.1 8.82c.54.3.72 1.02.42 1.56-.299.421-1.02.599-1.439.3z"/></svg>' +
                    ' Spotify' +
                '</button>' +
                '<button class="streaming-pick-btn" onclick="_selectStreamingService(\'apple\',\'' + _escAttr(frequency) + '\',\'' + _escAttr(artist) + '\',\'' + _escAttr(title) + '\',\'' + sid + '\',\'' + yid + '\')" style="background:linear-gradient(135deg,#fc3c44,#f94c57);color:#fff;">' +
                    '<svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M23.994 6.124a9.23 9.23 0 0 0-.24-2.19c-.317-1.31-1.062-2.31-2.18-3.043A5.022 5.022 0 0 0 19.6.28C18.98.164 18.348.094 17.712.053 17.38.03 17.048.019 16.716.013 16.414 0 16.112 0 15.81 0H8.19c-.302 0-.604 0-.906.013a44.27 44.27 0 0 0-1.236.053C5.01.17 4.02.48 3.2 1.27 2.327 2.112 1.87 3.133 1.68 4.3a10.47 10.47 0 0 0-.152 1.822C1.508 6.5 1.5 6.878 1.5 7.256v9.488c0 .378.008.756.028 1.134.04.74.13 1.476.32 2.192.317 1.193.967 2.167 1.98 2.882A5.05 5.05 0 0 0 5.88 23.72c.62.116 1.252.186 1.888.227.372.024.744.037 1.116.047.302.006.604.006.906.006h7.62c.302 0 .604 0 .906-.006a44.27 44.27 0 0 0 1.236-.053c1.038-.104 2.028-.414 2.848-1.204.873-.842 1.33-1.863 1.52-3.03.087-.522.132-1.05.152-1.582.02-.378.028-.756.028-1.134V7.256c0-.378-.008-.756-.028-1.134l-.002.002zM16.752 13.022l-4.632 2.796c-1.128.684-2.52.024-2.52-1.2V9.384c0-1.224 1.392-1.884 2.52-1.2l4.632 2.796c1.128.682 1.128 1.718 0 2.042z"/></svg>' +
                    ' Apple Music' +
                '</button>' +
            '</div>' +
        '</div>';

    document.body.appendChild(overlay);
}

async function _selectStreamingService(service, frequency, artist, title, spotifyId, youtubeId) {
    // Track service selection
    _ovTrackEvent('service_selection', { play_source: service, frequency: frequency });
    try {
        await fetch('/api/settings/features', {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ streaming_service: service }),
        });
        if (MC.features) MC.features.streaming_service = service;
    } catch(e) {
        console.warn('[music] Failed to save streaming preference:', e);
    }

    const picker = document.getElementById('streaming-picker-overlay');
    if (picker) picker.remove();

    _launchMusicModal(service, frequency, artist, title, spotifyId, youtubeId);
}

function _launchMusicModal(service, frequency, artist, title, spotifyId, youtubeId) {
    // Track streaming modal open
    _ovTrackEvent('streaming_modal_open', { play_source: service, frequency: frequency, echo_name: artist + ' - ' + title });
    let existing = document.getElementById('music-player-overlay');
    if (existing) existing.remove();

    const fc = window._homeMirrorColor || 'var(--accent)';
    const freqDisplay = frequency.charAt(0).toUpperCase() + frequency.slice(1);
    const freqKey = frequency.toLowerCase().replace(/\s+/g, '_');
    const playlistId = MUSIC_MIRROR_PLAYLISTS[service]?.[freqKey] || '';

    // Service label
    var serviceLabels = { spotify: 'Spotify', youtube: 'YouTube Music', apple: 'Apple Music' };
    var serviceLabel = serviceLabels[service] || service;

    // Build the main embed — track-first if we have a track ID
    var mainEmbed = '';
    var playlistLink = '';

    if (service === 'spotify') {
        if (spotifyId) {
            mainEmbed = '<iframe style="border-radius:12px;border:none;" src="https://open.spotify.com/embed/track/' + spotifyId + '?utm_source=generator&theme=0" width="100%" height="152" allowfullscreen allow="autoplay; clipboard-write; encrypted-media; fullscreen; picture-in-picture" loading="lazy"></iframe>';
            if (playlistId) {
                playlistLink = '<a href="#" style="display:block;text-align:center;margin-top:10px;font-size:0.75rem;color:' + fc + ';text-decoration:none;letter-spacing:0.05em;" onclick="_swapToPlaylist(\'' + _escAttr(service) + '\',\'' + playlistId + '\',\'' + _escAttr(frequency) + '\',\'' + _escAttr(artist) + '\',\'' + _escAttr(title) + '\',\'' + _escAttr(spotifyId || '') + '\'); return false;">Full Playlist &rarr;</a>';
            }
        } else if (playlistId) {
            mainEmbed = '<iframe style="border-radius:12px;border:none;" src="https://open.spotify.com/embed/playlist/' + playlistId + '?utm_source=generator&theme=0" width="100%" height="420" allowfullscreen allow="autoplay; clipboard-write; encrypted-media; fullscreen; picture-in-picture" loading="lazy"></iframe>';
        } else {
            mainEmbed = '<div style="text-align:center;padding:40px 20px;color:var(--dim);font-size:0.85rem;">Spotify playlists coming soon.<br>Search for <strong>' + esc(artist) + ' &mdash; ' + esc(title) + '</strong></div>';
        }
    } else if (service === 'youtube') {
        if (youtubeId && playlistId) {
            // Start on the mirror track, then continue with the playlist
            mainEmbed = '<iframe width="100%" height="420" src="https://www.youtube.com/embed/' + youtubeId + '?list=' + playlistId + '&autoplay=1" frameborder="0" allow="autoplay; encrypted-media" allowfullscreen style="border-radius:12px;border:none;"></iframe>';
        } else if (youtubeId) {
            // Single track, no playlist
            mainEmbed = '<iframe width="100%" height="420" src="https://www.youtube.com/embed/' + youtubeId + '?autoplay=1" frameborder="0" allow="autoplay; encrypted-media" allowfullscreen style="border-radius:12px;border:none;"></iframe>';
        } else if (playlistId) {
            // Playlist only, no specific track
            mainEmbed = '<iframe width="100%" height="420" src="https://www.youtube.com/embed/videoseries?list=' + playlistId + '&autoplay=1" frameborder="0" allow="autoplay; encrypted-media" allowfullscreen style="border-radius:12px;border:none;"></iframe>';
        } else {
            mainEmbed = '<div style="text-align:center;padding:40px 20px;color:var(--dim);font-size:0.85rem;">YouTube Music playlist coming soon.<br>Search for <strong>' + esc(artist) + ' &mdash; ' + esc(title) + '</strong></div>';
        }
    } else if (service === 'apple') {
        if (playlistId) {
            mainEmbed = '<iframe allow="autoplay *; encrypted-media *; fullscreen *; clipboard-write" frameborder="0" height="450" style="width:100%;overflow:hidden;border-radius:12px;border:none;" sandbox="allow-forms allow-popups allow-same-origin allow-scripts allow-storage-access-by-user-activation allow-top-navigation-by-user-activation" src="https://embed.music.apple.com/us/playlist/' + playlistId + '?theme=dark"></iframe>';
        } else {
            mainEmbed = '<div style="text-align:center;padding:40px 20px;color:var(--dim);font-size:0.85rem;">Apple Music playlist coming soon.<br>Search for <strong>' + esc(artist) + ' &mdash; ' + esc(title) + '</strong></div>';
        }
    }

    const overlay = document.createElement('div');
    overlay.id = 'music-player-overlay';
    overlay.className = 'upgrade-overlay';
    overlay.onclick = function(e) { if (e.target === overlay) overlay.remove(); };

    overlay.innerHTML =
        '<div class="upgrade-modal" style="max-width:420px;padding:20px;">' +
            '<button class="upgrade-close" onclick="document.getElementById(\'music-player-overlay\').remove()">&times;</button>' +
            '<div style="text-align:center;margin-bottom:12px;">' +
                '<div style="font-size:0.7rem;letter-spacing:0.1em;color:var(--muted);text-transform:uppercase;margin-bottom:4px;">' + esc(freqDisplay) + ' Frequency</div>' +
                '<div style="font-size:1rem;font-weight:500;color:' + fc + ';">Music Mirror</div>' +
            '</div>' +
            '<div id="music-embed-container">' + mainEmbed + '</div>' +
            playlistLink +
            '<div style="display:flex;justify-content:space-between;align-items:center;margin-top:12px;">' +
                '<span style="font-size:0.7rem;color:var(--muted);">' +
                    (service === 'youtube' && playlistId ? '<a href="https://music.youtube.com/playlist?list=' + playlistId + '" target="_blank" rel="noopener" style="color:' + fc + ';text-decoration:none;">Open Playlist &rarr;</a>' : '') +
                    (service === 'spotify' && playlistId ? '<a href="https://open.spotify.com/playlist/' + playlistId + '" target="_blank" rel="noopener" style="color:' + fc + ';text-decoration:none;">Open Playlist &rarr;</a>' : '') +
                '</span>' +
                '<select class="tuning-streaming-select" style="font-size:0.72rem;" onchange="_switchMusicService(this.value,\'' + _escAttr(frequency) + '\',\'' + _escAttr(artist) + '\',\'' + _escAttr(title) + '\',\'' + _escAttr(spotifyId || '') + '\',\'' + _escAttr(youtubeId || '') + '\')">' +
                    '<option value="youtube"' + (service === 'youtube' ? ' selected' : '') + '>YouTube Music</option>' +
                    '<option value="spotify"' + (service === 'spotify' ? ' selected' : '') + '>Spotify</option>' +
                    '<option value="apple"' + (service === 'apple' ? ' selected' : '') + '>Apple Music</option>' +
                '</select>' +
            '</div>' +
        '</div>';

    document.body.appendChild(overlay);
}

async function _switchMusicService(newService, frequency, artist, title, spotifyId, youtubeId) {
    // Save preference and relaunch modal with new service
    _setStreamingServiceHome(newService);
    _launchMusicModal(newService, frequency, artist, title, spotifyId, youtubeId);
}

function _swapToPlaylist(service, playlistId, frequency, artist, title, spotifyId) {
    var container = document.getElementById('music-embed-container');
    if (!container) return;
    var fc = window._homeMirrorColor || 'var(--accent)';

    if (service === 'spotify') {
        container.innerHTML = '<iframe style="border-radius:12px;border:none;" src="https://open.spotify.com/embed/playlist/' + playlistId + '?utm_source=generator&theme=0" width="100%" height="420" allowfullscreen allow="autoplay; clipboard-write; encrypted-media; fullscreen; picture-in-picture" loading="lazy"></iframe>';
    }

    // Replace playlist link with "Back to track" link if we have a track ID
    var linkEl = container.nextElementSibling;
    if (linkEl && linkEl.tagName === 'A' && spotifyId) {
        linkEl.textContent = '← Back to Track';
        linkEl.onclick = function(e) {
            e.preventDefault();
            _launchMusicModal(service, frequency, artist, title, spotifyId);
        };
    } else if (linkEl && linkEl.tagName === 'A') {
        linkEl.remove();
    }
}


async function _openHomeLyrics(principle, color) {
    const modal = document.getElementById('lyricsModal');
    if (!modal) return;

    document.getElementById('lyricsTitle').textContent = principle;
    document.getElementById('lyricsTitle').style.color = color;
    document.getElementById('lyricsStage').textContent = 'Loading...';
    document.getElementById('lyricsBody').innerHTML = '';
    modal.style.display = 'flex';

    try {
        const res = await fetch('/api/canon/' + encodeURIComponent(principle));
        const data = await res.json();
        if (!data.found) {
            document.getElementById('lyricsStage').textContent = '';
            document.getElementById('lyricsBody').innerHTML = '<p style="color:var(--dim);">Lyrics not available.</p>';
            return;
        }
        document.getElementById('lyricsStage').textContent =
            'Stage ' + data.stage + ' — Lucid Path';

        // Format lyrics: [verse], [chorus] etc become styled section headers
        const lines = data.lyrics.split('\n');
        let html = '';
        for (const line of lines) {
            const trimmed = line.trim();
            if (!trimmed) {
                html += '<div class="lyrics-break"></div>';
            } else if (/^\[.+\]$/.test(trimmed)) {
                html += '<div class="lyrics-section" style="color:' + color + ';">' + esc(trimmed.slice(1, -1)) + '</div>';
            } else {
                html += '<div class="lyrics-line">' + esc(trimmed) + '</div>';
            }
        }
        document.getElementById('lyricsBody').innerHTML = html;
    } catch (e) {
        document.getElementById('lyricsStage').textContent = '';
        document.getElementById('lyricsBody').innerHTML = '<p style="color:var(--dim);">Could not load lyrics.</p>';
    }
}

async function loadApprovals() {
    const list = document.getElementById('approvalsList');
    if (!list) return; // Operator tier has no approvals section
    try {
        const res = await fetch('/api/approvals');
        const data = await res.json();

        updateApprovalBadge(data.approvals);

        if (!data.approvals || !data.approvals.length) {
            list.innerHTML = '<span class="empty">No pending approvals</span>';
        } else {
            list.innerHTML = data.approvals.map(a => `
                <div class="approval-item">
                    <div class="approval-tool">${esc(a.tool_name || a.tool || 'unknown')}</div>
                    <div class="approval-desc">${esc(a.description || '')}</div>
                    <div class="approval-actions">
                        <button class="btn btn-approve" onclick="respondApproval('${esc(a.request_id || a.id)}', true, this)">Approve</button>
                        <button class="btn btn-deny" onclick="respondApproval('${esc(a.request_id || a.id)}', false, this)">Deny</button>
                    </div>
                </div>
            `).join('');
        }
    } catch (e) {
        console.error('Failed to load approvals:', e);
    }
}

// updateApprovalBadge is now in core.js (handles both desktop tabs and mobile nav badge)

async function respondApproval(requestId, approved, btnEl) {
    // Immediate tap feedback — the server can take a few seconds, so show the
    // press registered right away instead of leaving the button looking idle.
    let group = [];
    if (btnEl) {
        const actions = btnEl.closest('.approval-actions');
        group = actions ? Array.from(actions.querySelectorAll('button')) : [btnEl];
        group.forEach(b => { if (b !== btnEl) b.classList.add('btn-busy'); b.disabled = true; });
        btnEl.dataset.orig = btnEl.textContent;
        btnEl.textContent = approved ? 'Approving…' : 'Denying…';
        btnEl.style.opacity = '0.85';
    }
    try {
        const res = await fetch(`/api/approvals/${requestId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ approved }),
        });
        const data = await res.json();

        if (approved && data.executed !== undefined) {
            // Show execution result — detect error text even when executed===true
            const list = document.getElementById('approvalsList');
            const resultDiv = document.createElement('div');
            resultDiv.className = 'approval-result';
            const resultText = (data.result || '').substring(0, 400);
            const hasError = /Error:|\[exit:\s*\d+\]|FAILED|Traceback/.test(resultText);
            if (data.executed && !hasError) {
                resultDiv.innerHTML = `<span style="color:var(--green,#4ade80);">Executed successfully.</span> <span class="dim">${esc(resultText)}</span>`;
            } else {
                resultDiv.innerHTML = `<span style="color:var(--red,#f87171);">FAILED — ${data.executed ? 'tool ran but returned error' : 'execution blocked'}:</span> <pre style="margin:4px 0;background:rgba(248,113,113,0.1);padding:6px;border-radius:4px;font-size:0.85em;white-space:pre-wrap;">${esc(resultText)}</pre>`;
            }
            list.prepend(resultDiv);
            // Clear result after 12s (longer for errors)
            setTimeout(() => { if (resultDiv.parentNode) resultDiv.remove(); }, hasError ? 12000 : 8000);
        }

        loadApprovals();
    } catch (e) {
        console.error('Failed to respond to approval:', e);
        // Restore the buttons so the user can retry
        group.forEach(b => { b.disabled = false; b.classList.remove('btn-busy'); b.style.opacity = ''; });
        if (btnEl && btnEl.dataset.orig) btnEl.textContent = btnEl.dataset.orig;
    }
}

// =============================================================================
// Projects & Tasks
// =============================================================================
// ── Home tab project cards (visual efficiency design) ───────────────────────
const _homePriColors = {
    'urgent':  'var(--red)',     // Momentum
    'high':    'var(--orange)',  // Courage
    'normal':  'var(--silver)',  // Trust
    'low':     'rgba(128,128,128,0.3)',
};

function _homeAgentInitial(assignee) {
    if (!assignee) return '';
    return lpAgentBadgeHTML(assignee);
}

async function loadHomeProjects() {
    const el = document.getElementById('projectsList');
    if (!el) return;
    try {
        const res = await fetch('/api/projects');
        const data = await res.json();
        let projects = data.projects || [];

        if (!projects.length) {
            el.innerHTML = '<span class="empty-msg">No active projects</span>';
            return;
        }

        // API returns sorted by priority then updated_at, take top 5
        projects = projects.slice(0, 5);

        el.innerHTML = projects.map(p => {
            const done = p.done_tasks || 0;
            const total = p.total_tasks || 0;
            const pct = total > 0 ? Math.round((done / total) * 100) : 0;
            const ownerBadge = _homeAgentInitial(p.owner || '');
            const pri = p.top_priority || 'normal';
            const priColor = _homePriColors[pri] || _homePriColors.normal;
            const priTitle = pri.charAt(0).toUpperCase() + pri.slice(1);

            return `<div class="proj-card" onclick="showProjectDetail(${p.id})" style="cursor:pointer;">
                <div class="proj-card-row">
                    <span class="task-pri-dot" style="background:${priColor};" title="${ESC(priTitle)} priority"></span>
                    <div class="proj-card-info">
                        <div class="proj-card-title">${ESC(p.name || '')}</div>
                        <div class="task-meta-row">
                            <div class="task-meta-left">
                                ${ownerBadge}
                                <span class="proj-task-fraction">${done}/${total}</span>
                            </div>
                        </div>
                    </div>
                    <div class="proj-card-progress">
                        <div class="progress-bar-track"><div class="progress-bar-fill" style="width:${pct}%"></div></div>
                    </div>
                </div>
            </div>`;
        }).join('');
    } catch (e) {
        el.innerHTML = `<span class="empty-msg">Error: ${ESC(e.message)}</span>`;
    }
}

// =============================================================================
// Model dropdown builder for agent detail page
// =============================================================================

// DB-backed model manager (Team page). Renders WORKING + TUNING dropdowns for an agent
// and saves to /api/agents/{id}/model-assignment — instant, no restart, works under a
// read-only config mount. Empty = inherit (working falls back to YAML cascade; tuning
// falls back to the working model).
async function _loadModelDropdowns(agentId) {
    const wPrim = document.getElementById('agp-model-primary-slot');
    const wFall = document.getElementById('agp-model-fallback-slot');
    const tPrim = document.getElementById('agp-tuning-primary-slot');
    const tFall = document.getElementById('agp-tuning-fallback-slot');
    if (!wPrim || !wFall) return;

    try {
        const data = await fetch('/api/agents/model-assignments').then(r => r.json());
        const models = data.catalog || [];
        if (!models.length) return; // no registry, keep text display
        const a = (data.agents || {})[agentId] || {};

        wPrim.innerHTML = _modelSelect('agp-model-primary', models, a.working_primary, agentId, true, '(Cove default)');
        wFall.innerHTML = _modelSelect('agp-model-fallback', models, a.working_fallback, agentId, true, '(none)');
        if (tPrim) tPrim.innerHTML = _modelSelect('agp-tuning-primary', models, a.tuning_primary, agentId, true, '(use working)');
        if (tFall) tFall.innerHTML = _modelSelect('agp-tuning-fallback', models, a.tuning_fallback, agentId, true, '(none)');
    } catch (e) {
        // Silently keep text display on error
    }
}

function _modelSelect(id, models, currentValue, agentId, allowNone, noneLabel) {
    const dropdownStyle = 'font-size:0.78rem;background:var(--card2);color:var(--fg);border:1px solid var(--border);border-radius:4px;padding:2px 6px;max-width:200px;';
    let opts = allowNone ? `<option value="">${esc(noneLabel || '(none)')}</option>` : '';
    for (const m of models) {
        const sel = (m.id === currentValue) ? ' selected' : '';
        const typeTag = m.type === 'cloud' ? ' [cloud]' : ' [local]';
        opts += `<option value="${esc(m.id)}"${sel}>${esc(m.name || m.id)}${typeTag}</option>`;
    }
    return `<select id="${id}" style="${dropdownStyle}"
                onchange="_saveAgentModelAssignment('${esc(agentId)}', this)">
                ${opts}
            </select>
            <span id="${id}-status" style="font-size:0.68rem;margin-left:4px;"></span>`;
}

// Gather all four dropdowns and persist them together (the endpoint takes the full set).
async function _saveAgentModelAssignment(agentId, selectEl) {
    const statusEl = document.getElementById(selectEl.id + '-status');
    if (statusEl) { statusEl.textContent = '...'; statusEl.style.color = 'var(--dim)'; }
    const val = (id) => (document.getElementById(id) || {}).value || '';
    const body = {
        working_primary: val('agp-model-primary'),
        working_fallback: val('agp-model-fallback'),
        tuning_primary: val('agp-tuning-primary'),
        tuning_fallback: val('agp-tuning-fallback'),
    };
    try {
        const res = await fetch(`/api/agents/${agentId}/model-assignment`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await res.json();
        if (data.ok) {
            if (statusEl) {
                statusEl.textContent = 'Saved';
                statusEl.style.color = 'var(--green, #4caf50)';
                setTimeout(() => { statusEl.textContent = ''; }, 2500);
            }
        } else if (statusEl) {
            statusEl.textContent = data.error || 'Error';
            statusEl.style.color = 'var(--red, #f44336)';
        }
    } catch (e) {
        if (statusEl) { statusEl.textContent = e.message; statusEl.style.color = 'var(--red, #f44336)'; }
    }
}
