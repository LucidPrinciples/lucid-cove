// =============================================================================
// Stuart Mission Control — System tab + live logs + server hardware metrics
// =============================================================================

// =============================================================================
// System tab
// =============================================================================
function _sysIsAdmin() {
    const t = MC.instance?.type;
    return t === 'admin' || t === 'domain' || t === 'manager';
}

async function loadSystem() {
    if (_sysIsAdmin()) loadHardwareMetrics();
    loadRunbooks();
    loadHealth();
    loadScheduler();
    loadMemoryPipelines();
    loadDbStats();
    loadConfig();
    loadLogDates();
    loadLogs();
    renderLPColorLegend();
}

function renderLPColorLegend() {
    const el = document.getElementById('lpColorLegend');
    if (!el || typeof LP === 'undefined') return;

    function swatch(hex, size) {
        size = size || 12;
        return `<span style="display:inline-block;width:${size}px;height:${size}px;border-radius:3px;background:${hex};flex-shrink:0;${hex === '#ffffff' ? 'border:1px solid #555;' : ''}"></span>`;
    }

    let html = '<div class="lp-legend">';

    // Section 1: Frequencies
    html += '<div class="lp-legend-section"><div class="lp-legend-title">13 Broadcast Frequencies</div>';
    html += '<div class="lp-legend-grid">';
    for (const [name, c] of Object.entries(LP.freq)) {
        html += `<div class="lp-legend-item">
            ${swatch(c.primary, 14)}
            <span class="lp-legend-name" style="color:${c.primary};">${ESC(name)}</span>
            <span class="lp-legend-hex">${c.primary}</span>
        </div>`;
    }
    html += '</div></div>';

    // Section 2: Signal Types
    html += '<div class="lp-legend-section"><div class="lp-legend-title">7 Signal Types</div>';
    html += '<div class="lp-legend-grid">';
    for (const [sig, freqName] of Object.entries(LP.signal)) {
        const c = LP.freq[freqName];
        html += `<div class="lp-legend-item">
            ${swatch(c.primary, 14)}
            <span class="lp-legend-name" style="color:${c.primary};">${ESC(sig)} Signal</span>
            <span class="lp-legend-hex">${ESC(freqName)}</span>
        </div>`;
    }
    html += '</div></div>';

    // Section 3: Dashboard Semantic Mappings
    html += '<div class="lp-legend-section"><div class="lp-legend-title">Dashboard Color Roles</div>';
    html += '<div class="lp-legend-grid">';
    const roleLabels = {
        accent: 'UI Accent / Primary',
        active: 'Active / Done / Growth',
        paused: 'Paused / Holding',
        urgent: 'Urgent Priority',
        high: 'High Priority',
        normal: 'Normal Priority',
        review: 'Review / Transition',
        blocked: 'Blocked / Held',
        overdue: 'Overdue / Endurance',
    };
    for (const [role, freqName] of Object.entries(LP.semantic)) {
        const c = LP.freq[freqName];
        html += `<div class="lp-legend-item">
            ${swatch(c.primary, 14)}
            <span class="lp-legend-name">${ESC(roleLabels[role] || role)}</span>
            <span class="lp-legend-hex" style="color:${c.primary};">${ESC(freqName)}</span>
        </div>`;
    }
    html += '</div></div>';

    html += '</div>';
    el.innerHTML = html;
}

async function loadHealth() {
    const el = document.getElementById('healthList');
    try {
        // Use lightweight ping — does NOT invoke models or load GPU
        const res = await fetch('/api/system/ping');
        const data = await res.json();
        let html = '';

        if (data.services) {
            for (const [svc, info] of Object.entries(data.services)) {
                const ok = info.status === 'healthy' || info.status === 'configured';
                let detail = '';
                if (info.latency_ms) detail = info.latency_ms + 'ms';
                else if (info.available_models) detail = info.available_models.length + ' models';
                else if (info.status === 'not configured') detail = 'missing key';
                else if (info.error) detail = esc(info.error);

                html += `<div class="health-row">
                    <div class="health-status">
                        <span class="health-dot ${ok ? 'ok' : 'err'}"></span>
                        <span>${esc(svc)}</span>
                    </div>
                    <span class="health-latency">${detail}</span>
                </div>`;
            }
        }

        // Test Models button (admin/domain only — loads GPU)
        if (_sysIsAdmin()) {
            html += `<div style="margin-top:10px;border-top:1px solid var(--border);padding-top:8px;">
                <button class="btn btn-action" onclick="testModels()" id="testModelsBtn" style="padding:4px 10px;font-size:0.72rem;">Test Models</button>
                <span id="testModelsResult" style="margin-left:8px;font-size:0.72rem;color:var(--dim);"></span>
            </div>`;
        }

        el.innerHTML = html || '<span class="empty">Could not load health data</span>';
    } catch (e) {
        el.innerHTML = `<span class="empty">Error: ${esc(e.message)}</span>`;
    }
}

async function testModels() {
    const btn = document.getElementById('testModelsBtn');
    const result = document.getElementById('testModelsResult');
    btn.disabled = true;
    btn.textContent = 'Testing...';
    result.textContent = 'Invoking models (loads GPU)...';
    result.style.color = 'var(--yellow)';
    try {
        const res = await fetch('/api/system/health');
        const data = await res.json();
        let parts = [];
        if (data.tiers) {
            for (const [tier, info] of Object.entries(data.tiers)) {
                const ok = info.status === 'healthy';
                parts.push(`${tier}: ${ok ? info.latency_ms + 'ms' : 'FAIL'}`);
            }
        }
        if (data.database) {
            parts.push(`db: ${data.database.status === 'healthy' ? data.database.latency_ms + 'ms' : 'FAIL'}`);
        }
        result.textContent = parts.join(' | ');
        result.style.color = 'var(--green)';
    } catch (e) {
        result.textContent = 'Error: ' + e.message;
        result.style.color = 'var(--red)';
    }
    btn.disabled = false;
    btn.textContent = 'Test Models';
}

async function loadScheduler() {
    const el = document.getElementById('schedulerInfo');
    try {
        const res = await fetch('/api/system/scheduler');
        const data = await res.json();
        const jobs = data.jobs || [];

        if (!jobs.length) {
            el.innerHTML = '<span class="empty">No scheduled jobs</span>';
            return;
        }

        el.innerHTML = `<table class="data-table">
            <thead><tr><th>Job</th><th>Schedule</th><th>Status</th></tr></thead>
            <tbody>${jobs.map(j => `<tr>
                <td>${esc(j.name || j.id || '')}</td>
                <td class="date-dim">${esc(j.schedule || j.cron || '')}</td>
                <td>${j.enabled === false ? '<span style="color:var(--dim)">disabled</span>' : '<span class="success">active</span>'}</td>
            </tr>`).join('')}</tbody>
        </table>`;
    } catch (e) {
        el.innerHTML = `<span class="empty">Error: ${esc(e.message)}</span>`;
    }
}

async function loadMemoryPipelines() {
    const el = document.getElementById('memoryPipelines');
    if (!el) return;
    try {
        const res = await fetch('/api/system/archive-digestion');
        if (!res.ok) {
            el.innerHTML = '<span class="empty">Archive digestion not available</span>';
            return;
        }
        const data = await res.json();
        const total = data.total_sessions || 0;
        const digested = data.digested_sessions || 0;
        const pending = data.undigested_sessions || 0;
        const pct = total > 0 ? Math.round((digested / total) * 100) : 0;
        const color = pending === 0 ? 'var(--green)' : 'var(--yellow)';

        let html = `<div class="health-row">
            <div class="health-status">
                <span class="health-dot ${pending === 0 ? 'ok' : 'err'}"></span>
                <span>Archive Digestion</span>
            </div>
            <span class="health-latency" style="color:${color}">${digested}/${total} sessions (${pct}%)</span>
        </div>`;

        if (pending > 0) {
            html += `<div style="margin-top:4px;font-size:0.72rem;color:var(--dim);">${pending} sessions pending: #${(data.pending_numbers || []).join(', #')}</div>`;
        }

        html += `<div style="margin-top:8px;border-top:1px solid var(--border);padding-top:8px;">
            <button class="btn btn-action" onclick="triggerArchiveDigestion()" id="digestBtn" style="padding:4px 10px;font-size:0.72rem;">Run Now</button>
            <button class="btn" onclick="cancelArchiveDigestion()" id="digestStopBtn" style="padding:4px 10px;font-size:0.72rem;display:none;background:var(--red);color:#fff;">Stop</button>
            <span id="digestResult" style="margin-left:8px;font-size:0.72rem;color:var(--dim);">Next: Sunday 8:30pm</span>
        </div>`;

        el.innerHTML = html;
    } catch (e) {
        el.innerHTML = `<span class="empty">Error: ${esc(e.message)}</span>`;
    }
}

async function triggerArchiveDigestion() {
    const btn = document.getElementById('digestBtn');
    const stopBtn = document.getElementById('digestStopBtn');
    const result = document.getElementById('digestResult');
    btn.disabled = true;
    btn.textContent = 'Running...';
    if (stopBtn) stopBtn.style.display = 'inline-block';
    result.textContent = 'Processing archives (this may take a few minutes)...';
    result.style.color = 'var(--yellow)';
    try {
        const res = await fetch('/api/system/archive-digestion', { method: 'POST' });
        const data = await res.json();
        if (data.status === 'success') {
            result.textContent = `Done — ${data.sessions_processed || 0} sessions, ${data.memories_created || 0} memories, ${data.chunks_created || 0} chunks`;
            result.style.color = 'var(--green)';
        } else if (data.status === 'up_to_date') {
            result.textContent = 'All sessions already digested';
            result.style.color = 'var(--green)';
        } else {
            result.textContent = data.error || data.status || 'Unknown result';
            result.style.color = 'var(--red)';
        }
        // Refresh the status display
        setTimeout(loadMemoryPipelines, 1500);
    } catch (e) {
        result.textContent = 'Error: ' + e.message;
        result.style.color = 'var(--red)';
    }
    btn.disabled = false;
    btn.textContent = 'Run Now';
    if (stopBtn) stopBtn.style.display = 'none';
}

async function cancelArchiveDigestion() {
    const stopBtn = document.getElementById('digestStopBtn');
    const logStopBtn = document.getElementById('logStopPipelineBtn');
    const result = document.getElementById('digestResult');
    if (stopBtn) { stopBtn.disabled = true; stopBtn.textContent = 'Stopping...'; }
    if (logStopBtn) { logStopBtn.disabled = true; logStopBtn.textContent = 'Stopping...'; }
    try {
        const res = await fetch('/api/system/archive-digestion/cancel', { method: 'POST' });
        const data = await res.json();
        const msg = data.message || 'Cancel requested — will stop after current session';
        if (result) { result.textContent = msg; result.style.color = 'var(--yellow)'; }
        // Also append to log output so it's visible in the logs
        const logOut = document.getElementById('logOutput');
        if (logOut) {
            const line = document.createElement('div');
            line.className = 'log-line';
            line.style.color = 'var(--yellow)';
            line.textContent = `[STOP] ${msg}`;
            logOut.appendChild(line);
            logOut.scrollTop = logOut.scrollHeight;
        }
    } catch (e) {
        const errMsg = 'Cancel failed: ' + e.message;
        if (result) { result.textContent = errMsg; result.style.color = 'var(--red)'; }
    }
    if (stopBtn) { stopBtn.disabled = false; stopBtn.textContent = 'Stop'; }
    if (logStopBtn) { logStopBtn.disabled = false; logStopBtn.textContent = 'Stop Pipeline'; }
}

async function loadDbStats() {
    const el = document.getElementById('dbStats');
    try {
        const res = await fetch('/api/system/db-stats');
        const data = await res.json();
        // API returns {tables: [{table_name, row_count}, ...]}
        const tables = data.tables || [];

        if (!tables.length) {
            el.innerHTML = '<span class="empty">No data</span>';
            return;
        }

        el.innerHTML = `<table class="data-table">
            <thead><tr><th>Table</th><th>Rows</th></tr></thead>
            <tbody>${tables.map(t => `<tr>
                <td>${esc(t.table_name || t.relname || '')}</td>
                <td>${typeof t.row_count === 'number' ? t.row_count.toLocaleString() : esc(String(t.row_count || t.n_live_tup || 0))}</td>
            </tr>`).join('')}</tbody>
        </table>`;
    } catch (e) {
        el.innerHTML = `<span class="empty">Error: ${esc(e.message)}</span>`;
    }
}

async function loadConfig() {
    const el = document.getElementById('configInfo');
    try {
        const res = await fetch('/api/system/config');
        const data = await res.json();
        // API returns {agents: {...}, defaults: {...}, environment: {...}}
        // Show environment config (flat key-value pairs)
        const env = data.environment || {};
        const defaults = data.defaults || {};
        const model = defaults.model || {};

        let html = '';
        // Model chain
        if (model.primary) html += `<div class="config-row"><span class="config-key">Primary Model</span><span class="config-val">${esc(model.primary)}</span></div>`;
        if (model.fallback) html += `<div class="config-row"><span class="config-key">Fallback Model</span><span class="config-val">${esc(model.fallback)}</span></div>`;
        // Environment
        for (const [key, val] of Object.entries(env)) {
            html += `<div class="config-row"><span class="config-key">${esc(key)}</span><span class="config-val">${esc(String(val))}</span></div>`;
        }
        // Agent count
        const agentCount = Object.keys(data.agents || {}).length;
        html += `<div class="config-row"><span class="config-key">Registered Agents</span><span class="config-val">${agentCount}</span></div>`;

        el.innerHTML = html || '<span class="empty">No config data</span>';
    } catch (e) {
        el.innerHTML = `<span class="empty">Error: ${esc(e.message)}</span>`;
    }
}

// =============================================================================
// Server Hardware Metrics — THIS box, detected live (CF-60)
// =============================================================================
async function loadHardwareMetrics() {
    const el = document.getElementById('hardwareMetrics');
    if (!el) return;
    el.innerHTML = '<span class="empty">Loading metrics...</span>';
    try {
        const res = await fetch('/api/system/hardware-metrics');
        const data = await res.json();
        let html = '<div class="metrics-grid">';

        // CPU — core count comes from the box itself; no hardcoded default
        if (data.cpu && !data.cpu.error) {
            const load1 = data.cpu.load_1m;
            const cores = data.cpu.cores || 0;
            const pct = cores ? Math.min(100, (load1 / cores) * 100).toFixed(0) : null;
            const color = pct == null ? 'var(--dim)' : pct > 80 ? 'var(--red)' : pct > 50 ? 'var(--yellow)' : 'var(--green)';
            html += `<div class="metric-card">
                <div class="metric-label">CPU</div>
                <div class="metric-value" style="color:${color}">${pct != null ? pct + '%' : load1}</div>
                <div class="metric-detail">Load: ${load1} / ${data.cpu.load_5m} / ${data.cpu.load_15m}</div>
                <div class="metric-detail">${cores ? cores + ' threads | ' : ''}${data.cpu.processes}</div>
            </div>`;
        }

        // Memory
        if (data.memory && !data.memory.error) {
            const pct = data.memory.percent_used;
            const color = pct > 85 ? 'var(--red)' : pct > 70 ? 'var(--yellow)' : 'var(--green)';
            html += `<div class="metric-card">
                <div class="metric-label">RAM</div>
                <div class="metric-value" style="color:${color}">${pct}%</div>
                <div class="metric-detail">${data.memory.used_gb}GB / ${data.memory.total_gb}GB</div>
                <div class="metric-detail">${data.memory.available_gb}GB available</div>
            </div>`;
        }

        // GPU
        if (data.gpu) {
            const loaded = data.gpu.loaded_models || [];
            const vramUsed = data.gpu.total_vram_used_gb || 0;
            const vramCap = data.gpu.vram_capacity_gb || 0;          // real card capacity (#204)
            const gpuName = data.gpu.name || 'GPU';                  // real card name, not hardcoded
            const pct = vramCap ? ((vramUsed / vramCap) * 100).toFixed(0) : '0';
            const isIdle = data.gpu.status === 'idle';
            const temp = data.gpu.temp_c;
            const power = data.gpu.power_w;
            const util = data.gpu.utilization_pct;
            const fan = data.gpu.fan_pct;
            const tempColor = temp == null ? 'var(--dim)' : temp > 80 ? 'var(--red)' : temp > 65 ? 'var(--yellow)' : 'var(--green)';
            const color = isIdle ? 'var(--green)' : vramUsed > 20 ? 'var(--red)' : 'var(--yellow)';
            let gpuDetail = isIdle ? 'No models loaded' : loaded.map(m => `${m.name} (${m.size_gb}GB)`).join(', ');
            html += `<div class="metric-card">
                <div class="metric-label">${esc(gpuName)}</div>
                <div class="metric-value" style="color:${color}">${util != null ? util + '%' : isIdle ? 'IDLE' : pct + '%'}</div>
                <div class="metric-detail">${vramCap ? `VRAM: ${vramUsed}GB / ${vramCap}GB` : `VRAM: ${vramUsed}GB`}</div>
                ${temp != null ? `<div class="metric-detail" style="color:${tempColor}">Temp: ${temp}°C${power != null ? ' | Power: ' + power + 'W' : ''}${fan != null ? ' | Fan: ' + fan + '%' : ''}</div>` : ''}
                <div class="metric-detail">${gpuDetail}</div>
            </div>`;
        }

        // Uptime
        if (data.uptime && !data.uptime.error) {
            html += `<div class="metric-card">
                <div class="metric-label">Uptime</div>
                <div class="metric-value" style="color:var(--accent)">${esc(data.uptime.display)}</div>
                <div class="metric-detail">System uptime</div>
            </div>`;
        }

        html += '</div>';

        // Disk section below
        if (data.disk && !data.disk.error) {
            html += '<div style="margin-top:10px;border-top:1px solid var(--border);padding-top:8px;">';
            html += '<div class="metric-detail" style="margin-bottom:4px;color:var(--dim);">Disk Usage</div>';
            for (const [name, info] of Object.entries(data.disk)) {
                const color = info.percent_used > 90 ? 'var(--red)' : info.percent_used > 75 ? 'var(--yellow)' : 'var(--dim)';
                html += `<div class="health-row">
                    <div class="health-status"><span>${esc(name)} (${info.total_gb}GB)</span></div>
                    <span class="health-latency" style="color:${color}">${info.percent_used}% used (${info.free_gb}GB free)</span>
                </div>`;
            }
            html += '</div>';
        }

        el.innerHTML = html;
    } catch (e) {
        el.innerHTML = `<span class="empty">Error: ${esc(e.message)}</span>`;
    }
}

// =============================================================================

// Logs & Protocol Runs
// =============================================================================
// ── Live Log Streaming (SSE) ──
let logEventSource = null;
let logAutoScroll = true;

function formatLogLine(text) {
    const lower = text.toLowerCase();
    let cls = '';
    if (lower.includes('error') || lower.includes('ERROR')) cls = 'error';
    else if (lower.includes('warn') || lower.includes('WARNING')) cls = 'warn';
    else if (lower.includes('success') || lower.includes('complete')) cls = 'success';
    else if (lower.includes('[ltp') || lower.includes('[protocol') || lower.includes('[tuning') || lower.includes('[scheduler')) cls = 'protocol';

    // Try to extract and format timestamp from common log patterns
    // Pattern: "INFO:     1.2.3.4:1234 - "GET ..." — uvicorn access log (no timestamp)
    // Pattern: "2026-05-02 14:30:05,123 - ..." — Python logging
    // Pattern: "[2026-05-02 14:30:05]" — bracketed timestamp
    let display = esc(text);
    const tsMatch = text.match(/^(\d{4}-\d{2}-\d{2}[\sT]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?)\s*[-\s]?\s*(.*)/);
    const bracketMatch = text.match(/^\[(\d{4}-\d{2}-\d{2}[\sT]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?)\]\s*(.*)/);
    if (bracketMatch) {
        const ts = bracketMatch[1].replace('T', ' ');
        display = `<span class="log-ts">${esc(ts)}</span> ${esc(bracketMatch[2])}`;
    } else if (tsMatch) {
        const ts = tsMatch[1].replace('T', ' ');
        display = `<span class="log-ts">${esc(ts)}</span> ${esc(tsMatch[2])}`;
    }

    return `<div class="log-line ${cls}">${display}</div>`;
}

function connectLogStream() {
    const el = document.getElementById('logOutput');
    const statusEl = document.getElementById('logStreamStatus');
    const btn = document.getElementById('logStreamBtn');
    if (!el) return;

    if (logEventSource) { logEventSource.close(); logEventSource = null; }

    const filterEl = document.getElementById('logFilter');
    const filter = filterEl ? filterEl.value.trim() : '';
    const url = `/api/logs/stream${filter ? '?filter=' + encodeURIComponent(filter) : ''}`;

    el.innerHTML = '';
    statusEl.textContent = 'connecting...';
    statusEl.style.color = 'var(--yellow)';
    btn.textContent = 'Disconnect';

    logEventSource = new EventSource(url);

    logEventSource.onopen = () => {
        statusEl.textContent = 'streaming';
        statusEl.style.color = 'var(--green)';
    };

    logEventSource.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            el.innerHTML += formatLogLine(data.text || '');
            if (logAutoScroll) el.scrollTop = el.scrollHeight;
        } catch (e) {}
    };

    logEventSource.onerror = () => {
        statusEl.textContent = 'disconnected';
        statusEl.style.color = 'var(--red)';
        btn.textContent = 'Connect';
        logEventSource.close();
        logEventSource = null;
    };

    // Detect if user scrolled up (disable auto-scroll)
    el.addEventListener('scroll', () => {
        logAutoScroll = (el.scrollTop + el.clientHeight >= el.scrollHeight - 30);
    });
}

function disconnectLogStream() {
    if (logEventSource) { logEventSource.close(); logEventSource = null; }
    const statusEl = document.getElementById('logStreamStatus');
    const btn = document.getElementById('logStreamBtn');
    if (statusEl) { statusEl.textContent = 'disconnected'; statusEl.style.color = 'var(--dim)'; }
    if (btn) btn.textContent = 'Connect';
}

function toggleLogStream() {
    if (logEventSource) disconnectLogStream();
    else connectLogStream();
}

async function loadLogDates() {
    const picker = document.getElementById('logDatePicker');
    if (!picker) return;
    try {
        const res = await fetch('/api/logs/dates');
        const data = await res.json();
        const dates = data.dates || [];
        picker.innerHTML = '<option value="live">Live (today)</option>';
        dates.forEach(d => {
            picker.innerHTML += `<option value="${d.date}">${d.date} (${d.size_kb}kb)</option>`;
        });
        picker.onchange = async () => {
            if (picker.value === 'live') {
                connectLogStream();
            } else {
                disconnectLogStream();
                await loadLogsForDate(picker.value);
            }
        };
    } catch (e) {}
}

async function loadLogsForDate(date) {
    const el = document.getElementById('logOutput');
    if (!el) return;
    const filterEl = document.getElementById('logFilter');
    const filter = filterEl ? filterEl.value.trim() : '';
    try {
        const url = `/api/logs/raw?date=${date}${filter ? '&filter=' + encodeURIComponent(filter) : ''}`;
        const res = await fetch(url);
        const data = await res.json();
        const lines = (data.text || '').split('\n').filter(l => l.trim());
        if (!lines.length) {
            el.innerHTML = '<span class="empty">No logs for this date</span>';
            return;
        }
        el.innerHTML = lines.map(l => formatLogLine(l)).join('');
        el.scrollTop = el.scrollHeight;
    } catch (e) {
        el.innerHTML = `<span class="empty">Error: ${esc(e.message)}</span>`;
    }
}

async function loadLogs() {
    // Legacy compat — now starts the stream
    connectLogStream();
}

async function loadProtocolRuns() {
    const el = document.getElementById('protocolRuns');
    if (!el) return;

    try {
        const res = await fetch('/api/system/protocol-runs');
        const data = await res.json();
        const runs = data.runs || data.protocol_runs || [];

        if (!runs.length) {
            el.innerHTML = '<span class="empty">No protocol runs yet</span>';
            return;
        }

        el.innerHTML = `<table class="data-table">
            <thead><tr>
                <th>Protocol</th><th>Status</th><th>Duration</th><th>Triggered By</th><th>Started At</th>
            </tr></thead>
            <tbody>${runs.map(r => {
                const statusClass = r.status === 'success' || r.status === 'completed' ? 'success' : r.status === 'failed' ? 'fail' : '';
                const started = r.started_at ? formatDate(r.started_at) : '';
                return `<tr>
                    <td>${esc(r.protocol || r.name || '')}</td>
                    <td><span class="status-badge ${r.status === 'success' || r.status === 'completed' ? 'online' : r.status === 'failed' ? 'offline' : 'idle'}" style="font-size:0.68rem;">${esc(r.status || '')}</span></td>
                    <td>${r.duration ? esc(String(r.duration)) : r.duration_ms ? r.duration_ms + 'ms' : ''}</td>
                    <td>${esc(r.triggered_by || '')}</td>
                    <td class="date-dim">${started}</td>
                </tr>`;
            }).join('')}</tbody>
        </table>`;
    } catch (e) {
        el.innerHTML = `<span class="empty">Error: ${esc(e.message)}</span>`;
    }
}

// =============================================================================
// Ops Runbooks — structured command sequences for deploy/maintenance workflows
// =============================================================================

async function loadRunbooks() {
    const el = document.getElementById('runbooksList');
    if (!el) return;
    try {
        const res = await fetch('/api/runbooks');
        const d = await res.json();
        const runbooks = d.runbooks || [];
        if (!runbooks.length) {
            el.innerHTML = '<span class="empty">No runbooks configured</span>';
            return;
        }
        el.innerHTML = runbooks.map(rb => {
            const catColor = {
                stuart: 'var(--peace)',
                atlas: 'var(--clarity)',
                vps: 'var(--momentum)',
                general: 'var(--trust)',
            }[rb.category] || 'var(--trust)';
            return `<div class="runbook-card" onclick="loadRunbookDetail('${ESC(rb.slug)}')" style="cursor:pointer;">
                <div class="runbook-card-row">
                    <span class="runbook-num">${rb.order || ''}</span>
                    <span class="runbook-cat" style="color:${catColor};">${ESC(rb.category)}</span>
                    <span class="runbook-name">${ESC(rb.name)}</span>
                    <span class="runbook-steps">${rb.step_count} steps</span>
                </div>
                ${rb.description ? `<div class="runbook-desc">${ESC(rb.description)}</div>` : ''}
            </div>`;
        }).join('');
    } catch (e) {
        el.innerHTML = `<span class="empty">Error: ${esc(e.message)}</span>`;
    }
}

async function loadRunbookDetail(slug) {
    const el = document.getElementById('runbooksList');
    if (!el) return;
    try {
        const res = await fetch(`/api/runbooks/${slug}`);
        const rb = await res.json();
        if (rb.error) {
            el.innerHTML = `<span class="empty">${ESC(rb.error)}</span>`;
            return;
        }

        const steps = rb.steps || [];
        const catColor = {
            stuart: 'var(--peace)',
            atlas: 'var(--clarity)',
            vps: 'var(--momentum)',
            general: 'var(--trust)',
        }[rb.category] || 'var(--trust)';

        let html = `<div class="runbook-detail">
            <div class="runbook-detail-header">
                <button class="btn btn-sm" onclick="loadRunbooks()">← All Runbooks</button>
                <span class="runbook-name" style="font-size:1rem;">${ESC(rb.name)}</span>
                <span class="runbook-cat" style="color:${catColor};">${ESC(rb.category || '')}</span>
            </div>
            ${rb.description ? `<div class="runbook-desc" style="margin:6px 0 10px;">${ESC(rb.description)}</div>` : ''}
            <div class="runbook-steps">`;

        steps.forEach((step, i) => {
            const typeLabel = {
                'run': 'Run',
                'run-and-return': 'Run & Paste Back',
                'dynamic': 'Dynamic',
                'conditional': 'Conditional',
                'note': 'Note',
            }[step.type] || step.type || 'Run';

            const typeColor = {
                'run': 'var(--trust)',
                'run-and-return': 'var(--momentum)',
                'dynamic': 'var(--courage)',
                'conditional': 'var(--clarity)',
                'note': 'var(--silver)',
            }[step.type] || 'var(--trust)';

            html += `<div class="runbook-step">
                <div class="runbook-step-header">
                    <span class="runbook-step-num">${i + 1}</span>
                    <span class="runbook-step-label">${ESC(step.label || '')}</span>
                    <span class="runbook-step-type" style="color:${typeColor};">${ESC(typeLabel)}</span>
                </div>`;

            if (step.command) {
                const cmdId = `rb-cmd-${slug}-${i}`;
                html += `<div class="runbook-cmd-wrap">
                    <pre class="runbook-cmd" id="${cmdId}">${ESC(step.command)}</pre>
                    <button class="btn btn-sm runbook-copy" onclick="rbCopy('${cmdId}')" title="Copy command">Copy</button>
                </div>`;
            }

            if (step.note) {
                html += `<div class="runbook-note">${ESC(step.note)}</div>`;
            }

            if (step.branches) {
                html += '<div class="runbook-branches">';
                for (const [cond, sub] of Object.entries(step.branches)) {
                    html += `<div class="runbook-branch">
                        <span class="runbook-branch-if">If ${ESC(cond)}:</span>
                        <span class="runbook-branch-then">${ESC(sub)}</span>
                    </div>`;
                }
                html += '</div>';
            }

            html += '</div>';
        });

        html += '</div></div>';

        if (rb.updated_at) {
            const d = new Date(rb.updated_at);
            html += `<div class="runbook-updated">Last updated: ${formatDate(rb.updated_at)}</div>`;
        }

        el.innerHTML = html;
    } catch (e) {
        el.innerHTML = `<span class="empty">Error: ${esc(e.message)}</span>`;
    }
}

function rbCopy(id) {
    const el = document.getElementById(id);
    if (!el) return;
    navigator.clipboard.writeText(el.textContent).then(() => {
        const btn = el.parentElement.querySelector('.runbook-copy');
        if (btn) { btn.textContent = 'Copied!'; setTimeout(() => btn.textContent = 'Copy', 1500); }
    }).catch(() => {});
}

// =============================================================================
// Utility
