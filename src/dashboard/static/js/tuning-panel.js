// =============================================================================
// tuning-panel.js — Unified Player Template + Audio Engine
// =============================================================================
// THE player template for all of cove-core. Every tuning player in the system
// renders from otPlayerHTML(). The audio engine is shared — dual Audio elements
// (current + preloaded next) for Bluetooth/lock-screen track handoff, one
// MediaSession, one mini player. Any surface can call otSetPlaylist() to take
// over playback and otRenderPlayer() to mount the player UI.
// =============================================================================

const OT_AUDIO_BASE = 'https://audio.lucidtuner.com/Lucid_Tuner';
const OT_PRINCIPLES = [
    'A Good Time','Authenticity','Darkness and Light','Dreams','Faith',
    'Freedom Is','Guiding Force','Listen','Love Song','Moments',
    'Pattern','Signs','The Future','The Mirage','The Passing Tide',
    'The Power To Be Alive','Training Ground','Truth and Lies',
    'Tune Your Mind','Valley of Shadows','What Life Is About','Wonder'
];

let otAudio = null;
let _otNextAudio = null;        // preloaded next track — swap on ended (BT/lock handoff)
let _otPreloadIndex = -1;       // playlist index currently sitting on _otNextAudio
let _otNeedsResume = false;     // play() failed or silent while backgrounded — retry on foreground
let otTracks = [];
let otIndex = 0;
let otIsPlaying = false;
let otProgressInterval = null;
let otData = null;
let otLoaded = false;
let otPlaylistVisible = true;
let otPreviousTab = 'overview';
let _otConsecutiveErrors = 0;
let _otPendingPlay = false;     // true = new playlist displayed but not yet loaded into audio
let _otPlayStartTime = null;    // timestamp when current track started playing (for duration calc)
let _otCurrentTrackLogged = false; // prevent duplicate play_start for same track

// ── Shared Player API ───────────────────────────────────────────────────────
let _otSource = 'lt';           // 'lt' | 'tune' | 'playlist'
let _otOnTrackChange = null;
let _otOnProgress = null;
let _otPlaylistLabel = '';
let _otFavorites = [];           // [{filename, folder, principle, frequency}]
let _otFavsLoaded = false;

// ── Activity Tracking ───────────────────────────────────────────────────────
// Fire-and-forget event logging to /api/tuning/event. Never blocks playback.

function _otTrackEvent(eventType, extra) {
    const track = otTracks[otIndex];
    if (!track) return;
    const body = Object.assign({
        event_type: eventType,
        echo_name: track.filename || '',
        echo_album: track.folder || '',
        principle: track.principle || '',
        frequency: _otPlaylistLabel || '',
        play_source: _otSource || 'unknown',
        position_in_playlist: otIndex,
    }, extra || {});
    try {
        fetch('/api/tuning/event', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
    } catch (e) { /* silent */ }
}

// ── Helpers ─────────────────────────────────────────────────────────────────
function otSignalToFolder(s) {
    if (!s) return 'Raw_Signal';
    s = s.trim();
    if (s.endsWith('_Signal')) return s;
    const bare = s.charAt(0).toUpperCase() + s.slice(1).toLowerCase();
    const valid = ['Ground','Clear','Open','Rise','Raw','Bright','Drive'];
    if (valid.includes(bare)) return bare + '_Signal';
    const legacy = { EXPANSIVE:'Open_Signal', GROUNDING:'Ground_Signal', CLARITY:'Clear_Signal' };
    return legacy[s.toUpperCase()] || 'Raw_Signal';
}

function otSlugify(t) { return t.trim().replace(/-/g, '_').replace(/\s+/g, '_'); }

function otBuildTracks(folder) {
    const token = folder.replace(/_Signal$/, '');
    const display = token.replace(/_/g, ' ');
    return OT_PRINCIPLES.map(p => ({
        title: p + ' (' + display + ' Signal Echo)',
        filename: otSlugify(p) + '_' + token + '_Echo.mp3',
        folder: folder,
        principle: p,
    }));
}

function otGetAudioUrl(t) { return (t.cdnBase || OT_AUDIO_BASE) + '/' + t.folder + '/' + t.filename; }
function otGetCoverUrl(folder, cdnBase) { return (cdnBase || OT_AUDIO_BASE) + '/' + folder + '/Cover.png'; }
function otFmtTime(s) { if (!s||isNaN(s)) return '0:00'; const m=Math.floor(s/60),ss=Math.floor(s%60); return m+':'+(ss<10?'0':'')+ss; }

function otHexToRgb(hex) {
    if (!hex || hex.charAt(0) !== '#') return '102,225,255';
    hex = hex.replace('#', '');
    if (hex.length === 3) hex = hex[0]+hex[0]+hex[1]+hex[1]+hex[2]+hex[2];
    const r = parseInt(hex.substring(0,2), 16);
    const g = parseInt(hex.substring(2,4), 16);
    const b = parseInt(hex.substring(4,6), 16);
    return r + ',' + g + ',' + b;
}

// Frequency color map
const _OT_FALLBACK = {
    PEACE:'#5ce1e6', CLARITY:'#a0ebff', MOMENTUM:'#ff6b5c', TRUST:'#b8c6db',
    JOY:'#ffd700', CONNECTION:'#e0b0ff', PRESENCE:'#7b7394',
    RESILIENCE:'#d2691e', COURAGE:'#ff8c00', GRATITUDE:'#e8b830', RELEASE:'#9370db',
    INTEGRATION:'#20b2aa', BOUNDARY:'#4682b4'
};
const OT_FREQ_COLORS = (typeof LP !== 'undefined')
    ? Object.assign({}, _OT_FALLBACK,
        Object.fromEntries(Object.entries(LP.freq).map(([k,v]) => [k.toUpperCase(), v.primary])))
    : _OT_FALLBACK;

const OT_PLAYLIST_CDN = 'https://audio.lucidtuner.com/playlists';


// =============================================================================
// PLAYER TEMPLATE — the single source of truth for all player UI
// =============================================================================

function otPlayerHTML() {
    return `
        <div class="ot-player-wrap">
            <div class="ot-player">
                <img class="ot-cover-img" src="" alt="" style="display:none;">
                <div class="ot-track-title">Loading audio...</div>
                <div class="ot-track-signal"></div>
                <div class="ot-now-row">
                    <button class="ot-fav-btn" onclick="otToggleFav()" title="Add to favorites">♡</button>
                </div>
                <div class="ot-progress-wrap">
                    <div class="ot-progress-bar"></div>
                </div>
                <div class="ot-time">
                    <span class="ot-time-elapsed">0:00</span>
                    <span class="ot-time-duration">0:00</span>
                </div>
                <div class="ot-controls">
                    <button class="ot-btn" onclick="otPrev()" title="Previous">
                        <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M6 6h2v12H6zm3.5 6l8.5 6V6z"/></svg>
                    </button>
                    <button class="ot-btn ot-play" onclick="otTogglePlay()" title="Play">
                        <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor" class="ot-play-icon"><polygon points="5,3 19,12 5,21"/></svg>
                    </button>
                    <button class="ot-btn" onclick="otNext()" title="Next">
                        <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M6 18l8.5-6L6 6v12zM16 6v12h2V6h-2z"/></svg>
                    </button>
                </div>
                <div class="ot-volume">
                    <label>Vol</label>
                    <input type="range" min="0" max="100" value="30" oninput="otSetVolume(this.value)">
                </div>
            </div>
            <div class="ot-playlist">
                <div class="ot-playlist-header">
                    <span class="ot-playlist-label">Playlist</span>
                    <button class="ot-playlist-toggle" onclick="otTogglePlaylist()">Hide</button>
                </div>
                <div class="ot-playlist-tracks"></div>
            </div>
        </div>`;
}

/**
 * Render the player template into a container element.
 * Call this before otSetPlaylist() or otInitPlayer() to mount the UI.
 * @param {string} containerId - DOM id of the mount point
 */
function otRenderPlayer(containerId) {
    const el = document.getElementById(containerId);
    if (!el) return;
    el.innerHTML = otPlayerHTML();

    // Wire seek on progress bar
    const wrap = el.querySelector('.ot-progress-wrap');
    if (wrap) {
        wrap.addEventListener('click', (e) => {
            if (!otAudio || !otAudio.duration) return;
            const rect = e.currentTarget.getBoundingClientRect();
            const pct = (e.clientX - rect.left) / rect.width;
            otAudio.currentTime = pct * otAudio.duration;
            otUpdateProgressUI();
        });
    }

    // If audio is already playing, sync this player's state immediately
    if (otAudio && otTracks.length > 0) {
        _otSyncAllPlayers();
    }
}


// =============================================================================
// FAVORITES
// =============================================================================

async function otLoadFavorites() {
    if (_otFavsLoaded) return;
    try {
        const res = await fetch('/api/tuning/favorites');
        if (!res.ok) return;
        const data = await res.json();
        _otFavorites = data.favorites || [];
        _otFavsLoaded = true;
    } catch (e) {
        _otFavorites = [];
    }
}

function _otIsFav(filename) {
    return _otFavorites.some(f => f.filename === filename);
}

async function otToggleFav() {
    if (!otTracks.length) return;
    const track = otTracks[otIndex];
    if (!track) return;
    const fn = track.filename;
    const isFav = _otIsFav(fn);

    try {
        if (isFav) {
            await fetch('/api/tuning/favorites/' + encodeURIComponent(fn), { method: 'DELETE' });
            _otFavorites = _otFavorites.filter(f => f.filename !== fn);
        } else {
            await fetch('/api/tuning/favorites', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    filename: fn,
                    folder: track.folder,
                    principle: track.principle || '',
                    frequency: track.folder.replace(/_Signal$/, '').replace(/_/g, ' ')
                })
            });
            _otFavorites.push({ filename: fn, folder: track.folder, principle: track.principle, frequency: track.folder.replace(/_Signal$/, '') });
        }
    } catch (e) {
        console.warn('[player] Favorite toggle failed:', e);
    }

    // Update all heart buttons
    _otUpdateFavUI();
    otRenderPlaylist();
}

async function otToggleFavTrack(index) {
    if (index < 0 || index >= otTracks.length) return;
    const track = otTracks[index];
    const fn = track.filename;
    const isFav = _otIsFav(fn);

    try {
        if (isFav) {
            await fetch('/api/tuning/favorites/' + encodeURIComponent(fn), { method: 'DELETE' });
            _otFavorites = _otFavorites.filter(f => f.filename !== fn);
        } else {
            await fetch('/api/tuning/favorites', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    filename: fn,
                    folder: track.folder,
                    principle: track.principle || '',
                    frequency: track.folder.replace(/_Signal$/, '').replace(/_/g, ' ')
                })
            });
            _otFavorites.push({ filename: fn, folder: track.folder, principle: track.principle, frequency: track.folder.replace(/_Signal$/, '') });
        }
    } catch (e) {
        console.warn('[player] Favorite toggle failed:', e);
    }

    _otUpdateFavUI();
    otRenderPlaylist();
}

function _otUpdateFavUI() {
    if (!otTracks.length) return;
    const track = otTracks[otIndex];
    const isFav = _otIsFav(track.filename);
    document.querySelectorAll('.ot-fav-btn').forEach(el => {
        el.textContent = isFav ? '♥' : '♡';
        el.classList.toggle('fav-active', isFav);
    });
}


// =============================================================================
// OPERATOR TUNING PANEL (LT overlay + Tune tab)
// =============================================================================

async function openOperatorTuning() {
    otPreviousTab = activeTab;
    if (activeTab === 'system') disconnectLogStream();
    if (typeof stopJwAutoRefresh === 'function') stopJwAutoRefresh();

    document.querySelectorAll('.panel').forEach(p => {
        p.classList.remove('active', 'active-grid', 'active-flex');
        p.style.display = '';
    });
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    // Only hide mini player if nothing is playing — if audio is active, keep it visible
    if (!otAudio || otAudio.paused) hideMiniPlayer();
    activeTab = 'operator-tuning';

    const panel = document.getElementById('panel-operator-tuning');
    panel.style.display = 'block';
    panel.classList.add('active');

    if (!otLoaded) {
        document.getElementById('otLoading').style.display = 'block';
        document.getElementById('otContent').style.display = 'none';
        try {
            const res = await fetch('/api/tuning/operator');
            otData = await res.json();
            if (!otData.has_tuning) {
                document.getElementById('otLoading').innerHTML =
                    '<div style="margin-top:2rem;">' +
                    '<h3 style="color:var(--dim);font-size:1rem;">No Tuning Available</h3>' +
                    '<p style="color:var(--dim);font-size:0.8rem;margin-top:0.5rem;">' + esc(otData.note || 'Check back after 6am ET.') + '</p></div>';
                return;
            }
            // Render player template into mount point
            otRenderPlayer('otPlayerMount');
            otRenderTuning(otData);
            document.getElementById('otLoading').style.display = 'none';
            document.getElementById('otContent').style.display = 'block';
            otLoaded = true;

            // Recent Tunings — the public LT Drop archive below today's tuning
            otLoadRecentDrops();

            // Always init player — otInitPlayer handles pending state
            // if audio is already playing from another surface
            try { await otInitPlayer(otData); } catch (pe) {
                console.warn('[tuning-panel] Player init failed:', pe.message);
            }
        } catch (e) {
            document.getElementById('otLoading').innerHTML =
                '<div style="color:var(--red);">Failed to load tuning: ' + esc(e.message) + '</div>';
        }
    } else {
        // Return visit — re-init player so this surface shows ITS playlist
        // otInitPlayer handles pending state if audio is already playing
        try { await otInitPlayer(otData); } catch (pe) {
            console.warn('[tuning-panel] Player re-init failed:', pe.message);
        }
    }
}

function backFromTuning() {
    const panel = document.getElementById('panel-operator-tuning');
    panel.style.display = 'none';
    panel.classList.remove('active-grid');
    const tab = document.querySelector(`.tab[data-tab="${otPreviousTab}"]`);
    if (tab) tab.click();
    else {
        const fallback = document.querySelector('.tab[data-tab="home"]') || document.querySelector('.tab');
        if (fallback) fallback.click();
    }
    if (otAudio && otAudio.src) showMiniPlayer();
}

function _otPanel() {
    return document.getElementById('panel-operator-tuning') || document.getElementById('panel-tune');
}

function otRenderTuning(data) {
    const freq = (data.frequency || '').toUpperCase();
    const pkgColors = data.frequency_colors || {};
    const freqColor = OT_FREQ_COLORS[freq] || pkgColors.primary || '#66e1ff';
    const freqGlow = pkgColors.glow || (freqColor + '66');

    // Date
    const dateStr = data.date || new Date().toISOString().split('T')[0];
    const d = new Date(dateStr + 'T12:00:00');
    const dateEl = document.getElementById('otDate');
    if (dateEl) dateEl.textContent = formatDate(dateStr + 'T12:00:00', { weekday:'long', month:'long', day:'numeric', year:'numeric', hour: undefined, minute: undefined });

    // Alignment
    const alignEl = document.getElementById('otAlignment');
    if (alignEl) { alignEl.textContent = freq ? freq + ' ALIGNMENT' : ''; alignEl.style.color = freqColor; }

    // Frequency / Principle
    const freqEl = document.getElementById('otFrequency');
    if (freqEl) { freqEl.textContent = data.principle || ''; freqEl.style.color = freqColor; freqEl.style.textShadow = '0 0 30px ' + freqGlow; }

    const princEl = document.getElementById('otPrinciple');
    if (princEl) princEl.textContent = '';

    // Coaching — use universal coaching if available, fall back to operator tuning prompt
    const coachingText = data.universal_coaching || data.tuning_prompt || '';
    if (coachingText) {
        const coachText = document.getElementById('otCoachText');
        if (coachText) coachText.textContent = coachingText;
        const coachEl = document.getElementById('otCoachBlock');
        if (coachEl) {
            // Match Tune Now card style: full border, no left-accent
            const coachLabel = coachEl.querySelector('.ot-coach-label');
            if (coachLabel) { coachLabel.textContent = 'Coaching'; coachLabel.style.display = ''; }
        }
    } else {
        const coachEl = document.getElementById('otCoachBlock');
        if (coachEl) coachEl.style.display = 'none';
    }

    // Tuning key
    if (data.tuning_key) {
        const keyText = document.getElementById('otKeyText');
        if (keyText) keyText.textContent = data.tuning_key;
    } else {
        const keyEl = document.getElementById('otKeyBlock');
        if (keyEl) keyEl.style.display = 'none';
    }

    // Love equation
    const le = data.love_equation || {};
    if (le.value && le.value !== 0) {
        const eqVal = document.getElementById('otEqVal');
        if (eqVal) {
            eqVal.textContent = 'dE/dt = ' + (typeof le.value === 'number' ? le.value.toFixed(2) : le.value) + ' (' + (le.direction||'CONSTRUCTIVE') + ')';
            eqVal.style.color = freqColor;
        }
        const eqCard = document.getElementById('otEqBar');
        if (eqCard) {
            eqCard.style.borderColor = freqColor + '40';
            eqCard.style.borderLeft = '3px solid ' + freqColor;
            const eqLabel = eqCard.querySelector('.ot-label');
            if (eqLabel) eqLabel.style.color = freqColor;
        }
        const parts = [];
        if (le.beta!=null) parts.push('β='+Number(le.beta).toFixed(2));
        if (le.E!=null) parts.push('E='+Number(le.E).toFixed(2));
        if (le.C!=null) parts.push('C='+Number(le.C).toFixed(2));
        if (le.D!=null) parts.push('D='+Number(le.D).toFixed(2));
        const eqDetail = document.getElementById('otEqDetail');
        if (eqDetail) eqDetail.textContent = parts.join('  ');
    } else {
        const eqEl = document.getElementById('otEqBar');
        if (eqEl) eqEl.style.display = 'none';
    }

    // Date tint
    if (dateEl) dateEl.style.color = freqColor + 'aa';

    // Practice steps — use universal_practice if available, fall back to operator practice_steps
    const uPractice = data.universal_practice;
    const steps = data.practice_steps;
    if (uPractice && uPractice.length >= 3) {
        const practiceBlock = document.getElementById('otPracticeBlock');
        if (practiceBlock) {
            practiceBlock.style.display = 'block';
            const tmplEl = document.getElementById('otPracticeTemplate');
            if (tmplEl) tmplEl.textContent = '';
            const practiceLabel = practiceBlock.querySelector('.ot-practice-label');
            if (practiceLabel) practiceLabel.innerHTML = 'Practice';
            for (let i = 0; i < 3; i++) {
                const s = uPractice[i];
                const title = typeof s === 'object' ? s.title : '';
                const text = typeof s === 'object' ? s.instruction : s;
                const tEl = document.getElementById('otStep' + (i+1) + 'Title');
                const xEl = document.getElementById('otStep' + (i+1) + 'Text');
                if (tEl) tEl.textContent = title;
                if (xEl) xEl.textContent = text;
            }
        }
    } else if (steps && steps.step1) {
        const practiceBlock = document.getElementById('otPracticeBlock');
        if (practiceBlock) {
            practiceBlock.style.display = 'block';
            const templateName = (data.practice_template || '').charAt(0).toUpperCase() + (data.practice_template || '').slice(1);
            const tmplEl = document.getElementById('otPracticeTemplate');
            if (tmplEl) tmplEl.textContent = templateName;
            const s1t = document.getElementById('otStep1Title'); if (s1t) s1t.textContent = steps.step1.title;
            const s1x = document.getElementById('otStep1Text'); if (s1x) s1x.textContent = steps.step1.text;
            const s2t = document.getElementById('otStep2Title'); if (s2t) s2t.textContent = steps.step2.title;
            const s2x = document.getElementById('otStep2Text'); if (s2x) s2x.textContent = steps.step2.text;
            const s3t = document.getElementById('otStep3Title'); if (s3t) s3t.textContent = steps.step3.title;
            const s3x = document.getElementById('otStep3Text'); if (s3x) s3x.textContent = steps.step3.text;
        }
    }

    // Color the player and tuning elements with frequency accent
    otColorPlayer(freqColor);

    // Store freq color
    window._otFreqColor = freqColor;
    window._otFreqGlow = freqGlow;
    const mpProg = document.getElementById('mpProgress');
    if (mpProg) mpProg.style.background = freqColor;
}

// ═══════════════════════════════════════════════════════════════════════════════
// RECENT TUNINGS — the public LT Drop archive (last N daily Drops), shown on the
// Tuning Hub below today's orchestrated tuning, mirroring the public Drop page.
// Display-only, every Cove. Each row opens the same full tuning modal + player
// (_tfShowTuningDetail) used everywhere else — coaching/practice come from the
// frequency-keyed client templates, so the Drop payload only needs
// frequency / principle / tuning_key / audio_url / date.
// Backed by GET /api/tuning/recent-drops (public_drop.get_recent_drops).
// ═══════════════════════════════════════════════════════════════════════════════

async function otLoadRecentDrops() {
    const container = document.getElementById('otRecentDrops');
    if (!container) return;

    try {
        const resp = await fetch('/api/tuning/recent-drops?limit=10');
        const data = await resp.json();
        const drops = data.drops || [];
        window._otRecentDrops = drops;

        if (!drops.length) return;  // leave the section hidden when empty

        let html = '';
        drops.forEach((d, idx) => {
            const rawFreq = d.frequency || '';
            const freq = rawFreq.charAt(0).toUpperCase() + rawFreq.slice(1).toLowerCase();
            const freqColor = (typeof lpColor === 'function')
                ? lpColor(rawFreq)
                : (OT_FREQ_COLORS[rawFreq.toUpperCase()] || 'var(--accent)');
            const principle = d.principle || '';
            const dateLabel = (typeof _tfFormatHistDate === 'function')
                ? _tfFormatHistDate(d.date || '')
                : (d.date || '');

            const fBadge = (typeof lpFreqBadgeHTML === 'function')
                ? lpFreqBadgeHTML(rawFreq)
                : `<span class="freq-badge" style="color:${freqColor};">${esc(freq)}</span>`;

            html += `<div class="tf-hist-row" onclick="otOpenRecentDrop(${idx})">
                <div class="tf-hist-summary">
                    ${fBadge}
                    <span class="tf-hist-principle" style="color:${freqColor};">${esc(principle)}</span>
                    <span class="tf-hist-date-time">${esc(dateLabel)}</span>
                </div>
            </div>`;
        });

        container.innerHTML = html;
        const section = document.getElementById('thHistory');
        if (section) section.style.display = '';
    } catch (e) {
        // leave the section hidden on error
    }
}

async function otOpenRecentDrop(idx) {
    const drops = window._otRecentDrops;
    if (!drops || !drops[idx]) return;
    const d = drops[idx];
    if (typeof _tfShowTuningDetail !== 'function') return;
    // Map the public-Drop summary onto the shape _tfShowTuningDetail expects.
    await _tfShowTuningDetail({
        frequency: d.frequency || '',
        principle: d.principle || '',
        tuning_key: d.tuning_key || '',
        audio_url: d.audio_url || '',
        date: d.date || '',
        time: '',
        context: '',
    });
}

/** Apply frequency accent color to all player instances + tuning panel elements */
function otColorPlayer(freqColor) {
    const pnl = _otPanel();

    // Player elements (all instances — class-based)
    document.querySelectorAll('.ot-play').forEach(el => {
        el.style.color = freqColor;
        el.style.borderColor = freqColor;
    });
    document.querySelectorAll('.ot-progress-bar').forEach(el => {
        el.style.background = freqColor;
    });
    document.querySelectorAll('.ot-player').forEach(el => {
        el.style.borderColor = freqColor + '30';
        el.style.borderTop = '2px solid ' + freqColor + '60';
    });
    document.querySelectorAll('.ot-playlist').forEach(el => {
        el.style.borderColor = freqColor + '25';
    });
    document.querySelectorAll('.ot-playlist-header').forEach(el => {
        el.style.borderBottomColor = freqColor + '20';
    });
    document.querySelectorAll('.ot-playlist-label').forEach(el => {
        el.style.color = freqColor;
    });
    document.querySelectorAll('input[type="range"]').forEach(el => {
        el.style.accentColor = freqColor;
    });

    // Tuning panel specific (only if panel exists)
    if (pnl) {
        pnl.querySelectorAll('.ot-card.ot-key').forEach(el => {
            el.style.borderColor = 'rgba(' + otHexToRgb(freqColor) + ', 0.25)';
            el.style.borderLeft = '4px solid ' + freqColor;
            el.style.background = 'rgba(' + otHexToRgb(freqColor) + ', 0.04)';
        });
        pnl.querySelectorAll('.ot-card.ot-key .ot-label').forEach(el => {
            el.style.color = freqColor;
        });
        pnl.querySelectorAll('.ot-practice-label').forEach(el => {
            el.style.color = freqColor;
        });
        pnl.querySelectorAll('.ot-step-title').forEach(el => el.style.color = freqColor);
        pnl.querySelectorAll('.ot-step-num').forEach(el => {
            el.style.background = freqColor;
            el.style.color = '#0f1117';
        });
        pnl.querySelectorAll('.back-btn').forEach(el => {
            el.style.color = freqColor;
        });
    }
}


// =============================================================================
// AUDIO ENGINE
// =============================================================================

async function otInitPlayer(data) {
    const signalFolder = otSignalToFolder(data.signal_type);
    const freq = data.frequency || '';

    // Load favorites
    await otLoadFavorites();

    // Try loading frequency playlist from CDN
    let freqPlaylistLoaded = false;
    if (freq) {
        try {
            const freqLower = freq.toLowerCase();
            const res = await fetch(OT_PLAYLIST_CDN + '/' + freqLower + '.json');
            if (res.ok) {
                const playlist = await res.json();
                if (Array.isArray(playlist) && playlist.length > 0) {
                    otTracks = playlist.map(t => {
                        const filename = t.filename || t.file || '';
                        const folder = t.folder || t.signal_type || signalFolder;
                        const principle = t.principle || t.title || filename.replace(/_/g, ' ').replace(/\.mp3$/, '');
                        const signalDisplay = folder.replace(/_Signal$/, '').replace(/_/g, ' ');
                        return {
                            title: principle + ' (' + signalDisplay + ' Signal Echo)',
                            filename: filename,
                            folder: folder,
                            principle: principle,
                        };
                    });
                    freqPlaylistLoaded = true;
                }
            }
        } catch (e) {
            console.warn('Frequency playlist not available, using signal-type fallback:', e.message);
        }
    }

    if (!freqPlaylistLoaded) {
        otTracks = otBuildTracks(signalFolder);
    }

    // Shuffle
    for (let i = otTracks.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [otTracks[i], otTracks[j]] = [otTracks[j], otTracks[i]];
    }

    // Start with tuning principle if matched
    if (data.principle) {
        const idx = otTracks.findIndex(t => t.principle.toLowerCase() === data.principle.toLowerCase());
        if (idx > 0) {
            const m = otTracks.splice(idx, 1)[0];
            otTracks.unshift(m);
        }
    }

    // Create audio element
    _otEnsureAudio();

    // If audio is currently playing from another source, show new playlist
    // in "ready to play" state without interrupting current audio
    if (otAudio && !otAudio.paused) {
        _otPendingPlay = true;
        _otDisplayTrackInfo(0);
    } else {
        _otPendingPlay = false;
        otLoadTrack(0, false);
    }
    otRenderPlaylist();

    const streamName = freq ? freq + ' Tuning Stream' : signalFolder.replace(/_/g, ' ');
    const streamLabel = streamName + ' (' + otTracks.length + ' tracks)';
    document.querySelectorAll('.ot-playlist-label').forEach(el => el.textContent = streamLabel);

    // Stream activation label above player
    const streamLabelEl = document.getElementById('otStreamLabel');
    if (streamLabelEl) {
        streamLabelEl.innerHTML = '&#9654; PRESS PLAY TO ACTIVATE YOUR';
        const streamNameSpan = document.createElement('div');
        streamNameSpan.textContent = streamName;
        streamNameSpan.style.fontSize = '1rem';
        streamNameSpan.style.fontWeight = '700';
        streamNameSpan.style.letterSpacing = '0.1em';
        streamNameSpan.style.marginTop = '6px';
        streamNameSpan.style.color = window._otFreqColor || 'var(--accent)';
        streamLabelEl.appendChild(streamNameSpan);
    }
}

/** Style + mount a hidden media element used by the dual-buffer engine. */
function _otPrepAudioEl(el) {
    // crossOrigin is only needed for the desktop Web Audio gain path. On mobile we
    // play the element natively, and requesting CORS only adds a way for loads to fail.
    if (!_otIsMobile()) el.crossOrigin = 'anonymous';
    el.preload = 'auto';
    el.setAttribute('playsinline', 'true');
    el.setAttribute('webkit-playsinline', 'true');
    el.style.cssText = 'display:block!important;width:1px!important;height:1px!important;position:fixed!important;top:0!important;left:0!important;opacity:0.01!important;pointer-events:none!important;visibility:visible!important;';
    if (!el.parentNode) document.body.appendChild(el);
}

/** Bind play/pause/ended/error/metadata on the *current* otAudio element.
 *  Re-run after a dual-buffer swap so listeners stay on the live element. */
function _otBindCurrentAudioEvents() {
    if (!otAudio || otAudio._otEventsBound) return;
    otAudio._otEventsBound = true;

    // Every handler ignores events from a retired buffer element after dual-buffer swap
    // (listeners stay on the node; only the live otAudio pointer is authoritative).
    const isLive = (ev) => ev && ev.target === otAudio;

    otAudio.addEventListener('loadedmetadata', (ev) => {
        if (!isLive(ev) || !otAudio) return;
        document.querySelectorAll('.ot-time-duration').forEach(el => el.textContent = otFmtTime(otAudio.duration));
    });
    otAudio.addEventListener('ended', (ev) => {
        if (!isLive(ev)) return;
        // Log play_end with duration before advancing
        if (_otPlayStartTime) {
            const dur = (Date.now() - _otPlayStartTime) / 1000;
            _otTrackEvent('echo_play_end', { play_duration: Math.round(dur * 10) / 10 });
            _otPlayStartTime = null;
            _otCurrentTrackLogged = false;
        }
        // Prefer preloaded handoff so Bluetooth/lock keeps an already-warm element
        // instead of assigning .src on a cold load while backgrounded.
        if (!_otHandoffToPreloaded()) {
            otNext();
        }
    });
    otAudio.addEventListener('play', (ev) => {
        if (!isLive(ev)) return;
        _otResumeCtx();  // desktop safety; no-op on mobile (no context)
        otIsPlaying = true;
        _otNeedsResume = false;
        _otConsecutiveErrors = 0;  // Reset on successful play
        // Track play start (once per track load)
        if (!_otCurrentTrackLogged) {
            _otPlayStartTime = Date.now();
            _otCurrentTrackLogged = true;
            _otTrackEvent('echo_play_start');
        } else if (!_otPlayStartTime) {
            // Resuming from pause
            _otPlayStartTime = Date.now();
        }
        otUpdateIcons();
        otStartProgress();
        showMiniPlayer();
        if ('mediaSession' in navigator) navigator.mediaSession.playbackState = 'playing';
        // Warm the following track while this one is audible
        _otSchedulePreload();
    });
    otAudio.addEventListener('pause', (ev) => {
        if (!isLive(ev)) return;
        // Dual-buffer swap pauses the retiring element — ignore those
        if (ev.target && ev.target._otIgnorePause) return;
        otIsPlaying = false;
        // Track pause with duration so far
        if (_otPlayStartTime) {
            const dur = (Date.now() - _otPlayStartTime) / 1000;
            _otTrackEvent('echo_pause', { play_duration: Math.round(dur * 10) / 10 });
            _otPlayStartTime = null;  // Reset so resume creates new segment
        }
        otUpdateIcons();
        otStopProgress();
        if ('mediaSession' in navigator) navigator.mediaSession.playbackState = 'paused';
    });
    otAudio.addEventListener('error', (ev) => {
        if (!isLive(ev)) return;
        _otConsecutiveErrors++;
        if (_otConsecutiveErrors >= 3) {
            console.warn('[player] 3 consecutive load errors — stopping auto-advance');
            _otConsecutiveErrors = 0;
            return;
        }
        setTimeout(() => otNext(), 1500);
    });
    // If the element thinks it is playing but produces no progress while hidden,
    // mark for foreground resume (common BT A2DP stall after src swap).
    otAudio.addEventListener('playing', (ev) => {
        if (!isLive(ev)) return;
        _otNeedsResume = false;
    });
    otAudio.addEventListener('waiting', (ev) => {
        if (!isLive(ev)) return;
        if (document.hidden && otIsPlaying) _otNeedsResume = true;
    });
    otAudio.addEventListener('stalled', (ev) => {
        if (!isLive(ev)) return;
        if (document.hidden && otIsPlaying) _otNeedsResume = true;
    });
}

/** Ensure the shared Audio elements exist. Idempotent.
 *  Volume uses Web Audio API GainNode (set up on first user gesture via _otEnsureGain).
 *  iOS ignores HTMLMediaElement.volume but respects AudioContext gain. */
function _otEnsureAudio() {
    if (otAudio) return;
    otAudio = new Audio();
    _otPrepAudioEl(otAudio);
    _otNextAudio = new Audio();
    _otPrepAudioEl(_otNextAudio);

    // Bind lock screen / Bluetooth media controls once, up front, so they exist before
    // the first track loads and never get torn down between tracks (iOS default path).
    _otBindMediaSessionHandlers();
    _otBindCurrentAudioEvents();

    // Set initial volume via .volume (works on desktop, ignored on iOS)
    // iOS ignores .volume — GainNode handles it (see _otEnsureGain)
    const initVol = typeof window._otVolume === 'number' ? window._otVolume : 0.3;
    otAudio.volume = initVol;
    _otNextAudio.volume = initVol;
    window._otVolume = initVol;

    // Foreground: resume suspended AudioContext (desktop) and retry playback if a
    // background/Bluetooth handoff left us silent with the clock still advancing.
    if (!window._otVisibilityBound) {
        window._otVisibilityBound = true;
        document.addEventListener('visibilitychange', () => {
            if (document.hidden) return;
            _otResumeCtx();
            _otRecoverPlaybackIfNeeded();
        });
        // Some WebViews fire pageshow on BFCache restore without visibilitychange.
        window.addEventListener('pageshow', () => {
            _otResumeCtx();
            _otRecoverPlaybackIfNeeded();
        });
    }
}

/** Start playback on otAudio; on rejection while we intended to play, flag resume. */
function _otPlayCurrent(reason) {
    if (!otAudio || !otAudio.src) return Promise.resolve();
    return otAudio.play().then(() => {
        _otNeedsResume = false;
    }).catch(err => {
        console.warn('Play blocked (' + (reason || 'play') + '):', err && err.message ? err.message : err);
        // NotAllowedError while backgrounded is the classic lock-screen gap — retry
        // when the page is visible again (unlock / open player).
        _otNeedsResume = true;
        if ('mediaSession' in navigator) {
            try { navigator.mediaSession.playbackState = 'paused'; } catch (e) {}
        }
    });
}

/** If we expected audio but the element is paused or a prior play() failed, try again. */
function _otRecoverPlaybackIfNeeded() {
    if (!otAudio || !otAudio.src) return;
    if (_otPendingPlay) return;
    const wantsPlay = _otNeedsResume || (otIsPlaying && otAudio.paused);
    if (!wantsPlay) return;
    _otPlayCurrent('foreground-recover');
}

/** Pick the next index the same way otNext does (random, not current). */
function _otPickNextIndex() {
    if (!otTracks.length) return 0;
    if (otTracks.length <= 1) return 0;
    let next;
    do { next = Math.floor(Math.random() * otTracks.length); } while (next === otIndex);
    return next;
}

/** Preload a following track onto _otNextAudio without touching the live element. */
function _otPreloadIndexTrack(index) {
    if (!_otNextAudio || index < 0 || index >= otTracks.length) return;
    const track = otTracks[index];
    if (!track) return;
    const url = otGetAudioUrl(track);
    // Same URL already staged — leave the buffer alone
    if (_otPreloadIndex === index && _otNextAudio.src && _otNextAudio.getAttribute('src') === url) return;
    // Compare resolved src (browser may absolutize)
    try {
        if (_otPreloadIndex === index && _otNextAudio.src && _otNextAudio.src.indexOf(track.filename) !== -1) return;
    } catch (e) {}
    _otPreloadIndex = index;
    try {
        _otNextAudio.preload = 'auto';
        _otNextAudio.src = url;
        // kick network without playing
        if (typeof _otNextAudio.load === 'function') {
            // load() on the *next* buffer is safe — it is not the live session element
            try { _otNextAudio.load(); } catch (e) {}
        }
    } catch (e) {
        console.warn('[player] preload failed:', e && e.message ? e.message : e);
        _otPreloadIndex = -1;
    }
}

function _otSchedulePreload() {
    if (!otAudio || !otTracks.length) return;
    // Defer so we do not contend with the just-started play() decode
    setTimeout(() => {
        if (!otIsPlaying || !otAudio) return;
        _otPreloadIndexTrack(_otPickNextIndex());
    }, 800);
}

/**
 * Swap preloaded next → current and play. Returns true if handoff ran.
 * Keeps Bluetooth/lock audio session warmer than assigning .src on otAudio cold.
 */
function _otHandoffToPreloaded() {
    if (_otPendingPlay) return false;
    if (!_otNextAudio || _otPreloadIndex < 0 || _otPreloadIndex >= otTracks.length) return false;
    // Require the buffer to have data — HAVE_CURRENT_DATA (2)+ 
    if (!(_otNextAudio.readyState >= 2 && _otNextAudio.src)) return false;

    const nextIndex = _otPreloadIndex;
    const retiring = otAudio;
    const incoming = _otNextAudio;

    // Suppress pause analytics on the element we are deliberately stopping.
    // Leave the flag set — isLive() already ignores retired-element events after swap;
    // flag is belt-and-suspenders if pause races before the pointer moves.
    if (retiring) retiring._otIgnorePause = true;
    try { if (retiring && !retiring.paused) retiring.pause(); } catch (e) {}

    // Swap pointers BEFORE clearing the retiring buffer
    otAudio = incoming;
    _otNextAudio = retiring || new Audio();
    if (!_otNextAudio.parentNode) _otPrepAudioEl(_otNextAudio);
    // Clear retiring buffer so it can preload the track after this one
    try {
        _otNextAudio.removeAttribute('src');
        _otNextAudio.src = '';
        if (typeof _otNextAudio.load === 'function') _otNextAudio.load();
    } catch (e) {}
    _otPreloadIndex = -1;

    // Events must sit on the new current element
    otAudio._otEventsBound = false;
    _otBindCurrentAudioEvents();

    // Volume parity (desktop GainNode stays on first element only — mobile uses .volume)
    const vol = typeof window._otVolume === 'number' ? window._otVolume : 0.3;
    try { otAudio.volume = vol; } catch (e) {}
    try { _otNextAudio.volume = vol; } catch (e) {}

    // Sync UI + media session to the handoff track, then play without reassigning .src
    _otApplyTrackUI(nextIndex, { skipSrc: true });
    _otPlayCurrent('handoff');
    return true;
}

/** True for touch devices (iOS, Android). On these we deliberately avoid the Web
 *  Audio pipeline: a plain <audio> element outputs straight to the system audio
 *  session, which survives backgrounding, lock screen, and Bluetooth route changes.
 *  Routing through an AudioContext breaks all three (the OS suspends the context and
 *  audio goes silent while the element keeps "playing"). */
function _otIsMobile() {
    return ('ontouchstart' in window) || (navigator.maxTouchPoints > 0);
}

/** Resume a suspended AudioContext (desktop volume path). No-op if none exists. */
function _otResumeCtx() {
    const ctx = window._otAudioCtx;
    if (ctx && ctx.state === 'suspended' && typeof ctx.resume === 'function') {
        ctx.resume().catch(() => {});
    }
}

/** Wire up Web Audio API GainNode — MUST be called during a user gesture (tap/click).
 *  iOS requires AudioContext creation during user interaction. Idempotent.
 *  DESKTOP ONLY: on mobile the GainNode hijacks the audio route and kills background /
 *  lock screen / Bluetooth playback, so we never create it there and let the native
 *  <audio> element drive volume (hardware/Bluetooth controls it on mobile anyway). */
function _otEnsureGain() {
    if (_otIsMobile()) return;
    if (window._otGainNode || !otAudio) return;
    try {
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        const source = ctx.createMediaElementSource(otAudio);
        const gain = ctx.createGain();
        source.connect(gain);
        gain.connect(ctx.destination);
        const vol = typeof window._otVolume === 'number' ? window._otVolume : 0.3;
        gain.gain.value = vol;
        window._otGainNode = gain;
        window._otAudioCtx = ctx;
        // Resume immediately (we're in a user gesture)
        if (ctx.state === 'suspended') ctx.resume();
        console.log('[volume] GainNode created, gain=' + vol + ', ctx.state=' + ctx.state);
    } catch (e) {
        console.warn('[volume] GainNode FAILED:', e.message);
    }
}


// ── Shared Playlist Loader ──────────────────────────────────────────────────
// opts: { source, label, onTrackChange, onProgress, autoplay, startIndex, freqColor, mountId }

function otSetPlaylist(tracks, opts) {
    opts = opts || {};
    if (!tracks || tracks.length === 0) return;

    // Explicit playlist start — clear any pending state
    _otPendingPlay = false;
    _otNeedsResume = false;
    _otPreloadIndex = -1;
    if (_otNextAudio) {
        try {
            _otNextAudio.removeAttribute('src');
            _otNextAudio.src = '';
        } catch (e) {}
    }

    // Stop current playback
    if (otAudio && !otAudio.paused) otAudio.pause();

    // Render player into mount point if provided
    if (opts.mountId) {
        otRenderPlayer(opts.mountId);
    }

    // Set tracks
    otTracks = tracks;
    otIndex = opts.startIndex || 0;
    _otSource = opts.source || 'external';
    _otPlaylistLabel = opts.label || '';
    _otOnTrackChange = opts.onTrackChange || null;
    _otOnProgress = opts.onProgress || null;

    if (opts.freqColor) window._otFreqColor = opts.freqColor;

    _otEnsureAudio();

    // Load favorites if not yet loaded
    if (!_otFavsLoaded) otLoadFavorites();

    // Update labels + playlist in all rendered players
    document.querySelectorAll('.ot-playlist-label').forEach(el => el.textContent = _otPlaylistLabel);

    // Update mini player label
    const mpFreq = document.getElementById('mpFreq');
    if (mpFreq) mpFreq.textContent = _otPlaylistLabel;

    otRenderPlaylist();

    // Color player if freqColor provided
    if (opts.freqColor) otColorPlayer(opts.freqColor);

    // Load first track
    otLoadTrack(otIndex, opts.autoplay !== false);
}


// =============================================================================
// TRACK LOADING + UI SYNC (updates ALL player instances)
// =============================================================================

/**
 * Display track info in ALL player UIs without touching otAudio.src.
 * Used when a new playlist is loaded while audio from another source is still playing.
 * The player shows the new playlist in a "ready to play" state.
 */
function _otDisplayTrackInfo(index) {
    if (index < 0 || index >= otTracks.length) return;
    otIndex = index;
    const track = otTracks[index];
    const coverUrl = otGetCoverUrl(track.folder, track.cdnBase);

    document.querySelectorAll('.ot-cover-img').forEach(el => {
        el.src = coverUrl;
        el.alt = track.folder.replace(/_/g, ' ');
        el.style.display = 'block';
    });
    document.querySelectorAll('.ot-track-title').forEach(el => el.textContent = track.title);
    document.querySelectorAll('.ot-track-signal').forEach(el => {
        el.textContent = track.folder.replace(/_/g, ' ');
        if (typeof lpSignalColor === 'function') el.style.color = lpSignalColor(track.folder);
    });
    // Reset progress to 0 (this playlist hasn't started)
    document.querySelectorAll('.ot-progress-bar').forEach(el => el.style.width = '0%');
    document.querySelectorAll('.ot-time-elapsed').forEach(el => el.textContent = '0:00');
    document.querySelectorAll('.ot-time-duration').forEach(el => el.textContent = '0:00');
    // Force play icon (not pause — this playlist isn't playing yet)
    document.querySelectorAll('.ot-play-icon').forEach(el => {
        el.innerHTML = '<polygon points="5,3 19,12 5,21"/>';
    });
    // Favorites heart
    _otUpdateFavUI();
}

function otLoadTrack(index, autoplay) {
    if (index < 0 || index >= otTracks.length) return;
    _otEnsureAudio();
    // If this index is already warm on the preload buffer, hand off without a cold src set
    if (autoplay && _otNextAudio && _otPreloadIndex === index && _otNextAudio.readyState >= 2 && _otNextAudio.src) {
        if (_otHandoffToPreloaded()) return;
    }
    _otApplyTrackUI(index, { skipSrc: false });
    if (autoplay) {
        _otPlayCurrent('load');
    }
}

/**
 * Apply track index to UI + media session. Optionally assign otAudio.src.
 * skipSrc=true is used after dual-buffer handoff (src already on the element).
 */
function _otApplyTrackUI(index, opts) {
    opts = opts || {};
    if (index < 0 || index >= otTracks.length) return;
    _otPendingPlay = false;  // Explicit load = no longer pending
    _otCurrentTrackLogged = false;  // Reset for new track
    _otPlayStartTime = null;
    otIndex = index;
    const track = otTracks[index];
    if (!opts.skipSrc) {
        // Assigning .src triggers the load automatically. Calling .load() on top of it
        // resets the element and, on iOS, drops the audio session so the next track is
        // blocked while the screen is locked and the lock screen controls go dead.
        otAudio.src = otGetAudioUrl(track);
        // Invalidate preload if it pointed at something else
        if (_otPreloadIndex === index) _otPreloadIndex = -1;
    }

    // Update ALL player instances (class-based, null-safe)
    const coverUrl = otGetCoverUrl(track.folder, track.cdnBase);
    document.querySelectorAll('.ot-cover-img').forEach(el => {
        el.src = coverUrl;
        el.alt = track.folder.replace(/_/g, ' ');
        el.style.display = 'block';
    });
    document.querySelectorAll('.ot-track-title').forEach(el => {
        el.textContent = track.title;
    });
    document.querySelectorAll('.ot-track-signal').forEach(el => {
        el.textContent = track.folder.replace(/_/g, ' ');
        if (typeof lpSignalColor === 'function') el.style.color = lpSignalColor(track.folder);
    });
    document.querySelectorAll('.ot-progress-bar').forEach(el => el.style.width = '0%');
    document.querySelectorAll('.ot-time-elapsed').forEach(el => el.textContent = '0:00');
    document.querySelectorAll('.ot-time-duration').forEach(el => {
        el.textContent = (otAudio && otAudio.duration) ? otFmtTime(otAudio.duration) : '0:00';
    });

    // Playlist active highlight
    const fc = window._otFreqColor || 'var(--accent)';
    document.querySelectorAll('.ot-pl-track').forEach((el, i) => {
        const isActive = i === index;
        el.classList.toggle('active', isActive);
        if (isActive) {
            el.style.color = fc;
            el.style.background = 'rgba(' + otHexToRgb(fc) + ',0.08)';
        } else {
            el.style.color = '';
            el.style.background = '';
        }
    });

    // Favorites heart
    _otUpdateFavUI();

    // Mini player
    const mpTitle = document.getElementById('mpTitle');
    if (mpTitle) mpTitle.textContent = track.title || track.principle || '';
    const mpFreq = document.getElementById('mpFreq');
    if (mpFreq) mpFreq.textContent = _otPlaylistLabel || (otData ? otData.frequency : '').toUpperCase();

    // Media Session (lock screen)
    otSetupMediaSession(track);

    // External callback
    if (_otOnTrackChange) _otOnTrackChange(track, index);
}

/** Sync all player UIs to current state (used when a new player instance is rendered mid-playback) */
function _otSyncAllPlayers() {
    if (!otTracks.length) return;
    const track = otTracks[otIndex];
    const coverUrl = otGetCoverUrl(track.folder, track.cdnBase);
    document.querySelectorAll('.ot-cover-img').forEach(el => { el.src = coverUrl; el.alt = track.folder.replace(/_/g, ' '); el.style.display = 'block'; });
    document.querySelectorAll('.ot-track-title').forEach(el => el.textContent = track.title);
    document.querySelectorAll('.ot-track-signal').forEach(el => {
        el.textContent = track.folder.replace(/_/g, ' ');
        if (typeof lpSignalColor === 'function') el.style.color = lpSignalColor(track.folder);
    });
    _otUpdateFavUI();
    otUpdateIcons();
    otRenderPlaylist();
    if (window._otFreqColor) otColorPlayer(window._otFreqColor);
}


// =============================================================================
// PLAYBACK CONTROLS
// =============================================================================

function otTogglePlay() {
    if (!otAudio) return;
    // GainNode NOT created here — only on slider interaction (preserves iOS lock screen)
    // Pending playlist — user pressed play, start the new playlist
    if (_otPendingPlay) {
        _otPendingPlay = false;
        otLoadTrack(otIndex, true);
        return;
    }
    if (!otAudio.src) return;
    if (otIsPlaying) {
        _otNeedsResume = false;
        otAudio.pause();
    } else {
        _otPlayCurrent('toggle');
    }
}

function otNext() {
    // If pending, old audio finished — don't auto-advance into new playlist
    if (_otPendingPlay) return;
    // Track skip if user-initiated (not from ended event — ended already logged)
    if (otIsPlaying && _otPlayStartTime) {
        const dur = (Date.now() - _otPlayStartTime) / 1000;
        _otTrackEvent('echo_skip', { play_duration: Math.round(dur * 10) / 10 });
        _otPlayStartTime = null;
        _otCurrentTrackLogged = false;
    }
    // Prefer the already-warm preload buffer (same pick otNext would have made)
    if (_otHandoffToPreloaded()) return;
    if (otTracks.length <= 1) { otLoadTrack(0, true); return; }
    otLoadTrack(_otPickNextIndex(), true);
}

function otPrev() {
    if (otAudio && otAudio.currentTime > 3) {
        otAudio.currentTime = 0;
        return;
    }
    // Track skip on prev
    if (otIsPlaying && _otPlayStartTime) {
        const dur = (Date.now() - _otPlayStartTime) / 1000;
        _otTrackEvent('echo_skip', { play_duration: Math.round(dur * 10) / 10 });
        _otPlayStartTime = null;
        _otCurrentTrackLogged = false;
    }
    const prev = (otIndex - 1 + otTracks.length) % otTracks.length;
    otLoadTrack(prev, true);
}

function otSetVolume(val) {
    const v = val / 100;
    window._otVolume = v;
    // Desktop: create the GainNode on first slider touch (user-gesture context).
    // Mobile: _otEnsureGain() bails, so we fall through to the native <audio>.volume
    // path and never hijack the audio route (keeps background / lock screen / Bluetooth).
    if (!window._otGainNode && otAudio) _otEnsureGain();
    if (window._otGainNode) {
        window._otGainNode.gain.value = v;
    } else {
        if (otAudio) otAudio.volume = v;
        if (_otNextAudio) _otNextAudio.volume = v;
    }
}

function otUpdateIcons() {
    const playPath = '<polygon points="5,3 19,12 5,21"/>';
    const pausePath = '<rect x="5" y="4" width="4" height="16"/><rect x="15" y="4" width="4" height="16"/>';
    const svg = otIsPlaying ? pausePath : playPath;
    document.querySelectorAll('.ot-play-icon').forEach(el => el.innerHTML = svg);
    // Mini player icon
    const mpIcon = document.getElementById('mpPlayIcon');
    if (mpIcon) mpIcon.innerHTML = svg;
}


// =============================================================================
// PROGRESS
// =============================================================================

function otStartProgress() {
    otStopProgress();
    otProgressInterval = setInterval(otUpdateProgressUI, 250);
}

function otStopProgress() {
    if (otProgressInterval) { clearInterval(otProgressInterval); otProgressInterval = null; }
}

function otUpdateProgressUI() {
    if (!otAudio || !otAudio.duration) return;
    const pct = (otAudio.currentTime / otAudio.duration) * 100;
    // If pending, only update mini player — tab player shows new playlist at 0%
    if (!_otPendingPlay) {
        document.querySelectorAll('.ot-progress-bar').forEach(el => el.style.width = pct + '%');
        document.querySelectorAll('.ot-time-elapsed').forEach(el => el.textContent = otFmtTime(otAudio.currentTime));
        document.querySelectorAll('.ot-time-duration').forEach(el => el.textContent = otFmtTime(otAudio.duration));
    }
    // Mini player always updates (shows what's actually playing)
    const mpProg = document.getElementById('mpProgress');
    if (mpProg) mpProg.style.width = pct + '%';
    // External callback
    if (_otOnProgress) _otOnProgress(pct, otAudio.currentTime, otAudio.duration);
    // Media Session position
    if ('mediaSession' in navigator && 'setPositionState' in navigator.mediaSession) {
        try { navigator.mediaSession.setPositionState({ duration:otAudio.duration, playbackRate:otAudio.playbackRate, position:otAudio.currentTime }); } catch(e){}
    }
}


// =============================================================================
// MEDIA SESSION (lock screen controls)
// =============================================================================

/** Bind the lock screen / Bluetooth transport controls ONCE. iOS keeps these alive
 *  across tracks as long as they aren't re-registered out from under it, so we set them
 *  a single time and only swap metadata per track (see otSetupMediaSession). */
function _otBindMediaSessionHandlers() {
    if (!('mediaSession' in navigator) || window._otMediaHandlersBound) return;
    window._otMediaHandlersBound = true;
    const ms = navigator.mediaSession;
    ms.setActionHandler('play', () => {
        _otResumeCtx();  // desktop safety; no-op on mobile (no context)
        _otPlayCurrent('mediasession').then(() => {
            if (!otAudio.paused) ms.playbackState = 'playing';
        });
    });
    ms.setActionHandler('pause', () => {
        otAudio.pause();
        ms.playbackState = 'paused';
    });
    ms.setActionHandler('previoustrack', () => otPrev());
    ms.setActionHandler('nexttrack', () => otNext());
    // Null out seek so the OS shows prev/next buttons instead of scrubbers.
    try { ms.setActionHandler('seekbackward', null); } catch(e){}
    try { ms.setActionHandler('seekforward', null); } catch(e){}
    try { ms.setActionHandler('seekto', d => { if(d.seekTime!=null){otAudio.currentTime=d.seekTime;otUpdateProgressUI();} }); } catch(e){}
}

/** Update the now-playing metadata for the current track. Handlers are bound once in
 *  _otBindMediaSessionHandlers; this only refreshes title/artwork. */
function otSetupMediaSession(track) {
    if (!('mediaSession' in navigator)) return;
    navigator.mediaSession.metadata = new MediaMetadata({
        title: track.title,
        artist: 'LUCID TUNER',
        album: track.folder.replace(/_/g, ' '),
        artwork: [{ src: otGetCoverUrl(track.folder), sizes: '512x512', type: 'image/png' }],
    });
    _otBindMediaSessionHandlers();  // idempotent safety if audio was created elsewhere
}


// =============================================================================
// PLAYLIST RENDERING (updates ALL instances)
// =============================================================================

function otRenderPlaylist() {
    const containers = document.querySelectorAll('.ot-playlist-tracks');
    if (!containers.length) return;
    const fc = window._otFreqColor || 'var(--accent)';
    const html = otTracks.map((t, i) => {
        const isActive = i === otIndex;
        const activeStyle = isActive ? ' style="color:' + fc + ';background:rgba(' + otHexToRgb(fc) + ',0.08);"' : '';
        const isFav = _otIsFav(t.filename);
        const heartClass = isFav ? ' fav-active' : '';
        return '<div class="ot-pl-track' + (isActive ? ' active' : '') + '"' + activeStyle + '>' +
            '<span class="ot-pl-num">' + (i+1) + '</span>' +
            '<span class="ot-pl-name" onclick="otLoadTrack(' + i + ',true)">' + esc(t.title) + '</span>' +
            '<button class="ot-pl-fav' + heartClass + '" onclick="event.stopPropagation();otToggleFavTrack(' + i + ')" title="Favorite">' + (isFav ? '♥' : '♡') + '</button>' +
            '</div>';
    }).join('');
    containers.forEach(c => c.innerHTML = html);
}

function otTogglePlaylist() {
    otPlaylistVisible = !otPlaylistVisible;
    document.querySelectorAll('.ot-playlist-tracks').forEach(el => el.style.display = otPlaylistVisible ? 'block' : 'none');
    document.querySelectorAll('.ot-playlist-toggle').forEach(el => el.textContent = otPlaylistVisible ? 'Hide' : 'Show');
}


// =============================================================================
// MINI PLAYER
// =============================================================================

function showMiniPlayer() {
    const mp = document.getElementById('miniPlayer');
    if (!mp) { console.warn('[tuning-panel] miniPlayer element not found'); return; }
    if (otAudio && otAudio.src) {
        mp.classList.add('visible');
        document.body.classList.add('has-mini-player');
        const mpPlay = document.getElementById('mpPlayBtn');
        if (window._otFreqColor && mpPlay) mpPlay.style.color = window._otFreqColor;
    }
}

function hideMiniPlayer() {
    const mp = document.getElementById('miniPlayer');
    if (mp) mp.classList.remove('visible');
    document.body.classList.remove('has-mini-player');
}
