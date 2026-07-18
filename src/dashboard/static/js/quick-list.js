// =============================================================================
// Quick Lists — lightweight list cards on the home board
// =============================================================================
// Loaded alongside home.js. Provides loadQuickLists() called on home tab load.
// Lists show as compact cards. Click to open a modal with full item management.
//
// #QL-EDIT  — tap item text to rename in place
// #QL-DRAG  — drag handle to reorder (position PATCH; HTML5 DnD + touch)
// #QL-SPACER — human-added section divider (item_type=spacer)
// =============================================================================

let _qlLoaded = false;
let _qlData = [];
let _qlDragId = null;
let _qlOpenListId = null;
let _qlTouch = null; // { itemId, listId, overEl } — mobile touch-drag state
let _qlTouchMoveFn = null;
let _qlTouchEndFn = null;

async function loadQuickLists() {
    const container = document.getElementById('ql-cards');
    if (!container) return;

    try {
        const res = await fetch('/api/quick-lists');
        if (!res.ok) throw new Error(`${res.status}`);
        const data = await res.json();
        _qlData = data.lists || [];
        _renderQLCards(container);
    } catch (e) {
        container.innerHTML = '<span class="empty-msg">Lists unavailable</span>';
    }
    _qlLoaded = true;
}

function _renderQLCards(container) {
    const pinned = _qlData.filter(l => l.pinned);

    if (!pinned.length) {
        container.innerHTML = `<div class="ql-empty-hint" onclick="qlNewList()">
            <span class="ql-empty-icon">+</span>
            <span>Create your first list</span>
        </div>`;
        return;
    }

    let html = '';
    pinned.forEach(list => {
        const badge = list.unchecked > 0
            ? `<span class="ql-badge">${list.unchecked}</span>`
            : (list.total > 0 ? '<span class="ql-badge ql-badge-done">✓</span>' : '');
        const colorStyle = list.color ? `border-left-color: ${ESC(list.color)};` : '';

        html += `<div class="ql-card" onclick="qlOpen(${list.id})" style="${colorStyle}">
            <span class="ql-card-icon">${list.icon || '📋'}</span>
            <span class="ql-card-name">${ESC(list.name)}</span>
            ${badge}
        </div>`;
    });

    html += `<div class="ql-card ql-card-add" onclick="qlNewList()">
        <span class="ql-card-icon">+</span>
    </div>`;

    container.innerHTML = html;
}

// =============================================================================
// Modal — full list with items
// =============================================================================

async function qlOpen(listId) {
    const list = _qlData.find(l => l.id === listId);
    if (!list) return;
    _qlOpenListId = listId;

    let modal = document.getElementById('ql-modal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'ql-modal';
        modal.className = 'modal-overlay';
        modal.onclick = (e) => { if (e.target === modal) qlCloseModal(); };
        document.body.appendChild(modal);
    }

    modal.innerHTML = `<div class="modal-content ql-modal-content">
        <div class="modal-header ql-modal-header">
            <div class="ql-modal-title-row">
                <span class="ql-modal-icon">${list.icon || '📋'}</span>
                <span class="modal-title">${ESC(list.name)}</span>
            </div>
            <div class="ql-modal-actions">
                <button class="btn-icon-small" onclick="qlEditList(${listId})" title="Edit list">✎</button>
                <button class="btn-cancel" onclick="qlCloseModal()">✕</button>
            </div>
        </div>
        <div class="ql-add-row">
            <input type="text" id="ql-add-input" class="ql-add-input" placeholder="Add item..."
                   onkeydown="if(event.key==='Enter'){qlAddItem(${listId});}"
                   autofocus>
            <button class="ql-add-btn" onclick="qlAddItem(${listId})" title="Add item">+</button>
            <button class="ql-spacer-btn" onclick="qlAddSpacer(${listId})" title="Add section spacer">§</button>
        </div>
        <div class="modal-body ql-items-body" id="ql-items-body">
            <div class="loading">Loading...</div>
        </div>
        <div class="ql-modal-footer" id="ql-modal-footer"></div>
    </div>`;
    modal.style.display = 'flex';

    setTimeout(() => document.getElementById('ql-add-input')?.focus(), 100);
    await _qlLoadItems(listId);
}

function _qlRenderItemRow(item, listId) {
    const isSpacer = (item.item_type || 'item') === 'spacer';
    if (isSpacer) {
        const label = item.text ? ESC(item.text) : '';
        return `<div class="ql-item ql-item-spacer" data-item-id="${item.id}" data-type="spacer"
                     draggable="true"
                     ondragstart="_qlDragStart(event, ${item.id})"
                     ondragover="_qlDragOver(event)"
                     ondrop="_qlDrop(event, ${listId})"
                     ondragend="_qlDragEnd(event)">
            <span class="ql-drag-handle" title="Drag to reorder" aria-hidden="true">⠿</span>
            <div class="ql-spacer-line" onclick="qlStartEdit(${item.id}, ${listId})" title="Tap to label">
                <span class="ql-spacer-label ${item.text ? '' : 'ql-spacer-empty'}">${label || 'section'}</span>
            </div>
            <button class="ql-item-delete" onclick="qlDeleteItem(${item.id}, ${listId})" title="Delete">✕</button>
        </div>`;
    }

    const checked = !!item.checked;
    return `<div class="ql-item${checked ? ' ql-item-checked' : ''}" data-item-id="${item.id}" data-type="item"
                 draggable="true"
                 ondragstart="_qlDragStart(event, ${item.id})"
                 ondragover="_qlDragOver(event)"
                 ondrop="_qlDrop(event, ${listId})"
                 ondragend="_qlDragEnd(event)">
        <span class="ql-drag-handle" title="Drag to reorder" aria-hidden="true">⠿</span>
        <label class="ql-check-label">
            <input type="checkbox" class="ql-checkbox" ${checked ? 'checked' : ''}
                   onchange="qlToggleItem(${item.id}, ${listId}, ${checked ? 'false' : 'true'})">
            <span class="ql-item-text${checked ? ' ql-item-done' : ''}"
                  data-role="text"
                  onclick="event.preventDefault(); event.stopPropagation(); qlStartEdit(${item.id}, ${listId})"
                  title="Tap to edit">${ESC(item.text)}</span>
        </label>
        <button class="ql-item-delete" onclick="qlDeleteItem(${item.id}, ${listId})" title="Delete">✕</button>
    </div>`;
}

async function _qlLoadItems(listId) {
    const body = document.getElementById('ql-items-body');
    const footer = document.getElementById('ql-modal-footer');
    if (!body) return;

    try {
        const res = await fetch(`/api/quick-lists/${listId}/items`);
        if (!res.ok) throw new Error(`${res.status}`);
        const data = await res.json();
        const items = data.items || [];

        if (!items.length) {
            body.innerHTML = '<div class="ql-empty-items">No items yet. Add something above — or tap § for a section spacer.</div>';
            if (footer) footer.innerHTML = '';
            return;
        }

        // Keep API order (spacers + unchecked first by position, then checked)
        const open = items.filter(i => (i.item_type || 'item') === 'spacer' || !i.checked);
        const checked = items.filter(i => (i.item_type || 'item') === 'item' && i.checked);

        let html = '';
        open.forEach(item => { html += _qlRenderItemRow(item, listId); });

        if (checked.length) {
            html += `<div class="ql-checked-divider">
                <span>Checked (${checked.length})</span>
            </div>`;
            checked.forEach(item => { html += _qlRenderItemRow(item, listId); });
        }

        body.innerHTML = html;
        _qlBindDragHandles(listId);

        if (footer) {
            if (checked.length) {
                footer.innerHTML = `<button class="ql-clear-btn" onclick="qlClearChecked(${listId})">Clear ${checked.length} checked</button>`;
            } else {
                footer.innerHTML = '';
            }
        }
    } catch (e) {
        body.innerHTML = `<span class="empty-msg">Error loading items</span>`;
    }
}

async function qlAddItem(listId) {
    const input = document.getElementById('ql-add-input');
    const text = input?.value?.trim();
    if (!text) { input?.focus(); return; }

    const texts = text.includes(',')
        ? text.split(',').map(t => t.trim()).filter(Boolean)
        : [text];

    try {
        await fetch(`/api/quick-lists/${listId}/items`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(texts.length > 1 ? { items: texts } : { text: texts[0] }),
        });
        input.value = '';
        input.focus();
        await _qlLoadItems(listId);
        _qlRefreshCards();
    } catch (e) {
        console.error('Failed to add item:', e);
    }
}

async function qlAddSpacer(listId) {
    // Optional: if the add input has text, use it as the section label
    const input = document.getElementById('ql-add-input');
    const label = input?.value?.trim() || '';
    try {
        await fetch(`/api/quick-lists/${listId}/items`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ item_type: 'spacer', text: label }),
        });
        if (input) input.value = '';
        await _qlLoadItems(listId);
        _qlRefreshCards();
    } catch (e) {
        console.error('Failed to add spacer:', e);
    }
}

async function qlToggleItem(itemId, listId, checked) {
    try {
        await fetch(`/api/quick-lists/items/${itemId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ checked }),
        });
        await _qlLoadItems(listId);
        _qlRefreshCards();
    } catch (e) {
        console.error('Failed to toggle item:', e);
    }
}

async function qlDeleteItem(itemId, listId) {
    try {
        await fetch(`/api/quick-lists/items/${itemId}`, { method: 'DELETE' });
        await _qlLoadItems(listId);
        _qlRefreshCards();
    } catch (e) {
        console.error('Failed to delete item:', e);
    }
}

async function qlClearChecked(listId) {
    try {
        await fetch(`/api/quick-lists/${listId}/clear`, { method: 'POST' });
        await _qlLoadItems(listId);
        _qlRefreshCards();
    } catch (e) {
        console.error('Failed to clear checked:', e);
    }
}

// =============================================================================
// #QL-EDIT — inline rename
// =============================================================================

function qlStartEdit(itemId, listId) {
    const row = document.querySelector(`.ql-item[data-item-id="${itemId}"]`);
    if (!row || row.classList.contains('ql-editing')) return;

    const isSpacer = row.dataset.type === 'spacer';
    const textEl = isSpacer
        ? row.querySelector('.ql-spacer-label')
        : row.querySelector('.ql-item-text');
    if (!textEl) return;

    const current = isSpacer && textEl.classList.contains('ql-spacer-empty')
        ? ''
        : (textEl.textContent || '');

    row.classList.add('ql-editing');
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'ql-inline-input';
    input.value = current;
    input.setAttribute('aria-label', isSpacer ? 'Section label' : 'Item text');
    textEl.replaceWith(input);
    input.focus();
    input.select();

    let finished = false;
    const finish = async (save) => {
        if (finished) return;
        finished = true;
        const next = input.value.trim();
        // Spacers may be blank; items need text (keep previous if emptied)
        if (save) {
            if (!isSpacer && !next) {
                // revert — don't blank a real item
                await _qlLoadItems(listId);
                return;
            }
            if (next !== current) {
                try {
                    await fetch(`/api/quick-lists/items/${itemId}`, {
                        method: 'PATCH',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ text: next }),
                    });
                } catch (e) {
                    console.error('Failed to rename item:', e);
                }
            }
        }
        await _qlLoadItems(listId);
        _qlRefreshCards();
    };

    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); finish(true); }
        else if (e.key === 'Escape') { e.preventDefault(); finish(false); }
    });
    input.addEventListener('blur', () => finish(true));
}

// =============================================================================
// #QL-DRAG — reorder via position PATCH
// Desktop: HTML5 DnD on the row. Mobile: touch on the ⠿ handle only
// (HTML5 drag-and-drop does not fire from touch on iOS/Android).
// =============================================================================

function _qlBindDragHandles(listId) {
    const body = document.getElementById('ql-items-body');
    if (!body) return;
    body.querySelectorAll('.ql-item .ql-drag-handle').forEach((handle) => {
        // passive:false so we can preventDefault and stop the page scrolling
        handle.addEventListener('touchstart', (e) => _qlTouchStart(e, listId), { passive: false });
    });
}

function _qlSetDragOver(row) {
    document.querySelectorAll('.ql-item.ql-drag-over').forEach((el) => {
        if (el !== row) el.classList.remove('ql-drag-over');
    });
    if (row && row.classList.contains('ql-item')) row.classList.add('ql-drag-over');
}

function _qlDragStart(e, itemId) {
    _qlDragId = itemId;
    if (e.dataTransfer) {
        e.dataTransfer.effectAllowed = 'move';
        try { e.dataTransfer.setData('text/plain', String(itemId)); } catch (_) {}
    }
    const row = e.currentTarget;
    if (row) row.classList.add('ql-dragging');
}

function _qlDragOver(e) {
    e.preventDefault();
    if (e.dataTransfer) e.dataTransfer.dropEffect = 'move';
    const row = e.currentTarget;
    if (!row || !row.classList.contains('ql-item')) return;
    _qlSetDragOver(row);
}

async function _qlReorderTo(listId, fromId, toId) {
    if (!fromId || !toId || fromId === toId) return false;

    const body = document.getElementById('ql-items-body');
    if (!body) return false;
    // Final order is whatever the DOM shows after move (open + checked blocks).
    const rows = Array.from(body.querySelectorAll('.ql-item'));
    const ids = rows.map(r => parseInt(r.getAttribute('data-item-id'), 10)).filter(Boolean);
    const from = ids.indexOf(fromId);
    const to = ids.indexOf(toId);
    if (from < 0 || to < 0 || from === to) return false;

    ids.splice(to, 0, ids.splice(from, 1)[0]);

    try {
        await Promise.all(ids.map((id, idx) =>
            fetch(`/api/quick-lists/items/${id}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ position: idx }),
            })
        ));
        await _qlLoadItems(listId);
        return true;
    } catch (err) {
        console.error('Failed to reorder:', err);
        await _qlLoadItems(listId);
        return false;
    }
}

async function _qlDrop(e, listId) {
    e.preventDefault();
    const target = e.currentTarget;
    document.querySelectorAll('.ql-item.ql-drag-over').forEach(el => el.classList.remove('ql-drag-over'));
    if (!target || _qlDragId == null) return;

    const targetId = parseInt(target.getAttribute('data-item-id'), 10);
    await _qlReorderTo(listId, _qlDragId, targetId);
}

function _qlDragEnd(e) {
    _qlDragId = null;
    document.querySelectorAll('.ql-item.ql-dragging, .ql-item.ql-drag-over').forEach(el => {
        el.classList.remove('ql-dragging', 'ql-drag-over');
    });
}

function _qlTouchStart(e, listId) {
    if (!e.touches || e.touches.length !== 1) return;
    // Ignore touch-drag while inline-editing
    if (e.target && e.target.closest && e.target.closest('.ql-editing, .ql-inline-input')) return;

    const handle = e.currentTarget;
    const row = handle && handle.closest ? handle.closest('.ql-item') : null;
    if (!row) return;

    const itemId = parseInt(row.getAttribute('data-item-id'), 10);
    if (!itemId) return;

    // Stop scroll/zoom so the handle gesture owns the touch
    e.preventDefault();
    e.stopPropagation();

    _qlClearTouchListeners();
    _qlDragId = itemId;
    row.classList.add('ql-dragging');
    _qlTouch = { itemId, listId, overEl: null };

    _qlTouchMoveFn = (ev) => _qlTouchMove(ev);
    _qlTouchEndFn = (ev) => _qlTouchEnd(ev);
    document.addEventListener('touchmove', _qlTouchMoveFn, { passive: false });
    document.addEventListener('touchend', _qlTouchEndFn);
    document.addEventListener('touchcancel', _qlTouchEndFn);
}

function _qlTouchMove(e) {
    if (!_qlTouch) return;
    if (!e.touches || !e.touches.length) return;
    e.preventDefault();

    const t = e.touches[0];
    const el = document.elementFromPoint(t.clientX, t.clientY);
    const row = el && el.closest ? el.closest('#ql-items-body .ql-item') : null;
    if (row) {
        const overId = parseInt(row.getAttribute('data-item-id'), 10);
        if (overId && overId !== _qlTouch.itemId) {
            _qlSetDragOver(row);
            _qlTouch.overEl = row;
            return;
        }
    }
    _qlSetDragOver(null);
    _qlTouch.overEl = null;
}

async function _qlTouchEnd(e) {
    const state = _qlTouch;
    _qlClearTouchListeners();
    _qlTouch = null;

    try {
        if (!state) return;
        const target = state.overEl;
        const targetId = target ? parseInt(target.getAttribute('data-item-id'), 10) : 0;
        if (targetId) {
            await _qlReorderTo(state.listId, state.itemId, targetId);
        }
    } finally {
        // Always clear drag chrome / id — reload already drops classes when reorder runs
        _qlDragEnd();
    }
}

function _qlClearTouchListeners() {
    if (_qlTouchMoveFn) {
        document.removeEventListener('touchmove', _qlTouchMoveFn);
        _qlTouchMoveFn = null;
    }
    if (_qlTouchEndFn) {
        document.removeEventListener('touchend', _qlTouchEndFn);
        document.removeEventListener('touchcancel', _qlTouchEndFn);
        _qlTouchEndFn = null;
    }
}

function qlCloseModal() {
    const modal = document.getElementById('ql-modal');
    if (modal) modal.style.display = 'none';
    _qlOpenListId = null;
}

// =============================================================================
// New List / Edit List
// =============================================================================

function qlNewList() {
    const icons = ['📋', '🛒', '💡', '📌', '🎯', '🔧', '📦', '🏠', '💊', '📚', '🎵', '✈️'];

    let modal = document.getElementById('ql-modal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'ql-modal';
        modal.className = 'modal-overlay';
        modal.onclick = (e) => { if (e.target === modal) qlCloseModal(); };
        document.body.appendChild(modal);
    }

    modal.innerHTML = `<div class="modal-content ql-modal-content ql-modal-small">
        <div class="modal-header">
            <span class="modal-title">New List</span>
            <button class="btn-cancel" onclick="qlCloseModal()">✕</button>
        </div>
        <div class="modal-body" style="padding: 1rem;">
            <div class="ql-form-field">
                <label>Name</label>
                <input type="text" id="ql-new-name" class="ql-add-input" placeholder="Groceries, Ideas, Errands..."
                       onkeydown="if(event.key==='Enter'){qlCreateList();}" autofocus>
            </div>
            <div class="ql-form-field">
                <label>Icon</label>
                <div class="ql-icon-picker" id="ql-icon-picker">
                    ${icons.map((ic, i) => `<span class="ql-icon-opt${i === 0 ? ' ql-icon-selected' : ''}" onclick="qlPickIcon(this, '${ic}')">${ic}</span>`).join('')}
                </div>
                <input type="hidden" id="ql-new-icon" value="📋">
            </div>
            <button class="ql-create-btn" onclick="qlCreateList()">Create List</button>
        </div>
    </div>`;
    modal.style.display = 'flex';
    setTimeout(() => document.getElementById('ql-new-name')?.focus(), 100);
}

function qlPickIcon(el, icon) {
    document.querySelectorAll('.ql-icon-opt').forEach(e => e.classList.remove('ql-icon-selected'));
    el.classList.add('ql-icon-selected');
    document.getElementById('ql-new-icon').value = icon;
}

async function qlCreateList() {
    const name = document.getElementById('ql-new-name')?.value?.trim();
    if (!name) { document.getElementById('ql-new-name')?.focus(); return; }
    const icon = document.getElementById('ql-new-icon')?.value || '📋';

    try {
        const res = await fetch('/api/quick-lists', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, icon }),
        });
        if (!res.ok) throw new Error(`${res.status}`);
        const created = await res.json();
        _qlData.push(created);
        qlCloseModal();
        _qlRefreshCards();
        setTimeout(() => qlOpen(created.id), 200);
    } catch (e) {
        console.error('Failed to create list:', e);
    }
}

function qlEditList(listId) {
    const list = _qlData.find(l => l.id === listId);
    if (!list) return;

    const body = document.getElementById('ql-items-body');
    const footer = document.getElementById('ql-modal-footer');
    if (!body) return;

    body.innerHTML = `<div style="padding: 1rem;">
        <div class="ql-form-field">
            <label>Name</label>
            <input type="text" id="ql-edit-name" class="ql-add-input" value="${ESC(list.name)}">
        </div>
        <div class="ql-form-field">
            <label>Icon</label>
            <input type="text" id="ql-edit-icon" class="ql-add-input" value="${list.icon || '📋'}" style="width:60px;">
        </div>
        <div class="ql-edit-actions">
            <button class="ql-create-btn" onclick="qlSaveList(${listId})">Save</button>
            <button class="ql-delete-list-btn" onclick="qlDeleteList(${listId})">Delete List</button>
        </div>
    </div>`;
    if (footer) footer.innerHTML = `<button class="btn-cancel" onclick="qlOpen(${listId})">Cancel</button>`;
}

async function qlSaveList(listId) {
    const name = document.getElementById('ql-edit-name')?.value?.trim();
    const icon = document.getElementById('ql-edit-icon')?.value?.trim() || '📋';
    if (!name) return;

    try {
        await fetch(`/api/quick-lists/${listId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, icon }),
        });
        const list = _qlData.find(l => l.id === listId);
        if (list) { list.name = name; list.icon = icon; }
        qlOpen(listId);
        _qlRefreshCards();
    } catch (e) {
        console.error('Failed to save list:', e);
    }
}

async function qlDeleteList(listId) {
    if (!confirm('Delete this list and all its items?')) return;
    try {
        await fetch(`/api/quick-lists/${listId}`, { method: 'DELETE' });
        _qlData = _qlData.filter(l => l.id !== listId);
        qlCloseModal();
        _qlRefreshCards();
    } catch (e) {
        console.error('Failed to delete list:', e);
    }
}

async function _qlRefreshCards() {
    try {
        const res = await fetch('/api/quick-lists');
        if (!res.ok) return;
        const data = await res.json();
        _qlData = data.lists || [];
    } catch { return; }

    const container = document.getElementById('ql-cards');
    if (container) _renderQLCards(container);
}
