// =============================================================================
// calendar.js — Calendar tab with full Nextcloud CalDAV integration
// =============================================================================
// Read, create, edit, delete events via CalDAV. Links events to tasks/projects
// via local DB (event_links table). Nextcloud native app still manages the
// same calendar data — MC just taps into it from the backend.
// =============================================================================

let _calendarEvents = [];
let _calendarTasks  = [];    // tasks with due_date in the calendar window
let _calendarLinks  = {};   // { uid: { task_id, project_id } }
let _editingEventUid = null;
let _editingEventHref = null;

async function loadCalendar() {
    const days = document.getElementById('cal-days')?.value || 14;
    const container = document.getElementById('calendar-events');
    container.innerHTML = '<div class="loading">Loading calendar...</div>';

    try {
        // Fetch events and tasks with due dates in parallel
        const [evtData, taskData] = await Promise.all([
            fetch(`/api/calendar/events?days=${days}`).then(r => r.json()),
            fetch('/api/tasks?limit=200').then(r => r.json()).catch(() => ({ tasks: [] })),
        ]);

        if (evtData.error) {
            container.innerHTML = `<div class="error-msg">Calendar error: ${ESC(evtData.error)}</div>`;
            return;
        }

        _calendarEvents = evtData.events || [];
        // Filter tasks to only those with due_date, and within the calendar window
        const today = new Date();
        today.setHours(0, 0, 0, 0);
        const endDate = new Date(today);
        endDate.setDate(endDate.getDate() + parseInt(days));
        _calendarTasks = (taskData.tasks || []).filter(t => {
            if (!t.due_date) return false;
            const d = new Date(t.due_date + 'T12:00:00');
            return d >= today && d <= endDate;
        });

        if (!_calendarEvents.length && !_calendarTasks.length) {
            container.innerHTML = `<div class="empty-msg">No events or deadlines in the next ${days} days.</div>`;
            return;
        }

        // Batch-load event links for all UIDs
        await _loadEventLinks();

        // Filter out tasks that already have a linked CalDAV event (avoid duplicates)
        const linkedTaskIds = new Set(
            Object.values(_calendarLinks)
                .map(l => l.task_id)
                .filter(Boolean)
        );
        _calendarTasks = _calendarTasks.filter(t => !linkedTaskIds.has(t.id));

        _renderCalendarEvents(container);
    } catch (err) {
        container.innerHTML = `<div class="error-msg">Could not load calendar: ${ESC(err.message)}</div>`;
    }
}

async function _loadEventLinks() {
    const uids = _calendarEvents.map(e => e.uid).filter(Boolean).join(',');
    if (!uids) { _calendarLinks = {}; return; }
    try {
        const data = await fetch(`/api/calendar/links?uids=${encodeURIComponent(uids)}`).then(r => r.json());
        _calendarLinks = data.links || {};
    } catch { _calendarLinks = {}; }
}

function _renderCalendarEvents(container) {
    // Build unified date groups from both events and tasks
    const groups = {};

    // Add CalDAV events
    _calendarEvents.forEach(e => {
        const dateKey = e.start ? e.start.substring(0, 10) : 'Unknown';
        if (!groups[dateKey]) groups[dateKey] = { events: [], tasks: [] };
        groups[dateKey].events.push(e);
    });

    // Add tasks with due dates
    _calendarTasks.forEach(t => {
        const dateKey = t.due_date;  // already YYYY-MM-DD
        if (!groups[dateKey]) groups[dateKey] = { events: [], tasks: [] };
        groups[dateKey].tasks.push(t);
    });

    let html = '';
    Object.entries(groups).sort(([a], [b]) => a.localeCompare(b)).forEach(([date, group]) => {
        const d = new Date(date + 'T12:00:00');
        const today = new Date();
        const isToday = d.toDateString() === today.toDateString();
        const label = isToday
            ? 'Today'
            : formatDate(date, { weekday: 'short', month: 'short', day: 'numeric' });

        html += `<div class="event-date-group">
            <div class="event-date-label${isToday ? ' event-today' : ''}">${ESC(label)}</div>`;

        // Render CalDAV events
        group.events.forEach(e => {
            let time = '';
            if (!e.all_day && e.start && e.start.length > 10) {
                const t = new Date(e.start);
                time = formatTime(e.start);
            }
            const uid = e.uid || '';
            const link = _calendarLinks[uid] || {};
            const badges = [];
            if (link.project_name) badges.push(`<span class="event-link-badge" title="Project">${ESC(link.project_name)}</span>`);
            if (link.task_title) badges.push(`<span class="event-link-badge" title="Task">${ESC(link.task_title)}</span>`);
            const badgeHtml = badges.length ? `<div class="event-link-badges">${badges.join('')}</div>` : '';

            html += `<div class="event-card" ${uid ? `onclick="editCalEvent('${ESC(uid)}')"` : ''}>
                <div class="event-time">${time || 'All day'}</div>
                <div class="event-body">
                    <div class="event-title">${ESC(e.summary || 'Untitled')}</div>
                    ${e.location ? `<div class="event-location">${ESC(e.location)}</div>` : ''}
                    ${e.description ? `<div class="event-desc">${ESC(e.description)}</div>` : ''}
                    ${badgeHtml}
                </div>
            </div>`;
        });

        // Render task deadlines
        group.tasks.forEach(t => {
            const prioClass = t.priority === 'critical' ? 'task-due-critical'
                            : t.priority === 'high' ? 'task-due-high' : '';
            const statusLabel = t.status === 'done' ? '✓' : t.status === 'in_progress' ? '▶' : '○';
            const projectBadge = t.project_name
                ? `<span class="event-link-badge" title="Project">${ESC(t.project_name)}</span>` : '';
            const assigneeBadge = t.assigned_to
                ? lpAgentBadgeHTML(t.assigned_to) : '';

            const taskClick = t.project_id
                ? `onclick="switchTab('projects');setTimeout(()=>showProjectDetail(${t.project_id}),300);" style="cursor:pointer;"`
                : '';

            html += `<div class="task-due-card ${prioClass}" ${taskClick}>
                <div class="task-due-marker">${statusLabel}</div>
                <div class="event-body">
                    <div class="event-title">${ESC(t.title)}</div>
                    <div class="task-due-meta">
                        ${assigneeBadge}
                        ${projectBadge}
                        <span class="task-due-status">${ESC(t.status || 'pending')}</span>
                    </div>
                </div>
            </div>`;
        });

        html += '</div>';
    });

    container.innerHTML = html;
}

// =============================================================================
// New Event Form
// =============================================================================

async function showNewEventForm() {
    _editingEventUid = null;
    _editingEventHref = null;
    const form = document.getElementById('cal-event-form');
    if (!form) return;
    // Reset form fields
    document.getElementById('cef-title').value = '';
    document.getElementById('cef-date').value = new Date().toISOString().split('T')[0];
    document.getElementById('cef-time').value = '';
    document.getElementById('cef-end-time').value = '';
    document.getElementById('cef-allday').checked = true;
    document.getElementById('cef-location').value = '';
    document.getElementById('cef-description').value = '';
    document.getElementById('cef-delete-btn').style.display = 'none';
    document.getElementById('cef-form-title').textContent = 'New Event';
    _toggleTimeFields(true);

    // Load and populate dropdowns
    await _populateLinkDropdowns();
    document.getElementById('cef-project').value = '';
    document.getElementById('cef-task').value = '';

    form.style.display = 'block';
    form.scrollIntoView({ behavior: 'smooth', block: 'start' });
    document.getElementById('cef-title').focus();
}

async function editCalEvent(uid) {
    const ev = _calendarEvents.find(e => e.uid === uid);
    if (!ev) return;
    _editingEventUid = uid;
    _editingEventHref = ev.href || '';
    const form = document.getElementById('cal-event-form');
    if (!form) return;

    document.getElementById('cef-title').value = ev.summary || '';
    document.getElementById('cef-date').value = ev.start ? ev.start.substring(0, 10) : '';
    document.getElementById('cef-allday').checked = ev.all_day !== false;
    document.getElementById('cef-location').value = ev.location || '';
    document.getElementById('cef-description').value = ev.description || '';
    document.getElementById('cef-delete-btn').style.display = 'inline-block';
    document.getElementById('cef-form-title').textContent = 'Edit Event';

    // Time fields
    if (!ev.all_day && ev.start && ev.start.length > 10) {
        const t = new Date(ev.start);
        document.getElementById('cef-time').value = `${String(t.getHours()).padStart(2,'0')}:${String(t.getMinutes()).padStart(2,'0')}`;
    } else {
        document.getElementById('cef-time').value = '';
    }
    if (!ev.all_day && ev.end && ev.end.length > 10) {
        const t = new Date(ev.end);
        document.getElementById('cef-end-time').value = `${String(t.getHours()).padStart(2,'0')}:${String(t.getMinutes()).padStart(2,'0')}`;
    } else {
        document.getElementById('cef-end-time').value = '';
    }

    _toggleTimeFields(ev.all_day !== false);

    // Load dropdowns and set saved link values
    await _populateLinkDropdowns();
    const link = _calendarLinks[uid] || {};
    document.getElementById('cef-project').value = link.project_id || '';
    document.getElementById('cef-task').value = link.task_id || '';

    form.style.display = 'block';
    form.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function _toggleTimeFields(allDay) {
    const timeRow = document.getElementById('cef-time-row');
    if (timeRow) timeRow.style.display = allDay ? 'none' : 'grid';
}

function hideEventForm() {
    const form = document.getElementById('cal-event-form');
    if (form) form.style.display = 'none';
    _editingEventUid = null;
}

// =============================================================================
// Link Dropdowns — populate project and task selects
// =============================================================================

async function _populateLinkDropdowns() {
    const projSel = document.getElementById('cef-project');
    const taskSel = document.getElementById('cef-task');
    if (!projSel || !taskSel) return;

    // Fetch projects and tasks in parallel
    try {
        const [projData, taskData] = await Promise.all([
            fetch('/api/projects').then(r => r.json()),
            fetch('/api/tasks?limit=100').then(r => r.json()),
        ]);

        // Populate projects
        const projects = projData.projects || [];
        projSel.innerHTML = '<option value="">None</option>' +
            projects.map(p => `<option value="${p.id}">${ESC(p.name)}</option>`).join('');

        // Populate tasks (active ones)
        const tasks = taskData.tasks || [];
        taskSel.innerHTML = '<option value="">None</option>' +
            tasks.map(t => `<option value="${t.id}">${ESC(t.title)}</option>`).join('');
    } catch {
        // Dropdowns stay at "None" — non-fatal
    }
}

// =============================================================================
// Save / Delete
// =============================================================================

async function saveCalEvent() {
    const summary = document.getElementById('cef-title').value.trim();
    if (!summary) { document.getElementById('cef-title').focus(); return; }

    const date = document.getElementById('cef-date').value;
    const allDay = document.getElementById('cef-allday').checked;
    const time = document.getElementById('cef-time').value;
    const endTime = document.getElementById('cef-end-time').value;
    const location = document.getElementById('cef-location').value.trim();
    const description = document.getElementById('cef-description').value.trim();

    let start, end;
    if (allDay) {
        start = date;
        end = '';
    } else {
        start = time ? `${date}T${time}:00` : date;
        end = endTime ? `${date}T${endTime}:00` : '';
    }

    const body = { summary, start, end, all_day: allDay, location, description };

    try {
        let res;
        if (_editingEventUid) {
            const editBody = { ...body, href: _editingEventHref };
            res = await fetch(`/api/calendar/events/${_editingEventUid}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(editBody),
            });
        } else {
            res = await fetch('/api/calendar/events', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
        }

        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.error || `HTTP ${res.status}`);
        }

        // Get the UID (either existing or from create response)
        const result = await res.json();
        const uid = _editingEventUid || result.uid;

        // Save event links (project/task associations)
        if (uid) {
            const projId = document.getElementById('cef-project')?.value || '';
            const taskId = document.getElementById('cef-task')?.value || '';
            await _saveEventLink(uid, projId, taskId);
        }

        hideEventForm();
        loadCalendar();
    } catch (e) {
        alert('Failed to save event: ' + e.message);
    }
}

async function _saveEventLink(uid, projectId, taskId) {
    try {
        await fetch(`/api/calendar/links/${encodeURIComponent(uid)}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                project_id: projectId ? parseInt(projectId) : null,
                task_id: taskId ? parseInt(taskId) : null,
            }),
        });
    } catch { /* non-fatal */ }
}

async function deleteCalEvent() {
    if (!_editingEventUid) return;
    if (!confirm('Delete this event?')) return;

    try {
        const hrefParam = _editingEventHref ? `?href=${encodeURIComponent(_editingEventHref)}` : '';
        const res = await fetch(`/api/calendar/events/${_editingEventUid}${hrefParam}`, { method: 'DELETE' });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.error || `HTTP ${res.status}`);
        }
        // Also clean up the link
        try {
            await fetch(`/api/calendar/links/${encodeURIComponent(_editingEventUid)}`, { method: 'DELETE' });
        } catch { /* non-fatal */ }
        hideEventForm();
        loadCalendar();
    } catch (e) {
        alert('Failed to delete: ' + e.message);
    }
}
