// =============================================================================
// Panels — HTML template generation for tab panels and detail views
// =============================================================================
// Extracted from core.js. Defines _defaultPanelHTML() and _buildDetailPanels().
// Loaded before core.js so these functions are available when boot() runs.
// References MC, ESC, LP, lpColor, isMobile, avatarPath, etc. from core.js —
// these resolve at call time (boot), not at load time.
// =============================================================================
function _defaultPanelHTML(tabId) {
    // Provide skeleton HTML for core tab types so their JS can find elements.
    // Agent-specific tabs get a generic loading state.
    switch (tabId) {
        case 'home': {
            // ── Cove-admin apex surface: {cove}.{domain} opened by an admin ──
            // Stub for now: Cove name + the list of Presences in this Cove.
            // The personal home still lives at {handle}.{cove}.{domain}.
            if (MC.coveAdminView) {
                return `<div class="panel-toolbar">
                    <span class="panel-title">Presences</span>
                </div>
                <div class="panel-scroll">
                    <div id="cove-admin-presences"><div class="loading">Loading Presences...</div></div>
                </div>`;
            }
            // ── Tuner tiers (Free + Pro): tuning-first stripped Home ────────
            if (MC.tier && MC.tier.level < 10) {
                return `<div class="panel-scroll">
                    <div class="home-section ql-section">
                        <div class="home-section-header">
                            <span class="home-section-title">Quick Lists</span>
                            <a href="#" onclick="qlNewList(); return false;" class="home-link">+ New</a>
                        </div>
                        <div id="ql-cards" class="ql-cards"><div class="loading">Loading...</div></div>
                    </div>
                    <div class="home-top-grid">
                        <div class="home-section">
                            <div class="home-nav-buttons">
                                <button class="home-nav-btn" id="homeTuneBtn" onclick="tuneNow()">
                                    <span class="home-nav-icon">🎵</span>
                                    <span id="homeTuneLabel">Tune</span>
                                </button>
                                <button class="home-nav-btn" onclick="switchToTab('playlists')">
                                    <span class="home-nav-icon">🎧</span>
                                    <span>Playlists</span>
                                </button>
                                <button class="home-nav-btn" onclick="switchToTab('go-deeper')">
                                    <span class="home-nav-icon">📖</span>
                                    <span>Go Deeper</span>
                                </button>
                            </div>
                            <button class="home-nav-btn onboarding-start-btn" id="onboardingStartBtn" onclick="_startOnboarding()" style="margin-top:8px;width:100%;justify-content:center;border:1px dashed var(--border);opacity:0.85;">
                                <span class="home-nav-icon">✦</span>
                                <span>Getting Started</span>
                            </button>
                        </div>
                        <div class="home-section">
                            <div class="home-section-header">
                                <span class="home-section-title">Latest Tuning</span>
                                <div class="tuning-header-links" id="tuningHeaderLinks"></div>
                                <a href="#" id="openTuningLink" onclick="openOperatorTuning(); return false;" class="home-link">Open &rarr;</a>
                            </div>
                            <div id="overviewTuning"><div class="loading">Loading...</div></div>
                        </div>
                    </div>
                    <div id="home-upgrade-inline"></div>
                </div>`;
            }

            // ── Operator/Presence/Cove: standard Attention Home ────────────────
            const isAdmin = MC.instance?.type === 'admin' || MC.instance?.type === 'domain' || MC.instance?.type === 'manager';
            const approvalsSection = `
                    <div class="home-section">
                        <div class="home-section-header">
                            <span class="home-section-title">Pending Approvals</span>
                            <span id="home-approvals-badge" class="home-badge hidden">0</span>
                            <a href="#" onclick="switchBoard('action'); switchToTab('ab-actions'); return false;" class="home-link">Actions &rarr;</a>
                        </div>
                        <div id="home-approvals" class="home-approvals"><span class="empty-msg">No pending approvals</span></div>
                    </div>`;
            const rosterSection = isAdmin ? `
                <div class="home-section">
                    <div id="agentRoster"></div>
                </div>` : '';
            return `<div class="panel-scroll">
                <div class="home-section ql-section">
                    <div class="home-section-header">
                        <span class="home-section-title">Quick Lists</span>
                        <a href="#" onclick="qlNewList(); return false;" class="home-link">+ New</a>
                    </div>
                    <div id="ql-cards" class="ql-cards"><div class="loading">Loading...</div></div>
                </div>
                <div class="home-top-grid">
                    ${approvalsSection}
                    <div class="home-section">
                        <div class="home-section-header">
                            <span class="home-section-title">Latest Tuning</span>
                            <div class="tuning-header-links" id="tuningHeaderLinks"></div>
                            <a href="#" id="openTuningLink" onclick="openOperatorTuning(); return false;" class="home-link">Open &rarr;</a>
                        </div>
                        <div id="overviewTuning"><div class="loading">Loading...</div></div>
                    </div>
                </div>
                <div class="home-section">
                    <div class="home-section-header">
                        <span class="home-section-title">Projects</span>
                        <div class="home-header-actions">
                            <button class="btn-icon-add" onclick="showNewProjectForm()" title="New project">+</button>
                            <a href="#" onclick="switchToTab('projects'); return false;" class="home-link">View all &rarr;</a>
                        </div>
                    </div>
                    <div id="new-project-form" class="quick-add-form" style="display:none;">
                        <input type="text" id="new-project-title" class="task-input" placeholder="Project name..." onkeydown="if(event.key==='Enter')createProject();">
                        <button class="btn-small btn-save" onclick="createProject()">Add</button>
                    </div>
                    <div id="projectsList"></div>
                </div>
                <div class="home-section">
                    <div class="home-section-header">
                        <span class="home-section-title">Tasks</span>
                        <div class="home-header-actions">
                            <button class="btn-icon-info" onclick="toggleTaskLegend()" title="Legend">i</button>
                            <button class="btn-icon-add" onclick="showNewTaskForm()" title="New task">+</button>
                            <a href="#" onclick="showCompletedTasks(); return false;" class="home-link">Done</a>
                            <a href="#" onclick="loadAllHomeTasks(); return false;" class="home-link">View all &rarr;</a>
                        </div>
                    </div>
                    <div id="task-legend" class="task-legend" style="display:none;">
                        <div class="legend-row"><span class="pri-dot pri-urgent"></span> Urgent</div>
                        <div class="legend-row"><span class="pri-dot pri-high"></span> High</div>
                        <div class="legend-row"><span class="pri-dot pri-normal"></span> Normal</div>
                        <div class="legend-row"><span class="pri-dot pri-low"></span> Low</div>
                        <div class="legend-divider"></div>
                        <div class="legend-row"><span class="due-sample due-overdue">May 1</span> Overdue</div>
                        <div class="legend-row"><span class="due-sample due-soon">May 9</span> Today / Tomorrow</div>
                        <div class="legend-row"><span class="due-sample due-later">Jun 15</span> Later</div>
                    </div>
                    <div id="new-task-form" class="quick-add-form" style="display:none;">
                        <input type="text" id="new-task-title" class="task-input" placeholder="Task title..." onkeydown="if(event.key==='Enter')createTask();">
                        <button class="btn-small btn-save" onclick="createTask()">Add</button>
                    </div>
                    <div id="home-tasks" class="home-tasks"><div class="loading">Loading...</div></div>
                </div>
                ${rosterSection}
                <div id="home-upgrade-inline"></div>
            </div>`;
        }

        // ── Operator Tune tab — uses ot* IDs so tuning-panel.js works directly
        case 'tune':
            // Tune — personal tuning flow (one per day, resets at local midnight)
            // Renders via tune-flow.js: loadTuneFlow() checks for today's tuning,
            // shows completed view or starts the guided flow.
            return `<div class="panel-scroll"><div class="loading">Loading...</div></div>`;

        // ── Playlists tab — signal-type echo streams ─────────────────────
        case 'playlists':
            return `<div class="panel-scroll">
                <div class="panel-toolbar">
                    <span class="panel-title">Echo Playlists</span>
                </div>
                <p class="op-playlists-sub">Choose by Signal type. Shuffle and repeat what resonates.</p>
                <div id="playlistStreams" class="op-playlist-grid"><div class="loading">Loading streams...</div></div>
            </div>`;

        // ── Go Deeper tab — framework and philosophy ─────────────────────
        case 'go-deeper':
            return `<div class="panel-scroll">
                <div class="panel-toolbar">
                    <span class="panel-title">Go Deeper</span>
                </div>
                <div class="op-deeper-content">
                    <div class="op-deeper-section">
                        <h3 class="op-deeper-heading">The Lucid Principles</h3>
                        <p class="op-deeper-text">22 principles discovered through music, refined into a framework for conscious living. Each tuning frequency connects to a principle. Each principle carries a practice.</p>
                        <div class="op-deeper-links">
                            <a href="https://lucidprinciples.com/canon" target="_blank" class="op-deeper-link">The Canon &rarr;</a>
                            <a href="https://lucidprinciples.com/music" target="_blank" class="op-deeper-link">The Music &rarr;</a>
                        </div>
                    </div>
                    <div class="op-deeper-section">
                        <h3 class="op-deeper-heading">The Framework</h3>
                        <p class="op-deeper-text">Broadcast Frequency. Signal Type. Tuning Keys. The Love Equation. A complete system for understanding how attention shapes reality.</p>
                        <div class="op-deeper-links">
                            <a href="https://lucidprinciples.com" target="_blank" class="op-deeper-link">Lucid Principles &rarr;</a>
                        </div>
                    </div>
                    <div class="op-deeper-section">
                        <h3 class="op-deeper-heading">Lucid Cove</h3>
                        <p class="op-deeper-text">Your private intelligence. Built on the Lucid Principles framework. Tuning is the daily practice that aligns your broadcast.</p>
                        <div class="op-deeper-links">
                            <a href="https://lucidcove.org" target="_blank" class="op-deeper-link">About Lucid Cove &rarr;</a>
                        </div>
                    </div>
                    <div class="op-deeper-section">
                        <h3 class="op-deeper-heading">How to use this system</h3>
                        <p class="op-deeper-text">Help is the practical doorway — what you and your agent can do day to day. Latest Tuning on Attention keeps the practice front and center. Go Deeper is the framework behind both.</p>
                        <div class="op-deeper-links">
                            <a href="#" class="op-deeper-link" onclick="if(typeof openHelp==='function')openHelp('together'); return false;">Open Help &rarr;</a>
                        </div>
                    </div>
                </div>
            </div>`;

        case 'chat':
            return `<div class="chat-toolbar">
                <div class="chat-toolbar-top">
                    <div class="chat-toolbar-left">
                        <div class="channel-tabs" id="channel-tabs"></div>
                    </div>
                </div>
                <div class="chat-progress-bar" id="chat-progress-bar" onclick="showThreadModal()" title="Thread info — tap for options">
                    <div class="chat-progress-fill" id="chat-progress-fill"></div>
                    <span class="chat-progress-label" id="chat-progress-label">Chat</span>
                    <span class="chat-progress-pct" id="chat-progress-pct"></span>
                </div>
            </div>
            <div class="chat-messages" id="chat-messages">
                <div class="chat-welcome">
                    <div class="welcome-icon" id="welcome-icon"></div>
                    <div class="welcome-text" id="welcome-text">Ready.</div>
                </div>
            </div>
            <div class="activity-live" id="activity-live" style="display:none;"></div>
            <div class="chat-mode-bar">
                <button class="chat-mode-btn active" data-mode="type" id="mode-type">Type</button>
                <button class="chat-mode-btn" data-mode="dictate" id="mode-dictate">Dictate</button>
                <button class="chat-mode-btn" data-mode="voice" id="mode-voice">Voice</button>
            </div>
            <div class="chat-input-area">
                <textarea id="chat-input" placeholder="Type a message..." rows="1"></textarea>
                <div id="voice-partial" class="voice-partial" style="display:none;"></div>
                <button id="chat-mic" class="btn-mic-full" style="display:none;">
                    <svg width="28" height="28" viewBox="0 0 24 24" fill="currentColor"><path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3z"/><path d="M17 11c0 2.76-2.24 5-5 5s-5-2.24-5-5H5c0 3.53 2.61 6.43 6 6.92V21h2v-3.08c3.39-.49 6-3.39 6-6.92h-2z"/></svg>
                    <span id="mic-label">Tap to Talk</span>
                </button>
                <button id="chat-send" class="btn-primary">Send</button>
                <button id="chat-stop" class="btn-stop" style="display:none;">Stop</button>
            </div>`;

        case 'tuning':
            return `<div class="panel-toolbar">
                <span class="panel-title">Tuning</span>
                <button class="btn-sm" onclick="loadTuning()">&#8635;</button>
            </div>
            <div id="tuning-current" class="tuning-current"></div>
            <div id="echo-list" class="echo-list"><div class="loading">Loading echoes...</div></div>`;

        case 'joulework':
            return `<div class="panel-toolbar">
                <span class="panel-title">JouleWork</span>
                <button class="btn-sm" onclick="loadJouleWork()">&#8635;</button>
            </div>
            <div id="jw-content" class="panel-scroll"><div class="loading">Loading metrics...</div></div>`;

        case 'reports':
            return `<div class="panel-toolbar">
                <span class="panel-title">Reports</span>
            </div>
            <div class="sub-tab-bar" id="reports-sub-tabs">
                <button class="sub-tab active" data-sub="tuning" onclick="switchReportsSub('tuning')">Tuning</button>
                <button class="sub-tab" data-sub="joulework" onclick="switchReportsSub('joulework')">JouleWork</button>
            </div>
            <div class="reports-sub-panels">
                <div id="reports-sub-tuning" class="reports-sub active">
                    <div id="tuning-current" class="tuning-current"></div>
                    <div id="echo-list" class="echo-list"><div class="loading">Loading echoes...</div></div>
                </div>
                <div id="reports-sub-joulework" class="reports-sub" style="display:none;">
                    <div id="jw-content" class="panel-scroll"><div class="loading">Loading metrics...</div></div>
                </div>
            </div>`;

        case 'memory':
            return `<div class="panel-toolbar">
                <span class="panel-title">Memory</span>
                <div style="display:flex;gap:4px;align-items:center;">
                    <span id="memReviewBadge" style="font-size:0.68rem;padding:1px 6px;border-radius:8px;background:var(--yellow,#c90);color:#000;display:none;"></span>
                    <button class="btn-sm" onclick="toggleBulkMode()" id="bulkModeBtn" title="Bulk select">☐</button>
                    <button class="btn-sm" onclick="loadMemory()">&#8635;</button>
                </div>
            </div>
            <div id="memory-toolbar" style="padding:6px 12px;border-bottom:1px solid var(--border);display:flex;gap:6px;flex-wrap:wrap;align-items:center;">
                <input type="text" id="memSearchInput" placeholder="Search memories..." style="flex:1;min-width:120px;padding:4px 8px;font-size:0.75rem;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--fg);" oninput="filterMemories()">
                <select id="memViewMode" style="padding:4px 6px;font-size:0.72rem;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--fg);" onchange="filterMemories()">
                    <option value="review">Review Queue</option>
                    <option value="committed">Committed</option>
                    <option value="all">All Memories</option>
                </select>
                <select id="memCatFilter" style="padding:4px 6px;font-size:0.72rem;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--fg);" onchange="filterMemories()">
                    <option value="">All categories</option>
                </select>
                <select id="memSortBy" style="padding:4px 6px;font-size:0.72rem;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--fg);" onchange="sortAndRenderMemories()">
                    <option value="date-desc">Newest first</option>
                    <option value="date-asc">Oldest first</option>
                    <option value="importance-desc">Highest importance</option>
                    <option value="importance-asc">Lowest importance</option>
                    <option value="category">By category</option>
                </select>
            </div>
            <div id="memory-bulk-bar" style="display:none;padding:6px 12px;border-bottom:1px solid var(--border);background:var(--bg-alt);display:none;gap:6px;align-items:center;flex-wrap:wrap;">
                <label style="font-size:0.72rem;color:var(--dim);"><input type="checkbox" id="memSelectAll" onchange="toggleSelectAll(this)"> Select all</label>
                <span id="memSelectedCount" style="font-size:0.72rem;color:var(--fg);">0 selected</span>
                <select id="memBulkCat" style="padding:3px 6px;font-size:0.72rem;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--fg);">
                    <option value="">Move to category...</option>
                </select>
                <button class="btn-sm btn-action" onclick="applyBulkRecategorize()" style="font-size:0.72rem;">Apply</button>
                <button class="btn-sm" onclick="bulkDismissSelected()" style="font-size:0.72rem;color:var(--red);">Dismiss selected</button>
                <button class="btn-sm btn-action" onclick="bulkCommitSelected()" style="font-size:0.72rem;">Commit selected</button>
            </div>
            <div id="memory-content" class="panel-scroll"><div class="loading">Loading memory...</div></div>`;

        case 'affiliates':
            return `<div class="panel-toolbar">
                <span class="panel-title">Affiliates</span>
            </div>
            <div class="panel-scroll">
                <div id="affiliates-content"><div class="loading">Loading affiliate data...</div></div>
            </div>`;

        case 'haven':
            // Cove-admin Haven surface — the Coves in this Haven (read layer:
            // /api/haven/coves). Form/nest/invite controls layer on next.
            return `<div class="panel-toolbar">
                <span class="panel-title">Haven</span>
            </div>
            <div class="panel-scroll">
                <div id="haven-admin"><div class="loading">Loading Haven...</div></div>
            </div>`;

        case 'settings':
            // Unified settings template — progressive sections by tier
            return `<div class="panel-toolbar">
                <span class="panel-title">Settings</span>
            </div>
            <div class="panel-scroll">
                ${!(MC.adminView || MC.coveAdminView) ? (() => {
                  // Tier gating: an Agent + Voice + voice Tools only exist once you
                  // HAVE an agent (Presence/Cove). Cloud Storage starts at Operator.
                  // Tuner/Operator should never see empty agent/voice sections.
                  const _hasAgent = !!(MC.tier && (MC.tier.has_agent || MC.tier.level >= 20));
                  const _hasCloud = !!(MC.tier && MC.tier.level >= 10);
                  return `
                <div class="settings-group">
                    <div class="settings-group-title">Your Profile</div>
                    <div id="settings-profile" class="settings-row-list"></div>
                </div>
                ${_hasAgent ? `
                <div class="settings-group">
                    <div class="settings-group-title">Your Agent</div>
                    <div id="settings-my-model" class="settings-row-list"></div>
                </div>
                <div class="settings-group">
                    <div class="settings-group-title">Voice</div>
                    <div id="settings-voice" class="settings-row-list"></div>
                </div>` : ''}
                <div class="settings-group">
                    <div class="settings-group-title">Tuning</div>
                    <div id="settings-features" class="settings-row-list"></div>
                    <div style="margin-top:16px;border-top:1px solid var(--border);padding-top:12px;">
                        <div id="settings-signal-filter" class="settings-row-list"></div>
                    </div>
                </div>
                ${_hasCloud ? `
                <div class="settings-group" id="settings-cloud-group" style="display:none;">
                    <div class="settings-group-title">Cloud Storage</div>
                    <div id="settings-cloud" class="settings-row-list"></div>
                </div>
                <div class="settings-group" id="settings-connect-group" style="display:none;">
                    <div class="settings-group-title">Connect / Chat</div>
                    <div id="settings-connect" class="settings-row-list"></div>
                </div>` : ''}
                ${_hasAgent ? `
                <div class="settings-group">
                    <div class="settings-group-title">Tools</div>
                    <div id="settings-tools" class="settings-row-list"></div>
                </div>` : ''}
                ${_hasCloud ? `
                <div class="settings-group" id="settings-devices-group">
                    <div class="settings-group-title">Devices &amp; Access</div>
                    <div id="settings-devices" class="settings-row-list"></div>
                </div>` : ''}
                ${(MC.presence && MC.presence.cove_role === 'admin') ? `
                <div class="settings-group" id="settings-cove-admin-group">
                    <div class="settings-group-title">Cove Settings <span style="font-size:0.62rem;color:var(--dim);font-weight:normal;">· admin only</span></div>
                    <div id="settings-cove-admin" class="settings-row-list"></div>
                </div>` : ''}
                <div class="settings-group" id="settings-selfhost-group">
                    <div class="settings-group-title">Move/copy your Cove</div>
                    <div id="settings-selfhost" class="settings-row-list"></div>
                </div>`;
                })() : ''}
                ${MC.adminView ? `
                <div class="settings-group">
                    <div class="settings-group-title">Presences</div>
                    <div id="settings-presences"></div>
                </div>` : ''}
                ${MC.coveAdminView ? `
                <div class="settings-group">
                    <div class="settings-group-title">Cove Address</div>
                    <div id="settings-cove-admin" class="settings-row-list"></div>
                </div>` : ''}
                ${(MC.adminView || MC.coveAdminView) ? `
                <div class="settings-group">
                    <div class="settings-group-title">Intelligence</div>
                    <div id="settings-model" class="settings-row-list"></div>
                </div>
                <div class="settings-group">
                    <div class="settings-group-title">Manual Model</div>
                    <div id="settings-model-override" class="settings-row-list"></div>
                </div>
                <div class="settings-group">
                    <div class="settings-group-title">Model Registry</div>
                    <div id="settings-model-registry" class="settings-row-list"></div>
                </div>
                <div class="settings-group">
                    <div class="settings-group-title">Compute</div>
                    <div id="settings-compute" class="settings-row-list"></div>
                </div>
                <div class="settings-group">
                    <div class="settings-group-title">System</div>
                    <div id="settings-status" class="settings-status-grid"></div>
                </div>
                <div class="settings-group">
                    <div class="settings-group-title">Nextcloud</div>
                    <div id="settings-nc" class="settings-row-list"></div>
                </div>
                <div class="settings-group">
                    <div class="settings-group-title">Connect (Matrix)</div>
                    <div id="settings-matrix-admin" class="settings-row-list"></div>
                </div>
                <div class="settings-group">
                    <div class="settings-group-title">LTP</div>
                    <div id="settings-ltp" class="settings-row-list"></div>
                </div>
                <div class="settings-group">
                    <div class="settings-group-title">Agent Symbol</div>
                    <div id="settings-agent-symbol" class="settings-row-list"></div>
                </div>` : ''}
            </div>`;

        case 'calendar':
            return `<div class="panel-toolbar">
                <span class="panel-title">Calendar</span>
                <div class="toolbar-right">
                    <select id="cal-days" class="select-sm" onchange="loadCalendar()">
                        <option value="7">7 days</option>
                        <option value="14" selected>14 days</option>
                        <option value="30">30 days</option>
                        <option value="90">90 days</option>
                        <option value="365">1 year</option>
                    </select>
                    <button class="btn-sm" onclick="if(typeof showNewEventForm==='function')showNewEventForm();" title="New event">+</button>
                    <button class="btn-sm" onclick="loadCalendar()">&#8635;</button>
                </div>
            </div>
            <div id="cal-event-form" class="cal-form" style="display:none;">
                <div class="cal-form-header">
                    <span id="cef-form-title">New Event</span>
                    <button class="btn-cancel" onclick="hideEventForm()">Cancel</button>
                </div>
                <div class="task-edit-row">
                    <label>Title</label>
                    <input type="text" id="cef-title" class="task-input" placeholder="Event title...">
                </div>
                <div class="task-edit-row task-edit-grid">
                    <div>
                        <label>Date</label>
                        <input type="date" id="cef-date" class="task-input">
                    </div>
                    <div style="display:flex;align-items:flex-end;gap:8px;">
                        <label style="display:flex;align-items:center;gap:6px;font-size:0.75rem;color:var(--dim);cursor:pointer;">
                            <input type="checkbox" id="cef-allday" checked onchange="if(typeof _toggleTimeFields==='function')_toggleTimeFields(this.checked);">
                            All day
                        </label>
                    </div>
                </div>
                <div class="task-edit-row task-edit-grid" id="cef-time-row" style="display:none;">
                    <div>
                        <label>Start time</label>
                        <input type="time" id="cef-time" class="task-input">
                    </div>
                    <div>
                        <label>End time</label>
                        <input type="time" id="cef-end-time" class="task-input">
                    </div>
                </div>
                <div class="task-edit-row">
                    <label>Location</label>
                    <input type="text" id="cef-location" class="task-input" placeholder="Optional...">
                </div>
                <div class="task-edit-row">
                    <label>Description</label>
                    <textarea id="cef-description" class="task-input task-textarea" rows="2" placeholder="Optional..."></textarea>
                </div>
                <div class="task-edit-row task-edit-grid">
                    <div>
                        <label>Project</label>
                        <select id="cef-project" class="task-input"><option value="">None</option></select>
                    </div>
                    <div>
                        <label>Task</label>
                        <select id="cef-task" class="task-input"><option value="">None</option></select>
                    </div>
                </div>
                <div class="task-edit-actions">
                    <button class="btn-save" onclick="if(typeof saveCalEvent==='function')saveCalEvent();">Save</button>
                    <button id="cef-delete-btn" class="btn-danger" onclick="if(typeof deleteCalEvent==='function')deleteCalEvent();" style="display:none;">Delete</button>
                </div>
            </div>
            <div id="calendar-events" class="event-list"><div class="loading">Loading calendar...</div></div>`;

        case 'files':
            return `<div class="panel-toolbar">
                <div class="breadcrumb" id="file-breadcrumb">
                    <span class="breadcrumb-item active" data-path="/">Home</span>
                </div>
                <div class="toolbar-right" id="file-toolbar">
                    <button class="btn-sm" onclick="loadFiles(currentFilePath)">&#8635;</button>
                </div>
            </div>
            <div id="file-list" class="file-list"><div class="loading">Loading files...</div></div>`;

        case 'tasks':
            return `<div class="panel-toolbar">
                <span class="panel-title">Tasks</span>
                <div class="toolbar-right">
                    <select id="task-filter" class="select-sm" onchange="loadTasks()">
                        <option value="pending">Pending</option>
                        <option value="all">All</option>
                        <option value="done">Done</option>
                    </select>
                    <button class="btn-primary btn-sm" onclick="showAddTask()">+ Task</button>
                </div>
            </div>
            <div id="add-task-form" class="add-form hidden">
                <input id="new-task-title" placeholder="Task title..." class="form-input">
                <select id="new-task-priority" class="select-sm">
                    <option value="normal">Normal</option>
                    <option value="high">High</option>
                    <option value="urgent">Urgent</option>
                    <option value="low">Low</option>
                </select>
                <input id="new-task-due" type="date" class="form-input">
                <div class="form-actions">
                    <button class="btn-primary btn-sm" onclick="createTask()">Add</button>
                    <button class="btn-sm" onclick="hideAddTask()">Cancel</button>
                </div>
            </div>
            <div id="task-list" class="task-list"><div class="loading">Loading tasks...</div></div>`;

        // ── Steward / manager panels ────────────────────────────────
        // Only rendered when agent.yaml includes these tabs.
        // Atlas (personal tier) never triggers these cases.

        case 'team':
            return `<div class="panel-scroll">
                <div class="panel-header">
                    <div class="panel-header-left">
                        <span class="section-label" id="familyLabel">Family</span>
                        <span id="teamSummary" class="section-meta"></span>
                    </div>
                    <span id="teamLegend" class="section-meta"></span>
                </div>
                <div id="teamRoster"><span class="empty">Loading team...</span></div>
            </div>`;

        case 'projects':
            return `<div class="panel-scroll">
                <div class="section-header-row">
                    <span class="section-label">Projects</span>
                    <div class="home-header-actions">
                        <button class="btn-icon-add" onclick="toggleProjAddForm()" title="New project">+</button>
                    </div>
                </div>
                <div id="proj-tab-add-form" style="display:none;" class="quick-add-form">
                    <input type="text" id="newProjectName" class="task-input" placeholder="Project name...">
                    <div class="task-edit-actions">
                        <button class="btn-small btn-save" onclick="createProjectFromTab()">Create</button>
                        <button class="btn-small btn-cancel" onclick="document.getElementById('proj-tab-add-form').style.display='none'">Cancel</button>
                    </div>
                </div>
                <div id="projectsTabList"><span class="empty">Loading projects...</span></div>
            </div>`;

        case 'system':
            return `<div class="panel-scroll">
                <div id="dryRunBanner"></div>
                <div class="grid-2">
                    <div class="card span-2"><h2>Server Hardware <button class="btn btn-sm" onclick="loadHardwareMetrics()">Refresh</button></h2><div id="hardwareMetrics"><span class="empty">Loading...</span></div></div>
                    <div class="card span-2"><h2>Ops Runbooks <button class="btn btn-sm" onclick="loadRunbooks()">Refresh</button></h2><div id="runbooksList"><span class="empty">Loading...</span></div></div>
                    <div class="card"><h2>Model Chain Health</h2><div id="healthList"><span class="empty">Loading...</span></div></div>
                    <div class="card"><h2>Scheduler</h2><div id="schedulerInfo"><span class="empty">Loading...</span></div></div>
                    <div class="card"><h2>Memory Pipelines</h2><div id="memoryPipelines"><span class="empty">Loading...</span></div></div>
                    <div class="card"><h2>Database Stats</h2><div id="dbStats"><span class="empty">Loading...</span></div></div>
                    <div class="card"><h2>Configuration</h2><div id="configInfo"><span class="empty">Loading...</span></div></div>
                    <div class="card span-2">
                        <h2>Live Logs <span id="logStreamStatus" class="log-status">disconnected</span></h2>
                        <div class="log-filter-bar">
                            <input type="text" id="logFilter" placeholder="Filter logs..." class="form-input">
                            <select id="logDatePicker" class="form-input"><option value="live">Live (today)</option></select>
                            <button class="btn btn-action" onclick="toggleLogStream()" id="logStreamBtn">Connect</button>
                            <button class="btn" onclick="cancelArchiveDigestion()" id="logStopPipelineBtn" style="background:var(--red);color:#fff;margin-left:4px;" title="Stop running pipeline">Stop Pipeline</button>
                        </div>
                        <div id="logOutput" class="log-output"></div>
                    </div>
                    <div class="card span-2">
                        <h2>LP Color System</h2>
                        <div id="lpColorLegend"></div>
                    </div>
                </div>
            </div>`;

        // ── Action Board panels ──────────────────────────────────────
        case 'ab-actions':
            return `<div class="panel-scroll ab-content">
                <div class="ab-header" style="display:flex;align-items:baseline;gap:10px;">
                    <h2>Actions</h2>
                    <p class="ab-subtitle" style="flex:1;">Pending items that need your input</p>
                    <button onclick="_abActionsLoaded=false;loadABActions()" style="background:none;border:1px solid var(--border);color:var(--dim);padding:3px 10px;border-radius:5px;font-size:0.7rem;cursor:pointer;" title="Refresh">↻</button>
                </div>
                <div id="ab-actions-list" class="ab-actions-list">
                    <div class="loading">Loading actions...</div>
                </div>
            </div>`;

        case 'ab-links':
            return `<div class="panel-scroll ab-content">
                <div class="ab-header">
                    <h2>Links</h2>
                    <p class="ab-subtitle">Quick access to tools and services</p>
                </div>
                <div id="ab-links-grid" class="ab-links-container">
                    <div class="loading">Loading links...</div>
                </div>
            </div>`;

        case 'ab-flows': {
            // The guided "describe what you want" creation surface is agent-driven, so
            // show it only for agent-bearing tiers (Cove). Operators + Tuners get the
            // capability cards + filter chips only.
            const _hasAgent = (typeof MC !== 'undefined') && MC.tier && (MC.tier.has_agent || (MC.tier.level || 0) >= 20);
            if (!_hasAgent) {
                return `<div class="panel-scroll ab-content">
                    <div class="ab-header">
                        <h2>Flows</h2>
                        <p class="ab-subtitle">Guided journeys and buildable products</p>
                    </div>
                    <div id="ab-flows-list" class="ab-flows-list">
                        <div class="loading">Loading...</div>
                    </div>
                </div>`;
            }
            return `<div class="panel-scroll ab-content">
                <div class="ab-flows-intention">
                    <h2>Everything that exists started as an idea.</h2>
                    <p>What do you want to create?</p>
                </div>
                <div class="ab-flows-guide">
                    <textarea id="ab-flows-input" class="ab-flows-input" rows="2" placeholder="Describe what you want to create or build — I'll help you figure out where to start..."></textarea>
                    <button class="ab-flows-go" id="ab-flows-go-btn" onclick="startGuidedFlow()">Go</button>
                </div>
                <div id="ab-creations-list" class="ab-creations-list"></div>
                <div id="ab-flows-list" class="ab-flows-list">
                    <div class="loading">Loading flows...</div>
                </div>
            </div>`;
        }

        case 'ab-tools':
            return `<div class="panel-scroll ab-content">
                <div class="ab-header">
                    <h2>Tools</h2>
                    <p class="ab-subtitle">Your team's stations</p>
                </div>
                <div id="ab-tools-list" class="ab-tools-list">
                    <div class="loading">Loading tools...</div>
                </div>
            </div>`;

        default:
            return `<div class="panel-scroll"><div class="loading">Loading ${ESC(tabId)}...</div></div>`;
    }
}

// Detail overlay panels — injected once at boot for tabs that need drill-down views.
// Only created if the relevant tab exists in config.
function _buildDetailPanels() {
    const container = document.getElementById('panel-container');
    if (!container) return;

    // Agent detail (for team tab)
    if (MC.tabs.some(t => (t.id || t) === 'team')) {
        const agp = document.createElement('section');
        agp.id = 'panel-agent-detail';
        agp.className = 'panel';
        agp.style.display = 'none';
        agp.innerHTML = `<div class="panel-scroll">
            <button class="back-btn" onclick="backToOverview()">← Team</button>
            <div id="agp-hero" class="agp-hero-section" style="display:none;"></div>
            <div class="agp-header" id="agp-header-legacy" style="position:relative;display:none;">
                <img class="agp-avatar" id="agp-avatar" src="" alt="" onerror="this.style.display='none'" style="display:none;">
                <div class="agp-header-info">
                    <div class="detail-name-row">
                        <span class="agp-name" id="agp-name"></span>
                        <span class="agp-model" id="agp-model"></span>
                    </div>
                    <div class="agp-role" id="agp-role"></div>
                </div>
                <span id="agp-badge" style="position:absolute;top:0;right:0;"></span>
            </div>
            <div id="agp-persona" class="detail-persona" style="display:none;"></div>
            <div class="grid-2">
                <div class="card"><h2>Agent Stats</h2><div id="agp-stats"><span class="empty">Loading...</span></div></div>
                <div class="card"><h2>JouleWork</h2><div id="agp-jw"><span class="empty">Loading...</span></div></div>
            </div>
            <div class="card"><h2>Boundaries</h2><div id="agp-boundaries"><span class="empty">Loading...</span></div></div>
            <div class="card"><h2>Recent Echoes</h2><div id="agp-echoes"><span class="empty">Loading...</span></div></div>
            <div class="card"><h2>Tools</h2><div id="agp-tools"><span class="empty">Loading...</span></div></div>
            <div class="card"><h2>Channels</h2><div id="agp-channels"><span class="empty">Loading...</span></div></div>
            <div class="card"><h2>Pending Tasks</h2><div id="agp-tasks"><span class="empty">Loading...</span></div></div>
            <div class="card"><h2>Activity</h2><div id="agp-activity"><span class="empty">Loading...</span></div></div>
        </div>`;
        container.appendChild(agp);

        // Family member detail
        const hmp = document.createElement('section');
        hmp.id = 'panel-family-detail';
        hmp.className = 'panel';
        hmp.style.display = 'none';
        hmp.innerHTML = `<div class="panel-scroll">
            <button class="back-btn" onclick="backToOverview()">← Team</button>
            <div class="agp-header">
                <img class="agp-avatar" id="hmp-avatar" src="" alt="" onerror="this.style.display='none'" style="display:none;">
                <div class="agp-header-info">
                    <span class="agp-name" id="hmp-name"></span>
                    <div class="agp-role" id="hmp-role"></div>
                </div>
            </div>
            <div id="hmp-focus" class="detail-persona"></div>
            <div id="hmp-member-section" class="grid-2">
                <div class="card"><h2>Personal Agent</h2><div id="hmp-agent"><span class="empty">Loading...</span></div></div>
                <div class="card"><h2>Pending Tasks</h2><div id="hmp-tasks"><span class="empty">Loading...</span></div></div>
            </div>
            <div id="hmp-agent-only-section" style="display:none;">
                <div class="card"><h2>Agent Info</h2><div id="hmp-agent-only"><span class="empty">Loading...</span></div></div>
            </div>
        </div>`;
        container.appendChild(hmp);
    }

    // Tuning Hub — the full tuning experience behind the badge
    // Structure: Today's Tuning (hero) → Player → Echo History → Love Equation
    // Each section fills in as features are built. The hub IS the badge destination.
    if (true) {
        const otp = document.createElement('section');
        otp.id = 'panel-operator-tuning';
        otp.className = 'panel';
        otp.style.display = 'none';
        otp.innerHTML = `<div class="panel-scroll ot-scroll">
            <button class="back-btn" onclick="backFromTuning()">&larr; Return</button>

            <!-- ═══ Hub Header ═══ -->
            <div class="th-header" id="thHeader">
                <img class="th-lt-avatar" src="/static/avatars/lt.png" alt="LT"
                     onerror="this.style.display='none'">
                ${MC.isTuner ? `
                    <div class="th-title">Today's Tuning</div>
                ` : `
                    <div class="th-title">Tuning Hub</div>
                    <div class="th-subtitle" id="thSubtitle">LT Orchestrated Tuning</div>
                `}
            </div>

            <!-- ═══ Section 1: Today's Tuning (hero — already functional) ═══ -->
            <div id="otLoading" style="text-align:center;padding:3rem;color:var(--dim);">Loading tuning...</div>
            <div id="otContent" style="display:none;">
                <div class="tuning-header">
                    <div class="tuning-date" id="otDate"></div>
                    <div class="tuning-alignment" id="otAlignment"></div>
                    <div class="tuning-frequency" id="otFrequency"></div>
                    <div class="tuning-principle-title" id="otPrinciple"></div>
                </div>
                <div class="ot-card" id="otCoachBlock">
                    <div class="ot-label ot-coach-label" style="display:none"></div>
                    <div class="ot-text" id="otCoachText"></div>
                </div>
                <div class="ot-practice" id="otPracticeBlock" style="display:none;">
                    <div class="ot-label ot-practice-label">Practice &mdash; <span id="otPracticeTemplate"></span></div>
                    <div class="ot-practice-steps">
                        <div class="ot-step" id="otStep1">
                            <div class="ot-step-num">1</div>
                            <div class="ot-step-body">
                                <div class="ot-step-title" id="otStep1Title"></div>
                                <div class="ot-step-text" id="otStep1Text"></div>
                            </div>
                        </div>
                        <div class="ot-step" id="otStep2">
                            <div class="ot-step-num">2</div>
                            <div class="ot-step-body">
                                <div class="ot-step-title" id="otStep2Title"></div>
                                <div class="ot-step-text" id="otStep2Text"></div>
                            </div>
                        </div>
                        <div class="ot-step" id="otStep3">
                            <div class="ot-step-num">3</div>
                            <div class="ot-step-body">
                                <div class="ot-step-title" id="otStep3Title"></div>
                                <div class="ot-step-text" id="otStep3Text"></div>
                            </div>
                        </div>
                    </div>
                </div>
                <div class="ot-card ot-key" id="otKeyBlock">
                    <div class="ot-label">Tuning Key</div>
                    <div class="ot-text italic" id="otKeyText"></div>
                </div>

                <!-- ═══ Section 2: Player (rendered by otRenderPlayer) ═══ -->
                <div class="ot-stream-label" id="otStreamLabel"></div>
                <div id="otPlayerMount"></div>

                ${MC.isTuner ? '' : `
                <!-- ═══ Recent Tunings — public LT Drop archive (Operator+ only) ═══ -->
                <div class="th-section" id="thHistory" style="display:none;">
                    <div class="th-section-header">
                        <span class="th-section-title">Recent Tunings</span>
                    </div>
                    <div class="th-section-body" id="otRecentDrops"></div>
                </div>

                <!-- ═══ Love Equation ═══ -->
                <div class="ot-card ot-eq" id="otEqBar">
                    <div class="ot-eq-bar">
                        <span class="ot-label">Love Equation</span>
                        <span class="ot-eq-val" id="otEqVal"></span>
                        <span class="ot-eq-detail" id="otEqDetail"></span>
                    </div>
                </div>
                `}
            </div>
        </div>`;
        container.appendChild(otp);
    }

    // Mini player bar (persistent bottom bar when audio is playing)
    // Built for both Operator (Tune tab) and Cove/Presence (overlay) tiers
    if (MC.tabs.some(t => ['home','reports','tune'].includes(t.id || t))) {
        const mp = document.createElement('div');
        mp.className = 'mini-player';
        mp.id = 'miniPlayer';
        mp.onclick = function() {
            // Navigate to whichever panel is currently playing
            if (typeof _otSource !== 'undefined' && _otSource === 'tune') {
                switchToTab('tune');
            } else if (typeof _otSource !== 'undefined' && _otSource === 'playlist') {
                switchToTab('playlists');
            } else if (typeof openOperatorTuning === 'function') {
                openOperatorTuning();
            }
        };
        mp.innerHTML = `<div class="mp-progress" id="mpProgress"></div>
            <div class="mp-info">
                <div class="mp-title" id="mpTitle">--</div>
                <div class="mp-freq" id="mpFreq"></div>
            </div>
            <button class="mp-btn" onclick="event.stopPropagation(); otPrev();" title="Previous">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M6 6h2v12H6zm3.5 6l8.5 6V6z"/></svg>
            </button>
            <button class="mp-btn mp-play" id="mpPlayBtn" onclick="event.stopPropagation(); otTogglePlay();" title="Play/Pause">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" id="mpPlayIcon"><polygon points="5,3 19,12 5,21"/></svg>
            </button>
            <button class="mp-btn" onclick="event.stopPropagation(); otNext();" title="Next">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M6 18l8.5-6L6 6v12zM16 6v12h2V6h-2z"/></svg>
            </button>`;
        document.body.appendChild(mp);
    }

    // Project detail (for projects tab)
    if (MC.tabs.some(t => (t.id || t) === 'projects')) {
        const pdp = document.createElement('section');
        pdp.id = 'panel-project-detail';
        pdp.className = 'panel';
        pdp.style.display = 'none';
        pdp.innerHTML = `<div class="panel-scroll">
            <button class="back-btn" onclick="backToProjects()">← Projects</button>
            <div class="pdp-header">
                <div class="pdp-header-top">
                    <span id="pdp-status"></span>
                    <span class="pdp-title" id="pdp-name"></span>
                </div>
                <div class="pdp-header-desc" id="pdp-desc"></div>
                <div class="pdp-header-meta">
                    <span id="pdp-owner"></span>
                    <span style="color:var(--dim);" id="pdp-created"></span>
                </div>
            </div>
            <div class="card" id="pdp-goals-card"><h2>Goals</h2><div id="pdp-goals"><span class="empty">No goals set</span></div></div>
            <div class="card">
                <div class="pdp-tasks-header">
                    <h2>Tasks <span id="pdp-task-count" class="pdp-task-fraction"></span></h2>
                    <div class="home-header-actions">
                        <button class="btn-icon-info" onclick="toggleProjLegend()" title="Legend">i</button>
                    </div>
                </div>
                <div id="proj-legend" class="task-legend" style="display:none;">
                    <div class="legend-row"><span class="pri-dot pri-urgent"></span> Urgent</div>
                    <div class="legend-row"><span class="pri-dot pri-high"></span> High</div>
                    <div class="legend-row"><span class="pri-dot pri-normal"></span> Normal</div>
                    <div class="legend-row"><span class="pri-dot pri-low"></span> Low</div>
                    <div class="legend-divider"></div>
                    <div class="legend-row"><span class="due-overdue">May 1</span> Overdue</div>
                    <div class="legend-row"><span class="due-soon">Today</span> Due soon</div>
                    <div class="legend-row"><span class="due-later">Jun 15</span> Later</div>
                </div>
                <div class="progress-bar-track" style="margin-bottom:10px;"><div class="progress-bar-fill" id="pdp-progress" style="width:0%"></div></div>
                <div id="pdp-tasks"><span class="empty">Loading...</span></div>
                <div class="pdp-add-task-row" style="margin-top:10px;">
                    <input type="text" id="pdp-new-task" placeholder="New task..." class="task-input" style="flex:1;">
                    <select id="pdp-new-priority" class="task-select">
                        <option value="normal">Normal</option><option value="urgent">Urgent</option>
                        <option value="high">High</option><option value="low">Low</option>
                    </select>
                    <select id="pdp-new-assignee" class="task-select"></select>
                    <button class="btn-small btn-save" onclick="addTaskFromDetail()">Add</button>
                </div>
            </div>
            <div class="card"><h2>Events & Deadlines</h2>
                <div id="pdp-events"><span class="empty">Loading...</span></div>
            </div>
            <div class="card"><h2>Notes</h2>
                <div id="pdp-comments"><span class="empty">No notes yet</span></div>
                <div class="pdp-add-task-row" style="margin-top:10px;">
                    <input type="text" id="pdp-new-comment" placeholder="Add a note..." class="task-input" style="flex:1;">
                    <button class="btn-small btn-save" onclick="addCommentFromDetail()">Add</button>
                </div>
            </div>
            <div class="card" id="pdp-team-card"><h2>Team</h2><div id="pdp-team"><span class="empty">No team assigned</span></div></div>
        </div>`;
        container.appendChild(pdp);
    }

    // Task detail panel (drills down from project detail task list)
    if (MC.tabs.some(t => (t.id || t) === 'projects')) {
        const tdp = document.createElement('section');
        tdp.id = 'panel-task-detail';
        tdp.className = 'panel';
        tdp.style.display = 'none';
        tdp.innerHTML = `<div class="panel-scroll">
            <div class="tdp-breadcrumb" id="tdp-breadcrumb"></div>
            <div class="tdp-header">
                <div class="tdp-header-top">
                    <span class="tdp-status-badge" id="tdp-status"></span>
                    <span class="tdp-workflow-badge" id="tdp-workflow" style="display:none;"></span>
                </div>
                <div class="tdp-title" id="tdp-title"></div>
                <div class="tdp-header-meta">
                    <span id="tdp-assignee"></span>
                    <span id="tdp-priority"></span>
                    <span id="tdp-due"></span>
                </div>
            </div>
            <div class="tdp-desc-row" id="tdp-desc-row" style="display:none;">
                <div class="tdp-desc" id="tdp-desc"></div>
            </div>
            <div class="tdp-notes-row" id="tdp-notes-row" style="display:none;">
                <div class="tdp-notes-label">Notes</div>
                <div class="tdp-notes" id="tdp-notes"></div>
            </div>
            <div class="tdp-edit-toggle"><button class="btn-small btn-save" onclick="toggleTaskDetailEdit()">Edit</button></div>
            <div id="tdp-edit-form" style="display:none;">
                <div class="task-edit-row"><label>Title</label><input type="text" id="tdp-e-title" class="task-input"></div>
                <div class="task-edit-row task-edit-grid">
                    <div><label>Status</label>
                        <select id="tdp-e-status" class="task-select">
                            ${['pending','in_progress','blocked','review','done','cancelled'].map(s =>
                                '<option value="'+s+'">'+s.replace('_',' ')+'</option>'
                            ).join('')}
                        </select>
                    </div>
                    <div><label>Priority</label>
                        <select id="tdp-e-priority" class="task-select">
                            ${['urgent','high','normal','low'].map(p =>
                                '<option value="'+p+'">'+p+'</option>'
                            ).join('')}
                        </select>
                    </div>
                </div>
                <div class="task-edit-row task-edit-grid">
                    <div><label>Workflow Pattern</label><input type="text" id="tdp-e-wfpattern" class="task-input" placeholder="e.g. build-review-deploy"></div>
                    <div><label>Workflow State</label><input type="text" id="tdp-e-wfstate" class="task-input" placeholder="e.g. building"></div>
                </div>
                <div class="task-edit-row task-edit-grid">
                    <div id="tdp-e-assignee-wrap"><label>Assignee</label></div>
                    <div><label>Due date</label><input type="date" id="tdp-e-due" class="task-input"></div>
                </div>
                <div class="task-edit-row"><label>Description</label><textarea id="tdp-e-desc" class="task-input task-textarea" rows="2"></textarea></div>
                <div class="task-edit-row"><label>Notes</label><textarea id="tdp-e-notes" class="task-input task-textarea" rows="2"></textarea></div>
                <div class="task-edit-actions">
                    <button class="btn-small btn-save" onclick="saveTaskDetail()">Save</button>
                    <button class="btn-small btn-cancel" onclick="toggleTaskDetailEdit()">Cancel</button>
                </div>
            </div>
            <div class="card" id="tdp-subtasks-card">
                <h2>Sub-tasks <span id="tdp-subtask-count" class="pdp-task-fraction"></span></h2>
                <div id="tdp-subtasks"><span class="empty">No sub-tasks</span></div>
                <div class="pdp-add-task-row" style="margin-top:10px;">
                    <input type="text" id="tdp-new-subtask" placeholder="Add sub-task..." class="task-input" style="flex:1;">
                    <select id="tdp-new-sub-priority" class="task-select">
                        <option value="normal">Normal</option><option value="urgent">Urgent</option>
                        <option value="high">High</option><option value="low">Low</option>
                    </select>
                    <button class="btn-small btn-save" onclick="addSubtaskFromDetail()">Add</button>
                </div>
            </div>
            <div class="card" id="tdp-activity-card">
                <h2>Activity</h2>
                <div id="tdp-activity"><span class="empty">No activity yet</span></div>
                <div class="pdp-add-task-row" style="margin-top:10px;">
                    <input type="text" id="tdp-new-comment" placeholder="Add a comment..." class="task-input" style="flex:1;">
                    <button class="btn-small btn-save" onclick="addTaskCommentFromDetail()">Add</button>
                </div>
            </div>
        </div>`;
        container.appendChild(tdp);
    }
}
