// =============================================================================
// onboarding.js — Interactive highlight walkthrough for new users
// Loaded on-demand by core.js (not needed at parse time).
// =============================================================================

// =============================================================================
// Onboarding — interactive highlight walkthrough
// =============================================================================

const _onboardingSteps = [
    {
        title: "Welcome to Lucid Tuner",
        body: "This is your daily tuning space. Each day, a frequency is selected based on 22 core principles. Let's walk through the basics.",
        target: null,
    },
    {
        title: "Playlists — Start Here",
        body: "This is the best way to begin. Curated playlists organized by frequency — real songs across genres, all carrying these principles in their lyrics. Put one on while you work, drive, or wind down. Over time, these songs become part of how you tune.",
        target: () => document.querySelector('.nav-item[data-tab="playlists"], .tab[data-tab="playlists"]'),
        action: () => switchToTab('playlists'),
    },
    {
        title: "Today's Tuning",
        body: "This badge shows the daily orchestrated frequency — selected each morning for the entire system. It's the same signal everyone receives. The Latest Tuning section and content mirrors on your home screen all reflect this frequency.",
        target: () => document.getElementById('header-freq'),
        action: () => switchToTab('home'),
    },
    {
        title: "Tune — Your Personal Practice",
        body: "This is your personal tuning. A short guided practice that reads where you are right now and gives you something specific to work with. It's separate from the daily orchestrated frequency — this one is just for you.",
        target: () => document.getElementById('homeTuneBtn'),
    },
    {
        title: "The Tune Tab",
        body: "After you complete a tuning, it lives here. The Tune tab always shows your most recent personal tuning. Each time you tune, it replaces what's here with your latest session.",
        target: () => document.querySelector('.nav-item[data-tab="tune"], .tab[data-tab="tune"]'),
    },
    {
        title: "Latest Tuning & Mirrors",
        body: "Your home screen shows the daily orchestrated tuning and a content mirror — a curated passage from another tradition that connects to today's frequency. Scripture, philosophy, music.",
        target: () => document.getElementById('overviewTuning'),
        action: () => switchToTab('home'),
    },
    {
        title: "You're Set",
        body: "You choose what to do next. Listen to a playlist. Tune with the Field. Go Deeper into the principles. There's no right order. You can revisit this tour anytime from the ? button.",
        target: null,
    },
];

let _onboardingStep = 0;
let _onboardingOverlay = null;

function _startOnboarding() {
    _onboardingStep = 0;
    _renderOnboardingStep();
}

function _renderOnboardingStep() {
    // Remove previous overlay
    if (_onboardingOverlay) { _onboardingOverlay.remove(); _onboardingOverlay = null; }
    if (_onboardingStep >= _onboardingSteps.length) {
        // Mark as seen
        try { fetch('/api/settings/features', { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ onboarding_seen: true }) }); } catch(e) {}
        return;
    }

    const step = _onboardingSteps[_onboardingStep];
    const total = _onboardingSteps.length;

    // Run any pre-step action (like switching tabs)
    if (step.action) step.action();

    // Create overlay
    _onboardingOverlay = document.createElement('div');
    _onboardingOverlay.className = 'onboarding-overlay';
    _onboardingOverlay.onclick = (e) => { if (e.target === _onboardingOverlay) _dismissOnboarding(); };

    const targetEl = step.target ? step.target() : null;

    // Position the tooltip — always centered, highlight ring on target
    let tooltipStyle = 'top:50%;left:50%;transform:translate(-50%,-50%);';
    if (targetEl) {
        const rect = targetEl.getBoundingClientRect();
        // Highlight cutout — add a glowing ring around the target
        const highlight = document.createElement('div');
        highlight.className = 'onboarding-highlight';
        highlight.style.cssText = `top:${rect.top - 6}px;left:${rect.left - 6}px;width:${rect.width + 12}px;height:${rect.height + 12}px;`;
        _onboardingOverlay.appendChild(highlight);
    }

    const isLast = _onboardingStep === total - 1;
    const isFirst = _onboardingStep === 0;

    _onboardingOverlay.innerHTML += `
        <div class="onboarding-tooltip" style="${tooltipStyle}">
            <div class="onboarding-title">${step.title}</div>
            <div class="onboarding-body">${step.body}</div>
            <div class="onboarding-footer">
                <span class="onboarding-dots">${_onboardingStep + 1} / ${total}</span>
                <div class="onboarding-btns">
                    ${isFirst ? '' : '<button class="onboarding-btn onboarding-back" onclick="_onboardingPrev()">Back</button>'}
                    <button class="onboarding-btn onboarding-next" onclick="_onboardingNext()">${isLast ? 'Done' : 'Next'}</button>
                </div>
            </div>
        </div>`;

    document.body.appendChild(_onboardingOverlay);
}

function _onboardingNext() {
    _onboardingStep++;
    _renderOnboardingStep();
}

function _onboardingPrev() {
    if (_onboardingStep > 0) _onboardingStep--;
    _renderOnboardingStep();
}

function _dismissOnboarding() {
    if (_onboardingOverlay) { _onboardingOverlay.remove(); _onboardingOverlay = null; }
}
