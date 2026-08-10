// files.js — Nextcloud WebDAV file browser + Download pack (HTTPS bulk pull)

let currentFilePath = '/';
const PACK_DEFAULT = 'AgentSkills/Content/video/shorts';
let _packState = {
  path: PACK_DEFAULT,
  items: [],
  selected: new Set(),
  excludePreview: true,
  busy: false,
};

async function loadFiles(path) {
  currentFilePath = path || '/';
  const container = document.getElementById('file-list');
  if (!container) return;
  container.innerHTML = '<div class="loading">Loading files...</div>';
  updateBreadcrumb(currentFilePath);

  // Show "Open Cloud" button — works in both single mode (Cove tier) and multi mode (has_cloud)
  const p = MC.presence;
  const toolbar = document.getElementById('file-toolbar');
  const hasCloud = (p && p.has_cloud) || MC.config?.nextcloud_public_url;
  if (toolbar && hasCloud && !document.getElementById('open-cloud-btn')) {
    const btn = document.createElement('a');
    btn.id = 'open-cloud-btn';
    btn.href = MC.config?.nextcloud_public_url || '#';
    btn.target = '_blank';
    btn.className = 'btn-sm';
    btn.style.cssText = 'text-decoration:none;font-size:0.75rem;margin-right:4px;';
    btn.textContent = 'Open Cloud';
    toolbar.prepend(btn);
  }

  try {
    const data = await fetch(`/api/files/list?path=${encodeURIComponent(currentFilePath)}`).then(r => r.json());

    if (data.error) {
      container.innerHTML = `<div class="error-msg">Files error: ${ESC(data.error)}</div>`;
      return;
    }

    if (!data.items || data.items.length === 0) {
      container.innerHTML = '<div class="empty-msg">Empty folder.</div>';
      return;
    }

    let html = '';
    data.items.forEach(item => {
      const icon = item.is_dir ? '📁' : fileIcon(item.name);
      const size = item.is_dir ? '' : formatSize(item.size);
      // Prefer API path (decoded href) — concat of current+name double-nests share mounts
      const target = (item.path && String(item.path).replace(/^\/+/, '')) ||
        `${String(currentFilePath || '/').replace(/\/$/, '')}/${item.name}`.replace(/^\/+/, '');
      const targetJs = target.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
      html += `<div class="file-row ${item.is_dir ? 'file-dir' : 'file-file'}"
        onclick="${item.is_dir ? `loadFiles('${targetJs}')` : `downloadFile('${targetJs}')`}">
        <span class="file-icon">${icon}</span>
        <span class="file-name">${ESC(item.name)}</span>
        ${size ? `<span class="file-size">${size}</span>` : ''}
      </div>`;
    });

    container.innerHTML = html;
  } catch (err) {
    container.innerHTML = `<div class="error-msg">Could not load files: ${ESC(err.message)}</div>`;
  }
}

function updateBreadcrumb(path) {
  const bc = document.getElementById('file-breadcrumb');
  if (!bc) return;
  const parts = path.replace(/^\//, '').split('/').filter(Boolean);
  let html = `<span class="breadcrumb-item" onclick="loadFiles('/')">Home</span>`;
  let built = '';
  parts.forEach((p, i) => {
    built += '/' + p;
    const isLast = i === parts.length - 1;
    const captured = built;
    html += ` / <span class="breadcrumb-item ${isLast ? 'active' : ''}"
      ${isLast ? '' : `onclick="loadFiles('${captured}')"`}>${ESC(p)}</span>`;
  });
  bc.innerHTML = html;
}

function fileIcon(name) {
  const ext = name.split('.').pop().toLowerCase();
  const map = {
    pdf: '📄', doc: '📝', docx: '📝', txt: '📃', md: '📃',
    jpg: '🖼', jpeg: '🖼', png: '🖼', gif: '🖼', webp: '🖼',
    mp3: '🎵', wav: '🎵', flac: '🎵', m4a: '🎵',
    mp4: '🎬', mov: '🎬', avi: '🎬',
    zip: '🗜', tar: '🗜', gz: '🗜',
    py: '🐍', js: '⚡', html: '🌐', css: '🎨', json: '📋',
    xls: '📊', xlsx: '📊', csv: '📊',
    ppt: '📊', pptx: '📊',
  };
  return map[ext] || '📄';
}

function formatSize(bytes) {
  if (!bytes) return '';
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  if (bytes < 1024 * 1024 * 1024) return (bytes / 1024 / 1024).toFixed(1) + ' MB';
  return (bytes / 1024 / 1024 / 1024).toFixed(1) + ' GB';
}

function downloadFile(path) {
  // Same-origin session cookie; streaming download (no full buffer on server).
  window.location.href = `/api/files/download?path=${encodeURIComponent(path)}`;
}

// ── Download pack (HTTPS bulk, presence-scoped) ───────────────────────────

function openDownloadPack() {
  const panel = document.getElementById('download-pack-panel');
  if (!panel) return;
  // Prefer current folder if already under video/shorts; else default shorts.
  const cur = String(currentFilePath || '').replace(/^\/+/, '');
  if (cur && /Content\/video/i.test(cur)) {
    _packState.path = cur;
  } else if (!_packState.path) {
    _packState.path = PACK_DEFAULT;
  }
  panel.classList.remove('hidden');
  renderPackShell();
  loadDownloadPack();
}

function closeDownloadPack() {
  const panel = document.getElementById('download-pack-panel');
  if (panel) panel.classList.add('hidden');
}

function renderPackShell() {
  const panel = document.getElementById('download-pack-panel');
  if (!panel) return;
  const dirPicker = typeof window.showDirectoryPicker === 'function';
  panel.innerHTML = `
    <div class="download-pack-card">
      <div class="download-pack-head">
        <strong>Download pack</strong>
        <button type="button" class="btn-sm" onclick="closeDownloadPack()">Close</button>
      </div>
      <p class="download-pack-help">
        Pull files over the same HTTPS path Mission Control already uses — no SSH, works off-site and on VPS Coves.
        Previews are skipped by default. Captioned and full masters stay selected.
      </p>
      <div class="download-pack-row">
        <label class="download-pack-label">Folder</label>
        <input id="pack-path" class="form-input" type="text" value="${ESC(_packState.path)}" />
        <button type="button" class="btn-sm" onclick="packUseShorts()">Shorts</button>
        <button type="button" class="btn-sm" onclick="packUseCurrent()">This folder</button>
        <button type="button" class="btn-primary btn-sm" onclick="loadDownloadPack()">List</button>
      </div>
      <div class="download-pack-row download-pack-opts">
        <label><input type="checkbox" id="pack-exclude-preview" ${_packState.excludePreview ? 'checked' : ''} onchange="_packState.excludePreview=this.checked"> Skip previews</label>
        <input id="pack-filter" class="form-input" type="search" placeholder="Filter name…" oninput="renderPackList()" />
      </div>
      <div id="pack-status" class="download-pack-status"></div>
      <div id="pack-list" class="download-pack-list"></div>
      <div class="download-pack-actions">
        <button type="button" class="btn-sm" onclick="packSelectAll(true)">Select all</button>
        <button type="button" class="btn-sm" onclick="packSelectAll(false)">Select none</button>
        <button type="button" class="btn-primary btn-sm" id="pack-dl-seq" onclick="packDownloadSequential()">Download selected</button>
        ${dirPicker ? '<button type="button" class="btn-primary btn-sm" id="pack-dl-dir" onclick="packDownloadToFolder()">Save to folder…</button>' : ''}
        <button type="button" class="btn-sm" id="pack-dl-zip" onclick="packDownloadZip()">Zip selected</button>
      </div>
      <p class="download-pack-fine">
        <strong>Download selected</strong> streams one file at a time (best for multi‑GB).
        ${dirPicker ? '<strong>Save to folder</strong> uses the browser folder picker and writes each file there (Chrome/Edge). ' : ''}
        <strong>Zip</strong> is for smaller batches only (not a 30 GB archive).
      </p>
      <div id="pack-progress" class="download-pack-progress"></div>
    </div>`;
}

function packUseShorts() {
  _packState.path = PACK_DEFAULT;
  const el = document.getElementById('pack-path');
  if (el) el.value = PACK_DEFAULT;
  loadDownloadPack();
}

function packUseCurrent() {
  const cur = String(currentFilePath || '/').replace(/^\/+/, '') || '/';
  _packState.path = cur === '/' ? PACK_DEFAULT : cur;
  const el = document.getElementById('pack-path');
  if (el) el.value = _packState.path;
  loadDownloadPack();
}

async function loadDownloadPack() {
  const pathEl = document.getElementById('pack-path');
  const status = document.getElementById('pack-status');
  const list = document.getElementById('pack-list');
  if (pathEl) _packState.path = pathEl.value.trim() || PACK_DEFAULT;
  const ex = document.getElementById('pack-exclude-preview');
  if (ex) _packState.excludePreview = !!ex.checked;
  if (status) status.textContent = 'Loading pack…';
  if (list) list.innerHTML = '';
  try {
    const url = `/api/files/pack?path=${encodeURIComponent(_packState.path)}&exclude_preview=${_packState.excludePreview ? 'true' : 'false'}`;
    const data = await fetch(url).then(r => r.json());
    if (data.error) {
      if (status) status.textContent = data.error;
      _packState.items = [];
      return;
    }
    _packState.items = data.items || [];
    _packState.selected = new Set(_packState.items.map(it => it.path || `${data.path}/${it.name}`));
    if (status) {
      status.textContent = `${data.count || 0} files · ${data.total_label || formatSize(data.total_bytes || 0)} (previews ${data.exclude_preview ? 'excluded' : 'included'})`;
    }
    renderPackList();
  } catch (err) {
    if (status) status.textContent = err.message || String(err);
  }
}

function renderPackList() {
  const list = document.getElementById('pack-list');
  if (!list) return;
  const filter = (document.getElementById('pack-filter')?.value || '').trim().toLowerCase();
  const items = _packState.items.filter(it => {
    if (!filter) return true;
    return String(it.name || '').toLowerCase().includes(filter)
      || String(it.path || '').toLowerCase().includes(filter);
  });
  if (!items.length) {
    list.innerHTML = '<div class="empty-msg">No files in this pack.</div>';
    return;
  }
  list.innerHTML = items.map(it => {
    const path = it.path || `${_packState.path}/${it.name}`;
    const checked = _packState.selected.has(path) ? 'checked' : '';
    const pathJs = path.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
    return `<label class="download-pack-item">
      <input type="checkbox" ${checked} onchange="packToggle('${pathJs}', this.checked)">
      <span class="file-name">${ESC(it.name)}</span>
      <span class="file-size">${formatSize(it.size)}</span>
    </label>`;
  }).join('');
}

function packToggle(path, on) {
  if (on) _packState.selected.add(path);
  else _packState.selected.delete(path);
}

function packSelectAll(on) {
  const filter = (document.getElementById('pack-filter')?.value || '').trim().toLowerCase();
  _packState.items.forEach(it => {
    const path = it.path || `${_packState.path}/${it.name}`;
    if (filter && !String(it.name || '').toLowerCase().includes(filter)
        && !String(it.path || '').toLowerCase().includes(filter)) return;
    if (on) _packState.selected.add(path);
    else _packState.selected.delete(path);
  });
  renderPackList();
}

function packSelectedPaths() {
  return Array.from(_packState.selected);
}

function packSetProgress(msg) {
  const el = document.getElementById('pack-progress');
  if (el) el.textContent = msg || '';
}

async function packDownloadSequential() {
  const paths = packSelectedPaths();
  if (!paths.length) {
    packSetProgress('Select at least one file.');
    return;
  }
  if (_packState.busy) return;
  _packState.busy = true;
  try {
    for (let i = 0; i < paths.length; i++) {
      const path = paths[i];
      const name = path.split('/').pop();
      packSetProgress(`Downloading ${i + 1}/${paths.length}: ${name}`);
      // Navigate-style download keeps cookies; sequential avoids browser parallel choke.
      await packFetchToDisk(path, name);
    }
    packSetProgress(`Done — ${paths.length} file(s).`);
  } catch (err) {
    packSetProgress(err.message || String(err));
  } finally {
    _packState.busy = false;
  }
}

async function packFetchToDisk(path, filename) {
  // Do NOT res.blob() multi-GB files into JS memory. Hand the browser a
  // same-origin URL so it streams to the downloads folder like a normal file.
  const a = document.createElement('a');
  a.href = `/api/files/download?path=${encodeURIComponent(path)}`;
  a.download = filename || 'download';
  a.rel = 'noopener';
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Let the browser queue the next download without stacking too hard.
  await new Promise(r => setTimeout(r, 900));
}

async function packDownloadToFolder() {
  if (typeof window.showDirectoryPicker !== 'function') {
    packSetProgress('Folder picker not supported in this browser — use Download selected or Chrome/Edge.');
    return;
  }
  const paths = packSelectedPaths();
  if (!paths.length) {
    packSetProgress('Select at least one file.');
    return;
  }
  if (_packState.busy) return;
  let dir;
  try {
    dir = await window.showDirectoryPicker({ mode: 'readwrite' });
  } catch (err) {
    if (err && err.name === 'AbortError') return;
    packSetProgress(err.message || String(err));
    return;
  }
  _packState.busy = true;
  try {
    for (let i = 0; i < paths.length; i++) {
      const path = paths[i];
      const name = path.split('/').pop();
      packSetProgress(`Saving ${i + 1}/${paths.length}: ${name}`);
      const res = await fetch(`/api/files/download?path=${encodeURIComponent(path)}`, {
        credentials: 'same-origin',
      });
      if (!res.ok) {
        let detail = '';
        try { detail = (await res.json()).error || ''; } catch (_) {}
        throw new Error(detail || `Failed ${name} (${res.status})`);
      }
      const handle = await dir.getFileHandle(name, { create: true });
      const writable = await handle.createWritable();
      if (res.body && writable.write && typeof res.body.getReader === 'function') {
        const reader = res.body.getReader();
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          await writable.write(value);
        }
      } else {
        await writable.write(await res.blob());
      }
      await writable.close();
    }
    packSetProgress(`Saved ${paths.length} file(s) to the folder you chose.`);
  } catch (err) {
    packSetProgress(err.message || String(err));
  } finally {
    _packState.busy = false;
  }
}

async function packDownloadZip() {
  const paths = packSelectedPaths();
  if (!paths.length) {
    packSetProgress('Select at least one file.');
    return;
  }
  // Guard multi-GB zip in the client
  let total = 0;
  _packState.items.forEach(it => {
    const p = it.path || `${_packState.path}/${it.name}`;
    if (_packState.selected.has(p)) total += Number(it.size || 0);
  });
  if (total > 2 * 1024 * 1024 * 1024) {
    packSetProgress('Selection is over 2 GB — use Download selected or Save to folder instead of zip.');
    return;
  }
  if (_packState.busy) return;
  _packState.busy = true;
  packSetProgress(`Building zip of ${paths.length} file(s)…`);
  try {
    const res = await fetch('/api/files/pack/zip', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paths, exclude_preview: _packState.excludePreview }),
    });
    if (!res.ok) {
      let detail = '';
      try { detail = (await res.json()).error || ''; } catch (_) {}
      throw new Error(detail || `Zip failed (${res.status})`);
    }
    const blob = await res.blob();
    const a = document.createElement('a');
    const url = URL.createObjectURL(blob);
    a.href = url;
    a.download = 'cove-download-pack.zip';
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    packSetProgress('Zip download started.');
  } catch (err) {
    packSetProgress(err.message || String(err));
  } finally {
    _packState.busy = false;
  }
}
