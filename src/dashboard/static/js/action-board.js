// =============================================================================
// Action Board — Actions, Links, Flows, Tools
// =============================================================================
// Loaded as a tab script by core.js. Provides loadABActions(), loadABLinks(),
// loadABFlows(), loadABTools() which are called by switchToTab().
// =============================================================================

let _abActionsLoaded = false;
let _abLinksLoaded = false;
let _abFlowsLoaded = false;
let _abToolsLoaded = false;
const _taskCache = {};
const _scheduledCache = {};
const _historyCache = {};
// #VP-HIST1: history is on-demand (not cold-shell). Cache rows after first open.
let _historyLoaded = false;
let _historyLoading = false;
let _historyItems = [];
let _historyHasMore = false;
let _historyOffset = 0;
const _HISTORY_PAGE = 50;

// =============================================================================
// Actions tab — individual items from the queue
// =============================================================================

async function loadABActions() {
    if (_abActionsLoaded) return;
    const container = document.getElementById('ab-actions-list');
    if (!container) return;

    try {
        // #VP-HIST1 / #PERF-MC1: drafts + scheduled only on first paint.
        // History loads when the History subtab is opened (or restored).
        const [actRes, schedRes] = await Promise.all([
            fetch('/api/action-board/actions'),
            fetch('/api/action-board/scheduled'),
        ]);
        const actData = actRes.ok ? await actRes.json() : { actions: [] };
        const schedData = schedRes.ok ? await schedRes.json() : { scheduled: [] };
        renderActions(container, actData.actions || [], schedData.scheduled || []);
        // If operator was already on History this session, rehydrate after paint.
        if (_actActiveSubtab.social === 'history') {
            loadHistorySubtab({ reset: true });
        }
    } catch (e) {
        container.innerHTML = '<div class="ab-empty">Could not load actions.</div>';
    }
    _abActionsLoaded = true;
}

// Refresh on visibility return (back from flow page, tab switch)
document.addEventListener('visibilitychange', () => {
    if (!document.hidden && !document.getElementById('ab-flow-overlay')) {
        refreshActions();
    }
});

// Refresh when an embedded flow (in an iframe overlay) signals new queue items —
// e.g. the video pipeline just enqueued social_queue drafts. Without this the
// Actions board stays on its cached pre-processing state and the new cards never
// show until a full page reload. invalidate the cache always, refresh if visible.
window.addEventListener('message', (e) => {
    if (e.data && e.data.type === 'cove:social-queued') {
        _abActionsLoaded = false;
        if (!document.hidden) refreshActions();
    }
});

function refreshActions() {
    _abActionsLoaded = false;
    // Keep history cache unless operator force-refreshes while on History
    // (loadHistorySubtab reset happens when History subtab is re-opened after refresh).
    if (_actActiveSubtab.social === 'history') {
        _historyLoaded = false;
        _historyItems = [];
        _historyOffset = 0;
    }
    loadABActions();
}

function renderActions(container, actions, scheduled) {
    actions = actions || [];
    scheduled = scheduled || [];

    // ── Build tabs from all items ──────────────────────
    // Tab definitions: id, label, items, subtabs (optional)
    // #VP-HIST1: Social Posts always present so History is one click away
    // even on a published-only day (no drafts/scheduled).
    const tabs = [];

    // Wizard/Continue Setup items
    const wizardItems = actions.filter(a => a.category === 'wizard');
    if (wizardItems.length) {
        tabs.push({ id: 'setup', label: '🔧 Continue Setup', count: wizardItems.length, items: wizardItems });
    }

    // Social Posts — parent tab with sub-tabs
    // History is always present (lazy-filled). Other subtabs only when non-empty.
    const socialCats = {
        'scheduled': { label: '📅 Scheduled', items: scheduled },
        'youtube-short': { label: '📺 YouTube', items: actions.filter(a => a.category === 'youtube-short') },
        'tiktok': { label: '🎵 TikTok', items: actions.filter(a => a.category === 'tiktok') },
        'youtube-studio': { label: '🎬 Studio', items: actions.filter(a => a.category === 'youtube-studio') },
        'x-post': { label: '𝕏 X', items: actions.filter(a => a.category === 'x-post') },
        'instagram': { label: '📸 Insta', items: actions.filter(a => a.category === 'instagram') },
        'facebook': { label: '📘 FB', items: actions.filter(a => a.category === 'facebook') },
    };
    const socialSubs = Object.entries(socialCats)
        .filter(([_, v]) => v.items.length > 0)
        .map(([id, v]) => ({ id, label: v.label, count: v.items.length, items: v.items }));
    // Always append History (count unknown until lazy load)
    const histCount = _historyLoaded ? _historyItems.length : null;
    socialSubs.push({
        id: 'history',
        label: '🗂 History',
        count: histCount == null ? '…' : histCount,
        items: _historyItems || [],
        lazy: true,
    });
    const socialTotal = socialSubs.reduce((n, s) => n + (typeof s.count === 'number' ? s.count : 0), 0);
    tabs.push({
        id: 'social',
        label: '📡 Social Posts',
        count: socialTotal > 0 ? socialTotal : (histCount || 0),
        subtabs: socialSubs,
        showLegend: true,
    });

    // Other categories (tasks, internal, etc.)
    const handled = new Set(['wizard', 'youtube-short', 'youtube-studio', 'x-post', 'instagram', 'facebook', 'tiktok']);
    const otherItems = actions.filter(a => !handled.has(a.category));
    if (otherItems.length) {
        tabs.push({ id: 'other', label: '📋 Other', count: otherItems.length, items: otherItems });
    }

    // ── Render tab bar ──────────────────────────────────
    const tabBtns = tabs.map((t, i) =>
        `<button class="ab-act-tab${i === 0 ? ' active' : ''}" data-tab="${t.id}" onclick="_switchActTab('${t.id}')">${t.label} <span class="ab-act-tab-count">${t.count}</span></button>`
    ).join('');

    let html = `<div class="ab-act-tabs">${tabBtns}</div>`;

    // ── Render tab panels ───────────────────────────────
    tabs.forEach((tab, i) => {
        const display = i === 0 ? '' : 'display:none;';
        html += `<div class="ab-act-panel" id="ab-act-panel-${tab.id}" style="${display}">`;

        if (tab.showLegend) {
            html += _socialBoardLegendHtml();
        }

        if (tab.subtabs) {
            // Sub-tab bar
            const subBtns = tab.subtabs.map((s, j) =>
                `<button class="ab-act-subtab${j === 0 ? ' active' : ''}" data-subtab="${s.id}" onclick="_switchActSubtab('${tab.id}', '${s.id}')">${s.label} <span class="ab-act-tab-count">${s.count}</span></button>`
            ).join('');
            html += `<div class="ab-act-subtabs">${subBtns}</div>`;

            tab.subtabs.forEach((sub, j) => {
                const subDisplay = j === 0 ? '' : 'display:none;';
                html += `<div class="ab-act-subpanel" id="ab-act-sub-${tab.id}-${sub.id}" style="${subDisplay}">`;
                if (sub.id === 'scheduled') {
                    html += _renderScheduledCards(sub.items);
                } else if (sub.id === 'history') {
                    html += _renderHistoryShell(sub.items);
                } else {
                    html += _renderActionCards(sub.items);
                }
                html += '</div>';
            });
        } else {
            html += _renderActionCards(tab.items);
        }

        html += '</div>';
    });

    container.innerHTML = html;

    // Restore previously active tab/subtab after re-render
    _restoreActTabs();

    // #VP-HIST1: if History is the visible default subtab (e.g. no drafts today),
    // kick the lazy fetch once so the operator is not stuck on a placeholder.
    const histPanel = document.getElementById('ab-act-sub-social-history');
    if (histPanel && histPanel.style.display !== 'none' && !_historyLoaded && !_historyLoading) {
        loadHistorySubtab({ reset: true });
    }
}

// Track active tab/subtab so they survive refreshes and overlay close
let _actActiveTab = null;
let _actActiveSubtab = {};  // parentId → subId

function _switchActTab(tabId) {
    _actActiveTab = tabId;
    document.querySelectorAll('.ab-act-tab').forEach(b => b.classList.toggle('active', b.dataset.tab === tabId));
    document.querySelectorAll('.ab-act-panel').forEach(p => p.style.display = p.id === `ab-act-panel-${tabId}` ? '' : 'none');
}

function _switchActSubtab(parentId, subId) {
    _actActiveSubtab[parentId] = subId;
    const panel = document.getElementById(`ab-act-panel-${parentId}`);
    if (!panel) return;
    panel.querySelectorAll('.ab-act-subtab').forEach(b => b.classList.toggle('active', b.dataset.subtab === subId));
    panel.querySelectorAll('.ab-act-subpanel').forEach(p => p.style.display = p.id === `ab-act-sub-${parentId}-${subId}` ? '' : 'none');
    // #VP-HIST1: fetch published list only when History is opened
    if (parentId === 'social' && subId === 'history') {
        loadHistorySubtab({ reset: !_historyLoaded });
    }
}

function _restoreActTabs() {
    if (_actActiveTab) _switchActTab(_actActiveTab);
    for (const [parentId, subId] of Object.entries(_actActiveSubtab)) {
        _switchActSubtab(parentId, subId);
    }
}

function _openWizardAtStep(toolId, param, stepId, stepPage) {
    // If the step has its own page URL (from wizard provider), use it directly.
    // Otherwise fall back to FLOW_PAGES lookup with step= param (legacy).
    let url;
    if (stepPage) {
        url = stepPage;
    } else {
        const toolUrl = FLOW_PAGES[toolId] || '';
        if (!toolUrl) return;
        url = toolUrl + (toolUrl.includes('?') ? '&' : '?') + param + '&step=' + stepId;
    }
    openFlowOverlay(url, 'ab-actions', toolId);
}

/** Classify a social/scheduled card for board shading + legend.
 *  post_mode: api (auto upload) vs paste (manual).
 *  length_class: short vs long.
 */
function _cardPostClass(item) {
    if (!item) return { mode: '', length: '', classes: '' };
    let mode = (item.post_mode || '').toLowerCase();
    let length = (item.length_class || '').toLowerCase();
    const plat = (item.platform || item.source || '').toLowerCase();
    const dur = Number(item.duration_seconds || item.duration || 0);
    const clip = (item.clip_type || '').toLowerCase();
    const fmt = (item.format || '').toLowerCase();

    if (!mode) {
        if (item.type === 'youtube-short' || plat === 'youtube' || plat.includes('youtube')) {
            mode = 'api';
        } else if (plat === 'x' || plat.includes('x-post') || plat === 'social-x') {
            const isLong = clip === 'full' || dur > 140;
            mode = isLong ? 'paste' : 'api';
        } else if (plat && (plat.includes('tiktok') || plat.includes('instagram') || plat.includes('facebook'))) {
            mode = 'paste';
        }
    }
    if (!length) {
        if (item.is_short === true) length = 'short';
        else if (item.is_short === false) length = 'long';
        else if (clip === 'full' || fmt === 'horizontal' || dur > 140) length = 'long';
        else if (item.type === 'youtube-short' || fmt === 'vertical' || fmt === 'square' || dur > 0) length = 'short';
    }

    const classes = [
        mode === 'api' ? 'ab-post-api' : '',
        mode === 'paste' ? 'ab-post-paste' : '',
        length === 'short' ? 'ab-len-short' : '',
        length === 'long' ? 'ab-len-long' : '',
    ].filter(Boolean).join(' ');
    return { mode, length, classes };
}

function _postMetaChips(cls) {
    if (!cls.mode && !cls.length) return '';
    const chips = [];
    if (cls.mode === 'api') chips.push('<span class="ab-meta-chip ab-meta-api" title="API auto-post">API</span>');
    if (cls.mode === 'paste') chips.push('<span class="ab-meta-chip ab-meta-paste" title="Manual paste / Studio">Paste</span>');
    if (cls.length === 'short') chips.push('<span class="ab-meta-chip ab-meta-short" title="Short-form">Short</span>');
    if (cls.length === 'long') chips.push('<span class="ab-meta-chip ab-meta-long" title="Long-form">Long</span>');
    return chips.length ? `<span class="ab-meta-chips">${chips.join('')}</span>` : '';
}

/** Compact legend for Social Posts — API vs paste, short vs long. */
function _socialBoardLegendHtml() {
    return `<div class="ab-board-legend" role="note" aria-label="Card shading legend">
        <span class="ab-legend-title">Legend</span>
        <span class="ab-legend-item"><span class="ab-legend-swatch ab-post-api"></span> API auto</span>
        <span class="ab-legend-item"><span class="ab-legend-swatch ab-post-paste"></span> Paste / manual</span>
        <span class="ab-legend-item"><span class="ab-legend-swatch ab-len-short"></span> Short</span>
        <span class="ab-legend-item"><span class="ab-legend-swatch ab-len-long"></span> Long</span>
    </div>`;
}

function _renderActionCards(items) {
    return items.map(a => {
        const urgencyColors = { high: 'var(--red)', normal: 'var(--accent)', low: 'var(--green)' };
        const color = urgencyColors[a.urgency] || 'var(--dim)';
        const series = a.series ? `<span class="ab-action-series">${esc(a.series)}</span>` : '';
        const postCls = _cardPostClass(a);
        const metaChips = _postMetaChips(postCls);

        // Wizard-resume cards with step indicators (standardized system)
        // Supports both: default_page (new provider system) and tool_id (legacy)
        if (a.type === 'wizard-resume' && a.steps) {
            // Card click: use default_page if provided, else build from FLOW_PAGES
            let cardUrl = a.default_page || '';
            if (!cardUrl && a.tool_id) {
                const toolUrl = FLOW_PAGES[a.tool_id] || '';
                const param = a.tool_param || '';
                cardUrl = toolUrl + (toolUrl.includes('?') ? '&' : '?') + param;
            }

            const param = a.tool_param || '';
            const stepsHtml = a.steps.map(s => {
                if (s.done) {
                    return `<span class="ab-wz-step done" title="${esc(s.label)}">✓ ${esc(s.label)}</span>`;
                }
                // Pass step.page so _openWizardAtStep can route directly
                const stepPage = s.page ? esc(s.page) : '';
                return `<span class="ab-wz-step todo" title="Click to start ${esc(s.label)}"
                    onclick="event.stopPropagation(); _openWizardAtStep('${esc(a.tool_id || '')}','${esc(param)}','${esc(s.id)}','${stepPage}')">${esc(s.label)}</span>`;
            }).join('');

            return `
            <div class="ab-action-card ab-wizard-card" onclick="openFlowOverlay('${cardUrl}', 'ab-actions', '${esc(a.title)}')" style="cursor:pointer;">
                <div class="ab-action-urgency" style="background:${color}"></div>
                <div class="ab-action-info">
                    <div class="ab-action-title">${esc(a.title)}</div>
                    <div class="ab-wz-steps">${stepsHtml}</div>
                </div>
            </div>`;
        }

        let clickAttr = '';
        if (a.type === 'wizard-resume' && (a.default_page || a.tool_id)) {
            let url = a.default_page || '';
            if (!url && a.tool_id) {
                const toolUrl = FLOW_PAGES[a.tool_id] || '';
                const param = a.tool_param || '';
                url = toolUrl + (toolUrl.includes('?') ? '&' : '?') + param;
            }
            if (url) {
                clickAttr = `onclick="openFlowOverlay('${url}', 'ab-actions', '${esc(a.title)}')" style="cursor:pointer;"`;
            }
        } else if (a.type === 'social' && a.queue_id) {
            clickAttr = `onclick="openSocialDetail(${a.queue_id})" style="cursor:pointer;"`;
        } else if (a.type === 'link' && a.url) {
            clickAttr = `onclick="window.open('${esc(a.url)}', '_blank')" style="cursor:pointer;"`;
        } else if (a.type === 'youtube-short' && a.queue_id) {
            clickAttr = `onclick="openActionDetail(${a.queue_id})" style="cursor:pointer;"`;
        } else if (a.type === 'task' && a.task_id) {
            _taskCache[a.task_id] = a;
            clickAttr = `onclick="openTaskDetail(${a.task_id})" style="cursor:pointer;"`;
        }

        return `
        <div class="ab-action-card ${postCls.classes}" data-id="${esc(a.id)}" data-post-mode="${esc(postCls.mode)}" data-length="${esc(postCls.length)}" ${clickAttr}>
            <div class="ab-action-urgency" style="background:${color}"></div>
            <div class="ab-action-info">
                <div class="ab-action-title">${esc(a.title)} ${metaChips}</div>
                <div class="ab-action-desc">${esc(a.description || '')} ${series}</div>
            </div>
            <span class="ab-action-status-badge ab-status-${esc(a.status || 'draft')}">${esc(a.status || '')}</span>
        </div>`;
    }).join('');
}

function _renderScheduledCards(items) {
    return items.map(s => {
        const statusColors = { queued: 'var(--yellow)', uploading: 'var(--accent)', uploaded: 'var(--green)', published: 'var(--green)' };
        const color = statusColors[s.status] || 'var(--dim)';
        const series = s.series ? `<span class="ab-action-series">${esc(s.series)}</span>` : '';
        const plat = s.platform || 'youtube';
        const platTag = plat === 'x' ? '<span class="ab-action-series">𝕏</span> '
            : (plat === 'youtube' ? '<span class="ab-action-series">YT</span> ' : '');
        const postCls = _cardPostClass(s);
        const metaChips = _postMetaChips(postCls);

        let ytLink = '';
        if (s.youtube_video_id) {
            const studioUrl = `https://studio.youtube.com/video/${s.youtube_video_id}/edit`;
            ytLink = `<a href="${esc(studioUrl)}" target="_blank" style="color:var(--accent);font-size:11px;margin-left:8px;" onclick="event.stopPropagation()">Studio ↗</a>`;
        }

        // Cache every scheduled row so cancel/open can tell youtube_queue vs social_queue
        _scheduledCache[`${plat}:${s.id}`] = s;
        _scheduledCache[s.id] = s; // back-compat for YT-only callers

        let clickAttr = '';
        if (plat === 'x' && (s.status === 'queued' || s.status === 'uploading')) {
            clickAttr = `onclick="openSocialDetail(${s.id})" style="cursor:pointer;"`;
        } else if (s.status === 'queued') {
            clickAttr = `onclick="openActionDetail(${s.id})" style="cursor:pointer;"`;
        } else if (s.status === 'uploaded') {
            clickAttr = `onclick="openUploadedDetail(${s.id})" style="cursor:pointer;"`;
        }

        const err = s.error_message ? ` · ${esc(s.error_message).slice(0, 80)}` : '';

        return `
        <div class="ab-action-card ab-scheduled-card ${postCls.classes}" data-post-mode="${esc(postCls.mode)}" data-length="${esc(postCls.length)}" ${clickAttr}>
            <div class="ab-action-urgency" style="background:${color}"></div>
            <div class="ab-action-info">
                <div class="ab-action-title">${platTag}${esc(s.title)}${ytLink} ${metaChips}</div>
                <div class="ab-action-desc">${esc(s.subtitle)} ${series}${err}</div>
            </div>
            <span class="ab-action-status-badge ab-status-${esc(s.status)}">${esc(s.status)}</span>
        </div>`;
    }).join('');
}

async function cancelScheduled(queueId) {
    if (!confirm('Cancel this scheduled post? It will return to draft.')) return;
    const cached = _scheduledCache[queueId] || _scheduledCache[`youtube:${queueId}`] || _scheduledCache[`x:${queueId}`];
    const plat = (cached && cached.platform) || 'youtube';
    try {
        if (plat === 'x') {
            await fetch(`/api/action-board/social/${queueId}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ status: 'draft' }),
            });
        } else {
            await fetch(`/api/youtube/queue/${queueId}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ status: 'draft' }),
            });
        }
        // Close overlay if open, then refresh
        if (document.getElementById('ab-flow-overlay')) closeFlowOverlay();
        else refreshActions();
    } catch (e) {
        console.error('Cancel failed:', e);
    }
}

// =============================================================================
// #VP-HIST1 — Published history (lazy subtab)
// =============================================================================

function _renderHistoryShell(items) {
    const list = items || [];
    if (!_historyLoaded && !_historyLoading && list.length === 0) {
        return `<div class="ab-empty ab-history-empty" id="ab-history-body">
            Open History to load published posts (newest first).
            <div style="margin-top:8px;">
                <button type="button" class="ab-btn" onclick="loadHistorySubtab({reset:true})">Load history</button>
            </div>
        </div>`;
    }
    if (_historyLoading && list.length === 0) {
        return `<div class="ab-empty" id="ab-history-body">Loading published posts…</div>`;
    }
    if (_historyLoaded && list.length === 0) {
        return `<div class="ab-empty" id="ab-history-body">No published posts yet.</div>`;
    }
    return `<div id="ab-history-body">${_renderHistoryCards(list)}${_historyMoreHtml()}</div>`;
}

function _historyMoreHtml() {
    if (!_historyHasMore) return '';
    return `<div class="ab-history-more" style="padding:12px;text-align:center;">
        <button type="button" class="ab-btn" onclick="loadHistorySubtab({reset:false})"
            ${_historyLoading ? 'disabled' : ''}>
            ${_historyLoading ? 'Loading…' : 'Load more'}
        </button>
    </div>`;
}

function _renderHistoryCards(items) {
    return (items || []).map(h => {
        const plat = h.platform || 'youtube';
        const platTag = plat === 'x'
            ? '<span class="ab-action-series">𝕏</span> '
            : (plat === 'youtube' ? '<span class="ab-action-series">YT</span> ' : '');
        const postCls = _cardPostClass(h);
        const metaChips = _postMetaChips(postCls);
        const watch = h.watch_url || h.youtube_url || '';
        const studio = h.studio_url || (
            h.youtube_video_id
                ? `https://studio.youtube.com/video/${h.youtube_video_id}/edit`
                : ''
        );
        let links = '';
        if (watch) {
            links += `<a href="${esc(watch)}" target="_blank" rel="noopener"
                style="color:var(--accent);font-size:11px;margin-left:8px;"
                onclick="event.stopPropagation()">Watch ↗</a>`;
        }
        if (studio) {
            links += `<a href="${esc(studio)}" target="_blank" rel="noopener"
                style="color:var(--accent);font-size:11px;margin-left:8px;"
                onclick="event.stopPropagation()">Studio ↗</a>`;
        }
        const key = `${plat}:${h.id}`;
        _historyCache[key] = h;
        _historyCache[h.id] = h;

        const clickAttr = watch
            ? `onclick="window.open('${esc(watch)}', '_blank')" style="cursor:pointer;"`
            : '';

        return `
        <div class="ab-action-card ab-history-card ${postCls.classes}"
             data-post-mode="${esc(postCls.mode)}" data-length="${esc(postCls.length)}"
             data-history-id="${esc(String(h.id))}" data-platform="${esc(plat)}" ${clickAttr}>
            <div class="ab-action-urgency" style="background:var(--green)"></div>
            <div class="ab-action-info">
                <div class="ab-action-title">${platTag}${esc(h.title || '')}${links} ${metaChips}</div>
                <div class="ab-action-desc">${esc(h.subtitle || '')} ${
                    h.series ? `<span class="ab-action-series">${esc(h.series)}</span>` : ''
                }</div>
            </div>
            <span class="ab-action-status-badge ab-status-published">${esc(h.status || 'published')}</span>
        </div>`;
    }).join('');
}

function _paintHistoryPanel() {
    const body = document.getElementById('ab-history-body');
    if (body) {
        if (!_historyItems.length) {
            body.className = 'ab-empty';
            body.innerHTML = _historyLoaded ? 'No published posts yet.' : 'Loading published posts…';
        } else {
            body.className = '';
            body.innerHTML = _renderHistoryCards(_historyItems) + _historyMoreHtml();
        }
    }
    // Update History subtab count badge if present
    const btn = document.querySelector('.ab-act-subtab[data-subtab="history"] .ab-act-tab-count');
    if (btn && _historyLoaded) btn.textContent = String(_historyItems.length);
}

async function loadHistorySubtab(opts) {
    const reset = !opts || opts.reset !== false;
    if (_historyLoading) return;
    if (!reset && !_historyHasMore) return;

    if (reset) {
        _historyOffset = 0;
        _historyItems = [];
        _historyHasMore = false;
        _historyLoaded = false;
    }

    _historyLoading = true;
    _paintHistoryPanel();

    try {
        const res = await fetch(
            `/api/action-board/history?limit=${_HISTORY_PAGE}&offset=${_historyOffset}`
        );
        const data = res.ok ? await res.json() : { history: [], has_more: false };
        const batch = data.history || [];
        if (reset) _historyItems = batch;
        else _historyItems = _historyItems.concat(batch);
        _historyHasMore = !!data.has_more;
        _historyOffset = _historyItems.length;
        _historyLoaded = true;
    } catch (e) {
        console.error('History load failed:', e);
        _historyLoaded = true;
        if (!_historyItems.length) {
            const body = document.getElementById('ab-history-body');
            if (body) {
                body.className = 'ab-empty';
                body.innerHTML = 'Could not load history.';
            }
            _historyLoading = false;
            return;
        }
    }
    _historyLoading = false;
    _paintHistoryPanel();
}

// =============================================================================
// Action detail overlay — single item editor
// =============================================================================

async function openActionDetail(queueId) {
    // Fetch full item detail
    let item;
    try {
        const res = await fetch(`/api/action-board/actions/${queueId}`);
        if (!res.ok) throw new Error(`${res.status}`);
        item = await res.json();
    } catch (e) {
        console.error('Failed to load action detail:', e);
        return;
    }

    closeFlowOverlay();

    const panel = document.getElementById('panel-container');
    if (!panel) return;

    const overlay = document.createElement('div');
    overlay.id = 'ab-flow-overlay';
    overlay.className = 'ab-flow-embedded';
    overlay.setAttribute('data-return-tab', 'ab-actions');

    // Format dates for datetime-local inputs
    // If date is a placeholder (year >= 2098), show sensible defaults instead
    const fmtDT = (iso, defaultHour, defaultDaysOffset) => {
        if (!iso) return '';
        const d = new Date(iso);
        if (d.getFullYear() >= 2098) {
            // Default: N days from now at the specified hour
            const target = new Date();
            target.setDate(target.getDate() + (defaultDaysOffset || 1));
            const yyyy = target.getFullYear();
            const mm = String(target.getMonth() + 1).padStart(2, '0');
            const dd = String(target.getDate()).padStart(2, '0');
            const hh = String(defaultHour || 10).padStart(2, '0');
            return `${yyyy}-${mm}-${dd}T${hh}:00`;
        }
        return isoToLocalInput(iso);
    };

    const tagsStr = Array.isArray(item.tags) ? item.tags.join(', ') : (item.tags || '');

    overlay.innerHTML = `
        <div class="ab-flow-topbar">
            <button class="ab-flow-back" onclick="closeFlowOverlay()">← Back to Actions</button>
            <span class="ab-flow-title">📺 YouTube Short</span>
        </div>
        <div class="ab-detail-scroll">
            <div class="ab-detail-content">
                <div class="ab-detail-title-row">
                    <h2 class="ab-detail-title">${esc(item.title)}</h2>
                    <span class="ab-action-status-badge ab-status-${esc(item.status)}">${esc(item.status)}</span>
                </div>

                <div class="ab-detail-field">
                    <label>File</label>
                    <div class="ab-detail-filepath">${esc(item.file_path)}</div>
                </div>

                <div class="ab-detail-field">
                    <label>Title</label>
                    <textarea id="ad-title" class="ab-detail-input" rows="2">${esc(item.title)}</textarea>
                </div>

                <div class="ab-detail-field">
                    <label>Description</label>
                    <textarea id="ad-desc" class="ab-detail-input ab-detail-desc" rows="8">${esc(item.description)}</textarea>
                </div>

                <div class="ab-detail-row">
                    <div class="ab-detail-field ab-detail-half">
                        <label>Hashtags</label>
                        <textarea id="ad-hashtags" class="ab-detail-input" rows="3">${esc(item.hashtags)}</textarea>
                    </div>
                    <div class="ab-detail-field ab-detail-half">
                        <label>Tags</label>
                        <textarea id="ad-tags" class="ab-detail-input" rows="3">${esc(tagsStr)}</textarea>
                    </div>
                </div>

                <div class="ab-detail-field">
                    <label>Related Video</label>
                    <input type="text" id="ad-related" class="ab-detail-input" value="${esc(item.related_video || '')}" placeholder="Long-form video URL to link on this short (Studio-only)">
                </div>

                <div class="ab-detail-row">
                    <div class="ab-detail-field ab-detail-half">
                        <label>Upload to YouTube (private)</label>
                        <input type="datetime-local" id="ad-upload-date" class="ab-detail-input" value="${fmtDT(item.upload_date, 10, 1)}">
                    </div>
                    <div class="ab-detail-field ab-detail-half">
                        <label>Go Public</label>
                        <input type="datetime-local" id="ad-publish-date" class="ab-detail-input" value="${fmtDT(item.publish_date, 11, 3)}">
                    </div>
                </div>

                <div class="ab-detail-actions" id="ad-actions">
                    ${item.status === 'queued' ? `
                        <button class="ab-btn ab-btn-schedule" onclick="scheduleAction(${item.id})">Update Schedule</button>
                        <button class="ab-btn ab-btn-upload" onclick="uploadNow(${item.id})" style="border:1px solid var(--accent);color:var(--accent);background:transparent;">Upload Now</button>
                        <button class="ab-btn" onclick="cancelScheduled(${item.id})" style="border:1px solid var(--red);color:var(--red);background:transparent;">Cancel Schedule</button>
                    ` : `
                        <button class="ab-btn ab-btn-schedule" onclick="scheduleAction(${item.id})">Schedule</button>
                        <button class="ab-btn ab-btn-upload" onclick="uploadNow(${item.id})" style="border:1px solid var(--accent);color:var(--accent);background:transparent;">Upload Now</button>
                        <button class="ab-btn ab-btn-done" onclick="markActionDone(${item.id})">Mark Done</button>
                    `}
                    <div class="ab-detail-feedback" id="ad-feedback"></div>
                </div>
            </div>
        </div>`;

    panel.appendChild(overlay);
}

async function scheduleAction(queueId) {
    const feedback = document.getElementById('ad-feedback');
    const btn = document.querySelector('.ab-btn-schedule');
    if (btn) { btn.textContent = 'Scheduling...'; btn.disabled = true; }

    const body = {
        title: document.getElementById('ad-title')?.value,
        description: document.getElementById('ad-desc')?.value,
        hashtags: document.getElementById('ad-hashtags')?.value,
        tags: (document.getElementById('ad-tags')?.value || '').split(',').map(t => t.trim()).filter(Boolean),
        related_video: document.getElementById('ad-related')?.value || null,
        upload_date: localInputToISO(document.getElementById('ad-upload-date')?.value),
        publish_date: localInputToISO(document.getElementById('ad-publish-date')?.value),
        status: 'queued',
    };

    if (!body.upload_date || !body.publish_date) {
        if (feedback) feedback.textContent = 'Set both upload and publish dates.';
        if (btn) { btn.textContent = 'Schedule via API'; btn.disabled = false; }
        return;
    }

    try {
        const res = await fetch(`/api/youtube/queue/${queueId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!res.ok) throw new Error(`${res.status}`);
        const data = await res.json();

        if (feedback) feedback.innerHTML = `<span style="color:var(--green)">Queued ✓ — uploads ${body.upload_date}, public ${body.publish_date}</span>`;
        if (btn) { btn.textContent = 'Scheduled ✓'; btn.style.background = 'var(--green)'; }

        // Auto-close after 1.5s
        setTimeout(() => closeFlowOverlay(), 1500);
    } catch (e) {
        if (feedback) feedback.innerHTML = `<span style="color:var(--red)">Error: ${e.message}</span>`;
        if (btn) { btn.textContent = 'Schedule via API'; btn.disabled = false; }
    }
}

async function uploadNow(queueId) {
    const feedback = document.getElementById('ad-feedback');
    const btn = document.querySelector('.ab-btn-upload');
    if (btn) { btn.textContent = 'Uploading...'; btn.disabled = true; }
    if (feedback) feedback.innerHTML = '<span style="color:var(--yellow)">Saving + uploading to YouTube...</span>';

    // First save any edits (same as schedule but keep status as queued with upload_date = now)
    const body = {
        title: document.getElementById('ad-title')?.value,
        description: document.getElementById('ad-desc')?.value,
        hashtags: document.getElementById('ad-hashtags')?.value,
        tags: (document.getElementById('ad-tags')?.value || '').split(',').map(t => t.trim()).filter(Boolean),
        related_video: document.getElementById('ad-related')?.value || null,
        upload_date: new Date().toISOString(),
        publish_date: localInputToISO(document.getElementById('ad-publish-date')?.value) || new Date().toISOString(),
        status: 'queued',
    };

    try {
        // Save to queue with immediate upload_date
        const saveRes = await fetch(`/api/youtube/queue/${queueId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!saveRes.ok) throw new Error(`Save failed: ${saveRes.status}`);

        // Trigger queue processor
        if (feedback) feedback.innerHTML = '<span style="color:var(--yellow)">Uploading to YouTube... this may take a minute.</span>';
        const procRes = await fetch('/api/youtube/process-queue', { method: 'POST' });
        if (!procRes.ok) throw new Error(`Upload failed: ${procRes.status}`);
        const data = await procRes.json();

        const result = data.results?.find(r => r.id === queueId);
        if (result && result.status === 'uploaded') {
            if (feedback) feedback.innerHTML = `<span style="color:var(--green)">Uploaded ✓ — ${result.video_id}</span>`;
            if (btn) { btn.textContent = 'Uploaded ✓'; btn.style.borderColor = 'var(--green)'; btn.style.color = 'var(--green)'; }
        } else if (result && result.error) {
            throw new Error(result.error);
        } else {
            if (feedback) feedback.innerHTML = '<span style="color:var(--yellow)">Processed — check status</span>';
        }

        setTimeout(() => closeFlowOverlay(), 2000);
    } catch (e) {
        if (feedback) feedback.innerHTML = `<span style="color:var(--red)">Error: ${e.message}</span>`;
        if (btn) { btn.textContent = 'Upload Now'; btn.disabled = false; }
    }
}

async function markActionDone(queueId) {
    try {
        await fetch(`/api/youtube/queue/${queueId}`, {
            method: 'DELETE',
        });
    } catch (e) {
        console.error('Failed to mark done:', e);
    }
    closeFlowOverlay();
}

// =============================================================================
// Social queue detail overlay — multi-platform moment scheduling
// =============================================================================

const _platformLabels = { youtube: '📺 YouTube', tiktok: '🎵 TikTok', x: '𝕏 X', instagram: '📸 Instagram', facebook: '📘 Facebook' };

async function openSocialDetail(itemId) {
    let item;
    try {
        const res = await fetch(`/api/action-board/social/${itemId}`);
        if (!res.ok) throw new Error(`${res.status}`);
        item = await res.json();
    } catch (e) {
        console.error('Failed to load social detail:', e);
        return;
    }

    closeFlowOverlay();
    const panel = document.getElementById('panel-container');
    if (!panel) return;

    const overlay = document.createElement('div');
    overlay.id = 'ab-flow-overlay';
    overlay.className = 'ab-flow-embedded';
    overlay.setAttribute('data-return-tab', 'ab-actions');

    const platLabel = _platformLabels[item.platform] || item.platform;
    const tagsStr = Array.isArray(item.tags) ? item.tags.join(', ') : (item.tags || '');
    const dur = item.duration_seconds || 0;
    const durLabel = dur < 60 ? `${Math.round(dur)}s` : `${Math.floor(dur/60)}m ${Math.round(dur%60)}s`;
    const previewUrl = item.preview_file ? `/api/video/proxy/stream?filename=${item.preview_file}` : '';

    // API auto-post exists for: YouTube (all), X (clips ≤140s, not full-length).
    // Everything else (TikTok, Instagram, Facebook, X full-length) uses the
    // manual flow: copy caption + download video + post natively + mark posted.
    const isXLong = item.platform === 'x' && (item.clip_type === 'full' || dur > 140);
    const isApiAuto = item.platform === 'youtube' || (item.platform === 'x' && !isXLong);
    const fullFileName = (item.file_path || '').split('/').pop();
    // Standard provisioned path — same on every Operator's computer via NC sync.
    // file_path is an NC path like "AgentSkills/Content/video/shorts/x.mp4".
    // NC_SYNC_ROOT is the standard desktop sync location; ~ expands in
    // Finder's Go to Folder (Cmd+Shift+G), which jumps straight to the file.
    const NC_SYNC_ROOT = '~/Documents/';
    const relSyncPath = item.file_path && item.file_path.startsWith('AgentSkills/')
        ? item.file_path : (fullFileName ? `AgentSkills/Content/video/shorts/${fullFileName}` : '');
    const localSyncPath = relSyncPath ? NC_SYNC_ROOT + relSyncPath : '';
    // NC web folder — derive this Cove's NC host from the current hostname
    // (agent.cove.domain → cloud.cove.domain). Replicable across Coves.
    const ncHost = location.hostname.split('.').map((p, i) => i === 0 ? 'cloud' : p).join('.');
    const ncFolderUrl = `https://${ncHost}/apps/files/?dir=/AgentSkills/Content/video/shorts`;

    const fmtDT = (iso, defaultHour, defaultDaysOffset) => {
        if (!iso) {
            const target = new Date();
            target.setDate(target.getDate() + (defaultDaysOffset || 1));
            const yyyy = target.getFullYear();
            const mm = String(target.getMonth() + 1).padStart(2, '0');
            const dd = String(target.getDate()).padStart(2, '0');
            const hh = String(defaultHour || 10).padStart(2, '0');
            return `${yyyy}-${mm}-${dd}T${hh}:00`;
        }
        return isoToLocalInput(iso);
    };

    overlay.innerHTML = `
        <div class="ab-flow-topbar">
            <button class="ab-flow-back" onclick="closeFlowOverlay()">← Back to Actions</button>
            <span class="ab-flow-title">${platLabel} · ${esc(item.clip_type || 'Moment')}</span>
            <span data-social-platform="${esc(item.platform || '')}" hidden></span>
        </div>
        <div class="ab-detail-scroll">
            <div class="ab-detail-content">
                <div class="ab-detail-title-row">
                    <h2 class="ab-detail-title">${esc(item.title)}</h2>
                    <span class="ab-action-status-badge ab-status-${esc(item.status)}">${esc(item.status)}</span>
                </div>

                <div style="display:flex;gap:12px;align-items:center;margin-bottom:16px;font-size:12px;color:var(--dim)">
                    <span>${esc(item.clip_type || '')} · ${durLabel} · ${{vertical:'9:16',horizontal:'16:9',square:'1:1'}[item.format] || (item.is_vertical ? '9:16' : '16:9')}</span>
                    <span>Source: ${esc(item.source_stem || '')}</span>
                    ${previewUrl ? `<a href="${previewUrl}" target="_blank" style="color:var(--accent)">Preview ↗</a>` : ''}
                </div>

                <div class="ab-detail-field">
                    <label>Title</label>
                    <textarea id="sd-title" class="ab-detail-input" rows="2">${esc(item.title)}</textarea>
                </div>

                <div class="ab-detail-field">
                    <label>Description</label>
                    <textarea id="sd-desc" class="ab-detail-input ab-detail-desc" rows="8">${esc(item.description)}</textarea>
                </div>

                <div class="ab-detail-row">
                    <div class="ab-detail-field ab-detail-half">
                        <label>Hashtags</label>
                        <textarea id="sd-hashtags" class="ab-detail-input" rows="3">${esc(item.hashtags)}</textarea>
                    </div>
                    <div class="ab-detail-field ab-detail-half">
                        <label>Tags</label>
                        <textarea id="sd-tags" class="ab-detail-input" rows="3">${esc(tagsStr)}</textarea>
                    </div>
                </div>

                <div class="ab-detail-row">
                    <div class="ab-detail-field ab-detail-half">
                        <label>Upload Date</label>
                        <input type="datetime-local" id="sd-upload-date" class="ab-detail-input" value="${fmtDT(item.upload_date, 10, 1)}">
                    </div>
                    <div class="ab-detail-field ab-detail-half">
                        <label>Publish Date</label>
                        <input type="datetime-local" id="sd-publish-date" class="ab-detail-input" value="${fmtDT(item.publish_date, 11, 3)}">
                    </div>
                </div>

                ${isApiAuto ? `
                <div class="ab-detail-actions" id="sd-actions">
                    <button class="ab-btn ab-btn-schedule" onclick="scheduleSocial(${item.id})">Schedule</button>
                    <button class="ab-btn ab-btn-upload" onclick="publishSocialNow(${item.id})" style="border:1px solid var(--accent);color:var(--accent);background:transparent;">Publish Now</button>
                    <button class="ab-btn" onclick="cancelSocial(${item.id})" style="border:1px solid var(--red);color:var(--red);background:transparent;">Remove</button>
                    <div class="ab-detail-feedback" id="sd-feedback"></div>
                </div>` : `
                <div style="font-size:12px;color:var(--dim);margin-bottom:10px;padding:10px;border:1px solid var(--border,#333);border-radius:8px;">
                    Manual post${isXLong ? ' — over 2:20, upload natively on X (Premium)' : ` — no API auto-post for ${platLabel} yet`}.
                    Copy the caption, download the video, post it in the app, then mark posted.
                </div>
                ${localSyncPath ? `
                <div style="font-size:11px;color:var(--dim);margin-bottom:10px;font-family:monospace;word-break:break-all;">
                    ${esc(localSyncPath)}
                </div>` : ''}
                <div class="ab-detail-actions" id="sd-actions">
                    <button class="ab-btn" onclick="copySocialCaption()" style="border:1px solid var(--accent);color:var(--accent);background:transparent;">Copy Caption</button>
                    ${localSyncPath ? `<button class="ab-btn" onclick="copySocialFilePath('${esc(localSyncPath)}')" style="border:1px solid var(--accent);color:var(--accent);background:transparent;">Copy File Path</button>` : ''}
                    <a class="ab-btn" href="${ncFolderUrl}" target="_blank" style="border:1px solid var(--accent);color:var(--accent);background:transparent;text-decoration:none;display:inline-block;">Open Folder ↗</a>
                    <button class="ab-btn" onclick="markSocialPosted(${item.id})" style="border:1px solid var(--green,#4caf50);color:var(--green,#4caf50);background:transparent;">Mark Posted ✓</button>
                    <button class="ab-btn" onclick="cancelSocial(${item.id})" style="border:1px solid var(--red);color:var(--red);background:transparent;">Remove</button>
                    <div class="ab-detail-feedback" id="sd-feedback"></div>
                </div>`}
            </div>
        </div>`;

    panel.appendChild(overlay);
}

async function publishSocialNow(itemId) {
    const feedback = document.getElementById('sd-feedback');
    const btn = document.querySelector('.ab-btn-upload');
    if (btn) { btn.textContent = 'Publishing...'; btn.disabled = true; }

    // Platform from the open detail (data attr) or cache
    const platEl = document.querySelector('[data-social-platform]');
    const platform = platEl?.getAttribute('data-social-platform')
        || _scheduledCache[itemId]?.platform
        || 'x';

    // Save edits + set to queued with immediate dates
    const body = {
        title: document.getElementById('sd-title')?.value,
        description: document.getElementById('sd-desc')?.value,
        hashtags: document.getElementById('sd-hashtags')?.value,
        tags: (document.getElementById('sd-tags')?.value || '').split(',').map(t => t.trim()).filter(Boolean),
        upload_date: new Date().toISOString(),
        publish_date: new Date().toISOString(),
        status: 'queued',
    };

    try {
        const res = await fetch(`/api/action-board/social/${itemId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!res.ok) throw new Error(`${res.status}`);
        const saveData = await res.json().catch(() => ({}));

        // YouTube path: promote already mirrored into youtube_queue — kick processor.
        if (platform === 'youtube' || saveData.youtube_queue_id) {
            if (feedback) feedback.innerHTML = '<span style="color:var(--yellow)">Uploading to YouTube…</span>';
            const procRes = await fetch('/api/youtube/process-queue', { method: 'POST' });
            if (!procRes.ok) throw new Error(`YouTube process failed: ${procRes.status}`);
            if (feedback) feedback.innerHTML = '<span style="color:var(--green)">YouTube upload kicked ✓ — check Scheduled</span>';
            if (btn) { btn.textContent = 'Queued ✓'; btn.style.borderColor = 'var(--green)'; btn.style.color = 'var(--green)'; }
            setTimeout(() => closeFlowOverlay(), 1800);
            return;
        }

        // X path: social_queue is the uploader — process now (not wait 15m scheduler).
        if (feedback) feedback.innerHTML = '<span style="color:var(--yellow)">Posting to X… this can take a minute for video.</span>';
        const procRes = await fetch('/api/x/process-queue', { method: 'POST' });
        if (!procRes.ok) {
            const err = await procRes.json().catch(() => ({}));
            throw new Error(err.error || `X process failed: ${procRes.status}`);
        }
        const proc = await procRes.json();
        const mine = (proc.results || []).find(r => r.id === itemId);
        if (mine && mine.status === 'published') {
            const url = mine.tweet_url || mine.url || (mine.id_str ? `https://x.com/i/web/status/${mine.id_str}` : '');
            if (feedback) feedback.innerHTML = `<span style="color:var(--green)">Posted to X ✓${url ? ` — <a href="${url}" target="_blank" style="color:var(--accent)">open</a>` : ''}</span>`;
            if (btn) { btn.textContent = 'Posted ✓'; btn.style.borderColor = 'var(--green)'; btn.style.color = 'var(--green)'; }
        } else if (mine && mine.status === 'dry_run') {
            if (feedback) feedback.innerHTML = '<span style="color:var(--yellow)">X dry-run — would post (X_DRY_RUN is on)</span>';
            if (btn) { btn.textContent = 'Dry-run'; btn.disabled = false; }
        } else if (mine && (mine.status === 'failed' || mine.error)) {
            throw new Error(mine.error || 'X post failed');
        } else if (proc.ready === 0 && !(proc.results || []).length) {
            if (feedback) feedback.innerHTML = '<span style="color:var(--yellow)">Queued — nothing due yet (check upload date / duration ≤140s)</span>';
            if (btn) { btn.textContent = 'Queued'; btn.disabled = false; }
        } else {
            if (feedback) feedback.innerHTML = `<span style="color:var(--green)">X processor ran ✓ (${proc.processed || 0}) — check Scheduled</span>`;
            if (btn) { btn.textContent = 'Processed ✓'; btn.style.borderColor = 'var(--green)'; btn.style.color = 'var(--green)'; }
        }
        setTimeout(() => closeFlowOverlay(), 2200);
    } catch (e) {
        if (feedback) feedback.innerHTML = `<span style="color:var(--red)">Error: ${e.message}</span>`;
        if (btn) { btn.textContent = 'Publish Now'; btn.disabled = false; }
    }
}

async function scheduleSocial(itemId) {
    const feedback = document.getElementById('sd-feedback');
    const btn = document.querySelector('.ab-btn-schedule');
    if (btn) { btn.textContent = 'Scheduling...'; btn.disabled = true; }

    const body = {
        title: document.getElementById('sd-title')?.value,
        description: document.getElementById('sd-desc')?.value,
        hashtags: document.getElementById('sd-hashtags')?.value,
        tags: (document.getElementById('sd-tags')?.value || '').split(',').map(t => t.trim()).filter(Boolean),
        upload_date: localInputToISO(document.getElementById('sd-upload-date')?.value),
        publish_date: localInputToISO(document.getElementById('sd-publish-date')?.value),
        status: 'queued',
    };

    if (!body.upload_date || !body.publish_date) {
        if (feedback) feedback.textContent = 'Set both upload and publish dates.';
        if (btn) { btn.textContent = 'Schedule'; btn.disabled = false; }
        return;
    }

    try {
        const res = await fetch(`/api/action-board/social/${itemId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!res.ok) throw new Error(`${res.status}`);
        const data = await res.json().catch(() => ({}));
        if (data.calendar === 'failed') {
            // Queued for upload, but the calendar event didn't land — say so
            // instead of a false all-green (the old silent-failure mode).
            if (feedback) feedback.innerHTML = `<span style="color:var(--yellow, #d9a441)">Scheduled ✓ — but the calendar event failed (check cloud credentials)</span>`;
            if (btn) { btn.textContent = 'Scheduled (no calendar)'; btn.style.background = 'var(--yellow, #d9a441)'; }
            setTimeout(() => closeFlowOverlay(), 3500);
        } else {
            // YouTube promotes into youtube_queue (Scheduled). X stays on social_queue —
            // Scheduled tab now lists those too.
            if (feedback) feedback.innerHTML = `<span style="color:var(--green)">Scheduled ✓ — watch it under Scheduled</span>`;
            if (btn) { btn.textContent = 'Scheduled ✓'; btn.style.background = 'var(--green)'; }
            setTimeout(() => closeFlowOverlay(), 1500);
        }
    } catch (e) {
        if (feedback) feedback.innerHTML = `<span style="color:var(--red)">Error: ${e.message}</span>`;
        if (btn) { btn.textContent = 'Schedule'; btn.disabled = false; }
    }
}

async function cancelSocial(itemId) {
    try {
        await fetch(`/api/action-board/social/${itemId}`, { method: 'DELETE' });
    } catch (e) {
        console.error('Failed to cancel:', e);
    }
    closeFlowOverlay();
}

// Manual-post flow: copy caption from the live edit fields (title + hashtags).
// No URLs in captions by design.
async function copySocialCaption() {
    const feedback = document.getElementById('sd-feedback');
    const title = document.getElementById('sd-title')?.value?.trim() || '';
    const desc = document.getElementById('sd-desc')?.value?.trim() || '';
    const hashtags = document.getElementById('sd-hashtags')?.value?.trim() || '';
    // Description is the actual post text on X/TikTok/IG/FB; title is the fallback
    const base = desc || title;
    const caption = (hashtags && !base.includes(hashtags)) ? `${base}\n\n${hashtags}` : base;
    try {
        await navigator.clipboard.writeText(caption);
        if (feedback) feedback.innerHTML = '<span style="color:var(--green)">Caption copied ✓</span>';
    } catch (e) {
        if (feedback) feedback.innerHTML = `<span style="color:var(--red)">Copy failed: ${e.message}</span>`;
    }
}

// Copy the synced file path (relative to the Operator's NC sync root).
// Paste into Finder's Go to Folder (Cmd+Shift+G) or Explorer's address bar.
async function copySocialFilePath(path) {
    const feedback = document.getElementById('sd-feedback');
    try {
        await navigator.clipboard.writeText(path);
        if (feedback) feedback.innerHTML = '<span style="color:var(--green)">Path copied ✓ — paste in Finder (⌘⇧G)</span>';
    } catch (e) {
        if (feedback) feedback.innerHTML = `<span style="color:var(--red)">Copy failed: ${e.message}</span>`;
    }
}

// Mark a manually-posted item as published so the board stays truthful.
async function markSocialPosted(itemId) {
    const feedback = document.getElementById('sd-feedback');
    try {
        const res = await fetch(`/api/action-board/social/${itemId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                status: 'published',
                platform_data: { manual_post: true, posted_at: new Date().toISOString() },
            }),
        });
        if (!res.ok) throw new Error(`${res.status}`);
        if (feedback) feedback.innerHTML = '<span style="color:var(--green)">Marked posted ✓</span>';
        setTimeout(() => closeFlowOverlay(), 1200);
    } catch (e) {
        if (feedback) feedback.innerHTML = `<span style="color:var(--red)">Error: ${e.message}</span>`;
    }
}

// =============================================================================
// Studio task detail overlay — follow-up tasks from uploaded shorts
// =============================================================================

function openTaskDetail(taskId) {
    const task = _taskCache[taskId];
    if (!task) return;

    closeFlowOverlay();
    const panel = document.getElementById('panel-container');
    if (!panel) return;

    const overlay = document.createElement('div');
    overlay.id = 'ab-flow-overlay';
    overlay.className = 'ab-flow-embedded';
    overlay.setAttribute('data-return-tab', 'ab-actions');

    // Format description as HTML — convert newlines, make URLs clickable
    const descHtml = esc(task.description)
        .replace(/\n/g, '<br>')
        .replace(/(https:\/\/[^\s<]+)/g, '<a href="$1" target="_blank" style="color:var(--accent)">$1</a>')
        .replace(/• /g, '<span style="color:var(--accent)">•</span> ');

    // Extract Studio URL for the direct button
    const studioMatch = task.description.match(/Studio: (https:\/\/studio\.youtube\.com\S+)/);
    const studioUrl = studioMatch ? studioMatch[1] : '';

    overlay.innerHTML = `
        <div class="ab-flow-topbar">
            <button class="ab-flow-back" onclick="closeFlowOverlay()">← Back to Actions</button>
            <span class="ab-flow-title">🎬 Studio Task</span>
        </div>
        <div class="ab-detail-scroll">
            <div class="ab-detail-content">
                <h2 class="ab-detail-title" style="margin-bottom:16px;">${esc(task.title)}</h2>
                <div style="margin-bottom:20px;line-height:1.7;color:#aaa;font-size:13px;">
                    ${descHtml}
                </div>
                ${studioUrl ? `<a href="${esc(studioUrl)}" target="_blank" class="ab-btn" style="display:inline-block;margin-bottom:16px;text-align:center;text-decoration:none;background:var(--accent);color:#000;font-weight:600;">Open YouTube Studio ↗</a>` : ''}
                <div class="ab-detail-actions">
                    <button class="ab-btn ab-btn-done" id="task-done-btn" onclick="completeTask(${taskId})">Mark Done</button>
                    <div class="ab-detail-feedback" id="task-feedback"></div>
                </div>
            </div>
        </div>`;

    panel.appendChild(overlay);
}

async function completeTask(taskId) {
    const btn = document.getElementById('task-done-btn');
    const feedback = document.getElementById('task-feedback');
    if (btn) { btn.textContent = 'Completing...'; btn.disabled = true; }

    try {
        const res = await fetch(`/api/action-board/tasks/${taskId}`, { method: 'PATCH' });
        if (!res.ok) throw new Error(`${res.status}`);

        if (btn) { btn.textContent = 'Done ✓'; btn.style.background = 'var(--green)'; btn.style.color = '#000'; }
        if (feedback) feedback.innerHTML = '<span style="color:var(--green)">Task completed</span>';

        setTimeout(() => closeFlowOverlay(), 1500);
    } catch (e) {
        if (feedback) feedback.innerHTML = `<span style="color:var(--red)">Error: ${e.message}</span>`;
        if (btn) { btn.textContent = 'Mark Done'; btn.disabled = false; }
    }
}

// =============================================================================
// Uploaded detail — mark as published once it's live
// =============================================================================

function openUploadedDetail(queueId) {
    const item = _scheduledCache[queueId];
    if (!item) return;

    closeFlowOverlay();
    const panel = document.getElementById('panel-container');
    if (!panel) return;

    const overlay = document.createElement('div');
    overlay.id = 'ab-flow-overlay';
    overlay.className = 'ab-flow-embedded';
    overlay.setAttribute('data-return-tab', 'ab-actions');

    const studioUrl = item.youtube_video_id
        ? `https://studio.youtube.com/video/${item.youtube_video_id}/edit` : '';
    const pubDate = item.publish_date
        ? new Date(item.publish_date).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : '';

    overlay.innerHTML = `
        <div class="ab-flow-topbar">
            <button class="ab-flow-back" onclick="closeFlowOverlay()">← Back to Actions</button>
            <span class="ab-flow-title">📺 Uploaded Short</span>
        </div>
        <div class="ab-detail-scroll">
            <div class="ab-detail-content">
                <div class="ab-detail-title-row">
                    <h2 class="ab-detail-title">${esc(item.title)}</h2>
                    <span class="ab-action-status-badge ab-status-uploaded">uploaded</span>
                </div>
                ${pubDate ? `<div style="margin:12px 0;color:#aaa;font-size:13px;">Scheduled to go public: ${pubDate}</div>` : ''}
                ${item.series ? `<div style="margin-bottom:12px;"><span class="ab-action-series">${esc(item.series)}</span></div>` : ''}
                ${studioUrl ? `<a href="${esc(studioUrl)}" target="_blank" class="ab-btn" style="display:inline-block;margin-bottom:16px;text-align:center;text-decoration:none;background:var(--accent);color:#000;font-weight:600;">Open YouTube Studio ↗</a>` : ''}
                <div class="ab-detail-actions">
                    <button class="ab-btn ab-btn-done" id="publish-btn" onclick="markPublished(${queueId})">Mark Published</button>
                    <div class="ab-detail-feedback" id="publish-feedback"></div>
                </div>
            </div>
        </div>`;

    panel.appendChild(overlay);
}

async function markPublished(queueId) {
    const btn = document.getElementById('publish-btn');
    const feedback = document.getElementById('publish-feedback');
    if (btn) { btn.textContent = 'Updating...'; btn.disabled = true; }

    try {
        const res = await fetch(`/api/action-board/scheduled/${queueId}/publish`, { method: 'PATCH' });
        if (!res.ok) throw new Error(`${res.status}`);

        if (btn) { btn.textContent = 'Published ✓'; btn.style.background = 'var(--green)'; btn.style.color = '#000'; }
        if (feedback) feedback.innerHTML = '<span style="color:var(--green)">Cleared from scheduled</span>';

        setTimeout(() => closeFlowOverlay(), 1500);
    } catch (e) {
        if (feedback) feedback.innerHTML = `<span style="color:var(--red)">Error: ${e.message}</span>`;
        if (btn) { btn.textContent = 'Mark Published'; btn.disabled = false; }
    }
}

// =============================================================================
// Links tab — fetch and inject the links page content
// =============================================================================

// Editable per-operator link board. View mode = clickable compact cards.
// Edit mode = inline add/edit/delete + starter templates + a raw-text toggle
// for bulk edits. Persisted via /api/action-board/links (agents can write it too).

let _abLinks = [];            // [{id,title,url,note,icon,group}]
let _abLinksEditable = false; // server says this viewer may edit
let _abLinksEditing = false;  // in edit mode
let _abLinksRaw = false;      // raw textarea mode

const _AB_LINK_TEMPLATES = {
    tool:      { type: 'link', title: '', url: '', note: '', icon: '🔧', group: 'Tools', items: [] },
    dashboard: { type: 'link', title: '', url: '', note: '', icon: '📊', group: 'Dashboards', items: [] },
    doc:       { type: 'link', title: '', url: '', note: '', icon: '📄', group: 'Docs', items: [] },
    bundle:    { type: 'bundle', title: '', url: '', note: '', icon: '', group: '', wide: false, collapsed: false, items: [
        { kind: 'row', label: '', text: '', url: '' },
    ] },
};

function _abEsc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => (
        { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
    ));
}

async function loadABLinks() {
    if (_abLinksLoaded) return;
    const container = document.getElementById('ab-links-grid');
    if (!container) return;
    container.innerHTML = '<div class="ab-empty">Loading…</div>';
    try {
        const res = await fetch('/api/action-board/links');
        const data = await res.json();
        _abLinks = Array.isArray(data.cards) ? data.cards.map(_abNormalizeCard) : [];
        _abLinksEditable = !!data.editable;
        renderABLinks();
    } catch (e) {
        container.innerHTML = '<div class="ab-empty">Could not load links.</div>';
    }
    _abLinksLoaded = true;
}

function _abNormalizeCard(c) {
    const type = (c && c.type === 'bundle') ? 'bundle' : 'link';
    const items = Array.isArray(c && c.items) ? c.items.map(it => {
        if (!it || typeof it !== 'object') return null;
        const kind = it.kind === 'spacer' ? 'spacer' : (it.kind === 'subhead' ? 'subhead' : 'row');
        if (kind === 'spacer') return { kind: 'spacer', label: '', text: '', url: '' };
        if (kind === 'subhead') return { kind: 'subhead', label: String(it.label || it.text || ''), text: '', url: '' };
        return {
            kind: 'row',
            label: String(it.label || ''),
            text: String(it.text || it.note || ''),
            url: String(it.url || ''),
        };
    }).filter(Boolean) : [];
    return {
        id: (c && c.id) || '',
        type,
        title: (c && c.title) || '',
        url: (c && c.url) || '',
        note: (c && c.note) || '',
        icon: (c && c.icon) || '',
        group: (c && c.group) || '',
        wide: !!(c && c.wide),
        collapsed: !!(c && c.collapsed),
        items,
    };
}

function renderABLinks() {
    const container = document.getElementById('ab-links-grid');
    if (!container) return;

    let bar = '<div class="ablk-bar">';
    if (!_abLinksEditing) {
        if (_abLinksEditable) bar += '<button class="ablk-btn" onclick="abLinksToggleEdit()">Edit</button>';
    } else {
        bar += '<button class="ablk-btn" onclick="abLinksAdd()">+ Link</button>';
        bar += '<button class="ablk-btn" onclick="abLinksAddTemplate(\'bundle\')">+ Bundle</button>';
        bar += '<span class="ablk-tpl-lbl">Templates:</span>';
        bar += '<button class="ablk-btn ablk-tpl" onclick="abLinksAddTemplate(\'tool\')">🔧 Tool</button>';
        bar += '<button class="ablk-btn ablk-tpl" onclick="abLinksAddTemplate(\'dashboard\')">📊 Dashboard</button>';
        bar += '<button class="ablk-btn ablk-tpl" onclick="abLinksAddTemplate(\'doc\')">📄 Doc</button>';
        bar += '<button class="ablk-btn" onclick="abLinksToggleRaw()">' + (_abLinksRaw ? 'Cards' : 'Raw') + '</button>';
        bar += '<span class="ablk-spacer"></span>';
        bar += '<button class="ablk-btn ablk-save" onclick="abLinksSave()">Save</button>';
        bar += '<button class="ablk-btn" onclick="abLinksCancel()">Cancel</button>';
    }
    bar += '</div>';

    let body = '';
    if (_abLinksEditing && _abLinksRaw) {
        body = '<textarea id="ablk-raw" class="ablk-raw" spellcheck="false" '
             + 'placeholder="Leaf: Title | URL | note | group&#10;Bundle: BUNDLE | Title | icon | wide&#10;  row: label | linked text | url&#10;  sub: Section name&#10;  ---">'
             + _abEsc(_abLinksToRaw(_abLinks)) + '</textarea>'
             + '<div class="ablk-hint">Leaf: <b>Title | URL | note | group</b>. Bundle: <b>BUNDLE | Title | icon | wide</b> then indented rows <b>label | text | url</b>, <b>sub: Name</b>, or <b>---</b>.</div>';
    } else if (_abLinksEditing) {
        body = '<div class="ablk-grid ablk-edit">';
        _abLinks.forEach((c, i) => {
            const isBundle = c.type === 'bundle';
            body += '<div class="ablk-card-edit" data-i="' + i + '" data-type="' + (isBundle ? 'bundle' : 'link') + '">'
                  + '<div class="ablk-row1">'
                  + '<select class="ablk-type" data-f="type" onchange="abLinksTypeChange(' + i + ', this.value)">'
                  + '<option value="link"' + (!isBundle ? ' selected' : '') + '>Link</option>'
                  + '<option value="bundle"' + (isBundle ? ' selected' : '') + '>Bundle</option>'
                  + '</select>'
                  + '<input class="ablk-in ablk-icon" data-f="icon" maxlength="4" value="' + _abEsc(c.icon) + '" placeholder="🔗">'
                  + '<input class="ablk-in ablk-title" data-f="title" value="' + _abEsc(c.title) + '" placeholder="' + (isBundle ? 'Bundle title (e.g. Monthly Bills)' : 'Title') + '">'
                  + '<button class="ablk-del" title="Delete" onclick="abLinksDelete(' + i + ')">✕</button>'
                  + '</div>';
            if (!isBundle) {
                body += '<input class="ablk-in ablk-url" data-f="url" value="' + _abEsc(c.url) + '" placeholder="https://…  (or /path)">'
                      + '<input class="ablk-in ablk-note" data-f="note" value="' + _abEsc(c.note) + '" placeholder="One-line note (optional)">'
                      + '<input class="ablk-in ablk-note" data-f="group" value="' + _abEsc(c.group || '') + '" placeholder="Group (optional — label above leaf cluster)">';
            } else {
                body += '<label class="ablk-chk"><input type="checkbox" data-f="wide"' + (c.wide ? ' checked' : '') + '> Wide (span 2 cols when space allows)</label>'
                      + '<label class="ablk-chk"><input type="checkbox" data-f="collapsed"' + (c.collapsed ? ' checked' : '') + '> Start collapsed</label>'
                      + '<div class="ablk-items-ed" data-items="' + i + '">';
                (c.items || []).forEach((it, j) => {
                    const kind = it.kind || 'row';
                    if (kind === 'spacer') {
                        body += '<div class="ablk-item-ed" data-j="' + j + '" data-kind="spacer">'
                              + '<span class="ablk-label" style="grid-column:1/-2">— spacer —</span>'
                              + '<button class="ablk-del" onclick="abLinksDeleteItem(' + i + ',' + j + ')">✕</button></div>';
                    } else if (kind === 'subhead') {
                        body += '<div class="ablk-item-ed" data-j="' + j + '" data-kind="subhead">'
                              + '<input class="ablk-in" data-f="label" value="' + _abEsc(it.label || '') + '" placeholder="Subsection header" style="grid-column:1/-2">'
                              + '<button class="ablk-del" onclick="abLinksDeleteItem(' + i + ',' + j + ')">✕</button></div>';
                    } else {
                        body += '<div class="ablk-item-ed" data-j="' + j + '" data-kind="row">'
                              + '<input class="ablk-in" data-f="label" value="' + _abEsc(it.label || '') + '" placeholder="Label">'
                              + '<input class="ablk-in" data-f="text" value="' + _abEsc(it.text || '') + '" placeholder="Linked text">'
                              + '<input class="ablk-in ablk-in-url" data-f="url" value="' + _abEsc(it.url || '') + '" placeholder="https://… or /path">'
                              + '<button class="ablk-del" onclick="abLinksDeleteItem(' + i + ',' + j + ')">✕</button></div>';
                    }
                });
                body += '<div style="display:flex;gap:6px;flex-wrap:wrap">'
                      + '<button type="button" class="ablk-add-item" onclick="abLinksAddItem(' + i + ',\'row\')">+ Row</button>'
                      + '<button type="button" class="ablk-add-item" onclick="abLinksAddItem(' + i + ',\'subhead\')">+ Subhead</button>'
                      + '<button type="button" class="ablk-add-item" onclick="abLinksAddItem(' + i + ',\'spacer\')">+ Spacer</button>'
                      + '</div></div>';
            }
            body += '</div>';
        });
        body += '</div>';
        if (!_abLinks.length) body += '<div class="ab-empty">No links yet. Use <b>+ Link</b> or <b>+ Bundle</b>.</div>';
    } else {
        if (!_abLinks.length) {
            body = '<div class="ab-empty">No links yet.' + (_abLinksEditable ? ' Hit <b>Edit</b> to add some.' : '') + '</div>';
        } else {
            // Leaves may cluster under optional group headers; bundles are first-class tiles.
            const groups = {};
            const order = [];
            _abLinks.forEach((c, idx) => {
                if (c.type === 'bundle') {
                    const key = '__bundle_' + idx;
                    groups[key] = [{ card: c, idx, bundle: true }];
                    order.push(key);
                    return;
                }
                const g = c.group || '';
                if (!(g in groups)) { groups[g] = []; order.push(g); }
                groups[g].push({ card: c, idx, bundle: false });
            });
            body = '<div class="ablk-grid">';
            order.forEach(g => {
                const entries = groups[g];
                if (entries.length === 1 && entries[0].bundle) {
                    body += _abLinksRenderBundle(entries[0].card, entries[0].idx);
                    return;
                }
                if (g && !String(g).startsWith('__bundle_')) {
                    body += '<div class="ablk-group-h">' + _abEsc(g) + '</div>';
                }
                entries.forEach(e => {
                    if (e.bundle) body += _abLinksRenderBundle(e.card, e.idx);
                    else body += _abLinksRenderLeaf(e.card);
                });
            });
            body += '</div>';
        }
    }

    container.innerHTML = bar + body;
}

// Same-origin tool pages (Backlog, Jules) get ?return=links so their × lands
// back on the Links board instead of history.back() → Attention.
function _abLinksWithReturn(url) {
    if (!url) return url;
    try {
        const u = new URL(String(url), window.location.origin);
        if (u.origin !== window.location.origin) return url;
        const path = u.pathname.replace(/\/+$/, '') || '/';
        if (path !== '/backlog' && path !== '/jules') return url;
        if (!u.searchParams.has('return')) u.searchParams.set('return', 'links');
        // Keep relative form for in-Cove paths so host/presence doors stay correct.
        if (String(url).startsWith('/') || !/^[a-z][a-z0-9+.-]*:/i.test(String(url))) {
            return u.pathname + u.search + u.hash;
        }
        return u.toString();
    } catch (_) {
        return url;
    }
}

function _abLinksRenderLeaf(c) {
    const openUrl = c.url ? _abLinksWithReturn(c.url) : '';
    const href = openUrl ? ' href="' + _abEsc(openUrl) + '"' : '';
    // Always new window/tab — board stays put (desktop + mobile browser).
    const tgt = openUrl ? ' target="_blank" rel="noopener"' : '';
    return '<a class="ablk-card"' + href + tgt + '>'
         + '<div class="ablk-card-t">' + (c.icon ? '<span class="ablk-card-i">' + _abEsc(c.icon) + '</span>' : '')
         + _abEsc(c.title || c.url) + '</div>'
         + (c.note ? '<div class="ablk-card-n">' + _abEsc(c.note) + '</div>' : '')
         + '</a>';
}

function _abLinksRenderBundle(c, idx) {
    const collapsed = !!c.collapsed;
    const wide = c.wide ? ' ablk-wide' : '';
    const chev = collapsed ? '▶' : '▼';
    const items = Array.isArray(c.items) ? c.items : [];
    const rowCount = items.filter(it => (it.kind || 'row') === 'row').length;
    let html = '<div class="ablk-bundle' + wide + (collapsed ? ' ablk-collapsed' : '') + '" data-bundle-idx="' + idx + '">'
             + '<div class="ablk-bundle-h" onclick="abLinksToggleBundle(' + idx + ')">'
             + '<span class="ablk-bundle-toggle">' + chev + '</span>'
             + '<span class="ablk-bundle-title">' + (c.icon ? _abEsc(c.icon) + ' ' : '') + _abEsc(c.title || 'Bundle') + '</span>'
             + '<span class="ablk-bundle-meta">' + rowCount + '</span>'
             + '</div><div class="ablk-bundle-body">';
    items.forEach(it => {
        const kind = it.kind || 'row';
        if (kind === 'spacer') {
            html += '<hr class="ablk-hr">';
        } else if (kind === 'subhead') {
            html += '<div class="ablk-subh">' + _abEsc(it.label || '') + '</div>';
        } else {
            const label = it.label || '';
            const text = (it.text || '').trim() || _abLinksShortUrl(it.url) || label || 'Open';
            html += '<div class="ablk-row">';
            if (label) html += '<span class="ablk-label">' + _abEsc(label) + '</span>';
            if (it.url) {
                const openUrl = _abLinksWithReturn(it.url);
                html += '<a class="ablk-link" href="' + _abEsc(openUrl) + '" target="_blank" rel="noopener">'
                      + _abEsc(text) + '</a>';
            } else {
                html += '<span class="ablk-link-plain">' + _abEsc(text) + '</span>';
            }
            html += '</div>';
        }
    });
    if (!items.length) {
        html += '<div class="ablk-link-plain">Empty bundle — Edit to add rows</div>';
    }
    html += '</div></div>';
    return html;
}

function _abLinksShortUrl(u) {
    u = (u || '').trim();
    if (!u) return '';
    if (u.startsWith('/')) return u;
    try {
        const parsed = new URL(u, window.location.origin);
        let s = parsed.host + (parsed.pathname && parsed.pathname !== '/' ? parsed.pathname : '');
        if (s.endsWith('/')) s = s.slice(0, -1);
        return s || u;
    } catch (_) {
        return u;
    }
}

function abLinksToggleBundle(idx) {
    const c = _abLinks[idx];
    if (!c || c.type !== 'bundle') return;
    c.collapsed = !c.collapsed;
    // View-only toggle — do not persist until Save in edit mode.
    renderABLinks();
}

function _abLinksToRaw(cards) {
    const lines = [];
    cards.forEach(c => {
        if (c.type === 'bundle') {
            lines.push(['BUNDLE', c.title || '', c.icon || '', c.wide ? 'wide' : ''].join(' | '));
            (c.items || []).forEach(it => {
                const kind = it.kind || 'row';
                if (kind === 'spacer') lines.push('  ---');
                else if (kind === 'subhead') lines.push('  sub: ' + (it.label || ''));
                else lines.push('  ' + [it.label || '', it.text || '', it.url || ''].join(' | '));
            });
        } else {
            const parts = [c.title || '', c.url || ''];
            if (c.note || c.group) parts.push(c.note || '');
            if (c.group) parts.push(c.group);
            lines.push(parts.join(' | '));
        }
    });
    return lines.join('\n');
}

function _abLinksFromRaw(text) {
    const out = [];
    let cur = null;
    (text || '').split('\n').forEach(line => {
        const raw = line.replace(/\s+$/, '');
        if (!raw.trim()) return;
        const indented = /^\s+/.test(line);
        const t = raw.trim();
        if (!indented && t.toUpperCase().startsWith('BUNDLE')) {
            const p = t.split('|').map(s => s.trim());
            // BUNDLE | Title | icon | wide
            cur = _abNormalizeCard({
                type: 'bundle',
                title: p[1] || '',
                icon: p[2] || '',
                wide: String(p[3] || '').toLowerCase().includes('wide'),
                items: [],
            });
            out.push(cur);
            return;
        }
        if (indented && cur && cur.type === 'bundle') {
            if (t === '---') {
                cur.items.push({ kind: 'spacer', label: '', text: '', url: '' });
            } else if (/^sub:\s*/i.test(t)) {
                cur.items.push({ kind: 'subhead', label: t.replace(/^sub:\s*/i, ''), text: '', url: '' });
            } else {
                const p = t.split('|').map(s => s.trim());
                cur.items.push({ kind: 'row', label: p[0] || '', text: p[1] || '', url: p[2] || '' });
            }
            return;
        }
        cur = null;
        const p = t.split('|').map(s => s.trim());
        const title = p[0] || '';
        const url = p[1] || '';
        const note = p[2] || '';
        const group = p[3] || '';
        if (!title && !url) return;
        out.push(_abNormalizeCard({ type: 'link', title, url, note, icon: '', group }));
    });
    return out;
}

function _abLinksCollect() {
    if (_abLinksRaw) {
        const ta = document.getElementById('ablk-raw');
        _abLinks = _abLinksFromRaw(ta ? ta.value : '');
        return;
    }
    const cards = [];
    document.querySelectorAll('#ab-links-grid .ablk-card-edit').forEach(el => {
        const get = f => {
            const n = el.querySelector('[data-f="' + f + '"]');
            if (!n) return '';
            if (n.type === 'checkbox') return n.checked;
            return n.value.trim();
        };
        const i = parseInt(el.getAttribute('data-i'), 10);
        const prev = _abLinks[i] || {};
        const type = get('type') === 'bundle' ? 'bundle' : 'link';
        if (type === 'bundle') {
            const items = [];
            el.querySelectorAll('.ablk-item-ed').forEach(row => {
                const kind = row.getAttribute('data-kind') || 'row';
                if (kind === 'spacer') {
                    items.push({ kind: 'spacer', label: '', text: '', url: '' });
                    return;
                }
                const g = f => { const n = row.querySelector('[data-f="' + f + '"]'); return n ? n.value.trim() : ''; };
                if (kind === 'subhead') {
                    items.push({ kind: 'subhead', label: g('label'), text: '', url: '' });
                } else {
                    items.push({ kind: 'row', label: g('label'), text: g('text'), url: g('url') });
                }
            });
            const title = get('title');
            if (!title && !items.length) return;
            cards.push(_abNormalizeCard({
                id: prev.id || '',
                type: 'bundle',
                title,
                icon: get('icon'),
                wide: !!get('wide'),
                collapsed: !!get('collapsed'),
                items,
            }));
        } else {
            const title = get('title'), url = get('url');
            if (!title && !url) return;
            cards.push(_abNormalizeCard({
                id: prev.id || '',
                type: 'link',
                title, url,
                note: get('note'),
                icon: get('icon'),
                group: get('group'),
            }));
        }
    });
    _abLinks = cards;
}

function abLinksToggleEdit() {
    _abLinksEditing = true;
    renderABLinks();
}

function abLinksCancel() {
    _abLinksEditing = false;
    _abLinksRaw = false;
    _abLinksLoaded = false;
    loadABLinks();
}

function abLinksToggleRaw() {
    _abLinksCollect();
    _abLinksRaw = !_abLinksRaw;
    renderABLinks();
}

function abLinksAdd() {
    _abLinksCollect();
    _abLinks.push(_abNormalizeCard({ type: 'link', title: '', url: '', note: '', icon: '', group: '' }));
    _abLinksRaw = false;
    renderABLinks();
}

function abLinksAddTemplate(kind) {
    _abLinksCollect();
    const t = _AB_LINK_TEMPLATES[kind] || _AB_LINK_TEMPLATES.tool;
    _abLinks.push(_abNormalizeCard(Object.assign({ id: '' }, t)));
    _abLinksRaw = false;
    renderABLinks();
}

function abLinksTypeChange(i, type) {
    _abLinksCollect();
    const prev = _abLinks[i] || _abNormalizeCard({});
    if (type === 'bundle') {
        _abLinks[i] = _abNormalizeCard({
            id: prev.id,
            type: 'bundle',
            title: prev.title,
            icon: prev.icon,
            wide: false,
            collapsed: false,
            items: (prev.items && prev.items.length) ? prev.items : [
                { kind: 'row', label: '', text: prev.note || '', url: prev.url || '' },
            ],
        });
    } else {
        const first = (prev.items || []).find(it => (it.kind || 'row') === 'row') || {};
        _abLinks[i] = _abNormalizeCard({
            id: prev.id,
            type: 'link',
            title: prev.title,
            icon: prev.icon,
            url: first.url || prev.url || '',
            note: first.text || prev.note || '',
            group: prev.group || '',
        });
    }
    renderABLinks();
}

function abLinksAddItem(cardIdx, kind) {
    _abLinksCollect();
    const c = _abLinks[cardIdx];
    if (!c || c.type !== 'bundle') return;
    if (kind === 'spacer') c.items.push({ kind: 'spacer', label: '', text: '', url: '' });
    else if (kind === 'subhead') c.items.push({ kind: 'subhead', label: '', text: '', url: '' });
    else c.items.push({ kind: 'row', label: '', text: '', url: '' });
    renderABLinks();
}

function abLinksDeleteItem(cardIdx, itemIdx) {
    _abLinksCollect();
    const c = _abLinks[cardIdx];
    if (!c || c.type !== 'bundle') return;
    c.items.splice(itemIdx, 1);
    renderABLinks();
}

function abLinksDelete(i) {
    _abLinksCollect();
    _abLinks.splice(i, 1);
    renderABLinks();
}

async function abLinksSave() {
    _abLinksCollect();
    const btn = document.querySelector('#ab-links-grid .ablk-save');
    if (btn) { btn.textContent = 'Saving…'; btn.disabled = true; }
    try {
        const res = await fetch('/api/action-board/links', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ cards: _abLinks }),
        });
        if (!res.ok) throw new Error(`${res.status}`);
        _abLinksEditing = false;
        _abLinksRaw = false;
        _abLinksLoaded = false;
        await loadABLinks();
    } catch (e) {
        if (btn) { btn.textContent = 'Save'; btn.disabled = false; }
        alert('Could not save links: ' + e.message);
    }
}


// =============================================================================
// Creation Flows tab — guided LP-stage journeys
// =============================================================================

async function loadABFlows() {
    if (_abFlowsLoaded) return;

    // Load creation actions (top section)
    const creationsContainer = document.getElementById('ab-creations-list');
    if (creationsContainer) {
        try {
            const res = await fetch('/api/creation/actions');
            if (res.ok) {
                const data = await res.json();
                renderCreations(creationsContainer, data.actions || []);
            }
        } catch (e) {
            // Creation API not available yet — silently skip
        }
    }

    // Creation flow templates (bottom section) — unified capability cards:
    // builtin app flows + catalog flows from the hub, one shared card + filters.
    const container = document.getElementById('ab-flows-list');
    if (!container) return;

    await loadABCapabilities();
    renderCapTab('flows', container, _abFlowBuiltins());
    _abFlowsLoaded = true;
}

function renderFlows(container, flows) {
    if (flows.length === 0) {
        container.innerHTML = '<div class="ab-empty">No creation flows defined yet.</div>';
        return;
    }

    let html = '';
    flows.forEach(f => {
        const stepCount = f.steps ? f.steps.length : (f.step_count || '?');
        html += `
        <div class="ab-flow-card" onclick="openFlow('${esc(f.id)}')">
            <div class="ab-wf-header">
                <h3>${esc(f.name)}</h3>
                ${f.category ? `<span class="ab-wf-cat">${esc(f.category)}</span>` : ''}
            </div>
            <p class="ab-wf-desc">${esc(f.description || '')}</p>
            <div class="ab-wf-meta">
                <span>${stepCount} steps</span>
                ${f.est_time ? `<span>${esc(f.est_time)}</span>` : ''}
                <span class="ab-wf-cost" data-flow="${esc(f.id)}"></span>
            </div>
        </div>`;
    });
    container.innerHTML = html;
    loadFlowCostBadges(flows);
}

// #183 — lazy-load a per-run cost estimate onto each flow card. Stays quiet
// until a flow has a profile (after its first run), then shows the model span.
async function loadFlowCostBadges(flows) {
    for (const f of flows) {
        const el = document.querySelector(`.ab-wf-cost[data-flow="${cssEsc(f.id)}"]`);
        if (!el) continue;
        try {
            const est = await fetch(`/api/cost/estimate?flow=${encodeURIComponent(f.id)}`).then(r => r.json());
            if (est && est.has_data && est.headline) {
                const h = est.headline;
                el.textContent = `≈ ${h.summary}/run`;
                const cf = h.cloud_from, ct = h.cloud_to;
                el.title = `Assumes cloud: ${cf.label} $${cf.cost_cloud_usd.toFixed(2)} … ${ct.label} $${ct.cost_cloud_usd.toFixed(2)}`
                    + (h.local_available ? ' · $0 if run on your own GPU (Ollama)' : '');
            }
        } catch (e) { /* estimate unavailable — leave blank */ }
    }
}

function cssEsc(s) {
    return String(s).replace(/["\\]/g, '\\$&');
}

const FLOW_PAGES = {
    'new-cove-setup': '/static/action-board/new-cove-setup.html',
    'new-agent': '/static/action-board/new-agent-setup.html',
    'create-a-product': '/static/action-board/create-a-product.html',
    'create-a-business': '/static/action-board/create-a-business.html',
    'create-a-mirror': '/static/action-board/create-a-mirror.html',
    'site-builder': '/static/action-board/site-builder.html',
    'rent-gpu': '/static/action-board/rent-gpu.html',
    'gpu-marketplace': '/static/action-board/gpu-marketplace.html',
    'transcript-editor': '/static/action-board/video-transcript-editor.html',
    'moments-review': '/static/action-board/video-moments-review.html',
    'crop-template': '/static/action-board/video-crop-position.html',
};

// ── Routing table — maps keywords to flow/tool IDs ────────────────────
// Checked BEFORE AI routing. First match wins. Keep specific terms first.
const FLOW_ROUTING = [
    { keywords: ['website', 'site', 'web page', 'webpage', 'landing page'], target: 'site-builder', label: 'Site Builder' },
    { keywords: ['cove', 'onboard', 'set up', 'setup', 'family'], target: 'new-cove-setup', label: 'New Cove Setup' },
    { keywords: ['agent', 'steward', 'team member'], target: 'new-agent', label: 'New Agent Setup' },
    { keywords: ['product', 'sell', 'pricing', 'launch'], target: 'create-a-product', label: 'Create a Product' },
    { keywords: ['business', 'company', 'llc', 'entity'], target: 'create-a-business', label: 'Create a Business' },
    { keywords: ['mirror', 'canon', 'philosophy', 'stoic', 'buddhist', 'tao'], target: 'create-a-mirror', label: 'Create a Mirror' },
    { keywords: ['video', 'short', 'youtube', 'clip'], target: 'video-shorts-pipeline', label: 'Video Shorts Pipeline', isTool: true },
];

function openFlow(id) {
    const url = FLOW_PAGES[id];
    if (!url) return;

    const f = getSeedFlows().find(w => w.id === id);
    const title = f?.name || id;

    // Open flow page directly — flow-framework.js handles silent extraction
    // internally when no creation_id is in the URL
    openFlowOverlay(url, 'ab-flows', title);
}

// =============================================================================
// Guided flow — AI routes free-text input to the right creation flow
// =============================================================================

async function startGuidedFlow() {
    const input = document.getElementById('ab-flows-input');
    const btn = document.getElementById('ab-flows-go-btn');
    const text = input?.value?.trim();
    if (!text) return;

    btn.disabled = true;
    btn.textContent = 'Thinking...';

    // ── Step 1: Check routing table (instant, no API call) ────────────
    const lower = text.toLowerCase();
    const match = FLOW_ROUTING.find(r => r.keywords.some(kw => lower.includes(kw)));

    if (match) {
        if (match.isTool) {
            // Route to tool (opens tool page or placeholder)
            openTool(match.target);
        } else if (FLOW_PAGES[match.target]) {
            // Route to flow page with guided context
            const url = FLOW_PAGES[match.target];
            openFlowOverlay(url + (url.includes('?') ? '&' : '?') + 'guided=' + encodeURIComponent(text), 'ab-flows', match.label);
        }
        btn.disabled = false;
        btn.textContent = 'Go';
        input.value = '';
        return;
    }

    // ── Step 2: No keyword match — try AI routing ─────────────────────
    try {
        const templates = getSeedFlows().map(f => `${f.id}: ${f.name} — ${f.description}`).join('\n');
        const tools = getSeedTools().filter(t => t.status === 'active').map(t => `${t.id}: ${t.name} — ${t.description}`).join('\n');

        const res = await fetch('/api/flow/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                system_prompt: 'You are a routing assistant. Given what the user wants to create, pick the best matching flow or tool, or say "none". Return ONLY valid JSON.',
                messages: [{ role: 'user', content: `The user said: "${text}"\n\nFlows:\n${templates}\n\nTools:\n${tools}\n\nReturn JSON: {"id": "matched-id" or "none", "type": "flow" or "tool", "reason": "one sentence"}` }],
                model_id: 'kimi-k2.5-openrouter',
                temperature: 0.1,
            }),
        });

        if (res.ok) {
            const data = await res.json();
            try {
                let raw = (data.response || '').trim();
                if (raw.startsWith('```')) raw = raw.replace(/^```(?:json)?\n?/, '').replace(/\n?```$/, '');
                const parsed = JSON.parse(raw);
                if (parsed.id && parsed.id !== 'none') {
                    if (parsed.type === 'tool') {
                        openTool(parsed.id);
                    } else if (FLOW_PAGES[parsed.id]) {
                        const url = FLOW_PAGES[parsed.id];
                        openFlowOverlay(url + (url.includes('?') ? '&' : '?') + 'guided=' + encodeURIComponent(text), 'ab-flows', parsed.id);
                    }
                    btn.disabled = false;
                    btn.textContent = 'Go';
                    input.value = '';
                    return;
                }
            } catch (e) { /* fall through to free-form */ }
        }
    } catch (e) {
        console.error('AI routing failed:', e);
    }

    // ── Step 3: No match at all — create free-form creation ───────────
    try {
        const createRes = await fetch('/api/creation/actions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title: text.slice(0, 80), intention: text }),
        });
        if (createRes.ok) {
            const created = await createRes.json();
            _abFlowsLoaded = false;
            openCreationDetail(created.id);
        }
    } catch (e) {
        console.error('Free-form creation failed:', e);
    }

    btn.disabled = false;
    btn.textContent = 'Go';
    input.value = '';
}

// =============================================================================
// Flow overlay — opens a page INSIDE the MC (on top of panels)
// =============================================================================

function openFlowOverlay(url, returnTab, title) {
    closeFlowOverlay();
    const panel = document.getElementById('panel-container');
    if (!panel) return;

    const overlay = document.createElement('div');
    overlay.id = 'ab-flow-overlay';
    overlay.className = 'ab-flow-embedded';
    overlay.setAttribute('data-return-tab', returnTab || 'ab-flows');

    const backLabel = returnTab === 'ab-actions' ? 'Actions' : returnTab === 'ab-tools' ? 'Tools' : returnTab === 'team' ? 'Team' : 'Flows';

    overlay.innerHTML = `
        <div class="ab-flow-topbar">
            <button class="ab-flow-back" onclick="closeFlowOverlay()">← Back to ${backLabel}</button>
            ${title ? `<span class="ab-flow-title">${title}</span>` : ''}
        </div>
        <iframe class="ab-flow-frame" src="${url}${url.includes('?') ? '&' : '?'}embedded=1" frameborder="0"></iframe>`;

    panel.appendChild(overlay);
}

function closeFlowOverlay(silent) {
    const overlay = document.getElementById('ab-flow-overlay');
    if (!overlay) return;

    const returnTab = overlay.getAttribute('data-return-tab') || 'ab-flows';
    overlay.remove();

    // Force data refresh so content reloads when user returns
    if (returnTab === 'ab-actions') _abActionsLoaded = false;
    if (returnTab === 'ab-flows') _abFlowsLoaded = false;
    if (returnTab === 'ab-tools') _abToolsLoaded = false;

    // silent = true when called from switchBoard() — just remove overlay,
    // don't trigger a tab switch (the board switch handles its own routing)
    if (!silent && typeof switchToTab === 'function') switchToTab(returnTab);
}


// =============================================================================
// Creation Framework — intentional creation through the LP mechanism
// =============================================================================

const CREATION_STAGES = ['broadcast', 'tune', 'act', 'receive', 'manifest', 'complete'];
const STAGE_LABELS = { broadcast: 'BC', tune: 'TN', act: 'ACT', receive: 'RC', manifest: 'MF', complete: '✓' };
const STAGE_NAMES = { broadcast: 'Broadcast', tune: 'Tune', act: 'Act', receive: 'Receive', manifest: 'Manifest', complete: 'Complete' };

let _creationCache = {};

function renderCreations(container, actions) {
    if (!actions || actions.length === 0) {
        container.innerHTML = '';
        return;
    }

    let html = '<div class="ab-cat-header">Active Creations <span class="ab-cat-count">' + actions.length + '</span></div>';
    actions.forEach(a => {
        _creationCache[a.id] = a;
        const freqTitle = a.frequency ? a.frequency.charAt(0).toUpperCase() + a.frequency.slice(1) : '';
        const freqBadge = a.frequency && typeof lpFreqBadgeHTML === 'function'
            ? lpFreqBadgeHTML(freqTitle)
            : (freqTitle ? `<span class="ab-action-series">${esc(freqTitle)}</span>` : '');

        // Stage progress dots
        const stageIdx = CREATION_STAGES.indexOf(a.stage);
        let stageDots = '<div class="creation-stage-track">';
        CREATION_STAGES.slice(0, 5).forEach((s, i) => {
            const cls = i < stageIdx ? 'done' : i === stageIdx ? 'active' : '';
            stageDots += `<span class="creation-dot ${cls}" title="${STAGE_NAMES[s]}">${STAGE_LABELS[s]}</span>`;
            if (i < 4) stageDots += '<span class="creation-dot-line' + (i < stageIdx ? ' done' : '') + '"></span>';
        });
        stageDots += '</div>';

        const signsNote = a.signs_count ? `<span style="font-size:11px;color:var(--dim);">${a.signs_count} sign${a.signs_count > 1 ? 's' : ''}</span>` : '';

        html += `
        <div class="ab-action-card creation-card" onclick="openCreationDetail(${a.id})" style="cursor:pointer;">
            <div class="ab-action-urgency" style="background:${_creationFreqColor(a.frequency)}"></div>
            <div class="ab-action-info">
                <div class="ab-action-title">${esc(a.title)} ${freqBadge}</div>
                ${stageDots}
                ${a.intention ? `<div class="ab-action-desc" style="margin-top:4px;">${esc(a.intention)}</div>` : ''}
                ${signsNote}
            </div>
        </div>`;
    });

    container.innerHTML = html;
}

function _creationFreqColor(freq) {
    if (!freq || typeof LP === 'undefined') return 'var(--accent)';
    const title = freq.charAt(0).toUpperCase() + freq.slice(1);
    const c = LP.freq[title];
    return c ? c.primary : 'var(--accent)';
}

// ── Creation detail / stage view ────────────────────────────────────────────

async function openCreationDetail(actionId) {
    let item;
    try {
        const res = await fetch(`/api/creation/actions/${actionId}`);
        if (!res.ok) throw new Error(`${res.status}`);
        item = await res.json();
    } catch (e) {
        console.error('Failed to load creation detail:', e);
        return;
    }

    closeFlowOverlay();
    const panel = document.getElementById('panel-container');
    if (!panel) return;

    const overlay = document.createElement('div');
    overlay.id = 'ab-flow-overlay';
    overlay.className = 'ab-flow-embedded';
    overlay.setAttribute('data-return-tab', 'ab-flows');

    const freqTitle = item.frequency ? item.frequency.charAt(0).toUpperCase() + item.frequency.slice(1) : '';
    const stageIdx = CREATION_STAGES.indexOf(item.stage);
    const nextStage = stageIdx < 4 ? CREATION_STAGES[stageIdx + 1] : null;
    const isComplete = item.stage === 'complete';

    // Stage progress bar
    let stageBar = '<div class="creation-stage-track creation-stage-track-detail">';
    CREATION_STAGES.slice(0, 5).forEach((s, i) => {
        const cls = i < stageIdx ? 'done' : i === stageIdx ? 'active' : '';
        stageBar += `<span class="creation-dot ${cls}" title="${STAGE_NAMES[s]}">${STAGE_LABELS[s]}</span>`;
        if (i < 4) stageBar += '<span class="creation-dot-line' + (i < stageIdx ? ' done' : '') + '"></span>';
    });
    stageBar += '</div>';

    // Signs log
    const signs = item.signs_log || [];
    let signsHtml = '';
    if (signs.length > 0) {
        signsHtml = '<div class="creation-signs-section"><h3 style="font-size:13px;color:#888;margin-bottom:8px;">Signs Observed</h3>';
        signs.forEach(s => {
            const when = s.logged_at ? new Date(s.logged_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) : '';
            signsHtml += `<div class="creation-sign-entry"><span class="creation-sign-text">${esc(s.text)}</span><span class="creation-sign-date">${when}</span></div>`;
        });
        signsHtml += '</div>';
    }

    // Stage-specific guidance
    const stageGuidance = {
        broadcast: 'Your broadcast is set. When you are ready, tune into the creation — reflect, direct attention with intention, imagine the completed state.',
        tune: 'Tuning in progress. Reflect on the current state. Direct focus deliberately. Feel the completed creation. When clarity arrives, move to Act.',
        act: 'Execution phase. Work from the frequency you broadcast. When static hits, return to your tuning key. Log Signs as they appear.',
        receive: 'Notice the Signal. What is the Field sending back? Log Signs — synchronicities, unexpected connections, confirming or redirecting feedback.',
        manifest: 'The creation exists. Review: what was broadcast vs what manifested? What Signs appeared? What frequency shift occurred? Close with gratitude.',
        complete: 'This creation arc is complete.',
    };

    // Tuning notes display
    const tn = item.tuning_notes || {};
    let tuningHtml = '';
    if (tn.reflection || tn.attention || tn.imagination || tn.clarity) {
        tuningHtml = '<div style="margin:16px 0;">';
        for (const [key, label] of [['reflection', 'Reflection'], ['attention', 'Attention with Intention'], ['imagination', 'Imagination'], ['clarity', 'Clarity']]) {
            if (tn[key]) tuningHtml += `<div style="margin-bottom:8px;"><span style="color:#888;font-size:11px;">${label}</span><p style="color:#ccc;font-size:13px;margin:2px 0;">${esc(tn[key])}</p></div>`;
        }
        tuningHtml += '</div>';
    }

    overlay.innerHTML = `
        <div class="ab-flow-topbar">
            <button class="ab-flow-back" onclick="closeFlowOverlay()">← Back to Flows</button>
            <span class="ab-flow-title">${freqTitle ? '◆ ' + freqTitle : ''}</span>
        </div>
        <div class="ab-detail-scroll">
            <div class="ab-detail-content">
                <h2 class="ab-detail-title">${esc(item.title)}</h2>
                ${item.tuning_key ? `<p style="color:${_creationFreqColor(item.frequency)};font-style:italic;font-size:13px;margin:4px 0 16px;">"${esc(item.tuning_key)}"</p>` : ''}
                ${stageBar}
                <p style="color:#888;font-size:13px;margin:12px 0;">${stageGuidance[item.stage] || ''}</p>
                ${item.intention ? `<div class="ab-detail-field"><label>Intention</label><p style="color:#ccc;font-size:13px;">${esc(item.intention)}</p></div>` : ''}
                ${tuningHtml}

                ${item.stage === 'tune' ? `
                <div style="margin:16px 0;border-top:1px solid #333;padding-top:12px;">
                    <h3 style="font-size:13px;color:#888;margin-bottom:8px;">Tuning Formula</h3>
                    <div class="ab-detail-field"><label>Reflection — what is the current state?</label><textarea id="cd-tn-reflection" class="ab-detail-input" rows="2" placeholder="Honest assessment before action">${esc(tn.reflection || '')}</textarea></div>
                    <div class="ab-detail-field"><label>Attention with Intention — what outcomes matter?</label><textarea id="cd-tn-attention" class="ab-detail-input" rows="2" placeholder="Specific, deliberate focus">${esc(tn.attention || '')}</textarea></div>
                    <div class="ab-detail-field"><label>Imagination — feel the completed state</label><textarea id="cd-tn-imagination" class="ab-detail-input" rows="2" placeholder="What does it look like when this exists?">${esc(tn.imagination || '')}</textarea></div>
                    <div class="ab-detail-field"><label>Clarity — unified signal</label><textarea id="cd-tn-clarity" class="ab-detail-input" rows="2" placeholder="The broadcast is coherent">${esc(tn.clarity || '')}</textarea></div>
                    <button class="ab-btn" onclick="saveTuningNotes(${item.id})" style="margin-top:8px;">Save Notes</button>
                </div>` : ''}

                ${['act', 'receive'].includes(item.stage) ? `
                <div style="margin:16px 0;border-top:1px solid #333;padding-top:12px;">
                    <h3 style="font-size:13px;color:#888;margin-bottom:8px;">+ Log a Sign</h3>
                    <div class="ab-detail-field">
                        <textarea id="cd-sign-text" class="ab-detail-input" rows="2" placeholder="What did you notice?"></textarea>
                        <button class="ab-btn" onclick="logCreationSign(${item.id})" style="margin-top:8px;">Save Sign</button>
                        <div id="cd-sign-feedback" style="margin-top:4px;"></div>
                    </div>
                </div>` : ''}

                ${signsHtml}

                ${item.stage === 'manifest' ? `
                <div style="margin:16px 0;border-top:1px solid #333;padding-top:12px;">
                    <h3 style="font-size:13px;color:#888;margin-bottom:8px;">Manifestation Review</h3>
                    <div class="ab-detail-field"><label>What was broadcast, and what manifested?</label><textarea id="cd-mn-alignment" class="ab-detail-input" rows="2" placeholder="Alignment check">${esc((item.manifest_notes || {}).alignment || '')}</textarea></div>
                    <div class="ab-detail-field"><label>What frequency shift occurred?</label><textarea id="cd-mn-shift" class="ab-detail-input" rows="2" placeholder="How did the process change your broadcast?">${esc((item.manifest_notes || {}).frequency_shift || '')}</textarea></div>
                    <div class="ab-detail-field"><label>Gratitude</label><textarea id="cd-mn-gratitude" class="ab-detail-input" rows="2" placeholder="Felt appreciation for the creation and the process">${esc((item.manifest_notes || {}).gratitude || '')}</textarea></div>
                    <button class="ab-btn" onclick="saveManifestNotes(${item.id})" style="margin-top:8px;">Save Review</button>
                </div>` : ''}

                <div class="ab-detail-actions" style="margin-top:20px;">
                    ${!isComplete && nextStage ? `<button class="ab-btn ab-btn-schedule" id="cd-advance" onclick="advanceCreation(${item.id}, '${nextStage}')">Advance to ${STAGE_NAMES[nextStage]}</button>` : ''}
                    ${isComplete ? '<span style="color:var(--green);font-weight:600;">Creation Complete ✓</span>' : ''}
                    <button class="ab-btn" onclick="archiveCreation(${item.id})" style="border:1px solid #555;color:#888;background:transparent;margin-left:8px;">Archive</button>
                    <div class="ab-detail-feedback" id="cd-feedback"></div>
                </div>
            </div>
        </div>`;

    panel.appendChild(overlay);
}

async function advanceCreation(actionId, targetStage) {
    const btn = document.getElementById('cd-advance');
    const feedback = document.getElementById('cd-feedback');
    if (btn) { btn.textContent = 'Advancing...'; btn.disabled = true; }

    try {
        const res = await fetch(`/api/creation/actions/${actionId}/stage`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ stage: targetStage }),
        });
        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.error || res.status);
        }

        if (btn) { btn.textContent = STAGE_NAMES[targetStage] + ' ✓'; btn.style.background = 'var(--green)'; btn.style.color = '#000'; }
        _abFlowsLoaded = false;
        setTimeout(() => openCreationDetail(actionId), 800);
    } catch (e) {
        if (feedback) feedback.innerHTML = `<span style="color:var(--red)">Error: ${e.message}</span>`;
        if (btn) { btn.textContent = 'Advance'; btn.disabled = false; }
    }
}

async function logCreationSign(actionId) {
    const text = document.getElementById('cd-sign-text')?.value?.trim();
    const feedback = document.getElementById('cd-sign-feedback');
    if (!text) return;

    try {
        const res = await fetch(`/api/creation/actions/${actionId}/signs`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text }),
        });
        if (!res.ok) throw new Error(`${res.status}`);
        if (feedback) feedback.innerHTML = '<span style="color:var(--green)">Sign logged ✓</span>';
        document.getElementById('cd-sign-text').value = '';
        // Reload to show updated signs
        setTimeout(() => openCreationDetail(actionId), 800);
    } catch (e) {
        if (feedback) feedback.innerHTML = `<span style="color:var(--red)">Error: ${e.message}</span>`;
    }
}

async function saveTuningNotes(actionId) {
    const notes = {
        reflection: document.getElementById('cd-tn-reflection')?.value?.trim() || '',
        attention: document.getElementById('cd-tn-attention')?.value?.trim() || '',
        imagination: document.getElementById('cd-tn-imagination')?.value?.trim() || '',
        clarity: document.getElementById('cd-tn-clarity')?.value?.trim() || '',
    };
    try {
        await fetch(`/api/creation/actions/${actionId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tuning_notes: notes }),
        });
    } catch (e) { console.error('Save tuning notes failed:', e); }
}

async function saveManifestNotes(actionId) {
    const notes = {
        alignment: document.getElementById('cd-mn-alignment')?.value?.trim() || '',
        frequency_shift: document.getElementById('cd-mn-shift')?.value?.trim() || '',
        gratitude: document.getElementById('cd-mn-gratitude')?.value?.trim() || '',
    };
    try {
        await fetch(`/api/creation/actions/${actionId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ manifest_notes: notes }),
        });
    } catch (e) { console.error('Save manifest notes failed:', e); }
}

async function archiveCreation(actionId) {
    if (!confirm('Archive this creation? It will be removed from your active list.')) return;
    try {
        await fetch(`/api/creation/actions/${actionId}`, { method: 'DELETE' });
        _abFlowsLoaded = false;
        closeFlowOverlay();
    } catch (e) { console.error('Archive failed:', e); }
}

// =============================================================================
// Creation Flow seed data
// =============================================================================

function getSeedFlows() {
    // Tuner tier: daily practices and guided exercises
    if (typeof MC !== 'undefined' && MC.isTuner) {
        return [
            { id: 'gratitude-flow', name: 'Gratitude Check-In', description: 'A quick guided practice. Identify three things you appreciate right now and connect them to your current frequency.', category: 'daily', step_count: 3, est_time: '~5 min' },
            { id: 'signal-journal', name: 'Signal Journal', description: 'Write about what you noticed today. What signals came through? What patterns are forming? A simple daily reflection.', category: 'daily', step_count: 4, est_time: '~10 min' },
            { id: 'reframe-exercise', name: 'Reframe Exercise', description: 'Take a situation that feels stuck and walk through a guided reframe using the Lucid Principles framework.', category: 'practice', step_count: 5, est_time: '~10 min' },
        ];
    }
    // Operator+ tier: full Creation Flows
    return [
        { id: 'new-cove-setup', name: 'New Cove Setup', description: 'The complete onboarding journey. Name your Cove, set up your family, configure your steward agent, and generate your provisioner config.', category: 'onboarding', step_count: 7, est_time: '~20 min' },
        { id: 'create-a-product', name: 'Create a Product', description: 'Define a product, configure its agent pipeline, set pricing and delivery, and launch it into your Cove production system.', category: 'business', step_count: 0, est_time: 'Coming soon' },
        { id: 'create-a-business', name: 'Create a Business', description: 'Set up a new business entity. Identity, products, revenue model, agent roles, branding, and operational infrastructure.', category: 'business', step_count: 0, est_time: 'Coming soon' },
        { id: 'create-a-mirror', name: 'Create a Mirror', description: 'Build a philosophical mirror — map passages from your canon to Lucid Principles tuning combinations through your personal lens. The first marketplace product.', category: 'marketplace', step_count: 4, est_time: '~30 min' },
        // New Agent Setup is accessed from Team tab (per-operator) and end of New Cove Setup — not a standalone flow
    ];
}

// =============================================================================
// Tools tab — agent-owned utilities
// =============================================================================
// Each tool belongs to a team agent. This is how operators experience the team —
// everyone has a station. Tools can spawn actions and tasks.
// =============================================================================

// =============================================================================
// Capability cards (#190) — ONE shared card + status shading, sourced from the
// unified /api/capabilities catalog. The Tools + Flows tabs (and later the Market
// lens) all render the same Capability object the same way. Status is derived
// server-side per the viewer (tier + what they own). Spec: capability-system-spec.
// =============================================================================

let _abCaps = null;  // cached { capabilities:[], available:bool }
// Tools defaults to the Active view (the working stations: jules, video, site, +
// owned installs) so the operator lands on what's usable; All is one chip away.
const _abCapFilter = { tools: { status: 'active', category: '' }, flows: { status: '', category: '' } };

async function loadABCapabilities(force) {
    if (_abCaps && !force) return _abCaps;
    try {
        const res = await fetch('/api/capabilities');
        _abCaps = res.ok ? await res.json() : { capabilities: [], available: false };
    } catch (e) {
        _abCaps = { capabilities: [], available: false };
    }
    return _abCaps;
}

const _CAP_STATUS = {
    active:      { label: 'Active',      cls: 'cap-active' },
    available:   { label: 'Available',   cls: 'cap-available' },
    coming_soon: { label: 'Coming soon', cls: 'cap-soon' },
    needs_agent: { label: 'Needs agent', cls: 'cap-needsagent' },
    building:    { label: 'Building',    cls: 'cap-building' },
    wanted:      { label: 'Build this',  cls: 'cap-wanted' },
};

function _capAction(cap) {
    const isFlow = cap.tab === 'flows';
    const open = isFlow ? `openFlow('${esc(cap.slug)}')` : `abCapOpen('${esc(cap.slug)}')`;
    switch (cap.status) {
        case 'active':      return { label: 'Open',            fn: open };
        case 'available':   return { label: isFlow ? 'Open' : 'Install', fn: open };
        case 'needs_agent': return { label: 'Upgrade to Cove', fn: `abCapUpgrade()` };
        // Building shows Resume only to the builder; everyone else sees it read-only
        // (the "Building · @handle" link is their affordance).
        case 'building':    return cap.is_mine
                                ? { label: 'Resume', fn: `abCapBuild('${esc(cap.slug)}')` }
                                : { label: '', fn: '' };
        case 'wanted':      return { label: 'Build this',      fn: `abCapBuild('${esc(cap.slug)}')` };
        default:            return { label: 'Coming soon',     fn: `abCapNotify('${esc(cap.slug)}')` };
    }
}

// Builtin flows (app-native, not marketplace listings) rendered as capability cards.
function _abFlowBuiltins() {
    const flows = (typeof getSeedFlows === 'function') ? getSeedFlows() : [];
    return flows.map(f => ({
        id: f.id, slug: f.id, title: f.name, promise: f.description,
        type: 'flow', tab: 'flows', category: f.category,
        status: (f.est_time === 'Coming soon' || f.step_count === 0) ? 'coming_soon' : 'active',
        build_flow: f.id, _builtin: true, _costFlow: f.id,
        agent_owner: null, requires_agent: false, tuned_safe: false,
    }));
}

// Card image — the listing's image, or a gradient placeholder with a monogram
// (mirrors the Market's cardImg so cards look identical across surfaces).
function _capHue(s) { let h = 0; for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 360; return h; }
function _capImg(cap) {
    if (cap.image_url) return `<img class="ab-cap-img" src="${esc(cap.image_url)}" alt="">`;
    const title = cap.title || cap.slug || '';
    const h1 = _capHue(title), h2 = (h1 + 38) % 360;
    const words = title.trim().split(/\s+/).filter(Boolean);
    const mono = (words.slice(0, 2).map(w => w[0]).join('') || '◇').toUpperCase();
    return `<div class="ab-cap-img ab-cap-ph" style="background:linear-gradient(135deg,hsl(${h1},42%,30%),hsl(${h2},48%,17%))"><span class="ab-cap-mono">${esc(mono)}</span></div>`;
}

function capCardHTML(cap) {
    const s = _CAP_STATUS[cap.status] || _CAP_STATUS.coming_soon;
    const act = _capAction(cap);
    const owner = cap.agent_owner ? `<span class="ab-cap-owner">${esc(cap.agent_owner)}</span>` : '';
    const cat = cap.category ? `<span class="ab-cap-cat">${esc(cap.category)}</span>` : '';
    // Who's building it — the claimer's handle, linked to their Presence profile.
    const builder = (cap.status === 'building' && cap.seller_handle)
        ? `<a class="ab-cap-builder" onclick="event.stopPropagation();abCapProfile('${esc(cap.seller_handle)}')">@${esc(cap.seller_handle)}</a>`
        : '';
    const safe = cap.tuned_safe ? `<span class="ab-cap-safe-tag" title="Tuned + Safe">✓ Tuned + safe</span>` : '';
    const btn = act.label ? `<button class="ab-cap-act" onclick="${act.fn}">${esc(act.label)}</button>` : '';
    return `
    <div class="ab-cap-card ${s.cls}" data-status="${esc(cap.status)}">
        ${_capImg(cap)}
        <div class="ab-cap-top">
            <span class="ab-cap-meta">${cat}${owner}${builder}</span>
            <span class="ab-cap-badge">${s.label}</span>
        </div>
        <div class="ab-cap-title">${esc(cap.title || cap.slug)}</div>
        <div class="ab-cap-promise">${esc(cap.promise || '')}</div>
        <div class="ab-cap-foot">
            ${btn}
            ${cap.tab === 'flows' && cap._costFlow ? `<span class="ab-cap-cost" data-flow="${esc(cap._costFlow)}"></span>` : ''}
            ${safe}
        </div>
    </div>`;
}

// #183 — lazy per-run cost estimate onto flow capability cards (quiet until a flow
// has a profile). Mirrors loadFlowCostBadges but targets the shared card.
async function _loadCapCosts(caps) {
    for (const c of caps.filter(x => x.tab === 'flows' && x._costFlow)) {
        const el = document.querySelector(`.ab-cap-cost[data-flow="${cssEsc(c._costFlow)}"]`);
        if (!el) continue;
        try {
            const est = await fetch(`/api/cost/estimate?flow=${encodeURIComponent(c._costFlow)}`).then(r => r.json());
            if (est && est.has_data && est.headline) el.textContent = `≈ ${est.headline.summary}/run`;
        } catch (e) { /* estimate unavailable — leave blank */ }
    }
}

function _capFilterBarHTML(tab, caps) {
    const cats = [...new Set(caps.map(c => c.category).filter(Boolean))].sort();
    const f = _abCapFilter[tab];
    const statuses = [['active', 'Active'], ['', 'All'], ['available', 'Available'],
                      ['coming_soon', 'Coming soon'], ['needs_agent', 'Needs agent'], ['wanted', 'Build this']];
    let bar = '<div class="ab-cap-filter">';
    statuses.forEach(([v, l]) => {
        bar += `<button class="ab-cap-chip${f.status === v ? ' on' : ''}" onclick="abCapFilter('${tab}','status','${v}')">${l}</button>`;
    });
    if (cats.length) {
        bar += `<select class="ab-cap-catsel" onchange="abCapFilter('${tab}','category',this.value)"><option value="">All categories</option>`;
        cats.forEach(c => { bar += `<option value="${esc(c)}"${f.category === c ? ' selected' : ''}>${esc(c)}</option>`; });
        bar += '</select>';
    }
    return bar + '</div>';
}

function renderCapTab(tab, container, extras) {
    extras = extras || [];
    const extraSlugs = new Set(extras.map(e => e.slug));
    const catalog = ((_abCaps && _abCaps.capabilities) || []).filter(c => c.tab === tab && !extraSlugs.has(c.slug));
    const all = extras.concat(catalog);  // builtins first
    const f = _abCapFilter[tab];
    const caps = all.filter(c => (!f.status || c.status === f.status) && (!f.category || c.category === f.category));
    // "Your apps" = market items you've acquired (owned installs), shown first and
    // distinct from the native builtins + the build-these stubs.
    const mine = caps.filter(c => c.status === 'active' && !c._builtin);
    const rest = caps.filter(c => !(c.status === 'active' && !c._builtin));
    const grid = list => '<div class="ab-cap-grid">' + list.map(capCardHTML).join('') + '</div>';
    let body = '';
    if (mine.length) body += '<div class="ab-cap-section">Your apps</div>' + grid(mine);
    if (rest.length) body += (mine.length ? '<div class="ab-cap-section">Discover</div>' : '') + grid(rest);
    if (!body) body = '<div class="ab-empty">Nothing here for this filter yet.</div>';
    // Wrap in one full-width block so it spans the parent grid/flex container.
    container.innerHTML = '<div class="ab-cap-wrap">' + _capFilterBarHTML(tab, all) + body + '</div>';
    if (tab === 'flows') _loadCapCosts(caps);
}

function _abToolBuiltins() {
    // jules (voice transcription) is a built-in Cove tool, not a hub listing — always
    // present on the Tools tab so it's reachable without digging into Settings (#203).
    if (typeof MC !== 'undefined' && MC.isTuner) return [];
    return [{
        id: 'jules', slug: 'jules', title: 'Jules',
        promise: 'Voice transcription — tap, talk, and save straight to your vault.',
        type: 'tool', tab: 'tools', category: 'voice',
        status: 'active', build_flow: null, _builtin: true,
        agent_owner: 'Jules', requires_agent: false, tuned_safe: false,
    }, {
        id: 'video-shorts-pipeline', slug: 'video-shorts-pipeline', title: 'Video Pipeline',
        promise: 'Drop a video — transcript, clips, captions, and scheduled posts.',
        type: 'tool', tab: 'tools', category: 'video',
        status: 'active', build_flow: null, _builtin: true,
        agent_owner: 'Stuart', requires_agent: false, tuned_safe: false,
    }, {
        id: 'site-builder', slug: 'site-builder', title: 'Site Builder',
        promise: 'Build and publish a website your agents manage.',
        type: 'tool', tab: 'tools', category: 'web',
        status: 'active', build_flow: null, _builtin: true,
        agent_owner: 'Archimedes', requires_agent: false, tuned_safe: false,
    }, {
        id: 'rent-gpu', slug: 'rent-gpu', title: 'Rent GPU',
        promise: 'Share GPU power between Coves — offer yours, or run transcription on another Cove’s.',
        type: 'tool', tab: 'tools', category: 'compute',
        status: 'active', build_flow: null, _builtin: true,
        agent_owner: 'Stuart', requires_agent: false, tuned_safe: false,
    }, {
        id: 'gpu-marketplace', slug: 'gpu-marketplace', title: 'GPU Marketplace',
        promise: 'Find a Cove to run your heavy work — or list yours. Discovery + credits, coming soon.',
        type: 'tool', tab: 'tools', category: 'compute',
        status: 'soon', build_flow: null, _builtin: true,
        agent_owner: 'Stuart', requires_agent: false, tuned_safe: false,
    }];
}

function _capExtrasFor(tab) {
    return tab === 'flows' ? _abFlowBuiltins() : _abToolBuiltins();
}

function abCapFilter(tab, key, val) {
    _abCapFilter[tab][key] = val;
    const container = document.getElementById(tab === 'tools' ? 'ab-tools-list' : 'ab-flows-list');
    if (container) renderCapTab(tab, container, _capExtrasFor(tab));
}

function _capBySlug(slug) {
    return ((_abCaps && _abCaps.capabilities) || []).find(c => c.slug === slug);
}

function abCapProfile(handle) { if (window.PresenceProfile) window.PresenceProfile.open(handle); }

function abCapOpen(slug) {
    const cap = _capBySlug(slug);
    // Available (a real listing you don't own yet) → go buy it in the Market.
    if (cap && cap.status === 'available') {
        if (window.openConnectMarket) window.openConnectMarket();
        return;
    }
    // Owned / runnable → open the tool or its page.
    const page = FLOW_PAGES[slug] || (cap && cap.build_flow && FLOW_PAGES[cap.build_flow]);
    if (typeof openTool === 'function' && (FLOW_PAGES[slug] || slug === 'jules' || slug === 'video-pipeline' || slug === 'video-shorts-pipeline')) { openTool(slug); return; }
    if (page) { openFlowOverlay(page, cap && cap.tab === 'flows' ? 'ab-flows' : 'ab-tools', cap ? cap.title : slug); return; }
    if (typeof showToolPlaceholder === 'function') showToolPlaceholder(cap ? cap.title : slug, cap ? (cap.agent_owner || '') : '');
}
function abCapUpgrade() { if (typeof showUpgradeModal === 'function') showUpgradeModal('cove'); }

// The build→list loop (#190 S4). "Build this" on a wanted card claims the listing
// (it becomes the operator's draft), flips the card to Building, and routes them to
// finish + price + publish it under Market → My Offerings (Tuned + Safe at publish).
// "Resume" on a building card sends them back to finish it.
async function abCapBuild(slug) {
    const cap = _capBySlug(slug);
    if (!cap) return;
    const returnTab = cap.tab === 'flows' ? 'ab-flows' : 'ab-tools';

    // Already claimed/building → go finish it (build-flow page if any, else My Offerings).
    if (cap.status === 'building') {
        if (cap.build_flow && FLOW_PAGES[cap.build_flow]) { openFlowOverlay(FLOW_PAGES[cap.build_flow], returnTab, 'Build: ' + cap.title); return; }
        if (window.openConnectMarket) window.openConnectMarket('offerings');
        return;
    }

    // Wanted gap with a real listing → claim it, flip the card, route to finish.
    if (cap.id != null) {
        try {
            const r = await fetch('/api/market/claim', {
                method: 'POST', credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ listing_id: cap.id }),
            });
            if (!r.ok) { alert('Could not claim this (' + r.status + ').'); return; }
        } catch (e) { alert('Could not claim this right now.'); return; }
        await loadABCapabilities(true);  // card flips wanted → building (it's now your draft)
        const container = document.getElementById(cap.tab === 'tools' ? 'ab-tools-list' : 'ab-flows-list');
        if (container) renderCapTab(cap.tab, container, _capExtrasFor(cap.tab));
        if (window.openConnectMarket) window.openConnectMarket('offerings');
        return;
    }

    // No listing (a builtin coming-soon flow) → open its build-flow page if one exists.
    if (cap.build_flow && FLOW_PAGES[cap.build_flow]) { openFlowOverlay(FLOW_PAGES[cap.build_flow], returnTab, 'Build: ' + cap.title); return; }
    if (typeof showToolPlaceholder === 'function') showToolPlaceholder(cap.title + ' — Build this', 'finish it under Market → My Offerings');
}
function abCapNotify() { /* coming_soon — notify wiring is post-launch */ }

async function loadABTools() {
    if (_abToolsLoaded) return;
    const container = document.getElementById('ab-tools-list');
    if (!container) return;

    if (typeof MC !== 'undefined' && MC.isTuner) {
        container.innerHTML = '<div class="ab-empty">Tools unlock at Operator.</div>';
        _abToolsLoaded = true;
        return;
    }

    await loadABCapabilities();
    const hasCatalogTools = _abCaps.available && (_abCaps.capabilities || []).some(c => c.tab === 'tools');
    if (hasCatalogTools) {
        renderCapTab('tools', container, _capExtrasFor('tools'));   // catalog + built-in jules
    } else {
        renderTools(container, getSeedTools());  // offline fallback (no hub wired)
    }
    _abToolsLoaded = true;
}

function renderTools(container, tools) {
    if (tools.length === 0) {
        container.innerHTML = '<div class="ab-empty">No tools available yet.</div>';
        return;
    }

    let html = '';
    tools.forEach(tool => {
        const statusClass = tool.status === 'active' ? 'tool-active' : 'tool-placeholder';
        const agentColor = tool.agent_color || 'var(--accent)';
        html += `
        <div class="ab-tool-card ${statusClass}" onclick="openTool('${esc(tool.id)}')" style="--tool-agent-color: ${agentColor}">
            <div class="ab-tool-header">
                <h3>${esc(tool.name)}</h3>
                <span class="ab-tool-agent">${esc(tool.agent)}</span>
            </div>
            <p class="ab-tool-desc">${esc(tool.description)}</p>
            <div class="ab-tool-meta">
                ${tool.status === 'active' ? '<span class="ab-tool-status-active">Active</span>' : '<span class="ab-tool-status-soon">Coming soon</span>'}
            </div>
        </div>`;
    });
    container.innerHTML = html;
}

function openTool(id) {
    // Tool-specific pages take priority, then check shared FLOW_PAGES
    const toolPages = {
        'video-shorts-pipeline': '/static/action-board/full-video-pipeline.html',
        'jules': '/jules',  // voice transcription, served by this Cove's MC (#203)
    };
    const url = toolPages[id] || FLOW_PAGES[id];
    if (!url) {
        // No page yet — show coming soon placeholder
        const tool = getSeedTools().find(t => t.id === id);
        const name = tool ? tool.name : id;
        const agent = tool ? tool.agent : '';
        showToolPlaceholder(name, agent);
        return;
    }

    const tool = getSeedTools().find(t => t.id === id);
    const title = tool?.name || id;
    openFlowOverlay(url, 'ab-tools', title);
}

function showToolPlaceholder(name, agent) {
    const panel = document.getElementById('panel-container');
    if (!panel) return;

    closeFlowOverlay(true);
    const overlay = document.createElement('div');
    overlay.id = 'ab-flow-overlay';
    overlay.className = 'ab-flow-embedded';
    overlay.setAttribute('data-return-tab', 'ab-tools');

    overlay.innerHTML = `
        <div class="ab-flow-topbar">
            <button class="ab-flow-back" onclick="closeFlowOverlay()">← Back to Tools</button>
            <span class="ab-flow-title">${name}</span>
        </div>
        <div class="ab-tool-placeholder-body">
            <div class="ab-tool-placeholder-icon">🔧</div>
            <h2>${name}</h2>
            <p class="ab-tool-placeholder-agent">Owned by <strong>${agent}</strong></p>
            <p class="ab-tool-placeholder-msg">This tool is being built. When it's ready, ${agent} will be here to help.</p>
        </div>`;

    panel.appendChild(overlay);
}

function getSeedTools() {
    // Tuner tier: no tools (tools are Operator+)
    if (typeof MC !== 'undefined' && MC.isTuner) return [];

    return [
        { id: 'video-shorts-pipeline', name: 'Video Shorts Pipeline', agent: 'Stuart', agent_color: 'var(--accent)', description: 'End-to-end short-form video production. Upload, transcribe, generate shorts, schedule posts.', status: 'active' },
        { id: 'jules', name: 'Jules', agent: 'Jules', agent_color: 'var(--accent)', description: 'Voice transcription — tap, talk, and save straight to your vault.', status: 'active' },
        { id: 'gabs', name: 'Gabs', agent: 'Gabe', agent_color: 'var(--green)', description: 'Drop a link and Gabe researches it. The team synthesizes findings into a packaged brief.', status: 'placeholder' },
        { id: 'site-builder', name: 'Site Builder', agent: 'Archimedes', agent_color: 'var(--blue, #5b9bd5)', description: 'Build and deploy sites. Domain, hosting, design, and launch — guided by Archimedes.', status: 'active' },
        { id: 'copy-studio', name: 'Copy Studio', agent: 'Iris', agent_color: 'var(--purple, #b07cd8)', description: 'Marketing copy, emails, social posts. Brand voice enforced by Iris.', status: 'placeholder' },
        { id: 'signal-scanner', name: 'Signal Scanner', agent: 'Ezra', agent_color: 'var(--yellow, #d4a843)', description: 'Market analysis, data patterns, competitive intel. Ezra scans the signal.', status: 'placeholder' },
        { id: 'daily-brief', name: 'Daily Brief', agent: 'Stuart', agent_color: 'var(--accent)', description: 'Morning coordination. What\'s active, what needs attention, team status.', status: 'placeholder' },
    ];
}
