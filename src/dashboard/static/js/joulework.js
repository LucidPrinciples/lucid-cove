// joulework.js — LLM usage metrics for Atlas

async function loadJouleWork() {
  const container = document.getElementById('jw-content');
  container.innerHTML = '<div class="loading">Loading metrics...</div>';

  try {
    const data = await fetch('/api/system/jw-metrics').then(r => r.json());
    const metrics = data.summary || data;
    const feed = data.recent || [];

    let html = `<div class="jw-stats">
      <div class="jw-stat">
        <div class="jw-stat-val">${metrics.cost_today != null ? '$' + Number(metrics.cost_today).toFixed(2) : '—'}</div>
        <div class="jw-stat-label">Cost Today</div>
      </div>
      <div class="jw-stat">
        <div class="jw-stat-val">${metrics.calls_today != null ? metrics.calls_today : '—'}</div>
        <div class="jw-stat-label">Calls Today</div>
      </div>
      <div class="jw-stat">
        <div class="jw-stat-val">${metrics.tokens_today != null ? formatTokens(metrics.tokens_today) : '—'}</div>
        <div class="jw-stat-label">Tokens Today</div>
      </div>
      <div class="jw-stat">
        <div class="jw-stat-val">${metrics.success_rate != null ? metrics.success_rate + '%' : '—'}</div>
        <div class="jw-stat-label">Success Rate</div>
      </div>
    </div>
    <div id="jw-spend"></div>`;

    if (!feed.length) {
      html += '<div class="empty-msg">No activity yet.</div>';
    } else {
      html += '<div class="jw-feed">';
      feed.slice(0, 50).forEach(e => {
        const time = (e.recorded_at || e.created_at)
          ? formatTime(e.recorded_at || e.created_at)
          : '';
        const ok = e.succeeded !== false;
        const tokens = e.tokens_total != null ? e.tokens_total : ((e.tokens_in || 0) + (e.tokens_out || 0));

        const cost = e.cost_usd != null ? '$' + Number(e.cost_usd).toFixed(4) : '';
        html += `<div class="jw-row ${ok ? '' : 'jw-fail'}">
          <span class="jw-time">${ESC(time)}</span>
          <span class="jw-op">${ESC(e.operation_label || e.operation_type || '')}</span>
          <span class="jw-model">${ESC(e.model_used || '')}</span>
          <span class="jw-tokens">${tokens ? tokens.toLocaleString() + ' tok' : ''}</span>
          <span class="jw-cost">${cost}</span>
          <span class="jw-dur">${e.duration_ms != null ? e.duration_ms + 'ms' : ''}</span>
          <span class="jw-status">${ok ? 'OK' : 'FAIL'}</span>
        </div>`;
      });
      html += '</div>';
    }

    container.innerHTML = html;
    loadJWSpend();
  } catch (err) {
    container.innerHTML = `<div class="error-msg">Could not load metrics: ${ESC(err.message)}</div>`;
  }
}

// #183 — 7-day spend rollup by flow, under the today stats.
async function loadJWSpend() {
  const host = document.getElementById('jw-spend');
  if (!host) return;
  try {
    const r = await fetch('/api/cost/report?days=7&group_by=flow').then(x => x.json());
    if (!r || r.error || !r.rows || !r.rows.length) return;
    let html = `<div class="jw-spend-head">Spend · last 7 days · <strong>$${Number(r.total_cost_usd).toFixed(2)}</strong></div>`;
    html += '<div class="jw-spend-rows">';
    r.rows.slice(0, 8).forEach(row => {
      html += `<div class="jw-spend-row">
        <span class="jw-spend-flow">${ESC(row.bucket || '—')}</span>
        <span class="jw-spend-calls">${row.calls} calls</span>
        <span class="jw-spend-cost">$${Number(row.cost_usd).toFixed(2)}</span>
      </div>`;
    });
    html += '</div>';
    host.innerHTML = html;
  } catch (e) { /* spend unavailable — leave blank */ }
}

function formatTokens(n) {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K';
  return String(n);
}
