// =============================================================================
// Mission Control — Team tab (type-aware)
// =============================================================================
//
// Admin/domain view (Stuart):
//   Managers       — stuart, mercer (from roster, with tuning + JW stats)
//   Build Team     — field agents (from roster, with tuning + JW stats)
//   Family         — human operators paired with personal agents
//
// Personal view (Atlas):
//   Family         — human operators paired with personal agents (up top)
//   Managers       — from family.yaml family_agents (basic info only)
//   Build Team     — from family.yaml family_agents (basic info only)
//   No tuning/JW stats in personal view. No personal agents in agent sections.
//
// =============================================================================

const MANAGERS = new Set(['stuart', 'mercer']);
let _teamCoveName = '';  // Set by loadTeam(), used by startAgentSetupFor()

/** Strip family suffix from agent ID for comparison (uses _resolveAgentId from core.js if available) */
function _resolveId(id) {
    return typeof _resolveAgentId === 'function' ? _resolveAgentId(id) : id.toLowerCase();
}

/** Team view mode: admin (full controls), operator (read-only admin), member (directory) */
function _teamViewMode() {
    // Manager subdomain (stuart.{cove}) renders the steward/admin view.
    if (window.MC && MC.adminView) return 'admin';
    const t = MC.instance?.type || '';
    // Manager MCs (steward or merchant) — full admin with controls
    if (t === 'admin' || t === 'manager') return 'admin';
    // Multi-presence: check user's cove_role
    if (MC.presence) {
        return MC.presence.cove_role === 'admin' ? 'operator' : 'member';
    }
    // Standalone domain MC with no presence login — admin
    if (t === 'domain') return 'admin';
    // Personal MC — member directory view
    return 'member';
}

/** Backwards-compat: admin-style view (admin or operator) */
function _teamIsAdmin() {
    const mode = _teamViewMode();
    return mode === 'admin' || mode === 'operator';
}

async function loadTeam() {
    const el = document.getElementById('teamRoster');
    const summaryEl = document.getElementById('teamSummary');
    const isAdmin = _teamIsAdmin();
    try {
        // Fetch roster (with JW stats), family, and settings in parallel
        const [rosterRes, familyRes, settingsRes] = await Promise.all([
            fetch('/api/team/roster'),
            fetch('/api/family'),
            fetch('/api/settings'),
        ]);
        const rosterData    = await rosterRes.json();
        const familyData = await familyRes.json();
        const settingsData  = await settingsRes.json();

        // Derive build team suffix from family name (e.g., "Smith" → "-smith")
        const familyName = (settingsData.settings || {}).family_name || '';
        BUILD_TEAM_SUFFIX = familyName ? `-${familyName.toLowerCase()}` : '';

        // Family display name from config
        const hhInfo = familyData.family || {};
        const hhDisplayName = hhInfo.name || familyName || '';
        _teamCoveName = hhDisplayName;

        // Update panel header with family name
        const hhLabel = document.getElementById('familyLabel');
        if (hhLabel && hhDisplayName) hhLabel.textContent = hhDisplayName + ' Cove';

        const agentsObj     = rosterData.agents || {};
        const agents        = Object.entries(agentsObj);
        const members       = familyData.members || [];

        if (!agents.length && !members.length) {
            el.innerHTML = '<span class="empty">No team data available</span>';
            return;
        }

        // ── Member view: simplified directory (non-operator Presences) ────
        if (!isAdmin) {
            const familyAgents = familyData.family_agents || [];
            const hManagers  = familyAgents.filter(a => a.category === 'manager');
            const hBuildTeam = familyAgents.filter(a => a.category === 'build_team');
            const activeHA   = familyAgents.filter(a => a.status === 'active').length;

            // Count personal agents from family members
            if (summaryEl) {
                const _pl = members.length === 1 ? 'presence' : 'presences';
                summaryEl.textContent = `${activeHA} agents · ${members.length} ${_pl} · ${activeHA + members.length} total`;
            }

            let html = '';

            // Presences — who's in this Cove
            if (members.length) {
                html += sectionHeader(hhDisplayName || 'Presences');
                html += `<div class="family-roster">${members.map(familyPair).join('')}</div>`;
            }

            // Managers — Stuart, Mercer (basic info, no tuning/JW)
            if (hManagers.length) {
                html += sectionHeader('Managers');
                html += `<div class="agent-roster">${hManagers.map(a => directoryCard(a)).join('')}</div>`;
            }

            // Build Team (basic info, no tuning/JW)
            if (hBuildTeam.length) {
                html += sectionHeader('Build Team');
                html += `<div class="agent-roster">${hBuildTeam.map(a => directoryCard(a)).join('')}</div>`;
            }

            el.innerHTML = html;
            return;
        }

        // ── Admin/operator view: full roster + presences ──────────────────────
        const viewMode = _teamViewMode();
        const canEdit = viewMode === 'admin';  // Only Stuart MC gets controls

        // Categorize agents — managers by explicit set, everyone else is build team.
        // Agent IDs are first-name-only (no family suffix).
        const managers  = agents.filter(([id]) => MANAGERS.has(_resolveId(id)));
        const buildTeam = agents.filter(([id]) => !MANAGERS.has(_resolveId(id)));

        // Count: shared team agents (the roster) vs presences. A presence is an
        // operator + their OWN agent, so a presence's personal agent is part of the
        // presence — counting it as a standalone "agent" too double-counts (CF-125b).
        // Headline = team agents · presences; total = the two summed.
        const teamAgents = agents.length;
        const presenceCount = members.length;
        if (summaryEl) {
            const _pl = presenceCount === 1 ? 'presence' : 'presences';
            summaryEl.textContent = `${teamAgents} agents · ${presenceCount} ${_pl} · ${teamAgents + presenceCount} total`;
        }

        // JW legend — rendered in header right side
        const legendEl = document.getElementById('teamLegend');
        if (legendEl) legendEl.textContent = 'JW = 7-day rolling';

        let html = '';

        // ── Managers ──────────────────────────────────────────────────────────
        if (managers.length) {
            html += sectionHeader('Managers');
            html += `<div class="agent-roster">${managers.map(([id, a]) => agentCard(id, a)).join('')}</div>`;
        }

        // ── Build Team ────────────────────────────────────────────────────────
        if (buildTeam.length) {
            html += sectionHeader('Build Team');
            html += `<div class="agent-roster">${buildTeam.map(([id, a]) => agentCard(id, a)).join('')}</div>`;
        }

        // ── Presences ─────────────────────────────────────────────────────
        html += sectionHeader('Presences');
        html += `<div class="family-roster">${members.map(familyPair).join('')}</div>`;
        // Operators (and the admin/Stuart MC) can add presences to their Cove.
        if (canEdit || viewMode === 'operator') {
            html += `<div class="family-add-row">
                <button class="btn btn-subtle family-add-btn" onclick="window.location.href='/static/action-board/agent-setup.html'">+ Add a Presence</button>
                <button class="btn btn-subtle family-add-btn" onclick="invitePresence()">Invite by link</button>
            </div>
            <div id="invite-presence-box"></div>`;
        }

        // ── Haven ─────────────────────────────────────────────────────────
        // The Cove admin can form a Haven and connect other Coves.
        if (canEdit) {
            html += sectionHeader('Haven', 'Connect this Cove with others');
            html += `<div class="family-add-row">
                <button class="btn btn-subtle family-add-btn" onclick="window.location.href='/static/action-board/haven.html'">&#11041; Your Haven</button>
            </div>`;
        }

        el.innerHTML = html;
    } catch (e) {
        el.innerHTML = `<span class="empty">Error loading team: ${esc(e.message)}</span>`;
    }
}

// ─── Rendering helpers ────────────────────────────────────────────────────────

function sectionHeader(label, subtitle) {
    const sub = subtitle ? ` <span style="font-weight:400;opacity:0.6;text-transform:none;letter-spacing:0;">${esc(subtitle)}</span>` : '';
    return `<div class="team-section-header">${esc(label)}${sub}</div>`;
}

function agentCard(id, a, compact) {
    const name        = a.name || id.charAt(0).toUpperCase() + id.slice(1);
    const displayName = firstName(name);
    const archetype   = a.archetype || '';
    const status      = a.status || 'active';
    const freq        = a.last_frequency || '';

    // JW stats (7-day rolling)
    const jw = a.jw || {};
    const jwCalls   = jw.calls || 0;
    const jwTokens  = jw.tokens || 0;
    const jwSuccess = jw.success_rate || 0;

    // Format tokens (K/M)
    const tokStr = jwTokens >= 1000000
        ? (jwTokens / 1000000).toFixed(1) + 'M'
        : jwTokens >= 1000
            ? (jwTokens / 1000).toFixed(1) + 'K'
            : String(jwTokens);

    // Compact JW summary line
    const jwLine = compact ? '' : (status !== 'planned' && jwCalls > 0
        ? `<span class="agent-card-jw">${jwCalls} calls · ${tokStr} tok · ${jwSuccess.toFixed(0)}%</span>`
        : '');

    // Agent badge — permanent identity symbol (colored letter, same as tasks/attention board)
    const agentBadge = typeof lpAgentBadgeHTML === 'function' ? lpAgentBadgeHTML(id) : '';

    return `<div class="agent-card-v2${status === 'planned' ? ' agent-card-planned' : ''}"
                onclick="showAgentDetail('${esc(id)}')" style="cursor:pointer;"
                title="View ${esc(displayName)} details">
        <img class="ac2-avatar" src="${avatarPath(String(id).split('-')[0])}" alt="${esc(name)}"
             onerror="this.style.display='none'">
        <div class="ac2-info">
            <div class="ac2-name-row">
                <span class="ac2-badge">${agentBadge}</span>
                <span class="ac2-name">${esc(displayName)}</span>
            </div>
            ${archetype ? `<span class="ac2-archetype">${esc(archetype)}</span>` : ''}
            ${freq ? `<span class="ac2-freq">${typeof lpFreqBadgeHTML === 'function' ? lpFreqBadgeHTML(freq) : esc(freq)}</span>` : ''}
            ${jwLine}
        </div>
    </div>`;
}

function directoryCard(agent) {
    const name     = agent.name || agent.id;
    const arch     = agent.archetype || '';
    const status   = agent.status || 'active';
    const role     = agent.role || '';
    const mcUrl    = agent.mc_url || '';

    // Use agent.id for badge lookup
    const agentId = agent.id || name.toLowerCase().replace(/[^a-z0-9-]/g, '');
    // Card click opens the agent's PROFILE (not their MC) — these become the
    // editable Connect/Marketplace profiles.
    const clickAction = `showAgentDetail('${esc(agentId)}')`;
    const cursor = 'cursor:pointer;';
    const tip    = `title="View ${esc(name)}'s profile"`;
    const colorBadge = lpAgentBadgeHTML(agentId);

    // Avatar by base archetype name: instance ids are family-suffixed
    // (e.g. "stuart-clearfield"), but avatars ship as bare names ("stuart.png").
    const avatarSrc = agent.id ? avatarPath(String(agent.id).split('-')[0]) : avatarPath(name);

    return `<div class="agent-card${status === 'planned' ? ' agent-card-planned' : ''}"
                ${clickAction ? `onclick="${clickAction}"` : ''} style="${cursor}position:relative;" ${tip}>
        <div style="position:absolute;top:6px;right:8px;">${colorBadge}</div>
        <div class="agent-identity">
            <img class="agent-thumb" src="${avatarSrc}" alt="${esc(name)}"
                 onerror="this.style.display='none'">
            <div class="agent-id-text">
                <span class="agent-name"><span class="status-dot ${status}"></span> ${esc(name)}</span>
                ${arch ? `<span class="agent-archetype">${esc(arch)}</span>` : ''}
            </div>
        </div>
        ${role ? `<div class="agent-role-line">${esc(role)}</div>` : ''}
    </div>`;
}

function familyPair(member) {
    const admin    = (typeof _teamViewMode === 'function') && _teamViewMode() === 'admin';
    const pa       = member.personal_agent || null;
    const paStatus = pa ? (pa.status || 'planned') : null;
    const isPaLive = paStatus === 'active';

    // Agent badge color — use LP agent mapping if available
    const paBadge = pa && pa.name ? lpAgentBadgeHTML(pa.id || pa.name) : '';

    // Avatar: the archetype image if the Presence has one, else the agent-id file.
    const paAvatarSrc = pa ? (pa.avatar || avatarPath(pa.id || pa.name)) : '';

    const paCard = pa && pa.name ? `
        <div class="family-agent-card${!isPaLive ? ' agent-card-planned' : ''}"
             onclick="showFamilyMemberDetail('${esc(member.id)}')"
             style="cursor:pointer;position:relative;"
             title="View ${esc(pa.name)}'s profile">
            <div style="position:absolute;top:6px;right:8px;">${paBadge}</div>
            <div class="agent-identity">
                <img class="agent-thumb" src="${paAvatarSrc}" alt="${esc(pa.name)}"
                     onerror="this.style.display='none'">
                <div class="agent-id-text">
                    <span class="agent-name"><span class="status-dot ${paStatus}"></span> ${esc(pa.name)}</span>
                    ${pa.archetype ? `<span class="agent-archetype"${pa.frequency_color ? ` style="color:${esc(pa.frequency_color)};"` : ''}>${esc(pa.archetype)}${pa.frequency ? ` · ${esc(pa.frequency)}` : ''}${pa.shade ? ` <span style="opacity:.65;">+ ${esc(pa.shade)}</span>` : ''}</span>` : ''}
                    ${pa.last_frequency ? `<span class="ac2-freq" style="margin-top:4px;">${typeof lpFreqBadgeHTML === 'function' ? lpFreqBadgeHTML(pa.last_frequency) : esc(pa.last_frequency)}</span>` : ''}
                </div>
            </div>
            ${pa.tuning_key ? `<div class="pa-key" style="font-size:0.66rem;color:var(--dim);font-style:italic;margin-top:5px;line-height:1.4;border-left:2px solid ${esc(pa.frequency_color || 'var(--accent)')};padding-left:7px;">${esc(pa.tuning_key)}</div>` : ''}
            ${!isPaLive ? `<div style="font-size:0.68rem;color:var(--dim);margin-top:4px;">Container pending</div>` : ''}
        </div>` : `<div class="family-agent-card agent-card-planned family-agent-setup"
             onclick="startAgentSetupFor('${esc(member.id)}', '${esc(member.display_name || member.name)}')"
             style="cursor:pointer;" title="Set up a personal agent for ${esc(member.display_name || member.name)}">
            <div class="agent-setup-prompt">
                <div class="agent-setup-icon">+</div>
                <div class="agent-setup-text">Set Up Agent</div>
            </div>
        </div>`;

    return `<div class="family-pair">
        <div class="family-member-card" onclick="showFamilyMemberDetail('${esc(member.id)}')"
             style="cursor:pointer;" title="View ${esc(member.display_name || member.name)} details">
            <div class="agent-identity">
                ${member.photo || member.avatar_url
                    ? `<img class="agent-thumb" src="${esc(member.photo || member.avatar_url)}" alt="${esc(member.name)}" onerror="this.style.display='none'">`
                    : `<div class="agent-thumb" style="display:flex;align-items:center;justify-content:center;background:#2a2d3a;color:#cfcfe0;font-weight:700;border-radius:50%;">${esc(((member.display_name || member.name || '?').trim()[0] || '?').toUpperCase())}</div>`}
                <div class="agent-id-text">
                    <span class="agent-name"><span class="status-dot ${member.status || 'active'}"></span> ${esc(member.display_name || member.name)}</span>
                    <span class="agent-archetype" style="color:var(--accent);">${esc(member.role || 'Family Member')}</span>
                </div>
            </div>
            ${member.focus ? `<div style="font-size:0.7rem;color:var(--dim);margin-top:4px;line-height:1.4;">${esc(member.focus)}</div>` : ''}
            ${admin && member.is_presence ? `<button onclick="event.stopPropagation(); setPresenceRole('${esc(member.id)}','${member.cove_role === 'admin' ? 'member' : 'admin'}')" style="margin-top:6px;font-size:0.62rem;padding:2px 8px;background:transparent;border:1px solid var(--dim);border-radius:4px;color:var(--dim);cursor:pointer;">${member.cove_role === 'admin' ? 'Make member' : 'Make admin'}</button>` : ''}
        </div>
        <div class="family-pair-connector">⟷</div>
        ${paCard}
    </div>`;
}

// ─── Family detail pages ───────────────────────────────────────────────────

function _openFamilyPanel() {
    document.querySelectorAll('.panel').forEach(p => {
        p.classList.remove('active-grid', 'active-flex');
        p.style.display = 'none';
    });
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    const panel = document.getElementById('panel-family-detail');
    panel.style.display = 'block';
    panel.classList.add('active-grid');
    // Reset shared elements
    const av = document.getElementById('hmp-avatar');
    av.src = '';
    av.style.display = 'none';
    document.getElementById('hmp-focus').style.display = 'none';
    document.getElementById('hmp-member-section').style.display = '';
    document.getElementById('hmp-agent-only-section').style.display = 'none';
    document.getElementById('hmp-agent').innerHTML  = '<span class="empty">Loading...</span>';
    document.getElementById('hmp-tasks').innerHTML  = '<span class="empty">Loading...</span>';
}

// Operator page — human member detail
// Admin: set a presence's cove_role (admin|member). Admin-only on the backend.
async function setPresenceRole(id, role) {
    try {
        const res = await fetch('/api/presence/' + encodeURIComponent(id) + '/role', {
            method: 'PATCH', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ cove_role: role }),
        });
        const d = await res.json();
        if (!res.ok || d.error) throw new Error(d.detail || d.error || 'Update failed');
        if (typeof loadTeam === 'function') loadTeam();
        else location.reload();
    } catch (e) { alert('Could not update role: ' + (e.message || e)); }
}

async function showFamilyMemberDetail(memberId) {
    // #176: open the unified Presence page (showcase + inline editor) instead of the
    // old inline Team editor. Resolve the member's @handle and hand off.
    if (window.PresenceProfile) {
        try {
            const fr = await fetch('/api/family');
            const fd = await fr.json();
            const mem = (fd.members || []).find(m => m.id === memberId);
            if (mem && mem.username) { window.PresenceProfile.open(mem.username, { canEdit: true }); return; }
        } catch (e) { /* fall back to the legacy panel below */ }
    }
    _openFamilyPanel();
    document.getElementById('hmp-name').textContent = memberId;
    document.getElementById('hmp-role').textContent = '';

    try {
        const res  = await fetch('/api/family');
        const data = await res.json();
        const member = (data.members || []).find(m => m.id === memberId);
        if (!member) {
            document.getElementById('hmp-agent').innerHTML = '<span class="empty">Member not found</span>';
            return;
        }

        document.getElementById('hmp-name').textContent = member.display_name || member.name;
        document.getElementById('hmp-role').textContent = member.role || 'Family Member';

        const av = document.getElementById('hmp-avatar');
        av.src = avatarPath(member.name);
        av.alt = member.name;
        av.style.display = '';

        // Show focus as editable below
        const focusEl = document.getElementById('hmp-focus');
        focusEl.style.display = 'none';

        // ── Editable profile section (above the grid) ──
        let profileEl = document.getElementById('hmp-profile');
        if (!profileEl) {
            profileEl = document.createElement('div');
            profileEl.id = 'hmp-profile';
            profileEl.className = 'card hmp-profile-card';
            // Insert before the grid
            const memberSection = document.getElementById('hmp-member-section');
            memberSection.parentNode.insertBefore(profileEl, memberSection);
        }
        profileEl.style.display = '';
        profileEl.innerHTML = `
            <h2>Profile</h2>
            <div class="hmp-profile-fields">
                <div class="hmp-field">
                    <label>Display Name</label>
                    <input type="text" id="hmp-edit-name" class="amf-input" value="${esc(member.display_name || member.name)}">
                </div>
                <div class="hmp-field">
                    <label>Role</label>
                    <input type="text" id="hmp-edit-role" class="amf-input" value="${esc(member.role || '')}">
                </div>
                <div class="hmp-field">
                    <label>Focus / Interests</label>
                    <textarea id="hmp-edit-focus" class="amf-input" rows="3" placeholder="What are they into? What matters to them? This helps shape their personal agent.">${esc(member.focus || '')}</textarea>
                </div>
                <div class="hmp-profile-actions">
                    <button class="btn btn-action amf-save" onclick="saveMemberProfile('${esc(member.id)}')">Save Profile</button>
                    <span id="hmp-profile-feedback" class="hmp-profile-fb"></span>
                </div>
            </div>`;

        // Personal agent card
        const pa = member.personal_agent || null;
        const agentEl = document.getElementById('hmp-agent');
        if (pa && pa.name) {
            const paStatus = pa.status || 'planned';
            const isPaLive = paStatus === 'active';
            const launchBtn = (isPaLive && member.mc_url)
                ? `<button class="btn btn-action" onclick="window.open('${esc(member.mc_url)}', '_blank')" style="margin-top:12px;width:100%;">Launch ${esc(pa.name)} MC →</button>`
                : '';
            agentEl.innerHTML = `
                <div class="agp-stat-row"><span class="agp-stat-label">Name</span><span>${esc(pa.name)}</span></div>
                <div class="agp-stat-row"><span class="agp-stat-label">Archetype</span><span>${esc(pa.archetype || '')}</span></div>
                <div class="agp-stat-row"><span class="agp-stat-label">Status</span><span>${statusDotHTML(paStatus)} ${esc(paStatus)}</span></div>
                <div class="agp-stat-row"><span class="agp-stat-label">Container</span><span style="color:var(--dim);font-size:0.78rem;">${esc(pa.container || '')}</span></div>
                ${member.mc_port ? `<div class="agp-stat-row"><span class="agp-stat-label">MC Port</span><span style="color:var(--dim);font-size:0.78rem;">${member.mc_port}</span></div>` : ''}
                ${!isPaLive ? `<div style="margin-top:10px;padding:8px;background:var(--card2);border-radius:6px;font-size:0.75rem;color:var(--dim);line-height:1.5;">Container not yet deployed. Will appear at port ${member.mc_port || '—'} when active.</div>` : ''}
                ${launchBtn}
            `;
        } else {
            agentEl.innerHTML = `
                <div style="text-align:center;padding:12px 0;">
                    <div style="color:var(--dim);font-size:0.82rem;margin-bottom:12px;">No personal agent yet</div>
                    <button class="btn btn-action" onclick="startAgentSetupFor('${esc(member.id)}', '${esc(member.display_name || member.name)}')"
                            style="width:100%;">Set Up Personal Agent →</button>
                </div>`;
        }

        loadFamilyMemberTasks(memberId);

    } catch (e) {
        document.getElementById('hmp-agent').innerHTML = `<span class="empty">Error: ${esc(e.message)}</span>`;
    }
}

async function saveMemberProfile(memberId) {
    const feedback = document.getElementById('hmp-profile-feedback');
    const body = {
        display_name: document.getElementById('hmp-edit-name')?.value.trim() || '',
        role: document.getElementById('hmp-edit-role')?.value.trim() || '',
        focus: document.getElementById('hmp-edit-focus')?.value.trim() || '',
    };
    // Also update the top-level name if display_name changed
    if (body.display_name) body.name = body.display_name;

    try {
        const res = await fetch(`/api/family/${encodeURIComponent(memberId)}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!res.ok) {
            const data = await res.json();
            if (feedback) { feedback.textContent = data.error || 'Save failed'; feedback.style.color = 'var(--red)'; }
            return;
        }
        if (feedback) { feedback.textContent = 'Saved'; feedback.style.color = 'var(--daily-freq, var(--accent))'; }
        // Update header to reflect changes
        document.getElementById('hmp-name').textContent = body.display_name;
        document.getElementById('hmp-role').textContent = body.role;
        setTimeout(() => { if (feedback) feedback.textContent = ''; }, 2000);
    } catch (e) {
        if (feedback) { feedback.textContent = `Error: ${e.message}`; feedback.style.color = 'var(--red)'; }
    }
}

// Atlas's page — personal agent detail (planned or active)
async function showPersonalAgentPreview(memberId) {
    _openFamilyPanel();
    document.getElementById('hmp-name').textContent = '...';
    document.getElementById('hmp-role').textContent = '';
    document.getElementById('hmp-member-section').style.display = 'none';
    document.getElementById('hmp-agent-only-section').style.display = '';

    try {
        const res  = await fetch('/api/family');
        const data = await res.json();
        const member = (data.members || []).find(m => m.id === memberId);
        if (!member || !member.personal_agent) return;

        const pa       = member.personal_agent;
        const paStatus = pa.status || 'planned';
        const isPaLive = paStatus === 'active';

        document.getElementById('hmp-name').textContent = pa.name;
        document.getElementById('hmp-role').textContent = pa.archetype || '';

        const av = document.getElementById('hmp-avatar');
        av.src = avatarPath(pa.name);
        av.alt = pa.name;
        av.style.display = '';

        const focusEl = document.getElementById('hmp-focus');
        focusEl.textContent = isPaLive
            ? `Personal agent for ${member.display_name || member.name} · Mission Control active at port ${member.mc_port || '—'}`
            : `Personal agent for ${member.display_name || member.name} · Container pending deployment at port ${member.mc_port || '—'}`;
        focusEl.style.display = '';

        const launchBtn = (isPaLive && member.mc_url)
            ? `<button class="btn btn-action" onclick="window.open('${esc(member.mc_url)}', '_blank')" style="margin-top:12px;width:100%;">Launch Mission Control →</button>`
            : '';

        document.getElementById('hmp-agent-only').innerHTML = `
            <div class="agp-stat-row"><span class="agp-stat-label">Status</span><span>${statusDotHTML(paStatus)} ${esc(paStatus)}</span></div>
            <div class="agp-stat-row"><span class="agp-stat-label">Container</span><span style="color:var(--dim);font-size:0.78rem;">${esc(pa.container || '')}</span></div>
            <div class="agp-stat-row"><span class="agp-stat-label">MC Port</span><span style="color:var(--dim);font-size:0.78rem;">${member.mc_port || '—'}</span></div>
            <div class="agp-stat-row"><span class="agp-stat-label">Operator</span><span style="color:var(--accent);">${esc(member.display_name || member.name)}</span></div>
            ${!isPaLive ? `<div style="margin-top:10px;padding:8px;background:var(--card2);border-radius:6px;font-size:0.75rem;color:var(--dim);line-height:1.5;">Isolated container not yet deployed. When active, Mission Control will be available at port ${member.mc_port || '—'}.</div>` : ''}
            ${launchBtn}
        `;

    } catch (e) {
        document.getElementById('hmp-agent-only').innerHTML = `<span class="empty">Error: ${esc(e.message)}</span>`;
    }
}

// =============================================================================
// Agent setup + Add family member actions
// =============================================================================

function startAgentSetupFor(memberId, memberName) {
    const coveName = _teamCoveName || '';
    const url = `/static/action-board/new-agent-setup.html?cove=${encodeURIComponent(coveName)}&member=${encodeURIComponent(memberId)}&name=${encodeURIComponent(memberName)}`;
    if (typeof openFlowOverlay === 'function') {
        openFlowOverlay(url, 'team', 'Personal Agent Setup');
    } else {
        window.location.href = url;
    }
}

function showAddFamilyMember() {
    const roster = document.getElementById('teamRoster');
    if (!roster) return;

    // Don't duplicate if form already exists
    if (document.getElementById('add-member-form')) return;

    const form = document.createElement('div');
    form.id = 'add-member-form';
    form.className = 'add-member-form';
    form.innerHTML = `
        <div class="team-section-header">Add Family Member</div>
        <div class="add-member-fields">
            <input type="text" id="amf-name" class="amf-input" placeholder="Name" autocomplete="off">
            <input type="text" id="amf-role" class="amf-input" placeholder="Role (optional)" value="Family Member">
            <input type="text" id="amf-focus" class="amf-input" placeholder="Interests, focus areas (optional)">
            <div class="amf-actions">
                <button class="btn btn-action amf-save" onclick="addFamilyMember()">Add Member</button>
                <button class="btn amf-cancel" onclick="document.getElementById('add-member-form').remove()">Cancel</button>
            </div>
            <div id="amf-feedback" class="amf-feedback"></div>
        </div>`;
    roster.appendChild(form);
    document.getElementById('amf-name').focus();
}

async function addFamilyMember() {
    const nameInput = document.getElementById('amf-name');
    const roleInput = document.getElementById('amf-role');
    const focusInput = document.getElementById('amf-focus');
    const feedback = document.getElementById('amf-feedback');
    const name = (nameInput?.value || '').trim();

    if (!name) {
        if (feedback) feedback.textContent = 'Name is required.';
        return;
    }

    const memberId = name.toLowerCase().replace(/[^a-z0-9]/g, '');
    const body = {
        id: memberId,
        name: name,
        display_name: name,
        role: (roleInput?.value || '').trim() || 'Family Member',
        focus: (focusInput?.value || '').trim(),
        status: 'planned',
    };

    try {
        const res = await fetch('/api/family', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await res.json();
        if (!res.ok) {
            if (feedback) feedback.textContent = data.error || 'Failed to add member.';
            return;
        }
        // Success — remove form and reload team
        document.getElementById('add-member-form')?.remove();
        loadTeam();
    } catch (e) {
        if (feedback) feedback.textContent = `Error: ${e.message}`;
    }
}

// ── Invite a Presence by link (self-onboard) ────────────────────────────────
function invitePresence() {
    const box = document.getElementById('invite-presence-box');
    if (!box) return;
    if (document.getElementById('invite-form')) { document.getElementById('invite-form').remove(); return; }
    const form = document.createElement('div');
    form.id = 'invite-form';
    form.className = 'add-member-form';
    form.innerHTML = `
        <div class="team-section-header">Invite a Presence by link</div>
        <div class="add-member-fields">
            <select id="inv-role" class="amf-input">
                <option value="member">Member — their own agent + tools, reaches the team through an admin</option>
                <option value="admin">Admin — full access to the build team</option>
            </select>
            <input type="text" id="inv-label" class="amf-input" placeholder="For whom? (optional, e.g. Mom)" autocomplete="off">
            <div class="amf-actions">
                <button class="btn btn-action amf-save" onclick="createPresenceInvite()">Create link</button>
                <button class="btn amf-cancel" onclick="document.getElementById('invite-form').remove()">Cancel</button>
            </div>
            <div id="inv-feedback" class="amf-feedback"></div>
            <div id="inv-result"></div>
        </div>`;
    box.innerHTML = '';
    box.appendChild(form);
}

async function createPresenceInvite() {
    const role = (document.getElementById('inv-role')?.value) || 'member';
    const label = (document.getElementById('inv-label')?.value || '').trim();
    const fb = document.getElementById('inv-feedback');
    const out = document.getElementById('inv-result');
    if (fb) fb.textContent = 'Creating…';
    try {
        const res = await fetch('/api/presence/invite', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ role, label }),
        });
        const data = await res.json();
        if (!res.ok || !data.ok) { if (fb) fb.textContent = data.detail || data.error || 'Could not create the link.'; return; }
        if (fb) fb.textContent = '';
        const url = data.join_url || '';
        out.innerHTML = `<div style="margin-top:10px;padding:10px;background:#0e0e16;border:1px solid #24242f;border-radius:8px;">
            <div style="font-size:0.75rem;color:#888;margin-bottom:6px;">Send this to the person — they open it on their own phone to set up their Presence. Single-use, expires in 7 days.</div>
            <div style="display:flex;gap:6px;align-items:center;">
                <input id="inv-link" class="amf-input" style="flex:1;" readonly value="${esc(url)}">
                <button class="btn btn-action" onclick="copyInviteLink(event)">Copy</button>
            </div>
        </div>`;
    } catch (e) { if (fb) fb.textContent = 'Error: ' + e.message; }
}

function copyInviteLink(ev) {
    const el = document.getElementById('inv-link');
    if (!el) return;
    el.select();
    try { navigator.clipboard.writeText(el.value); } catch (e) { try { document.execCommand('copy'); } catch (_) {} }
    const btn = ev && ev.target;
    if (btn) { const t = btn.textContent; btn.textContent = 'Copied'; setTimeout(() => { btn.textContent = t; }, 1500); }
}

async function loadFamilyMemberTasks(memberId) {
    const el = document.getElementById('hmp-tasks');
    try {
        const res  = await fetch(`/api/tasks?assignee=${encodeURIComponent(memberId)}&limit=20`);
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
        const priClassMap  = { 'urgent': 'p1', 'high': 'p2', 'normal': 'p3', 'low': 'p4' };
        const priLabelMap  = { 'urgent': 'URG', 'high': 'HIGH', 'normal': 'NORM', 'low': 'LOW' };
        el.innerHTML = tasks.map(t => {
            const dotColor = statusColors[t.status] || 'var(--dim)';
            const priClass = priClassMap[t.priority] || 'p4';
            const priLabel = priLabelMap[t.priority] || 'LOW';
            const projLink = t.project_id
                ? `<span onclick="showProjectDetail(${t.project_id})" style="color:var(--accent);cursor:pointer;">P-${t.project_id}</span>`
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
