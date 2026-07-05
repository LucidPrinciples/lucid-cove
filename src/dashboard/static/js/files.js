// files.js — Nextcloud WebDAV file browser

let currentFilePath = '/';

async function loadFiles(path) {
  currentFilePath = path || '/';
  const container = document.getElementById('file-list');
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
      html += `<div class="file-row ${item.is_dir ? 'file-dir' : 'file-file'}"
        onclick="${item.is_dir ? `loadFiles('${currentFilePath.replace(/\/$/, '')}/${item.name}')` : `downloadFile('${currentFilePath.replace(/\/$/, '')}/${item.name}')`}">
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
  window.open(`/api/files/download?path=${encodeURIComponent(path)}`, '_blank');
}
