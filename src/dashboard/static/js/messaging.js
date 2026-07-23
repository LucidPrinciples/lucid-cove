// =============================================================================
// Chat — Core messaging, navigation, threads
// =============================================================================
// Core chat functions: send/receive messages, thread management, agent navigation.
// Voice/dictation in voice.js. Manager supervisory view in manager-chat.js.
// =============================================================================

let chatLoading = false;
let sending = false;
let activeChannel = '';  // actual channel name sent to backend (e.g. "day", "stuart-day")
let activeAgent = null;  // currently selected chat_agent object

// =============================================================================
// Two-level Chat Navigation — Agent selector → Day/Deep per agent
// =============================================================================

function _getChatAgents() {
    // Build agent groups from MC.channels (is_steward / is_merchant flags)
    // Supports host agent + steward (Stuart) + merchant (Mercer)
    const channels = MC.channels || {};
    const hostChannels = [];
    const stewardChannels = [];
    const merchantChannels = [];

    for (const [name, def] of Object.entries(channels)) {
        if (def.is_steward) {
            stewardChannels.push(name);
        } else if (def.is_merchant) {
            merchantChannels.push(name);
        } else {
            hostChannels.push(name);
        }
    }

    const agents = [{
        id: MC.agentId || 'agent',
        name: MC.agentName || 'Agent',
        channels: hostChannels,
        is_steward: false,
    }];

    if (stewardChannels.length > 0) {
        const sc = MC.config?.steward_channel || {};
        const meta = (MC.config?.chat_agents || []).find(a => a.is_steward) || {};
        const rawName = meta.name || sc.name || 'Stuart';

        agents.push({
            id: meta.id || sc.agent_id || 'stuart',
            name: rawName.charAt(0).toUpperCase() + rawName.slice(1),
            channels: stewardChannels,
            is_steward: true,
            admin_url: meta.admin_url || '',
            archetype: meta.archetype || '',
            emoji: meta.emoji || '\u{1F3E0}',
        });
    }

    if (merchantChannels.length > 0) {
        const mc = MC.config?.merchant_channel || {};
        const meta = (MC.config?.chat_agents || []).find(a => a.is_merchant) || {};
        const rawName = meta.name || mc.name || 'Mercer';

        agents.push({
            id: meta.id || mc.agent_id || 'mercer',
            name: rawName.charAt(0).toUpperCase() + rawName.slice(1),
            channels: merchantChannels,
            is_merchant: true,
            admin_url: meta.admin_url || '',
            archetype: meta.archetype || '',
            emoji: meta.emoji || '\u{1F4E6}',
        });
    }

    return agents;
}

function _getChannelLabel(channelName) {
    const parts = channelName.split('-');
    const last = parts[parts.length - 1];
    return last.charAt(0).toUpperCase() + last.slice(1);
}

function _getActiveAIName() {
    return activeAgent?.name || MC.agentName;
}

function buildChannelTabs() {
    const container = document.getElementById('channel-tabs');
    if (!container) return;

    const agents = _getChatAgents();
    // Agent chat is an agent feature. Tuner/Operator (public app, no agent) get
    // ONLY Connect — no agent tab, Connect opens by default. (agent separation)
    // But a Cove operator always gets agent chat. The public agentless app is the ONLY
    // place to hide it — so gate on "not the public app" (robust), plus the personal-agent
    // flag and tier as fallbacks. A stray tier can no longer hide the operator's own agent.
    const _isPublicApp = !!(MC.config && MC.config.is_public_app);
    const _hasAgentChat = !_isPublicApp
        || !!(MC.config && MC.config.has_personal_agent)
        || !!(MC.tier && (MC.tier.has_agent || MC.tier.level >= 20));

    console.log('[chat] gate hasAgentChat=' + _hasAgentChat
        + ' is_public_app=' + !!(MC.config && MC.config.is_public_app)
        + ' has_personal_agent=' + !!(MC.config && MC.config.has_personal_agent)
        + ' tierLevel=' + (MC.tier && MC.tier.level)
        + ' agents=' + agents.map(a => a.id).join(','));

    container.innerHTML = '';
    container.style.display = '';

    if (!_hasAgentChat) {
        if (window.addConnectTab) window.addConnectTab();
        if (window.openConnect) window.openConnect();
        return;
    }

    // ── Agent selector ──
    // The agent row ALWAYS exists so Connect (the Matrix layer / Market) can
    // attach, even for a single-agent Cove or a no-agent Operator (#137/#18).
    // Render a tab for EVERY agent, including a lone personal agent: a member
    // Presence has only its own agent (Stuart/Mercer are never injected for
    // members), and without that tab there's no way back to the agent chat once
    // Connect is open. The agent tab is the home button beside Connect.
    const agentBar = document.createElement('div');
    agentBar.className = 'agent-selector';
    agentBar.id = 'agent-selector';

    if (agents.length >= 1) {
        agents.forEach(agent => {
            const btn = document.createElement('button');
            btn.className = `agent-tab${agent.id === activeAgent?.id ? ' active' : ''}${agent.is_steward ? ' steward-agent' : ''}${agent.is_merchant ? ' merchant-agent' : ''}`;
            btn.dataset.agentId = agent.id;
            btn.textContent = agent.name;
            btn.addEventListener('click', () => switchAgent(agent));
            agentBar.appendChild(btn);
        });
    }

    container.appendChild(agentBar);
    // "Connect" — the Matrix layer + Market (#137), always available.
    if (window.addConnectTab) window.addConnectTab();

    // ── Day/Deep tabs for current agent ──
    _buildSubChannelTabs(container);
}

function _buildSubChannelTabs(container) {
    if (!activeAgent) return;

    // Remove old sub-channel bar if it exists
    const old = document.getElementById('subchannel-tabs');
    if (old) old.remove();

    const channels = activeAgent.channels || [];
    if (channels.length <= 1) return;

    const subBar = document.createElement('div');
    subBar.className = 'subchannel-tabs';
    subBar.id = 'subchannel-tabs';

    channels.forEach(ch => {
        const btn = document.createElement('button');
        btn.className = `channel-tab${ch === activeChannel ? ' active' : ''}`;
        btn.dataset.channel = ch;
        btn.textContent = _getChannelLabel(ch);
        btn.addEventListener('click', () => switchChannel(ch));
        subBar.appendChild(btn);
    });

    // Manager admin link — host-follows so it works either way. On the box
    // (localhost/http/IP) open the manager door locally via ?as= (subdomains don't
    // resolve there, and admin_url is blank with no domain); on the real https address
    // use the manager's own subdomain MC for remote/family access.
    // Only managers (Stuart/Mercer) get an admin door — never the personal agent.
    const _isManager = !!(activeAgent.is_steward || activeAgent.is_merchant);
    const _onBox = (location.protocol !== 'https:')
        || ['localhost', '127.0.0.1'].includes(location.hostname)
        || /^\d+\.\d+\.\d+\.\d+$/.test(location.hostname);
    const _mgrId = (activeAgent.id || '').toLowerCase();
    let _adminHref = '';
    if (_isManager && _onBox && _mgrId) {
        _adminHref = location.origin + '/?as=' + encodeURIComponent(_mgrId);
    } else if (_isManager && activeAgent.admin_url) {
        _adminHref = activeAgent.admin_url;
    }
    if (_adminHref) {
        const link = document.createElement('a');
        link.className = 'steward-admin-link';
        link.href = _adminHref;
        link.target = '_blank';
        link.title = `Open ${activeAgent.name} MC`;
        link.textContent = 'Admin';
        subBar.appendChild(link);
    }

    container.appendChild(subBar);
}

function switchAgent(agent) {
    if (window.closeConnect) window.closeConnect();  // leave the Matrix layer if open
    if (agent.id === activeAgent?.id) return;
    activeAgent = agent;

    // Update agent tab highlights
    document.querySelectorAll('.agent-tab').forEach(t => {
        t.classList.toggle('active', t.dataset.agentId === agent.id);
    });

    // Default to first channel of this agent (Day)
    const defaultCh = agent.channels[0] || 'day';
    activeChannel = defaultCh;
    _rememberChatTab();

    // Rebuild sub-channel tabs
    const container = document.getElementById('channel-tabs');
    if (container) _buildSubChannelTabs(container);

    // Clear and reload
    const box = document.getElementById('chat-messages');
    if (box) box.innerHTML = '';

    // Update welcome/placeholder for this agent
    _updateAgentUI();

    stopStatusPoller();
    loadChat();
    loadThreadInfo();
    loadContextUsage();
}

function _updateAgentUI() {
    const welcomeIcon = document.getElementById('welcome-icon');
    const welcomeText = document.getElementById('welcome-text');
    const input = document.getElementById('chat-input');

    const name = _getActiveAIName();
    const emoji = activeAgent?.emoji || MC.agentEmoji;

    if (welcomeIcon) welcomeIcon.textContent = emoji;
    if (welcomeText) welcomeText.textContent = `${name} is ready.`;
    if (input) input.placeholder = `Ask ${name} anything...`;
}

function switchChannel(name) {
    if (window.closeConnect) window.closeConnect();  // leave the Matrix layer if open
    if (name === activeChannel) return;
    activeChannel = name;
    _rememberChatTab();

    // Update tab highlights
    document.querySelectorAll('.channel-tab').forEach(t => {
        t.classList.toggle('active', t.dataset.channel === name);
    });

    // Clear and reload for this channel
    const box = document.getElementById('chat-messages');
    if (box) box.innerHTML = '';
    stopStatusPoller();
    loadChat();
    loadThreadInfo();
    loadContextUsage();
}

// =============================================================================
// Progress bar — unified thread + context indicator (replaces ctx-indicator + thread row)
// =============================================================================
let _threadCtx = {};  // cached context usage for modal display

function updateProgressBar(usage) {
    if (!usage) return;
    _threadCtx = usage;
    const fill = document.getElementById('chat-progress-fill');
    const pct = document.getElementById('chat-progress-pct');
    const bar = document.getElementById('chat-progress-bar');
    if (!fill || !pct) return;

    const percent = usage.percent || 0;
    const status = usage.status || 'ok';

    fill.style.width = Math.min(percent, 100) + '%';
    pct.textContent = percent + '%';

    // Color states
    bar.classList.remove('ctx-warn', 'ctx-crit');
    if (status === 'critical') {
        bar.classList.add('ctx-crit');
    } else if (status === 'warning') {
        bar.classList.add('ctx-warn');
    }

    bar.title = `${percent}% context · ${(usage.tokens_used || 0).toLocaleString()} / ${(usage.token_limit || 0).toLocaleString()} tokens · ${usage.message_count || 0} msgs — tap for options`;
}

async function loadContextUsage() {
    try {
        const res = await fetch(`/api/chat/context?channel=${activeChannel}`);
        const data = await res.json();
        if (data.context_usage) updateProgressBar(data.context_usage);
    } catch (e) {
        console.error('Failed to load context:', e);
    }
}

// Keep old function name as alias for any callers
function updateContextIndicator(usage) { updateProgressBar(usage); }

// =============================================================================
// Thread rotation handling
// =============================================================================
function handleThreadRotation(rotation) {
    const container = document.getElementById('chat-messages');
    if (container) container.innerHTML = '';

    addSystemMessage(
        `Thread rotated — ${rotation.memories_extracted} memories extracted from ${rotation.old_message_count || '?'} messages. ` +
        `Conversation continues on a fresh thread with full context summary.`
    );

    const label = document.getElementById('thread-label');
    if (label && rotation.new_thread) {
        label.textContent = rotation.new_thread.title || 'New Thread';
    }
    const meta = document.getElementById('thread-meta');
    if (meta) meta.textContent = 'auto-rotated just now';
}

function addSystemMessage(text) {
    const container = document.getElementById('chat-messages');
    if (!container) return;
    const div = document.createElement('div');
    div.className = 'message system-message';
    div.textContent = text;
    container.appendChild(div);
}

// =============================================================================
// Message display — with name, model, timestamp, thinking
// =============================================================================
function addMessage(role, content, timestamp, model, thinking) {
    const container = document.getElementById('chat-messages');
    const div = document.createElement('div');
    div.className = `message ${role === 'user' ? 'user' : 'assistant'}`;

    // Header
    const header = document.createElement('div');
    header.className = 'msg-header';

    const leftSide = document.createElement('span');
    leftSide.className = 'msg-header-left';

    const label = document.createElement('span');
    label.className = 'role-label';
    // Assistant label = the agent that is actually SPEAKING on this channel.
    // The presence's own agent name (MC.presence.agent_name) applies ONLY on the
    // personal-agent tab — on steward/merchant tabs it would label Stuart's
    // replies with the presence's agent (e.g. "KNIGHT" on the Stuart tab).
    const _onManagerTab = !!(activeAgent && (activeAgent.is_steward || activeAgent.is_merchant));
    const _aiLabel = _onManagerTab
        ? _getActiveAIName()
        : ((MC.presence && MC.presence.agent_name) || _getActiveAIName());
    label.textContent = role === 'user'
        ? ((MC.presence && MC.presence.display_name) || MC.operatorName).toUpperCase()
        : _aiLabel.toUpperCase();
    leftSide.appendChild(label);

    if (model && role !== 'user') {
        const modelTag = document.createElement('span');
        modelTag.className = 'msg-model';
        let shortModel = model;
        if (model.includes('/')) shortModel = model.split('/').pop();
        if (shortModel.length > 20) shortModel = shortModel.substring(0, 20);
        modelTag.textContent = shortModel;
        leftSide.appendChild(modelTag);
    }

    header.appendChild(leftSide);

    if (timestamp) {
        const ts = document.createElement('span');
        ts.className = 'msg-timestamp';
        const isoTs = timestamp instanceof Date ? timestamp.toISOString() : timestamp;
        ts.textContent = formatDate(isoTs);
        header.appendChild(ts);
    }

    div.appendChild(header);

    // Thinking block
    if (thinking && role !== 'user') {
        const thinkWrap = document.createElement('details');
        thinkWrap.className = 'thinking-block';
        const summary = document.createElement('summary');
        summary.textContent = 'Thinking';
        const thinkBody = document.createElement('div');
        thinkBody.className = 'thinking-content';
        thinkBody.innerHTML = formatMessage(thinking);
        thinkWrap.appendChild(summary);
        thinkWrap.appendChild(thinkBody);
        div.appendChild(thinkWrap);
    }

    // Body
    const body = document.createElement('div');
    body.className = 'msg-body';
    body.innerHTML = formatMessage(content);
    div.appendChild(body);
    container.appendChild(div);
}

function formatMessage(text) {
    if (!text) return '';
    return ESC(text)
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/`(.+?)`/g, '<code>$1</code>')
        .replace(/\n/g, '<br>');
}

function scrollToBottom() {
    const box = document.getElementById('chat-messages');
    if (box) box.scrollTop = box.scrollHeight;
}

// =============================================================================
// Typing indicator
// =============================================================================
function setTyping(show) {
    const existing = document.getElementById('typing-indicator');
    if (show && !existing) {
        const div = document.createElement('div');
        div.id = 'typing-indicator';
        div.className = 'message assistant';
        div.innerHTML = `<div class="msg-header"><span class="msg-header-left"><span class="role-label">${ESC(_getActiveAIName().toUpperCase())}</span></span></div><div class="msg-body typing"><span id="typing-status">thinking...</span></div>`;
        document.getElementById('chat-messages').appendChild(div);
        scrollToBottom();
    } else if (!show && existing) {
        existing.remove();
    }
}

function updateTypingStatus(text) {
    const el = document.getElementById('typing-status');
    if (el) el.textContent = text;
}

// =============================================================================
// Activity feed
// =============================================================================
function addActivityStep(text) {
    const live = document.getElementById('activity-live');
    if (!live) return;
    live.style.display = 'block';
    const step = document.createElement('div');
    step.className = 'activity-step';
    step.textContent = text;
    live.appendChild(step);
    live.scrollTop = live.scrollHeight;
}

function clearActivitySteps() {
    const live = document.getElementById('activity-live');
    if (!live || !live.children.length) { if (live) live.style.display = 'none'; return; }

    const stepCount = live.children.length;
    const messages = document.getElementById('chat-messages');
    if (messages && stepCount > 0) {
        const details = document.createElement('details');
        details.className = 'activity-collapsed';
        const summary = document.createElement('summary');
        summary.textContent = `${stepCount} steps`;
        details.appendChild(summary);
        const wrap = document.createElement('div');
        wrap.className = 'activity-steps-wrap';
        while (live.firstChild) wrap.appendChild(live.firstChild);
        details.appendChild(wrap);
        messages.appendChild(details);
    }

    live.innerHTML = '';
    live.style.display = 'none';
}

function _renderPersistedActivity(record) {
    const messages = document.getElementById('chat-messages');
    if (!messages || !record.steps || !record.steps.length) return;

    const details = document.createElement('details');
    details.className = 'activity-collapsed';
    const summary = document.createElement('summary');
    summary.textContent = `${record.step_count} steps`;
    details.appendChild(summary);
    const wrap = document.createElement('div');
    wrap.className = 'activity-steps-wrap';
    record.steps.forEach(text => {
        const step = document.createElement('div');
        step.className = 'activity-step';
        step.textContent = text;
        wrap.appendChild(step);
    });
    details.appendChild(wrap);
    messages.appendChild(details);
}

// =============================================================================
// Send message — SSE streaming
// =============================================================================
async function sendMessage() {
    if (sending) return;
    const input = document.getElementById('chat-input');
    const msg = input.value.trim();
    if (!msg) return;

    const sendBtn = document.getElementById('chat-send');
    const stopBtn = document.getElementById('chat-stop');

    sending = true;
    input.value = '';
    input.style.height = 'auto';
    sendBtn.style.display = 'none';
    const micBtn = document.getElementById('chat-mic');
    if (micBtn) micBtn.style.display = 'none';
    stopBtn.style.display = '';
    stopBtn.disabled = false;            // clear any stale "stopping" state from a prior turn
    stopBtn.classList.remove('btn-busy');

    const welcome = document.querySelector('.chat-welcome');
    if (welcome) welcome.remove();

    addMessage('user', msg, new Date());
    setTyping(true);
    updateTypingStatus(`${_getActiveAIName()} is thinking...`);
    scrollToBottom();

    let finalData = null;  // outside try so post-send TTS can read it after Stop clears
    let detached = false;  // stream died; server turn may still be running
    try {
        const res = await fetch('/api/chat/send', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message: msg,
                channel: activeChannel,
                input_mode: typeof chatMode !== 'undefined' ? chatMode : 'type',
            }),
        });

        // A non-stream error (4xx/5xx) would otherwise read as an empty stream and look
        // like "nothing happened." Surface it instead.
        if (!res.ok) {
            let detail = '';
            let code = '';
            try {
                const j = await res.json();
                detail = j.detail || j.error || '';
                code = j.error || '';
            } catch (e) {}
            // 409 = turn already running (e.g. after lock detach). Attach to status.
            if (res.status === 409 || code === 'already_processing') {
                detached = true;
                updateTypingStatus('Still working on your last message…');
            } else {
                throw new Error(`server ${res.status}${detail ? ': ' + detail : ''}`);
            }
        } else {
            const reader = res.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            while (true) {
                let chunk;
                try {
                    chunk = await reader.read();
                } catch (readErr) {
                    // Phone lock / tab freeze kills the SSE body. Do not paint a fake
                    // assistant "Connection error" — server turn keeps running.
                    console.warn('[chat] SSE read failed; detaching to status poll', readErr);
                    detached = true;
                    break;
                }
                const { done, value } = chunk;
                if (done) break;

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop();

                for (const line of lines) {
                    if (!line.startsWith('data: ')) continue;
                    try {
                        const event = JSON.parse(line.slice(6));

                        if (event.type === 'status') {
                            updateTypingStatus(event.text);
                        } else if (event.type === 'tool_call') {
                            updateTypingStatus(`▸ ${event.tool}(${(event.args || '').substring(0, 80)})`);
                            addActivityStep(`Tool: ${event.tool}(${event.args || ''})`);
                            scrollToBottom();
                        } else if (event.type === 'tool_result') {
                            const preview = event.preview || '(done)';
                            addActivityStep(`Result: ${preview}`);
                            updateTypingStatus(`▸ Result: ${preview.substring(0, 80)}`);
                            scrollToBottom();
                        } else if (event.type === 'heartbeat') {
                            const secs = Math.round(event.elapsed || 0);
                            updateTypingStatus(`${event.step || 'Working...'} (${secs}s)`);
                        } else if (event.type === 'rotation') {
                            handleThreadRotation(event.data);
                        } else if (event.type === 'cancelled') {
                            addSystemMessage('Generation stopped.');
                        } else if (event.type === 'error') {
                            addMessage('assistant', `Error: ${event.message}`, new Date());
                        } else if (event.type === 'done') {
                            finalData = event.data;
                        }
                    } catch (e) { /* skip malformed SSE lines */ }
                }
            }
        }

        if (detached) {
            // Hand off to status poller — unlock/tab-back will show progress + final reply.
            updateTypingStatus('Still working… (you can lock the phone)');
            sending = false;
            if (!_statusPoller) {
                _statusPoller = setInterval(pollChatStatus, 3000);
            }
            // Keep Stop visible so explicit cancel still works while detached.
            pollChatStatus();
            return;
        }

        setTyping(false);
        clearActivitySteps();

        if (finalData) {
            if (finalData.cancelled) {
                // Already showed "Generation stopped."
            } else if (finalData.error) {
                addMessage('assistant', `Error: ${finalData.error}`, new Date());
            } else {
                addMessage('assistant', finalData.response || '(no response)', new Date(), finalData.model || '', finalData.thinking || null);
            }
        } else {
            // Stream ended with no done event — recover from server history/status
            // instead of inventing a connection error bubble.
            await checkChatProcessing();
            if (!_statusPoller) {
                const box = document.getElementById('chat-messages');
                if (box) box.innerHTML = '';
                await loadChat();
            }
        }

        loadContextUsage();
    } catch (e) {
        // Hard failures before/without a running turn. If status says processing,
        // detach rather than lie about connection.
        console.warn('[chat] send failed', e);
        try {
            const res = await fetch(`/api/chat/status?channel=${activeChannel}`);
            const data = await res.json();
            if (data.processing) {
                detached = true;
                updateTypingStatus('Still working… (you can lock the phone)');
                sending = false;
                if (!_statusPoller) {
                    _statusPoller = setInterval(pollChatStatus, 3000);
                }
                pollChatStatus();
                return;
            }
        } catch (statusErr) { /* fall through */ }
        setTyping(false);
        clearActivitySteps();
        addMessage('assistant', `Connection error: ${e.message}`, new Date());
    }

    // Release the chat Stop control as soon as generation ends — before TTS.
    // Speaking is a mic-state concern; holding Stop through playTTS is what made
    // a hung voice host look like the whole reply was stuck.
    sending = false;
    stopBtn.style.display = 'none';
    stopBtn.disabled = false;
    stopBtn.classList.remove('btn-busy');
    stopBtn.title = '';

    // Voice mode: speak after Stop is cleared, then auto-resume listening.
    // playTTS hard-times out per chunk and fail-opens (text stays on screen).
    if (finalData && !finalData.cancelled && !finalData.error
        && chatMode === 'voice' && voiceActive && finalData.response) {
        const micBtnSpeak = (typeof getMicBtn === 'function') ? getMicBtn() : document.getElementById('chat-mic');
        if (micBtnSpeak) micBtnSpeak.style.display = '';
        if (typeof updateMicState === 'function') updateMicState('speaking');
        try {
            if (typeof playTTS === 'function') await playTTS(finalData.response);
        } catch (ttsErr) {
            console.warn('[voice] playTTS failed open:', ttsErr);
        }
        if (voiceActive) {
            if (typeof micStartContinuous === 'function') micStartContinuous();
            if (typeof updateMicState === 'function') updateMicState('listening');
        } else if (typeof updateMicState === 'function') {
            updateMicState('idle');
        }
    }

    // Restore input area based on mode
    if (chatMode === 'type') {
        sendBtn.style.display = '';
        input.focus();
    } else if (chatMode === 'voice' && voiceActive) {
        // Voice conversation in progress — mic is managed by auto-resume above
        const micBtn2 = getMicBtn();  // Defined in voice.js
        if (micBtn2) micBtn2.style.display = '';
    } else {
        // Dictate or Voice (not active) — show mic button, keep send hidden
        const micBtn2 = getMicBtn();  // Defined in voice.js
        if (micBtn2) micBtn2.style.display = '';
    }
    scrollToBottom();
}

async function cancelSend() {
    // Immediate tap feedback — show the stop registered while the stream winds down.
    const _stopBtn = document.getElementById('chat-stop');
    if (_stopBtn) {
        if (_stopBtn.disabled) return;   // already stopping
        _stopBtn.disabled = true;
        _stopBtn.classList.add('btn-busy');
        _stopBtn.title = 'Stopping…';
    }
    try {
        await fetch('/api/chat/cancel', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ channel: activeChannel }),
        });
    } catch (e) {
        console.error('Cancel failed:', e);
    }
}

// =============================================================================
// New thread
// =============================================================================
let _resetThreadInFlight = false;
async function resetThread() {
    if (_resetThreadInFlight) return;  // guard against double-tap while archiving
    if (!confirm('Start a new thread? Current conversation will be archived and memories extracted.')) return;
    _resetThreadInFlight = true;
    const container = document.getElementById('chat-messages');
    // Immediate feedback — archiving + memory extraction can take several seconds, and the
    // modal has already closed, so without this the old conversation just sits there and the
    // action looks dead (→ double-tap). Show a working state right away.
    if (container) {
        container.innerHTML = `<div class="chat-welcome"><div class="loading">Starting a new thread… archiving and extracting memories</div></div>`;
    }
    try {
        const res = await fetch(`/api/chat/reset?channel=${activeChannel}`, { method: 'DELETE' });
        const data = await res.json();
        if (data.success) {
            if (container) container.innerHTML = '';
            addSystemMessage('New thread started. Previous conversation archived.');
            loadContextUsage();
        } else {
            // Reset didn't take — restore the conversation so nothing looks lost.
            if (container) container.innerHTML = '';
            await loadChat();
            alert('Could not start a new thread. Please try again.');
        }
    } catch (e) {
        console.error('Reset failed:', e);
        if (container) container.innerHTML = '';
        await loadChat();
        alert('Could not start a new thread. Please try again.');
    } finally {
        _resetThreadInFlight = false;
    }
}

// =============================================================================
// Load history
// =============================================================================
async function loadChat() {
    try {
        const data = await fetch(`/api/chat/history?channel=${activeChannel}`).then(r => r.json());
        console.log('[chat] loadChat agent=' + (activeAgent && activeAgent.id) + ' channel=' + activeChannel
            + ' thread=' + (data.thread_id || '?') + ' msgs=' + (data.messages ? data.messages.length : 0)
            + (data.error ? ' error=' + data.error : ''));
        const box = document.getElementById('chat-messages');
        if (!data.messages || data.messages.length === 0) return;

        box.innerHTML = '';

        const activity = (data.activity || []).slice();
        let actIdx = 0;

        data.messages.forEach(m => {
            const ts = m.timestamp || null;

            if (m.role === 'ai' && ts) {
                const msgTime = new Date(ts).getTime();
                while (actIdx < activity.length) {
                    const actTime = new Date(activity[actIdx].recorded_at).getTime();
                    if (actTime <= msgTime) {
                        _renderPersistedActivity(activity[actIdx]);
                        actIdx++;
                    } else break;
                }
            }

            if (m.role === 'human') addMessage('user', m.content, ts);
            else if (m.role === 'ai') addMessage('assistant', m.content, ts, m.model || '', m.thinking || null);
        });

        while (actIdx < activity.length) {
            _renderPersistedActivity(activity[actIdx]);
            actIdx++;
        }

        scrollToBottom();
    } catch (e) {
        console.error('Failed to load chat history:', e);
    }
    loadContextUsage();
    checkChatProcessing();
}

// =============================================================================
// Cross-device + lock-resume processing awareness
// Server owns the turn. SSE is a live window; status poll is the durable attach.
// Phone lock / tab hide must detach the window, not invent a connection error.
// =============================================================================
let _statusPoller = null;
let _renderedStepCount = 0;
let _chatVisibilityBound = false;

function renderPolledSteps(steps) {
    if (!steps || !steps.length) return;
    if (steps.length <= _renderedStepCount) return;
    for (let i = _renderedStepCount; i < steps.length; i++) {
        addActivityStep(steps[i]);
    }
    _renderedStepCount = steps.length;
}

function _restoreChatChromeAfterTurn() {
    const sendBtn = document.getElementById('chat-send');
    const stopBtn = document.getElementById('chat-stop');
    const input = document.getElementById('chat-input');
    if (stopBtn) {
        stopBtn.style.display = 'none';
        stopBtn.disabled = false;
        stopBtn.classList.remove('btn-busy');
        stopBtn.title = '';
    }
    if (typeof chatMode !== 'undefined' && chatMode === 'type') {
        if (sendBtn) sendBtn.style.display = '';
        if (input) input.focus();
    } else {
        const micBtn2 = (typeof getMicBtn === 'function') ? getMicBtn() : document.getElementById('chat-mic');
        if (micBtn2) micBtn2.style.display = '';
    }
}

async function checkChatProcessing() {
    // Even while sending=true after a detach handoff we want status; only skip when
    // this tab still owns a live SSE stream (sending and no poller yet is fine —
    // sendMessage starts the poller on detach).
    if (sending && !_statusPoller) return;
    try {
        const res = await fetch(`/api/chat/status?channel=${activeChannel}`);
        const data = await res.json();
        if (data.processing) {
            const secs = Math.round(data.elapsed_seconds || 0);
            setTyping(true);
            updateTypingStatus(`${data.step || 'Working...'} (${secs}s)`);
            renderPolledSteps(data.steps);
            const stopBtn = document.getElementById('chat-stop');
            const sendBtn = document.getElementById('chat-send');
            if (stopBtn) {
                stopBtn.style.display = '';
                stopBtn.disabled = false;
            }
            if (sendBtn) sendBtn.style.display = 'none';
            if (!_statusPoller) {
                _statusPoller = setInterval(pollChatStatus, 3000);
            }
        } else {
            stopStatusPoller();
        }
    } catch (e) { /* non-fatal */ }
}

async function pollChatStatus() {
    // Live SSE owner drives UI from the stream; poller is for detach/cross-device.
    if (sending && !_statusPoller) { return; }
    try {
        const res = await fetch(`/api/chat/status?channel=${activeChannel}`);
        const data = await res.json();
        if (data.processing) {
            const secs = Math.round(data.elapsed_seconds || 0);
            setTyping(true);
            updateTypingStatus(`${data.step || 'Working...'} (${secs}s)`);
            renderPolledSteps(data.steps);
            const stopBtn = document.getElementById('chat-stop');
            if (stopBtn) stopBtn.style.display = '';
        } else {
            setTyping(false);
            clearActivitySteps();
            stopStatusPoller();
            sending = false;
            const box = document.getElementById('chat-messages');
            if (box) box.innerHTML = '';
            await loadChat();
            _restoreChatChromeAfterTurn();
            loadContextUsage();
        }
    } catch (e) {
        // Transient network (mesh sleep) — keep poller; visibility resume retries.
        console.warn('[chat] status poll failed; will retry', e);
    }
}

function stopStatusPoller() {
    if (_statusPoller) {
        clearInterval(_statusPoller);
        _statusPoller = null;
    }
    _renderedStepCount = 0;
}

function _onChatVisibilityResume() {
    if (document.hidden) return;
    // Unlock / tab back: re-attach to server truth. Do not require a full refresh.
    checkChatProcessing();
    if (!_statusPoller && !sending) {
        // Idle return — cheap history refresh so a finished turn while locked appears.
        loadChat();
    }
}

function bindChatVisibilityResume() {
    if (_chatVisibilityBound) return;
    _chatVisibilityBound = true;
    document.addEventListener('visibilitychange', _onChatVisibilityResume);
    window.addEventListener('pageshow', _onChatVisibilityResume);
    window.addEventListener('focus', _onChatVisibilityResume);
}

// =============================================================================
// Thread info + history modal
// =============================================================================
let _activeThread = null;  // cached for modal display

async function loadThreadInfo() {
    try {
        const res = await fetch(`/api/threads?channel=${activeChannel}&status=active`);
        const data = await res.json();
        if (data.threads && data.threads.length > 0) {
            _activeThread = data.threads[0];
            const label = document.getElementById('chat-progress-label');
            if (label) label.textContent = _activeThread.title || 'Chat';
        }
    } catch (e) { /* non-fatal */ }
}

// =============================================================================
// Thread management modal — opens from progress bar click
// =============================================================================
function showThreadModal() {
    const overlay = document.createElement('div');
    overlay.className = 'thread-history-overlay';
    overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

    const t = _activeThread || {};
    const ctx = _threadCtx || {};
    const percent = ctx.percent || 0;
    const tokens = (ctx.tokens_used || 0).toLocaleString();
    const limit = (ctx.token_limit || 0).toLocaleString();
    const msgCount = ctx.message_count || t.message_count || 0;
    const created = t.created_at ? formatDate(t.created_at, {month:'short', day:'numeric', year:'numeric'}) : '';
    const title = ESC(t.title || 'Current Thread');
    const threadId = t.thread_id || '';

    let html = `<div class="thread-mgmt-modal">
        <button class="close-modal" onclick="this.closest('.thread-history-overlay').remove()">×</button>
        <h3>${title}</h3>
        <div class="thread-mgmt-stats">
            <div class="thread-mgmt-bar">
                <div class="thread-mgmt-bar-fill" style="width:${Math.min(percent,100)}%"></div>
            </div>
            <div class="thread-mgmt-info">
                <span>${percent}% context</span>
                <span>${tokens} / ${limit} tokens</span>
                <span>${msgCount} messages</span>
                ${created ? `<span>Started ${created}</span>` : ''}
            </div>
        </div>
        <div class="thread-mgmt-actions">
            <button class="btn-primary" onclick="this.closest('.thread-history-overlay').remove(); resetThread()">
                New Thread
            </button>`;

    if (threadId) {
        html += `
            <button class="btn-small" onclick="_extractMemories('${ESC(threadId)}', this)">
                Extract Memories
            </button>`;
    }

    html += `
            <button class="btn-small" onclick="this.closest('.thread-history-overlay').remove(); showThreadHistory()">
                Thread History
            </button>
        </div>
    </div>`;

    overlay.innerHTML = html;
    document.body.appendChild(overlay);
}

async function _extractMemories(threadId, btn) {
    if (!threadId) return;
    const origText = btn.textContent;
    btn.textContent = 'Extracting...';
    btn.disabled = true;
    try {
        const res = await fetch(`/api/memories/extract/${threadId}`, { method: 'POST' });
        const data = await res.json();
        if (data.error) {
            btn.textContent = 'Failed';
        } else {
            const count = data.memories_created || data.extracted || 0;
            btn.textContent = `${count} memories extracted`;
        }
    } catch (e) {
        btn.textContent = 'Failed';
    }
    setTimeout(() => { btn.textContent = origText; btn.disabled = false; }, 3000);
}

async function showThreadHistory() {
    try {
        const res = await fetch(`/api/threads?channel=${activeChannel}&limit=20`);
        const data = await res.json();

        if (!data.threads || data.threads.length === 0) {
            alert('No thread history found.');
            return;
        }

        const overlay = document.createElement('div');
        overlay.className = 'thread-history-overlay';
        overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

        let html = `<div class="thread-history-modal">
            <button class="close-modal" onclick="this.closest('.thread-history-overlay').remove()">×</button>
            <h3>Thread History</h3>`;

        data.threads.forEach(t => {
            const created = t.created_at ? formatDate(t.created_at, {month:'short', day:'numeric', year:'numeric'}) : '';
            const archived = t.archived_at ? formatDate(t.archived_at, {month:'short', day:'numeric'}) : '';
            const statusClass = t.status || 'active';

            html += `<div class="thread-item clickable" onclick="openThreadReader('${ESC(t.thread_id)}')">
                <div class="thread-info">
                    <span class="thread-title">${ESC(t.title || 'Conversation')}</span>
                    <span class="thread-detail">
                        ${t.message_count || 0} messages · Created ${created}
                        ${archived ? ` · Archived ${archived}` : ''}
                        ${t.extraction_count ? ` · ${t.extraction_count} memories extracted` : ''}
                    </span>
                </div>
                <span class="thread-status ${statusClass}">${t.status}</span>
            </div>`;
        });

        html += `</div>`;
        overlay.innerHTML = html;
        document.body.appendChild(overlay);

    } catch (e) {
        console.error('Failed to load thread history:', e);
    }
}

async function openThreadReader(threadId) {
    const overlay = document.createElement('div');
    overlay.className = 'thread-history-overlay';
    overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

    overlay.innerHTML = `<div class="thread-reader-modal">
        <button class="close-modal" onclick="this.closest('.thread-history-overlay').remove()">×</button>
        <div class="thread-reader-header"><h3>Loading thread...</h3></div>
        <div class="thread-reader-messages"><span class="empty">Loading messages...</span></div>
    </div>`;
    document.body.appendChild(overlay);

    try {
        const res = await fetch(`/api/threads/${encodeURIComponent(threadId)}/history`);
        const data = await res.json();

        if (data.error) {
            overlay.querySelector('.thread-reader-messages').innerHTML =
                `<span class="empty">Error: ${ESC(data.error)}</span>`;
            return;
        }

        const headerEl = overlay.querySelector('.thread-reader-header');
        const meta = data.thread || {};
        const created = meta.created_at ? formatDate(meta.created_at, {month:'short', day:'numeric', year:'numeric'}) : '';
        const archived = meta.archived_at ? formatDate(meta.archived_at, {month:'short', day:'numeric'}) : '';
        const statusClass = meta.status || 'active';

        headerEl.innerHTML = `
            <h3>${ESC(meta.title || (created ? 'Conversation — ' + created : 'Conversation'))}</h3>
            <div class="thread-reader-meta">
                <span class="thread-status ${statusClass}">${meta.status || 'unknown'}</span>
                <span>${data.messages ? data.messages.length : 0} messages</span>
                ${created ? `<span>Created ${created}</span>` : ''}
                ${archived ? `<span>Archived ${archived}</span>` : ''}
            </div>
        `;

        const messagesEl = overlay.querySelector('.thread-reader-messages');
        if (!data.messages || data.messages.length === 0) {
            messagesEl.innerHTML = '<span class="empty">No messages in this thread.</span>';
            return;
        }

        const activity = (data.activity || []).slice();
        let actIdx = 0;

        function renderActivityHtml(record) {
            if (!record.steps || !record.steps.length) return '';
            const stepsHtml = record.steps.map(s => `<div class="activity-step">${ESC(s)}</div>`).join('');
            return `<details class="activity-collapsed">
                <summary>${record.step_count} steps</summary>
                <div class="activity-steps-wrap">${stepsHtml}</div>
            </details>`;
        }

        let html = '';
        data.messages.forEach(msg => {
            const role = msg.role === 'human' ? 'user' : 'assistant';
            // Same speaker-label rule as addMessage: on steward/merchant tabs the
            // speaking agent's name wins over the presence's own agent name.
            const _readerManagerTab = !!(activeAgent && (activeAgent.is_steward || activeAgent.is_merchant));
            const _readerAiLabel = _readerManagerTab
                ? _getActiveAIName()
                : ((MC.presence && MC.presence.agent_name) || MC.agentName);
            const roleLabel = role === 'user'
                ? ((MC.presence && MC.presence.display_name) || MC.operatorName).toUpperCase()
                : _readerAiLabel.toUpperCase();
            let ts = '';
            if (msg.timestamp) {
                const d = new Date(msg.timestamp);
                ts = formatDate(msg.timestamp);
            }

            if (role === 'assistant' && msg.timestamp) {
                const msgTime = new Date(msg.timestamp).getTime();
                while (actIdx < activity.length) {
                    const actTime = new Date(activity[actIdx].recorded_at).getTime();
                    if (actTime <= msgTime) {
                        html += renderActivityHtml(activity[actIdx]);
                        actIdx++;
                    } else break;
                }
            }

            let modelTag = '';
            if (msg.model && role !== 'user') {
                let shortModel = msg.model;
                if (shortModel.includes('/')) shortModel = shortModel.split('/').pop();
                if (shortModel.length > 20) shortModel = shortModel.substring(0, 20);
                modelTag = `<span class="thread-reader-model">${ESC(shortModel)}</span>`;
            }

            let thinkingHtml = '';
            if (msg.thinking && role !== 'user') {
                thinkingHtml = `<details class="thinking-block">
                    <summary>Thinking</summary>
                    <div class="thinking-content">${formatMessage(msg.thinking)}</div>
                </details>`;
            }

            html += `<div class="message ${role}">
                <div class="msg-header">
                    <span class="msg-header-left">
                        <span class="role-label">${roleLabel}</span>
                        ${modelTag}
                    </span>
                    ${ts ? `<span class="msg-timestamp">${ts}</span>` : ''}
                </div>
                ${thinkingHtml}
                <div class="msg-body">${formatMessage(msg.content)}</div>
            </div>`;
        });

        while (actIdx < activity.length) {
            html += renderActivityHtml(activity[actIdx]);
            actIdx++;
        }

        messagesEl.innerHTML = html;

    } catch (e) {
        const messagesEl = overlay.querySelector('.thread-reader-messages');
        if (messagesEl) {
            messagesEl.innerHTML = `<span class="empty">Failed to load: ${ESC(e.message)}</span>`;
        }
    }
}

// =============================================================================
// Event listeners
// =============================================================================
let _chatInitialized = false;
function _rememberChatTab() {
    // Persist the selected agent tab + channel so a refresh (or an auto thread
    // rotation reload) doesn't dump the operator back on the personal-agent tab.
    try {
        sessionStorage.setItem('mc-chat-tab', JSON.stringify({
            agentId: activeAgent && activeAgent.id,
            channel: activeChannel,
        }));
    } catch (e) { /* private mode etc — non-fatal */ }
}

function _restoreChatTab(agents) {
    try {
        const saved = JSON.parse(sessionStorage.getItem('mc-chat-tab') || 'null');
        if (!saved || !saved.agentId) return null;
        const agent = agents.find(a => a.id === saved.agentId);
        if (!agent) return null;
        const channel = agent.channels.includes(saved.channel)
            ? saved.channel : (agent.channels[0] || MC.defaultChannel);
        return { agent, channel };
    } catch (e) { return null; }
}

function initChat() {
    if (_chatInitialized) return;
    _chatInitialized = true;
    // Set up active agent — restore the last selected tab, else first (host) agent
    const agents = _getChatAgents();
    const restored = _restoreChatTab(agents);
    activeAgent = (restored && restored.agent) || agents[0];
    activeChannel = (restored && restored.channel) || activeAgent.channels[0] || MC.defaultChannel;
    console.log('[chat] initChat agent=' + (activeAgent && activeAgent.id) + ' name=' + (activeAgent && activeAgent.name) + ' channel=' + activeChannel + ' channels=' + JSON.stringify(activeAgent && activeAgent.channels));

    // Set welcome/placeholder from active agent
    _updateAgentUI();

    // Build two-level channel selector (agent tabs + day/deep)
    buildChannelTabs();

    // Wire up buttons
    document.getElementById('chat-send').addEventListener('click', sendMessage);
    document.getElementById('chat-stop').addEventListener('click', cancelSend);
    document.getElementById('chat-input').addEventListener('keydown', e => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });
    document.getElementById('chat-input').addEventListener('input', function() {
        this.style.height = 'auto';
        this.style.height = Math.min(this.scrollHeight, 150) + 'px';
    });
    // New Thread is now in the thread modal (showThreadModal), no standalone button

    // Wire mic button + mode bar buttons
    // voice.js loads AFTER this file, so getMicBtn/micToggle/switchChatMode
    // may not exist yet. Wire them with deferred references.
    const _micBtn = document.getElementById('chat-mic');
    if (_micBtn) _micBtn.addEventListener('click', () => { if (typeof micToggle === 'function') micToggle(); });

    document.querySelectorAll('.chat-mode-btn').forEach(btn => {
        btn.addEventListener('click', () => { if (typeof switchChatMode === 'function') switchChatMode(btn.dataset.mode); });
    });

    // Manager/admin MC → read-only supervisory view (rendered by manager-chat.js's
    // initStewardChat, which auto-runs right after this script). Skip the interactive
    // chat load entirely so its async loadChat() never clobbers the supervisory render.
    if (MC.adminView === true) return;

    // Lock/unlock + tab back: re-attach to /api/chat/status so a finished turn
    // while the phone slept shows up without a full refresh.
    bindChatVisibilityResume();

    // #PERF-MC1: only pull history when Chat is the active tab. Idle-prefetch and
    // cold boot used to fetch /api/chat/history on every messaging.js inject even
    // when landing on Home — wasteful on DERP/hotspot paths. switchToTab('chat')
    // still calls loadChat() once the user opens Chat.
    const _chatActive = (typeof activeTab !== 'undefined' && activeTab === 'chat')
        || !!(document.getElementById('panel-chat') && document.getElementById('panel-chat').classList.contains('active'));
    if (_chatActive) {
        loadChat();
        loadThreadInfo();
        loadContextUsage();
    }
}

// =============================================================================
// Auto-init — runs when this script loads (core.js has already populated MC)
// =============================================================================
// initChat() always runs here. If manager-chat.js loads after this, it will
// detect _isManagerMC() and call initStewardChat() to override with supervisory mode.
// =============================================================================
try {
    initChat();
    if (window._mcDebugLog) window._mcDebugLog('[CHAT] initChat() complete, channel=' + activeChannel + ', channels=' + Object.keys(MC.channels).join(','));
} catch (e) {
    console.error('initChat failed:', e);
    if (window._mcDebugLog) window._mcDebugLog('[CHAT ERR] initChat: ' + e.message);
}
