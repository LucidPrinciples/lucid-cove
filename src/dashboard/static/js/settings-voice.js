// =============================================================================
// settings-voice.js — Voice selection from pipecat-voice
// =============================================================================

// Cache for available voices from the voice server
let _voicesCache = null;

async function _fetchAvailableVoices() {
    if (_voicesCache) return _voicesCache;
    // Same-origin proxy first (works whenever the Cove page loads); direct voice host fallback.
    const urls = ['/api/voice/tts/voices'];
    const voiceBase = (typeof MC !== 'undefined' && MC.voiceUrl) ? MC.voiceUrl('http') : '';
    if (voiceBase) urls.push(`${voiceBase.replace(/\/+$/, '')}/api/tts/voices`);
    for (const url of urls) {
        try {
            const res = await fetch(url, { credentials: 'same-origin' });
            if (!res.ok) continue;
            const data = await res.json();
            _voicesCache = data;
            return _voicesCache;
        } catch (e) {
            console.warn('[settings] voices fetch failed for', url, e.message);
        }
    }
    _voicesCache = { available_files: [], config: {} };
    return _voicesCache;
}

function _voiceLabel(voiceId) {
    // Parse voice ID like "en_US-ryan-medium" into a readable label
    // Format: lang_REGION-name-quality
    const parts = voiceId.split('-');
    if (parts.length < 2) return voiceId;
    const langPart = parts[0]; // e.g. "en_US"
    const name = parts[1]; // e.g. "ryan"
    const quality = parts.slice(2).join('-'); // e.g. "medium"
    const displayName = name.charAt(0).toUpperCase() + name.slice(1);
    const region = langPart.includes('_') ? langPart.split('_')[1] : '';
    const regionLabel = region === 'US' ? 'American' : region === 'GB' ? 'British' : region;
    return `${displayName}${regionLabel ? ' (' + regionLabel + ')' : ''}${quality ? ' [' + quality + ']' : ''}`;
}

async function loadSettingsVoice() {
    const el = document.getElementById('settings-voice');
    if (!el) return;

    const voiceData = await _fetchAvailableVoices();
    // Check both available_files (filesystem scan) and config.available (config-defined)
    const availableVoices = voiceData.available_files || [];
    const configVoices = voiceData.config?.available || [];

    if (availableVoices.length === 0 && configVoices.length === 0) {
        el.innerHTML = `<div style="font-size:0.72rem;color:var(--dim);padding:4px 0;">Voice server not available.</div>`;
        return;
    }

    // Determine which agents to show dropdowns for.
    // Woods / Jules 1316: scope by role (was inverted — member saw full roster, admin only 3).
    //   - Member: personal agent only.
    //   - Admin: personal + steward/merchant + build-team roster (full voice map).
    const isAdminDoor = !!(MC.adminView || MC.coveAdminView);
    const isCoveAdmin = !!(MC.presence && MC.presence.cove_role === 'admin')
        || !!MC.config?.is_cove_admin
        || isAdminDoor;
    const chatAgents = MC.config?.chat_agents || [];
    const baseAgents = MC.agents || [];
    const agentList = [];
    const seen = new Set();
    const pushAgent = (a) => {
        if (!a || !a.id) return;
        const key = String(a.id).toLowerCase().replace(/-cove$/, '').replace(/_cove$/, '').trim();
        if (!key || seen.has(key)) return;
        seen.add(key);
        agentList.push({ id: a.id, name: a.name || a.id, emoji: a.emoji || '' });
    };

    if (isCoveAdmin) {
        // Personal / host first
        (baseAgents.length ? baseAgents : chatAgents).forEach(pushAgent);
        chatAgents.forEach(pushAgent);
        // Build-team roster so admin can set every team voice (Jules 1316).
        try {
            const rr = await fetch('/api/team/roster', { credentials: 'same-origin' });
            if (rr.ok) {
                const data = await rr.json();
                const team = data.agents || data.team || {};
                if (Array.isArray(team)) {
                    team.forEach(pushAgent);
                } else if (team && typeof team === 'object') {
                    Object.keys(team).forEach((k) => {
                        const a = team[k];
                        if (a && typeof a === 'object') {
                            pushAgent({ id: a.id || k, name: a.name || a.display_name || k, emoji: a.emoji || '' });
                        }
                    });
                }
            }
        } catch (e) { /* roster best-effort */ }
    } else {
        // Member: personal agent only — never the full build-team dump.
        if (baseAgents[0]) pushAgent(baseAgents[0]);
        else if (chatAgents[0]) pushAgent(chatAgents[0]);
    }

    // If no agents loaded (shouldn't happen), fall back to hostname-based
    if (agentList.length === 0) {
        const hostname = location.hostname.split('.')[0] || 'agent';
        agentList.push({ id: hostname, name: hostname, emoji: '' });
    }

    // Build the voice options HTML once
    // Prefer available_files (full filesystem scan), fall back to config.available
    const voiceList = availableVoices.length > 0
        ? availableVoices.map(v => ({ id: v, label: _voiceLabel(v) }))
        : configVoices.map(v => ({ id: v.id, label: v.label || _voiceLabel(v.id) }));
    const optionsHtml = voiceList.map(v =>
        `<option value="${ESC(v.id)}">${ESC(v.label)}</option>`
    ).join('');

    // Server config mapping (from /voices/config.json on the voice server)
    const serverAgentMap = voiceData.config?.agents || {};
    const serverDefault = voiceData.config?.default || '';

    let html = `<div style="font-size:0.72rem;color:var(--dim);margin-bottom:8px;">
        Choose a voice for each agent. Changes take effect on the next TTS request.
    </div>`;

    for (const agent of agentList) {
        // Normalize agent ID: strip -cove/_cove for consistent key across save/load/TTS
        const agentKey = agent.id.toLowerCase().replace(/-cove$/, '').replace(/_cove$/, '').trim();
        const featureKey = `voice_${agentKey}`;
        // Priority: user's feature override > server config > server default
        const currentVoice = MC.features?.[featureKey] || serverAgentMap[agentKey] || serverDefault;

        html += `
        <div class="settings-edit-row" style="display:flex;align-items:center;justify-content:space-between;padding:6px 0;">
            <div style="display:flex;align-items:center;gap:6px;flex:1;min-width:0;">
                ${agent.emoji ? `<span style="font-size:1rem;">${agent.emoji}</span>` : ''}
                <span style="font-size:0.82rem;">${ESC(agent.name)}</span>
            </div>
            <select id="voice-select-${ESC(agentKey)}" onchange="_saveVoiceSelection('${ESC(agentKey)}', this.value)"
                    style="background:var(--card);border:1px solid var(--border);color:var(--text);padding:6px 10px;border-radius:6px;font-family:inherit;font-size:0.78rem;max-width:200px;">
                <option value="">Default</option>
                ${optionsHtml}
            </select>
            <button onclick="_testVoice('${ESC(agentKey)}')" title="Test voice"
                    style="background:none;border:1px solid var(--border);color:var(--accent);border-radius:6px;padding:4px 8px;cursor:pointer;font-size:0.82rem;margin-left:4px;">
                ▶
            </button>
        </div>`;

        // Set selected value after render via setTimeout
        if (currentVoice) {
            const _key = agentKey;
            setTimeout(() => {
                const sel = document.getElementById(`voice-select-${_key}`);
                if (sel) sel.value = currentVoice;
            }, 0);
        }
    }

    // Status indicator
    html += `<span id="voice-save-result" style="font-size:0.7rem;color:var(--dim);display:block;margin-top:4px;"></span>`;

    el.innerHTML = html;
}

let _testAudioCtx = null;
let _testAudioSource = null;

async function _testVoice(agentId) {
    const sel = document.getElementById(`voice-select-${agentId}`);
    const voiceName = sel?.value || '';
    // Prefer same-origin proxy so Settings test works on mesh/laptop without voice.{domain}.
    const voiceBase = (typeof MC !== 'undefined' && MC.voiceUrl) ? MC.voiceUrl('http') : '';
    const ttsUrls = ['/api/voice/tts'];
    if (voiceBase) ttsUrls.push(`${voiceBase.replace(/\/+$/, '')}/api/tts`);
    const testPhrase = 'Hello, this is what I sound like. How does this feel to you?';

    // Find and update the button
    const btn = sel?.parentElement?.querySelector('button');
    if (btn) { btn.textContent = '...'; btn.disabled = true; }

    try {
        let res = null;
        let lastErr = null;
        for (const ttsUrl of ttsUrls) {
            try {
                res = await fetch(ttsUrl, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    credentials: 'same-origin',
                    body: JSON.stringify({ text: testPhrase, agent: agentId, voice: voiceName }),
                });
                if (res.ok) break;
                lastErr = new Error('TTS request failed');
                res = null;
            } catch (e) {
                lastErr = e;
                res = null;
            }
        }
        if (!res || !res.ok) throw lastErr || new Error('TTS request failed');

        const arrayBuffer = await res.arrayBuffer();
        if (!_testAudioCtx) _testAudioCtx = new (window.AudioContext || window.webkitAudioContext)();
        // Stop any currently playing test
        if (_testAudioSource) { try { _testAudioSource.stop(); } catch(e) {} }

        const audioBuffer = await _testAudioCtx.decodeAudioData(arrayBuffer);
        _testAudioSource = _testAudioCtx.createBufferSource();
        _testAudioSource.buffer = audioBuffer;
        _testAudioSource.connect(_testAudioCtx.destination);
        _testAudioSource.onended = () => { if (btn) { btn.textContent = '▶'; btn.disabled = false; } };
        _testAudioSource.start();
        if (btn) btn.textContent = '■';
    } catch(e) {
        const result = document.getElementById('voice-save-result');
        if (result) { result.textContent = 'Voice test failed'; result.style.color = 'var(--red)'; }
        if (btn) { btn.textContent = '▶'; btn.disabled = false; }
    }
}

async function _saveVoiceSelection(agentId, voiceName) {
    const result = document.getElementById('voice-save-result');
    if (!MC.features) MC.features = {};

    const featureKey = `voice_${agentId}`;
    const saveData = { [featureKey]: voiceName };

    try {
        const res = await fetch('/api/settings/features', {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(saveData),
        });
        const data = await res.json();
        if (data.ok) {
            MC.features[featureKey] = voiceName;
            if (result) {
                result.textContent = 'Saved';
                result.style.color = 'var(--green, #4caf50)';
                setTimeout(() => { result.textContent = ''; }, 2000);
            }
        } else {
            if (result) {
                result.textContent = 'Error: ' + (data.error || 'Unknown');
                result.style.color = 'var(--red, #f44336)';
            }
        }
    } catch (e) {
        if (result) {
            result.textContent = 'Error: ' + e.message;
            result.style.color = 'var(--red, #f44336)';
        }
    }
}
