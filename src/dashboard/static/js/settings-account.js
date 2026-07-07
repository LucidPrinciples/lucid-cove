// =============================================================================
// settings-account.js — Profile, Cloud, Tools, affiliate helpers
// =============================================================================

async function loadSettingsProfile() {
    const el = document.getElementById('settings-profile');
    if (!el) return;

    // Tier labels — same for all modes
    const tierLabels = { free: 'Lucid Tuner (Free)', pro: 'Tuner Pro', operator: 'Operator', presence: 'Presence', cove: 'Cove' };

    // Use MC.presence if we got it from /api/presence/me at boot (multi-Presence mode)
    const p = MC.presence;

    if (!p && (MC.tier?.level || 0) < 30) {
        // Not signed in, below Cove — shouldn't normally happen
        el.innerHTML = `<div style="padding:8px 0;font-size:0.8rem;color:var(--dim);">
            Not signed in. <a href="/" style="color:var(--accent);">Sign in</a> to see your profile.
        </div>`;
        return;
    }

    // ── Build profile fields ────────────────────────────────────────────
    // Multi-mode (MC.presence): editable Name, Handle, Email
    // Single-mode (Cove, no presence): read-only Name from config
    let fieldsHtml = '';
    if (p) {
        const currentTier = p.tier || 'free';
        const tierLabel = tierLabels[currentTier] || currentTier;
        fieldsHtml = `
            <div class="settings-edit-row">
                <label class="settings-label">Name</label>
                <input type="text" id="prof-display-name" class="settings-input"
                       value="${ESC(p.display_name || '')}" placeholder="Display name">
            </div>
            <div class="settings-edit-row">
                <label class="settings-label">Handle</label>
                <div style="display:flex;align-items:center;gap:2px;">
                    <span style="color:var(--dim);font-size:0.8rem;">@</span>
                    <input type="text" id="prof-username" class="settings-input"
                           value="${ESC(p.username || '')}" placeholder="username" style="flex:1;">
                </div>
            </div>
            <div class="settings-edit-row">
                <label class="settings-label">Email</label>
                <input type="email" id="prof-email" class="settings-input"
                       value="${ESC(p.email || '')}" placeholder="email@example.com">
            </div>
            <div class="settings-edit-row">
                <label class="settings-label">Timezone</label>
                <select id="prof-timezone" class="settings-input">
                    ${_buildTimezoneOptions(p.timezone || MC.instance?.timezone || 'America/New_York')}
                </select>
                <div style="font-size:0.65rem;color:var(--dim);margin-top:2px;">
                    ${p.timezone ? '' : 'Using Cove default'}
                </div>
            </div>
            <div class="settings-edit-row">
                <label class="settings-label">Tier</label>
                <div style="font-size:0.85rem;color:var(--text);padding:6px 0;">${ESC(tierLabel)}</div>
            </div>
            <div style="margin-top:12px;display:flex;gap:8px;align-items:center;">
                <button class="btn-sm" onclick="saveProfile()">Save</button>
                <span id="prof-save-result" style="font-size:0.75rem;color:var(--dim);"></span>
            </div>
            <div style="margin-top:12px;padding-top:12px;border-top:1px solid var(--border);">
                <div style="font-size:0.72rem;color:var(--dim);">
                    Account created ${p.created_at ? formatDateOnly(p.created_at) : 'recently'}
                </div>
            </div>`;
    } else {
        // Single-mode Cove — read-only identity from config
        const opName = MC.instance?.operator || 'Operator';
        const opHandle = MC.instance?.operator_handle || '';
        fieldsHtml = `
            <div class="settings-edit-row">
                <label class="settings-label">Name</label>
                <div style="font-size:0.85rem;color:var(--text);padding:6px 0;">${ESC(opName)}</div>
            </div>
            ${opHandle ? `<div class="settings-edit-row">
                <label class="settings-label">Handle</label>
                <div style="font-size:0.85rem;color:var(--text);padding:6px 0;">@${ESC(opHandle)}</div>
            </div>` : ''}
            <div class="settings-edit-row">
                <label class="settings-label">Tier</label>
                <div style="font-size:0.85rem;color:var(--text);padding:6px 0;">Cove</div>
            </div>`;
    }

    // Cove address moved to the admin-only "Cove Settings" section (loadSettingsCoveAdmin).
    // Self-host now lives in its own bottom section (loadSettingsSelfHost).
    // Affiliate program lives entirely in the Affiliates tab now — nothing here.
    el.innerHTML = fieldsHtml;
}

// =============================================================================
// Cove Settings — ADMIN ONLY (cove_role === 'admin'). Lives on the admin's own
// Presence MC so they can manage the whole Cove from one place: the Cove address,
// the Cove brain (the admin's own intelligence becomes the team default), and a
// pointer to member management.
// =============================================================================
async function loadSettingsCoveAdmin() {
    const el = document.getElementById('settings-cove-admin');
    if (!el) return;
    const p = MC.presence;
    const isAdmin = !!((p && p.cove_role === 'admin') || MC.adminView);
    if (!isAdmin) { el.innerHTML = ''; return; }

    const curDomain = (MC.config && MC.config.domain) || '';
    const addrHtml = `
        <div style="padding-bottom:12px;margin-bottom:12px;border-bottom:1px solid var(--border);">
            <label class="settings-label">Cove address</label>
            <div style="font-size:0.7rem;color:var(--dim);margin:2px 0 6px;">${curDomain ? 'Current: <strong style="color:var(--text);">' + ESC(curDomain) + '</strong> — everyone signs in at their-handle.' + ESC(curDomain) + '.' : 'No address set yet.'}</div>
            <input type="text" id="addr-domain" class="settings-input" value="${ESC(curDomain)}" placeholder="cove.yourdomain.com" style="width:100%;">
            <div style="margin-top:6px;"><button class="btn-sm" onclick="saveSettingsAddress()">Set address</button> <span id="addr-status" style="font-size:0.72rem;color:var(--dim);"></span></div>
            <div style="font-size:0.62rem;color:var(--dim);margin-top:4px;">Drives every link + turns on HTTPS. On a self-host the cert may need a one-time host step.</div>
        </div>`;

    // Public reachability (self-host only): a home Cove is mesh-only, so a REMOTE invite
    // link times out on an off-mesh phone. One host-side step opens a Cloudflare tunnel
    // (no port-forward, home IP hidden) so remote /join links resolve anywhere. Only shown
    // once an address exists — the tunnel needs a domain to route.
    const publicHtml = curDomain ? `
        <div style="padding-bottom:12px;margin-bottom:12px;border-bottom:1px solid var(--border);">
            <label class="settings-label">Public reachability</label>
            <div style="font-size:0.7rem;color:var(--dim);margin:2px 0 6px;">Make this Cove reachable from anywhere so you can invite people who aren't on your mesh. One-time setup on the Cove's machine; nothing changes for the people you invite.</div>
            <div style="margin-top:6px;"><button class="btn-sm" onclick="enablePublicCove(this)">Make this Cove public</button> <span id="public-status" style="font-size:0.72rem;color:var(--dim);"></span></div>
            <div id="public-cmd-out" style="display:none;margin-top:8px;font-size:0.7rem;"></div>
        </div>` : '';

    // The Cove brain — the admin's own intelligence becomes the team default for
    // every agent + scheduled job. jules 1656: this section renders on the Cove-admin
    // APEX, where there is NO "Your Agent" section above — scope the copy to where
    // the connect actually happens (the admin's own presence MC).
    const onApex = !!MC.coveAdminView;
    const brainHtml = `
        <div style="padding-bottom:12px;margin-bottom:12px;border-bottom:1px solid var(--border);">
            <label class="settings-label">Cove brain</label>
            <div style="font-size:0.7rem;color:var(--dim);margin:2px 0 0;">The intelligence your whole Cove runs on — every agent and scheduled job uses it unless a member connects their own. ${onApex
                ? 'Connect or change it from <strong>your own presence page</strong> (Settings → Your Agent, or the Add Intelligence card).'
                : 'Whatever provider &amp; model you connect under <strong>Your Agent</strong> above becomes that default.'}</div>
        </div>`;

    // Member management lives on the steward admin MC — link straight to it (no
    // future-state placeholder copy). Same reachable-host rule as the presence card:
    // the steward subdomain only when we're ALREADY on the claimed domain (DNS proven),
    // else the same-origin ?as= door that works on localhost/NAT.
    let stewardHref = '?as=stuart';
    try {
        const _dom = (typeof MC !== 'undefined' && MC.config && MC.config.domain) ? MC.config.domain : '';
        if (_dom && location.host.endsWith(_dom)) stewardHref = `${location.protocol}//stuart.${_dom}`;
    } catch (e) { /* same-origin fallback stands */ }
    const membersHtml = `
        <div>
            <label class="settings-label">Members</label>
            <div style="font-size:0.7rem;color:var(--dim);margin:2px 0 0;">Add or remove people and agents in the steward console. <a href="${stewardHref}" target="_blank" rel="noopener" style="color:var(--accent);">Open the steward console ↗</a></div>
        </div>`;

    el.innerHTML = addrHtml + publicHtml + brainHtml + membersHtml;
}

// Request making this Cove publicly reachable (Cloudflare tunnel). The app can't run
// docker or hold the CF token, so it hands back the one command to run on the box.
async function enablePublicCove(btn) {
    const out = document.getElementById('public-cmd-out');
    const status = document.getElementById('public-status');
    if (btn) { btn.disabled = true; }
    if (status) { status.textContent = 'Preparing…'; status.style.color = 'var(--dim)'; }
    try {
        const r = await fetch('/api/reachability/public', { method: 'POST' });
        const d = await r.json();
        if (!r.ok || d.error) { if (status) { status.textContent = d.error || 'Could not prepare.'; status.style.color = '#ff6b6b'; } if (btn) btn.disabled = false; return; }
        if (status) { status.textContent = 'Run this once on your Cove’s machine:'; status.style.color = 'var(--accent)'; }
        if (out) {
            out.style.display = 'block';
            out.innerHTML =
                '<div style="background:#0e0e16;border:1px solid var(--border);border-radius:6px;padding:8px 10px;font-family:ui-monospace,monospace;color:var(--accent);word-break:break-all;">' + ESC(d.host_command || '') + '</div>' +
                '<div style="color:var(--dim);margin-top:6px;line-height:1.5;">' + ESC(d.note || '') + '</div>';
        }
    } catch (e) {
        if (status) { status.textContent = 'Could not prepare — try again.'; status.style.color = '#ff6b6b'; }
    }
    if (btn) btn.disabled = false;
}

// =============================================================================
// Self-host a Cove — its own section at the BOTTOM of Settings.
// Available to EVERY signed-in account (Tuner/Operator/Cove), since self-hosting is
// exactly the path FROM Tuner/Operator → Cove. Mints + reveals the connect token once
// (their @handle shows with it), to paste into a self-host install's connect panel.
// =============================================================================
async function loadSettingsSelfHost() {
    const el = document.getElementById('settings-selfhost');
    if (!el) return;
    el.innerHTML = `
        <div style="font-size:0.72rem;color:var(--dim);margin-bottom:8px;">Running your own box? Get the connect key + config it needs to join the network as <strong>your @handle</strong>. Your current login stays active.</div>
        <button class="btn-sm" onclick="getSelfHostConfig(this)">Get my connect key</button>
        <div id="self-host-config-out" style="display:none;margin-top:8px;font-size:0.7rem;"></div>`;
}

async function saveSettingsAddress(confirmChange) {
    const domain = ((document.getElementById('addr-domain') || {}).value || '').trim().toLowerCase()
        .replace(/^https?:\/\//, '').split('/')[0];
    const status = document.getElementById('addr-status');
    if (!domain) { alert('Enter your address.'); return; }
    try {
        const r = await fetch('/api/domain/set', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ domain, confirm: !!confirmChange }),
        });
        const d = await r.json();
        // Changing an already-live address needs explicit confirmation before it repoints.
        if (d && d.code === 'confirm_change') {
            if (confirm(d.message || `Replace ${d.current_domain} with ${domain}?`)) {
                return saveSettingsAddress(true);
            }
            return;
        }
        if (!r.ok || d.error) { alert(d.error || 'Could not set the address.'); return; }
        if (status) {
            status.style.color = 'var(--green)';
            if (d.records && d.records.length) {
                status.textContent = 'Saved. Add the DNS records shown, then reload.';
            } else {
                // Changing the address repoints every link, so hand the operator the sign-in
                // door at the NEW address (d.door = /p/{token}) instead of silently reloading
                // them onto a login wall. The door only resolves once DNS + cert exist for the
                // new address, so it's framed as "open it there," matching the claim card.
                const _door = d.door || ('https://' + d.domain);
                status.innerHTML = `Address set to <b>https://${ESC(d.domain)}</b>. The secure connection takes about a minute, then open it there, already signed in: `
                    + `<a href="${ESC(_door)}" target="_blank" rel="noopener" style="color:var(--accent);">Open my Cove &#8599;</a>`
                    + `<div style="margin-top:8px;"><button class="btn-sm" onclick="location.reload()">Refresh settings</button></div>`;
            }
        }
        // No auto-reload: the records path stays put so the operator can copy them, and the
        // address-change path now hands back the door above rather than reloading onto a login
        // wall. The operator refreshes on their own click.
    } catch (e) { alert('Could not set: ' + e.message); }
}

async function saveProfile() {
    const result = document.getElementById('prof-save-result');
    if (!result) return;
    result.textContent = 'Saving...';
    result.style.color = 'var(--dim)';

    const body = {};
    const nameEl = document.getElementById('prof-display-name');
    const userEl = document.getElementById('prof-username');
    const emailEl = document.getElementById('prof-email');
    if (nameEl) body.display_name = nameEl.value.trim();
    if (userEl) body.username = userEl.value.trim();
    if (emailEl) body.email = emailEl.value.trim();
    const tzEl = document.getElementById('prof-timezone');
    if (tzEl) body.timezone = tzEl.value;

    try {
        const res = await fetch('/api/presence/me', {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await res.json();
        if (data.ok) {
            result.textContent = 'Saved.';
            result.style.color = 'var(--green, #4caf50)';
            // Update MC.presence in memory
            if (MC.presence) {
                if (body.display_name) MC.presence.display_name = body.display_name;
                if (body.username) MC.presence.username = body.username;
                if (body.email) MC.presence.email = body.email;
            }
        } else {
            result.textContent = 'Error: ' + (data.detail || data.error || 'Unknown');
            result.style.color = 'var(--red, #f44336)';
        }
    } catch (err) {
        result.textContent = 'Error: ' + err.message;
        result.style.color = 'var(--red, #f44336)';
    }
}

// =============================================================================
// Cloud Storage — unified NC credentials for all tiers (Operator+)
// =============================================================================

async function loadSettingsCloud() {
    const group = document.getElementById('settings-cloud-group');
    const el = document.getElementById('settings-cloud');
    if (!group || !el) return;

    let ncUser = '', ncPass = '', ncUrl = '';

    // Multi-mode: credentials from presence record
    if (MC.presence && MC.presence.has_cloud) {
        ncUser = MC.presence.nc_username || '';
        ncPass = MC.presence.nc_password || '';
        ncUrl = MC.config?.nextcloud_public_url || '';
    }
    // Single-mode (Cove): credentials from NC settings API
    else if (MC.config?.nextcloud_public_url || MC.isCove) {
        try {
            const data = await fetch('/api/settings/nextcloud').then(r => r.json());
            if (data.username && data.username !== 'not configured') {
                ncUser = data.username;
                ncPass = data.password || '';
                ncUrl = MC.config?.nextcloud_public_url || '';
            }
        } catch(e) {}
    }

    if (!ncUser) return; // No NC configured — leave section hidden

    group.style.display = '';
    el.innerHTML = `
        <div style="font-size:0.72rem;color:var(--dim);margin-bottom:8px;">Your private cloud for files, calendar, and documents.</div>
        <div class="settings-edit-row">
            <label class="settings-label">Username</label>
            <div style="font-size:0.82rem;color:var(--text);padding:4px 0;font-family:monospace;">${ESC(ncUser)}</div>
        </div>
        ${ncPass ? `<div class="settings-edit-row">
            <label class="settings-label">Password</label>
            <div style="display:flex;align-items:center;gap:6px;">
                <span id="cloud-pass-display" style="font-size:0.82rem;color:var(--text);font-family:monospace;">••••••••</span>
                <button class="btn-sm" style="font-size:0.68rem;padding:2px 6px;" onclick="const s=document.getElementById('cloud-pass-display');if(s.textContent==='••••••••'){s.textContent='${ESC(ncPass)}';this.textContent='Hide';}else{s.textContent='••••••••';this.textContent='Show';}">Show</button>
            </div>
        </div>` : ''}
        ${ncUrl ? `<div class="settings-edit-row">
            <label class="settings-label">Server</label>
            <div style="font-size:0.78rem;color:var(--text);padding:4px 0;">${ESC(ncUrl)}</div>
        </div>` : ''}
        <div style="margin-top:8px;display:flex;gap:8px;align-items:center;">
            ${ncUrl ? `<a href="${ncUrl}" target="_blank" class="btn-sm" style="text-decoration:none;">Open Cloud</a>` : ''}
        </div>
        <div style="font-size:0.65rem;color:var(--dim);margin-top:6px;">Use these credentials to log in at the web link above, or to connect the Nextcloud app on your phone.</div>
    `;
}

// ── Connect / Chat (Matrix) credentials — for Element or another client ──────
async function loadSettingsConnect() {
    const group = document.getElementById('settings-connect-group');
    const el = document.getElementById('settings-connect');
    if (!group || !el) return;

    let creds;
    try {
        creds = await fetch('/api/matrix/credentials').then(r => r.json());
    } catch (e) { return; }
    if (!creds) return;

    group.style.display = '';
    if (!creds.provisioned) {
        el.innerHTML = `<div style="font-size:0.72rem;color:var(--dim);">Your chat account is created the first time you open <strong>Connect</strong>. Open it once, then your sign-in details for other Matrix apps (like Element) will appear here.</div>`;
        return;
    }
    const hs = creds.homeserver || '', user = creds.username || '', pw = creds.password || '';
    // No client-reachable homeserver yet (fresh Cove, no address claimed) — the server
    // now returns homeserver:null instead of a dead localhost URL. Say what unlocks it.
    const hsHtml = hs
        ? `<div style="font-size:0.78rem;color:var(--text);padding:4px 0;">${ESC(hs)}</div>`
        : `<div style="font-size:0.72rem;color:var(--dim);padding:4px 0;">Available once you set your Cove address (Settings → Cove address).</div>`;
    el.innerHTML = `
        <div style="font-size:0.72rem;color:var(--dim);margin-bottom:8px;">Your chat identity. Use these to sign in with Element or any Matrix app.</div>
        <div class="settings-edit-row">
            <label class="settings-label">Homeserver</label>
            ${hsHtml}
        </div>
        <div class="settings-edit-row">
            <label class="settings-label">Username</label>
            <div style="font-size:0.82rem;color:var(--text);padding:4px 0;font-family:monospace;">${ESC(user)}</div>
        </div>
        ${pw ? `<div class="settings-edit-row">
            <label class="settings-label">Password</label>
            <div style="display:flex;align-items:center;gap:6px;">
                <span id="mx-pass-display" style="font-size:0.82rem;color:var(--text);font-family:monospace;">••••••••</span>
                <button class="btn-sm" style="font-size:0.68rem;padding:2px 6px;" onclick="const s=document.getElementById('mx-pass-display');if(s.textContent==='••••••••'){s.textContent='${ESC(pw)}';this.textContent='Hide';}else{s.textContent='••••••••';this.textContent='Show';}">Show</button>
            </div>
        </div>` : `<div style="font-size:0.65rem;color:var(--dim);">Managed by this Cove's configuration.</div>`}
        <div style="font-size:0.65rem;color:var(--dim);margin-top:6px;">In-app Connect signs you in automatically — these are for connecting another device or client.</div>
    `;
}

// =============================================================================
// Tools — jules link and future Presence tools
// =============================================================================

async function loadSettingsTools() {
    const el = document.getElementById('settings-tools');
    if (!el) return;

    // Build jules URL — served from the MC itself at /jules
    const origin = window.location.origin;
    const julesUrl = `${origin}/jules`;

    el.innerHTML = `
        <div style="font-size:0.72rem;color:var(--dim);margin-bottom:8px;">Voice tools for your Presence. Tap a link to open, or add to your phone's home screen.</div>
        <div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--border);">
            <span style="font-size:1.4rem;">🎙</span>
            <div style="flex:1;min-width:0;">
                <div style="font-size:0.82rem;font-weight:600;color:var(--text);">jules</div>
                <div style="font-size:0.68rem;color:var(--dim);">Voice transcription — tap, talk, save to vault</div>
            </div>
            <a href="${julesUrl}" target="_blank"
               style="font-size:0.75rem;color:var(--accent);text-decoration:none;padding:4px 10px;border:1px solid var(--accent);border-radius:6px;white-space:nowrap;">
                Open →
            </a>
        </div>
        <div style="font-size:0.62rem;color:var(--dim);margin-top:6px;">
            Tip: Open jules, then use your browser's "Add to Home Screen" to create a shortcut.
        </div>
    `;
}

// =============================================================================
// Devices & Access — add another device to this Presence, and (on a self-hosted
// Cove) mint a mesh join code. Sign-in links never invalidate existing sessions.
// =============================================================================
async function loadSettingsDevices() {
    const el = document.getElementById('settings-devices');
    if (!el) return;

    const p = MC.presence;
    const hasAgent = !!(MC.config && MC.config.has_personal_agent)
        || !!(MC.tier && (MC.tier.has_agent || MC.tier.level >= 20));

    // ── B12: the two layers, up front. A phone needs BOTH, in order: the join code puts
    // the DEVICE on the mesh (network); the sign-in link signs the PERSON into the Cove
    // (identity). Only show the framing when this account actually has both controls. ──
    const layersHtml = (p && hasAgent) ? `
        <div style="padding-bottom:10px;margin-bottom:10px;border-bottom:1px solid var(--border);font-size:0.7rem;color:var(--dim);line-height:1.55;">
            <strong style="color:var(--text);">Two layers, in order:</strong>
            a <strong style="color:var(--text);">join code</strong> puts a <em>device</em> on your Cove's private network (the mesh) so it can reach the box; a
            <strong style="color:var(--text);">sign-in link</strong> signs a <em>person</em> into the Cove (their identity, files, agent). A phone needs both — network first, then identity.
        </div>` : '';

    // ── Mesh join code FIRST — it's the prerequisite (a phone can't use a sign-in
    // link until it can reach the box). Chords, run-3 T5 note, 2026-07-04. Only
    // meaningful on a self-hosted Cove; hosted Operators don't run a box. ──
    const meshHtml = hasAgent ? `
        <div style="padding-bottom:10px;margin-bottom:10px;border-bottom:1px solid var(--border);">
            <label class="settings-label">Step 1 — Connect a device to your private network (mesh)</label>
            <div style="font-size:0.7rem;color:var(--dim);margin:2px 0 6px;">A <strong>join code</strong> puts a device (laptop, server, or phone) on your Cove's mesh so it can reach the box. This is the network layer — the phone then needs a sign-in link (below) to open as you.</div>
            <button class="btn-sm" onclick="getDevicesMeshKey(this)">Get join code</button>
            <div id="devices-mesh-out" style="display:none;margin-top:8px;font-size:0.7rem;"></div>
            <div style="font-size:0.62rem;color:var(--dim);margin-top:8px;line-height:1.5;">
                <strong>On a phone?</strong> Install the Tailscale app, tap the <strong>⋯ menu (top right)</strong> and choose
                <em>“Use a custom coordination server”</em> and enter
                <code style="background:var(--bg-card);padding:1px 4px;border-radius:3px;">https://headscale.lucidcove.org</code>,
                sign in, and approve the device. If Tailscale is already signed in to another network, log out first (this is a separate tailnet).
            </div>
        </div>` : '';

    // ── Sign-in link — multi-Presence accounts can add a phone/laptop/tablet ──
    const signinHtml = p ? `
        <div style="padding-bottom:10px;margin-bottom:10px;border-bottom:1px solid var(--border);">
            <label class="settings-label">${hasAgent ? 'Step 2 — ' : ''}Add another device (identity)</label>
            <div style="font-size:0.7rem;color:var(--dim);margin:2px 0 6px;">A personal <strong>sign-in link</strong> signs <em>you</em> into the Cove on a phone or another browser — your files and agent. Your current sessions stay signed in.${hasAgent ? ' (On a phone, put it on the mesh first, above.)' : ''}</div>
            <button class="btn-sm" onclick="createSigninLink(this)">Create sign-in link</button>
            <div id="signin-link-out" style="display:none;margin-top:8px;"></div>
        </div>` : '';

    // ── My door link — batch-10 #2. The operator's own signed-in link to their Cove.
    // `/p/` tokens are stored hashed only, so we can't read the current raw token back;
    // this row MINTS a fresh working link from the live token store on demand (old links
    // stay valid — sessions are never invalidated). This is the reliable door that the
    // done-cards used to try to build from the stale cove.yaml token (and 401 on). ──
    const doorHtml = p ? `
        <div style="padding-bottom:10px;margin-bottom:10px;border-bottom:1px solid var(--border);">
            <label class="settings-label">My door link</label>
            <div style="font-size:0.7rem;color:var(--dim);margin:2px 0 6px;">Your personal signed-in link to this Cove. Bookmark it, or open it on another browser to sign in as you. Always current — it's minted fresh from your Cove, not a stored link that can go stale.</div>
            <button class="btn-sm" onclick="showMyDoorLink(this)">Show my door link</button>
            <div id="my-door-out" style="display:none;margin-top:8px;"></div>
        </div>` : '';

    // ── Active sessions — every signed-in device, with revoke (not this one) ──
    const sessionsHtml = p ? `
        <div style="padding-bottom:10px;margin-bottom:10px;">
            <label class="settings-label">Signed-in devices</label>
            <div id="devices-sessions" style="margin-top:6px;font-size:0.72rem;color:var(--dim);">Loading…</div>
        </div>` : '';

    if (!signinHtml && !sessionsHtml && !meshHtml && !doorHtml) { el.innerHTML = `<div style="font-size:0.7rem;color:var(--dim);">No device options for this account.</div>`; return; }
    el.innerHTML = layersHtml + meshHtml + doorHtml + signinHtml + sessionsHtml;
    if (sessionsHtml) loadDeviceSessions();
}

// Mint + show the operator's current working door link (batch-10 #2). Reuses the
// regenerate-link mechanism (mints into the live auth_sessions store); old links stay
// valid, so re-showing is safe. Raw tokens can't be read back from their hashes, so
// minting fresh IS the resolution of "the current door."
async function showMyDoorLink(btn) {
    const out = document.getElementById('my-door-out');
    const p = MC.presence;
    if (!p || !p.id) { if (out) { out.style.display = 'block'; out.textContent = 'A door link is only available on a Cove.'; } return; }
    if (btn) { btn.disabled = true; btn.textContent = '…'; }
    try {
        const r = await fetch(`/api/presence/${p.id}/regenerate-link`, { method: 'POST' });
        const d = await r.json();
        if (!r.ok || d.error || !d.signin_link) { if (out) { out.style.display = 'block'; out.textContent = d.error || 'Could not build your door link.'; } return; }
        if (out) {
            out.style.display = 'block';
            out.innerHTML =
                '<div style="color:var(--dim);margin-bottom:4px;font-size:0.7rem;">Your door — open or bookmark this:</div>'
                + '<div style="display:flex;gap:6px;align-items:center;">'
                + `<input type="text" readonly id="my-door-input" value="${ESC(d.signin_link)}" style="flex:1;min-width:0;font-size:0.72rem;padding:5px 7px;background:var(--bg-card);color:var(--text);border:1px solid var(--border);border-radius:5px;">`
                + `<button class="btn-sm" style="white-space:nowrap;" onclick="copyAffiliateLink('my-door-input')">Copy</button>`
                + '</div>';
        }
    } catch (e) {
        if (out) { out.style.display = 'block'; out.textContent = 'Could not build your door link: ' + e.message; }
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = 'Show my door link'; }
    }
}

async function loadDeviceSessions() {
    const out = document.getElementById('devices-sessions');
    if (!out) return;
    try {
        const d = await fetch('/api/presence/sessions').then(r => r.json());
        const sessions = d.sessions || [];
        if (!sessions.length) { out.innerHTML = 'No active sessions.'; return; }
        out.innerHTML = sessions.map(s => {
            const when = s.last_used ? formatDateOnly(s.last_used) : '';
            return `<div style="display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid var(--border);">
                <div style="flex:1;min-width:0;">
                    <span style="color:var(--text);">${ESC(s.device_label || 'Device')}</span>
                    ${s.current ? '<span style="color:var(--accent);font-size:0.66rem;"> · this device</span>' : ''}
                    ${when ? `<div style="font-size:0.64rem;color:var(--dim);">last used ${ESC(when)}</div>` : ''}
                </div>
                ${s.current ? '' : `<button class="btn-sm" style="font-size:0.68rem;color:var(--red);" onclick="revokeDeviceSession('${ESC(s.id)}', this)">Sign out</button>`}
            </div>`;
        }).join('');
    } catch (e) {
        out.innerHTML = 'Could not load sessions.';
    }
}

async function revokeDeviceSession(id, btn) {
    if (!confirm('Sign this device out? It will need a new sign-in link to get back in.')) return;
    if (btn) { btn.disabled = true; btn.textContent = '…'; }
    try {
        const r = await fetch('/api/presence/sessions/revoke', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id }),
        });
        const d = await r.json();
        if (!r.ok || d.ok === false) { alert((d && d.error) || 'Could not sign that device out.'); if (btn) { btn.disabled = false; btn.textContent = 'Sign out'; } return; }
        loadDeviceSessions();
    } catch (e) {
        alert('Could not sign that device out: ' + e.message);
        if (btn) { btn.disabled = false; btn.textContent = 'Sign out'; }
    }
}

async function createSigninLink(btn) {
    const out = document.getElementById('signin-link-out');
    const p = MC.presence;
    if (!p || !p.id) { if (out) { out.style.display = 'block'; out.textContent = 'Sign-in links are only available on a Cove.'; } return; }
    if (btn) { btn.disabled = true; btn.textContent = '…'; }
    try {
        const r = await fetch(`/api/presence/${p.id}/regenerate-link`, { method: 'POST' });
        const d = await r.json();
        if (!r.ok || d.error || !d.signin_link) { if (out) { out.style.display = 'block'; out.textContent = d.error || 'Could not create a link.'; } return; }
        if (out) {
            out.style.display = 'block';
            out.innerHTML =
                '<div style="color:var(--dim);margin-bottom:4px;font-size:0.7rem;">Open this on the new device to sign in:</div>'
                + '<div style="display:flex;gap:6px;align-items:center;">'
                + `<input type="text" readonly id="signin-link-input" value="${ESC(d.signin_link)}" style="flex:1;min-width:0;font-size:0.72rem;padding:5px 7px;background:var(--bg-card);color:var(--text);border:1px solid var(--border);border-radius:5px;">`
                + `<button class="btn-sm" style="white-space:nowrap;" onclick="copyAffiliateLink('signin-link-input')">Copy</button>`
                + '</div>';
        }
    } catch (e) {
        if (out) { out.style.display = 'block'; out.textContent = 'Could not create a link: ' + e.message; }
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = 'Create sign-in link'; }
    }
}

async function getDevicesMeshKey(btn) {
    const out = document.getElementById('devices-mesh-out');
    if (btn) { btn.disabled = true; btn.textContent = '…'; }
    try {
        const r = await fetch('/api/onboarding/mesh-key');
        const d = await r.json();
        if (out) {
            out.style.display = 'block';
            if (d.ok && d.join_cmd) {
                out.innerHTML = '<div style="color:var(--dim);margin-bottom:4px;">Run this on the device (valid ~1h):</div>'
                    + '<code style="display:block;padding:6px;background:var(--bg-card);border:1px solid var(--border);border-radius:4px;word-break:break-all;">'
                    + ESC(d.join_cmd) + '</code>';
            } else {
                out.innerHTML = '<div style="color:var(--orange);">' + ESC(d.reason || 'Could not mint a join code here.') + '</div>'
                    + (d.instructions ? '<div style="color:var(--dim);margin-top:4px;">' + ESC(d.instructions) + '</div>' : '');
            }
        }
    } catch (e) {
        if (out) { out.style.display = 'block'; out.textContent = 'Could not reach the mesh service.'; }
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = 'Get join code'; }
    }
}

async function getSelfHostConfig(btn) {
    const out = document.getElementById('self-host-config-out');
    if (!confirm('This issues a new connect key and shows it once. Your current login stays active. Continue?')) return;
    if (btn) { btn.disabled = true; btn.textContent = '…'; }
    try {
        const r = await fetch('/api/account/self-host-token', { method: 'POST' });
        const d = await r.json();
        if (!r.ok || d.error) { alert(d.error || 'Could not issue a token.'); return; }
        if (out) {
            // Lead with THE CONNECT KEY under that exact name — it's what the setup
            // wizard tells the user to come here and copy ("paste your connect key").
            // The .env / cove.config forms are the same key for hand-setup, secondary.
            out.style.display = 'block';
            out.innerHTML =
                '<div style="color:var(--orange);margin-bottom:6px;">Your <strong>connect key</strong> — shown once, copy it now.</div>'
                + '<div style="color:var(--dim);margin-bottom:4px;">Paste it into the Cove setup wizard where it asks for your connect key:</div>'
                + '<div style="display:flex;gap:6px;align-items:center;margin-bottom:8px;">'
                +   '<input type="text" readonly id="connect-key-out" value="' + ESC(d.token || '') + '"'
                +     ' style="flex:1;font-family:monospace;font-size:0.72rem;padding:6px;background:var(--bg-card);color:var(--text);border:1px solid var(--border);border-radius:5px;min-width:0;">'
                +   '<button class="btn-sm" style="white-space:nowrap;" onclick="var i=document.getElementById(\'connect-key-out\');i.select();navigator.clipboard.writeText(i.value);this.textContent=\'Copied\';">Copy</button>'
                + '</div>'
                + '<div style="color:var(--dim);margin:6px 0 4px;">Setting up by hand instead? Same key, two other forms — your Cove\'s <code>.env</code>:</div>'
                + '<textarea readonly rows="2" style="width:100%;font-family:monospace;font-size:0.68rem;padding:6px;background:var(--bg-card);color:var(--text);border:1px solid var(--border);border-radius:5px;">'
                + ESC(d.env_snippet) + '</textarea>'
                + '<div style="color:var(--dim);margin:6px 0 4px;">…or as <code>operator.token</code> in your cove.config:</div>'
                + '<textarea readonly rows="3" style="width:100%;font-family:monospace;font-size:0.68rem;padding:6px;background:var(--bg-card);color:var(--text);border:1px solid var(--border);border-radius:5px;">'
                + ESC(d.config_snippet) + '</textarea>'
                + '<div style="color:var(--dim);margin-top:4px;">' + ESC(d.note || '') + '</div>';
        }
    } catch (e) {
        alert('Could not issue a token: ' + e.message);
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = 'Get my connect key'; }
    }
}

function _buildAffiliateLink(label, destUrl, code) {
    const url = `https://app.lucidcove.org/r/${code}?to=${destUrl}`;
    const id = 'aff-' + label.replace(/\s+/g, '-').toLowerCase();
    return `<div style="display:flex;align-items:center;gap:6px;">
        <div style="flex:0 0 auto;font-size:0.7rem;color:var(--dim);width:110px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${label}</div>
        <input type="text" readonly id="${id}" value="${url}"
               style="flex:1;font-size:0.72rem;padding:5px 7px;background:var(--bg-card);color:var(--text);border:1px solid var(--border);border-radius:5px;cursor:text;min-width:0;">
        <button class="btn-sm" onclick="copyAffiliateLink('${id}')" style="white-space:nowrap;font-size:0.7rem;padding:4px 8px;">Copy</button>
    </div>`;
}

function copyAffiliateLink(inputId) {
    const input = document.getElementById(inputId);
    if (!input) return;
    navigator.clipboard.writeText(input.value).then(() => {
        const btn = input.nextElementSibling;
        if (btn) { btn.textContent = 'Copied!'; setTimeout(() => { btn.textContent = 'Copy'; }, 1500); }
    }).catch(() => {
        input.select();
        document.execCommand('copy');
        const btn = input.nextElementSibling;
        if (btn) { btn.textContent = 'Copied!'; setTimeout(() => { btn.textContent = 'Copy'; }, 1500); }
    });
}
