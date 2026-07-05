// =============================================================================
// settings-voice.js — Voice selection from pipecat-voice
// =============================================================================

// Cache for available voices from the voice server
let _voicesCache = null;

async function _fetchAvailableVoices() {
    if (_voicesCache) return _voicesCache;
    try {
        const voiceBase = MC.voiceUrl('http');
        if (!voiceBase) { _voicesCache = []; return _voicesCache; }
        const res = await fetch(`${voiceBase}/api/tts/voices`);
        const data = await res.json();
        _voicesCache = data;
    } catch (e) {
        console.warn('[settings] Failed to fetch voices:', e.message);
        _voicesCache = { available_files: [], config: {} };
    }
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
    // Use chat_agents (host + steward + merchant) when available so any Presence
    // can configure how Stuart and Mercer sound. Falls back to MC.agents.
    const agents = MC.config?.chat_agents || MC.agents || [];
    const agentList = [];

    for (const agent of agents) {
        agentList.push({ id: agent.id, name: agent.name, emoji: agent.emoji || '' });
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
    const voiceBase = MC.voiceUrl('http');
    if (!voiceBase) { console.warn('[settings] no voice backend configured'); return; }
    const ttsUrl = `${voiceBase}/api/tts`;
    const testPhrase = 'Hello, this is what I sound like. How does this feel to you?';

    // Find and update the button
    const btn = sel?.parentElement?.querySelector('button');
    if (btn) { btn.textContent = '...'; btn.disabled = true; }

    try {
        const res = await fetch(ttsUrl, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: testPhrase, agent: agentId, voice: voiceName }),
        });
        if (!res.ok) throw new Error('TTS request failed');

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
