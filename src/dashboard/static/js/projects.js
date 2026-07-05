// =============================================================================
// projects.js — Projects tab + detail views (visual efficiency design)
// =============================================================================
// Uses same design language as home.js: priority dots, agent initials,
// due date coloring, config-driven dropdowns, inline expand editing.
// =============================================================================

// ── Type-aware helper ───────────────────────────────────────────────────────
function _projIsAdmin() {
    const t = MC.instance?.type;
    return t === 'admin' || t === 'domain' || t === 'manager';
}

// ── Shared constants (match home.js) ────────────────────────────────────────
// ── Workflow state colors (used in task rows + detail page) ─────────────────
const _wfStateColors = {
    'building':   'var(--orange)',
    'reviewing':  '#9370db',
    'deploying':  'var(--accent)',
    'deployed':   'var(--green)',
    'auditing':   '#9370db',
    'approved':   'var(--green)',
    'rejected':   'var(--red)',
    'blocked':    '#4682b4',
    'waiting':    'var(--yellow)',
};

// ── Priority colors (frequency-derived — see LP object in core.js) ──────────
const _projPriColors = {
    'urgent':  'var(--red)',     // Momentum
    'high':    'var(--orange)',  // Courage
    'normal':  'var(--silver)',  // Trust
    'low':     'rgba(128,128,128,0.3)',
};

// ── Project status colors (frequency-derived) ──────────────────────────────
const _projStatusColors = {
    'active':   'var(--green)',   // Abundance — growth
    'paused':   'var(--yellow)',  // Joy — holding space
    'planning': 'var(--accent)',  // Peace — calm intention
    'done':     'var(--dim)',
    'archived': 'rgba(128,128,128,0.3)',
};

// ── Helpers (mirror home.js patterns) ───────────────────────────────────────

function _projAgentInitial(assignee) {
    if (!assignee) return '';
    return lpAgentBadgeHTML(assignee);
}

function _projDueDateHTML(due_date) {
    if (!due_date) return '';
    const d = new Date(due_date + 'T12:00:00');
    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const tomorrow = new Date(today); tomorrow.setDate(today.getDate() + 1);
    const dueDay = new Date(d.getFullYear(), d.getMonth(), d.getDate());

    let cls = 'due-later';
    if (dueDay < today) cls = 'due-overdue';
    else if (dueDay <= tomorrow) cls = 'due-soon';

    const label = formatDate(due_date, { month: 'short', day: 'numeric' });
    return `<span class="task-due ${cls}">${label}</span>`;
}

function _projAssigneeDropdown(id, currentAssignee) {
    const agents = MC.agents || [];
    const operator = MC.instance?.operator || 'Operator';
    let opts = `<option value="">Unassigned</option>`;
    opts += `<option value="${ESC(operator.toLowerCase())}"${currentAssignee === operator.toLowerCase() ? ' selected' : ''}>${ESC(operator)}</option>`;
    agents.forEach(a => {
        const sel = (currentAssignee === a.id || currentAssignee === a.name?.toLowerCase()) ? ' selected' : '';
        opts += `<option value="${ESC(a.id)}"${sel}>${ESC(a.name || a.id)}</option>`;
    });
    return `<select id="${id}" class="task-select">${opts}</select>`;
}

// ── Task row for project context (same visual as home.js tasks) ─────────────

let _projExpandedTaskId = null;

function _projTaskItemHTML(t, projectId) {
    const pri = t.priority || 'normal';
    const dotColor = _projPriColors[pri] || _projPriColors.normal;
    const priTitle = pri.charAt(0).toUpperCase() + pri.slice(1);
    const agentBadge = _projIsAdmin() ? _projAgentInitial(t.assignee) : '';
    const noteIcon = t.notes ? '<span class="task-note-icon" title="Has notes">&#128221;</span>' : '';
    const dueHTML = _projDueDateHTML(t.due_date);

    // Done tasks get strikethrough
    const titleStyle = t.status === 'done' ? ' style="text-decoration:line-through;color:var(--dim);"' : '';

    // Workflow state badge
    const wfBadge = t.workflow_state ? `<span class="wf-badge wf-badge-sm" style="color:${_wfStateColors?.[t.workflow_state?.toLowerCase()] || 'var(--dim)'};border-color:${_wfStateColors?.[t.workflow_state?.toLowerCase()] || 'var(--dim)'};">${ESC(t.workflow_state)}</span>` : '';

    // Sub-task count indicator
    const subCount = (t.total_subtasks && t.total_subtasks > 0)
        ? `<span class="task-sub-count" title="${t.done_subtasks || 0}/${t.total_subtasks} sub-tasks done">${t.done_subtasks || 0}/${t.total_subtasks}</span>`
        : '';

    return `<div class="task-item" onclick="toggleProjTaskEdit(${t.id}, ${projectId})" data-task-id="${t.id}">
        <span class="task-pri-dot" style="background:${dotColor};" title="${ESC(priTitle)} priority"></span>
        <div class="task-info">
            <div class="task-title-row">
                <span class="task-title"${titleStyle}>${ESC(t.title || '')}${noteIcon}</span>
                <span class="task-detail-link" onclick="event.stopPropagation(); showTaskDetail(${t.id}, ${projectId})" title="Open detail">→</span>
            </div>
            <div class="task-meta-row">
                <div class="task-meta-left">
                    ${agentBadge}${wfBadge}${subCount}
                </div>
                ${dueHTML}
            </div>
        </div>
    </div>
    <div class="task-edit-form" id="ptask-edit-${t.id}" style="display:none;">
        <div class="task-edit-row">
            <label>Title</label>
            <input type="text" id="pt-title-${t.id}" value="${ESC(t.title || '')}" class="task-input">
        </div>
        <div class="task-edit-row task-edit-grid">
            <div>
                <label>Status</label>
                <select id="pt-status-${t.id}" class="task-select">
                    ${['pending','in_progress','blocked','review','done','cancelled'].map(s =>
                        `<option value="${s}"${t.status === s ? ' selected' : ''}>${s.replace('_',' ')}</option>`
                    ).join('')}
                </select>
            </div>
            <div>
                <label>Priority</label>
                <select id="pt-pri-${t.id}" class="task-select">
                    ${['urgent','high','normal','low'].map(p =>
                        `<option value="${p}"${t.priority === p ? ' selected' : ''}>${p}</option>`
                    ).join('')}
                </select>
            </div>
        </div>
        <div class="task-edit-row task-edit-grid">
            ${_projIsAdmin() ? `<div>
                <label>Assignee</label>
                ${_projAssigneeDropdown(`pt-assign-${t.id}`, t.assignee || '')}
            </div>` : ''}
            <div>
                <label>Due date</label>
                <input type="date" id="pt-due-${t.id}" value="${t.due_date || ''}" class="task-input">
            </div>
        </div>
        <div class="task-edit-row">
            <label>Notes</label>
            <textarea id="pt-notes-${t.id}" class="task-input task-textarea" rows="2" placeholder="Optional notes...">${ESC(t.notes || '')}</textarea>
        </div>
        <div class="task-edit-actions">
            <button class="btn-small btn-save" onclick="event.stopPropagation(); saveProjTask(${t.id}, ${projectId})">Save</button>
            <button class="btn-small btn-cancel" onclick="event.stopPropagation(); closeProjTaskEdit(${t.id})">Cancel</button>
        </div>
    </div>`;
}

function toggleProjTaskEdit(taskId, projectId) {
    const form = document.getElementById(`ptask-edit-${taskId}`);
    if (!form) return;
    if (_projExpandedTaskId === taskId) {
        closeProjTaskEdit(taskId);
        return;
    }
    if (_projExpandedTaskId !== null) {
        const prev = document.getElementById(`ptask-edit-${_projExpandedTaskId}`);
        if (prev) prev.style.display = 'none';
    }
    form.style.display = 'block';
    _projExpandedTaskId = taskId;
}

function closeProjTaskEdit(taskId) {
    const form = document.getElementById(`ptask-edit-${taskId}`);
    if (form) form.style.display = 'none';
    if (_projExpandedTaskId === taskId) _projExpandedTaskId = null;
}

async function saveProjTask(taskId, projectId) {
    const body = {
        title: document.getElementById(`pt-title-${taskId}`)?.value || '',
        status: document.getElementById(`pt-status-${taskId}`)?.value || 'pending',
        priority: document.getElementById(`pt-pri-${taskId}`)?.value || 'normal',
        assignee: document.getElementById(`pt-assign-${taskId}`)?.value || '',
        due_date: document.getElementById(`pt-due-${taskId}`)?.value || null,
        notes: document.getElementById(`pt-notes-${taskId}`)?.value || '',
    };
    try {
        const res = await fetch(`/api/tasks/${taskId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        _projExpandedTaskId = null;
        // Refresh project detail
        const pid = currentProjectId || projectId;
        if (pid) showProjectDetail(pid);
    } catch (e) {
        alert('Failed to save: ' + e.message);
    }
}

// =============================================================================
// Project Detail
// =============================================================================
let currentProjectId = null;

async function showProjectDetail(projectId) {
    currentProjectId = projectId;

    // Hide all panels, show project detail (class-only)
    document.querySelectorAll('.panel').forEach(p => {
        p.classList.remove('active', 'active-grid', 'active-flex');
        p.style.display = '';
    });
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    const detail = document.getElementById('panel-project-detail');
    detail.classList.add('active-grid');

    // Reset
    document.getElementById('pdp-name').textContent = 'Loading...';
    document.getElementById('pdp-desc').textContent = '';
    document.getElementById('pdp-tasks').innerHTML = '<span class="empty">Loading...</span>';
    document.getElementById('pdp-comments').innerHTML = '<span class="empty">Loading...</span>';

    try {
        const res = await fetch(`/api/projects/${projectId}`);
        const data = await res.json();
        if (data.error) {
            document.getElementById('pdp-name').textContent = 'Error';
            document.getElementById('pdp-desc').textContent = data.error;
            return;
        }

        const p = data.project || data;

        // Header — status dot instead of text badge
        document.getElementById('pdp-name').textContent = p.name || '';
        document.getElementById('pdp-desc').textContent = p.description || '';
        const statusEl = document.getElementById('pdp-status');
        const sColor = _projStatusColors[p.status] || 'var(--dim)';
        statusEl.innerHTML = `<span class="proj-status-dot" style="background:${sColor};" title="${ESC(p.status || 'active')}"></span>`;

        // Owner as agent initial
        const ownerEl = document.getElementById('pdp-owner');
        if (p.owner) {
            ownerEl.innerHTML = _projAgentInitial(p.owner) + ` <span class="accent">${ESC(lpAgentName(p.owner))}</span>`;
        } else {
            ownerEl.innerHTML = '<span class="dim">Unassigned</span>';
        }

        document.getElementById('pdp-created').textContent = p.created_at ? formatDateOnly(p.created_at) : '';

        // Goals
        const goalsEl = document.getElementById('pdp-goals');
        if (p.goals) {
            const goalsList = typeof p.goals === 'string' ? p.goals.split('\n').filter(g => g.trim()) : (Array.isArray(p.goals) ? p.goals : []);
            if (goalsList.length) {
                goalsEl.innerHTML = goalsList.map(g => `<div style="padding:3px 0;font-size:0.8rem;color:var(--text);line-height:1.5;">${esc(g)}</div>`).join('');
            } else {
                goalsEl.innerHTML = '<span class="empty">No goals set</span>';
            }
        } else {
            goalsEl.innerHTML = '<span class="empty">No goals set</span>';
        }

        // Team — agent initial circles
        const teamEl = document.getElementById('pdp-team');
        if (p.team) {
            const members = typeof p.team === 'string' ? p.team.split(',').map(s => s.trim()).filter(Boolean) : (Array.isArray(p.team) ? p.team : []);
            if (members.length) {
                teamEl.innerHTML = members.map(m => _projAgentInitial(m)).join(' ');
            } else {
                teamEl.innerHTML = '<span class="empty">No team assigned</span>';
            }
        } else {
            teamEl.innerHTML = '<span class="empty">No team assigned</span>';
        }

        // Populate assignee dropdown for new task form (admin/domain only)
        const assigneeSelect = document.getElementById('pdp-new-assignee');
        if (assigneeSelect) {
            if (_projIsAdmin()) {
                const agents = MC.agents || [];
                const operator = MC.instance?.operator || 'Operator';
                assigneeSelect.innerHTML = `<option value="">Assignee</option>
                    <option value="${ESC(operator.toLowerCase())}">${ESC(operator)}</option>
                    ${agents.map(a => `<option value="${ESC(a.id)}">${ESC(a.name || a.id)}</option>`).join('')}`;
            } else {
                assigneeSelect.style.display = 'none';
            }
        }

        // Tasks — using new visual system
        renderProjectDetailTasks(data.tasks || []);

        // Events & Deadlines — calendar events linked to this project + task due dates
        renderProjectEvents(projectId, data.tasks || []);

        // Comments
        renderProjectDetailComments(data.comments || []);

    } catch (e) {
        document.getElementById('pdp-name').textContent = 'Error';
        document.getElementById('pdp-desc').textContent = e.message;
    }
}

function renderProjectDetailTasks(tasks) {
    const el = document.getElementById('pdp-tasks');
    const countEl = document.getElementById('pdp-task-count');
    const progressEl = document.getElementById('pdp-progress');

    const done = tasks.filter(t => t.status === 'done').length;
    const total = tasks.length;
    const pct = total > 0 ? Math.round((done / total) * 100) : 0;
    countEl.textContent = `${done}/${total}`;
    progressEl.style.width = pct + '%';

    if (!tasks.length) {
        el.innerHTML = '<span class="empty">No tasks yet</span>';
        return;
    }

    // Show active tasks first, done tasks at bottom
    const active = tasks.filter(t => t.status !== 'done');
    const completed = tasks.filter(t => t.status === 'done');
    const sorted = [...active, ...completed];

    el.innerHTML = sorted.map(t => _projTaskItemHTML(t, currentProjectId)).join('');
}

function renderProjectDetailComments(comments) {
    const el = document.getElementById('pdp-comments');
    if (!comments.length) {
        el.innerHTML = '<span class="empty">No notes yet</span>';
        return;
    }

    el.innerHTML = comments.map(c => {
        const when = c.created_at ? formatDate(c.created_at) : '';
        const authorBadge = _projAgentInitial(c.author || (MC.instance?.operator?.toLowerCase() || 'operator'));
        return `<div class="pdp-comment">
            <div class="pdp-comment-header">${authorBadge}<span class="pdp-comment-meta">${when}</span></div>
            <div class="pdp-comment-body">${esc(c.content || '')}</div>
        </div>`;
    }).join('');
}

async function renderProjectEvents(projectId, tasks) {
    const el = document.getElementById('pdp-events');
    if (!el) return;

    try {
        // Fetch calendar events linked to this project (via event_links)
        const evtData = await fetch(`/api/calendar/events?days=365`).then(r => r.json()).catch(() => ({ events: [] }));
        const linkData = await fetch(`/api/calendar/links?project_id=${projectId}`).then(r => r.json()).catch(() => ({ links: {} }));

        const linkedUids = new Set(Object.keys(linkData.links || {}));
        const linkedEvents = (evtData.events || []).filter(e => linkedUids.has(e.uid));

        // Tasks with due dates from this project
        const dueTasks = tasks.filter(t => t.due_date);

        if (!linkedEvents.length && !dueTasks.length) {
            el.innerHTML = '<span class="empty">No events or deadlines</span>';
            return;
        }

        // Build a combined timeline sorted by date
        const items = [];

        linkedEvents.forEach(e => {
            const dateKey = e.start ? e.start.substring(0, 10) : '9999-99-99';
            let time = '';
            if (!e.all_day && e.start && e.start.length > 10) {
                const t = new Date(e.start);
                time = formatTime(e.start);
            }
            items.push({ type: 'event', date: dateKey, time: time || 'All day', title: e.summary || 'Untitled', uid: e.uid });
        });

        dueTasks.forEach(t => {
            items.push({ type: 'task', date: t.due_date, time: '', title: t.title, status: t.status, priority: t.priority });
        });

        items.sort((a, b) => a.date.localeCompare(b.date));

        const today = new Date();
        today.setHours(0, 0, 0, 0);

        el.innerHTML = items.map(item => {
            const d = new Date(item.date + 'T12:00:00');
            const isPast = d < today;
            const isToday = d.toDateString() === today.toDateString();
            const dateLabel = isToday ? 'Today' : formatDate(item.date, { month: 'short', day: 'numeric' });

            if (item.type === 'event') {
                return `<div class="pdp-timeline-item">
                    <span class="pdp-tl-date${isToday ? ' event-today' : ''}">${ESC(dateLabel)}</span>
                    <span class="pdp-tl-icon">📅</span>
                    <span class="pdp-tl-title" ${item.uid ? `onclick="switchTab('calendar');setTimeout(()=>editCalEvent('${ESC(item.uid)}'),300);" style="cursor:pointer;color:var(--accent);"` : ''}>${ESC(item.title)}</span>
                    <span class="pdp-tl-time">${ESC(item.time)}</span>
                </div>`;
            } else {
                const prioClass = item.priority === 'critical' || item.priority === 'urgent' ? 'due-overdue'
                                : item.priority === 'high' ? 'due-soon' : '';
                const statusIcon = item.status === 'done' ? '✓' : '○';
                return `<div class="pdp-timeline-item${isPast && item.status !== 'done' ? ' pdp-tl-overdue' : ''}">
                    <span class="pdp-tl-date${isToday ? ' event-today' : ''}">${ESC(dateLabel)}</span>
                    <span class="pdp-tl-icon">${statusIcon}</span>
                    <span class="pdp-tl-title ${prioClass}">${ESC(item.title)}</span>
                </div>`;
            }
        }).join('');

    } catch (e) {
        el.innerHTML = '<span class="empty">Could not load events</span>';
    }
}

async function addTaskFromDetail() {
    if (!currentProjectId) return;
    const titleEl = document.getElementById('pdp-new-task');
    const priorityEl = document.getElementById('pdp-new-priority');
    const assigneeEl = document.getElementById('pdp-new-assignee');
    const title = titleEl.value.trim();
    if (!title) return;

    try {
        await fetch('/api/tasks', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                project_id: currentProjectId,
                title: title,
                priority: priorityEl?.value || 'normal',
                assignee: assigneeEl?.value || '',
            }),
        });
        titleEl.value = '';
        showProjectDetail(currentProjectId);
    } catch (e) {
        console.error('Failed to add task:', e);
    }
}

async function addCommentFromDetail() {
    if (!currentProjectId) return;
    const inputEl = document.getElementById('pdp-new-comment');
    const content = inputEl.value.trim();
    if (!content) return;

    try {
        await fetch(`/api/projects/${currentProjectId}/comments`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content: content, author: MC.instance?.operator?.toLowerCase() || 'operator' }),
        });
        inputEl.value = '';
        showProjectDetail(currentProjectId);
    } catch (e) {
        console.error('Failed to add comment:', e);
    }
}

function backToProjects() {
    currentProjectId = null;
    _projExpandedTaskId = null;
    document.querySelectorAll('.panel').forEach(p => {
        p.classList.remove('active', 'active-grid', 'active-flex');
        p.style.display = '';
    });
    switchToTab('projects');
}

// =============================================================================
// Projects Tab — Card listing (redesigned)
// =============================================================================
async function loadProjectsTab() {
    const el = document.getElementById('projectsTabList');
    try {
        const res = await fetch('/api/projects');
        const data = await res.json();
        const projects = data.projects || [];

        if (!projects.length) {
            el.innerHTML = '<span class="empty">No projects yet</span>';
            return;
        }

        el.innerHTML = projects.map(p => {
            const done = p.done_tasks || p.tasks_done || 0;
            const total = p.total_tasks || p.tasks_total || 0;
            const pct = total > 0 ? Math.round((done / total) * 100) : 0;
            const ownerBadge = _projIsAdmin() ? _projAgentInitial(p.owner || '') : '';

            // Priority derived from highest-priority open task
            const pri = p.top_priority || 'normal';
            const priColor = _projPriColors[pri] || _projPriColors.normal;
            const priTitle = pri.charAt(0).toUpperCase() + pri.slice(1);

            return `<div class="proj-card" id="project-card-${p.id}" data-project-id="${p.id}">
                <div class="proj-card-row" onclick="showProjectDetail(${p.id})">
                    <span class="task-pri-dot" style="background:${priColor};" title="${ESC(priTitle)} priority"></span>
                    <div class="proj-card-info">
                        <div class="proj-card-title">${ESC(p.name || '')}</div>
                        <div class="task-meta-row">
                            <div class="task-meta-left">
                                ${ownerBadge}
                                <span class="proj-task-fraction">${done}/${total}</span>
                            </div>
                            ${p.next_due ? _projDueDateHTML(p.next_due) : ''}
                        </div>
                    </div>
                    <div class="proj-card-progress">
                        <div class="progress-bar-track"><div class="progress-bar-fill" style="width:${pct}%"></div></div>
                    </div>
                </div>
            </div>`;
        }).join('');
    } catch (e) {
        el.innerHTML = `<span class="empty">Error: ${ESC(e.message)}</span>`;
    }
}

async function createProjectFromTab() {
    const nameEl = document.getElementById('newProjectName');
    const name = nameEl.value.trim();
    if (!name) return;

    const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
    try {
        await fetch('/api/projects', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, slug, status: 'active' }),
        });
        nameEl.value = '';
        loadProjectsTab();
    } catch (e) {
        console.error('Failed to create project:', e);
    }
}

// ── Legend toggle (project detail) ──────────────────────────────────────────

function toggleProjAddForm() {
    const el = document.getElementById('proj-tab-add-form');
    if (el) {
        el.style.display = el.style.display === 'none' ? 'flex' : 'none';
        if (el.style.display === 'flex') {
            document.getElementById('newProjectName')?.focus();
        }
    }
}

function toggleProjLegend() {
    const el = document.getElementById('proj-legend');
    if (el) el.style.display = el.style.display === 'none' ? 'flex' : 'none';
}

// =============================================================================
// Task Detail — drills down from project detail or task list
// =============================================================================

let currentTaskId = null;
let _taskDetailProject = null;

function _wfBadgeHTML(state) {
    if (!state) return '';
    const color = _wfStateColors[state.toLowerCase()] || 'var(--dim)';
    return `<span class="wf-badge" style="color:${color};border-color:${color};">${ESC(state)}</span>`;
}

async function showTaskDetail(taskId, fromProjectId) {
    currentTaskId = taskId;
    if (fromProjectId) _taskDetailProject = { id: fromProjectId };

    // Switch panels
    document.querySelectorAll('.panel').forEach(p => {
        p.classList.remove('active', 'active-grid', 'active-flex');
        p.style.display = '';
    });
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    const detail = document.getElementById('panel-task-detail');
    detail.classList.add('active');

    // Reset
    document.getElementById('tdp-title').textContent = 'Loading...';
    document.getElementById('tdp-subtasks').innerHTML = '<span class="empty">Loading...</span>';
    document.getElementById('tdp-activity').innerHTML = '<span class="empty">Loading...</span>';

    try {
        const res = await fetch(`/api/tasks/${taskId}/detail`);
        const data = await res.json();
        if (data.error) {
            document.getElementById('tdp-title').textContent = 'Error: ' + data.error;
            return;
        }

        const t = data.task;
        _taskDetailProject = data.project || _taskDetailProject;

        // Breadcrumb
        const bc = document.getElementById('tdp-breadcrumb');
        let crumbs = `<button class="back-btn" onclick="backToProjects()">Projects</button>`;
        if (_taskDetailProject) {
            crumbs += ` <span class="tdp-bc-sep">›</span> <a class="tdp-bc-link" onclick="showProjectDetail(${_taskDetailProject.id})">${ESC(_taskDetailProject.name || 'Project')}</a>`;
        }
        if (data.parent) {
            crumbs += ` <span class="tdp-bc-sep">›</span> <a class="tdp-bc-link" onclick="showTaskDetail(${data.parent.id})">${ESC(data.parent.title || 'Parent')}</a>`;
        }
        crumbs += ` <span class="tdp-bc-sep">›</span> <span class="tdp-bc-current">${ESC(t.title || '')}</span>`;
        bc.innerHTML = crumbs;

        // Status badge
        const statusEl = document.getElementById('tdp-status');
        const statusLabel = (t.status || 'pending').replace('_', ' ');
        const statusColor = t.status === 'done' ? 'var(--green)' :
                            t.status === 'in_progress' ? 'var(--accent)' :
                            t.status === 'blocked' ? '#4682b4' :
                            t.status === 'review' ? '#9370db' :
                            t.status === 'cancelled' ? 'var(--red)' : 'var(--dim)';
        statusEl.innerHTML = `<span style="color:${statusColor};border-color:${statusColor};" class="tdp-status-pill">${ESC(statusLabel)}</span>`;

        // Workflow badge
        const wfEl = document.getElementById('tdp-workflow');
        if (t.workflow_state) {
            wfEl.style.display = '';
            wfEl.innerHTML = _wfBadgeHTML(t.workflow_state);
        } else {
            wfEl.style.display = 'none';
        }

        // Title
        document.getElementById('tdp-title').textContent = t.title || '';

        // Meta row
        const assigneeEl = document.getElementById('tdp-assignee');
        assigneeEl.innerHTML = t.assignee ? (_projAgentInitial(t.assignee) + ' ' + ESC(lpAgentName(t.assignee))) : '<span class="dim">Unassigned</span>';

        const priEl = document.getElementById('tdp-priority');
        const priColor = _projPriColors[t.priority] || _projPriColors.normal;
        priEl.innerHTML = `<span class="task-pri-dot" style="background:${priColor};"></span> ${ESC((t.priority || 'normal'))}`;

        const dueEl = document.getElementById('tdp-due');
        dueEl.innerHTML = t.due_date ? _projDueDateHTML(t.due_date) : '';

        // Description
        const descRow = document.getElementById('tdp-desc-row');
        if (t.description) {
            descRow.style.display = '';
            document.getElementById('tdp-desc').textContent = t.description;
        } else {
            descRow.style.display = 'none';
        }

        // Notes
        const notesRow = document.getElementById('tdp-notes-row');
        if (t.notes) {
            notesRow.style.display = '';
            document.getElementById('tdp-notes').textContent = t.notes;
        } else {
            notesRow.style.display = 'none';
        }

        // Hide edit form
        document.getElementById('tdp-edit-form').style.display = 'none';

        // Populate edit form values
        document.getElementById('tdp-e-title').value = t.title || '';
        document.getElementById('tdp-e-status').value = t.status || 'pending';
        document.getElementById('tdp-e-priority').value = t.priority || 'normal';
        document.getElementById('tdp-e-wfpattern').value = t.workflow_pattern || '';
        document.getElementById('tdp-e-wfstate').value = t.workflow_state || '';
        document.getElementById('tdp-e-due').value = t.due_date || '';
        document.getElementById('tdp-e-desc').value = t.description || '';
        document.getElementById('tdp-e-notes').value = t.notes || '';

        // Assignee dropdown
        const assignWrap = document.getElementById('tdp-e-assignee-wrap');
        if (_projIsAdmin()) {
            assignWrap.innerHTML = '<label>Assignee</label>' + _projAssigneeDropdown('tdp-e-assignee', t.assignee || '');
        }

        // Sub-tasks
        _renderTaskDetailSubtasks(data.subtasks || [], t);

        // Activity feed (merge history + comments, sorted by time)
        _renderTaskDetailActivity(data.history || [], data.comments || [], t);

    } catch (e) {
        document.getElementById('tdp-title').textContent = 'Error loading task';
        console.error('showTaskDetail error:', e);
    }
}

function _renderTaskDetailSubtasks(subtasks, parentTask) {
    const el = document.getElementById('tdp-subtasks');
    const countEl = document.getElementById('tdp-subtask-count');
    const done = subtasks.filter(s => s.status === 'done').length;
    countEl.textContent = subtasks.length ? `${done}/${subtasks.length}` : '';

    if (!subtasks.length) {
        el.innerHTML = '<span class="empty">No sub-tasks yet</span>';
        return;
    }

    el.innerHTML = subtasks.map(s => {
        const pri = s.priority || 'normal';
        const dotColor = _projPriColors[pri] || _projPriColors.normal;
        const doneStyle = s.status === 'done' ? ' style="text-decoration:line-through;color:var(--dim);"' : '';
        const statusIcon = s.status === 'done' ? '✓' : s.status === 'in_progress' ? '◉' : s.status === 'blocked' ? '✕' : '○';
        const statusColor = s.status === 'done' ? 'var(--green)' :
                            s.status === 'in_progress' ? 'var(--accent)' :
                            s.status === 'blocked' ? '#4682b4' : 'var(--dim)';
        const wfBadge = s.workflow_state ? _wfBadgeHTML(s.workflow_state) : '';
        const agentBadge = _projIsAdmin() ? _projAgentInitial(s.assignee) : '';

        return `<div class="tdp-subtask-row" onclick="showTaskDetail(${s.id})">
            <span style="color:${statusColor};font-size:0.85rem;">${statusIcon}</span>
            <span class="task-pri-dot" style="background:${dotColor};"></span>
            <span class="tdp-subtask-title"${doneStyle}>${ESC(s.title || '')}</span>
            ${wfBadge}${agentBadge}${_projDueDateHTML(s.due_date)}
        </div>`;
    }).join('');
}

function _renderTaskDetailActivity(history, comments, task) {
    const el = document.getElementById('tdp-activity');
    const items = [];

    // History entries
    history.forEach(h => {
        items.push({
            type: 'history',
            time: h.changed_at,
            field: h.field_changed,
            old_val: h.old_value,
            new_val: h.new_value,
            by: h.changed_by,
        });
    });

    // Comments
    comments.forEach(c => {
        items.push({
            type: 'comment',
            time: c.created_at,
            author: c.author,
            content: c.content,
        });
    });

    // Audit verdict as special entry
    if (task.audit_verdict) {
        items.push({
            type: 'audit',
            time: task.updated_at,
            verdict: task.audit_verdict,
            count: task.audit_count || 0,
        });
    }

    // Sort newest first
    items.sort((a, b) => new Date(b.time) - new Date(a.time));

    if (!items.length) {
        el.innerHTML = '<span class="empty">No activity yet</span>';
        return;
    }

    el.innerHTML = items.map(item => {
        const when = item.time ? formatDate(item.time) : '';
        if (item.type === 'comment') {
            const badge = _projAgentInitial(item.author || 'system');
            return `<div class="tdp-activity-item tdp-act-comment">
                ${badge}<div class="tdp-act-body">
                    <div class="tdp-act-content">${ESC(item.content)}</div>
                    <div class="tdp-act-time">${when}</div>
                </div>
            </div>`;
        } else if (item.type === 'audit') {
            const verdictColor = item.verdict === 'approved' ? 'var(--green)' : 'var(--red)';
            return `<div class="tdp-activity-item tdp-act-audit">
                <span class="tdp-act-audit-icon" style="color:${verdictColor};">⬥</span>
                <div class="tdp-act-body">
                    <div class="tdp-act-content">Audit: <strong style="color:${verdictColor};">${ESC(item.verdict)}</strong> (round ${item.count})</div>
                    <div class="tdp-act-time">${when}</div>
                </div>
            </div>`;
        } else {
            // history
            return `<div class="tdp-activity-item tdp-act-history">
                <span class="tdp-act-history-icon">↻</span>
                <div class="tdp-act-body">
                    <div class="tdp-act-content"><span class="dim">${ESC(item.field)}</span> ${ESC(item.old_val || '(empty)')} → ${ESC(item.new_val || '(empty)')}</div>
                    <div class="tdp-act-time">${ESC(item.by || 'system')} · ${when}</div>
                </div>
            </div>`;
        }
    }).join('');
}

function toggleTaskDetailEdit() {
    const form = document.getElementById('tdp-edit-form');
    form.style.display = form.style.display === 'none' ? 'block' : 'none';
}

async function saveTaskDetail() {
    if (!currentTaskId) return;
    const body = {
        title: document.getElementById('tdp-e-title')?.value || '',
        status: document.getElementById('tdp-e-status')?.value || 'pending',
        priority: document.getElementById('tdp-e-priority')?.value || 'normal',
        workflow_pattern: document.getElementById('tdp-e-wfpattern')?.value || null,
        workflow_state: document.getElementById('tdp-e-wfstate')?.value || null,
        assignee: document.getElementById('tdp-e-assignee')?.value || '',
        due_date: document.getElementById('tdp-e-due')?.value || null,
        description: document.getElementById('tdp-e-desc')?.value || '',
        notes: document.getElementById('tdp-e-notes')?.value || '',
        changed_by: MC.instance?.operator?.toLowerCase() || 'operator',
    };
    try {
        const res = await fetch(`/api/tasks/${currentTaskId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        showTaskDetail(currentTaskId);
    } catch (e) {
        alert('Failed to save: ' + e.message);
    }
}

async function addSubtaskFromDetail() {
    if (!currentTaskId) return;
    const titleEl = document.getElementById('tdp-new-subtask');
    const priEl = document.getElementById('tdp-new-sub-priority');
    const title = titleEl.value.trim();
    if (!title) return;

    // Get the project_id from the parent task's project
    const projectId = _taskDetailProject?.id;

    try {
        await fetch('/api/tasks', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                project_id: projectId,
                parent_task_id: currentTaskId,
                title: title,
                priority: priEl?.value || 'normal',
            }),
        });
        titleEl.value = '';
        showTaskDetail(currentTaskId);
    } catch (e) {
        console.error('Failed to add sub-task:', e);
    }
}

async function addTaskCommentFromDetail() {
    if (!currentTaskId) return;
    const inputEl = document.getElementById('tdp-new-comment');
    const content = inputEl.value.trim();
    if (!content) return;

    try {
        await fetch(`/api/tasks/${currentTaskId}/comments`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                content: content,
                author: MC.instance?.operator?.toLowerCase() || 'operator',
            }),
        });
        inputEl.value = '';
        showTaskDetail(currentTaskId);
    } catch (e) {
        console.error('Failed to add comment:', e);
    }
}

function backToProjectFromTask() {
    currentTaskId = null;
    if (_taskDetailProject?.id) {
        showProjectDetail(_taskDetailProject.id);
    } else {
        backToProjects();
    }
}
