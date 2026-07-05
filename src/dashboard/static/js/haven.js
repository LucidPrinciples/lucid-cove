// =============================================================================
// haven.js — Cove-admin Haven surface (read + manage).
// =============================================================================
// Reads the Coves in this Haven from GET /api/haven/coves (this Cove + each
// connected Cove, sourced from the hub registrar). Admin-gated server-side.
// Actions: form a Haven (/api/haven/create), nest a member Cove
// (/api/haven/{id}/nest), invite a federated member (/api/haven/{id}/invite).
// =============================================================================
function _havenSlug(s) {
    return (s || '').toLowerCase().trim().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
}

function _havenCardsHtml(coves) {
    return (coves || []).map(c => {
        const presences = (c.presences || []).join(', ');
        const open = c.mc_url
            ? `<a href="${ESC(c.mc_url)}" target="_blank" rel="noopener" style="font-size:0.7rem;">Open MC &rarr;</a>`
            : '';
        const ownerTag = c.is_owner
            ? ' <span style="font-size:0.6rem;text-transform:uppercase;color:var(--orange,#e67e22);">owner</span>'
            : '';
        return `
            <div class="settings-row" style="flex-direction:column;align-items:stretch;gap:0.2rem;padding:0.5rem 0;border-bottom:1px solid var(--border,#2a2a3a);">
                <div style="display:flex;align-items:center;gap:0.5rem;">
                    <span style="font-weight:600;color:var(--text);flex:1;">${ESC(c.name || '')}${ownerTag}</span>
                    ${open}
                </div>
                <div style="font-size:0.72rem;color:var(--dim);">${ESC(c.operator || '')}${presences ? ' &middot; ' + ESC(presences) : ''}</div>
            </div>`;
    }).join('');
}

async function loadHaven() {
    const el = document.getElementById('haven-admin');
    if (!el) return;
    el.innerHTML = '<div class="loading">Loading Haven...</div>';

    let data;
    try {
        data = await fetch('/api/haven/coves').then(r => r.json());
    } catch (err) {
        el.innerHTML = `<div class="error-msg">${ESC(err.message || 'Failed to load Haven')}</div>`;
        return;
    }

    const coves = data.coves || [];
    const formed = !!data.formed;
    const isMember = !!data.member;   // batch-10 #4b: nested into someone else's Haven
    const haven = data.haven || {};
    MC._havenId = haven.haven_id || '';
    const cards = _havenCardsHtml(coves);

    if (!formed) {
        el.innerHTML = `
            <div style="font-weight:600;color:var(--text);">No Haven yet</div>
            <div style="font-size:0.72rem;color:var(--dim);margin-bottom:0.6rem;">A <strong>Haven</strong> is the network your Coves join. Naming one creates the Haven and its <strong>Commons</strong> — the shared room everyone in the Haven can talk in.</div>
            <div style="display:flex;gap:0.4rem;flex-wrap:wrap;margin-bottom:0.3rem;">
                <input id="haven-name" class="settings-input" placeholder="Name your Haven (e.g. Covington)" style="flex:1;min-width:180px;" oninput="_havenPreview()">
                <button class="btn-sm" onclick="havenCreate()">Form Haven</button>
            </div>
            <div id="haven-preview" style="font-size:0.68rem;color:var(--dim);min-height:1em;margin-bottom:0.3rem;"></div>
            <div id="haven-action-result" style="font-size:0.72rem;color:var(--dim);min-height:1em;"></div>
            <div style="margin-top:0.8rem;">${cards}</div>`;
        return;
    }

    // Member-side: this Cove was nested into a Haven it doesn't own. Show the belonging,
    // read-only — no Manage controls (only the owner forms/nests/invites).
    if (isMember) {
        el.innerHTML = `
            <div style="font-weight:600;color:var(--text);">You're part of ${ESC(haven.name || 'a Haven')}</div>
            <div style="font-size:0.72rem;color:var(--dim);margin-bottom:0.6rem;">This Cove is connected into the ${ESC(haven.name || '')} Haven. Its Commons and the other Coves show up in Connect.</div>
            <div>${cards}</div>`;
        return;
    }

    el.innerHTML = `
        <div style="font-weight:600;color:var(--text);">${ESC(haven.name || 'Haven')}</div>
        <div style="font-size:0.72rem;color:var(--dim);margin-bottom:0.6rem;">${coves.length} Cove${coves.length === 1 ? '' : 's'} in this Haven</div>
        <div style="margin-bottom:0.6rem;">${cards}</div>
        <div style="border-top:1px solid var(--border,#2a2a3a);padding-top:0.6rem;">
            <div style="font-size:0.75rem;font-weight:600;color:var(--text);margin-bottom:0.4rem;">Manage</div>
            <div style="display:flex;gap:0.4rem;flex-wrap:wrap;margin-bottom:0.4rem;">
                <input id="haven-nest" class="settings-input" placeholder="Nest a Cove (id or name)" style="flex:1;min-width:180px;">
                <button class="btn-sm" onclick="havenNest()">Nest</button>
            </div>
            <div style="display:flex;gap:0.4rem;flex-wrap:wrap;">
                <input id="haven-invite" class="settings-input" placeholder="Invite @handle:server" style="flex:1;min-width:180px;">
                <button class="btn-sm" onclick="havenInvite()">Invite</button>
            </div>
            <div id="haven-action-result" style="font-size:0.72rem;color:var(--dim);margin-top:0.4rem;min-height:1em;"></div>
        </div>`;
}

function _havenResult(msg) {
    const r = document.getElementById('haven-action-result');
    if (r) r.textContent = msg;
}

// Live preview of what forming a Haven will create — kills the T9 confusion where a
// name produced a "{name} Commons" room with no visible Haven. (batch-10 #4a)
function _havenPreview() {
    const el = document.getElementById('haven-preview');
    if (!el) return;
    const name = (document.getElementById('haven-name')?.value || '').trim();
    el.innerHTML = name
        ? `Creates &mdash; Haven: <strong>${ESC(name)}</strong> &middot; Commons room: <strong>${ESC(name)} Commons</strong>`
        : '';
}

async function _havenPost(url, body) {
    const r = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    let d = {};
    try { d = await r.json(); } catch (e) {}
    if (!r.ok || d.ok === false) {
        throw new Error(d.detail || d.error || `Request failed (${r.status})`);
    }
    return d;
}

async function havenCreate() {
    const name = (document.getElementById('haven-name')?.value || '').trim();
    if (!name) { _havenResult('Enter a Haven name.'); return; }
    _havenResult('Forming Haven...');
    try {
        const d = await _havenPost('/api/haven/create', { haven_id: _havenSlug(name), name });
        _havenResult(d.created === false ? `${name} already exists.` : `Formed ${name} + its ${name} Commons.`);
        loadHaven();
    } catch (err) { _havenResult('Couldn\'t form the Haven: ' + err.message); }
}

async function havenNest() {
    const cove = (document.getElementById('haven-nest')?.value || '').trim();
    if (!cove) { _havenResult('Enter a Cove id or name.'); return; }
    if (!MC._havenId) { _havenResult('No Haven.'); return; }
    _havenResult('Nesting Cove...');
    try {
        const d = await _havenPost(`/api/haven/${encodeURIComponent(MC._havenId)}/nest`, { cove });
        _havenResult(d.message || 'Cove nested.');
        loadHaven();
    } catch (err) { _havenResult('Couldn\'t nest that Cove: ' + err.message); }
}

async function havenInvite() {
    const user_id = (document.getElementById('haven-invite')?.value || '').trim();
    if (!user_id) { _havenResult('Enter @handle:server.'); return; }
    if (!MC._havenId) { _havenResult('No Haven.'); return; }
    _havenResult('Inviting...');
    try {
        const d = await _havenPost(`/api/haven/${encodeURIComponent(MC._havenId)}/invite`, { user_id });
        // Surface the REAL delivery result — the federation invite can be recorded but
        // fail to deliver, which used to show a misleading flat "Invited." (T9).
        _havenResult(d.message || (d.delivered === false ? 'Added, but the invite is still pending delivery.' : 'Invited.'));
    } catch (err) { _havenResult('Couldn\'t invite: ' + err.message); }
}
