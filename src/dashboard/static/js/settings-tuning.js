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


// ── Morning open alert (habit wedge) ─────────────────────────────────────
// Persist preferred local time; schedule best-effort Notification when the
// browser allows. Deep link: /?tab=tune&view=latest → latest Drop.

async function loadSettingsMorningAlert() {
    const el = document.getElementById('settings-morning-alert');
    if (!el) return;

    let alert = { enabled: false, local_time: '07:00' };
    try {
        const res = await fetch('/api/tuning/morning-alert');
        const data = await res.json();
        if (data && data.morning_alert) alert = data.morning_alert;
    } catch (e) { /* defaults */ }

    const enabled = !!alert.enabled;
    const time = alert.local_time || '07:00';
    const notifOk = (typeof Notification !== 'undefined');
    const perm = notifOk ? Notification.permission : 'unsupported';
    let supportNote = '';
    if (!notifOk) {
        supportNote = 'This browser cannot schedule local alerts. Time is saved for when a capable shell is available.';
    } else if (perm === 'denied') {
        supportNote = 'Notifications are blocked for this site. Enable them in browser settings, or add to Home Screen where supported.';
    } else if (perm === 'default') {
        supportNote = 'When you turn this on, the browser will ask to allow alerts. On iPhone, add the app to your Home Screen for the best chance of delivery — Safari tabs are limited.';
    } else {
        supportNote = 'Alert fires around your chosen local time while this app can run (installed PWA / open browser). True lock-screen delivery varies by OS.';
    }

    el.innerHTML = `
        <div style="font-size:0.7rem;color:var(--dim);margin-bottom:10px;line-height:1.45;">
            Morning open — open the <strong>latest daily tuning</strong> (practice + music) from a time you choose. Low-friction habit path; not a second Tune wizard.
        </div>
        <div class="settings-edit-row" style="display:flex;align-items:center;justify-content:space-between;padding:6px 0;">
            <span style="font-size:0.82rem;">Morning alert</span>
            <div style="cursor:pointer;position:relative;width:40px;height:22px;flex-shrink:0;"
                 onclick="_toggleMorningAlertEnabled(${enabled ? 'true' : 'false'})">
                <span style="pointer-events:none;position:absolute;inset:0;background:${enabled ? 'var(--accent,#5ce1e6)' : 'var(--border)'};border-radius:11px;transition:background 0.2s;"></span>
                <span style="pointer-events:none;position:absolute;top:2px;left:${enabled ? '20px' : '2px'};width:18px;height:18px;background:#fff;border-radius:50%;transition:left 0.2s;"></span>
            </div>
        </div>
        <div class="settings-edit-row" style="display:flex;align-items:center;justify-content:space-between;padding:6px 0;gap:12px;">
            <span style="font-size:0.82rem;">Local time</span>
            <input type="time" id="morning-alert-time" value="${ESC(time)}"
                   style="background:var(--surface);border:1px solid var(--border);color:var(--text);border-radius:8px;padding:6px 10px;font-size:0.85rem;"
                   onchange="saveMorningAlertTime(this.value)" />
        </div>
        <div id="morning-alert-status" style="font-size:0.68rem;color:var(--dim);margin-top:8px;line-height:1.4;">${ESC(supportNote)}</div>
    `;

    // Keep in-memory + reschedule if already enabled
    if (!MC.features) MC.features = {};
    MC.features.morning_alert = alert;
    if (typeof window._morningAlertReschedule === 'function') {
        window._morningAlertReschedule(alert);
    }
}

async function _toggleMorningAlertEnabled(currentlyEnabled) {
    const next = !currentlyEnabled;
    if (next && typeof Notification !== 'undefined' && Notification.permission === 'default') {
        try { await Notification.requestPermission(); } catch (e) {}
    }
    await _saveMorningAlert({ enabled: next });
}

async function saveMorningAlertTime(value) {
    if (!value) return;
    // input type=time may be HH:MM
    await _saveMorningAlert({ local_time: value.slice(0, 5) });
}

async function _saveMorningAlert(partial) {
    const status = document.getElementById('morning-alert-status');
    try {
        const res = await fetch('/api/tuning/morning-alert', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(partial || {}),
        });
        const data = await res.json();
        if (!res.ok || data.ok === false) {
            if (status) { status.textContent = data.error || 'Could not save'; status.style.color = 'var(--red,#f44336)'; }
            return;
        }
        if (!MC.features) MC.features = {};
        MC.features.morning_alert = data.morning_alert;
        if (typeof window._morningAlertReschedule === 'function') {
            window._morningAlertReschedule(data.morning_alert);
        }
        await loadSettingsMorningAlert();
    } catch (e) {
        if (status) { status.textContent = 'Could not save: ' + e.message; status.style.color = 'var(--red,#f44336)'; }
    }
}
