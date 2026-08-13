const $ = (id) => document.getElementById(id);
let currentSessionId = null;
let currentThreadKind = null;
let currentTab = 'threads';
let sessionFilter = 'all';
let pollTimer = null;

// Show close control when opened inside MC tool overlay (same pattern as Jules/Model Lab).
(function () {
  try {
    if (window.parent && window.parent !== window) {
      document.body.classList.add('kb-show-close');
    }
  } catch (e) {}
  const btn = $('kb-close');
  if (btn) {
    btn.onclick = () => {
      try {
        if (window.parent && typeof window.parent.closeFlowOverlay === 'function') {
          window.parent.closeFlowOverlay();
          return;
        }
      } catch (e) {}
      history.length > 1 ? history.back() : (location.href = '/');
    };
  }
})();

function setStatus(elId, msg, isErr) {
  const el = $(elId);
  if (el) {
    el.textContent = msg || '';
    el.style.color = isErr ? '#e74c3c' : '#888';
  }
}

function esc(s) {
  return String(s || '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

function formatDateTime(isoString) {
  if (!isoString) return '';
  try {
    const d = new Date(isoString);
    return d.toLocaleString(undefined, {
      year: 'numeric', month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit', hour12: false
    });
  } catch {
    return isoString;
  }
}

async function api(path, opts) {
  const res = await fetch(path, {
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', ...(opts && opts.headers || {}) },
    ...opts,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText || 'request failed');
  return data;
}

function showView(viewId) {
  const views = ['threadKindsView', 'sessionsListView', 'sessionDetailView'];
  views.forEach(id => {
    $(id)?.classList.toggle('show', id === viewId);
  });
  document.body.classList.toggle('kb-detail', viewId === 'sessionDetailView');
  updateCloseVisibility();
}

function updateCloseVisibility() {
  const isDetailView = $('sessionDetailView').classList.contains('show');
  document.body.classList.toggle('kb-show-close', isDetailView);
}

function goBackFromDetail() {
  currentSessionId = null;
  showView('sessionsListView');
  clearInterval(pollTimer);
  pollTimer = null;
  loadSessions(currentThreadKind); // Refresh list on back
}

// ── Thread Kinds ─────────────────────────────────────────────────────────────

async function loadThreadKinds() {
  try {
    const data = await api('/api/knowledge/threads');
    if (!data.ok) throw new Error(data.error);

    const listEl = $('threadKindsList');
    if (!data.items.length) {
      listEl.innerHTML = '<div class="empty">No knowledge thread kinds available.</div>';
      return;
    }
    // Store thread kinds globally for easy access
    window._threadKinds = data.items;

    listEl.innerHTML = data.items.map(t => `
      <div class="thread-kind-item" data-kind="${t.id}" onclick="selectThreadKind('${t.id}')">
        <div class="icon">${esc(t.icon)}</div>
        <div class="details">
          <h3>${esc(t.title)}</h3>
          <p>${esc(t.system_prompt)}</p>
        </div>
      </div>
    `).join('');
  } catch (e) {
    console.error('Error loading thread kinds:', e);
    setStatus('createSessionStatus', e.message, true);
  }
}

function selectThreadKind(kind) {
  currentThreadKind = kind;
  $('tabThreads').classList.remove('on');
  $('tabSessions').classList.add('on');
  showView('sessionsListView');
  loadSessions(kind);
  // Pre-fill session creation form with thread kind defaults
  const threadConfig = window._threadKinds.find(t => t.id === kind);
  if (threadConfig) {
    $('newSessionTitle').value = threadConfig.title;
    $('newSessionSystemPrompt').value = threadConfig.system_prompt;
    $('sessionChatInput').placeholder = `Ask Ezra about ${threadConfig.title}...`;
  } else {
    $('newSessionTitle').value = '';
    $('newSessionSystemPrompt').value = '';
    $('sessionChatInput').placeholder = 'Ask Ezra...';
  }
}

// ── Sessions ─────────────────────────────────────────────────────────────────

async function loadSessions(kind) {
  try {
    const data = await api(`/api/knowledge/sessions?status=${sessionFilter}&kind=${kind || ''}`);
    if (!data.ok) throw new Error(data.error);

    const listEl = $('sessionsList');
    if (!data.items.length) {
      listEl.innerHTML = '<div class="empty">No knowledge sessions yet. Create one above.</div>';
      return;
    }

    listEl.innerHTML = data.items.map(s => `
      <div class="item" data-id="${s.id}" onclick="openSession(${s.id})">
        <div class="t">${esc(s.title || `Session ${s.id}`)}</div>
        <div class="sub-t">Model: ${esc(s.model_tag)}</div>
        <div class="meta">
          <span class="chip ${s.status}">${esc(s.status)}</span>
          <span>Updated: ${formatDateTime(s.updated_at)}</span>
        </div>
        ${s.notes ? `<div class="preview">${esc(s.notes)}</div>` : ''}
      </div>
    `).join('');

  } catch (e) {
    console.error('Error loading sessions:', e);
    setStatus('createSessionStatus', e.message, true);
  }
}

async function createSession() {
  setStatus('createSessionStatus', 'Creating session…');
  try {
    const title = $('newSessionTitle').value.trim();
    const system_prompt = $('newSessionSystemPrompt').value.trim();
    const temperature = parseFloat($('newSessionTemperature').value);

    if (!currentThreadKind) {
      setStatus('createSessionStatus', 'Please select a thread kind first.', true);
      return;
    }

    const body = {
      title,
      thread_kind: currentThreadKind,
      system_prompt,
      temperature
    };

    const data = await api('/api/knowledge/sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!data.ok) throw new Error(data.error);

    $('newSessionTitle').value = '';
    $('newSessionSystemPrompt').value = '';
    $('newSessionTemperature').value = '0.7';

    setStatus('createSessionStatus', 'Session created.');
    await loadSessions(currentThreadKind);
    openSession(data.item.id); // Open the new session immediately

  } catch (e) {
    console.error('Error creating session:', e);
    setStatus('createSessionStatus', e.message, true);
  }
}

async function openSession(id) {
  currentSessionId = id;
  showView('sessionDetailView');
  await loadSessionDetail(id);
}

async function loadSessionDetail(id) {
  try {
    const data = await api(`/api/knowledge/sessions/${id}`);
    if (!data.ok) throw new Error(data.error);

    const s = data.item;
    const chatLogEl = $('chatLog');
    chatLogEl.innerHTML = ''; // Clear existing messages

    data.messages.forEach(m => {
      const msgEl = document.createElement('div');
      msgEl.className = `chat-message ${m.role}`;
      const bubbleEl = document.createElement('div');
      bubbleEl.className = 'chat-bubble';
      bubbleEl.textContent = m.content || '';

      if (m.thinking) {
        const detailsEl = document.createElement('details');
        detailsEl.className = 'think-block';
        detailsEl.innerHTML = '<summary>Ezra thinking (for context)</summary><pre></pre>';
        detailsEl.querySelector('pre').textContent = m.thinking;
        bubbleEl.appendChild(detailsEl);
      }
      msgEl.appendChild(bubbleEl);
      chatLogEl.appendChild(msgEl);
    });

    chatLogEl.scrollTop = chatLogEl.scrollHeight; // Scroll to bottom

    $('btnReopenSession').style.display = s.status === 'closed' ? 'block' : 'none';
    $('btnCloseSession').style.display = s.status === 'open' ? 'block' : 'none';
    $('sessionChatInput').disabled = s.status === 'closed';
    $('btnSessionChatSend').disabled = s.status === 'closed';

    // Update placeholder based on thread kind
    const threadConfig = window._threadKinds.find(t => t.id === s.thread_kind);
    if (threadConfig) {
      $('sessionChatInput').placeholder = `Ask Ezra about ${threadConfig.title}...`;
    } else {
      $('sessionChatInput').placeholder = 'Ask Ezra...';
    }

    if (s.status === 'open' && !pollTimer) {
      pollTimer = setInterval(() => loadSessionDetail(id), 3000);
    } else if (s.status !== 'open' && pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }

  } catch (e) {
    console.error('Error loading session detail:', e);
    setStatus('chatStatus', e.message, true);
    goBackFromDetail(); // Go back to list if error
  }
}

async function sendSessionMessage() {
  if (!currentSessionId || window._kbChatBusy) return;
  window._kbChatBusy = true;
  const inputEl = $('sessionChatInput');
  const message = inputEl.value.trim();
  if (!message) {
    window._kbChatBusy = false;
    return;
  }
  inputEl.value = '';
  setStatus('chatStatus', 'Ezra is thinking…', false);
  $('btnSessionChatSend').disabled = true;

  try {
    // Add user message to UI immediately
    const chatLogEl = $('chatLog');
    const userMsgEl = document.createElement('div');
    userMsgEl.className = 'chat-message user';
    const userBubbleEl = document.createElement('div');
    userBubbleEl.className = 'chat-bubble';
    userBubbleEl.textContent = message;
    userMsgEl.appendChild(userBubbleEl);
    chatLogEl.appendChild(userMsgEl);
    chatLogEl.scrollTop = chatLogEl.scrollHeight;

    const data = await api(`/api/knowledge/sessions/${currentSessionId}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: message }),
    });
    if (!data.ok) throw new Error(data.error || 'chat failed');
    if (data.rotation && data.rotation.rotated && data.session_id) {
      currentSessionId = data.session_id;
      setStatus('chatStatus', 'Rotated with a summary — new thread, same topic.', false);
    } else {
      setStatus('chatStatus', data.latency_ms ? `Reply in ${data.latency_ms} ms` : 'Done', false);
    }
    await loadSessionDetail(currentSessionId);
  } catch (e) {
    console.error('Error sending message:', e);
    setStatus('chatStatus', e.message || String(e), true);
  } finally {
    window._kbChatBusy = false;
    inputEl.disabled = false;
    $('btnSessionChatSend').disabled = false;
    inputEl.focus();
  }
}

async function setSessionStatus(status) {
  if (!currentSessionId) return;
  try {
    const data = await api(`/api/knowledge/sessions/${currentSessionId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status }),
    });
    if (!data.ok) throw new Error(data.error || 'update failed');
    await loadSessionDetail(currentSessionId);
    loadSessions(currentThreadKind);
  } catch (e) {
    setStatus('chatStatus', e.message || String(e), true);
  }
}

// ── Event Handlers ───────────────────────────────────────────────────────────

$('btnCreateSession').onclick = createSession;
if ($('btnCloseSession')) $('btnCloseSession').onclick = () => setSessionStatus('closed');
if ($('btnReopenSession')) $('btnReopenSession').onclick = () => setSessionStatus('open');
$('btnSessionChatSend').onclick = sendSessionMessage;
$('sessionChatInput').onkeydown = (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendSessionMessage();
  }
};
if ($('btnBackSession')) $('btnBackSession').onclick = goBackFromDetail;

const sf = $('sessionFilters');
if (sf) {
  sf.querySelectorAll('button').forEach(btn => {
    btn.onclick = () => {
      sessionFilter = btn.dataset.filter || 'all';
      sf.querySelectorAll('button').forEach(b => b.classList.toggle('on', b === btn));
      loadSessions(currentThreadKind);
    };
  });
}

document.querySelectorAll('.tabs .tab').forEach(tabEl => {
  tabEl.onclick = () => {
    currentTab = tabEl.dataset.tab;
    document.querySelectorAll('.tabs .tab').forEach(t => t.classList.remove('on'));
    tabEl.classList.add('on');

    if (currentTab === 'threads') {
      showView('threadKindsView');
      loadThreadKinds();
    } else if (currentTab === 'sessions') {
      showView('sessionsListView');
      loadSessions(currentThreadKind);
    }
  };
});

// Initial load
(async function() {
  // Pre-load thread kinds into a global variable for easy access
  const data = await api('/api/knowledge/threads');
  if (data.ok) {
    window._threadKinds = data.items;
  } else {
    window._threadKinds = [];
    console.error('Failed to load thread kinds', data.error);
  }

  // Deep link support
  const q = new URLSearchParams(location.search);
  const sessionId = q.get('session');
  const threadKind = q.get('kind');

  if (sessionId) {
    currentTab = 'sessions';
    $('tabThreads').classList.remove('on');
    $('tabSessions').classList.add('on');
    if (threadKind) currentThreadKind = threadKind;
    showView('sessionDetailView');
    openSession(parseInt(sessionId));
  } else {
    loadThreadKinds();
    showView('threadKindsView');
  }
})();