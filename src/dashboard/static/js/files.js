// files.js — Nextcloud WebDAV file browser + Download pack (HTTPS bulk pull)

let currentFilePath = '/';
const PACK_DEFAULT = 'AgentSkills/Content/video/shorts';
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
// Product process for every Cove: list newest→oldest, track last pull, stream
// multi-GB media direct from Cloud (not hairpinned through Mission Control).

let _packState = {
  path: PACK_DEFAULT,
  items: [],
  selected: new Set(),
  excludePreview: true,
  busy: false,
  progressKey: '',
  storageKey: '',
  lastDownloadedPath: '',
  lastModified: '',
  sort: 'newest_first',
};

function packItemPath(it, folder) {
  const base = (folder || _packState.path || '').replace(/^\/+|\/+$/g, '');
  if (it && it.path) return String(it.path).replace(/^\/+/, '');
  return `${base}/${it && it.name ? it.name : ''}`.replace(/^\/+/, '');
}

function packLoadLocalProgress() {
  const key = _packState.storageKey;
  if (!key) return;
  try {
    const raw = localStorage.getItem(key);
    if (!raw) {
      _packState.lastDownloadedPath = '';
      _packState.lastModified = '';
      return;
    }
    const parsed = JSON.parse(raw);
    _packState.lastDownloadedPath = parsed.lastDownloadedPath || parsed.path || '';
    _packState.lastModified = parsed.lastModified || '';
  } catch (e) {
    _packState.lastDownloadedPath = '';
    _packState.lastModified = '';
  }
}

function packSaveLocalProgress(path, modified) {
  const key = _packState.storageKey;
  if (!key || !path) return;
  try {
    localStorage.setItem(key, JSON.stringify({
      lastDownloadedPath: path,
      lastModified: modified || '',
      updatedAt: new Date().toISOString(),
      folder: _packState.path,
    }));
    _packState.lastDownloadedPath = path;
    _packState.lastModified = modified || '';
  } catch (e) { /* quota / private mode */ }
}

function packMarkDoneThrough(path) {
  // Keep watermark at the last successfully started file (resume from below it).
  const it = (_packState.items || []).find(x => packItemPath(x) === path);
  packSaveLocalProgress(path, it && it.modified);
}

function openDownloadPack() {
  const panel = document.getElementById('download-pack-panel');
  if (!panel) return;
  const cur = String(currentFilePath || '').replace(/^\/+/, '');
  if (cur && cur.indexOf('AgentSkills/Content/video') === 0) {
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
        Pull publish-ready video off this Cove over HTTPS — works the same on home hosts and VPS.
        Listed <strong>newest first</strong>. Resume picks up below your last pull. Previews skipped by default.
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
        <button type="button" class="btn-sm" onclick="packSelectNewerThanLast()">Select newer than last pull</button>
        <button type="button" class="btn-primary btn-sm" id="pack-dl-next" onclick="packDownloadDirectCloud(true)">Download next only</button>
        <button type="button" class="btn-sm" id="pack-dl-direct" onclick="packDownloadDirectCloud(false)">Download selected</button>
        ${dirPicker ? '<button type="button" class="btn-sm" id="pack-dl-dir" onclick="packDownloadToFolder()">Save via app…</button>' : ''}
        <button type="button" class="btn-sm" id="pack-dl-proxy" onclick="packDownloadSequential()">Via Mission Control</button>
        <button type="button" class="btn-sm" id="pack-dl-zip" onclick="packDownloadZip()">Zip selected</button>
      </div>
      <p class="download-pack-fine">
        <strong>Download selected</strong> hands files to the browser one-at-a-time via short-lived, read-only Cloud links (no Mission Control byte proxy). Links expire; they are not open access to your whole Cloud. Remote speed needs a public Cloud base (tunnel or VPS HTTPS) — mesh-only hosts stay slow off-site. Keep one active transfer when the link is thin.
        <strong>Via Mission Control</strong> if Cloud links fail or the browser blocks the handoff (usually slower).
        ${dirPicker ? '<strong>Save via app</strong> still hairpins through MC. ' : ''}
        <strong>Zip</strong> is for smaller batches only.
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
    const data = await fetch(url, { credentials: 'same-origin' }).then(r => r.json());
    if (data.error) {
      if (status) status.textContent = data.error;
      _packState.items = [];
      return;
    }
    _packState.items = data.items || [];
    _packState.sort = data.sort || 'newest_first';
    _packState.progressKey = data.progress_key || _packState.path;

    // Resolve durable storage key for this presence + folder
    try {
      const prog = await fetch(
        `/api/files/pack/progress?path=${encodeURIComponent(_packState.path)}`,
        { credentials: 'same-origin' }
      ).then(r => r.json());
      if (prog && prog.storage_key) {
        _packState.storageKey = prog.storage_key;
      } else {
        const pid = (MC.presence && (MC.presence.id || MC.presence.presence_id)) || 'local';
        _packState.storageKey = `cove.packProgress.${pid}.${_packState.progressKey}`;
      }
    } catch (_) {
      const pid = (MC.presence && (MC.presence.id || MC.presence.presence_id)) || 'local';
      _packState.storageKey = `cove.packProgress.${pid}.${_packState.progressKey}`;
    }
    packLoadLocalProgress();

    // Default selection: files newer than last pull (or none if watermark missing → operator chooses)
    _packState.selected = new Set();
    const last = _packState.lastDownloadedPath;
    if (last) {
      let seenLast = false;
      for (const it of _packState.items) {
        const p = packItemPath(it, data.path);
        if (p === last) { seenLast = true; break; }
        _packState.selected.add(p);
      }
      // If watermark not in list (deleted/moved), leave selection empty — safer than all
      if (!seenLast && _packState.selected.size === _packState.items.length) {
        _packState.selected = new Set();
      }
    }

    const newerN = _packState.selected.size;
    const watermark = last
      ? ` · last pull: ${ESC(last.split('/').pop())}${newerN ? ` · ${newerN} newer selected` : ' · caught up'}`
      : ' · no pull mark yet — select what you need';
    if (status) {
      status.textContent = `${data.count || 0} files · newest first · ${data.total_label || formatSize(data.total_bytes || 0)} (previews ${data.exclude_preview ? 'excluded' : 'included'})${last ? ` · last pull: ${last.split('/').pop()}${newerN ? ` · ${newerN} newer selected` : ' · caught up'}` : ' · no pull mark yet — select what you need'}`;
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
    const path = packItemPath(it);
    const checked = _packState.selected.has(path) ? 'checked' : '';
    const pathJs = path.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
    const isLast = path === _packState.lastDownloadedPath;
    const lastCls = isLast ? ' download-pack-last-downloaded' : '';
    const lastLbl = isLast ? '<span class="download-pack-last-label">last pull</span>' : '';
    const mod = it.modified ? `<span class="file-mod" title="${ESC(it.modified)}">${ESC(packFormatMod(it.modified))}</span>` : '';
    return `<label class="download-pack-item${lastCls}">
      <input type="checkbox" ${checked} onchange="packToggle('${pathJs}', this.checked)">
      <span class="file-name">${ESC(it.name)}${lastLbl}</span>
      ${mod}
      <span class="file-size">${formatSize(it.size)}</span>
    </label>`;
  }).join('');
}

function packFormatMod(s) {
  if (!s) return '';
  try {
    const d = new Date(s);
    if (isNaN(d.getTime())) return String(s).slice(0, 16);
    return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  } catch (_) {
    return String(s).slice(0, 16);
  }
}

function packToggle(path, on) {
  if (on) _packState.selected.add(path);
  else _packState.selected.delete(path);
}

function packSelectAll(on) {
  const filter = (document.getElementById('pack-filter')?.value || '').trim().toLowerCase();
  _packState.items.forEach(it => {
    const path = packItemPath(it);
    if (filter && !String(it.name || '').toLowerCase().includes(filter)
        && !String(it.path || '').toLowerCase().includes(filter)) return;
    if (on) _packState.selected.add(path);
    else _packState.selected.delete(path);
  });
  renderPackList();
}

function packSelectNewerThanLast() {
  const last = _packState.lastDownloadedPath;
  _packState.selected = new Set();
  if (!last) {
    packSetProgress('No last-pull mark yet — pick files manually or Select all.');
    renderPackList();
    return;
  }
  for (const it of _packState.items) {
    const p = packItemPath(it);
    if (p === last) break;
    _packState.selected.add(p);
  }
  packSetProgress(`${_packState.selected.size} file(s) newer than last pull selected.`);
  renderPackList();
}

function packSelectedPaths() {
  // Preserve newest-first order from items list
  const sel = _packState.selected;
  const ordered = [];
  for (const it of _packState.items) {
    const p = packItemPath(it);
    if (sel.has(p)) ordered.push(p);
  }
  // Any selected not in items (shouldn't happen)
  sel.forEach(p => { if (!ordered.includes(p)) ordered.push(p); });
  return ordered;
}

function packSetProgress(msg) {
  const el = document.getElementById('pack-progress');
  if (el) el.textContent = msg || '';
}

async function packDownloadDirectCloud(onlyFirst) {
  let paths = packSelectedPaths();
  if (!paths.length) {
    packSetProgress('Select at least one file.');
    return;
  }
  if (onlyFirst) {
    paths = paths.slice(0, 1);
  }
  if (_packState.busy) return;
  _packState.busy = true;
  packSetProgress(
    onlyFirst
      ? `Asking Cloud for 1 download link (next only — keeps the full remote pipe on one file)…`
      : `Asking Cloud for ${paths.length} download link(s), queued one-at-a-time…`
  );
  try {
    const res = await fetch('/api/files/pack/direct-urls', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paths, expire_days: 2 }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.error || `Direct URLs failed (${res.status})`);
    }
    const items = data.items || [];
    const errors = data.errors || [];
    const cloudBase = data.cloud_base || '';
    const egress = data.egress || 'ok';
    if (!items.length) {
      const detail = errors[0] && (errors[0].error || errors[0].detail);
      throw new Error(detail || 'No Cloud links returned — try Via Mission Control.');
    }

    const started = [];
    const failed = [];
    // STRICTLY one file at a time. Parallel multi-GB GETs on remote/mesh egress
    // split a thin uplink into KB/s slices (four at ~8 KB/s each). One stream
    // takes the full pipe; operator resumes the rest via last-pull watermark.
    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      const name = item.name || (item.path || '').split('/').pop() || 'download';
      const url = item.download_url;
      if (!url) {
        failed.push(name);
        continue;
      }
      packSetProgress(
        `Queue ${i + 1}/${items.length}: starting ${name} alone (one-at-a-time so remote links are not split). ` +
        `Watch the download shelf — cancel any older parallel copies of the same file.`
      );
      try {
        await packTriggerBrowserDownload(url, name);
        started.push(name);
        packMarkDoneThrough(item.path);
      } catch (e) {
        failed.push(name);
      }
      // Wait long enough that the browser has bound ONE transfer before the next
      // URL is offered. Still does not wait for multi-GB completion (browser-owned).
      await new Promise(r => setTimeout(r, 4000));
    }

    const names = started.slice(0, 4).join(', ') + (started.length > 4 ? '…' : '');
    let msg = '';
    if (started.length) {
      msg = `Queued ${started.length} Cloud download(s) one-at-a-time: ${names}. `;
      if (cloudBase) msg += `Base ${cloudBase}. `;
      if (egress === 'mesh_base' || egress === 'loopback_base' || egress === 'missing_public_base') {
        msg += 'This Cloud base is not a public tunnel/VPS origin — remote pulls will stay slow until NEXTCLOUD_PUBLIC_URL points at a public hostname. ';
      } else {
        msg += 'Remote speed follows public HTTPS egress (tunnel or VPS), not mesh. ';
      }
      msg += 'One active transfer in the download shelf. Watermark saved for handoff files.';
    } else {
      msg = 'Cloud links were created but the browser did not start any downloads. ';
      msg += 'Allow downloads for this site, or use Via Mission Control.';
    }
    if (failed.length || errors.length) {
      msg += ` (${failed.length + errors.length} file(s) did not start)`;
    }
    packSetProgress(msg);
    renderPackList();
  } catch (err) {
    packSetProgress(err.message || String(err));
  } finally {
    _packState.busy = false;
  }
}

/** Start ONE browser download (single request — never iframe+click double-fetch). */
async function packTriggerBrowserDownload(url, filename) {
  // One hidden iframe only. A second <a click> would open a parallel GET of the
  // same multi-GB object and cut effective speed in half on thin remote pipes.
  const iframe = document.createElement('iframe');
  iframe.style.cssText = 'position:fixed;width:0;height:0;border:0;left:-9999px;top:-9999px;';
  iframe.setAttribute('aria-hidden', 'true');
  iframe.title = filename || 'download';
  iframe.src = url;
  document.body.appendChild(iframe);
  setTimeout(() => {
    try { iframe.remove(); } catch (_) {}
  }, 120000);
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
      packSetProgress(`Via MC ${i + 1}/${paths.length}: ${name} (slow path)`);
      await packFetchToDisk(path, name);
      packMarkDoneThrough(path);
    }
    packSetProgress(`Done — ${paths.length} file(s) via Mission Control. Watermark saved.`);
    renderPackList();
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
      packMarkDoneThrough(path);
    }
    packSetProgress(`Saved ${paths.length} file(s) to the folder you chose. Watermark saved.`);
    renderPackList();
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
    const p = packItemPath(it);
    if (_packState.selected.has(p)) total += Number(it.size || 0);
  });
  if (total > 2 * 1024 * 1024 * 1024) {
    packSetProgress('Selection is over 2 GB — use Download selected (Cloud) instead of zip.');
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
    if (paths.length) packMarkDoneThrough(paths[paths.length - 1]);
    packSetProgress('Zip download started. Watermark updated to last file in selection.');
  } catch (err) {
    packSetProgress(err.message || String(err));
  } finally {
    _packState.busy = false;
  }
}
