// =============================================================================
// settings-admin.js — Status, Nextcloud, Model, Model Registry, Agent Symbol, Presences
// =============================================================================

async function loadSettingsStatus() {
    const el = document.getElementById('settings-status');
    if (!el) return;
    try {
        const data = await fetch('/api/status').then(r => r.json());

        // On the Stuart admin MC, the System panel reflects the STEWARD + the Cove,
        // never the logged-in operator's personal agent (which /api/status returns).
        const isAdmin = MC.adminView === true;
        const fam = MC.instance?.family_name || '';
        const lbl = (MC.hostContext?.label || 'steward');
        const stewardName = lbl.charAt(0).toUpperCase() + lbl.slice(1);
        const stewardArch = lbl.toLowerCase() === 'mercer' ? 'The Merchant' : 'The Steward';

        const rows = isAdmin ? [
            ['Agent', `${stewardName} ${fam}`.trim()],
            ['Archetype', stewardArch],
            ['Cove', fam || '—'],
            ['Dry Run', data.dry_run ? 'Yes' : 'No'],
        ] : [
            ['Agent', data.agent?.name || MC.agentName],
            ['Archetype', data.agent?.archetype || ''],
            ['Operator', data.operator || MC.operatorName],
            ['Family', MC.instance.family_name || '—'],
            ['Dry Run', data.dry_run ? 'Yes' : 'No'],
        ];

        if (data.latest_echo) {
            rows.push(['Last Echo', `#${data.latest_echo.echo_num} — ${data.latest_echo.frequency}`]);
        }

        el.innerHTML = rows.map(([label, val]) => `
            <div class="settings-row">
                <span class="settings-label">${ESC(label)}</span>
                <span class="settings-val">${ESC(String(val))}</span>
            </div>`).join('');
    } catch (err) {
        el.innerHTML = `<div class="error-msg">${ESC(err.message)}</div>`;
    }
}

async function loadSettingsNextcloud() {
    const el = document.getElementById('settings-nc');
    if (!el) return;
    try {
        const data = await fetch('/api/settings/nextcloud').then(r => r.json());

        if (data.error) {
            el.innerHTML = `<div class="error-msg">${ESC(data.error)}</div>`;
            return;
        }

        const rows = [
            ['URL', data.url || ''],
            ['User', data.username || ''],
            ['CalDAV', (data.caldav_status === 'ok' || data.caldav_status === 'configured') ? 'Connected' : data.caldav_status || 'not checked'],
            ['WebDAV', (data.webdav_status === 'ok' || data.webdav_status === 'configured') ? 'Connected' : data.webdav_status || 'not checked'],
        ];

        const pw = data.password || '';
        const pwRow = pw
            ? `<div class="settings-row" style="align-items:center;gap:6px;">
                   <span class="settings-label">Admin Password</span>
                   <input id="nc-pw-field" type="password" value="${ESC(pw)}" readonly
                       style="flex:1;min-width:0;background:var(--card2);border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:monospace;font-size:0.78rem;padding:3px 6px;">
                   <button class="btn-sm" style="font-size:0.68rem;padding:0.15rem 0.45rem;" onclick="_ncTogglePw(this)">Reveal</button>
                   <button class="btn-sm" style="font-size:0.68rem;padding:0.15rem 0.45rem;" onclick="_ncCopyPw(this)">Copy</button>
               </div>`
            : `<div class="settings-row"><span class="settings-label">Admin Password</span><span class="settings-val">Not configured</span></div>`;

        el.innerHTML = rows.map(([label, val]) => `
            <div class="settings-row">
                <span class="settings-label">${ESC(label)}</span>
                <span class="settings-val">${ESC(String(val))}</span>
            </div>`).join('') + pwRow +
            `<div style="margin-top:8px;display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
                <button class="btn-sm" onclick="testNextcloud()">Test Connection</button>
                ${MC.config?.nextcloud_public_url ? `<a href="${MC.config.nextcloud_public_url}" target="_blank" class="btn-sm" style="text-decoration:none;">Open Cloud</a>` : ''}
                <span id="nc-test-result" style="font-size:0.75rem;color:var(--dim);"></span>
            </div>`;
    } catch (err) {
        el.innerHTML = `<div class="error-msg">${ESC(err.message)}</div>`;
    }
}

// Connect (Matrix) master block — same pattern as the Nextcloud one (jules 1653):
// homeserver identity + the master registration secret, reveal/copy gated.
async function loadSettingsMatrixAdmin() {
    const el = document.getElementById('settings-matrix-admin');
    if (!el) return;
    try {
        const data = await fetch('/api/settings/matrix').then(r => r.json());
        if (data.error) { el.innerHTML = `<div class="error-msg">${ESC(data.error)}</div>`; return; }
        if (!data.enabled) {
            el.innerHTML = '<div class="settings-row"><span class="settings-val" style="color:var(--dim);">Connect is not enabled on this Cove.</span></div>';
            return;
        }
        const rows = [
            ['Server name', data.server_name || ''],
            ['Internal URL', data.internal_url || ''],
            ['Public URL', data.public_url || '(set when an address is claimed)'],
        ];
        const secret = data.reg_secret || '';
        const secretRow = secret
            ? `<div class="settings-row" style="align-items:center;gap:6px;">
                   <span class="settings-label">Reg. secret</span>
                   <input id="mx-secret-field" type="password" value="${ESC(secret)}" readonly
                       style="flex:1;min-width:0;background:var(--card2);border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:monospace;font-size:0.78rem;padding:3px 6px;">
                   <button class="btn-sm" style="font-size:0.68rem;padding:0.15rem 0.45rem;" onclick="_mxToggleSecret(this)">Reveal</button>
                   <button class="btn-sm" style="font-size:0.68rem;padding:0.15rem 0.45rem;" onclick="_mxCopySecret(this)">Copy</button>
               </div>
               <div style="font-size:0.62rem;color:var(--dim);margin-top:2px;">The master registration secret — mints accounts on this homeserver. Treat it like the NC admin password.</div>`
            : `<div class="settings-row"><span class="settings-label">Reg. secret</span><span class="settings-val">Not configured</span></div>`;
        el.innerHTML = rows.map(([label, val]) => `
            <div class="settings-row">
                <span class="settings-label">${ESC(label)}</span>
                <span class="settings-val">${ESC(String(val))}</span>
            </div>`).join('') + secretRow;
    } catch (err) {
        el.innerHTML = `<div class="error-msg">${ESC(err.message)}</div>`;
    }
}

function _mxToggleSecret(btn) {
    const f = document.getElementById('mx-secret-field');
    if (!f) return;
    const reveal = f.type === 'password';
    f.type = reveal ? 'text' : 'password';
    btn.textContent = reveal ? 'Hide' : 'Reveal';
}

async function _mxCopySecret(btn) {
    const f = document.getElementById('mx-secret-field');
    if (!f) return;
    try {
        await navigator.clipboard.writeText(f.value);
    } catch (e) {
        f.type = 'text'; f.select(); try { document.execCommand('copy'); } catch (_) {}
    }
    btn.textContent = 'Copied';
    setTimeout(() => { btn.textContent = 'Copy'; }, 1500);
}

function _ncTogglePw(btn) {
    const f = document.getElementById('nc-pw-field');
    if (!f) return;
    const reveal = f.type === 'password';
    f.type = reveal ? 'text' : 'password';
    btn.textContent = reveal ? 'Hide' : 'Reveal';
}

async function _ncCopyPw(btn) {
    const f = document.getElementById('nc-pw-field');
    if (!f) return;
    try {
        await navigator.clipboard.writeText(f.value);
    } catch (e) {
        f.type = 'text'; f.select(); try { document.execCommand('copy'); } catch (_) {}
    }
    btn.textContent = 'Copied';
    setTimeout(() => { btn.textContent = 'Copy'; }, 1500);
}

async function testNextcloud() {
    const result = document.getElementById('nc-test-result');
    result.textContent = 'Testing...';
    result.style.color = 'var(--dim)';
    try {
        const data = await fetch('/api/settings/nextcloud/test', { method: 'POST' }).then(r => r.json());
        if (data.ok) {
            result.textContent = 'Connected';
            result.style.color = 'var(--green, #4caf50)';
        } else {
            result.textContent = 'Failed: ' + (data.error || 'Unknown');
            result.style.color = 'var(--red, #f44336)';
        }
    } catch (err) {
        result.textContent = 'Error: ' + err.message;
        result.style.color = 'var(--red, #f44336)';
    }
}

// Team model manager — the single home. DB-backed (/api/agents/model-assignments),
// saves instantly with no restart and works under a read-only config mount. Two axes per
// agent: WORKING (chat/build) and TUNING (LTP). The agent-detail page shows the same data.
async function loadSettingsModel() {
    const el = document.getElementById('settings-model');
    if (!el) return;
    try {
        const data = await fetch('/api/agents/model-assignments').then(r => r.json());
        if (data.error) {
            el.innerHTML = `<div class="error-msg">${ESC(data.error)}</div>`;
            return;
        }
        const catalog = data.catalog || [];
        const agentsObj = data.agents || {};
        const agentIds = Object.keys(agentsObj);
        const brain = data.cove_brain || {};

        const opts = (sel, noneLabel) => [`<option value="">${ESC(noneLabel || '— none —')}</option>`].concat(
            catalog.map(m => `<option value="${ESC(m.id)}"${m.id === sel ? ' selected' : ''}>${ESC(m.name)}${m.type ? ' · ' + ESC(m.type) : ''}</option>`)
        ).join('');

        // CSS grid keeps the headers aligned over the dropdowns; the media query stacks each
        // agent's four dropdowns (with their own labels) on a phone, and the toggle collapses
        // the whole table since it's long and rarely needed on mobile.
        const style = `<style>
          .tm-head,.tm-row{display:grid;grid-template-columns:84px 1fr 1fr 1fr 1fr 30px;gap:6px;align-items:center;}
          .tm-head{font-size:0.55rem;text-transform:uppercase;letter-spacing:0.03em;opacity:0.55;margin:6px 0 4px;}
          .tm-row{margin-bottom:6px;}
          .tm-cell{display:flex;flex-direction:column;min-width:0;}
          .tm-cell>select{width:100%;min-width:0;}
          .tm-lbl{display:none;font-size:0.55rem;color:var(--dim);margin-bottom:2px;text-transform:uppercase;letter-spacing:0.03em;}
          .tm-agent{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
          .tm-status{font-size:0.6rem;color:var(--dim);}
          .tm-toggle{cursor:pointer;user-select:none;display:flex;align-items:flex-start;gap:6px;margin-bottom:4px;}
          @media(max-width:680px){
            .tm-head{display:none;}
            .tm-row{grid-template-columns:1fr;gap:4px;padding:8px 0;border-bottom:1px solid var(--border);}
            .tm-agent{font-weight:600;}
            .tm-lbl{display:block;}
          }
        </style>`;
        const headRow = `<div class="tm-head"><span>Agent</span><span>Work · primary</span><span>Work · fallback</span><span>Tune · primary</span><span>Tune · fallback</span><span></span></div>`;
        const cell = (aid, a, kind, none, label) =>
            `<label class="tm-cell"><span class="tm-lbl">${label}</span><select class="settings-input team-model-sel" data-agent="${ESC(aid)}" data-kind="${kind}">${opts(a[kind], none)}</select></label>`;
        const rows = agentIds.map(aid => {
            const a = agentsObj[aid];
            // When an axis isn't explicitly assigned, show what it ACTUALLY runs on (the
            // Cove brain) right in the none-option, so "(Cove default)" never hides the model.
            const wEff = a.working_primary ? '' : (a.working_effective ? ` · ${a.working_effective}` : '');
            const tEff = a.tuning_primary ? '' : (a.tuning_effective ? ` · ${a.tuning_effective}` : '');
            return `<div class="tm-row">
                <span class="tm-agent">${ESC(a.name)}</span>
                ${cell(aid, a, 'working_primary', `(Cove default${wEff})`, 'Work · primary')}
                ${cell(aid, a, 'working_fallback', '(none)', 'Work · fallback')}
                ${cell(aid, a, 'tuning_primary', `(use working${tEff})`, 'Tune · primary')}
                ${cell(aid, a, 'tuning_fallback', '(none)', 'Tune · fallback')}
                <span class="tm-status" data-agent="${ESC(aid)}"></span>
            </div>`;
        }).join('');
        const brainLine = brain.model
            ? `<div style="font-size:0.7rem;color:var(--dim);margin-bottom:6px;">Cove brain (the default an agent runs on when not overridden): <strong>${ESC(brain.model)}</strong>${brain.provider ? ' · ' + ESC(brain.provider) : ''}</div>`
            : '';
        el.innerHTML = style +
            `<div class="tm-toggle" onclick="var t=this.nextElementSibling;var h=t.style.display==='none';t.style.display=h?'':'none';this.querySelector('.tm-caret').textContent=h?'▾':'▸';">
                <span class="tm-caret">▾</span>
                <span style="font-size:0.7rem;color:var(--dim);">Build-team models — <strong>working</strong> (chat/build) + <strong>tuning</strong> per agent. Saved instantly, no restart. Presences override their own personal agent in their settings.</span>
            </div>
            ${brainLine}<div>${headRow}${rows}</div>`;

        el.querySelectorAll('.team-model-sel').forEach(sel => {
            sel.addEventListener('change', () => _saveTeamModel(sel.dataset.agent, el));
        });
    } catch (err) {
        el.innerHTML = `<div class="error-msg">${ESC(err.message)}</div>`;
    }
}

async function _saveTeamModel(agentId, el) {
    const v = (kind) => el.querySelector(`.team-model-sel[data-agent="${agentId}"][data-kind="${kind}"]`)?.value || '';
    const statusEl = el.querySelector(`.tm-status[data-agent="${agentId}"]`);
    if (statusEl) { statusEl.textContent = '…'; statusEl.style.color = 'var(--dim)'; }
    try {
        const res = await fetch(`/api/agents/${encodeURIComponent(agentId)}/model-assignment`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                working_primary: v('working_primary'),
                working_fallback: v('working_fallback'),
                tuning_primary: v('tuning_primary'),
                tuning_fallback: v('tuning_fallback'),
            }),
        });
        const d = await res.json();
        if (statusEl) {
            statusEl.textContent = d.ok ? 'saved' : (d.error || 'error');
            statusEl.style.color = d.ok ? 'var(--green)' : 'var(--red)';
            if (d.ok) setTimeout(() => { statusEl.textContent = ''; }, 2000);
        }
    } catch (e) {
        if (statusEl) { statusEl.textContent = 'error'; statusEl.style.color = 'var(--red)'; }
    }
}

// =============================================================================
// Model Registry — editable textarea for models.yaml
// =============================================================================

async function loadSettingsModelRegistry() {
    const el = document.getElementById('settings-model-registry');
    if (!el) return;
    try {
        const res = await fetch('/api/models');
        const data = await res.json();
        const models = data.models || [];

        // Convert models to YAML-like text for the textarea
        const yamlText = modelsToYaml(models);

        el.innerHTML = `
            <div style="margin-bottom:8px;font-size:0.7rem;color:var(--dim);line-height:1.5;">
                Available models for agent assignment. Edits apply immediately on save.
                A Cove restart is only needed when adding a brand-new provider whose API key isn't in the environment yet.
            </div>
            <textarea id="model-registry-editor"
                style="width:100%;min-height:280px;max-height:500px;font-family:monospace;font-size:0.72rem;
                       background:var(--card2);color:var(--fg);border:1px solid var(--border);
                       border-radius:6px;padding:10px;resize:vertical;line-height:1.5;
                       tab-size:2;"
                spellcheck="false">${ESC(yamlText)}</textarea>
            <div style="margin-top:8px;display:flex;gap:8px;align-items:center;">
                <button class="btn-sm" onclick="saveModelRegistry()">Save Registry</button>
                <span id="model-registry-result" style="font-size:0.75rem;color:var(--dim);"></span>
            </div>
        `;
    } catch (err) {
        el.innerHTML = `<div class="error-msg">${ESC(err.message)}</div>`;
    }
}

function modelsToYaml(models) {
    // Simple YAML serialization for the textarea display
    if (!models.length) return 'models: []';
    let lines = ['models:'];
    for (const m of models) {
        lines.push('');
        lines.push(`  - id: ${m.id}`);
        if (m.name) lines.push(`    name: "${m.name}"`);
        lines.push(`    provider: ${m.provider}`);
        if (m.model_string) lines.push(`    model_string: "${m.model_string}"`);
        lines.push(`    type: ${m.type || 'local'}`);
        if (m.context_window) lines.push(`    context_window: ${m.context_window}`);
        if (m.notes) lines.push(`    notes: "${m.notes}"`);
    }
    return lines.join('\n');
}

async function saveModelRegistry() {
    const result = document.getElementById('model-registry-result');
    const textarea = document.getElementById('model-registry-editor');
    if (!textarea || !result) return;

    result.textContent = 'Saving...';
    result.style.color = 'var(--dim)';

    try {
        const res = await fetch('/api/models', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ yaml_content: textarea.value }),
        });
        const data = await res.json();
        if (data.ok) {
            // Apply immediately — clear server caches, then refresh the dropdowns
            // so any newly-added models are selectable without a restart.
            try { await fetch('/api/settings/reload', { method: 'POST' }); } catch (e) {}
            result.textContent = `Saved (${data.count} models) — applied.`;
            result.style.color = 'var(--green, #4caf50)';
            if (typeof loadSettingsModel === 'function') loadSettingsModel();
        } else {
            result.textContent = 'Error: ' + (data.error || 'Unknown');
            result.style.color = 'var(--red, #f44336)';
        }
    } catch (err) {
        result.textContent = 'Error: ' + err.message;
        result.style.color = 'var(--red, #f44336)';
    }
}


// =============================================================================
// Agent Symbol — editable SVG for the header
// =============================================================================

async function loadSettingsAgentSymbol() {
    const el = document.getElementById('settings-agent-symbol');
    if (!el) return;

    const agentId = MC.agents[0]?.id || '';
    const currentSvg = MC.agents[0]?.symbol_svg || '';

    el.innerHTML = `
        <div style="margin-bottom:8px;font-size:0.7rem;color:var(--dim);line-height:1.5;">
            Custom SVG symbol shown in the header next to the Cove logo.
            Leave empty to use the default emoji. Paste raw SVG code.
        </div>
        <div style="display:flex;align-items:center;gap:12px;margin-bottom:8px;">
            <span style="font-size:0.7rem;color:var(--dim);">Preview:</span>
            <div id="symbol-preview" style="width:28px;height:28px;display:flex;align-items:center;justify-content:center;">
                ${currentSvg || MC.agentEmoji}
            </div>
        </div>
        <textarea id="symbol-svg-editor"
            style="width:100%;min-height:100px;max-height:300px;font-family:monospace;font-size:0.72rem;
                   background:var(--card2);color:var(--text);border:1px solid var(--border);
                   border-radius:6px;padding:10px;resize:vertical;line-height:1.5;"
            spellcheck="false"
            placeholder="<svg viewBox=&quot;0 0 100 100&quot;>...</svg>"
            oninput="previewAgentSymbol()">${ESC(currentSvg)}</textarea>
        <div style="margin-top:8px;display:flex;gap:8px;align-items:center;">
            <button class="btn-sm" onclick="saveAgentSymbol()">Save Symbol</button>
            <button class="btn-sm" onclick="clearAgentSymbol()" style="opacity:0.6;">Clear</button>
            <span id="symbol-save-result" style="font-size:0.75rem;color:var(--dim);"></span>
        </div>
    `;
}

function previewAgentSymbol() {
    const textarea = document.getElementById('symbol-svg-editor');
    const preview = document.getElementById('symbol-preview');
    if (!textarea || !preview) return;
    const val = textarea.value.trim();
    if (val && val.startsWith('<svg')) {
        preview.innerHTML = val;
        const svg = preview.querySelector('svg');
        if (svg) { svg.style.width = '28px'; svg.style.height = '28px'; }
    } else if (!val) {
        preview.textContent = MC.agentEmoji;
    }
}

async function saveAgentSymbol() {
    const result = document.getElementById('symbol-save-result');
    const textarea = document.getElementById('symbol-svg-editor');
    if (!textarea || !result) return;

    const agentId = MC.agents[0]?.id || '';
    if (!agentId) { result.textContent = 'No agent ID'; return; }

    result.textContent = 'Saving...';
    result.style.color = 'var(--dim)';

    try {
        const res = await fetch(`/api/agents/${agentId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ symbol_svg: textarea.value.trim() }),
        });
        const data = await res.json();
        if (data.ok) {
            result.textContent = 'Saved. Refresh to see in header.';
            result.style.color = 'var(--green)';
            if (MC.agents[0]) MC.agents[0].symbol_svg = textarea.value.trim();
            // Live-update header
            const symbolEl = document.getElementById('header-symbol');
            if (symbolEl) {
                const val = textarea.value.trim();
                if (val) { symbolEl.innerHTML = val; }
                else { symbolEl.textContent = MC.agentEmoji; }
            }
        } else {
            result.textContent = 'Error: ' + (data.error || 'Unknown');
            result.style.color = 'var(--red)';
        }
    } catch (err) {
        result.textContent = 'Error: ' + err.message;
        result.style.color = 'var(--red)';
    }
}

async function clearAgentSymbol() {
    const textarea = document.getElementById('symbol-svg-editor');
    if (textarea) textarea.value = '';
    previewAgentSymbol();
    await saveAgentSymbol();
}


// =============================================================================
// Presences — Operator-only admin section
// =============================================================================

// Compute backends (Admin Presence) — where heavy work runs. Three pluggable
// offramps per section; `external` URL = borrow another box's GPU (e.g. the P620).
async function loadSettingsCompute() {
    const el = document.getElementById('settings-compute');
    if (!el) return;
    try {
        const data = await fetch('/api/settings/compute').then(r => r.json());
        if (data.error) { el.innerHTML = `<div class="error-msg">${ESC(data.error)}</div>`; return; }
        const labels = { llm: 'LLM', voice: 'Voice (jules)', video_asr: 'Video transcription' };
        const hint = {
            llm: 'cloud = BYOK API · local = host Ollama · external = a GPU box URL',
            voice: 'local = CPU whisper in-Cove · external = a voice/GPU URL · off = disabled',
            video_asr: 'cloud = BYOK ASR API · local = local GPU · external = a GPU box URL',
        };
        const modes = data.modes || {};
        const cfg = data.compute || {};
        el.innerHTML = Object.keys(labels).map(sec => {
            const cur = cfg[sec] || { mode: '', url: '' };
            const opts = (modes[sec] || []).map(m =>
                `<option value="${m}"${m === cur.mode ? ' selected' : ''}>${m}</option>`).join('');
            // A RENTED external GPU authenticates with a grant token — without it the
            // external mode can't fully configure here (had to use the Rent-GPU tool).
            // Token input shows only for mode=external; has_token renders a "grant set"
            // state (the secret itself is never echoed back).
            const tokOn = cur.mode === 'external';
            const tokPh = cur.has_token ? 'grant set — paste to replace' : 'grant code (from the GPU owner)';
            return `
            <div class="settings-row" style="flex-wrap:wrap;gap:6px;align-items:center;">
                <span class="settings-label" style="flex:0 0 140px;">${ESC(labels[sec])}</span>
                <select id="cmp-mode-${sec}" class="settings-input" style="max-width:120px;"
                        onchange="document.getElementById('cmp-token-${sec}').style.display=(this.value==='external')?'':'none'">${opts}</select>
                <input id="cmp-url-${sec}" class="settings-input" placeholder="external URL (optional)"
                       value="${ESC(cur.url || '')}" style="flex:1;min-width:160px;">
                <input id="cmp-token-${sec}" class="settings-input" type="password" placeholder="${tokPh}"
                       autocomplete="off" style="flex:1;min-width:140px;${tokOn ? '' : 'display:none'}">
                <button class="btn-sm" onclick="saveCompute('${sec}')">Save</button>
                <div style="flex:0 0 100%;font-size:0.6rem;color:var(--dim);">${ESC(hint[sec])}${cur.has_token ? ' · grant set ✓ (leave the grant field blank to keep it)' : ''}</div>
                <div id="cmp-ready-${sec}" style="flex:0 0 100%;font-size:0.62rem;"></div>
            </div>`;
        }).join('');
        loadComputeReadiness();
    } catch (err) {
        el.innerHTML = `<div class="error-msg">${ESC(err.message)}</div>`;
    }
}

// CF-96: one-line readiness echo per section, read from the ONE compute resolver
// (/api/compute/status) — "external — ✓ endpoint + token set" / "external — ⚠ no token".
async function loadComputeReadiness() {
    try {
        const s = await fetch('/api/compute/status').then(r => r.json());
        ['llm', 'voice', 'video_asr'].forEach(sec => {
            const box = document.getElementById(`cmp-ready-${sec}`);
            if (!box || !s[sec]) return;
            const st = s[sec];
            const ok = !!st.ready;
            box.textContent = (ok ? '✓ ' : '⚠ ') + (st.mode || '') + ' — ' + (st.why || (ok ? 'ready' : 'not ready'));
            box.style.color = ok ? 'var(--ok, #7dc98f)' : 'var(--warn, #e0b96a)';
        });
    } catch (e) { /* readiness echo is best-effort */ }
}

async function saveCompute(section) {
    const mode = document.getElementById(`cmp-mode-${section}`)?.value;
    const url = document.getElementById(`cmp-url-${section}`)?.value || '';
    // Include the grant token only when the operator typed one — blank means "keep
    // the saved grant" (the PUT treats a missing token as untouched).
    const token = document.getElementById(`cmp-token-${section}`)?.value || '';
    const body = { mode, url };
    if (token) body.token = token;
    try {
        const r = await fetch(`/api/settings/compute/${section}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const d = await r.json();
        if (!r.ok || d.error) { alert(d.error || 'Save failed'); return; }
        try { await fetch('/api/settings/reload', { method: 'POST' }); } catch (e) {}
        loadSettingsCompute();
    } catch (e) { alert('Save failed: ' + e.message); }
}

// =============================================================================

async function loadSettingsPresences() {
    const el = document.getElementById('settings-presences');
    if (!el) return;

    el.innerHTML = '<span style="color:var(--dim);font-size:0.82rem;">Loading Presences...</span>';

    try {
        const data = await fetch('/api/presence/list').then(r => r.json());
        const presences = data.presences || [];

        const roleColors = {
            admin: 'var(--orange, #e67e22)',
            member:   'var(--green, #2ecc71)',
            guest:    'var(--dim, #888)',
        };

        const listHtml = presences.length ? presences.map(p => {
            const lastSeen = p.last_access
                ? new Date(p.last_access).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
                : 'never';
            const roleColor = roleColors[p.cove_role] || 'var(--dim)';
            return `
                <div class="settings-row" style="align-items:center;gap:0.5rem;padding:0.4rem 0;">
                    <span style="width:8px;height:8px;border-radius:50%;background:${p.active !== false ? 'var(--green,#2ecc71)' : 'var(--dim,#888)'};flex-shrink:0;"></span>
                    <span style="font-weight:600;color:var(--text);flex:1;">${ESC(p.display_name)}</span>
                    <span style="font-size:0.65rem;text-transform:uppercase;letter-spacing:0.03em;color:${roleColor};padding:0.1rem 0.35rem;background:var(--card2);border-radius:3px;">${ESC(p.cove_role)}</span>
                    <span style="font-size:0.7rem;color:var(--dim);">${ESC(p.full_name)}</span>
                    <span style="font-size:0.68rem;color:var(--dim);margin-left:auto;">${lastSeen}</span>
                    <button class="btn-sm" style="font-size:0.68rem;padding:0.15rem 0.4rem;" onclick="_presenceRegenLink('${ESC(p.id)}','${ESC(p.display_name)}')">Link</button>
                </div>`;
        }).join('') : '<span style="color:var(--dim);font-size:0.82rem;font-style:italic;">No Presences yet</span>';

        el.innerHTML = `
            ${listHtml}
            <div style="margin-top:0.75rem;padding-top:0.75rem;border-top:1px solid var(--border,#2a2a3a);">
                <div style="font-size:0.75rem;font-weight:600;color:var(--text);margin-bottom:0.25rem;">Invite New Presence</div>
                <div style="font-size:0.68rem;color:var(--dim);margin-bottom:0.5rem;">Creates their account + personal agent on this Cove and mints a sign-in link you hand them — no email required.</div>
                <div style="display:flex;flex-direction:column;gap:0.4rem;">
                    <div style="display:flex;gap:0.4rem;flex-wrap:wrap;">
                        <input id="np-name" type="text" placeholder="Display name" class="settings-input" style="flex:1;min-width:120px;">
                        <input id="np-email" type="email" placeholder="Email (optional)" class="settings-input" style="flex:1;min-width:140px;">
                    </div>
                    <div style="display:flex;gap:0.4rem;flex-wrap:wrap;align-items:center;">
                        <input id="np-agent" type="text" placeholder="Agent name" class="settings-input" style="flex:1;min-width:100px;">
                        <select id="np-role" class="settings-input" style="max-width:200px;" title="Member = family/household; Admin = can also manage the Cove">
                            <option value="member">Member (family)</option>
                            <option value="admin">Admin (manages the Cove)</option>
                        </select>
                        <span style="font-size:0.68rem;color:var(--dim);display:flex;align-items:center;">Invite by copy-link</span>
                    </div>
                    <div style="display:flex;gap:0.4rem;align-items:center;margin-top:0.25rem;">
                        <button class="btn-sm" onclick="_presenceCreate()">Create Presence</button>
                        <span id="np-result" style="font-size:0.72rem;color:var(--dim);"></span>
                    </div>
                </div>
            </div>
            <div id="np-link-display" style="display:none;margin-top:0.5rem;padding:0.5rem;background:var(--card2);border-radius:5px;font-size:0.75rem;">
            </div>`;
    } catch (err) {
        el.innerHTML = `<div class="error-msg">${ESC(err.message)}</div>`;
    }
}

async function _presenceCreate() {
    const result = document.getElementById('np-result');
    const name = document.getElementById('np-name')?.value.trim();
    const email = document.getElementById('np-email')?.value.trim();
    const agent = document.getElementById('np-agent')?.value.trim();
    const role = document.getElementById('np-role')?.value;

    if (!name || !agent) {
        if (result) { result.textContent = 'Name and agent name required'; result.style.color = 'var(--red)'; }
        return;
    }

    if (result) { result.textContent = 'Creating...'; result.style.color = 'var(--dim)'; }

    try {
        const res = await fetch('/api/presence/create', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                display_name: name,
                email: email || undefined,
                agent_name: agent,
                cove_role: role,
                send_email: false,  // #154 — in-Cove invites are copy-link only
            }),
        });
        const data = await res.json();
        if (data.presence_id) {
            if (result) {
                result.textContent = 'Created — copy the link below';
                result.style.color = 'var(--green)';
            }
            // Show the magic link
            const linkEl = document.getElementById('np-link-display');
            if (linkEl) {
                linkEl.style.display = 'block';
                linkEl.innerHTML = `
                    <div style="color:var(--text);margin-bottom:0.3rem;"><strong>${ESC(data.display_name)}</strong> — ${ESC(data.full_name)}</div>
                    <div style="display:flex;align-items:center;gap:0.4rem;">
                        <span style="color:var(--dim);">Sign-in link:</span>
                        <input type="text" value="${ESC(data.signin_link)}" readonly
                            style="flex:1;font-size:0.68rem;padding:0.2rem 0.4rem;background:var(--card);border:1px solid var(--border);border-radius:3px;color:var(--text);"
                            onclick="this.select()">
                        <button class="btn-sm" style="font-size:0.65rem;" onclick="navigator.clipboard.writeText('${ESC(data.signin_link)}');this.textContent='Copied';setTimeout(()=>this.textContent='Copy',1500)">Copy</button>
                    </div>
                    ${data.nc?.ok ? '<div style="color:var(--green);font-size:0.68rem;margin-top:0.2rem;">Cloud storage provisioned</div>' : ''}
                    ${data.nc?.ok === false ? `<div style="color:var(--orange);font-size:0.68rem;margin-top:0.2rem;">Cloud: ${ESC(data.nc.error || 'not provisioned')}</div>` : ''}`;
            }
            // Clear form
            document.getElementById('np-name').value = '';
            document.getElementById('np-email').value = '';
            document.getElementById('np-agent').value = '';
            // Refresh the list
            loadSettingsPresences();
        } else {
            if (result) { result.textContent = data.detail || 'Error'; result.style.color = 'var(--red)'; }
        }
    } catch (err) {
        if (result) { result.textContent = err.message; result.style.color = 'var(--red)'; }
    }
}

async function _presenceRegenLink(presenceId, name) {
    const linkEl = document.getElementById('np-link-display');
    if (!linkEl) return;

    try {
        const res = await fetch(`/api/presence/${presenceId}/regenerate-link`, { method: 'POST' });
        const data = await res.json();
        if (data.signin_link) {
            linkEl.style.display = 'block';
            linkEl.innerHTML = `
                <div style="color:var(--text);margin-bottom:0.3rem;"><strong>${ESC(name)}</strong> — new sign-in link</div>
                <div style="display:flex;align-items:center;gap:0.4rem;">
                    <input type="text" value="${ESC(data.signin_link)}" readonly
                        style="flex:1;font-size:0.68rem;padding:0.2rem 0.4rem;background:var(--card);border:1px solid var(--border);border-radius:3px;color:var(--text);"
                        onclick="this.select()">
                    <button class="btn-sm" style="font-size:0.65rem;" onclick="navigator.clipboard.writeText('${ESC(data.signin_link)}');this.textContent='Copied';setTimeout(()=>this.textContent='Copy',1500)">Copy</button>
                </div>`;
        }
    } catch (err) {
        if (linkEl) {
            linkEl.style.display = 'block';
            linkEl.innerHTML = `<span style="color:var(--red);">Error: ${ESC(err.message)}</span>`;
        }
    }
}
