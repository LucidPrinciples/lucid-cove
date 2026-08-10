// display-prefs.js — MC Display prefs (text size / font / contrast)
// Dark LP palette + frequency colors stay locked. Values apply via
// html[data-*] + CSS variables (see dashboard.css). localStorage for instant
// paint; server prefs for cross-device when signed in.

(function () {
  const STORAGE_KEY = 'mc.displayPrefs';
  const DEFAULTS = {
    text_size: 'md',
    font: 'mono',
    contrast: 'standard',
  };
  const TEXT_SIZES = ['sm', 'md', 'lg', 'xl'];
  const FONTS = ['mono', 'sans', 'serif'];
  const CONTRASTS = ['standard', 'high'];

  function _normalize(raw) {
    const out = Object.assign({}, DEFAULTS);
    if (!raw || typeof raw !== 'object') return out;
    if (TEXT_SIZES.indexOf(raw.text_size) >= 0) out.text_size = raw.text_size;
    if (FONTS.indexOf(raw.font) >= 0) out.font = raw.font;
    if (CONTRASTS.indexOf(raw.contrast) >= 0) out.contrast = raw.contrast;
    return out;
  }

  function readLocal() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return Object.assign({}, DEFAULTS);
      return _normalize(JSON.parse(raw));
    } catch (e) {
      return Object.assign({}, DEFAULTS);
    }
  }

  function writeLocal(prefs) {
    const p = _normalize(prefs);
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(p));
    } catch (e) { /* private mode */ }
    return p;
  }

  function applyDisplayPrefs(prefs) {
    const p = _normalize(prefs);
    const h = document.documentElement;
    h.setAttribute('data-text-size', p.text_size);
    h.setAttribute('data-font', p.font);
    h.setAttribute('data-contrast', p.contrast);
    if (typeof MC !== 'undefined') MC.displayPrefs = p;
    return p;
  }

  async function fetchServerPrefs() {
    try {
      const res = await fetch('/api/settings/display');
      if (!res.ok) return null;
      const data = await res.json();
      if (data && data.display) return _normalize(data.display);
    } catch (e) { /* offline / single without endpoint yet */ }
    return null;
  }

  async function saveServerPrefs(prefs) {
    try {
      const res = await fetch('/api/settings/display', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(prefs),
      });
      if (!res.ok) return null;
      const data = await res.json();
      if (data && data.display) return _normalize(data.display);
    } catch (e) {
      return null;
    }
    return null;
  }

  /** Boot: local first (already applied inline), then merge server if present. */
  async function syncDisplayPrefsFromServer() {
    const local = readLocal();
    applyDisplayPrefs(local);
    const remote = await fetchServerPrefs();
    if (!remote) return local;
    // Server wins when it has non-default values or any saved shape
    const merged = _normalize(remote);
    writeLocal(merged);
    return applyDisplayPrefs(merged);
  }

  async function setDisplayPrefs(partial) {
    const next = _normalize(Object.assign({}, readLocal(), partial || {}));
    writeLocal(next);
    applyDisplayPrefs(next);
    const saved = await saveServerPrefs(next);
    if (saved) {
      writeLocal(saved);
      applyDisplayPrefs(saved);
      return saved;
    }
    return next;
  }

  function _chipRow(name, options, labels, current, onPick) {
    return (
      '<div class="disp-chip-row" role="group" aria-label="' + name + '">' +
      options.map(function (opt, i) {
        const lab = labels[i] || opt;
        const on = opt === current ? ' on' : '';
        return (
          '<button type="button" class="disp-chip' + on + '" data-val="' + opt + '"' +
          ' onclick="' + onPick + '(\'' + opt + '\')">' + lab + '</button>'
        );
      }).join('') +
      '</div>'
    );
  }

  async function loadSettingsDisplay() {
    const el = document.getElementById('settings-display');
    if (!el) return;
    let prefs = readLocal();
    applyDisplayPrefs(prefs);
    // Refresh from server in background shape for first paint of section
    const remote = await fetchServerPrefs();
    if (remote) {
      prefs = remote;
      writeLocal(prefs);
      applyDisplayPrefs(prefs);
    }

    el.innerHTML =
      '<div style="font-size:0.7rem;color:var(--dim);margin:0 0 10px;line-height:1.45;">' +
      'Dark theme and frequency colors stay fixed. Adjust text size, typeface, and contrast for long sessions.' +
      '</div>' +
      '<div class="settings-edit-row" style="flex-direction:column;align-items:stretch;gap:6px;">' +
      '<label class="settings-label">Text size</label>' +
      _chipRow('Text size', TEXT_SIZES, ['S', 'M', 'L', 'XL'], prefs.text_size, 'setDisplayTextSize') +
      '</div>' +
      '<div class="settings-edit-row" style="flex-direction:column;align-items:stretch;gap:6px;margin-top:10px;">' +
      '<label class="settings-label">Font</label>' +
      _chipRow('Font', FONTS, ['Mono', 'Sans', 'Serif'], prefs.font, 'setDisplayFont') +
      '</div>' +
      '<div class="settings-edit-row" style="flex-direction:column;align-items:stretch;gap:6px;margin-top:10px;">' +
      '<label class="settings-label">Contrast</label>' +
      _chipRow('Contrast', CONTRASTS, ['Standard', 'High'], prefs.contrast, 'setDisplayContrast') +
      '</div>' +
      '<div id="display-prefs-status" style="font-size:0.68rem;color:var(--dim);margin-top:10px;line-height:1.4;">' +
      'Changes apply immediately. Saved on this device' +
      (typeof MC !== 'undefined' && MC.presence ? ' and your account.' : '.') +
      '</div>';
  }

  async function _updateField(field, value) {
    const status = document.getElementById('display-prefs-status');
    if (status) status.textContent = 'Saving…';
    const partial = {};
    partial[field] = value;
    const next = await setDisplayPrefs(partial);
    // Re-render chips so .on state matches
    await loadSettingsDisplay();
    const st = document.getElementById('display-prefs-status');
    if (st) {
      st.textContent = 'Saved — ' + next.text_size + ' · ' + next.font + ' · ' + next.contrast;
      st.style.color = 'var(--green, #20b2aa)';
    }
    return next;
  }

  window.applyDisplayPrefs = applyDisplayPrefs;
  window.syncDisplayPrefsFromServer = syncDisplayPrefsFromServer;
  window.setDisplayPrefs = setDisplayPrefs;
  window.loadSettingsDisplay = loadSettingsDisplay;
  window.setDisplayTextSize = function (v) { return _updateField('text_size', v); };
  window.setDisplayFont = function (v) { return _updateField('font', v); };
  window.setDisplayContrast = function (v) { return _updateField('contrast', v); };

  // Chip styles (once)
  if (!document.getElementById('display-prefs-chip-style')) {
    const st = document.createElement('style');
    st.id = 'display-prefs-chip-style';
    st.textContent =
      '.disp-chip-row{display:flex;flex-wrap:wrap;gap:6px;}' +
      '.disp-chip{background:var(--card2);border:1px solid var(--border);color:var(--dim);' +
      'border-radius:6px;padding:6px 12px;font-size:0.72rem;font-weight:600;font-family:inherit;' +
      'cursor:pointer;transition:all .15s;}' +
      '.disp-chip:hover{color:var(--text);border-color:var(--accent);}' +
      '.disp-chip.on{background:var(--accent);color:var(--bg);border-color:var(--accent);}';
    document.head.appendChild(st);
  }

  // Apply local immediately if inline head script missed (e.g. cached HTML)
  applyDisplayPrefs(readLocal());
})();
