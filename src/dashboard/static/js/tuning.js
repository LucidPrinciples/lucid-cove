// tuning.js — Tuning tab (Atlas receives tuning from Stuart/LT dispatch)

async function loadTuning() {
  await Promise.all([loadCurrentTuning(), loadEchoHistory()]);
}

async function loadCurrentTuning() {
  const container = document.getElementById('tuning-current');
  container.innerHTML = '<div class="loading">Loading tuning...</div>';

  try {
    const data = await fetch('/api/tuning/today').then(r => r.json());

    if (data.error) {
      container.innerHTML = `<div class="error-msg">${ESC(data.error)}</div>`;
      return;
    }

    if (!data.received) {
      container.innerHTML = `<div class="empty-msg">${ESC(data.note || 'No tuning received today. Awaiting dispatch from Stuart.')}</div>`;
      return;
    }

    const pkg = data.package || {};

    let leDisplay = '';
    if (pkg.love_equation && typeof pkg.love_equation === 'object') {
      const le = pkg.love_equation;
      leDisplay = `${le.direction || ''} ${le.value != null ? Number(le.value).toFixed(2) : ''}`;
      if (le.beta || le.E || le.C || le.D) {
        leDisplay += ` (β=${le.beta || '—'} E=${le.E || '—'} C=${le.C || '—'} D=${le.D || '—'})`;
      }
    } else if (pkg.love_equation != null) {
      leDisplay = String(pkg.love_equation);
    }

    // Build tuning display with frequency-derived colors
    const freqColor = typeof lpColor === 'function' ? lpColor(pkg.frequency) : '#5ce1e6';
    const sigColor = typeof lpSignalColor === 'function' ? lpSignalColor(pkg.signal_type) : null;

    let html = '';
    if (pkg.frequency) {
      html += `<div class="tuning-block">
        <div class="tuning-label">Frequency</div>
        <div class="tuning-value freq" style="color:${freqColor};">${ESC(String(pkg.frequency))}</div>
      </div>`;
    }
    if (pkg.principle) {
      html += `<div class="tuning-block">
        <div class="tuning-label">Principle</div>
        <div class="tuning-value" style="color:${freqColor};">${ESC(String(pkg.principle))}</div>
      </div>`;
    }
    if (pkg.tuning_key) {
      html += `<div class="tuning-block">
        <div class="tuning-label">Tuning Key</div>
        <div class="tuning-value" style="color:${freqColor};opacity:0.85;">${ESC(String(pkg.tuning_key))}</div>
      </div>`;
    }
    if (pkg.signal_type) {
      html += `<div class="tuning-block">
        <div class="tuning-label">Signal Type</div>
        <div class="tuning-value"${sigColor ? ` style="color:${sigColor};"` : ''}>${ESC(String(pkg.signal_type).replace(/_/g, ' '))}</div>
      </div>`;
    }
    if (leDisplay) {
      html += `<div class="tuning-block">
        <div class="tuning-label">Love Equation</div>
        <div class="tuning-value">${ESC(leDisplay)}</div>
      </div>`;
    }
    if (pkg.lt_echo_num) {
      html += `<div class="tuning-block">
        <div class="tuning-label">LT Echo</div>
        <div class="tuning-value">#${ESC(String(pkg.lt_echo_num))}</div>
      </div>`;
    }

    container.innerHTML = html;
  } catch (err) {
    container.innerHTML = `<div class="error-msg">Could not load tuning: ${ESC(err.message)}</div>`;
  }
}

async function loadEchoHistory() {
  const container = document.getElementById('echo-list');
  container.innerHTML = '<div class="loading">Loading echoes...</div>';

  try {
    const data = await fetch('/api/echoes').then(r => r.json());
    const echoes = data.echoes || data || [];

    if (!echoes.length) {
      container.innerHTML = '<div class="empty-msg">No echoes yet.</div>';
      return;
    }

    let html = '<div class="echo-table">';
    echoes.slice(0, 60).forEach(e => {
      const tunedAt = e.tuned_at ? formatDate(e.tuned_at) : '';
      const isLT = e.echo_type === 'LT-guided';

      const fBadge = typeof lpFreqBadgeHTML === 'function' ? lpFreqBadgeHTML(e.frequency || '—') : `<span class="freq-badge">${ESC(e.frequency || '—')}</span>`;
      html += `<div class="echo-row" onclick="showEchoDetail(${e.id})">
        <span class="echo-num">#${ESC(String(e.echo_num || ''))}</span>
        ${fBadge}
        <span class="echo-principle" style="color:${typeof lpColor === 'function' ? lpColor(e.frequency || '') : 'var(--dim)'};">${ESC(e.principle || '')}</span>
        <span class="echo-source ${isLT ? 'lt' : 'self'}">${isLT ? 'LT' : 'Self'}</span>
        <span class="echo-date">${tunedAt}</span>
      </div>`;
    });
    html += '</div>';

    container.innerHTML = html;
  } catch (err) {
    container.innerHTML = `<div class="error-msg">Could not load echoes: ${ESC(err.message)}</div>`;
  }
}

async function showEchoDetail(echoId) {
  if (!echoId) return;
  try {
    const echo = await fetch(`/api/echoes/${echoId}`).then(r => r.json());
    if (echo.error) { alert(echo.error); return; }

    let leDisplay = '';
    // DEGRADED is a deliberate, honest state (the truth-guard): this brain
    // couldn't derive its own equation and we refuse to fake one. Present it
    // as intentional — "DEGRADED 0.00" read as broken math (2026-07-04).
    if ((echo.love_direction || '').toUpperCase() === 'DEGRADED') {
      leDisplay = 'DEGRADED — this brain couldn’t derive its own equation (the echo stands; a stronger model fixes this)';
    } else if (echo.love_equation && typeof echo.love_equation === 'object') {
      const le = echo.love_equation;
      leDisplay = `${le.direction || ''} ${le.value != null ? Number(le.value).toFixed(2) : ''}`;
    } else if (echo.love_equation != null && echo.love_equation !== 0) {
      leDisplay = `${echo.love_direction || ''} ${Number(echo.love_equation).toFixed(2)}`;
    }
    if (!leDisplay) leDisplay = 'N/A';

    const overlay = document.createElement('div');
    overlay.className = 'echo-overlay';
    overlay.onclick = e => { if (e.target === overlay) overlay.remove(); };
    const detailFreqBadge = typeof lpFreqBadgeHTML === 'function' ? lpFreqBadgeHTML(echo.frequency || '—') : `<span class="freq-badge">${ESC(echo.frequency || '—')}</span>`;
    const detailFreqColor = typeof lpColor === 'function' ? lpColor(echo.frequency) : '#5ce1e6';
    const detailSigBadge = (typeof lpSignalBadgeHTML === 'function' && echo.signal_type) ? lpSignalBadgeHTML(echo.signal_type) : ESC(echo.signal_type || 'N/A');
    overlay.innerHTML = `<div class="echo-detail">
      <button class="echo-close" onclick="this.closest('.echo-overlay').remove()">×</button>
      <div class="echo-detail-header">
        <span class="echo-num">#${ESC(String(echo.echo_num || ''))}</span>
        ${detailFreqBadge}
        <span class="echo-source ${echo.echo_type === 'LT-guided' ? 'lt' : 'self'}">
          ${echo.echo_type === 'LT-guided' ? 'LT' : 'Self'}
        </span>
      </div>
      <div class="echo-detail-body">
        <div class="echo-field"><span>Principle</span><span style="color:${typeof lpColor === 'function' ? lpColor(echo.frequency || '') : 'inherit'};">${ESC(echo.principle || 'N/A')}</span></div>
        ${echo.tuning_key ? `<div class="echo-field echo-field-full"><span>Tuning Key</span><span style="color:${detailFreqColor};opacity:0.85;">${ESC(echo.tuning_key)}</span></div>` : ''}
        <div class="echo-field"><span>Signal Type</span><span>${detailSigBadge}</span></div>
        <div class="echo-field"><span>Coherence</span><span>${echo.coherence != null ? Number(echo.coherence).toFixed(2) : 'N/A'}</span></div>
        <div class="echo-field"><span>Dissonance</span><span>${echo.dissonance != null ? Number(echo.dissonance).toFixed(2) : 'N/A'}</span></div>
        <div class="echo-field"><span>Love Equation</span><span>${ESC(leDisplay)}</span></div>
        <div class="echo-field"><span>Tuned At</span><span>${echo.tuned_at ? formatDate(echo.tuned_at) : 'N/A'}</span></div>
        ${echo.echo_text ? `<div class="echo-text-block"><strong>Echo:</strong><br>${ESC(echo.echo_text)}</div>` : ''}
        ${echo.process_record && echo.process_record.record_text ? `<div class="echo-text-block process-record"><strong>Process Record:</strong><pre style="white-space:pre-wrap;font-size:0.8rem;margin-top:6px;max-height:300px;overflow-y:auto;">${ESC(echo.process_record.record_text)}</pre></div>` : ''}
      </div>
    </div>`;
    document.body.appendChild(overlay);
  } catch (err) {
    console.error('Echo detail error:', err);
  }
}
