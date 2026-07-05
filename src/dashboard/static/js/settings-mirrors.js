// =============================================================================
// settings-mirrors.js — Mirror registry, features toggle, manager modal, drag reorder
// =============================================================================

// Mirror registry cache — fetched once, used by Settings + Mirror Manager
let _mirrorRegistryCache = null;

async function _getMirrorRegistry() {
    if (_mirrorRegistryCache) return _mirrorRegistryCache;
    try {
        const res = await fetch('/api/mirrors/registry');
        const data = await res.json();
        _mirrorRegistryCache = data.mirrors || [];
    } catch (e) {
        console.warn('[settings] Failed to load mirror registry:', e.message);
        _mirrorRegistryCache = [];
    }
    return _mirrorRegistryCache;
}

function _parseMirrorCSV(raw) {
    return (raw || '').split(',').map(s => {
        const t = s.trim();
        return t === 'scripture' ? 'scripture-tpt' : t;
    }).filter(Boolean);
}

function _getEnabledMirrors() {
    return _parseMirrorCSV(MC.features?.mirror_sources || MC.features?.mirror_source || '');
}

function _getLibraryMirrors() {
    // Library = which mirrors the user has "added" to their personal list.
    // Falls back to mirror_sources for migration (users who don't have mirror_library yet).
    const lib = MC.features?.mirror_library || '';
    if (lib.trim()) return _parseMirrorCSV(lib);
    return _getEnabledMirrors(); // migration fallback
}

async function loadSettingsFeatures() {
    const el = document.getElementById('settings-features');
    if (!el) return;

    const registry = await _getMirrorRegistry();
    const mirrorEnabled = MC.features?.mirror ? true : false;
    const enabledMirrors = _getEnabledMirrors();
    const libraryMirrors = _getLibraryMirrors();

    // Check if any music-type mirror is active
    const hasMusicMirror = enabledMirrors.some(id => {
        const m = registry.find(r => r.id === id);
        return m && m.type === 'music';
    });

    // Build library mirror cards — show all mirrors in library, checked if in mirror_sources
    const libraryCards = libraryMirrors
        .map(id => registry.find(r => r.id === id))
        .filter(Boolean)
        .map(m => _renderMirrorCard(m, enabledMirrors.includes(m.id)))
        .join('');

    // Master toggle
    el.innerHTML = `
        <div class="settings-edit-row" style="display:flex;align-items:center;justify-content:space-between;">
            <div style="flex:1;">
                <div style="font-size:0.82rem;">Tuning Mirrors</div>
                <div style="font-size:0.7rem;color:var(--dim);">Show mirror content on Attention Home</div>
            </div>
            <div style="cursor:pointer;position:relative;width:40px;height:22px;flex-shrink:0;"
                 onclick="toggleFeature('mirror', !MC.features?.['mirror'])">
                <span style="pointer-events:none;position:absolute;inset:0;background:${mirrorEnabled ? 'var(--accent)' : 'var(--border)'};border-radius:11px;transition:background 0.2s;"></span>
                <span style="pointer-events:none;position:absolute;top:2px;left:${mirrorEnabled ? '20px' : '2px'};width:18px;height:18px;background:#fff;border-radius:50%;transition:left 0.2s;"></span>
            </div>
        </div>
        ${mirrorEnabled ? `
            <div class="settings-edit-row" style="margin-top:8px;">
                <div style="font-size:0.7rem;color:var(--dim);margin-bottom:6px;">
                    Your Mirrors <span style="font-size:0.65rem;opacity:0.6;">(drag to reorder)</span>
                </div>
                <div id="mirror-sort-list">
                    ${libraryCards || '<div style="font-size:0.75rem;color:var(--dim);padding:8px 0;">No mirrors added. Tap Manage Mirrors to add one.</div>'}
                </div>
                <button onclick="_openMirrorManager()" class="mirror-manage-btn">Manage Mirrors</button>
            </div>
        ` : ''}`;

    // Streaming service selector — only when a music-type mirror is active
    if (mirrorEnabled && hasMusicMirror) {
        const currentService = MC.features?.streaming_service || 'youtube';
        const serviceOptions = [
            { key: 'youtube', label: 'YouTube Music' },
            { key: 'spotify', label: 'Spotify' },
            { key: 'apple', label: 'Apple Music' },
        ];
        el.innerHTML += `
            <div class="settings-edit-row" style="margin-top:16px;display:flex;align-items:center;justify-content:space-between;">
                <div style="flex:1;">
                    <div style="font-size:0.82rem;">Music Service</div>
                    <div style="font-size:0.7rem;color:var(--dim);">For Music Mirror playback</div>
                </div>
                <select onchange="_setStreamingService(this.value)"
                        style="background:var(--card);border:1px solid var(--border);color:var(--text);padding:6px 10px;border-radius:6px;font-family:inherit;font-size:0.8rem;">
                    ${serviceOptions.map(o => `<option value="${o.key}" ${currentService === o.key ? 'selected' : ''}>${o.label}</option>`).join('')}
                </select>
            </div>`;
    }

    // Init touch drag after DOM is ready
    setTimeout(_initMirrorTouchDrag, 50);
}

function _renderMirrorCard(mirror, isChecked) {
    const typeLabel = mirror.type === 'music' ? 'Music' : 'Text';
    const typeBadgeColor = mirror.type === 'music' ? 'var(--accent)' : 'var(--dim)';
    return `<div class="mirror-sort-item" draggable="true" data-mirror-id="${mirror.id}"
                 style="display:flex;align-items:center;gap:8px;padding:8px 6px;border-radius:6px;
                        cursor:grab;border:1px solid transparent;transition:border-color 0.15s,background 0.15s;touch-action:none;
                        ${isChecked ? '' : 'opacity:0.6;'}"
                 ondragstart="_mirrorDragStart(event)" ondragover="_mirrorDragOver(event)"
                 ondrop="_mirrorDrop(event)" ondragend="_mirrorDragEnd(event)">
        <span class="mirror-drag-handle" style="color:var(--dim);font-size:1.1rem;cursor:grab;user-select:none;padding:4px 8px;touch-action:none;">&#9776;</span>
        <input type="checkbox" ${isChecked ? 'checked' : ''}
               onchange="toggleMirrorSource('${mirror.id}', this.checked)"
               style="accent-color:var(--accent);width:16px;height:16px;">
        <div style="flex:1;min-width:0;">
            <div style="font-size:0.8rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${ESC(mirror.name)}</div>
            <div style="font-size:0.65rem;color:var(--dim);display:flex;align-items:center;gap:6px;">
                <span style="color:${typeBadgeColor};">${typeLabel}</span>
                ${mirror.curator ? '<span>·</span><span>' + ESC(mirror.curator) + '</span>' : ''}
            </div>
        </div>
    </div>`;
}

async function toggleFeature(key, enabled) {
    if (!MC.features) MC.features = {};
    const saveData = { [key]: enabled };

    // When turning mirror ON for first time, set default library + sources
    if (key === 'mirror' && enabled) {
        const currentSources = (MC.features.mirror_sources || '').trim();
        const currentLibrary = (MC.features.mirror_library || '').trim();
        if (!currentSources && !currentLibrary) {
            // First time — populate both with defaults
            const reg = _mirrorRegistryCache || [];
            const defaults = reg.filter(m => m.default).map(m => m.id);
            const defaultStr = defaults.length ? defaults.join(',') : 'scripture-tpt,music-mirror';
            saveData.mirror_sources = defaultStr;
            saveData.mirror_library = defaultStr;
            MC.features.mirror_sources = defaultStr;
            MC.features.mirror_library = defaultStr;
        } else if (!currentLibrary && currentSources) {
            // Migration — user has sources but no library yet
            saveData.mirror_library = currentSources;
            MC.features.mirror_library = currentSources;
        }
    }
    try {
        const res = await fetch('/api/settings/features', {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(saveData),
        });
        const data = await res.json();
        if (data.ok) {
            MC.features[key] = enabled;
            // Re-render the toggle visually
            loadSettingsFeatures();
        } else {
            console.warn('[settings] Feature toggle response:', data);
        }
    } catch (e) {
        console.warn('[settings] Feature toggle failed:', e.message);
    }
}

async function _setStreamingService(service) {
    if (!MC.features) MC.features = {};
    try {
        const res = await fetch('/api/settings/features', {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ streaming_service: service }),
        });
        const data = await res.json();
        if (data.ok) {
            MC.features.streaming_service = service;
        }
    } catch (e) {
        console.warn('[settings] Streaming service save failed:', e.message);
    }
}

async function toggleMirrorSource(mirrorId, enabled) {
    // Parse current sources
    const raw = MC.features?.mirror_sources || MC.features?.mirror_source || '';
    let sources = raw.split(',').map(s => {
        const t = s.trim();
        return t === 'scripture' ? 'scripture-tpt' : t;
    }).filter(Boolean);

    if (enabled && !sources.includes(mirrorId)) {
        sources.push(mirrorId);
    } else if (!enabled) {
        sources = sources.filter(s => s !== mirrorId);
    }

    const newValue = sources.join(',');
    const mirrorOn = sources.length > 0;

    // Sync mirror flag — unchecking all sources turns mirror off
    const saveData = { mirror_sources: newValue, mirror: mirrorOn };

    try {
        const res = await fetch('/api/settings/features', {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(saveData),
        });
        const data = await res.json();
        if (data.ok) {
            MC.features.mirror_sources = newValue;
            MC.features.mirror = mirrorOn;
            loadSettingsFeatures(); // Re-render to reflect state
        }
    } catch (e) {
        console.warn('[settings] Mirror source toggle failed:', e.message);
    }
}

// ── Mirror Manager modal ──────────────────────────────────────────────────

async function _openMirrorManager() {
    const registry = await _getMirrorRegistry();
    const libraryMirrors = _getLibraryMirrors();

    const mirrorItems = registry
        .filter(m => m.available !== false)
        .map(m => {
            const inLibrary = libraryMirrors.includes(m.id);
            const typeLabel = m.type === 'music' ? 'Music' : 'Text';
            const typeBadgeColor = m.type === 'music' ? 'var(--accent)' : 'var(--dim)';
            return `<div class="mm-item${inLibrary ? ' mm-active' : ''}" data-mirror-id="${m.id}">
                <div class="mm-item-info">
                    <div class="mm-item-header">
                        <span class="mm-item-name">${ESC(m.name)}</span>
                        <span class="mm-type-badge" style="color:${typeBadgeColor};">${typeLabel}</span>
                        ${m.default ? '<span class="mm-default-badge">Default</span>' : ''}
                    </div>
                    <div class="mm-item-desc">${ESC(m.description)}</div>
                    <div class="mm-item-meta">
                        ${m.curator ? ESC(m.curator) : ''}
                        ${m.canon ? ' · ' + ESC(m.canon) : ''}
                    </div>
                </div>
                <button class="mm-toggle-btn${inLibrary ? ' mm-btn-remove' : ' mm-btn-add'}"
                        onclick="_mirrorManagerToggle('${m.id}', ${!inLibrary})">
                    ${inLibrary ? 'Remove' : 'Add'}
                </button>
            </div>`;
        }).join('');

    // Create modal
    const overlay = document.createElement('div');
    overlay.id = 'mirrorManagerOverlay';
    overlay.className = 'mm-overlay';
    overlay.onclick = function(e) { if (e.target === overlay) _closeMirrorManager(); };
    overlay.innerHTML = `
        <div class="mm-modal">
            <div class="mm-header">
                <span class="mm-title">Manage Mirrors</span>
                <button class="mm-close" onclick="_closeMirrorManager()">&times;</button>
            </div>
            <p class="mm-explain">A mirror reflects your daily tuning through another canon. Add or remove mirrors below.</p>
            <div class="mm-list">${mirrorItems}</div>
        </div>`;
    document.body.appendChild(overlay);
}

function _closeMirrorManager() {
    const el = document.getElementById('mirrorManagerOverlay');
    if (el) el.remove();
}

async function _mirrorManagerToggle(mirrorId, add) {
    // Update mirror_library (personal list) — separate from mirror_sources (active/checked)
    const libRaw = MC.features?.mirror_library || '';
    let library = _parseMirrorCSV(libRaw);

    const srcRaw = MC.features?.mirror_sources || '';
    let sources = _parseMirrorCSV(srcRaw);

    if (add) {
        if (!library.includes(mirrorId)) library.push(mirrorId);
        if (!sources.includes(mirrorId)) sources.push(mirrorId);
    } else {
        library = library.filter(s => s !== mirrorId);
        sources = sources.filter(s => s !== mirrorId);
    }

    const newLibrary = library.join(',');
    const newSources = sources.join(',');
    const mirrorOn = sources.length > 0;

    const saveData = {
        mirror_library: newLibrary,
        mirror_sources: newSources,
        mirror: mirrorOn,
    };

    try {
        const res = await fetch('/api/settings/features', {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(saveData),
        });
        const data = await res.json();
        if (data.ok) {
            MC.features.mirror_library = newLibrary;
            MC.features.mirror_sources = newSources;
            MC.features.mirror = mirrorOn;
            // Re-render modal + settings
            const overlay = document.getElementById('mirrorManagerOverlay');
            if (overlay) {
                _closeMirrorManager();
                _openMirrorManager();
            }
            loadSettingsFeatures();
        }
    } catch (e) {
        console.warn('[settings] Mirror manager toggle failed:', e.message);
    }
}

// ── Mirror drag-to-reorder ──────────────────────────────────────────────────
let _mirrorDragItem = null;

function _mirrorDragStart(e) {
    _mirrorDragItem = e.currentTarget;
    _mirrorDragItem.style.opacity = '0.5';
    e.dataTransfer.effectAllowed = 'move';
}

function _mirrorDragOver(e) {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    const target = e.currentTarget;
    if (target !== _mirrorDragItem && target.classList.contains('mirror-sort-item')) {
        target.style.borderColor = 'var(--accent)';
    }
}

function _mirrorDragEnd(e) {
    e.currentTarget.style.opacity = '1';
    // Clear all border highlights
    document.querySelectorAll('.mirror-sort-item').forEach(el => {
        el.style.borderColor = 'transparent';
    });
    _mirrorDragItem = null;
}

function _mirrorDrop(e) {
    e.preventDefault();
    const target = e.currentTarget;
    if (!_mirrorDragItem || target === _mirrorDragItem) return;

    const list = document.getElementById('mirror-sort-list');
    if (!list) return;

    // Determine position: insert before or after
    const items = [...list.querySelectorAll('.mirror-sort-item')];
    const dragIdx = items.indexOf(_mirrorDragItem);
    const dropIdx = items.indexOf(target);

    if (dragIdx < dropIdx) {
        list.insertBefore(_mirrorDragItem, target.nextSibling);
    } else {
        list.insertBefore(_mirrorDragItem, target);
    }

    // Save new order — read checked items in DOM order
    const newItems = [...list.querySelectorAll('.mirror-sort-item')];
    const newSources = newItems
        .filter(el => el.querySelector('input[type="checkbox"]')?.checked)
        .map(el => el.dataset.mirrorId);

    if (newSources.length > 0) {
        _saveMirrorSources(newSources.join(','));
    }

    target.style.borderColor = 'transparent';
}

// ── Touch support for mobile drag-to-reorder ────────────────────────────────
let _touchDragItem = null;
let _touchStartY = 0;

function _initMirrorTouchDrag() {
    const list = document.getElementById('mirror-sort-list');
    if (!list) return;

    list.addEventListener('touchstart', function(e) {
        const handle = e.target.closest('.mirror-drag-handle');
        if (!handle) return;
        e.preventDefault(); // Prevent browser from claiming this as a scroll gesture
        _touchDragItem = handle.closest('.mirror-sort-item');
        _touchStartY = e.touches[0].clientY;
        _touchDragItem.style.opacity = '0.5';
    }, { passive: false });

    list.addEventListener('touchmove', function(e) {
        if (!_touchDragItem) return;
        e.preventDefault();
        const touchY = e.touches[0].clientY;
        const items = [...list.querySelectorAll('.mirror-sort-item')];
        for (const item of items) {
            if (item === _touchDragItem) continue;
            const rect = item.getBoundingClientRect();
            const mid = rect.top + rect.height / 2;
            if (touchY < mid && item.nextElementSibling === _touchDragItem) {
                // Dragging up: touch above midpoint, drag item is after this item
                list.insertBefore(_touchDragItem, item);
                break;
            } else if (touchY > mid && item.previousElementSibling === _touchDragItem) {
                // Dragging down: touch below midpoint, drag item is before this item
                list.insertBefore(_touchDragItem, item.nextSibling);
                break;
            }
        }
    }, { passive: false });

    list.addEventListener('touchend', function() {
        if (!_touchDragItem) return;
        _touchDragItem.style.opacity = '1';
        _touchDragItem = null;
        // Save new order
        const items = [...list.querySelectorAll('.mirror-sort-item')];
        const newSources = items
            .filter(el => el.querySelector('input[type="checkbox"]')?.checked)
            .map(el => el.dataset.mirrorId);
        if (newSources.length > 0) _saveMirrorSources(newSources.join(','));
    });
}

async function _saveMirrorSources(newValue) {
    try {
        const res = await fetch('/api/settings/features', {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mirror_sources: newValue }),
        });
        const data = await res.json();
        if (data.ok) {
            MC.features.mirror_sources = newValue;
        }
    } catch (e) {
        console.warn('[settings] Mirror reorder save failed:', e.message);
    }
}
