// =============================================================================
// lp-colors.js — LP Color System: unified color language for Lucid Principles
// Source of truth for all frequency, signal, principle, and agent colors.
// Must load before core.js (referenced by every tab script).
// =============================================================================

// =============================================================================
// core.js — Config-driven bootstrap for cove-core Mission Control
// =============================================================================
// Reads /api/config on load, builds tabs + panels dynamically.
// No hardcoded agent names, tab lists, or channel names.
// =============================================================================

// ── Debug overlay — catches JS errors visually (for mobile debugging) ────────
// Toggle: add ?debug=1 to URL to show errors on-screen. Remove to hide.
(function() {
    var DEBUG = location.search.indexOf('debug=1') !== -1;
    if (!DEBUG) return;
    var _errBox = null;
    function getBox() {
        if (_errBox) return _errBox;
        _errBox = document.createElement('div');
        _errBox.id = 'js-error-overlay';
        _errBox.style.cssText = 'position:fixed;bottom:0;left:0;right:0;max-height:40vh;overflow:auto;background:rgba(0,0,0,0.92);color:#ff6b6b;font:11px/1.4 monospace;padding:8px 10px;z-index:99999;white-space:pre-wrap;word-break:break-all;';
        var close = document.createElement('button');
        close.textContent = 'X';
        close.style.cssText = 'position:sticky;top:0;float:right;background:#333;color:#fff;border:none;padding:2px 8px;cursor:pointer;font:12px monospace;';
        close.onclick = function() { _errBox.style.display = 'none'; };
        _errBox.appendChild(close);
        document.body.appendChild(_errBox);
        return _errBox;
    }
    function logError(msg) {
        var box = getBox();
        var line = document.createElement('div');
        line.style.cssText = 'border-bottom:1px solid #333;padding:3px 0;';
        line.textContent = msg;
        box.appendChild(line);
        box.style.display = 'block';
        box.scrollTop = box.scrollHeight;
    }
    window.onerror = function(msg, src, line, col) {
        logError('[ERR] ' + msg + ' (' + (src || '').split('/').pop() + ':' + line + ':' + col + ')');
    };
    window.addEventListener('unhandledrejection', function(e) {
        logError('[PROMISE] ' + (e.reason ? (e.reason.message || e.reason) : 'unknown'));
    });
    window._mcDebugLog = logError;
    window._mcDebugScripts = {};
})();
// ── End debug overlay ────────────────────────────────────────────────────────

const ESC = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');

// =============================================================================
// LP Color System — Unified color language across all Lucid Principles systems
// Source of truth: FREQUENCY_COLORS from Lucid Tuner app.js
// Every color in Mission Control derives from a Broadcast Frequency.
// =============================================================================

const LP = {
    // ── 13 Broadcast Frequencies ───────────────────────────────────────────
    freq: {
        Peace:       { primary: '#5ce1e6', secondary: '#7c5cff', glow: 'rgba(92,225,230,0.45)' },
        Clarity:     { primary: '#a0ebff', secondary: '#5ce1e6', glow: 'rgba(160,235,255,0.45)' },
        Momentum:    { primary: '#ff6b5c', secondary: '#ffb86c', glow: 'rgba(255,107,92,0.45)' },
        Trust:       { primary: '#b8c6db', secondary: '#7c5cff', glow: 'rgba(184,198,219,0.45)' },
        Joy:         { primary: '#ffd700', secondary: '#ffb86c', glow: 'rgba(255,215,0,0.45)' },
        Connection:  { primary: '#e0b0ff', secondary: '#ff6b5c', glow: 'rgba(224,176,255,0.45)' },
        Presence:    { primary: '#7b7394', secondary: '#7c5cff', glow: 'rgba(123,115,148,0.45)' },
        Resilience:  { primary: '#d2691e', secondary: '#8b4513', glow: 'rgba(210,105,30,0.45)' },
        Courage:     { primary: '#ff8c00', secondary: '#ff6347', glow: 'rgba(255,140,0,0.45)' },
        Gratitude:   { primary: '#e8b830', secondary: '#ffb347', glow: 'rgba(232,184,48,0.45)' },
        Release:     { primary: '#9370db', secondary: '#ba55d3', glow: 'rgba(147,112,219,0.45)' },
        Integration: { primary: '#20b2aa', secondary: '#48d1cc', glow: 'rgba(32,178,170,0.45)' },
        Boundary:    { primary: '#4682b4', secondary: '#708090', glow: 'rgba(70,130,180,0.45)' },
    },

    // ── 7 Signal Types → dominant frequency color ───────────────────────────
    signal: {
        Ground: 'Peace',
        Clear:  'Clarity',
        Open:   'Connection',
        Rise:   'Momentum',
        Raw:    'Courage',
        Bright: 'Joy',
        Drive:  'Integration',
    },

    // ── Dashboard semantic roles → frequency source ─────────────────────────
    semantic: {
        accent:    'Peace',       // UI primary: calm, clear, functional
        active:    'Integration',  // Active/done/bringing together
        paused:    'Joy',         // Paused = holding space, not negative
        urgent:    'Momentum',    // Urgent = needs momentum NOW
        high:      'Courage',     // High priority = requires courage
        normal:    'Trust',       // Normal = trust the process
        review:    'Release',     // Review = preparing to release/complete
        blocked:   'Boundary',    // Blocked = a boundary is in the way
        overdue:   'Resilience',  // Overdue = requires endurance
    },

    // ── 22 Principles → primary frequency + full frequency spectrum ────────
    // Color Signature: each principle inherits its primary frequency's color.
    // Tuning keys are colored by the frequency they activate (target).
    // Three-layer encoding: Frequency Color + Principle Color + Key Color.
    principle: {
        'A Good Time':            { primary: 'Joy',        frequencies: ['Joy','Gratitude','Peace','Presence','Connection'] },
        'Authenticity':           { primary: 'Boundary',   frequencies: ['Boundary','Clarity','Connection'] },
        'Darkness and Light':     { primary: 'Release',    frequencies: ['Release','Trust','Resilience','Peace','Clarity','Presence','Momentum'] },
        'Dreams':                 { primary: 'Momentum',   frequencies: ['Momentum','Clarity','Gratitude','Joy','Trust','Peace','Presence','Connection','Courage'] },
        'Faith':                  { primary: 'Trust',      frequencies: ['Trust','Release','Clarity','Presence'] },
        'Freedom Is':             { primary: 'Peace',      frequencies: ['Peace','Release','Trust','Joy','Clarity'] },
        'Guiding Force':          { primary: 'Connection', frequencies: ['Connection','Trust','Clarity','Presence'] },
        'Listen':                 { primary: 'Boundary',   frequencies: ['Boundary','Clarity','Trust','Momentum','Connection','Peace'] },
        'Love Song':              { primary: 'Connection', frequencies: ['Connection','Joy','Clarity'] },
        'Moments':                { primary: 'Presence',   frequencies: ['Presence','Clarity','Momentum','Trust'] },
        'Pattern':                { primary: 'Clarity',    frequencies: ['Clarity','Presence','Trust','Momentum'] },
        'Signs':                  { primary: 'Trust',      frequencies: ['Trust','Clarity','Presence'] },
        'The Future':             { primary: 'Courage',    frequencies: ['Courage','Momentum','Boundary','Clarity','Trust'] },
        'The Mirage':             { primary: 'Trust',      frequencies: ['Trust','Resilience','Release','Clarity','Peace','Presence','Momentum','Joy'] },
        'The Passing Tide':       { primary: 'Resilience', frequencies: ['Resilience','Release','Trust','Peace','Momentum','Joy','Clarity'] },
        'The Power To Be Alive':  { primary: 'Courage',    frequencies: ['Courage','Momentum','Gratitude','Connection','Trust','Clarity','Joy','Presence'] },
        'Training Ground':        { primary: 'Resilience', frequencies: ['Resilience','Clarity','Presence','Trust','Peace'] },
        'Truth and Lies':         { primary: 'Boundary',   frequencies: ['Boundary','Clarity','Trust'] },
        'Tune Your Mind':         { primary: 'Clarity',    frequencies: ['Clarity','Momentum','Trust'] },
        'Valley of Shadows':      { primary: 'Courage',    frequencies: ['Courage','Boundary','Resilience','Clarity','Trust','Peace','Connection'] },
        'What Life Is About':     { primary: 'Momentum',   frequencies: ['Momentum','Courage','Joy','Clarity','Connection','Trust'] },
        'Wonder':                 { primary: 'Connection', frequencies: ['Connection','Gratitude','Momentum'] },
    },
};

// ── Lookup helpers ──────────────────────────────────────────────────────────

/** Get frequency color object { primary, secondary, glow } by name (case-insensitive) */
function lpFreqColor(freqName) {
    if (!freqName) return LP.freq.Peace;
    // Try exact match, then title case, then scan keys
    const key = Object.keys(LP.freq).find(k => k.toLowerCase() === freqName.toLowerCase());
    return key ? LP.freq[key] : LP.freq.Peace;
}

/** Get primary color hex for a frequency name */
function lpColor(freqName) {
    return lpFreqColor(freqName).primary;
}

/** Get primary color for a signal type (e.g. "Ground_Signal" or "Ground") */
function lpSignalColor(signalType) {
    if (!signalType) return LP.freq.Peace.primary;
    const clean = signalType.replace(/_Signal$/i, '').replace(/_/g, ' ');
    const key = Object.keys(LP.signal).find(k => k.toLowerCase() === clean.toLowerCase());
    return key ? LP.freq[LP.signal[key]].primary : LP.freq.Peace.primary;
}

/** Get primary color for a semantic role */
function lpSemantic(role) {
    const freqName = LP.semantic[role];
    return freqName ? LP.freq[freqName].primary : LP.freq.Trust.primary;
}

/** Apply frequency color to a .freq-badge element (call after rendering) */
function lpStyleFreqBadge(el, freqName) {
    const c = lpFreqColor(freqName);
    el.style.color = c.primary;
    el.style.borderColor = c.primary;
    el.style.background = c.primary.replace(')', ',0.08)').replace('rgb', 'rgba').replace('#', '');
    // For hex colors, build rgba
    if (c.primary.startsWith('#')) {
        const r = parseInt(c.primary.slice(1,3), 16);
        const g = parseInt(c.primary.slice(3,5), 16);
        const b = parseInt(c.primary.slice(5,7), 16);
        el.style.background = `rgba(${r},${g},${b},0.08)`;
    }
}

/** Render a frequency badge with correct color (returns HTML string) */
function lpFreqBadgeHTML(freqName) {
    const c = lpFreqColor(freqName);
    const r = parseInt(c.primary.slice(1,3), 16) || 0;
    const g = parseInt(c.primary.slice(3,5), 16) || 0;
    const b = parseInt(c.primary.slice(5,7), 16) || 0;
    return `<span class="freq-badge" style="color:${c.primary};border-color:${c.primary};background:rgba(${r},${g},${b},0.08);">${ESC(freqName || '')}</span>`;
}

/** Render a signal type badge with frequency-derived color */
function lpSignalBadgeHTML(signalType) {
    if (!signalType) return '';
    const color = lpSignalColor(signalType);
    const display = signalType.replace(/_Signal$/i, '').replace(/_/g, ' ');
    const r = parseInt(color.slice(1,3), 16) || 0;
    const g = parseInt(color.slice(3,5), 16) || 0;
    const b = parseInt(color.slice(5,7), 16) || 0;
    return `<span class="signal-badge" style="color:${color};border-color:${color};background:rgba(${r},${g},${b},0.08);">${ESC(display)}</span>`;
}

/** Get principle data { primary, frequencies } by name (case-insensitive) */
function lpPrinciple(principleName) {
    if (!principleName) return null;
    const key = Object.keys(LP.principle).find(k => k.toLowerCase() === principleName.toLowerCase());
    return key ? LP.principle[key] : null;
}

/** Get primary color hex for a principle (inherits from primary frequency) */
function lpPrincipleColor(principleName) {
    const p = lpPrinciple(principleName);
    return p ? lpColor(p.primary) : lpColor('Peace');
}

/** Render a principle badge with its primary frequency color */
function lpPrincipleBadgeHTML(principleName) {
    const color = lpPrincipleColor(principleName);
    const r = parseInt(color.slice(1,3), 16) || 0;
    const g = parseInt(color.slice(3,5), 16) || 0;
    const b = parseInt(color.slice(5,7), 16) || 0;
    return `<span class="principle-badge" style="color:${color};border-color:${color};background:rgba(${r},${g},${b},0.08);">${ESC(principleName || '')}</span>`;
}

/** Get the Color Signature for a tuning key moment:
 *  { frequency: hex, principle: hex, key: hex }
 *  - frequency = the frequency being tuned to (primary frequency color)
 *  - principle = the principle's inherited color (same as primary freq with Option A)
 *  - key = the target frequency the tuning key activates
 */
function lpColorSignature(principleName, targetFrequency) {
    const p = lpPrinciple(principleName);
    const freqColor = p ? lpColor(p.primary) : lpColor('Peace');
    const principleColor = freqColor; // Option A: principle inherits primary
    const keyColor = lpColor(targetFrequency);
    return { frequency: freqColor, principle: principleColor, key: keyColor };
}

// ── Agent Identity Colors — each agent maps to their archetype's frequency ──

LP.agent = {
    stuart:    { name: 'Stuart',     archetype: 'The Steward',  frequency: 'Peace',       badge: 'S' },
    archimedes:{ name: 'Archimedes', archetype: 'The Builder',  frequency: 'Momentum',    badge: 'A' },
    arthur:    { name: 'Arthur',     archetype: 'The Analyst',  frequency: 'Clarity',     badge: 'A' },
    gabe:      { name: 'Gabe',       archetype: 'The Scout',    frequency: 'Courage',     badge: 'G' },
    ezra:      { name: 'Ezra',       archetype: 'The Keeper',   frequency: 'Trust',       badge: 'E' },
    julian:    { name: 'Julian',     archetype: 'The Scribe',   frequency: 'Connection',  badge: 'J' },
    iris:      { name: 'Iris',       archetype: 'The Advocate', frequency: 'Joy',         badge: 'I' },
    vera:      { name: 'Vera',       archetype: 'The Auditor',  frequency: 'Boundary',    badge: 'V' },
    soren:     { name: 'Soren',      archetype: 'The Lens',     frequency: 'Integration', badge: 'So' },
    mercer:    { name: 'Mercer',     archetype: 'The Merchant', frequency: 'Gratitude',   badge: 'M' },
    lt:        { name: 'LT',         archetype: 'The Field Coach', frequency: 'Presence', badge: 'LT' },
    atlas:     { name: 'Atlas',      archetype: 'The Architect', frequency: 'Clarity',    badge: 'At' },
};

/** Normalize agent ID — lowercase, strip family suffix, alias admin user.
 *  Family suffix is derived from MC.instance.family_name at runtime.
 *  Falls back to stripping any trailing -{word} for legacy IDs. */
function _resolveAgentId(raw) {
    if (!raw) return '';
    let id = raw.toLowerCase();
    // Strip family suffix if instance config available (e.g., "-smith")
    const hhName = (MC.instance?.family_name || '').toLowerCase();
    if (hhName && id.endsWith('-' + hhName)) {
        id = id.slice(0, -(hhName.length + 1));
    }
    return id;
}

/** Get agent color (primary hex) by agent_id */
function lpAgentColor(agentId) {
    const a = LP.agent[_resolveAgentId(agentId)];
    return a ? lpColor(a.frequency) : lpColor('Trust');
}

/** Get display name for an agent_id */
function lpAgentName(agentId) {
    const a = LP.agent[_resolveAgentId(agentId)];
    return a ? a.name : (agentId || '');
}

/** Render a frequency symbol — circular, larger, for card/hero layouts */
function lpFreqSymbolHTML(frequency) {
    if (!frequency) return '';
    const color = lpColor(frequency);
    const initial = frequency.charAt(0).toUpperCase();
    const r = parseInt(color.slice(1,3), 16) || 0;
    const g = parseInt(color.slice(3,5), 16) || 0;
    const b = parseInt(color.slice(5,7), 16) || 0;
    return `<span class="freq-symbol" style="background:rgba(${r},${g},${b},0.15);color:${color};border:1.5px solid ${color};" title="${ESC(frequency)}">${initial}</span>`;
}

/** Render an agent badge — colored letter rectangle */
function lpAgentBadgeHTML(agentId) {
    const id = _resolveAgentId(agentId);
    const a = LP.agent[id];
    if (!a) return `<span class="agent-badge" style="background:${lpColor('Trust')};">${ESC(agentId ? agentId[0].toUpperCase() : '?')}</span>`;
    const color = lpColor(a.frequency);
    const r = parseInt(color.slice(1,3), 16) || 0;
    const g = parseInt(color.slice(3,5), 16) || 0;
    const b = parseInt(color.slice(5,7), 16) || 0;
    return `<span class="agent-badge" style="background:rgba(${r},${g},${b},0.18);color:${color};border:1px solid ${color};" title="${ESC(a.name)} — ${ESC(a.archetype)}">${ESC(a.badge)}</span>`;
}
