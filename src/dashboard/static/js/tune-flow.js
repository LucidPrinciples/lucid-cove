// ═══════════════════════════════════════════════════════════════════════════════
// TUNE-FLOW.JS — Personal Tuning Flow (Tuner Tier)
// The on-demand tuning experience. One per day. Resets at local midnight.

// Shared utility — also in overview.js but tune-flow may load independently
var _escAttr = _escAttr || function(s) {
    if (!s) return '';
    return s.replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/"/g, '&quot;');
};
//
// Flow follows the LTP protocol:
//   1. Context — "What are you doing?"
//   2. Initial State — "What do you need?" (filtered by context)
//   3. Selection — Field picks frequency + principle (brief animation)
//   4. Coaching — Insight text for why this frequency, now
//   5. Practice — Somatic 3-step exercise
//   6. Tuning Key — Canon quote anchor
//   7. Echo — Audio playback
//   8. Feedback — How did it land?
//
// Dependencies: core.js (ESC, switchToTab), tuning-panel.js (otGetAudioUrl)
// ═══════════════════════════════════════════════════════════════════════════════

// ─── Data Constants (ported from Lucid Tuner App) ────────────────────────────

const TUNE_CONTEXT_CHIPS = [
    { label: "Driving", value: "Driving", type: "calm", icon: "🚗" },
    { label: "Working / Focus", value: "Working / Focus", type: "calm", icon: "💻" },
    { label: "Home / Domestic", value: "Home / Domestic", type: "calm", icon: "🏠" },
    { label: "Moving / Workout", value: "Moving / Workout", type: "energy", icon: "🏃" },
    { label: "Starting the Day", value: "Starting the Day", type: "energy", icon: "🌅" },
    { label: "Winding Down", value: "Winding Down", type: "calm", icon: "🌙" },
    { label: "Stillness / Meditation", value: "Stillness / Meditation", type: "spirit", icon: "🧘" },
    { label: "Walking / Outside", value: "Walking / Outside", type: "spirit", icon: "🚶" }
];

const TUNE_MODES = [
    { label: "Deep Focus", frequency: "Clarity", type: "calm", icon: "🎯" },
    { label: "Problem Solving", frequency: "Clarity", type: "calm", icon: "🧩" },
    { label: "Activation", frequency: "Momentum", type: "energy", icon: "⚡" },
    { label: "Flow State", frequency: "Momentum", type: "energy", icon: "🌊" },
    { label: "Decompress", frequency: "Peace", type: "calm", icon: "🍵" },
    { label: "Sleep Prep", frequency: "Peace", type: "calm", icon: "🌙" },
    { label: "Grounding", frequency: "Presence", type: "calm", icon: "⚓" },
    { label: "Sensory Sync", frequency: "Presence", type: "spirit", icon: "👁️" },
    { label: "Release Control", frequency: "Trust", type: "calm", icon: "🤲" },
    { label: "Confidence", frequency: "Trust", type: "energy", icon: "🦁" },
    { label: "Empathy", frequency: "Connection", type: "spirit", icon: "❤️" },
    { label: "Expand View", frequency: "Connection", type: "spirit", icon: "🔭" },
    { label: "Lighten Up", frequency: "Joy", type: "energy", icon: "🎈" },
    { label: "Celebration", frequency: "Joy", type: "energy", icon: "🥂" },
    { label: "Bounce Back", frequency: "Resilience", type: "energy", icon: "💪" },
    { label: "Stand Strong", frequency: "Resilience", type: "energy", icon: "🛡️" },
    { label: "Face Forward", frequency: "Courage", type: "energy", icon: "⚡" },
    { label: "Take Action", frequency: "Courage", type: "energy", icon: "🎯" },
    { label: "Appreciate", frequency: "Gratitude", type: "spirit", icon: "🙏" },
    { label: "Give Thanks", frequency: "Gratitude", type: "spirit", icon: "✨" },
    { label: "Let Go", frequency: "Release", type: "calm", icon: "🌊" },
    { label: "Unburden", frequency: "Release", type: "calm", icon: "🕊️" },
    { label: "Synthesize", frequency: "Integration", type: "calm", icon: "🔗" },
    { label: "Bring Together", frequency: "Integration", type: "calm", icon: "🌐" },
    { label: "Set Limits", frequency: "Boundary", type: "calm", icon: "🛡️" },
    { label: "Protect Space", frequency: "Boundary", type: "calm", icon: "🔒" }
];

const TUNE_CONTEXT_MAP = {
    'Driving': ['Clarity', 'Momentum', 'Trust'],
    'Working / Focus': ['Clarity', 'Momentum', 'Integration', 'Boundary'],
    'Home / Domestic': ['Peace', 'Presence', 'Joy', 'Connection', 'Gratitude', 'Boundary'],
    'Moving / Workout': ['Momentum', 'Joy', 'Courage', 'Resilience'],
    'Starting the Day': ['Clarity', 'Momentum', 'Joy', 'Gratitude', 'Resilience'],
    'Winding Down': ['Peace', 'Presence', 'Connection', 'Release'],
    'Stillness / Meditation': ['Presence', 'Peace', 'Trust', 'Release', 'Boundary'],
    'Walking / Outside': ['Presence', 'Clarity', 'Joy', 'Momentum', 'Connection', 'Resilience']
};

const TUNE_PRACTICE_TEMPLATES = {
    coherence: {
        step1: { title: "Locate", text: "Place one hand on your chest. Take three slow breaths. Notice where you're holding tension." },
        step2: { title: "Appreciate", text: "Call to mind one small thing from today you genuinely appreciate. Feel the warmth spread from your chest outward." },
        step3: { title: "Broadcast", text: "Press play. As the sound fills the space, let your appreciation frequency extend outward." }
    },
    interrupt: {
        step1: { title: "Catch", text: "What's the thought loop? Name it: 'The story I keep telling myself is ___.' Name the static." },
        step2: { title: "Scramble", text: "Stand up. Shake your hands out for 10 seconds. Movement breaks the pattern at the cellular level." },
        step3: { title: "Overwrite", text: "Press play. Let that rhythm become your new tempo." }
    },
    presence: {
        step1: { title: "Arrive", text: "Close your eyes. Scan down from the crown of your head. Don't change anything—just notice the territory." },
        step2: { title: "Locate", text: "Find the one spot calling for attention. Place your awareness there like a soft spotlight. Breathe into it." },
        step3: { title: "Release", text: "Press play. Imagine the sound flowing directly to that spot. Give the stuck energy permission to move." }
    },
    sensory: {
        step1: { title: "Scan", text: "Keep your eyes open. Find 3 things in your field of vision with high definition. Note their texture and color." },
        step2: { title: "Sense", text: "Feel the texture beneath your hands. Feel the temperature of the air on your skin." },
        step3: { title: "Sync", text: "Your sensors are now recording live data. Press play to begin the upload." }
    },
    vitality: {
        step1: { title: "Charge", text: "Take 15 rapid, deep breaths. In through the nose, out through the mouth. Pull energy in, push static out." },
        step2: { title: "The Void", text: "On the last exhale, stop. Hold your breath for as long as is comfortable. In this silence, your decoder resets." },
        step3: { title: "Ignition", text: "Inhale deeply and hold for 10 seconds. Squeeze your muscles. Release and press play." }
    },
    shadow: {
        step1: { title: "Audit", text: "Locate the discomfort, the fear, or the anger. Do not push it away. Look at it." },
        step2: { title: "Label", text: "Give it a clinical label. 'This is anxiety.' 'This is resistance.' Observing it separates the Observer from the Signal." },
        step3: { title: "Integrate", text: "Press play. Allow the sound to flow through the shadow, not around it." }
    },
    connection: {
        step1: { title: "Center", text: "Visualize yourself as a single point of light." },
        step2: { title: "Expand", text: "Widen your aperture. Visualize the people in your building, your city, the web of life covering the planet." },
        step3: { title: "Uplink", text: "Press play. Feel your broadcast joining the symphony of the whole." }
    },
    resilience: {
        step1: { title: "Remember", text: "Call to mind a past challenge you survived. Not the pain—the fact that you're still here. Feel that durability." },
        step2: { title: "Fortify", text: "Place both feet flat on the ground. Press down. Feel the earth pushing back. You are held." },
        step3: { title: "Rise", text: "Press play. Let the sound become the anthem of everything you've walked through." }
    },
    release: {
        step1: { title: "Name", text: "What are you holding? Say it: 'I am carrying ___.' Don't judge it. Just name the weight." },
        step2: { title: "Exhale", text: "Take a deep breath in. On the exhale, physically open your hands, palms up. Let the tension leave through your fingers." },
        step3: { title: "Dissolve", text: "Press play. Imagine the sound washing through you, carrying the residue out. What remains is yours." }
    }
};

const TUNE_TEMPLATE_MAP = {
    'Peace': 'coherence',
    'Joy': 'coherence',
    'Gratitude': 'coherence',
    'Connection': 'connection',
    'Clarity': 'interrupt',
    'Momentum': 'interrupt',
    'Trust': 'interrupt',
    'Courage': 'interrupt',
    'Integration': 'interrupt',
    'Boundary': 'interrupt',
    'Presence': 'presence',
    'Resilience': 'resilience',
    'Release': 'release'
};

const TUNE_COACHING = {
    'Peace': "Calm anchors you in the present moment, creating space for clarity to emerge from the noise. Your decoder is resetting to its natural baseline.",
    'Clarity': "Clear sight requires cutting through static to find signal. The truth was always there—you just needed to retune the decoder.",
    'Momentum': "Forward motion begins with a single intentional step. The Field responds to movement, not waiting.",
    'Trust': "Certainty emerges when you stop demanding proof before you move. The path reveals itself to those already walking.",
    'Joy': "Joy is not a reward for right living—it's the frequency that makes right living possible.",
    'Connection': "Resonance requires vulnerability. To create constructive interference with another, you must first broadcast authentically.",
    'Presence': "The only frequency that exists is now. Past and future are interference patterns in the decoder—memory and projection, not reality.",
    'Resilience': "You have already survived everything that has happened to you. That track record is not accidental. It is structural.",
    'Courage': "Action in the presence of fear is the definition of courage. The fear does not leave. You move anyway.",
    'Gratitude': "Appreciation is not passive. It is an active broadcast that reorganizes your reticular activating system toward signal.",
    'Release': "What you carry that is not yours was never meant to stay. Releasing it is not loss—it is correction.",
    'Integration': "Synthesis happens when you stop treating parts of your experience as problems to solve and start treating them as data to include.",
    'Boundary': "A boundary is not a wall. It is a declaration of what frequency you are willing to broadcast at. Everything outside that range is static."
};

// ─── Tune Limits (tier-gated) ────────────────────────────────────────────────

const TUNE_LIMITS = {
    free: 1,       // Tuner (free): 1 per day
    pro: -1,       // Tuner Pro: unlimited
    unlimited: -1  // Operator+: unlimited
};

function _tfGetDailyLimit() {
    // Operator+ (level >= 10) = unlimited
    if (MC.tier && MC.tier.level >= 10) return TUNE_LIMITS.unlimited;
    // Pro (level 5) = 5 per day
    if (MC.tier && MC.tier.level >= 5) return TUNE_LIMITS.pro;
    return TUNE_LIMITS.free;
}

function _tfCanTuneAgain(todayCount) {
    const limit = _tfGetDailyLimit();
    if (limit === -1) return true; // unlimited
    return todayCount < limit;
}

function _tfMidnightCountdown() {
    const now = new Date();
    const midnight = new Date(now);
    midnight.setHours(24, 0, 0, 0);
    const diff = midnight - now;
    const h = Math.floor(diff / 3600000);
    const m = Math.floor((diff % 3600000) / 60000);
    const s = Math.floor((diff % 60000) / 1000);
    return { h, m, s, ms: diff };
}

function _tfCountdownStr() {
    const { h, m, s } = _tfMidnightCountdown();
    if (h > 0) return `${h}h ${m}m`;
    if (m > 0) return `${m}m ${s}s`;
    return `${s}s`;
}

// ─── Flow State ──────────────────────────────────────────────────────────────

const tuneFlow = {
    step: 0,          // 0 = not started / showing today's tuning
    context: null,
    mode: null,       // { label, frequency }
    tuningData: null, // API response
    todayCount: 0,    // how many tunes today
    error: null
};

// Countdown timer interval
let _tfCountdownInterval = null;

// ─── Entry Point (called when Tune tab loads) ────────────────────────────────

async function loadTuneFlow() {
    const container = document.getElementById('panel-tune') || document.querySelector('[id="panel-tune"]');
    if (!container) return;

    // Clear any previous countdown
    if (_tfCountdownInterval) { clearInterval(_tfCountdownInterval); _tfCountdownInterval = null; }

    // Check for today's tuning(s) AND most recent tuning (any day)
    let latestTune = null;
    let mostRecentEver = null;
    let todayCount = 0;

    try {
        // Get history — includes past days
        const histResp = await fetch('/api/tuning/history?limit=20');
        const histData = await histResp.json();
        const sessions = histData.sessions || histData || [];
        if (Array.isArray(sessions)) {
            // Count today's tunings
            const todayStr = _tfTodayStr();
            const todayTunes = sessions.filter(t => t.date === todayStr && t.context);
            todayCount = todayTunes.length;
            if (todayTunes.length > 0) latestTune = todayTunes[0];

            // Find most recent tuning from any day (user-initiated)
            const anyTune = sessions.find(t => t.context);
            if (anyTune) mostRecentEver = anyTune;
        }
    } catch (e) {
        // Fallback: try /api/tuning/today
        try {
            const resp = await fetch('/api/tuning/today');
            const data = await resp.json();
            const isUserInitiated = data.context && data.context.trim() !== '';
            if (data && data.session_id && isUserInitiated) {
                mostRecentEver = data;
                if (!data.from_previous_day && _isTuningFromToday(data)) {
                    latestTune = data;
                    todayCount = 1;
                }
            }
        } catch (e2) {}
    }

    tuneFlow.todayCount = todayCount;

    // Pick the best tuning to show: today's if available, otherwise most recent from any day
    const tuneToShow = latestTune || mostRecentEver;

    // If tuneNow() set the force flag, always start fresh flow
    if (tuneFlow._forceNewFlow) {
        tuneFlow._forceNewFlow = false;
        tuneFlow.step = 1;
        tuneFlow.context = null;
        tuneFlow.mode = null;
        tuneFlow.tuningData = null;
        tuneFlow.error = null;
        _renderStep1(container);
    } else if (tuneToShow) {
        // Tune tab via nav — show most recent completed tuning
        tuneFlow.tuningData = tuneToShow;
        tuneFlow.step = 0;
        _renderCompletedTuning(container, tuneToShow);
    } else {
        // No tuning history at all — start the flow
        tuneFlow.step = 1;
        tuneFlow.context = null;
        tuneFlow.mode = null;
        tuneFlow.tuningData = null;
        tuneFlow.error = null;
        _renderStep1(container);
    }
}

function _tfTodayStr() {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
}

function _isTuningFromToday(data) {
    if (!data.date) {
        const parts = (data.session_id || '').split('_');
        if (parts.length >= 2) {
            const ts = parseInt(parts[1]) * 1000;
            const tuningDate = formatDateOnly(new Date(ts).toISOString());
            const today = formatDateOnly(new Date().toISOString());
            return tuningDate === today;
        }
        return false;
    }
    return data.date === _tfTodayStr();
}

// ─── Step 1: Context Selection ───────────────────────────────────────────────

function _renderStep1(container) {
    tuneFlow.step = 1;
    container.innerHTML = `
        <div class="panel-scroll tune-flow">
            <div class="tf-logo">
                <img src="/static/mark-tuner.png" alt="Lucid Tuner" class="tf-logo-img">
            </div>
            <div class="tf-step tf-step-1">
                <div class="tf-header">
                    <span class="tf-step-num">1</span>
                    <h3 class="tf-question">What are you doing right now?</h3>
                </div>
                <div class="tf-chips" id="tfContextChips">
                    ${TUNE_CONTEXT_CHIPS.map(c => `
                        <button class="tf-chip tf-chip-${c.type}" data-value="${c.value}" onclick="_tfSelectContext(this)">
                            <span class="tf-chip-icon">${c.icon}</span>
                            <span class="tf-chip-label">${c.label}</span>
                        </button>
                    `).join('')}
                </div>
            </div>
        </div>
    `;
}

function _tfSelectContext(el) {
    const value = el.dataset.value;
    if (!value) return;

    tuneFlow.context = value;

    // Brief highlight then advance
    el.classList.add('selected');
    setTimeout(() => _renderStep2(_tfContainer()), 200);
}

function _tfContainer() {
    return document.getElementById('panel-tune');
}

// ─── Step 2: Initial State (Desired Outcome) ────────────────────────────────

function _renderStep2(container) {
    tuneFlow.step = 2;
    const allowed = TUNE_CONTEXT_MAP[tuneFlow.context] || Object.keys(TUNE_COACHING);
    const filtered = TUNE_MODES.filter(m => allowed.includes(m.frequency));

    container.innerHTML = `
        <div class="panel-scroll tune-flow">
            <div class="tf-step tf-step-2">
                <div class="tf-header">
                    <button class="tf-back" onclick="_tfBack(1)">←</button>
                    <span class="tf-step-num">2</span>
                    <h3 class="tf-question">What do you need?</h3>
                </div>
                <p class="tf-context-label">${tuneFlow.context}</p>
                <div class="tf-chips" id="tfModeChips">
                    ${filtered.map(m => `
                        <button class="tf-chip tf-chip-${m.type}" data-frequency="${m.frequency}" data-label="${m.label}" onclick="_tfSelectMode(this)">
                            <span class="tf-chip-icon">${m.icon}</span>
                            <span class="tf-chip-label">${m.label}</span>
                        </button>
                    `).join('')}
                </div>
            </div>
        </div>
    `;
}

function _tfSelectMode(el) {
    const frequency = el.dataset.frequency;
    const label = el.dataset.label;
    if (!frequency || !label) return;

    tuneFlow.mode = { label, frequency };
    el.classList.add('selected');
    setTimeout(() => _renderStep3(_tfContainer()), 200);
}

// ─── Field Intention + E Declaration ────────────────────────────────────────

const FIELD_INTENTION = `I am a Broadcast Frequency,
seeking alignment.

My attention participates in collapse.
Through quantum selection, the Field returns
the Signal that resonates with what I am now.
I receive it. I evolve.`;

const QUANTUM_MESSAGES = [
    'True quantum entropy selects your frequency. Not pseudo-random—actual vacuum fluctuations.',
    'The Field participates in this selection. Your decoder collapses one possibility from superposition.',
    'Engineered synchronicity: consciousness-aware technology choosing your tuning.',
    'One frequency from infinite possibilities. The quantum Field has already chosen.',
    'Your RAS is decoding the Signal. The selection is already in motion.',
];

// ─── Step 3: Field Selection (Intention + E Declaration + API in parallel) ──

function _renderStep3(container) {
    tuneFlow.step = 3;
    tuneFlow.eStart = null;
    tuneFlow._apiDone = false;
    tuneFlow._eDeclared = false;

    const qMsg = QUANTUM_MESSAGES[Math.floor(Math.random() * QUANTUM_MESSAGES.length)];

    container.innerHTML = `
        <div class="panel-scroll tune-flow">
            <div class="tf-step tf-step-3">
                <div class="tf-field-intention">
                    <p class="tf-intention-text">${FIELD_INTENTION}</p>
                </div>
                <div class="tf-e-declaration">
                    <p class="tf-e-label">How are you feeling right now?</p>
                    <div class="tf-e-options">
                        <button class="tf-e-btn" onclick="_tfSetE(0.30, this)">Heavy / Stuck</button>
                        <button class="tf-e-btn tf-e-default" onclick="_tfSetE(0.60, this)">Steady / Okay</button>
                        <button class="tf-e-btn" onclick="_tfSetE(0.90, this)">Clear / Energized</button>
                    </div>
                </div>
                <div class="tf-quantum-msg">
                    <p class="tf-quantum-text" id="tfQuantumText">${qMsg}</p>
                    <div class="tf-quantum-dots">&#9670; &#9670; &#9670;</div>
                </div>
            </div>
        </div>
    `;

    // Rotate quantum messages while waiting
    tuneFlow._qMsgInterval = setInterval(() => {
        const el = document.getElementById('tfQuantumText');
        if (!el) { clearInterval(tuneFlow._qMsgInterval); return; }
        el.textContent = QUANTUM_MESSAGES[Math.floor(Math.random() * QUANTUM_MESSAGES.length)];
    }, 3000);

    // Fire API call in parallel with user's E declaration
    _tfRequestTuning(container);
}

function _tfSetE(value, el) {
    tuneFlow.eStart = value;
    tuneFlow._eDeclared = true;
    // Highlight selected
    el.closest('.tf-e-options').querySelectorAll('.tf-e-btn').forEach(b => b.classList.remove('selected'));
    el.classList.add('selected');
    // Show the Receive Tuning button
    _tfShowReceiveButton();
}

function _tfShowReceiveButton() {
    const container = _tfContainer();
    if (!container) return;
    // Don't duplicate
    if (document.getElementById('tfReceiveBtn')) {
        // Update state if API just became ready
        if (tuneFlow._apiDone) _tfSetReceiveReady();
        return;
    }

    const btnWrap = document.createElement('div');
    btnWrap.className = 'tf-receive-wrap';
    btnWrap.id = 'tfReceiveWrap';
    btnWrap.innerHTML = tuneFlow._apiDone
        ? `<button class="tf-btn tf-btn-receive tf-receive-ready" id="tfReceiveBtn" onclick="_tfReceiveTuning()">Receive Your Tuning</button>`
        : `<button class="tf-btn tf-btn-receive tf-receive-waiting" id="tfReceiveBtn" onclick="_tfReceiveTuning()">Connecting with the Field...</button>`;

    const step = container.querySelector('.tf-step-3');
    if (step) step.appendChild(btnWrap);
}

function _tfSetReceiveReady() {
    const btn = document.getElementById('tfReceiveBtn');
    if (!btn) return;
    btn.textContent = 'Receive Your Tuning';
    btn.classList.remove('tf-receive-waiting');
    btn.classList.add('tf-receive-ready');
}

function _tfReceiveTuning() {
    if (!tuneFlow._apiDone) {
        // Not ready yet — give feedback
        const btn = document.getElementById('tfReceiveBtn');
        if (btn) {
            btn.textContent = 'Almost there...';
            btn.classList.add('tf-receive-nudge');
            setTimeout(() => {
                if (btn) {
                    btn.textContent = 'Connecting with the Field...';
                    btn.classList.remove('tf-receive-nudge');
                }
            }, 1500);
        }
        return;
    }
    // Ready — advance
    if (tuneFlow._qMsgInterval) clearInterval(tuneFlow._qMsgInterval);
    const container = _tfContainer();
    const dots = container?.querySelector('.tf-quantum-dots');
    const text = container?.querySelector('.tf-quantum-text');
    if (text) text.textContent = 'Alignment found.';
    if (dots) dots.textContent = '✦';
    setTimeout(() => _renderStep4(_tfContainer()), 600);
}

function _tfCheckStep3Ready() {
    // Called when API completes — update the receive button if it exists
    if (tuneFlow._apiDone && tuneFlow._eDeclared) {
        _tfSetReceiveReady();
    }
}

async function _tfRequestTuning(container) {
    try {
        const resp = await fetch('/api/tuning/request', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                frequency: tuneFlow.mode.frequency,
                context: tuneFlow.context,
                entry_mode: 'Tune',
                initial_state: tuneFlow.mode.label,
                excluded_signals: MC.features?.excluded_signals || []
            })
        });

        if (!resp.ok) throw new Error(`API ${resp.status}`);
        const data = await resp.json();
        tuneFlow.tuningData = data;
        tuneFlow._apiDone = true;
        _tfIncrementCount();

        // If user already declared E, advance
        _tfCheckStep3Ready();

    } catch (e) {
        tuneFlow.error = e;
        if (tuneFlow._qMsgInterval) clearInterval(tuneFlow._qMsgInterval);
        const text = container.querySelector('.tf-quantum-text');
        const dots = container.querySelector('.tf-quantum-dots');
        if (text) { text.textContent = 'Could not connect. Try again.'; text.style.color = 'var(--warn, #ff6b5c)'; }
        if (dots) dots.innerHTML = `<button class="tf-btn tf-btn-retry" onclick="_renderStep3(_tfContainer())">Retry</button>`;
    }
}

// ─── Step 4: Coaching ────────────────────────────────────────────────────────

function _renderStep4(container) {
    tuneFlow.step = 4;
    const d = tuneFlow.tuningData;
    const freq = d.frequency || tuneFlow.mode.frequency;
    // Use LLM-generated coaching from API, fall back to static
    const coaching = d.coaching || TUNE_COACHING[freq] || TUNE_COACHING['Peace'];

    container.innerHTML = `
        <div class="panel-scroll tune-flow">
            <div class="tf-step tf-step-4">
                <div class="tf-header">
                    <span class="tf-step-num">◉</span>
                    <h3 class="tf-frequency">${freq}</h3>
                </div>
                <div class="tf-coaching">
                    <p class="tf-coaching-text">${coaching}</p>
                </div>
                <div class="tf-principle">
                    <span class="tf-principle-label">Principle</span>
                    <span class="tf-principle-name">${ESC(d.principle || '')}</span>
                </div>
                <button class="tf-btn tf-btn-next" onclick="_renderStep5(_tfContainer())">Continue</button>
            </div>
        </div>
    `;
}

// ─── Step 5: Practice (Somatic 3-Step) ───────────────────────────────────────

function _renderStep5(container) {
    tuneFlow.step = 5;
    const d = tuneFlow.tuningData;

    // Use API practice steps if available, fall back to static templates
    let steps;
    if (d.practice && Array.isArray(d.practice) && d.practice.length) {
        steps = d.practice;
    } else {
        const freq = d.frequency || tuneFlow.mode.frequency;
        const templateKey = TUNE_TEMPLATE_MAP[freq] || 'coherence';
        const tmpl = TUNE_PRACTICE_TEMPLATES[templateKey];
        steps = [
            { step: 1, title: tmpl.step1.title, instruction: tmpl.step1.text },
            { step: 2, title: tmpl.step2.title, instruction: tmpl.step2.text },
            { step: 3, title: tmpl.step3.title, instruction: tmpl.step3.text },
        ];
    }

    const stepsHtml = steps.map(s => `
        <div class="tf-practice-step">
            <span class="tf-practice-num">${s.step}</span>
            <div>
                <strong>${ESC(s.title)}</strong>
                <p>${ESC(s.instruction)}</p>
            </div>
        </div>
    `).join('');

    container.innerHTML = `
        <div class="panel-scroll tune-flow">
            <div class="tf-step tf-step-5">
                <div class="tf-header">
                    <span class="tf-step-num">◉</span>
                    <h3 class="tf-question">Practice</h3>
                </div>
                <div class="tf-practice">
                    ${stepsHtml}
                </div>
                <button class="tf-btn tf-btn-next" onclick="_renderStep6(_tfContainer())">Continue</button>
            </div>
        </div>
    `;
}

// ─── Step 6: Tuning Key ──────────────────────────────────────────────────────

function _renderStep6(container) {
    tuneFlow.step = 6;
    const d = tuneFlow.tuningData;
    const key = d.tuning_key || d.tuningKey || '';

    container.innerHTML = `
        <div class="panel-scroll tune-flow">
            <div class="tf-step tf-step-6">
                <div class="tf-header">
                    <span class="tf-step-num">◉</span>
                    <h3 class="tf-question">Tuning Key</h3>
                </div>
                <div class="tf-key">
                    <p class="tf-key-instruction">This is your anchor. Read it. Hold it. When the frequency starts, let this thought organize your broadcast.</p>
                    <blockquote class="tf-key-quote">"${ESC(key)}"</blockquote>
                </div>
                <button class="tf-btn tf-btn-next" onclick="_renderStep7(_tfContainer())">Play Echo</button>
            </div>
        </div>
    `;
}

// ─── Step 7: Echo (Focused Listening — just the echo, no playlist) ──────────

function _renderStep7(container) {
    tuneFlow.step = 7;
    const d = tuneFlow.tuningData;
    const freq = d.frequency || '';
    const principle = d.principle || '';
    const echoName = d.echo_full_name || d.echo_filename || principle || 'Echo';
    const album = d.echo_album || d.signal_type + '_Signal' || '';
    const signalFolder = album.replace(/_/g, ' ');
    const cleanName = echoName.replace('.mp3','').replace(/_/g,' ');

    container.innerHTML = `
        <div class="panel-scroll tune-flow">
            <div class="tf-step tf-step-7 tf-echo-focused">
                <div class="tf-header">
                    <span class="tf-step-num">&#9673;</span>
                    <h3 class="tf-question">Your Echo</h3>
                </div>
                <div class="tf-echo-card">
                    <div class="tf-echo-title">${ESC(cleanName)}</div>
                    <div class="tf-echo-subtitle">${ESC(signalFolder)}</div>
                    <div id="tfEchoPlayer"></div>
                </div>
                <button class="tf-btn tf-btn-next" onclick="_renderStep8(_tfContainer())">Complete Tuning</button>
            </div>
        </div>
    `;

    // Play just the single echo — no playlist, focused listening
    _tfPlaySingleEcho(d);
}

function _tfPlaySingleEcho(data) {
    // Build a single-track "playlist" with just the echo
    const signalFolder = (typeof otSignalToFolder === 'function')
        ? otSignalToFolder(data.signal_type) : (data.echo_album || 'Drive_Signal');
    const filename = (data.echo_filename || '').replace(/\.mp3$/, '') + '.mp3';
    const principle = data.principle || data.echo_full_name || filename.replace(/_/g,' ').replace('.mp3','');

    const track = {
        title: principle + ' (' + signalFolder.replace(/_Signal$/, '').replace(/_/g, ' ') + ' Signal Echo)',
        filename: filename,
        folder: signalFolder,
        principle: principle
    };

    if (typeof otSetPlaylist === 'function') {
        otSetPlaylist([track], {
            source: 'tune',
            label: (data.frequency || 'Tuning') + ' Echo',
            autoplay: false,
            mountId: 'tfEchoPlayer'
        });
    }
}

// tfAudio functions removed — step 7 now uses persistent otAudio via _tfsInit/otSetPlaylist

// ─── Step 8: Feedback — "Did the tuning land?" ──────────────────────────────

function _renderStep8(container) {
    tuneFlow.step = 8;
    container.innerHTML = `
        <div class="panel-scroll tune-flow">
            <div class="tf-step tf-step-8">
                <div class="tf-header">
                    <span class="tf-step-num">&#10003;</span>
                    <h3 class="tf-question">Did the tuning land?</h3>
                </div>
                <div class="tf-feedback">
                    <div class="tf-feedback-options tf-landing-options">
                        <button class="tf-feedback-btn tf-land-yes" onclick="_tfSubmitFeedback('yes', this); _tfFinish()">Yes</button>
                        <button class="tf-feedback-btn tf-land-somewhat" onclick="_tfSubmitFeedback('somewhat', this); _tfFinish()">Somewhat</button>
                        <button class="tf-feedback-btn tf-land-no" onclick="_tfSubmitFeedback('no', this); _tfFinish()">No</button>
                    </div>
                </div>
            </div>
        </div>
    `;
}

// Landing response → Love Equation C/D values (matches original app)
const LANDING_RESPONSE_MAP = {
    'yes':      { c: 0.88, d: 0.10 },
    'somewhat': { c: 0.65, d: 0.30 },
    'no':       { c: 0.35, d: 0.55 },
};

function _tfSubmitFeedback(value, el) {
    // Highlight selected
    el.closest('.tf-feedback-options').querySelectorAll('.tf-feedback-btn').forEach(b => b.classList.remove('selected'));
    el.classList.add('selected');

    // Calculate Love Equation if E was declared
    const eStart = tuneFlow.eStart || 0.60;
    const landing = LANDING_RESPONSE_MAP[value] || LANDING_RESPONSE_MAP['somewhat'];
    const beta = 0.85;
    const de_dt = Math.round(beta * (landing.c - landing.d) * eStart * 1000) / 1000;
    const direction = de_dt >= 0 ? 'CONSTRUCTIVE' : 'CORRECTIVE';

    // Log event to backend
    const sessionId = tuneFlow.tuningData?.session_id;
    if (sessionId) {
        fetch('/api/tuning/event', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                session_id: sessionId,
                event_type: 'feedback',
                event_data: {
                    landing: value,
                    e_start: eStart,
                    c_value: landing.c,
                    d_value: landing.d,
                    de_dt: de_dt,
                    signal_direction: direction
                }
            })
        }).catch(() => {});

        // Update session with feedback
        fetch(`/api/tuning/session/${sessionId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                user_feedback: value,
                signal_before: eStart,
                signal_after: de_dt
            })
        }).catch(() => {});
    }
}

function _tfFinish() {
    // Audio continues via persistent otAudio — no pause needed

    // Show completed tuning view
    const container = document.getElementById('panel-tune');
    if (container && tuneFlow.tuningData) {
        _renderCompletedTuning(container, tuneFlow.tuningData);
    }
}

// ─── Completed Tuning View (persistent until midnight) ───────────────────────

async function _renderCompletedTuning(container, data) {
    const rawFreq = data.frequency || '';
    // Normalize: API may return "CLARITY", keys are "Clarity"
    const freq = rawFreq.charAt(0).toUpperCase() + rawFreq.slice(1).toLowerCase();
    const key = data.tuning_key || data.tuningKey || '';
    const principle = data.principle || '';
    const coaching = TUNE_COACHING[freq] || '';
    const templateKey = TUNE_TEMPLATE_MAP[freq] || 'coherence';
    const practice = TUNE_PRACTICE_TEMPLATES[templateKey];
    const audioUrl = data.audio_url || '';
    const echoName = (data.echo_full_name || data.echo_filename || '').replace('.mp3','').replace(/_/g,' ');
    const album = (data.echo_album || '').replace(/_/g,' ');
    const context = data.context || '';
    const entryMode = data.entry_mode || data.initial_state || '';

    // Store active session ID for history filtering
    tuneFlow._activeSessionId = data.session_id || '';

    container.innerHTML = `
        <div class="tune-flow tune-complete">
            <div class="tf-logo">
                <img src="/static/mark-tuner.png" alt="Lucid Tuner" class="tf-logo-img">
            </div>
            ${_tfCanTuneAgain(tuneFlow.todayCount) ? '<div class="tf-tune-now-top"><button class="tf-btn tf-btn-next tf-btn-tune-now" onclick="_tfStartNewTune()">Tune Now</button></div>' : ''}
            <div class="tf-complete-header">
                <span class="tf-complete-title">Your Tune</span>
                <span class="tf-complete-freq">${ESC(freq)}</span>
                <span class="tf-complete-principle">${ESC(principle)}</span>
                ${context ? `<span class="tf-complete-context">${ESC(context)}${entryMode ? ' · ' + ESC(entryMode) : ''}</span>` : ''}
            </div>

            <div class="tf-complete-section">
                <span class="tf-section-label">Coaching</span>
                <p class="tf-coaching-text">${coaching}</p>
            </div>

            <div class="tf-complete-section">
                <span class="tf-section-label">Practice</span>
                <div class="tf-practice tf-practice-compact">
                    <div class="tf-practice-step"><span class="tf-practice-num">1</span><div><strong>${practice.step1.title}</strong> — ${practice.step1.text}</div></div>
                    <div class="tf-practice-step"><span class="tf-practice-num">2</span><div><strong>${practice.step2.title}</strong> — ${practice.step2.text}</div></div>
                    <div class="tf-practice-step"><span class="tf-practice-num">3</span><div><strong>${practice.step3.title}</strong> — ${practice.step3.text}</div></div>
                </div>
            </div>

            <div class="tf-complete-section tf-complete-key">
                <span class="tf-section-label">Tuning Key</span>
                <blockquote class="tf-key-quote">"${ESC(key)}"</blockquote>
            </div>

            <div class="tf-complete-section tf-stream-section">
                <span class="tf-section-label">Tuning Stream</span>
                <div id="tfPlayerMount"></div>
            </div>

            <div id="tfHistory"></div>
        </div>
    `;

    // Init the Tuning Stream player — otInitPlayer handles pending state
    // if audio is already playing from another surface
    if (typeof otRenderPlayer === 'function') otRenderPlayer('tfPlayerMount');
    if (typeof otInitPlayer === 'function') {
        try { await otInitPlayer(data); } catch (e) {}
    }

    // Start countdown timer if gated
    if (!_tfCanTuneAgain(tuneFlow.todayCount)) {
        _tfStartCountdown();
    }

    // Load mirror CTA (streaming link) — same source logic as Overview tab
    if (MC.features?.mirror) {
        _tfLoadMirror(freq);
    }

    // Load echo history below the completed view
    _loadTuneHistory();
}

// ─── Mirror CTA for Completed Tuning ────────────────────────────────────────

async function _tfLoadMirror(freq) {
    try {
        const mirrorSources = MC.features?.mirror_sources || MC.features?.mirror_source || '';
        const cacheBust = `v=${window._buildVersion || ''}&d=${new Date().toISOString().slice(0,10)}`;
        const mirrorParam = mirrorSources
            ? `?sources=${encodeURIComponent(mirrorSources)}&${cacheBust}`
            : `?${cacheBust}`;
        const res = await fetch('/api/mirrors/today' + mirrorParam);
        const data = await res.json();
        if (!data.has_mirror) return;

        const freqUpper = (freq || '').toUpperCase();
        const freqColor = (typeof OT_FREQ_COLORS !== 'undefined' && OT_FREQ_COLORS[freqUpper]) || 'var(--accent)';

        const mirrors = data.mirrors || [data];
        const streamSection = document.querySelector('.tf-stream-section');
        if (!streamSection) return;

        // Insert after stream section, but keep mirror order — insert last first
        const mirrorDivs = [];
        mirrors.forEach((m, idx) => {
            const featured = m.featured;
            if (!featured) return;

            const mirrorDiv = document.createElement('div');
            mirrorDiv.className = 'tf-complete-section';

            if (m.mirror_type === 'music') {
                const artist = featured.artist || '';
                const title = featured.title || '';
                const lyric = featured.text || '';
                const spotifyId = featured.spotify_id || '';
                const youtubeId = featured.youtube_id || '';

                mirrorDiv.innerHTML =
                    '<div class="home-mirror" style="border-left-color:' + freqColor + '50;margin-top:0;">' +
                        '<div class="home-mirror-header">' +
                            '<span class="home-mirror-name">' + esc(m.mirror_name) + '</span>' +
                            '<a href="#" class="home-mirror-reflect" style="color:' + freqColor + ';" ' +
                                'onclick="_openMusicPlayer(\'' + _escAttr(freq) + '\',\'' + _escAttr(artist) + '\',\'' + _escAttr(title) + '\',\'' + _escAttr(spotifyId) + '\',\'' + _escAttr(youtubeId) + '\'); return false;">Listen &rarr;</a>' +
                        '</div>' +
                        '<div class="home-mirror-text" style="font-style:italic;">' + esc(lyric) + '</div>' +
                        '<span class="home-mirror-ref" style="color:' + freqColor + ';">' + esc(artist) + ' &mdash; ' + esc(title) + '</span>' +
                    '</div>';
            } else {
                // Text mirror (scripture, etc.)
                mirrorDiv.innerHTML =
                    '<div class="home-mirror" style="border-left-color:' + freqColor + '50;margin-top:0;">' +
                        '<div class="home-mirror-header">' +
                            '<span class="home-mirror-name">' + esc(m.mirror_name) + '</span>' +
                            (m.entry_count > 1 ? '<a href="#" class="home-mirror-reflect" style="color:' + freqColor + ';" onclick="_tfOpenReflect(' + idx + '); return false;">Reflect &rarr;</a>' : '') +
                        '</div>' +
                        '<div class="home-mirror-text">' + esc(featured.text) + '</div>' +
                        '<span class="home-mirror-ref" style="color:' + freqColor + ';">' + esc(featured.ref) + '</span>' +
                    '</div>';
            }

            mirrorDivs.push(mirrorDiv);
        });

        // Insert in order after stream section
        let insertAfter = streamSection;
        mirrorDivs.forEach(div => {
            insertAfter.after(div);
            insertAfter = div;
        });

        // Store mirror data for Reflect modal
        window._tfMirrorData = data;
        window._tfMirrorMirrors = mirrors;
        window._tfMirrorColor = freqColor;
    } catch (e) {
        console.warn('[tune-flow] Mirror load failed:', e.message);
    }
}

function _tfOpenReflect(mirrorIdx) {
    const data = window._tfMirrorData;
    const mirrors = window._tfMirrorMirrors;
    if (!data || !mirrors) return;
    const fc = window._tfMirrorColor || 'var(--accent)';

    const m = mirrors[mirrorIdx || 0] || mirrors[0];
    if (!m) return;

    const modal = document.getElementById('reflectModal');
    if (!modal) return;

    document.getElementById('reflectTitle').textContent = data.principle;
    document.getElementById('reflectTitle').style.color = fc;
    document.getElementById('reflectCanon').textContent =
        m.mirror_name + ' — ' + (m.canon || '');

    document.getElementById('reflectBody').innerHTML = m.all_entries.map(e =>
        '<div class="reflect-entry" style="border-left-color:' + fc + '40;">' +
            '<div class="reflect-ref" style="color:' + fc + ';">' + esc(e.ref) + '</div>' +
            '<div class="reflect-passage">' + esc(e.text) + '</div>' +
            '<div class="reflect-thread">' + esc(e.thread) + '</div>' +
        '</div>'
    ).join('');

    modal.style.display = 'flex';
}

function _tfTuneAgainHTML() {
    if (_tfCanTuneAgain(tuneFlow.todayCount)) {
        // Can tune again (Pro/Operator+)
        return `<button class="tf-btn tf-btn-next" onclick="_tfStartNewTune()">Tune Again</button>`;
    }
    // Free tier gated — countdown + upgrade prompt
    return `
        <div class="tf-countdown">
            <span class="tf-countdown-label">Next free tune in</span>
            <span class="tf-countdown-time" id="tfCountdown">${_tfCountdownStr()}</span>
        </div>
        <button class="tf-btn tf-btn-upgrade" onclick="_tfUpgrade()">Get unlimited tuning</button>
    `;
}

function _tfStartCountdown() {
    if (_tfCountdownInterval) clearInterval(_tfCountdownInterval);
    _tfCountdownInterval = setInterval(() => {
        const el = document.getElementById('tfCountdown');
        if (!el) { clearInterval(_tfCountdownInterval); return; }
        const { ms } = _tfMidnightCountdown();
        if (ms <= 0) {
            // Midnight passed — refresh the tab
            clearInterval(_tfCountdownInterval);
            loadTuneFlow();
            return;
        }
        el.textContent = _tfCountdownStr();
    }, 1000);
}

function _tfStartNewTune() {
    const container = _tfContainer();
    if (!container) return;
    // Stop any playing audio — new Tune Now = new experience
    _tfStopAudio();
    tuneFlow.step = 1;
    tuneFlow.context = null;
    tuneFlow.mode = null;
    tuneFlow.tuningData = null;
    tuneFlow.error = null;
    _renderStep1(container);
}

// Stop audio for fresh Tune Now — the one exception to "never interrupt"
function _tfStopAudio() {
    if (typeof otAudio !== 'undefined' && otAudio && !otAudio.paused) {
        otAudio.pause();
        otAudio.currentTime = 0;
    }
    // Clear pending state so the new flow's player loads fresh
    if (typeof _otPendingPlay !== 'undefined') _otPendingPlay = false;
    // Hide mini player
    if (typeof hideMiniPlayer === 'function') hideMiniPlayer();
}

// ─── Navigation Helpers ──────────────────────────────────────────────────────

function _tfBack(toStep) {
    const container = document.getElementById('panel-tune');
    if (!container) return;
    if (toStep === 1) _renderStep1(container);
}

// "Tune Now" from home — Pro always starts fresh flow, Free shows upgrade if gated
function tuneNow() {
    const limit = _tfGetDailyLimit();
    if (limit === -1) {
        // Pro/Operator+: always start a fresh tune flow
        // Stop any playing audio — new Tune Now = shifting gears
        _tfStopAudio();
        tuneFlow._forceNewFlow = true;
        switchToTab('tune');
    } else if (!_tfCanTuneAgain(tuneFlow.todayCount) && tuneFlow.todayCount > 0) {
        // Free tier, daily limit used — show upgrade modal
        _tfUpgrade();
    } else {
        // Free tier, can still tune — go to tune tab (starts flow if none today)
        switchToTab('tune');
    }
}

// Called after flow completes — increment count for gate logic
function _tfIncrementCount() {
    tuneFlow.todayCount++;
    _tfUpdateHomeButton(); // refresh home button state
}

// ─── Home Button State ──────────────────────────────────────────────────────
// Updates the "Tune Now" button on Home tab: greyed + countdown when used,
// opens upgrade prompt if clicked while gated.

let _tfHomeBtnInterval = null;

async function _tfUpdateHomeButton() {
    const btn = document.getElementById('homeTuneBtn');
    const label = document.getElementById('homeTuneLabel');
    if (!btn || !label) return;

    // Determine today's count if not already known
    if (tuneFlow.todayCount === 0 && !tuneFlow.tuningData) {
        try {
            const resp = await fetch('/api/tuning/history?limit=20');
            const data = await resp.json();
            const sessions = data.sessions || data || [];
            if (Array.isArray(sessions)) {
                const todayStr = _tfTodayStr();
                tuneFlow.todayCount = sessions.filter(t => t.date === todayStr && t.context).length;
            }
        } catch (e) {}
    }

    if (_tfHomeBtnInterval) { clearInterval(_tfHomeBtnInterval); _tfHomeBtnInterval = null; }

    if (!_tfCanTuneAgain(tuneFlow.todayCount) && tuneFlow.todayCount > 0) {
        // Free tier gated — grey out, show countdown, open upgrade modal on click
        btn.classList.add('home-nav-btn-disabled');
        label.innerHTML = `${_tfCountdownStr()}<span style="display:block;font-size:0.6rem;opacity:0.6;margin-top:2px;">Go Operator</span>`;
        btn.onclick = function() { _tfUpgrade(); };

        // Update countdown every second
        _tfHomeBtnInterval = setInterval(() => {
            const l = document.getElementById('homeTuneLabel');
            if (!l) { clearInterval(_tfHomeBtnInterval); return; }
            const { ms } = _tfMidnightCountdown();
            if (ms <= 0) {
                clearInterval(_tfHomeBtnInterval);
                _tfUpdateHomeButton(); // midnight reset
                return;
            }
            l.innerHTML = `${_tfCountdownStr()}<span style="display:block;font-size:0.6rem;opacity:0.6;margin-top:2px;">Get unlimited</span>`;
        }, 1000);
    } else {
        // Available — normal state
        btn.classList.remove('home-nav-btn-disabled');
        label.textContent = 'Tune';
        btn.onclick = function() { tuneNow(); };
    }
}

// The tune-limit upsell opens the full upgrade modal (same as "Go further"),
// falling back to the tune-specific prompt only if it isn't loaded.
function _tfUpgrade() {
    if (typeof showUpgradeModal === 'function') return showUpgradeModal();
    return _tfShowUpgradePrompt();
}

function _tfShowUpgradePrompt() {
    // Remove existing modal if present
    const existing = document.getElementById('tf-upgrade-overlay');
    if (existing) { existing.remove(); return; }

    const overlay = document.createElement('div');
    overlay.id = 'tf-upgrade-overlay';
    overlay.className = 'upgrade-overlay';
    overlay.onclick = function(e) { if (e.target === overlay) overlay.remove(); };

    overlay.innerHTML = `
        <div class="upgrade-modal">
            <button class="upgrade-close" onclick="document.getElementById('tf-upgrade-overlay').remove()">&times;</button>
            <div class="upgrade-modal-icon" style="color:var(--daily-freq)">
                <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                    <circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>
                </svg>
            </div>
            <h2 class="upgrade-modal-title">Unlimited tuning with Operator</h2>
            <p class="upgrade-modal-sub">You've used today's free tune. Become an Operator to tune whenever you need to realign — plus the full platform.</p>
            <div class="upgrade-features">
                <div class="upgrade-feature-row">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--daily-freq)" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>
                    <span>Unlimited tunings, whenever you need realignment</span>
                </div>
                <div class="upgrade-feature-row">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--daily-freq)" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>
                    <span>Creation Flows, calendar, files, tasks</span>
                </div>
                <div class="upgrade-feature-row">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--daily-freq)" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>
                    <span>Marketplace access — and the path to your own Cove</span>
                </div>
            </div>
            <div class="upgrade-price">
                <span class="upgrade-price-amount" style="color:var(--daily-freq)">$12</span>
                <span class="upgrade-price-period">/month</span>
            </div>
            <button class="upgrade-cta-btn" onclick="document.getElementById('tf-upgrade-overlay').remove(); if (typeof showUpgradeModal === 'function') showUpgradeModal();">
                Become an Operator
            </button>
            <p class="upgrade-fine">Next free tune resets at midnight &middot; ${_tfCountdownStr()}</p>
        </div>
    `;
    document.body.appendChild(overlay);
}


async function _tfStartProCheckout() {
    const btn = document.getElementById('tfUpgradeProBtn');
    if (!btn) return;
    btn.disabled = true;
    btn.textContent = 'Loading...';

    try {
        // Get user info from MC
        const email = MC.presence?.email;
        const name = MC.presence?.display_name || MC.presence?.username || '';
        if (!email) {
            alert('Please sign in to upgrade.');
            btn.disabled = false;
            btn.textContent = 'Upgrade to Pro';
            return;
        }

        // Get referral code if user was referred
        let ref = null;
        try {
            const refRes = await fetch('/api/account/referral-code');
            const refData = await refRes.json();
            ref = refData.ref || null;
        } catch(e) {}

        // Create Stripe checkout session via Socrates commerce API
        const res = await fetch('https://api.lucidcove.org/api/commerce/checkout/session', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                plan_type: 'pro_monthly',
                email: email,
                name: name,
                ref: ref,
                success_url: window.location.origin + '/?upgraded=pro',
                cancel_url: window.location.origin + '/',
            }),
        });

        const data = await res.json();

        if (data.success && data.url) {
            // Redirect to Stripe Checkout
            window.location.href = data.url;
        } else {
            alert(data.error || 'Unable to start checkout. Please try again.');
            btn.disabled = false;
            btn.textContent = 'Upgrade to Pro';
        }
    } catch(e) {
        console.error('[tune-flow] Checkout error:', e);
        alert('Unable to connect to payment system. Please try again.');
        btn.disabled = false;
        btn.textContent = 'Upgrade to Pro';
    }
}

// ═══════════════════════════════════════════════════════════════════════════════
// ECHO HISTORY — Previous tunings (Broadcast Log)
// Shows last 5 past tunings below completed view. Tap opens full tuning modal.
// "See full tuning history" opens paginated modal with all past sessions.
// ═══════════════════════════════════════════════════════════════════════════════

// Cache fetched history so modal doesn't re-fetch
let _tfHistoryCache = null;

async function _loadTuneHistory() {
    const container = document.getElementById('tfHistory');
    if (!container) return;

    try {
        const resp = await fetch('/api/tuning/history?limit=50');
        const data = await resp.json();
        const sessions = data.sessions || [];

        // Filter out only the currently displayed session (not all of today)
        const activeId = tuneFlow._activeSessionId || '';
        const past = sessions.filter(s => s.session_id !== activeId);
        _tfHistoryCache = past;

        if (!past.length) {
            container.innerHTML = '';
            return;
        }

        // Show only last 5
        const recent = past.slice(0, 5);

        let html = '<div class="tf-history">';
        html += '<div class="tf-history-header">Previous Tunings</div>';
        html += _tfRenderHistRows(recent);

        if (past.length > 5) {
            html += `<button class="tf-hist-see-all" onclick="_tfOpenFullHistory()">See full tuning history</button>`;
        }

        html += '</div>';
        container.innerHTML = html;
    } catch (e) {
        container.innerHTML = '';
    }
}

function _tfRenderHistRows(sessions) {
    let html = '';
    let lastDate = '';

    sessions.forEach((s, idx) => {
        const date = s.date || '';
        if (date !== lastDate) {
            html += `<div class="tf-history-date">${ESC(_tfFormatHistDate(date))}</div>`;
            lastDate = date;
        }

        const rawFreq = s.frequency || '';
        const freq = rawFreq.charAt(0).toUpperCase() + rawFreq.slice(1).toLowerCase();
        const freqColor = typeof lpColor === 'function' ? lpColor(rawFreq) : 'var(--accent)';
        const principle = s.principle || '';
        const dateLabel = _tfFormatHistDate(date);
        const timeStr = s.time ? s.time.substring(0, 5) : '';

        const fBadge = typeof lpFreqBadgeHTML === 'function'
            ? lpFreqBadgeHTML(rawFreq)
            : `<span class="freq-badge" style="color:${freqColor};">${ESC(freq)}</span>`;

        html += `<div class="tf-hist-row" data-idx="${idx}" onclick="_tfOpenTuningModal(${idx})">
            <div class="tf-hist-summary">
                ${fBadge}
                <span class="tf-hist-principle" style="color:${freqColor};">${ESC(principle)}</span>
                <span class="tf-hist-date-time">${ESC(dateLabel)} ${ESC(timeStr)}</span>
            </div>
        </div>`;
    });

    return html;
}

async function _tfOpenTuningModal(idx) {
    const sessions = _tfHistoryCache;
    if (!sessions || !sessions[idx]) return;
    const s = sessions[idx];
    await _tfShowTuningDetail(s);
}

async function _tfShowTuningDetail(s) {
    // Remove existing modal
    const existing = document.getElementById('tf-tuning-modal');
    if (existing) existing.remove();

    // Stop any playing audio to avoid player state conflicts
    if (typeof otAudio !== 'undefined' && otAudio && !otAudio.paused) {
        otAudio.pause();
        otAudio.currentTime = 0;
    }
    if (typeof _otPendingPlay !== 'undefined') _otPendingPlay = false;

    const rawFreq = s.frequency || '';
    const freq = rawFreq.charAt(0).toUpperCase() + rawFreq.slice(1).toLowerCase();
    const freqColor = typeof lpColor === 'function' ? lpColor(rawFreq) : 'var(--accent)';
    const principle = s.principle || '';
    const key = s.tuning_key || '';
    const context = s.context || '';
    const mode = s.initial_state || s.entry_mode || '';
    const audioUrl = s.audio_url || '';
    const dateLabel = _tfFormatHistDate(s.date || '');
    const timeStr = s.time ? s.time.substring(0, 5) : '';

    // Coaching and practice from static templates (same as completed view)
    const coaching = TUNE_COACHING[freq] || '';
    const templateKey = TUNE_TEMPLATE_MAP[freq] || 'coherence';
    const practice = TUNE_PRACTICE_TEMPLATES[templateKey];

    const fBadge = typeof lpFreqBadgeHTML === 'function'
        ? lpFreqBadgeHTML(rawFreq)
        : `<span class="freq-badge" style="color:${freqColor};">${ESC(freq)}</span>`;

    const overlay = document.createElement('div');
    overlay.id = 'tf-tuning-modal';
    overlay.className = 'tf-modal-overlay';
    overlay.onclick = function(e) { if (e.target === overlay) _tfCloseDetailModal(); };

    overlay.innerHTML = `
        <div class="tf-modal">
            <button class="tf-modal-close" onclick="_tfCloseDetailModal()">&times;</button>

            <div class="tf-modal-header">
                ${fBadge}
                <div class="tf-modal-freq" style="color:${freqColor};">${ESC(freq)}</div>
                <div class="tf-modal-principle" style="color:${freqColor};">${ESC(principle)}</div>
                <div class="tf-modal-meta">${ESC(dateLabel)} ${ESC(timeStr)}${context ? ' · ' + ESC(context) : ''}${mode ? ' · ' + ESC(mode) : ''}</div>
            </div>

            ${coaching ? `<div class="tf-modal-section">
                <span class="tf-modal-label">Coaching</span>
                <p class="tf-modal-coaching">${coaching}</p>
            </div>` : ''}

            ${practice ? `<div class="tf-modal-section">
                <span class="tf-modal-label">Practice</span>
                <div class="tf-modal-practice">
                    <div class="tf-practice-step"><span class="tf-practice-num" style="background:${freqColor};">1</span><div><strong>${practice.step1.title}</strong> — ${practice.step1.text}</div></div>
                    <div class="tf-practice-step"><span class="tf-practice-num" style="background:${freqColor};">2</span><div><strong>${practice.step2.title}</strong> — ${practice.step2.text}</div></div>
                    <div class="tf-practice-step"><span class="tf-practice-num" style="background:${freqColor};">3</span><div><strong>${practice.step3.title}</strong> — ${practice.step3.text}</div></div>
                </div>
            </div>` : ''}

            ${key ? `<div class="tf-modal-section">
                <span class="tf-modal-label">Tuning Key</span>
                <blockquote class="tf-modal-key" style="border-left-color:${freqColor};">"${ESC(key)}"</blockquote>
            </div>` : ''}

            ${audioUrl ? `<div class="tf-modal-section">
                <span class="tf-modal-label">Tuning Stream</span>
                <div id="tfModalPlayer"></div>
            </div>` : ''}
        </div>
    `;

    document.body.appendChild(overlay);

    // Init player in the modal — loads the tuning stream for this frequency
    if (audioUrl && typeof otSetPlaylist === 'function') {
        const freqUpper = rawFreq.toUpperCase();
        const fColor = (typeof OT_FREQ_COLORS !== 'undefined' && OT_FREQ_COLORS[freqUpper]) || 'var(--accent)';
        const folder = audioUrl.split('/').slice(-2, -1)[0] || '';

        try {
            await _tfBuildModalPlaylist(s, freq, folder, fColor);
        } catch (e) {
            console.error('[tune-history] Player init error:', e);
        }
    }
}

function _tfCloseDetailModal() {
    // Stop audio when closing history modal — avoids ghost player state
    if (typeof otAudio !== 'undefined' && otAudio && !otAudio.paused) {
        otAudio.pause();
        otAudio.currentTime = 0;
    }
    if (typeof hideMiniPlayer === 'function') hideMiniPlayer();
    const modal = document.getElementById('tf-tuning-modal');
    if (modal) modal.remove();
}

async function _tfBuildModalPlaylist(s, freq, signalFolder, freqColor) {
    let tracks = [];
    let loaded = false;

    // Try CDN frequency playlist first
    if (freq && typeof OT_PLAYLIST_CDN !== 'undefined') {
        try {
            const res = await fetch(OT_PLAYLIST_CDN + '/' + freq.toLowerCase() + '.json');
            if (res.ok) {
                const playlist = await res.json();
                if (Array.isArray(playlist) && playlist.length > 0) {
                    tracks = playlist.map(t => {
                        const fn = t.filename || t.file || '';
                        const fd = t.folder || t.signal_type || signalFolder;
                        const pr = t.principle || t.title || fn.replace(/_/g, ' ').replace(/\.mp3$/, '');
                        return { title: pr + ' (' + fd.replace(/_Signal$/, '').replace(/_/g, ' ') + ' Signal Echo)', filename: fn, folder: fd, principle: pr };
                    });
                    loaded = true;
                }
            }
        } catch (e) {}
    }

    // Fallback to signal folder tracks
    if (!loaded && typeof otBuildTracks === 'function') {
        tracks = otBuildTracks(signalFolder);
    }

    // Shuffle
    for (let i = tracks.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [tracks[i], tracks[j]] = [tracks[j], tracks[i]];
    }

    // Put this tuning's echo first
    if (s.principle) {
        const idx = tracks.findIndex(t => t.principle && t.principle.toLowerCase() === s.principle.toLowerCase());
        if (idx > 0) { const m = tracks.splice(idx, 1)[0]; tracks.unshift(m); }
    }

    otSetPlaylist(tracks, {
        source: 'history',
        label: freq + ' Tuning Stream',
        freqColor: freqColor,
        autoplay: false,
        mountId: 'tfModalPlayer',
    });
}

// ── Full History Modal ──────────────────────────────────────────────────────

function _tfOpenFullHistory() {
    const existing = document.getElementById('tf-fullhist-modal');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.id = 'tf-fullhist-modal';
    overlay.className = 'tf-modal-overlay';
    overlay.onclick = function(e) { if (e.target === overlay) overlay.remove(); };

    overlay.innerHTML = `
        <div class="tf-modal tf-modal-full">
            <button class="tf-modal-close" onclick="document.getElementById('tf-fullhist-modal').remove()">&times;</button>
            <div class="tf-modal-header">
                <div class="tf-modal-freq">Previous Tunings</div>
            </div>
            <div class="tf-fullhist-list" id="tfFullHistList"></div>
        </div>
    `;

    document.body.appendChild(overlay);
    _tfRenderFullHistPage(0);
}

const TF_HIST_PAGE_SIZE = 10;

function _tfRenderFullHistPage(offset) {
    const list = document.getElementById('tfFullHistList');
    if (!list) return;

    const sessions = _tfHistoryCache || [];
    const page = sessions.slice(offset, offset + TF_HIST_PAGE_SIZE);

    if (!page.length && offset === 0) {
        list.innerHTML = '<div class="empty-msg">No previous tunings.</div>';
        return;
    }

    // Render rows — tapping opens the detail modal on top
    let html = '';
    let lastDate = '';

    page.forEach((s, i) => {
        const globalIdx = offset + i;
        const date = s.date || '';
        if (date !== lastDate) {
            html += `<div class="tf-history-date">${ESC(_tfFormatHistDate(date))}</div>`;
            lastDate = date;
        }

        const rawFreq = s.frequency || '';
        const freq = rawFreq.charAt(0).toUpperCase() + rawFreq.slice(1).toLowerCase();
        const freqColor = typeof lpColor === 'function' ? lpColor(rawFreq) : 'var(--accent)';
        const principle = s.principle || '';
        const dateLabel = _tfFormatHistDate(date);
        const timeStr = s.time ? s.time.substring(0, 5) : '';

        const fBadge = typeof lpFreqBadgeHTML === 'function'
            ? lpFreqBadgeHTML(rawFreq)
            : `<span class="freq-badge" style="color:${freqColor};">${ESC(freq)}</span>`;

        html += `<div class="tf-hist-row" onclick="_tfOpenTuningModal(${globalIdx})">
            <div class="tf-hist-summary">
                ${fBadge}
                <span class="tf-hist-principle" style="color:${freqColor};">${ESC(principle)}</span>
                <span class="tf-hist-date-time">${ESC(dateLabel)} ${ESC(timeStr)}</span>
            </div>
        </div>`;
    });

    // Show more button
    if (offset + TF_HIST_PAGE_SIZE < sessions.length) {
        const nextOffset = offset + TF_HIST_PAGE_SIZE;
        html += `<button class="tf-hist-see-all" onclick="_tfAppendFullHistPage(${nextOffset})">Show more</button>`;
    }

    if (offset === 0) {
        list.innerHTML = html;
    } else {
        // Append mode — remove old "show more" button first
        const oldBtn = list.querySelector('.tf-hist-see-all');
        if (oldBtn) oldBtn.remove();
        list.insertAdjacentHTML('beforeend', html);
    }
}

function _tfAppendFullHistPage(offset) {
    _tfRenderFullHistPage(offset);
}

// ── Helpers ─────────────────────────────────────────────────────────────────

function _tfFormatHistDate(dateStr) {
    if (!dateStr) return '';
    try {
        const [y, m, d] = dateStr.split('-').map(Number);
        const date = new Date(y, m - 1, d);
        const today = new Date();
        const yesterday = new Date(today);
        yesterday.setDate(yesterday.getDate() - 1);

        if (date.toDateString() === today.toDateString()) return 'Today';
        if (date.toDateString() === yesterday.toDateString()) return 'Yesterday';

        const diffDays = Math.floor((today - date) / 86400000);
        if (diffDays < 7) return formatDate(dateStr, { weekday: 'long' });

        return formatDate(dateStr, { month: 'short', day: 'numeric' });
    } catch (e) {
        return dateStr;
    }
}

// ═══════════════════════════════════════════════════════════════════════════════
// TUNING STREAM — Uses shared player template (tuning-panel.js)
// Builds frequency playlist and hands off to otSetPlaylist with mountId
// ═══════════════════════════════════════════════════════════════════════════════

async function _tfsInit(data) {
    const rawFreq = data.frequency || '';
    const freq = rawFreq.charAt(0).toUpperCase() + rawFreq.slice(1).toLowerCase();
    const signalFolder = (typeof otSignalToFolder === 'function') ? otSignalToFolder(data.signal_type) : 'Raw_Signal';

    // Build track list — CDN frequency playlist or signal-type fallback
    let tracks = [];
    let loaded = false;
    if (freq && typeof OT_PLAYLIST_CDN !== 'undefined') {
        try {
            const res = await fetch(OT_PLAYLIST_CDN + '/' + freq.toLowerCase() + '.json');
            if (res.ok) {
                const playlist = await res.json();
                if (Array.isArray(playlist) && playlist.length > 0) {
                    tracks = playlist.map(t => {
                        const filename = t.filename || t.file || '';
                        const folder = t.folder || t.signal_type || signalFolder;
                        const principle = t.principle || t.title || filename.replace(/_/g, ' ').replace(/\.mp3$/, '');
                        return { title: principle + ' (' + folder.replace(/_Signal$/, '').replace(/_/g, ' ') + ' Signal Echo)', filename, folder, principle };
                    });
                    loaded = true;
                }
            }
        } catch (e) {}
    }
    if (!loaded && typeof otBuildTracks === 'function') {
        tracks = otBuildTracks(signalFolder);
    }

    // Shuffle
    for (let i = tracks.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [tracks[i], tracks[j]] = [tracks[j], tracks[i]];
    }

    // Put tuning echo first
    if (data.principle) {
        const idx = tracks.findIndex(t => t.principle && t.principle.toLowerCase() === data.principle.toLowerCase());
        if (idx > 0) { const m = tracks.splice(idx, 1)[0]; tracks.unshift(m); }
    }

    // Get frequency color
    const freqUpper = freq.toUpperCase();
    const freqColor = (typeof OT_FREQ_COLORS !== 'undefined' && OT_FREQ_COLORS[freqUpper]) || 'var(--accent)';

    // Render player template + load tracks (no autoplay — user presses play)
    if (typeof otSetPlaylist === 'function') {
        otSetPlaylist(tracks, {
            source: 'tune',
            label: freq + ' Tuning Stream',
            freqColor: freqColor,
            autoplay: false,
            mountId: 'tfPlayerMount'
        });
    }
}
