// =============================================================================
// settings.js — Settings tab dispatcher + shared utilities
//
// Sub-modules (loaded on first switchToTab('settings') via _settingsSubsLoaded guard):
//   settings-account.js  — Profile, Cloud, Tools, affiliate helpers
//   settings-voice.js    — Voice selection from pipecat-voice
//   settings-mirrors.js  — Mirror registry, features, manager modal, drag reorder
//   settings-tuning.js   — Signal filter, LTP settings, Cove timezone
//   settings-admin.js    — Status, Nextcloud, Model, Model Registry, Symbol, Presences
// =============================================================================

// ── Load sub-modules on first use ────────────────────────────────────────────
let _settingsSubsLoaded = false;
async function _loadSettingsSubs() {
    if (_settingsSubsLoaded) return;
    _settingsSubsLoaded = true;
    const subs = [
        'settings-account',
        'settings-voice',
        'settings-mirrors',
        'settings-tuning',
        'settings-admin',
    ];
    await Promise.all(subs.map(s => loadScript(`/static/js/${s}.js`)));
}

// Common IANA timezones for the dropdown (US-centric with international coverage)
const _TIMEZONES = [
    ['America/New_York', 'Eastern (ET)'],
    ['America/Chicago', 'Central (CT)'],
    ['America/Denver', 'Mountain (MT)'],
    ['America/Los_Angeles', 'Pacific (PT)'],
    ['America/Anchorage', 'Alaska (AKT)'],
    ['Pacific/Honolulu', 'Hawaii (HT)'],
    ['America/Phoenix', 'Arizona (no DST)'],
    ['America/Puerto_Rico', 'Atlantic (AT)'],
    ['Europe/London', 'London (GMT/BST)'],
    ['Europe/Paris', 'Central European (CET)'],
    ['Europe/Berlin', 'Berlin (CET)'],
    ['Europe/Moscow', 'Moscow (MSK)'],
    ['Asia/Tokyo', 'Tokyo (JST)'],
    ['Asia/Shanghai', 'China (CST)'],
    ['Asia/Kolkata', 'India (IST)'],
    ['Asia/Dubai', 'Dubai (GST)'],
    ['Australia/Sydney', 'Sydney (AEST)'],
    ['Australia/Perth', 'Perth (AWST)'],
    ['Pacific/Auckland', 'New Zealand (NZST)'],
    ['America/Toronto', 'Toronto (ET)'],
    ['America/Vancouver', 'Vancouver (PT)'],
    ['America/Mexico_City', 'Mexico City (CST)'],
    ['America/Sao_Paulo', 'Sao Paulo (BRT)'],
    ['Africa/Johannesburg', 'South Africa (SAST)'],
    ['UTC', 'UTC'],
];

function _buildTimezoneOptions(selected) {
    // If the selected timezone isn't in our list, add it at the top
    const known = _TIMEZONES.map(t => t[0]);
    let extra = '';
    if (selected && !known.includes(selected)) {
        const sel = ' selected';
        extra = `<option value="${selected}"${sel}>${selected}</option>`;
    }
    return extra + _TIMEZONES.map(([tz, label]) => {
        const sel = tz === selected ? ' selected' : '';
        return `<option value="${tz}"${sel}>${label}</option>`;
    }).join('');
}

async function loadSettings() {
    // Load sub-modules if not already loaded
    await _loadSettingsSubs();

    // Cove/system-WIDE settings only on the admin doors: the manager MC
    // (stuart.{cove}, MC.adminView) AND the Cove apex ({cove}.{domain},
    // MC.coveAdminView). The apex swaps Presences (its own tab) for the Cove
    // Address group; the manager door keeps Presences. The operator's personal
    // profile/agent/voice/tuning live in their own presence MC, never here.
    if (MC.adminView || MC.coveAdminView) {
        const tasks = [
            loadSettingsStatus(),
            loadSettingsNextcloud(),
            loadSettingsMatrixAdmin(),
            loadSettingsModel(),
            loadSettingsModelRegistry(),
            loadSettingsCompute(),
            loadSettingsLTP(),
            loadSettingsAgentSymbol(),
        ];
        if (MC.adminView) tasks.push(loadSettingsPresences());
        if (MC.coveAdminView) tasks.push(loadSettingsCoveAdmin());
        await Promise.all(tasks);
        return;
    }

    // Personal MC — PERSONAL settings only. Cove-ops (Model Registry, Nextcloud
    // admin, LTP, System, Presences) live exclusively on the Stuart admin MC
    // (the adminView branch above), never on a presence's own MC.
    //
    // Tier gating (matches the settings panel groups in panels.js):
    //   Agent model + Voice + voice Tools (jules) → only with an agent (Presence/Cove).
    //   Cloud Storage → Operator and up.
    //   Profile + Tuning (mirrors + signal filter) → every tier.
    // Tuner/Operator have no agent, so they should never see those sections.
    // A real Cove operator HAS an agent (Intelligence/voice/tools sections) regardless of
    // tier — the public agentless app is the only place to hide them. Match the chat gate
    // so a stray tier can't hide the operator's own model/key. (#chat-settings agent-gate)
    const hasAgent = !(MC.config && MC.config.is_public_app)
        || !!(MC.config && MC.config.has_personal_agent)
        || !!(MC.tier && (MC.tier.has_agent || MC.tier.level >= 20));
    const hasCloud = !!(MC.tier && MC.tier.level >= 10);
    const personal = [
        loadSettingsProfile(),
        loadSettingsFeatures(),
        loadSettingsSignalFilter(),
        loadSettingsSelfHost(),
    ];
    if (hasCloud) personal.push(loadSettingsCloud(), loadSettingsConnect(), loadSettingsDevices());
    if (hasAgent) personal.push(loadSettingsMyModel(), loadSettingsVoice(), loadSettingsTools());
    if (MC.presence && MC.presence.cove_role === 'admin') personal.push(loadSettingsCoveAdmin());
    await Promise.all(personal);
}

// Presence personal-agent model override — cascades over the Stuart-set team default.
async function loadSettingsMyModel() {
    const el = document.getElementById('settings-my-model');
    if (!el) return;
    try {
        const [data, mk] = await Promise.all([
            fetch('/api/settings/my-model').then(r => r.json()),
            fetch('/api/settings/model-key').then(r => r.json()).catch(() => ({})),
        ]);
        if (data.error) { el.innerHTML = `<div class="error-msg">${ESC(data.error)}</div>`; return; }
        const catalog = data.catalog || [];
        const agentName = data.agent_name || 'your agent';
        const opts = (sel) => ['<option value="">— use Cove default —</option>'].concat(
            catalog.map(m => `<option value="${ESC(m.id)}"${m.id === sel ? ' selected' : ''}>${ESC(m.name)}${m.type ? ' · ' + ESC(m.type) : ''}</option>`)
        ).join('');

        // ── Source: is this agent running on the Cove default, or this presence's own
        // override? Both signals come back from the two GETs, so we resolve it client-side. ──
        const hasProvider = !!mk.provider;
        const hasModelOverride = !!(data.primary || data.fallback);
        const usingOwn = hasProvider || hasModelOverride;
        // jules 1816: a fresh Cove has NO real default — say so instead of
        // presenting a preselected model no one ever chose.
        const defaultSet = data.default_set !== false && !!data.default_primary_name;
        const defaultStr = defaultSet
            ? (data.default_primary_name
                + (data.default_fallback_name ? ' → ' + data.default_fallback_name : ''))
            : 'not set — add intelligence';
        const ownModelStr = (() => {
            const find = (id) => (catalog.find(m => m.id === id) || {}).name || id;
            if (!hasModelOverride) return '';
            return ESC(find(data.primary || data.fallback));
        })();
        const banner = `<div style="margin-bottom:10px;padding:8px 10px;border-radius:6px;background:var(--bg-card);border:1px solid ${usingOwn ? 'var(--accent)' : 'var(--border)'};">
            <div style="font-size:0.72rem;color:var(--text);">
                ${usingOwn
                    ? `<strong>${ESC(agentName)}</strong> is running on <strong style="color:var(--accent);">your own setup</strong>${hasProvider ? ' · ' + ESC(mk.provider) + (mk.has_key ? ' (key set)' : '') : ''}${ownModelStr ? ' · ' + ownModelStr : ''}.`
                    : (defaultSet
                        ? `<strong>${ESC(agentName)}</strong> is running on the <strong>Cove default</strong> — <span style="color:var(--text);">${ESC(defaultStr)}</span>, set by your steward.`
                        : `<strong>${ESC(agentName)}</strong> has <strong>no intelligence set yet</strong> — connect a provider below, or use the Add Intelligence setup card.`)}
            </div>
            ${hasModelOverride ? `<div style="margin-top:6px;"><button class="btn-sm" onclick="resetToCoveDefault(this)">Use Cove default model</button> <span id="reset-default-status" style="font-size:0.68rem;color:var(--dim);"></span></div>` : ''}
        </div>`;

        // BYOK provider + key — the actual intelligence connection (change anytime).
        const provOpt = (v, label) => `<option value="${v}"${mk.provider === v ? ' selected' : ''}>${label}</option>`;
        const byok = `<div style="margin-bottom:10px;padding-bottom:10px;border-bottom:1px solid var(--border);">
            <div style="font-size:0.7rem;color:var(--dim);margin-bottom:6px;">Connect your own provider & key to power ${ESC(agentName)} yourself instead of the Cove default.</div>
            <select id="byok-provider" class="settings-input" style="max-width:240px;">
                <option value="">Choose provider…</option>
                ${provOpt('openrouter', 'OpenRouter (Claude, GPT &amp; more)')}
                ${provOpt('openai', 'OpenAI')}${provOpt('google', 'Google')}${provOpt('groq', 'Groq')}
                ${provOpt('ollama', 'Ollama (local — no key)')}
            </select>
            <input id="byok-key" class="settings-input" placeholder="${mk.has_key ? '••• key set — type to replace' : 'API key (blank for Ollama)'}" style="margin-top:6px;width:100%;">
            <div style="margin-top:6px;display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
                <button class="btn-sm" onclick="saveSettingsModelKey()">Save provider</button>
                ${hasProvider ? `<button class="btn-sm" style="background:transparent;border:1px solid var(--border);color:var(--dim);" onclick="disconnectBYOK(this)">Use Cove default (disconnect)</button>` : ''}
                <span id="byok-status" style="font-size:0.7rem;color:var(--dim);"></span>
            </div>
        </div>`;
        // xAI OAuth section — device-code flow for Grok models
        const xaiStatus = await fetch('/api/xai/auth/status').then(r => r.json()).catch(() => ({authorized: false}));
        const xaiSection = `<div style="margin:10px 0;padding:10px;border-radius:6px;background:var(--bg-card);border:1px solid var(--border);">
            <div style="font-size:0.75rem;color:var(--text);margin-bottom:6px;"><strong>xAI (Grok)</strong> — OAuth connection</div>
            <div id="xai-auth-container">
                ${xaiStatus.authorized
                    ? `<div style="font-size:0.7rem;color:var(--green);">✓ Authorized ${xaiStatus.has_refresh_token ? '(with refresh)' : ''}</div>
                       <button class="btn-sm" style="margin-top:6px;background:transparent;border:1px solid var(--border);color:var(--dim);" onclick="revokeXAIAuth(this)">Disconnect xAI</button>`
                    : `<div style="font-size:0.7rem;color:var(--dim);margin-bottom:6px;">Not connected. Authorize to use Grok models.</div>
                       <button class="btn-sm" onclick="startXAIAuth(this)">Connect xAI</button>
                       <div id="xai-auth-pending" style="display:none;margin-top:8px;">
                           <div style="font-size:0.7rem;color:var(--text);">Go to <a id="xai-verify-link" href="#" target="_blank" style="color:var(--accent);">x.ai</a> and enter:</div>
                           <div id="xai-user-code" style="font-family:monospace;font-size:1.1rem;margin:6px 0;padding:8px 12px;background:#0e0e16;border-radius:4px;border:1px solid var(--border);color:var(--accent);letter-spacing:2px;"></div>
                           <div style="font-size:0.65rem;color:var(--dim);">Waiting for authorization…</div>
                       </div>
                       <div id="xai-auth-error" style="display:none;margin-top:6px;font-size:0.7rem;color:#ff6b6b;"></div>`}
            </div>
        </div>`;

        el.innerHTML = banner + byok + xaiSection +
            `<div style="margin-bottom:8px;font-size:0.7rem;color:var(--dim);">Pick a specific model, or leave on "Cove default" to inherit <strong style="color:var(--text);">${ESC(defaultStr)}</strong>.</div>
            <div class="settings-row" style="align-items:center;gap:8px;">
                <span class="settings-label" style="flex:0 0 90px;">Primary</span>
                <select id="my-model-primary" class="settings-input" style="flex:1 1 0;min-width:140px;">${opts(data.primary)}</select>
            </div>
            <div class="settings-row" style="align-items:center;gap:8px;">
                <span class="settings-label" style="flex:0 0 90px;">Fallback</span>
                <select id="my-model-fallback" class="settings-input" style="flex:1 1 0;min-width:140px;">${opts(data.fallback)}</select>
            </div>`;
        ['my-model-primary', 'my-model-fallback'].forEach(id => {
            document.getElementById(id)?.addEventListener('change', _saveMyModel);
        });
    } catch (err) {
        el.innerHTML = `<div class="error-msg">${ESC(err.message)}</div>`;
    }
}

async function _saveMyModel() {
    const primary = document.getElementById('my-model-primary')?.value || '';
    const fallback = document.getElementById('my-model-fallback')?.value || '';
    try {
        await fetch('/api/settings/my-model', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ primary, fallback }),
        });
    } catch (e) {
        console.warn('my-model save error:', e);
    }
}

// Clear this presence's model override → the agent falls back to the steward-set Cove default.
async function resetToCoveDefault(btn) {
    const status = document.getElementById('reset-default-status');
    if (btn) { btn.disabled = true; }
    if (status) { status.textContent = 'Resetting…'; }
    try {
        await fetch('/api/settings/my-model', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ primary: '', fallback: '' }),
        });
        await loadSettingsMyModel();  // re-render with the override cleared
    } catch (e) {
        if (status) { status.textContent = 'Could not reset.'; }
        if (btn) { btn.disabled = false; }
    }
}

// Change the BYOK provider/key from Settings (same endpoint as the onboarding card).
async function saveSettingsModelKey() {
    const provider = (document.getElementById('byok-provider') || {}).value || '';
    const api_key = (document.getElementById('byok-key') || {}).value || '';
    const status = document.getElementById('byok-status');
    if (!provider) { alert('Choose a provider.'); return; }
    if (provider !== 'ollama' && !api_key) { alert('Enter your API key (or pick Ollama for local).'); return; }
    const body = { provider };
    if (api_key) body.api_key = api_key;   // omit to keep the existing key
    try {
        const r = await fetch('/api/settings/model-key', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const d = await r.json();
        if (!r.ok || d.error || d.ok === false) { alert(d.error || 'Could not save.'); return; }
        if (status) { status.textContent = d.verified ? 'connected ✓' : 'saved'; status.style.color = 'var(--green)'; }
        loadSettingsMyModel();
    } catch (e) { alert('Could not save: ' + e.message); }
}

// Disconnect BYOK — fall back to Cove default model.
async function disconnectBYOK(btn) {
    const status = document.getElementById('byok-status');
    if (btn) { btn.disabled = true; }
    if (status) { status.textContent = 'Disconnecting…'; status.style.color = 'var(--dim)'; }
    try {
        const r = await fetch('/api/settings/model-key', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ disconnect: true }),
        });
        const d = await r.json();
        if (!r.ok || d.error || d.ok === false) { alert(d.error || 'Could not disconnect.'); if (btn) btn.disabled = false; return; }
        if (status) { status.textContent = 'disconnected — using Cove default'; status.style.color = 'var(--green)'; }
        loadSettingsMyModel();
    } catch (e) { alert('Could not disconnect: ' + e.message); if (btn) btn.disabled = false; }
}


// ── xAI OAuth device-code flow handlers ─────────────────────────────────────

let _xaiPollInterval = null;

async function startXAIAuth(btn) {
    if (_xaiPollInterval) {
        clearInterval(_xaiPollInterval);
        _xaiPollInterval = null;
    }
    btn.disabled = true;
    const errorEl = document.getElementById('xai-auth-error');
    const pendingEl = document.getElementById('xai-auth-pending');
    if (errorEl) errorEl.style.display = 'none';
    
    try {
        const r = await fetch('/api/xai/auth/start', {method: 'POST'});
        const d = await r.json();
        if (!r.ok || d.error) {
            throw new Error(d.error || 'Failed to start OAuth flow');
        }
        
        // Show the code and link
        const codeEl = document.getElementById('xai-user-code');
        const linkEl = document.getElementById('xai-verify-link');
        if (codeEl) codeEl.textContent = d.user_code || '';
        if (linkEl) linkEl.href = d.verification_uri || '#';
        if (pendingEl) pendingEl.style.display = 'block';
        
        // Start polling
        _xaiPollInterval = setInterval(() => pollXAIAuth(), (d.interval || 5) * 1000);
        
    } catch (e) {
        if (errorEl) {
            errorEl.textContent = e.message;
            errorEl.style.display = 'block';
        }
        btn.disabled = false;
    }
}

async function pollXAIAuth() {
    try {
        const r = await fetch('/api/xai/auth/poll', {method: 'POST'});
        const d = await r.json();
        
        if (d.status === 'authorized') {
            // Success — clear polling and reload
            if (_xaiPollInterval) {
                clearInterval(_xaiPollInterval);
                _xaiPollInterval = null;
            }
            await loadSettingsMyModel();
        } else if (d.status === 'no_flow') {
            // Flow was cleared or expired
            if (_xaiPollInterval) {
                clearInterval(_xaiPollInterval);
                _xaiPollInterval = null;
            }
        }
        // pending — keep polling
    } catch (e) {
        // Network errors — keep polling unless it's a terminal error
        console.warn('xAI poll error:', e);
    }
}

async function revokeXAIAuth(btn) {
    btn.disabled = true;
    try {
        const r = await fetch('/api/xai/auth/revoke', {method: 'POST'});
        const d = await r.json();
        if (d.status === 'revoked') {
            await loadSettingsMyModel();
        }
    } catch (e) {
        console.warn('xAI revoke error:', e);
        btn.disabled = false;
    }
}

// Clean up polling on page unload
window.addEventListener('beforeunload', () => {
    if (_xaiPollInterval) {
        clearInterval(_xaiPollInterval);
        _xaiPollInterval = null;
    }
});
