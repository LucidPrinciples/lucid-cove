// tasks.js — Task management (local + Nextcloud VTODO)

function showAddTask() {
  const form = document.getElementById('add-task-form');
  form.classList.toggle('hidden');
  if (!form.classList.contains('hidden')) {
    document.getElementById('new-task-title').focus();
  }
}

function hideAddTask() {
  document.getElementById('add-task-form').classList.add('hidden');
  document.getElementById('new-task-title').value = '';
  document.getElementById('new-task-priority').value = 'normal';
  document.getElementById('new-task-due').value = '';
}

async function loadTasks() {
  const filter = document.getElementById('task-filter').value;
  const container = document.getElementById('task-list');
  container.innerHTML = '<div class="loading">Loading tasks...</div>';

  try {
    const data = await fetch(`/api/tasks?status=${filter}`).then(r => r.json());

    if (data.error) {
      container.innerHTML = `<div class="error-msg">Tasks error: ${ESC(data.error)}</div>`;
      return;
    }

    const tasks = data.tasks || [];
    if (tasks.length === 0) {
      container.innerHTML = `<div class="empty-msg">No ${filter === 'all' ? '' : filter + ' '}tasks.</div>`;
      return;
    }

    container.innerHTML = tasks.map(t => renderTask(t)).join('');
  } catch (err) {
    container.innerHTML = `<div class="error-msg">Could not load tasks: ${ESC(err.message)}</div>`;
  }
}

function renderTask(t) {
  const done = t.status === 'done';
  const priorityClass = `priority-${t.priority || 'normal'}`;
  const sourceIcon = t.source === 'nextcloud' ? '☁' : '◉';
  const sourceTitle = t.source === 'nextcloud' ? 'Nextcloud' : 'Local';

  let dueStr = '';
  if (t.due_date) {
    const d = new Date(t.due_date + 'T12:00:00');
    dueStr = d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    if (!done && d < today) dueStr = `<span class="overdue">${dueStr}</span>`;
  }

  return `<div class="task-row ${done ? 'task-done' : ''}" id="task-${t.id}">
    <button class="task-check ${done ? 'checked' : ''}"
      onclick="toggleTask(${t.id}, ${done})"
      title="${done ? 'Mark pending' : 'Mark done'}">
      ${done ? '✓' : ''}
    </button>
    <div class="task-body">
      <div class="task-title ${done ? 'struck' : ''}">${ESC(t.title)}</div>
      <div class="task-meta">
        <span class="task-priority ${priorityClass}">${t.priority || 'normal'}</span>
        ${dueStr ? `<span class="task-due">${dueStr}</span>` : ''}
        <span class="task-source" title="${sourceTitle}">${sourceIcon}</span>
      </div>
    </div>
  </div>`;
}

async function toggleTask(id, isDone) {
  const newStatus = isDone ? 'pending' : 'done';
  try {
    const res = await fetch(`/api/tasks/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: newStatus }),
    });
    const data = await res.json();
    if (data.error) { console.error(data.error); return; }
    loadTasks();
  } catch (err) {
    console.error('Toggle failed:', err);
  }
}

async function createTask() {
  const title = document.getElementById('new-task-title').value.trim();
  if (!title) return;

  const priority = document.getElementById('new-task-priority').value;
  const due_date = document.getElementById('new-task-due').value || null;

  try {
    const res = await fetch('/api/tasks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title, priority, due_date }),
    });
    const data = await res.json();
    if (data.error) { alert(data.error); return; }
    hideAddTask();
    loadTasks();
  } catch (err) {
    alert('Could not create task: ' + err.message);
  }
}

// Enter in title field submits
document.getElementById('new-task-title').addEventListener('keydown', e => {
  if (e.key === 'Enter') { e.preventDefault(); createTask(); }
  if (e.key === 'Escape') hideAddTask();
});
