// memory.js — Memory management
//
// Two-section layout: Review Queue (pending) + Committed memories.
// Features: review badge, search/filter/sort, inline edit/correct,
// bulk select + recategorize + commit, flagged items review, action buttons.

let _memoryData = { memories: [], stats: {}, flagged: [], reviewStats: {} };
let _bulkMode = false;
let _selectedIds = new Set();

const _CATEGORIES = [
  'instruction','decision','preference','fact','person','project',
  'technical','observation','deployment','architecture','bug_fix',
  'feature','process','general'
];


// ═══════════════════════════════════════════════════════════════════════
// Load & Render
// ═══════════════════════════════════════════════════════════════════════

async function loadMemory() {
  const container = document.getElementById('memory-content');
  container.innerHTML = '<div class="loading">Loading memory...</div>';

  try {
    const viewMode = document.getElementById('memViewMode')?.value || 'review';
    const reviewParam = viewMode === 'review' ? 'pending' : viewMode === 'committed' ? 'committed' : '';
    const url = `/api/memories?limit=500${reviewParam ? '&review_status=' + reviewParam : ''}`;

    const [memRes, statsRes, flaggedRes, reviewRes] = await Promise.all([
      fetch(url).then(r => r.json()),
      fetch('/api/memories/stats').then(r => r.json()),
      fetch('/api/memories/flagged').then(r => r.json()),
      fetch('/api/memories/review-stats').then(r => r.json()),
    ]);

    _memoryData.memories = memRes.memories || [];
    _memoryData.stats = statsRes || {};
    _memoryData.flagged = flaggedRes.memories || [];
    _memoryData.reviewStats = reviewRes || {};

    populateCategoryDropdowns();
    updateReviewBadge();
    renderMemoryDashboard(container);
  } catch (err) {
    container.innerHTML = `<div class="error-msg">Could not load memory: ${ESC(err.message)}</div>`;
  }
}

function updateReviewBadge() {
  const badge = document.getElementById('memReviewBadge');
  const pending = _memoryData.reviewStats.pending || 0;
  if (badge) {
    if (pending > 0) {
      badge.textContent = `${pending} pending`;
      badge.style.display = '';
    } else {
      badge.style.display = 'none';
    }
  }
}

function populateCategoryDropdowns() {
  const cats = new Set(_CATEGORIES);
  _memoryData.memories.forEach(m => { if (m.category) cats.add(m.category); });
  const sorted = [...cats].sort();

  const filterEl = document.getElementById('memCatFilter');
  const bulkEl = document.getElementById('memBulkCat');
  if (filterEl) {
    filterEl.innerHTML = '<option value="">All categories</option>' +
      sorted.map(c => `<option value="${ESC(c)}">${ESC(c)}</option>`).join('');
  }
  if (bulkEl) {
    bulkEl.innerHTML = '<option value="">Move to category...</option>' +
      sorted.map(c => `<option value="${ESC(c)}">${ESC(c)}</option>`).join('');
  }
}

function getFilteredMemories() {
  const query = (document.getElementById('memSearchInput')?.value || '').toLowerCase().trim();
  const catFilter = document.getElementById('memCatFilter')?.value || '';

  let items = _memoryData.memories;
  if (query) {
    items = items.filter(m =>
      (m.content || '').toLowerCase().includes(query) ||
      (m.tags || []).some(t => t.toLowerCase().includes(query)) ||
      (m.source_summary || '').toLowerCase().includes(query)
    );
  }
  if (catFilter) {
    items = items.filter(m => m.category === catFilter);
  }
  return items;
}

function getSortedMemories(items) {
  const sortBy = document.getElementById('memSortBy')?.value || 'date-desc';
  const sorted = [...items];
  switch (sortBy) {
    case 'date-desc':
      sorted.sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0));
      break;
    case 'date-asc':
      sorted.sort((a, b) => new Date(a.created_at || 0) - new Date(b.created_at || 0));
      break;
    case 'importance-desc':
      sorted.sort((a, b) => (b.importance || 0) - (a.importance || 0));
      break;
    case 'importance-asc':
      sorted.sort((a, b) => (a.importance || 0) - (b.importance || 0));
      break;
    case 'category':
      sorted.sort((a, b) => (a.category || 'zzz').localeCompare(b.category || 'zzz'));
      break;
  }
  return sorted;
}

function renderMemoryDashboard(container) {
  const viewMode = document.getElementById('memViewMode')?.value || 'review';
  let html = '';
  html += renderStatsHeader(_memoryData.stats, _memoryData.reviewStats);

  if (_memoryData.flagged.length > 0) {
    html += renderFlaggedSection(_memoryData.flagged);
  }

  const filtered = getFilteredMemories();
  const sorted = getSortedMemories(filtered);

  if (viewMode === 'review') {
    html += renderReviewQueueHeader(sorted.length);
  }

  const sortBy = document.getElementById('memSortBy')?.value || 'date-desc';
  if (sortBy === 'category') {
    html += renderMemoriesByCategory(sorted);
  } else {
    html += renderMemoriesFlat(sorted, viewMode);
  }

  if (viewMode === 'review' && sorted.length > 0) {
    html += `<div style="padding:8px 12px;text-align:center;border-top:1px solid var(--border);">
      <button class="btn-sm btn-action" onclick="commitAllPending()" style="font-size:0.72rem;padding:4px 14px;">
        Commit all ${sorted.length} to long-term memory
      </button>
      <div style="font-size:0.65rem;color:var(--dim);margin-top:3px;">
        Memories auto-commit after 7 days if not dismissed or edited.
      </div>
    </div>`;
  }

  if (sorted.length === 0 && viewMode === 'review') {
    html += `<div class="empty" style="padding:30px;text-align:center;">
      <div style="font-size:0.85rem;margin-bottom:4px;">Review queue is clear</div>
      <div style="font-size:0.72rem;color:var(--dim);">New memories will appear here for review before being committed.</div>
    </div>`;
  }

  container.innerHTML = html;
}

function renderReviewQueueHeader(count) {
  return `<div style="padding:6px 12px;background:var(--bg-alt);border-bottom:1px solid var(--border);">
    <div style="font-size:0.75rem;font-weight:600;">Review Queue <span style="font-weight:normal;color:var(--dim);">${count} pending</span></div>
    <div style="font-size:0.65rem;color:var(--dim);margin-top:2px;">Edit, recategorize, or dismiss before they commit to long-term memory</div>
  </div>`;
}

function filterMemories() {
  // When view mode changes, we need to reload from API with the right filter
  const viewMode = document.getElementById('memViewMode')?.value || 'review';
  const currentData = _memoryData.memories;

  // If we have data, check if we can filter client-side or need to reload
  // Category/search filters work client-side; view mode requires API reload
  if (viewMode !== _lastViewMode) {
    _lastViewMode = viewMode;
    loadMemory();
    return;
  }

  const container = document.getElementById('memory-content');
  if (container) renderMemoryDashboard(container);
}

let _lastViewMode = 'review';

function sortAndRenderMemories() {
  const container = document.getElementById('memory-content');
  if (container) renderMemoryDashboard(container);
}


// ═══════════════════════════════════════════════════════════════════════
// Stats Header
// ═══════════════════════════════════════════════════════════════════════

function renderStatsHeader(stats, reviewStats) {
  const active = stats.active_memories || 0;
  const flagged = stats.flagged_count || 0;
  const pending = reviewStats.pending || 0;
  const committed = reviewStats.committed || 0;
  const cats = stats.by_category || {};

  let catPills = Object.entries(cats)
    .sort((a, b) => b[1] - a[1])
    .map(([cat, count]) => `<span class="memory-stat-pill cat-${ESC(cat)}">${ESC(cat)}: ${count}</span>`)
    .join('');

  return `<div class="memory-stats-bar">
    <div class="memory-stats-row">
      <span class="memory-stat"><strong>${active}</strong> active</span>
      <span class="memory-stat"><strong>${pending}</strong> pending review</span>
      <span class="memory-stat"><strong>${committed}</strong> committed</span>
      ${flagged > 0 ? `<span class="memory-stat flagged-stat"><strong>${flagged}</strong> flagged</span>` : ''}
    </div>
    ${catPills ? `<div class="memory-cat-pills">${catPills}</div>` : ''}
  </div>`;
}


// ═══════════════════════════════════════════════════════════════════════
// Flagged Section
// ═══════════════════════════════════════════════════════════════════════

function renderFlaggedSection(flagged) {
  let items = flagged.map(m => {
    const created = m.created_at
      ? formatDate(m.created_at, { month: 'short', day: 'numeric' })
      : '';
    return `<div class="memory-flagged-item" id="flagged-${m.id}">
      <div class="flagged-header">
        <span class="flagged-badge">REVIEW</span>
        <span class="memory-category cat-${ESC(m.category || 'general')}">${ESC(m.category || 'general')}</span>
        <span class="memory-date">${created}</span>
      </div>
      <div class="flagged-content">${ESC(m.content)}</div>
      ${m.review_reason ? `<div class="flagged-reason">${ESC(m.review_reason)}</div>` : ''}
      <div class="flagged-actions">
        <button class="btn-sm btn-approve" onclick="approveMemory(${m.id})">Keep</button>
        <button class="btn-sm btn-dismiss" onclick="dismissMemory(${m.id})">Dismiss</button>
        <button class="btn-sm btn-history" onclick="showHistory(${m.id})">History</button>
      </div>
    </div>`;
  }).join('');

  return `<div class="memory-section flagged-section">
    <div class="section-header">
      <h3>Flagged <span class="badge-count">${flagged.length}</span></h3>
    </div>
    ${items}
  </div>`;
}


// ═══════════════════════════════════════════════════════════════════════
// Flat Rendering (for date/importance sorts)
// ═══════════════════════════════════════════════════════════════════════

function renderMemoriesFlat(memories, viewMode) {
  if (!memories.length) {
    return '';
  }
  let html = `<div class="memory-section"><div class="section-body">`;
  memories.forEach(m => { html += renderMemoryItem(m, viewMode); });
  html += '</div></div>';
  return html;
}


// ═══════════════════════════════════════════════════════════════════════
// Category Rendering
// ═══════════════════════════════════════════════════════════════════════

function renderMemoriesByCategory(memories) {
  if (!memories.length) {
    return '';
  }
  const groups = {};
  memories.forEach(m => {
    const cat = m.category || 'general';
    if (!groups[cat]) groups[cat] = [];
    groups[cat].push(m);
  });

  const priority = ['instruction','decision','preference','fact','person','project','technical','observation','general'];
  const sortedCats = Object.keys(groups).sort((a, b) => {
    const ai = priority.indexOf(a);
    const bi = priority.indexOf(b);
    return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
  });

  let html = '';
  const viewMode = document.getElementById('memViewMode')?.value || 'review';
  for (const cat of sortedCats) {
    const items = groups[cat];
    const collapsed = items.length > 8;
    const visibleCount = collapsed ? 5 : items.length;

    html += `<div class="memory-section">
      <div class="section-header" onclick="toggleSection(this)">
        <h3>${ESC(cat)} <span class="badge-count">${items.length}</span></h3>
        <span class="toggle-icon">&#9660;</span>
      </div>
      <div class="section-body">`;

    items.forEach((m, idx) => {
      const hidden = collapsed && idx >= visibleCount ? ' style="display:none" data-overflow="true"' : '';
      html += `<div${hidden}>${renderMemoryItem(m, viewMode)}</div>`;
    });

    if (collapsed) {
      html += `<button class="btn-show-more" onclick="showMoreMemories(this)">Show ${items.length - visibleCount} more</button>`;
    }
    html += '</div></div>';
  }
  return html;
}


// ═══════════════════════════════════════════════════════════════════════
// Single Memory Item
// ═══════════════════════════════════════════════════════════════════════

function renderMemoryItem(m, viewMode) {
  const created = m.created_at
    ? formatDate(m.created_at, { month: 'short', day: 'numeric' })
    : '';
  const importance = m.importance || 0.5;
  const impClass = importance >= 0.8 ? 'imp-high' : importance >= 0.5 ? 'imp-mid' : 'imp-low';
  const tags = (m.tags || []).slice(0, 4);
  const checked = _selectedIds.has(m.id) ? ' checked' : '';
  const isPending = m.reviewed === false;
  const vm = viewMode || 'review';

  // Age indicator for pending items
  let ageLabel = '';
  if (isPending && m.created_at) {
    const days = Math.floor((Date.now() - new Date(m.created_at).getTime()) / 86400000);
    if (days >= 5) {
      ageLabel = `<span style="font-size:0.6rem;color:var(--yellow,#c90);margin-left:4px;" title="Auto-commits in ${7 - days} day(s)">${days}d</span>`;
    }
  }

  return `<div class="memory-item ${impClass}" id="mem-${m.id}">
    <div class="memory-row">
      ${_bulkMode ? `<input type="checkbox" class="mem-checkbox" data-id="${m.id}" onchange="updateBulkSelection()"${checked}>` : ''}
      <div class="memory-text">${ESC(m.content)}</div>
      <div class="memory-meta-right">
        <span class="memory-category cat-${ESC(m.category || 'general')}">${ESC(m.category || 'general')}</span>
        ${tags.length ? tags.map(t => `<span class="memory-tag">${ESC(t)}</span>`).join('') : ''}
        <span class="memory-date">${created}${ageLabel}</span>
        <span class="memory-imp" title="Importance: ${importance}">${importance.toFixed(1)}</span>
      </div>
    </div>
    <div class="memory-actions">
      <button class="btn-sm btn-action" onclick="startEditMemory(${m.id})">Edit</button>
      <button class="btn-sm" onclick="showHistory(${m.id})">History</button>
      <button class="btn-sm" onclick="deleteMemory(${m.id})" style="color:var(--red);">Remove</button>
      ${isPending && vm !== 'all' ? `<button class="btn-sm btn-action" onclick="commitMemory(${m.id})">Commit</button>` : ''}
    </div>
  </div>`;
}


// ═══════════════════════════════════════════════════════════════════════
// Inline Edit / Correct
// ═══════════════════════════════════════════════════════════════════════

function startEditMemory(id) {
  const mem = _memoryData.memories.find(m => m.id === id);
  if (!mem) return;

  const el = document.getElementById(`mem-${id}`);
  if (!el) return;

  const catOptions = _CATEGORIES.map(c =>
    `<option value="${ESC(c)}"${c === (mem.category || 'general') ? ' selected' : ''}>${ESC(c)}</option>`
  ).join('');

  el.innerHTML = `<div class="memory-edit-form" style="display:flex;flex-direction:column;gap:6px;">
    <textarea id="edit-content-${id}" style="width:100%;min-height:60px;padding:6px;font-size:0.78rem;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--fg);resize:vertical;">${ESC(mem.content)}</textarea>
    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
      <label style="font-size:0.7rem;color:var(--dim);">Category:
        <select id="edit-cat-${id}" style="padding:3px 6px;font-size:0.72rem;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--fg);">
          ${catOptions}
        </select>
      </label>
      <label style="font-size:0.7rem;color:var(--dim);">Importance:
        <input type="range" id="edit-imp-${id}" min="0" max="1" step="0.1" value="${mem.importance || 0.5}" style="width:80px;vertical-align:middle;">
        <span id="edit-imp-val-${id}" style="font-size:0.7rem;">${(mem.importance || 0.5).toFixed(1)}</span>
      </label>
      <div style="margin-left:auto;display:flex;gap:4px;">
        <button class="btn-sm btn-action" onclick="saveEditMemory(${id})" style="font-size:0.7rem;padding:3px 10px;">Save</button>
        <button class="btn-sm" onclick="cancelEditMemory(${id})" style="font-size:0.7rem;padding:3px 10px;">Cancel</button>
      </div>
    </div>
  </div>`;

  const slider = document.getElementById(`edit-imp-${id}`);
  const valSpan = document.getElementById(`edit-imp-val-${id}`);
  if (slider && valSpan) {
    slider.addEventListener('input', () => { valSpan.textContent = parseFloat(slider.value).toFixed(1); });
  }
}

async function saveEditMemory(id) {
  const content = document.getElementById(`edit-content-${id}`)?.value.trim();
  const category = document.getElementById(`edit-cat-${id}`)?.value;
  const importance = parseFloat(document.getElementById(`edit-imp-${id}`)?.value || 0.5);

  if (!content) return;

  const mem = _memoryData.memories.find(m => m.id === id);
  const contentChanged = mem && content !== mem.content;

  try {
    if (contentChanged) {
      await fetch(`/api/memories/${id}/correct`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          content: content,
          reason: 'Corrected via Memory tab',
        }),
      });
    } else {
      await fetch(`/api/memories/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ category, importance }),
      });
    }
    loadMemory();
  } catch (err) {
    console.error('Save failed:', err);
  }
}

function cancelEditMemory(id) {
  const mem = _memoryData.memories.find(m => m.id === id);
  if (!mem) return;
  const el = document.getElementById(`mem-${id}`);
  if (el) {
    el.outerHTML = renderMemoryItem(mem);
  }
}


// ═══════════════════════════════════════════════════════════════════════
// Commit Actions
// ═══════════════════════════════════════════════════════════════════════

async function commitMemory(id) {
  try {
    await fetch(`/api/memories/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reviewed: true }),
    });
    loadMemory();
  } catch (err) { console.error('Commit failed:', err); }
}

async function commitAllPending() {
  const ids = _memoryData.memories.filter(m => m.reviewed === false).map(m => m.id);
  if (!ids.length) return;
  if (!confirm(`Commit all ${ids.length} pending memories to long-term storage?`)) return;

  try {
    await fetch('/api/memories/commit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids }),
    });
    loadMemory();
  } catch (err) { console.error('Commit all failed:', err); }
}

async function bulkCommitSelected() {
  if (_selectedIds.size === 0) { alert('Select some memories first.'); return; }

  try {
    await fetch('/api/memories/commit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids: [..._selectedIds] }),
    });
    _selectedIds.clear();
    loadMemory();
  } catch (err) { console.error('Bulk commit failed:', err); }
}


// ═══════════════════════════════════════════════════════════════════════
// Bulk Operations
// ═══════════════════════════════════════════════════════════════════════

function toggleBulkMode() {
  _bulkMode = !_bulkMode;
  _selectedIds.clear();
  const btn = document.getElementById('bulkModeBtn');
  const bar = document.getElementById('memory-bulk-bar');
  if (btn) btn.style.background = _bulkMode ? 'var(--accent)' : '';
  if (bar) bar.style.display = _bulkMode ? 'flex' : 'none';
  updateBulkCount();
  const container = document.getElementById('memory-content');
  if (container) renderMemoryDashboard(container);
}

function updateBulkSelection() {
  _selectedIds.clear();
  document.querySelectorAll('.mem-checkbox:checked').forEach(cb => {
    _selectedIds.add(parseInt(cb.dataset.id));
  });
  updateBulkCount();
}

function updateBulkCount() {
  const el = document.getElementById('memSelectedCount');
  if (el) el.textContent = `${_selectedIds.size} selected`;
}

function toggleSelectAll(masterCb) {
  const checkboxes = document.querySelectorAll('.mem-checkbox');
  checkboxes.forEach(cb => { cb.checked = masterCb.checked; });
  updateBulkSelection();
}

async function applyBulkRecategorize() {
  const cat = document.getElementById('memBulkCat')?.value;
  if (!cat) { alert('Pick a category first.'); return; }
  if (_selectedIds.size === 0) { alert('Select some memories first.'); return; }

  try {
    const res = await fetch('/api/memories/bulk', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids: [..._selectedIds], category: cat }),
    });
    const data = await res.json();
    if (data.updated) {
      _selectedIds.clear();
      loadMemory();
    }
  } catch (err) {
    console.error('Bulk update failed:', err);
  }
}

async function bulkDismissSelected() {
  if (_selectedIds.size === 0) { alert('Select some memories first.'); return; }
  if (!confirm(`Dismiss ${_selectedIds.size} memories? They will be deactivated.`)) return;

  try {
    await fetch('/api/memories/bulk', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids: [..._selectedIds], is_active: false }),
    });
    _selectedIds.clear();
    loadMemory();
  } catch (err) {
    console.error('Bulk dismiss failed:', err);
  }
}


// ═══════════════════════════════════════════════════════════════════════
// Single Actions
// ═══════════════════════════════════════════════════════════════════════

async function approveMemory(id) {
  try {
    await fetch(`/api/memories/${id}/approve`, { method: 'POST' });
    loadMemory();
  } catch (err) { console.error('Approve failed:', err); }
}

async function dismissMemory(id) {
  if (!confirm('Dismiss this memory? It will be deactivated.')) return;
  try {
    await fetch(`/api/memories/${id}/dismiss`, { method: 'POST' });
    loadMemory();
  } catch (err) { console.error('Dismiss failed:', err); }
}

async function deleteMemory(id) {
  if (!confirm('Remove this memory entry?')) return;
  try {
    await fetch(`/api/memories/${id}`, { method: 'DELETE' });
    loadMemory();
  } catch (err) { console.error('Delete failed:', err); }
}

async function showHistory(id) {
  try {
    const data = await fetch(`/api/memories/${id}/history`).then(r => r.json());
    const history = data.history || [];

    if (history.length <= 1) {
      alert('No correction history for this memory.');
      return;
    }

    let modalContent = '<div class="history-chain">';
    history.forEach((m, idx) => {
      const date = m.created_at
        ? formatDate(m.created_at, { month: 'short', day: 'numeric', year: 'numeric' })
        : '';
      const active = m.is_active ? 'current' : 'superseded';
      const source = m.source_summary || '';

      modalContent += `<div class="history-entry ${active}">
        <div class="history-marker">${idx === history.length - 1 ? '&#9679;' : '&#9675;'}</div>
        <div class="history-body">
          <div class="history-meta">
            <span class="history-id">#${m.id}</span>
            <span class="history-date">${date}</span>
            <span class="history-status">${active}</span>
          </div>
          <div class="history-content">${ESC(m.content)}</div>
          ${source ? `<div class="history-source">${ESC(source)}</div>` : ''}
        </div>
      </div>`;
      if (idx < history.length - 1) {
        modalContent += '<div class="history-arrow">&#8595;</div>';
      }
    });
    modalContent += '</div>';
    showModal('Memory History', modalContent);
  } catch (err) { console.error('History failed:', err); }
}

function showModal(title, content) {
  const existing = document.getElementById('memory-modal');
  if (existing) existing.remove();

  const modal = document.createElement('div');
  modal.id = 'memory-modal';
  modal.className = 'modal-overlay';
  modal.innerHTML = `<div class="modal-box">
    <div class="modal-header">
      <h3>${title}</h3>
      <button class="modal-close" onclick="document.getElementById('memory-modal').remove()">&times;</button>
    </div>
    <div class="modal-body">${content}</div>
  </div>`;
  modal.addEventListener('click', (e) => {
    if (e.target === modal) modal.remove();
  });
  document.body.appendChild(modal);
}

function toggleSection(header) {
  const body = header.nextElementSibling;
  const icon = header.querySelector('.toggle-icon');
  if (body.style.display === 'none') {
    body.style.display = '';
    icon.innerHTML = '&#9660;';
  } else {
    body.style.display = 'none';
    icon.innerHTML = '&#9654;';
  }
}

function showMoreMemories(btn) {
  const section = btn.parentElement;
  section.querySelectorAll('[data-overflow]').forEach(el => {
    el.style.display = '';
    el.removeAttribute('data-overflow');
  });
  btn.remove();
}
