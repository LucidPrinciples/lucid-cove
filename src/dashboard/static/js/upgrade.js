// =============================================================================
// upgrade.js — Tier ladder, upgrade modal, Stripe checkout, upgrade CTA
// Loaded on-demand by core.js (not needed at parse time).
// =============================================================================

function adminUpgradeTier() {
    // Admin shortcut: cycle through the live ladder via URL param reload.
    // Ladder (locked 2026-06-23): Tuner(free) → Operator → Cove. Pro + Presence retired.
    const current = new URLSearchParams(location.search).get('tier') || 'free';
    const cycle = { free: 'operator', operator: 'cove', cove: null };
    const next = cycle[current];
    if (next) {
        location.href = location.pathname + '?tier=' + next;
    } else {
        location.href = location.pathname;  // back to tuner (no param = container default)
    }
}

// ── Tier-aware upgrade modal ──────────────────────────────────────────────
// Shows the tiers above the user's current tier. The ladder is three rungs:
// Tuner(free) → Operator → Cove. "Pro" and "Presence" are RETIRED (Pro folded into
// Operator; a solo Presence is now just a Cove with the team off). Cove ships in two
// configs (solo / + team); HOSTING (host-with-us vs self-host) is a fork at Cove
// signup, orthogonal to tier — both run the same provisioner (#167).
var TIER_LADDER = [
    {
        key: 'operator',
        title: 'Operator',
        subtitle: 'The complete platform — tuning, Creation Flows, calendar, files, tasks. No agent yet.',
        price: '$12',
        active: true,
        features: [
            'Unlimited personal tunings',
            'Connect — private, secure messaging that replaces group chat and email between members',
            'Marketplace — buy and sell',
            'Cloud file storage (Documents, Projects, Notes)',
            'Calendar and task management',
            'Full Creation Flows',
        ],
    },
    {
        key: 'cove',
        title: 'Cove',
        subtitle: 'Add intelligence. You and your Agent form a Presence — with the option of a full team: Stuart + 9 specialists.',
        price: 'from $29',
        infra: 'Self-host free · hosted $59 Iceland / $29 EU',
        active: true,
        fork: true,  // host-with-us vs self-host choice instead of a single checkout
        features: [
            'Everything in Operator',
            'Your personal agent — persistent memory, daily tuning, BYOK',
            'Connect: your own portable Matrix identity',
            'Turn the team on for Stuart + 9 specialist agents',
            'Website, video and voice pipelines; marketplace (buy + sell)',
            'Host with us (we run it on the VPS) or self-host the open-source stack',
        ],
    },
];

// Legacy lookup (used by _renderUpgradeCTA). Keyed by CURRENT tier → the next rung.
// Legacy 'pro'/'presence' accounts are mapped forward onto the live ladder.
var UPGRADE_TIERS = {
    free: { title: 'Go further', subtitle: 'Become an Operator for the full platform — or add your intelligence and build a Cove.', price: '$12', next: 'operator' },
    pro: { title: 'Go further', subtitle: 'Become an Operator for the full platform — or add your intelligence and build a Cove.', price: '$12', next: 'operator' },
    operator: { title: 'Build a Cove', subtitle: 'Add your intelligence — your own agent, and the team when you want it.', price: 'from $29', next: 'cove' },
    presence: { title: 'Build a Cove', subtitle: 'Add the team — Stuart + 9 specialists, business tools.', price: 'from $29', next: 'cove' },
};

// Full order kept (incl. retired rungs) so a legacy 'pro'/'presence' account still
// filters correctly to the rungs above it.
var TIER_ORDER = ['free', 'pro', 'operator', 'presence', 'cove'];

function showUpgradeModal() {
    // Remove existing modal if any
    let existing = document.getElementById('upgrade-modal-overlay');
    if (existing) { existing.remove(); return; }

    // Determine current tier
    var currentTier = MC.tier?.current || 'free';
    var currentIdx = TIER_ORDER.indexOf(currentTier);
    if (currentIdx < 0) currentIdx = 0;

    // Get all tiers above current
    var availableTiers = TIER_LADDER.filter(function(t) {
        return TIER_ORDER.indexOf(t.key) > currentIdx;
    });
    if (availableTiers.length === 0) return;  // Already at highest tier

    const overlay = document.createElement('div');
    overlay.id = 'upgrade-modal-overlay';
    overlay.className = 'upgrade-overlay';
    overlay.onclick = function(e) { if (e.target === overlay) overlay.remove(); };

    const fc = lpFreqColor(window._todayTuning?.frequency || 'MOMENTUM');
    const accent = fc.primary || 'var(--daily-freq)';

    // Build tier cards
    var cardsHtml = availableTiers.map(function(tier, i) {
        if (tier.active) {
            // Active tier — full card with checkout
            var featuresHtml = tier.features.map(function(f) {
                return '<div class="upgrade-feature-row">' +
                    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="' + accent + '" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>' +
                    '<span>' + f + '</span></div>';
            }).join('');

            // Cove forks into host-with-us vs self-host; every other active tier is a
            // single Stripe checkout.
            var actionHtml = tier.fork
                ? '<div class="upgrade-fork">' +
                      '<button class="upgrade-cta-btn" style="background:' + accent + '" onclick="_coveHosting(\'us\')">' +
                          'Host it for me' +
                      '</button>' +
                      '<button class="upgrade-cta-btn-secondary" onclick="_coveHosting(\'self\')">' +
                          'I\'ll self-host (open source, free)' +
                      '</button>' +
                  '</div>'
                : '<button class="upgrade-cta-btn" style="background:' + accent + '" onclick="_upgradeStartCheckout(\'' + tier.key + '\')">' +
                      'Upgrade to ' + tier.title +
                  '</button>';
            return '<div class="upgrade-tier-card upgrade-tier-active">' +
                '<div class="upgrade-modal-icon" style="color:' + accent + '">' +
                    '<svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>' +
                '</div>' +
                '<h2 class="upgrade-modal-title">' + tier.title + '</h2>' +
                '<p class="upgrade-modal-sub">' + tier.subtitle + '</p>' +
                '<div class="upgrade-features">' + featuresHtml + '</div>' +
                '<div class="upgrade-price">' +
                    '<span class="upgrade-price-amount" style="color:' + accent + '">' + tier.price + '</span>' +
                    '<span class="upgrade-price-period">/month</span>' +
                '</div>' +
                (tier.infra ? '<div class="upgrade-coming-infra">' + tier.infra + '</div>' : '') +
                actionHtml +
            '</div>';
        } else {
            // Coming Soon tier — compact card
            var infraNote = tier.infra ? '<div class="upgrade-coming-infra">' + tier.infra + '</div>' : '';
            return (i > 0 ? '<div class="upgrade-divider"></div>' : '') +
                '<div class="upgrade-coming-soon">' +
                    '<div class="upgrade-coming-title">' + tier.title + ' — ' + tier.price + '/mo</div>' +
                    infraNote +
                    '<div class="upgrade-coming-desc">' + tier.subtitle + '</div>' +
                    '<span class="upgrade-coming-badge">Coming Soon</span>' +
                '</div>';
        }
    }).join('');

    // Frame the rung they're on now, so the modal reads as the full ladder:
    // Tuner (here) → Operator → Cove (add your intelligence).
    var currentRung = {
        free:     { name: 'Lucid Tuner · Free', sub: 'A daily tuning practice and your music — free, forever. Here\'s where you can go next:' },
        operator: { name: 'Operator', sub: 'You\'ve got the full platform. The next step is your own intelligence — a Cove:' },
    }[currentTier] || { name: 'your plan', sub: 'Where you can go next:' };
    var currentHtml =
        '<div style="text-align:center;padding-bottom:10px;">' +
            '<div style="display:inline-block;font-size:0.7rem;font-weight:700;text-transform:uppercase;letter-spacing:0.06em;color:' + accent + ';">You\'re on ' + currentRung.name + '</div>' +
            '<p style="margin:6px 0 0;font-size:0.85rem;color:#9a9aaf;line-height:1.5;">' + currentRung.sub + '</p>' +
        '</div>';

    overlay.innerHTML =
        '<div class="upgrade-modal">' +
            '<button class="upgrade-close" onclick="document.getElementById(\'upgrade-modal-overlay\').remove()">&times;</button>' +
            currentHtml +
            cardsHtml +
            '<button class="upgrade-cta-btn-secondary" onclick="window.open(\'https://lucidcove.org/pricing\',\'_blank\')">' +
                'View All Plans' +
            '</button>' +
            '<p class="upgrade-fine">No lock-in. Cancel anytime.<br>A Cove runs either hosted by us (on the VPS, infra at cost) or self-hosted on your own hardware — the open-source stack is free.</p>' +
        '</div>';
    document.body.appendChild(overlay);
}

// Cove signup fork (#167): host-with-us (we provision + run it on the VPS, via
// Stripe) vs self-host (download the open-source stack, run the provisioner CLI).
// Both stand up the SAME Cove from the same engine — only the trigger differs.
function _coveHosting(mode) {
    if (mode === 'self') {
        // Self-host: the open-source quickstart (clone, fill cove.config.yaml, run the
        // provisioner, docker compose up). No checkout.
        window.open('https://lucidcove.org/self-host', '_blank');
        return;
    }
    // Host-with-us: name the Cove + claim a @handle (globally unique, checked against
    // the Hub registrar) BEFORE checkout, so they ride in Stripe metadata and the
    // webhook can auto-provision the Cove on the VPS (#167).
    const modal = document.querySelector('.upgrade-modal');
    if (!modal) return;
    const fc = lpFreqColor(window._todayTuning?.frequency || 'MOMENTUM');
    const accent = fc.primary || 'var(--daily-freq)';
    modal.innerHTML =
        '<button class="upgrade-close" onclick="document.getElementById(\'upgrade-modal-overlay\').remove()">&times;</button>' +
        '<h2 class="upgrade-modal-title">Name your Cove</h2>' +
        '<p class="upgrade-modal-sub">Your family\'s identity in the Haven. Both have to be unique — and they\'re yours for good.</p>' +
        '<div style="margin:1rem 0;display:flex;flex-direction:column;gap:0.75rem">' +
            '<input id="ch-cove-name" placeholder="Cove name (e.g. Riverside)" autocomplete="off" ' +
                'style="padding:0.7rem;border-radius:8px;border:1px solid #2a2d3a;background:#1a1d27;color:#e1e4ea;font-size:15px">' +
            '<input id="ch-handle" placeholder="Your @handle (e.g. sam)" autocomplete="off" ' +
                'style="padding:0.7rem;border-radius:8px;border:1px solid #2a2d3a;background:#1a1d27;color:#e1e4ea;font-size:15px">' +
            '<div id="ch-status" style="min-height:18px;font-size:12.5px"></div>' +
        '</div>' +
        '<div style="margin:0 0 0.75rem">' +
            '<div style="font-size:12.5px;color:#9a9aaf;margin-bottom:0.4rem">Where should we run it?</div>' +
            '<div style="display:flex;gap:0.5rem">' +
                '<button type="button" id="ch-rg-iceland" onclick="_chPickRegion(\'iceland\')" ' +
                    'style="flex:1;padding:0.6rem;border-radius:8px;border:1px solid ' + accent + ';background:rgba(92,225,230,0.08);color:#e1e4ea;font-size:13px;cursor:pointer;text-align:left">' +
                    '<b>Iceland · $59</b><br><span style="font-size:11px;color:#9a9aaf">Constitutional privacy</span></button>' +
                '<button type="button" id="ch-rg-eu" onclick="_chPickRegion(\'eu\')" ' +
                    'style="flex:1;padding:0.6rem;border-radius:8px;border:1px solid #2a2d3a;background:#1a1d27;color:#e1e4ea;font-size:13px;cursor:pointer;text-align:left">' +
                    '<b>EU · $29</b><br><span style="font-size:11px;color:#9a9aaf">GDPR</span></button>' +
            '</div>' +
        '</div>' +
        '<button class="upgrade-cta-btn" id="ch-go" style="background:' + accent + '" disabled ' +
            'onclick="_coveHostingCheckout()">Continue to checkout</button>' +
        '<p style="font-size:11.5px;color:#9a9aaf;text-align:center;margin-top:10px;line-height:1.5;">' +
            'We hand-build each Cove right now, so yours is ready within 24 hours of checkout — ' +
            'we\'ll email you the moment it\'s live. Your Cove name and @handle are locked in today.</p>';
    window._chRegion = 'iceland';  // default jurisdiction (primary)
    const nameEl = document.getElementById('ch-cove-name');
    const handleEl = document.getElementById('ch-handle');
    const statusEl = document.getElementById('ch-status');
    const goBtn = document.getElementById('ch-go');
    let t = null;
    function check() {
        clearTimeout(t);
        goBtn.disabled = true;
        const name = nameEl.value.trim();
        const handle = handleEl.value.trim().replace(/^@/, '');
        if (!name || !handle) { statusEl.textContent = ''; return; }
        statusEl.textContent = 'Checking availability…'; statusEl.style.color = accent;
        t = setTimeout(async () => {
            try {
                const r = await fetch('https://app.lucidcove.org/api/registry/availability?name=' +
                    encodeURIComponent(name) + '&handle=' + encodeURIComponent(handle));
                const d = await r.json();
                const nameOk = d.name_available !== false, handleOk = d.handle_available !== false;
                if (nameOk && handleOk) {
                    statusEl.textContent = '✓ Both available'; statusEl.style.color = accent;
                    goBtn.disabled = false;
                } else {
                    statusEl.textContent = (!nameOk ? 'Cove name taken. ' : '') + (!handleOk ? '@handle taken.' : '');
                    statusEl.style.color = '#ff6b6b';
                }
            } catch (e) { statusEl.textContent = 'Could not check — try again'; statusEl.style.color = '#ff6b6b'; }
        }, 350);
    }
    nameEl.addEventListener('input', check);
    handleEl.addEventListener('input', check);
    nameEl.focus();
}

// Jurisdiction picker (#167): highlight the chosen hosting region; the choice maps to the
// region-specific Stripe price (Iceland → cove_hosted_is $59, EU → cove_hosted_eu $29).
function _chPickRegion(r) {
    window._chRegion = r;
    const fc = lpFreqColor(window._todayTuning?.frequency || 'MOMENTUM');
    const accent = fc.primary || 'var(--daily-freq)';
    const ice = document.getElementById('ch-rg-iceland');
    const eu = document.getElementById('ch-rg-eu');
    if (ice) { ice.style.border = '1px solid ' + (r === 'iceland' ? accent : '#2a2d3a'); ice.style.background = (r === 'iceland' ? 'rgba(92,225,230,0.08)' : '#1a1d27'); }
    if (eu)  { eu.style.border  = '1px solid ' + (r === 'eu' ? accent : '#2a2d3a');      eu.style.background  = (r === 'eu' ? 'rgba(92,225,230,0.08)' : '#1a1d27'); }
}

function _coveHostingCheckout() {
    const name = (document.getElementById('ch-cove-name') || {}).value || '';
    const handle = ((document.getElementById('ch-handle') || {}).value || '').replace(/^@/, '');
    const region = window._chRegion || 'iceland';
    // Carry the captured referrer (#169) so the new Cove's operator gets a referred_by
    // edge in the registry. Don't credit a self-referral.
    let referredBy = (typeof window.lpStoredRef === 'function' ? window.lpStoredRef() : '') || '';
    if (referredBy && referredBy.toLowerCase() === handle.trim().toLowerCase()) referredBy = '';
    _upgradeStartCheckout('cove', { cove_name: name.trim(), handle: handle.trim(), referred_by: referredBy, region: region });
}

// Checkout handler — routes to Stripe for active tiers. opts carries host-with-us
// Cove details (cove_name, handle) when present (#167).
async function _upgradeStartCheckout(tierKey, opts) {
    opts = opts || {};
    const btn = document.querySelector('.upgrade-cta-btn');
    if (!btn) return;
    btn.disabled = true;
    btn.textContent = 'Loading...';

    try {
        const email = MC.presence?.email;
        const name = MC.presence?.display_name || MC.presence?.username || '';
        if (!email) {
            alert('Please sign in to upgrade.');
            btn.disabled = false;
            btn.textContent = 'Try again';
            return;
        }

        // Get referral code
        let ref = null;
        try {
            const refRes = await fetch('/api/account/referral-code');
            const refData = await refRes.json();
            ref = refData.ref || null;
        } catch(e) {}

        // Plan type mapping. 'cove' = host-with-us (Stripe → auto-provision on the VPS,
        // #167). Self-host doesn't checkout — see _coveHosting('self').
        const planMap = { operator: 'operator_monthly', cove: 'cove_hosted' };
        let planType = planMap[tierKey];
        // host-with-us Cove: the jurisdiction choice picks the region-specific price (#167).
        if (tierKey === 'cove') {
            planType = ({ iceland: 'cove_hosted_is', eu: 'cove_hosted_eu' })[opts.region] || 'cove_hosted_is';
        }
        if (!planType) {
            alert('This tier is not available for checkout yet.');
            btn.disabled = false;
            return;
        }

        const res = await fetch('https://api.lucidcove.org/api/commerce/checkout/session', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                plan_type: planType,
                email: email,
                name: name,
                ref: ref,
                cove_name: opts.cove_name || undefined,
                handle: opts.handle || undefined,
                referred_by: opts.referred_by || undefined,
                region: opts.region || undefined,
                success_url: window.location.origin + '/?upgraded=' + tierKey,
                cancel_url: window.location.origin + '/',
            }),
        });

        const data = await res.json();
        if (data.success && data.url) {
            window.location.href = data.url;
        } else {
            alert(data.error || 'Unable to start checkout. Please try again.');
            btn.disabled = false;
            btn.textContent = 'Try again';
        }
    } catch(e) {
        console.error('[upgrade] Checkout error:', e);
        alert('Unable to connect to payment system. Please try again.');
        btn.disabled = false;
        btn.textContent = 'Try again';
    }
}

// Render the upgrade CTA card on the home tab (tier-aware)
// Tuner: full card in dedicated section. Operator/Presence: inline card in approvals area.
function _renderUpgradeCTA() {
    var currentTier = MC.tier?.current || 'free';
    var info = UPGRADE_TIERS[currentTier];

    // Tuner: dedicated upgrade section
    var el = document.getElementById('home-upgrade-cta');
    if (el) {
        if (!info) { el.innerHTML = ''; }
        else if (MC.isOperator) {
            el.innerHTML = '<div class="op-upgrade-card">' +
                '<div class="op-upgrade-icon">' +
                    '<svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>' +
                '</div>' +
                '<div class="op-upgrade-body">' +
                    '<div class="op-upgrade-title">' + info.title + '</div>' +
                    '<div class="op-upgrade-text">' + info.subtitle + '</div>' +
                '</div>' +
                '<button class="op-upgrade-btn" onclick="showUpgradeModal()">Upgrade</button>' +
            '</div>';
        }
    }

    // Operator/Presence: inline upgrade in approvals area
    var inlineEl = document.getElementById('home-upgrade-inline');
    if (inlineEl) {
        if (!info) { inlineEl.innerHTML = ''; }
        else {
            inlineEl.innerHTML = '<div class="op-upgrade-card" style="margin-top:0.75rem">' +
                '<div class="op-upgrade-icon">' +
                    '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>' +
                '</div>' +
                '<div class="op-upgrade-body">' +
                    '<div class="op-upgrade-title">' + info.title + '</div>' +
                    '<div class="op-upgrade-text">' + info.subtitle + '</div>' +
                '</div>' +
                '<button class="op-upgrade-btn" onclick="showUpgradeModal()">Upgrade</button>' +
            '</div>';
        }
    }
}
