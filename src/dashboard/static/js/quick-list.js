// =============================================================================
// Quick Lists — lightweight list cards on the home board
// =============================================================================
// Loaded alongside home.js. Provides loadQuickLists() called on home tab load.
// Lists show as compact cards. Click to open a modal with full item management.
// =============================================================================

let _qlLoaded = false;
let _qlData = [];

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

    // "+" card to create new list
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
            <button class="ql-add-btn" onclick="qlAddItem(${listId})">+</button>
        </div>
        <div class="modal-body ql-items-body" id="ql-items-body">
            <div class="loading">Loading...</div>
        </div>
        <div class="ql-modal-footer" id="ql-modal-footer"></div>
    </div>`;
    modal.style.display = 'flex';

    // Focus the input
    setTimeout(() => document.getElementById('ql-add-input')?.focus(), 100);

    // Load items
    await _qlLoadItems(listId);
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
            body.innerHTML = '<div class="ql-empty-items">No items yet. Add something above.</div>';
            if (footer) footer.innerHTML = '';
            return;
        }

        const unchecked = items.filter(i => !i.checked);
        const checked = items.filter(i => i.checked);

        let html = '';
        unchecked.forEach(item => {
            html += `<div class="ql-item" data-item-id="${item.id}">
                <label class="ql-check-label">
                    <input type="checkbox" class="ql-checkbox" onchange="qlToggleItem(${item.id}, ${listId}, true)">
                    <span class="ql-item-text">${ESC(item.text)}</span>
                </label>
                <button class="ql-item-delete" onclick="qlDeleteItem(${item.id}, ${listId})" title="Delete">✕</button>
            </div>`;
        });

        if (checked.length) {
            html += `<div class="ql-checked-divider">
                <span>Checked (${checked.length})</span>
            </div>`;
            checked.forEach(item => {
                html += `<div class="ql-item ql-item-checked" data-item-id="${item.id}">
                    <label class="ql-check-label">
                        <input type="checkbox" class="ql-checkbox" checked onchange="qlToggleItem(${item.id}, ${listId}, false)">
                        <span class="ql-item-text ql-item-done">${ESC(item.text)}</span>
                    </label>
                    <button class="ql-item-delete" onclick="qlDeleteItem(${item.id}, ${listId})" title="Delete">✕</button>
                </div>`;
            });
        }

        body.innerHTML = html;

        // Footer with clear button
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

    // Support comma-separated batch: "milk, eggs, bread"
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

function qlCloseModal() {
    const modal = document.getElementById('ql-modal');
    if (modal) modal.style.display = 'none';
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
        // Open the new list immediately
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
        // Update local data
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

// =============================================================================
// Refresh cards without full reload
// =============================================================================

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
