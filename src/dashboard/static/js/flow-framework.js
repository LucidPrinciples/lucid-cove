// =============================================================================
// Flow Framework — shared infrastructure for all Creation Flow pages
// =============================================================================
// Include this in any flow HTML page to connect it to the Creation Framework.
// Provides: silent frequency extraction, color theming, stage indicator,
// stage transitions, action spawning, signs logging.
//
// Usage in a flow page:
//   <script src="/static/js/flow-framework.js?v=3"></script>
//   <script>
//     FlowFramework.init({
//       flowName: 'New Cove Setup',
//       stageMap: { 1: 'tune', 3: 'act', 7: 'manifest' },
//       totalSteps: 8,
//       onReady: (creation) => { /* creation loaded or null */ }
//     });
//
//     // After step 1-2, once you have user context:
//     const creation = await FlowFramework.extractFromContext(userText);
//     // creation record built silently, frequency color applied
//   </script>
//
// The framework does NOT show any UI of its own. Flow pages handle their
// own step-by-step presentation. The framework provides the creation
// record, stage tracking, and frequency theming as a background layer.
// =============================================================================

const FlowFramework = (() => {

    // ── Inject CSS (stage bar only) ───────────────────────────────────────
    const _css = document.createElement('style');
    _css.textContent = `
        .ff-stage-bar {
            margin-bottom: 16px; padding: 10px 14px;
            background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.06);
            border-radius: 8px;
        }
        .ff-stage-info { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
        .ff-creation-title {
            font-size: 11px; font-weight: 600; color: #d8d8e0;
            overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
        }
        .ff-freq-badge {
            font-size: 10px; font-weight: 700; text-transform: uppercase;
            letter-spacing: 0.05em; opacity: 0.8;
        }
        .ff-stage-dots { display: flex; align-items: center; gap: 0; }
        .ff-stage-dot {
            display: inline-flex; align-items: center; justify-content: center;
            width: 28px; height: 18px; border-radius: 3px;
            font-size: 0.55rem; font-weight: 700; letter-spacing: 0.02em;
            background: #1a1d27; color: #555;
            border: 1px solid #2a2d3a; transition: all 0.2s;
        }
        .ff-stage-dot.done { background: #4caf50; color: #0a0a0f; border-color: #4caf50; }
        .ff-stage-dot.active {
            background: var(--daily-freq, #5ce1e6); color: #0a0a0f;
            border-color: var(--daily-freq, #5ce1e6);
            box-shadow: 0 0 8px var(--daily-freq-glow, rgba(92,225,230,0.4));
        }
        .ff-stage-line { width: 10px; height: 1px; background: #2a2d3a; flex-shrink: 0; }
        .ff-stage-line.done { background: #4caf50; }

        .ff-loading {
            display: flex; align-items: center; justify-content: center;
            padding: 24px 16px; margin: 12px 0;
        }
        .ff-loading-text {
            font-size: 14px; font-weight: 500; letter-spacing: 0.02em;
            color: var(--flow-freq, var(--daily-freq, #5ce1e6));
            transition: opacity 0.25s ease;
        }
    `;
    document.head.appendChild(_css);

    // ── State ──────────────────────────────────────────────────────────────
    let _creationId = null;
    let _creation = null;
    let _config = null;
    let _currentStage = null;
    let _initialized = false;
    let _extracting = false;

    const STAGES = ['broadcast', 'tune', 'act', 'receive', 'manifest', 'complete'];
    const STAGE_LABELS = { broadcast: 'BC', tune: 'TN', act: 'AC', receive: 'RC', manifest: 'MF', complete: 'DN' };

    const FREQ_COLORS = {
        peace: '#7ec8e3', clarity: '#5ce1e6', momentum: '#f7a135',
        trust: '#8bc48a', joy: '#ffd166', connection: '#e88db6',
        presence: '#7b7394', resilience: '#d4a843', courage: '#e57373',
        gratitude: '#e8b830', release: '#90a4ae', integration: '#a1887f',
        boundary: '#ff8a65',
    };

    const FREQ_LIST = Object.keys(FREQ_COLORS);

    // ── Init ───────────────────────────────────────────────────────────────
    function init(config) {
        if (_initialized) return;
        _config = config || {};

        const params = new URLSearchParams(window.location.search);
        _creationId = params.get('creation_id');

        if (_creationId) {
            _loadCreation();
        } else {
            // No creation_id — flow page shows its own content immediately.
            // Creation record will be built later via extractFromContext().
            if (_config.onReady) _config.onReady(null);
        }

        _initialized = true;
    }

    // ── Load existing creation ─────────────────────────────────────────────
    async function _loadCreation() {
        try {
            const res = await fetch(`/api/creation/actions/${_creationId}`);
            if (!res.ok) throw new Error(`${res.status}`);
            _creation = await res.json();
            _currentStage = _creation.stage;

            _applyFrequencyTheme(_creation.frequency);
            _renderStageBar(_creation.stage);

            if (_creation.stage === 'broadcast') {
                await advanceStage('tune');
            }

            if (_config.onReady) _config.onReady(_creation);
            if (_config.onCreationLoaded) _config.onCreationLoaded(_creation);
        } catch (e) {
            console.warn('Flow Framework: could not load creation', _creationId, e);
            _renderStageBar(null);
            if (_config.onReady) _config.onReady(null);
        }
    }

    // ── Silent extraction ──────────────────────────────────────────────────
    // Called by flow pages after step 1-2 with the user's text responses.
    // Silently determines frequency, tuning key, and intention from context.
    // Creates the creation record, applies frequency color, renders stage bar.
    // Returns the creation object (or null on failure).

    async function extractFromContext(userText, title) {
        if (_creationId || _extracting) return _creation;
        _extracting = true;

        const flowName = title || _config.flowName || 'Creation';

        const extractPrompt = `Based on what this person shared about their creation, extract the following as JSON.

What they said:
"${userText}"

Context: They are working on "${flowName}".

Choose the single best broadcast frequency from: ${FREQ_LIST.join(', ')}. Pick the one that matches the state of mind this creation calls for.

Choose a tuning key: a short grounding phrase (one sentence) that anchors their intention. Not a quote, just a clear phrase capturing the essence.

Distill their intention into one clean sentence.

Return ONLY valid JSON, no markdown, no code fences:
{"title": "short name for this creation", "intention": "one sentence intention", "frequency": "one frequency", "tuning_key": "grounding phrase"}`;

        try {
            const res = await fetch('/api/flow/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    system_prompt: 'You extract structured data from user input. Return only valid JSON.',
                    messages: [{ role: 'user', content: extractPrompt }],
                    model_id: 'kimi-k2.5-openrouter',
                    temperature: 0.2,
                }),
            });

            if (!res.ok) throw new Error(`${res.status}`);
            const data = await res.json();
            if (!data.response) throw new Error('Empty response');

            let extracted;
            try {
                let raw = data.response.trim();
                if (raw.startsWith('```')) raw = raw.replace(/^```(?:json)?\n?/, '').replace(/\n?```$/, '');
                extracted = JSON.parse(raw);
            } catch (pe) {
                console.warn('Flow Framework: JSON parse error, using fallback', pe);
                extracted = {
                    title: flowName,
                    intention: userText.slice(0, 200),
                    frequency: 'clarity',
                    tuning_key: '',
                };
            }

            if (!FREQ_LIST.includes(extracted.frequency)) extracted.frequency = 'clarity';

            // Create the creation record
            const createRes = await fetch('/api/creation/actions', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    title: extracted.title || flowName,
                    intention: extracted.intention,
                    frequency: extracted.frequency,
                    tuning_key: extracted.tuning_key,
                }),
            });

            if (!createRes.ok) throw new Error(`Create failed: ${createRes.status}`);
            const createData = await createRes.json();
            _creationId = createData.id;

            // Apply frequency color
            _applyFrequencyTheme(extracted.frequency);

            // Load full record and advance past broadcast
            const detailRes = await fetch(`/api/creation/actions/${_creationId}`);
            if (detailRes.ok) {
                _creation = await detailRes.json();
                _currentStage = _creation.stage;
            }

            await advanceStage('tune');
            _renderStageBar('tune');

            // Update URL with creation_id (no reload)
            const url = new URL(window.location);
            url.searchParams.set('creation_id', _creationId);
            history.replaceState(null, '', url);

            _extracting = false;
            return _creation;

        } catch (e) {
            console.error('Flow Framework: extraction failed', e);
            _extracting = false;
            return null;
        }
    }

    // ── Frequency color theming ────────────────────────────────────────────
    function _applyFrequencyTheme(frequency) {
        if (!frequency || !FREQ_COLORS[frequency]) return;

        const color = FREQ_COLORS[frequency];
        const root = document.documentElement;

        root.style.setProperty('--flow-freq', color);
        root.style.setProperty('--flow-freq-glow', _hexToGlow(color, 0.4));
        root.style.setProperty('--flow-freq-subtle', _hexToGlow(color, 0.13));
        root.style.setProperty('--flow-freq-border', _hexToGlow(color, 0.21));

        root.style.setProperty('--daily-freq', color);
        root.style.setProperty('--daily-freq-glow', _hexToGlow(color, 0.4));
        root.style.setProperty('--daily-freq-subtle', _hexToGlow(color, 0.13));
        root.style.setProperty('--daily-freq-border', _hexToGlow(color, 0.21));
    }

    function _hexToGlow(hex, alpha) {
        const r = parseInt(hex.slice(1, 3), 16);
        const g = parseInt(hex.slice(3, 5), 16);
        const b = parseInt(hex.slice(5, 7), 16);
        return `rgba(${r}, ${g}, ${b}, ${alpha})`;
    }

    // ── Stage indicator bar ────────────────────────────────────────────────
    function _renderStageBar(currentStage) {
        const existing = document.getElementById('ff-stage-bar');
        if (existing) existing.remove();

        const bar = document.createElement('div');
        bar.id = 'ff-stage-bar';
        bar.className = 'ff-stage-bar';

        const displayStages = STAGES.filter(s => s !== 'complete');
        const currentIdx = currentStage ? STAGES.indexOf(currentStage) : -1;

        let html = '';
        displayStages.forEach((stage, i) => {
            const stageIdx = STAGES.indexOf(stage);
            let cls = 'ff-stage-dot';
            if (stageIdx < currentIdx) cls += ' done';
            else if (stageIdx === currentIdx) cls += ' active';

            html += `<span class="${cls}">${STAGE_LABELS[stage]}</span>`;
            if (i < displayStages.length - 1) {
                const lineCls = stageIdx < currentIdx ? 'ff-stage-line done' : 'ff-stage-line';
                html += `<span class="${lineCls}"></span>`;
            }
        });

        if (_creation) {
            const freqBadge = _creation.frequency
                ? `<span class="ff-freq-badge" style="color: ${FREQ_COLORS[_creation.frequency] || 'inherit'}">${_creation.frequency}</span>`
                : '';
            html = `<div class="ff-stage-info">
                <span class="ff-creation-title">${_esc(_creation.title)}</span>
                ${freqBadge}
            </div>
            <div class="ff-stage-dots">${html}</div>`;
        } else {
            html = `<div class="ff-stage-dots">${html}</div>`;
        }

        bar.innerHTML = html;

        const container = document.querySelector('.flow-container') || document.body;
        container.insertBefore(bar, container.firstChild);
    }

    // ── Step change handler ────────────────────────────────────────────────
    async function onStepChange(step) {
        if (!_creationId || !_config.stageMap) return;

        const targetStage = _config.stageMap[step];
        if (targetStage && targetStage !== _currentStage) {
            const currentIdx = STAGES.indexOf(_currentStage);
            const targetIdx = STAGES.indexOf(targetStage);
            if (targetIdx > currentIdx) {
                await advanceStage(targetStage);
            }
        }
    }

    // ── Stage advance ──────────────────────────────────────────────────────
    async function advanceStage(targetStage) {
        if (!_creationId) return false;

        // Walk through intermediate stages sequentially
        // (API enforces one-step-at-a-time advancement)
        const currentIdx = STAGES.indexOf(_currentStage || 'broadcast');
        const targetIdx = STAGES.indexOf(targetStage);
        if (targetIdx <= currentIdx) return true; // already there or past

        for (let i = currentIdx + 1; i <= targetIdx; i++) {
            const stage = STAGES[i];
            try {
                const res = await fetch(`/api/creation/actions/${_creationId}/stage`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ stage }),
                });

                if (!res.ok) {
                    const data = await res.json();
                    console.warn('Flow Framework: stage advance failed at', stage, data.error);
                    return false;
                }

                _currentStage = stage;
                if (_creation) _creation.stage = stage;
            } catch (e) {
                console.error('Flow Framework: stage advance error', e);
                return false;
            }
        }

        _renderStageBar(targetStage);
        return true;
    }

    // ── Action spawning ────────────────────────────────────────────────────
    async function spawnAction(title, description) {
        if (!_creationId) return null;
        try {
            const res = await fetch('/api/action-board/tasks', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ title, description: description || '', creation_action_id: parseInt(_creationId) }),
            });
            if (!res.ok) return null;
            const data = await res.json();
            return data.id || null;
        } catch (e) { console.error('Flow Framework: spawnAction error', e); return null; }
    }

    // ── Signs logging ──────────────────────────────────────────────────────
    async function logSign(text) {
        if (!_creationId || !text) return false;
        try {
            const res = await fetch(`/api/creation/actions/${_creationId}/signs`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text }),
            });
            return res.ok;
        } catch (e) { console.error('Flow Framework: logSign error', e); return false; }
    }

    // ── Tuning/Manifest notes ──────────────────────────────────────────────
    async function saveTuningNotes(notes) {
        if (!_creationId) return false;
        try {
            const res = await fetch(`/api/creation/actions/${_creationId}`, {
                method: 'PATCH', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ tuning_notes: notes }),
            });
            return res.ok;
        } catch (e) { return false; }
    }

    async function saveManifestNotes(notes) {
        if (!_creationId) return false;
        try {
            const res = await fetch(`/api/creation/actions/${_creationId}`, {
                method: 'PATCH', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ manifest_notes: notes }),
            });
            return res.ok;
        } catch (e) { return false; }
    }

    // ── Animated loading indicator ───────────────────────────────────────
    // Usage: FlowFramework.showLoading(container)  — container is a DOM element
    //        FlowFramework.hideLoading()
    // Shows animated cycling text: Listening... → Thinking... → Still thinking... → Focusing...

    const LOADING_PHASES = ['Listening...', 'Thinking...', 'Still thinking...', 'Focusing...'];
    let _loadingEl = null;
    let _loadingTimer = null;
    let _loadingPhase = 0;

    function showLoading(container) {
        hideLoading();
        _loadingPhase = 0;

        const el = document.createElement('div');
        el.className = 'ff-loading';
        el.innerHTML = `<span class="ff-loading-text">${LOADING_PHASES[0]}</span>`;
        _loadingEl = el;

        if (container && container.appendChild) {
            container.appendChild(el);
        }

        _loadingTimer = setInterval(() => {
            _loadingPhase = (_loadingPhase + 1) % LOADING_PHASES.length;
            const textEl = el.querySelector('.ff-loading-text');
            if (textEl) {
                textEl.style.opacity = '0';
                setTimeout(() => {
                    textEl.textContent = LOADING_PHASES[_loadingPhase];
                    textEl.style.opacity = '1';
                }, 250);
            }
        }, 3000);

        return el;
    }

    function hideLoading() {
        if (_loadingTimer) { clearInterval(_loadingTimer); _loadingTimer = null; }
        if (_loadingEl) { _loadingEl.remove(); _loadingEl = null; }
        _loadingPhase = 0;
    }

    // ── Getters ────────────────────────────────────────────────────────────
    function getCreationId() { return _creationId; }
    function getCreation() { return _creation; }
    function getCurrentStage() { return _currentStage; }
    function getFrequencyColor() {
        return _creation && _creation.frequency ? FREQ_COLORS[_creation.frequency] : null;
    }

    // ── Utility ────────────────────────────────────────────────────────────
    function _esc(s) {
        if (!s) return '';
        const d = document.createElement('div');
        d.textContent = s;
        return d.innerHTML;
    }

    // ── Public API ─────────────────────────────────────────────────────────
    return {
        init,
        extractFromContext,
        onStepChange,
        advanceStage,
        spawnAction,
        logSign,
        saveTuningNotes,
        saveManifestNotes,
        showLoading,
        hideLoading,
        getCreationId,
        getCreation,
        getCurrentStage,
        getFrequencyColor,
        STAGES,
        FREQ_COLORS,
    };

})();
