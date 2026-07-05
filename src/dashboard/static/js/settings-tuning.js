// =============================================================================
// settings-tuning.js — Signal filter types, LTP settings, Cove timezone
// =============================================================================

// ── Signal Type Filter ───────────────────────────────────────────────────
const _SIGNAL_FILTER_TYPES = [
    { name: 'Ground',  color: '#5ce1e6' },
    { name: 'Clear',   color: '#a0ebff' },
    { name: 'Open',    color: '#e0b0ff' },
    { name: 'Rise',    color: '#ff6b5c' },
    { name: 'Raw',     color: '#ff8c00' },
    { name: 'Bright',  color: '#ffd700' },
    { name: 'Drive',   color: '#20b2aa' },
];

async function loadSettingsSignalFilter() {
    const el = document.getElementById('settings-signal-filter');
    if (!el) return;

    const excluded = MC.features?.excluded_signals || [];
    const excludedSet = new Set(excluded.map(s => s.toLowerCase()));

    let html = `
        <div style="font-size:0.7rem;color:var(--dim);margin-bottom:8px;">
            Exclude signal types from your tuning. These won't appear in generated tunings.
        </div>`;

    _SIGNAL_FILTER_TYPES.forEach(sig => {
        const isEnabled = !excludedSet.has(sig.name.toLowerCase());
        html += `
        <div class="settings-edit-row" style="display:flex;align-items:center;justify-content:space-between;padding:6px 0;">
            <div style="display:flex;align-items:center;gap:8px;">
                <span style="width:10px;height:10px;border-radius:50%;background:${sig.color};flex-shrink:0;"></span>
                <span style="font-size:0.82rem;">${sig.name} Signal</span>
            </div>
            <div style="cursor:pointer;position:relative;width:40px;height:22px;flex-shrink:0;"
                 onclick="_toggleSignalFilter('${sig.name}', ${isEnabled})">
                <span style="pointer-events:none;position:absolute;inset:0;background:${isEnabled ? sig.color : 'var(--border)'};border-radius:11px;transition:background 0.2s;"></span>
                <span style="pointer-events:none;position:absolute;top:2px;left:${isEnabled ? '20px' : '2px'};width:18px;height:18px;background:#fff;border-radius:50%;transition:left 0.2s;"></span>
            </div>
        </div>`;
    });

    el.innerHTML = html;
}

async function _toggleSignalFilter(signalName, currentlyEnabled) {
    const excluded = MC.features?.excluded_signals || [];
    let updated;

    if (currentlyEnabled) {
        // Turning off — add to exclusions
        updated = [...excluded, signalName];
    } else {
        // Turning on — remove from exclusions
        updated = excluded.filter(s => s.toLowerCase() !== signalName.toLowerCase());
    }

    // Don't let them exclude everything
    if (updated.length >= _SIGNAL_FILTER_TYPES.length) {
        return; // silently prevent — at least one must remain
    }

    try {
        const res = await fetch('/api/settings/features', {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ excluded_signals: updated }),
        });
        const data = await res.json();
        if (data.ok) {
            if (!MC.features) MC.features = {};
            MC.features.excluded_signals = updated;
            loadSettingsSignalFilter();
        }
    } catch (e) {
        console.warn('[settings] Signal filter save failed:', e.message);
    }
}

async function loadSettingsLTP() {
    const el = document.getElementById('settings-ltp');
    if (!el) return;
    try {
        const data = await fetch('/api/settings/system').then(r => r.json());

        if (data.error) {
            el.innerHTML = `<div class="error-msg">${ESC(data.error)}</div>`;
            return;
        }

        const ltp = data.ltp || {};
        const coveTimezone = ltp.timezone || MC.instance?.timezone || 'America/New_York';
        const rows = [
            ['Source', ltp.source || ''],
            ['Delivery', ltp.delivery || ''],
            ['Schedule', ltp.schedule || ''],
        ];

        el.innerHTML = rows.map(([label, val]) => `
            <div class="settings-row">
                <span class="settings-label">${ESC(label)}</span>
                <span class="settings-val">${ESC(String(val))}</span>
            </div>`).join('') + `
            <div class="settings-row" style="align-items:center;">
                <span class="settings-label">Cove Timezone</span>
                <div style="display:flex;align-items:center;gap:8px;">
                    <select id="ltp-cove-timezone" class="settings-input" style="max-width:220px;">
                        ${_buildTimezoneOptions(coveTimezone)}
                    </select>
                    <button class="btn-sm" onclick="saveCoveTimezone()">Save</button>
                    <span id="ltp-tz-result" style="font-size:0.7rem;color:var(--dim);"></span>
                </div>
            </div>`;
    } catch (err) {
        el.innerHTML = `<div class="error-msg">${ESC(err.message)}</div>`;
    }
}

async function saveCoveTimezone() {
    const el = document.getElementById('ltp-cove-timezone');
    const result = document.getElementById('ltp-tz-result');
    if (!el) return;
    const tz = el.value;
    if (result) { result.textContent = 'Saving...'; result.style.color = 'var(--dim)'; }
    try {
        const res = await fetch('/api/settings/cove', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ timezone: tz }),
        });
        if (!res.ok) throw new Error(`${res.status}`);
        if (result) { result.textContent = 'Saved'; result.style.color = 'var(--green)'; }
        // Update the in-memory config so other features pick it up immediately
        if (MC.instance) MC.instance.timezone = tz;
    } catch (err) {
        if (result) { result.textContent = 'Error: ' + err.message; result.style.color = 'var(--red, #f44336)'; }
    }
}
