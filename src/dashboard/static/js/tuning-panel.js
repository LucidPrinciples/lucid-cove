// =============================================================================
// tuning-panel.js — Unified Player Template + Audio Engine
// =============================================================================
// THE player template for all of cove-core. Every tuning player in the system
// renders from otPlayerHTML(). Phones play through one audio element so a
// preload cannot starve the live file. The next song is chosen when this one
// starts, then warmed with a ranged fetch near the end. Desktop may also hold
// that file on a second element only after this one is playing. One
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
const OT_ERROR_RETRY_MS = 3000;
const OT_STALL_QUIET_MS = 15000;
const OT_PRELOAD_DELAY_MS = 3000;
const OT_WARM_LEAD_S = 25;
const OT_FAIL_CAP = 3;
const OT_PLAYER_LOG_KEY = 'lt_player_log';
const OT_PLAYER_LOG_MAX = 200;
const OT_HISTORY_MAX = 20;
const OT_FAIL_STATUS = 'The stream failed three times. Playback stopped.';
let _otStallTimer = null;
let _otErrorTimer = null;
let _otStallMark = 0;
let _otAudibleTimer = null;
let _otRetriedThisTrack = false;
let _otWantAudio = false;
let _otReloading = false;
let _otPlayHistory = [];
let _otPlayerLog = [];
let _otPreloadTimer = null;
let _otBuffering = false;
let _otSounding = false;
let _otHoldUserPause = false;
let _otSrcDepth = 0;
let _otUpNext = -1;
let _otWarmFor = -1;
let _otWarming = false;
let _otWarmGen = 0;
let _otWarmAbort = null;
let _otLoadStamp = 0;

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

// ── Car player: log, stall retry, played-track history ─────────────────────
// The log is for a drive. Track title and player state only. No account data.

function _otPlayerTitle() {
    const track = otTracks[otIndex];
    return (track && (track.title || track.principle)) || '';
}

function _otLoadFile() {
    const track = otTracks[otIndex];
    if (track && track.filename) return String(track.filename);
    try {
        const src = (otAudio && (otAudio.currentSrc || otAudio.src)) || '';
        const part = String(src).split('/').pop() || '';
        return part.split('?')[0];
    } catch (e) {
        return '';
    }
}

// A src swap pauses the element. That pause is queued on the media task
// source and can arrive after a timer, so only loadstart or playing ends it.
// Events fired inside this call are still the assignment. The queued pause
// has not been delivered yet.
function _otWithSrcChange(fn) {
    _otAbortWarm();
    _otSrcDepth += 1;
    _otHoldUserPause = true;
    try {
        return fn();
    } finally {
        _otSrcDepth -= 1;
    }
}

function _otReleaseSrcPause() {
    if (_otSrcDepth > 0) return;
    _otHoldUserPause = false;
}

function _otLogPlayer(event, reason) {
    const entry = {
        time: new Date().toISOString(),
        event: String(event || ''),
        title: _otPlayerTitle(),
        reason: String(reason || ''),
        hidden: false,
        readyState: null,
        networkState: null,
    };
    try { entry.hidden = !!(document && document.hidden); } catch (e) {}
    try {
        if (otAudio) {
            entry.readyState = otAudio.readyState;
            entry.networkState = otAudio.networkState;
        }
    } catch (e) {}
    const last = _otPlayerLog[_otPlayerLog.length - 1];
    if (last && last.event === entry.event && last.reason === entry.reason && last.title === entry.title) return;
    _otPlayerLog.push(entry);
    if (_otPlayerLog.length > OT_PLAYER_LOG_MAX) {
        _otPlayerLog.splice(0, _otPlayerLog.length - OT_PLAYER_LOG_MAX);
    }
    try { localStorage.setItem(OT_PLAYER_LOG_KEY, JSON.stringify(_otPlayerLog)); } catch (e) {}
}

function _otLoadPlayerLog() {
    try {
        const raw = localStorage.getItem(OT_PLAYER_LOG_KEY);
        const parsed = raw ? JSON.parse(raw) : [];
        if (!Array.isArray(parsed)) { _otPlayerLog = []; return; }
        _otPlayerLog = parsed.slice(-OT_PLAYER_LOG_MAX).map(e => ({
            time: String((e && e.time) || ''),
            event: String((e && e.event) || ''),
            title: String((e && e.title) || ''),
            reason: String((e && e.reason) || ''),
            hidden: !!(e && e.hidden),
            readyState: (e && e.readyState != null) ? e.readyState : null,
            networkState: (e && e.networkState != null) ? e.networkState : null,
        }));
    } catch (e) {
        _otPlayerLog = [];
    }
}

function otPlayerLogEntries() {
    return _otPlayerLog.map(e => ({
        time: e.time,
        event: e.event,
        title: e.title,
        reason: e.reason,
        hidden: e.hidden,
        readyState: e.readyState,
        networkState: e.networkState,
    }));
}

function otPlayerLogText() {
    const header = 'time\tevent\ttitle\treason\thidden\treadyState\tnetworkState';
    const lines = otPlayerLogEntries().map(e =>
        [e.time, e.event, e.title, e.reason, e.hidden, e.readyState, e.networkState].join('\t')
    );
    return [header].concat(lines).join('\n');
}

function otCopyPlayerLog() {
    const text = otPlayerLogText();
    const btn = document.getElementById('player-log-copy');
    const status = document.getElementById('player-log-status');
    const mark = (ok) => {
        if (btn) btn.textContent = ok ? 'Copied' : 'Copy';
        if (status) {
            if (ok && !_otPlayerLog.length) status.textContent = 'No player events yet.';
            else status.textContent = ok ? '' : 'Could not copy.';
        }
        if (ok && btn) setTimeout(() => { btn.textContent = 'Copy'; }, 1500);
    };
    const fallback = () => {
        try {
            const ta = document.createElement('textarea');
            ta.value = text;
            ta.setAttribute('readonly', 'readonly');
            document.body.appendChild(ta);
            if (ta.select) ta.select();
            const ok = document.execCommand('copy');
            if (ta.remove) ta.remove();
            return !!ok;
        } catch (e) {
            return false;
        }
    };
    try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text).then(() => mark(true)).catch(() => mark(fallback()));
            return;
        }
    } catch (e) {}
    mark(fallback());
}

function _otBindPlayerLogCopy() {
    const btn = document.getElementById('player-log-copy');
    if (!btn || btn._otBound) return;
    btn._otBound = true;
    btn.addEventListener('click', () => otCopyPlayerLog());
}

function _otClearFaultTimers() {
    if (_otStallTimer) {
        clearTimeout(_otStallTimer);
        _otStallTimer = null;
    }
    if (_otErrorTimer) {
        clearTimeout(_otErrorTimer);
        _otErrorTimer = null;
    }
}

function _otBufferEnd(audio) {
    try {
        const ranges = audio && audio.buffered;
        if (ranges && ranges.length > 0 && typeof ranges.end === 'function') {
            const end = ranges.end(ranges.length - 1);
            if (isFinite(end) && end > 0) return end;
        }
    } catch (e) {}
    return 0;
}

function _otMediaMark() {
    if (!otAudio) return 0;
    const time = (otAudio.currentTime && isFinite(otAudio.currentTime)) ? otAudio.currentTime : 0;
    return Math.max(time, _otBufferEnd(otAudio));
}

function _otClearAudibleTimer() {
    if (_otAudibleTimer) {
        clearTimeout(_otAudibleTimer);
        _otAudibleTimer = null;
    }
}

function _otShowFailStatus() {
    const el = document.getElementById('mpStatus');
    if (!el) return;
    el.hidden = false;
    el.textContent = OT_FAIL_STATUS;
}

function _otClearFailStatus() {
    const el = document.getElementById('mpStatus');
    if (!el) return;
    el.hidden = true;
    el.textContent = '';
}

function _otNoteAudible() {
    _otNeedsResume = false;
    // Playing again means the load error recovered. A stall watch stays until
    // the playhead or the buffer actually moves.
    if (_otErrorTimer) {
        clearTimeout(_otErrorTimer);
        _otErrorTimer = null;
    }
    if (_otStallTimer && _otMediaMark() > _otStallMark + 0.05) {
        clearTimeout(_otStallTimer);
        _otStallTimer = null;
    }
    _otClearAudibleTimer();
    const startedAt = _otMediaMark();
    _otAudibleTimer = setTimeout(() => {
        _otAudibleTimer = null;
        if (!otAudio || otAudio.paused || !_otWantAudio) return;
        if (_otMediaMark() >= startedAt + 0.25) {
            _otConsecutiveErrors = 0;
            _otRetriedThisTrack = false;
            _otClearFailStatus();
        }
    }, 1500);
}

function _otScheduleError(reason) {
    if (_otReloading || !otAudio) return;
    const srcNow = otAudio.src || (otAudio.getAttribute && otAudio.getAttribute('src')) || '';
    if (!srcNow) return;
    _otLogPlayer('error', reason || '');
    if (!_otWantAudio && !otIsPlaying) return;
    if (_otErrorTimer) return;
    _otErrorTimer = setTimeout(() => {
        _otErrorTimer = null;
        _otResolveError();
    }, OT_ERROR_RETRY_MS);
}

function _otArmStallWatch() {
    if (_otStallTimer || !otAudio) return;
    const mark = _otMediaMark();
    _otStallMark = mark;
    _otStallTimer = setTimeout(() => {
        _otStallTimer = null;
        _otOnStallQuiet(mark);
    }, OT_STALL_QUIET_MS);
}

function _otWatchStall(kind) {
    // The live element is still filling. Do not start a second download.
    _otBuffering = true;
    _otCancelPreload();
    _otAbortWarm();
    if (_otReloading || !otAudio) return;
    const srcNow = otAudio.src || (otAudio.getAttribute && otAudio.getAttribute('src')) || '';
    if (!srcNow) return;
    _otLogPlayer(kind, '');
    if (!_otWantAudio && !otIsPlaying) return;
    _otArmStallWatch();
}

function _otOnStallQuiet(mark) {
    if (!_otWantAudio || !otAudio || otAudio.paused) return;
    if (_otMediaMark() > mark + 0.05) {
        _otLogPlayer('waiting', 'growing');
        _otArmStallWatch();
        return;
    }
    _otLogPlayer('retry', 'stall');
    _otRetrySameTrack();
}

function _otRetrySameTrack() {
    const track = otTracks[otIndex];
    if (!otAudio || !track) return;
    const url = otGetAudioUrl(track);
    const pos = (otAudio.currentTime && isFinite(otAudio.currentTime)) ? otAudio.currentTime : 0;
    if (_otStallTimer) {
        clearTimeout(_otStallTimer);
        _otStallTimer = null;
    }
    const restore = () => {
        try { if (otAudio && pos > 0) otAudio.currentTime = pos; } catch (e) {}
        try { otAudio.removeEventListener('loadedmetadata', restore); } catch (e) {}
    };
    if (pos > 0) otAudio.addEventListener('loadedmetadata', restore);
    _otWithSrcChange(() => {
        _otReloading = true;
        try {
            // Assigning src reloads. load() on this live element drops the lock-screen session.
            otAudio.removeAttribute('src');
            otAudio.src = url;
            if (pos > 0) {
                try { otAudio.currentTime = pos; } catch (e) {}
            }
            _otLogPlayer('load', _otLoadFile());
        } catch (e) {}
        _otReloading = false;
        _otPlayCurrent('retry');
    });
}

function _otStopForFailures() {
    _otWantAudio = false;
    otIsPlaying = false;
    _otNeedsResume = false;
    _otClearFaultTimers();
    _otClearAudibleTimer();
    _otLogPlayer('error', 'cap');
    _otCancelPreload();
    _otWithSrcChange(() => {
        _otReloading = true;
        try {
            if (otAudio) {
                otAudio.pause();
                otAudio.removeAttribute('src');
            }
        } catch (e) {}
        _otReloading = false;
    });
    _otShowFailStatus();
    try { otUpdateIcons(); } catch (e) {}
    try { otStopProgress(); } catch (e) {}
    if ('mediaSession' in navigator) {
        try { navigator.mediaSession.playbackState = 'paused'; } catch (e) {}
    }
}

function _otResolveError() {
    if (!_otWantAudio || !otAudio) return;
    _otClearAudibleTimer();
    _otConsecutiveErrors += 1;
    if (_otConsecutiveErrors >= OT_FAIL_CAP) {
        _otStopForFailures();
        return;
    }
    if (!_otRetriedThisTrack) {
        _otRetriedThisTrack = true;
        _otLogPlayer('retry', 'same');
        _otRetrySameTrack();
        return;
    }
    _otRetriedThisTrack = false;
    _otLogPlayer('retry', 'next');
    otNext();
}

function _otResumeAfterStop() {
    if (otAudio && otAudio.src) return false;
    if (!otTracks.length) return false;
    _otConsecutiveErrors = 0;
    _otRetriedThisTrack = false;
    _otWantAudio = true;
    otLoadTrack(otIndex, true);
    return true;
}

function _otRememberPlayed(index) {
    if (index < 0 || index >= otTracks.length) return;
    if (_otPlayHistory[_otPlayHistory.length - 1] === index) return;
    _otPlayHistory.push(index);
    if (_otPlayHistory.length > OT_HISTORY_MAX) {
        _otPlayHistory.splice(0, _otPlayHistory.length - OT_HISTORY_MAX);
    }
}

function _otResetPlayHistory() {
    _otPlayHistory = [];
}

function _otForgiveFailures() {
    _otConsecutiveErrors = 0;
    _otRetriedThisTrack = false;
    _otClearFaultTimers();
    _otCancelPreload();
}

function _otPublishPosition() {
    if (!otAudio || !('mediaSession' in navigator) || !navigator.mediaSession) return;
    if (!('setPositionState' in navigator.mediaSession)) return;
    const dur = otAudio.duration;
    if (!isFinite(dur) || dur <= 0) return;
    let pos = otAudio.currentTime || 0;
    if (!isFinite(pos) || pos < 0) pos = 0;
    if (pos > dur) pos = dur;
    try {
        navigator.mediaSession.setPositionState({
            duration: dur,
            playbackRate: otAudio.playbackRate || 1,
            position: pos,
        });
    } catch (e) {}
}

// ── Helpers ─────────────────────────────────────────────────────────────────
function otSignalToFolder(s) {
    if (!s) return 'Raw_Signal';
    s = String(s).trim();
    const normalized = s.replace(/[\s-]+/g, '_');
    const head = normalized.replace(/_Signal$/i, '');
    const bare = head.charAt(0).toUpperCase() + head.slice(1).toLowerCase();
    const valid = ['Ground','Clear','Open','Rise','Raw','Bright','Drive'];
    if (valid.includes(bare)) return bare + '_Signal';
    const legacy = { EXPANSIVE:'Open_Signal', GROUNDING:'Ground_Signal', CLARITY:'Clear_Signal' };
    return legacy[s.toUpperCase()] || legacy[head.toUpperCase()] || 'Raw_Signal';
}

function otSlugify(t) { return t.trim().replace(/-/g, '_').replace(/\s+/g, '_'); }

function otPlaylistRows(payload) {
    if (Array.isArray(payload) && payload.length) return payload;
    if (payload && Array.isArray(payload.tracks) && payload.tracks.length) return payload.tracks;
    return [];
}

function otTrackPrinciple(t, filename) {
    if (t && t.principle) return String(t.principle);
    const title = String((t && t.title) || '');
    const stripped = title.replace(/\s*\([^)]*Echo\)\s*$/i, '').trim();
    if (stripped) return stripped;
    return String(filename || '')
        .replace(/\.mp3$/i, '')
        .replace(/_/g, ' ')
        .replace(/\s+(Ground|Clear|Open|Rise|Raw|Bright|Drive)\s+Echo$/i, '')
        .trim();
}

function otMapCdnTrack(t, fallbackFolder) {
    const filename = (t && (t.filename || t.file)) || '';
    const rawFolder = (t && (t.folder || t.signal_type || t.signalType || t.album)) || fallbackFolder;
    const folder = otSignalToFolder(rawFolder);
    const principle = otTrackPrinciple(t, filename);
    const signalDisplay = folder.replace(/_Signal$/, '').replace(/_/g, ' ');
    return {
        title: principle + ' (' + signalDisplay + ' Signal Echo)',
        filename: filename,
        folder: folder,
        principle: principle,
    };
}

function otBuildTracks(folder) {
    folder = otSignalToFolder(folder);
    const token = folder.replace(/_Signal$/, '');
    const display = token.replace(/_/g, ' ');
    return OT_PRINCIPLES.map(p => ({
        title: p + ' (' + display + ' Signal Echo)',
        filename: otSlugify(p) + '_' + token + '_Echo.mp3',
        folder: folder,
        principle: p,
    }));
}

function otGetAudioUrl(t) {
    const filename = t && t.filename;
    if (!filename) return '';
    const folder = (t && t.cdnBase)
        ? String(t.folder || '')
        : otSignalToFolder(t && t.folder);
    return ((t && t.cdnBase) || OT_AUDIO_BASE) + '/' + folder + '/' + filename;
}
function otGetCoverUrl(folder, cdnBase) {
    const mapped = cdnBase ? String(folder || '') : otSignalToFolder(folder);
    return (cdnBase || OT_AUDIO_BASE) + '/' + mapped + '/Cover.png';
}

/** True when the shared element is actually producing sound. */
function _otAudible() {
    return !!(otAudio && otAudio.src && !otAudio.paused && _otSounding && !_otBuffering);
}

/** Play was asked for, and nothing is audible yet. */
function _otLoadingSound() {
    return !!(otAudio && otAudio.src && _otWantAudio && !otAudio.paused && !_otAudible());
}

function _otPublishPlaybackState() {
    if (!('mediaSession' in navigator)) return;
    try {
        navigator.mediaSession.playbackState = _otAudible() ? 'playing' : 'paused';
    } catch (e) {}
}

function _otPaintLoadStatus() {
    const el = document.getElementById('mpStatus');
    if (!el || el.textContent === OT_FAIL_STATUS) return;
    if (_otLoadingSound()) {
        el.hidden = false;
        el.textContent = 'Loading';
        return;
    }
    if (el.textContent === 'Loading') {
        el.hidden = true;
        el.textContent = '';
    }
}

/** Same file already on the shared element. Do not assign src again. */
function _otTrackIsLoaded(track) {
    if (!otAudio || !otAudio.src || !track) return false;
    const want = otGetAudioUrl(track);
    if (!want) return false;
    if (otAudio.src === want) return true;
    try {
        return new URL(otAudio.src).href === new URL(want, location.href).href;
    } catch (e) {
        const name = track.filename || '';
        return !!(name && otAudio.src.indexOf(name) !== -1);
    }
}

/** Stable list. Start on the tuning's principle when it is in the list, else a random row. */
function _otStableStart(tracks, principle) {
    if (!tracks || !tracks.length) return 0;
    if (principle) {
        const want = String(principle).toLowerCase();
        const idx = tracks.findIndex(t => t.principle && String(t.principle).toLowerCase() === want);
        if (idx >= 0) return idx;
    }
    return Math.floor(Math.random() * tracks.length);
}

/** Keep currentTime. Opening the same track is not a restart. */
function _otKeepLoaded(tracks, opts, startAt) {
    opts = opts || {};
    window._otPreview = null;
    _otResetPlayHistory();
    otTracks = tracks;
    otIndex = startAt;
    _otDropUpNext();
    _otEnsureUpNext();
    if (opts.source) _otSource = opts.source;
    if (opts.label) _otPlaylistLabel = opts.label;
    if (opts.freqColor) {
        window._otFreqColor = opts.freqColor;
        otColorPlayer(opts.freqColor);
    }
    if (opts.mountId) otRenderPlayer(opts.mountId);
    _otApplyTrackUI(startAt, { skipSrc: true, keepTime: true });
    if (opts.label) {
        const root = opts.mountId ? document.getElementById(opts.mountId) : null;
        const label = root ? root.querySelector('.ot-playlist-label') : null;
        if (label) label.textContent = opts.label;
        const mpFreq = document.getElementById('mpFreq');
        if (mpFreq) mpFreq.textContent = _otPlaylistLabel;
    }
    if (opts.autoplay === true && otAudio && otAudio.paused) {
        _otPlayCurrent('same-track');
    } else if (typeof showMiniPlayer === 'function') {
        showMiniPlayer();
    }
    otUpdateIcons();
    otUpdateProgressUI();
    otRenderPlaylist();
}
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
function otRenderPlayer(containerId, opts) {
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
    // skipSync: preview of a different playlist (badge / Tune while something else is live)
    if (otAudio && otTracks.length > 0 && !(opts && opts.skipSync)) {
        _otSyncAllPlayers();
    }
}

/** Idle playlist in one mount. Mini bar and otTracks stay on whatever is actually playing. */
function _otPaintPreview(mountId, tracks, opts) {
    opts = opts || {};
    if (!mountId || !tracks || !tracks.length) return;
    otRenderPlayer(mountId, { skipSync: true });
    const root = document.getElementById(mountId);
    if (!root) return;
    window._otPreview = { tracks: tracks, opts: opts, mountId: mountId };
    const track = tracks[0];
    const coverUrl = otGetCoverUrl(track.folder, track.cdnBase);
    const img = root.querySelector('.ot-cover-img');
    if (img && coverUrl) { img.src = coverUrl; img.style.display = 'block'; }
    const title = root.querySelector('.ot-track-title');
    if (title) title.textContent = track.title;
    const sig = root.querySelector('.ot-track-signal');
    if (sig) sig.textContent = String(track.folder || '').replace(/_/g, ' ');
    const label = root.querySelector('.ot-playlist-label');
    if (label) label.textContent = opts.label || '';
    root.querySelectorAll('.ot-play-icon').forEach(el => {
        el.innerHTML = '<polygon points="5,3 19,12 5,21"/>';
    });
    const playBtn = root.querySelector('.ot-play');
    if (playBtn) playBtn.setAttribute('onclick', '_otStartPreview(-1)');
    const list = root.querySelector('.ot-playlist-tracks');
    if (list) {
        list.innerHTML = tracks.map((t, i) =>
            '<div class="ot-pl-track">' +
            '<span class="ot-pl-num">' + (i + 1) + '</span>' +
            '<span class="ot-pl-name" onclick="_otStartPreview(' + i + ')">' +
            String(t.title || '').replace(/</g, '') + '</span></div>'
        ).join('');
    }
}

function _otStartPreview(index) {
    const preview = window._otPreview;
    if (!preview || !preview.tracks || !preview.tracks.length) return;
    let start = index;
    if (start == null || start < 0) {
        if (preview.opts && preview.opts.startIndex != null && preview.opts.startIndex >= 0) {
            start = preview.opts.startIndex;
        } else {
            start = Math.floor(Math.random() * preview.tracks.length);
        }
    }
    const o = Object.assign({}, preview.opts, { autoplay: true, startIndex: start });
    window._otPreview = null;
    otSetPlaylist(preview.tracks, o);
}

function _otInPreview(el) {
    const id = window._otPreview && window._otPreview.mountId;
    if (!id || !el) return false;
    const root = document.getElementById(id);
    return !!(root && root.contains(el));
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

    // Frequency — same pattern as Drop: name only, no ALIGNMENT
    const alignEl = document.getElementById('otAlignment');
    if (alignEl) {
        alignEl.hidden = false;
        alignEl.textContent = freq || '';
        alignEl.style.color = freqColor;
    }

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
        if (keyText) {
            keyText.textContent = String(data.tuning_key).split(' / ').join('\n');
            keyText.style.whiteSpace = 'pre-line';
        }
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
            const dateLabel = (typeof _tfFullDropDate === 'function')
                ? _tfFullDropDate(d.date || '')
                : ((typeof _tfFormatHistDate === 'function') ? _tfFormatHistDate(d.date || '') : (d.date || ''));
            const signal = String(d.signal_type || '').replace(/_Signal$/, '').replace(/_/g, ' ');
            const meta = [freq, signal, dateLabel].filter(Boolean).join(' / ');

            html += `<div class="tf-hist-row history-item" onclick="otOpenRecentDrop(${idx})">
                <div class="history-dot" style="background:${freqColor};box-shadow:0 0 6px ${freqColor}66;"></div>
                <div class="history-info">
                    <div class="history-principle" style="color:${freqColor};">${esc(principle)}</div>
                    <div class="history-meta">${esc(meta)}</div>
                </div>
                <div class="history-arrow">&#8250;</div>
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
        signal_type: d.signal_type || '',
        universal_coaching: d.coaching || d.universal_coaching || '',
        lt_echo_num: d.sequence || d.lt_echo_num || d.tuning_day || '',
        love_equation: d.love_equation || (d.love_value != null ? { value: d.love_value } : null),
        _fromArchive: true,
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

    // Try loading frequency playlist from CDN. If a stream is already on the
    // shared element, put the old queue back so its label does not change.
    const loadedTracks = otTracks.slice();
    let nextTracks = [];
    let freqPlaylistLoaded = false;
    if (freq) {
        try {
            const freqLower = freq.toLowerCase();
            const res = await fetch(OT_PLAYLIST_CDN + '/' + freqLower + '.json');
            if (res.ok) {
                const rows = otPlaylistRows(await res.json());
                if (rows.length > 0) {
                    otTracks = rows.map(t => otMapCdnTrack(t, signalFolder));
                    nextTracks = otTracks.slice();
                    freqPlaylistLoaded = true;
                }
            }
        } catch (e) {
            console.warn('Frequency playlist not available, using signal-type fallback:', e.message);
        }
    }

    if (!freqPlaylistLoaded) {
        nextTracks = otBuildTracks(signalFolder);
    }

    // List order stays as loaded. Play starts on this tuning's principle, or a random row.
    const startAt = _otStableStart(nextTracks, data && data.principle);

    // Create audio element
    _otEnsureAudio();

    const streamName = freq ? freq + ' Tuning Stream' : signalFolder.replace(/_/g, ' ');
    const streamLabel = streamName + ' (' + nextTracks.length + ' tracks)';

    // Opening Tune is not Play. A loaded stream keeps its label and its place.
    if (otAudio && otAudio.src) {
        otTracks = loadedTracks;
        const mountId = document.getElementById('tfPlayerMount') ? 'tfPlayerMount' : 'otPlayerMount';
        _otPaintPreview(mountId, nextTracks, {
            label: streamName,
            source: 'tune',
            freqColor: window._otFreqColor,
            startIndex: startAt,
        });
        return;
    }

    otTracks = nextTracks;
    _otPlaylistLabel = streamName;
    _otSource = 'tune';
    _otPendingPlay = false;
    otLoadTrack(startAt, false);
    otRenderPlaylist();

    document.querySelectorAll('#tfPlayerMount .ot-playlist-label, #otPlayerMount .ot-playlist-label, #panel-operator-tuning .ot-playlist-label').forEach(el => {
        el.textContent = streamLabel;
    });
    const mpFreqNow = document.getElementById('mpFreq');
    if (mpFreqNow) mpFreqNow.textContent = streamName;

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
 *  Listeners stay on the node. isLive() follows the otAudio pointer, so a
 *  recycled buffer must not get a second set (that double-fires pause and ended). */
function _otBindCurrentAudioEvents() {
    if (!otAudio) return;
    // Live element accepts real pauses. The ignore flag belongs only to a buffer
    // that this handoff is deliberately stopping.
    otAudio._otIgnorePause = false;
    if (otAudio._otEventsBound) return;
    otAudio._otEventsBound = true;

    // Every handler ignores events from a retired buffer element after dual-buffer swap
    // (listeners stay on the node; only the live otAudio pointer is authoritative).
    const isLive = (ev) => ev && ev.target === otAudio;

    otAudio.addEventListener('loadedmetadata', (ev) => {
        if (!isLive(ev) || !otAudio) return;
        _otLogPlayer('loadedmetadata', _otLoadFile());
        document.querySelectorAll('.ot-time-duration').forEach(el => el.textContent = otFmtTime(otAudio.duration));
        _otPublishPosition();
    });
    ['loadstart', 'canplay', 'suspend', 'abort', 'emptied'].forEach((name) => {
        otAudio.addEventListener(name, (ev) => {
            if (!isLive(ev)) return;
            if (name === 'loadstart') {
                _otSounding = false;
                _otReleaseSrcPause();
                _otLoadStamp = Date.now();
                _otPublishPlaybackState();
                otUpdateIcons();
            }
            if (name === 'canplay') {
                const waited = _otLoadStamp ? Math.max(0, Date.now() - _otLoadStamp) : 0;
                _otLogPlayer('canplay', _otLoadFile() + ' ' + waited);
                _otMaybeWarm();
                return;
            }
            _otLogPlayer(name, _otLoadFile());
        });
    });
    otAudio.addEventListener('ended', (ev) => {
        if (!isLive(ev)) return;
        _otSounding = false;
        _otPublishPlaybackState();
        _otLogPlayer('ended', '');
        // Log play_end with duration before advancing
        if (_otPlayStartTime) {
            const dur = (Date.now() - _otPlayStartTime) / 1000;
            _otTrackEvent('echo_play_end', { play_duration: Math.round(dur * 10) / 10 });
            _otPlayStartTime = null;
            _otCurrentTrackLogged = false;
        }
        // Desktop may hand off the buffer that is exactly up next. A phone
        // sets src on this element and play() here, which iOS allows while locked.
        if (_otUpNextValid() && _otPreloadIndex === _otUpNext) {
            _otAbortWarm();
            if (_otHandoffToPreloaded()) return;
        }
        otNext();
    });
    otAudio.addEventListener('play', (ev) => {
        if (!isLive(ev)) return;
        _otResumeCtx();  // desktop safety; no-op on mobile (no context)
        otIsPlaying = true;
        _otWantAudio = true;
        _otNeedsResume = false;
        _otLogPlayer('play', '');
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
        // play fires before any bytes. Do not report playing until sound starts.
        _otPublishPlaybackState();
    });
    otAudio.addEventListener('pause', (ev) => {
        if (!isLive(ev)) return;
        // Dual-buffer swap, and the pause a src change emits, are not the car.
        if ((ev.target && ev.target._otIgnorePause) || _otHoldUserPause) {
            _otLogPlayer('pause', 'src');
            return;
        }
        otIsPlaying = false;
        _otSounding = false;
        _otWantAudio = false;
        _otNeedsResume = false;
        _otClearFaultTimers();
        _otLogPlayer('pause', '');
        // Track pause with duration so far
        if (_otPlayStartTime) {
            const dur = (Date.now() - _otPlayStartTime) / 1000;
            _otTrackEvent('echo_pause', { play_duration: Math.round(dur * 10) / 10 });
            _otPlayStartTime = null;  // Reset so resume creates new segment
        }
        otUpdateIcons();
        otStopProgress();
        _otPublishPlaybackState();
    });
    otAudio.addEventListener('error', (ev) => {
        if (!isLive(ev)) return;
        if (_otReloading) return;
        // MEDIA_ERR_ABORTED (1) is the src swap inside a retry, not a failed load.
        const code = otAudio.error && otAudio.error.code;
        if (code === 1) return;
        // Do not auto-advance on the first load error. That skip loop hammered the CDN.
        // One retry of this track, then the next track, then stop. See _otResolveError.
        console.warn('[player] load error — not skipping');
        _otScheduleError(code ? String(code) : '');
    });
    // Playing resumes the error budget. A stall watch stays until bytes move.
    // Preload waits until this event plus a few seconds of audio, and never
    // while waiting. On a phone there is no second element at all.
    otAudio.addEventListener('playing', (ev) => {
        if (!isLive(ev)) return;
        _otSounding = true;
        _otBuffering = false;
        _otReleaseSrcPause();
        _otNoteAudible();
        _otEnsureUpNext();
        _otMaybeWarm();
        _otSchedulePreload();
        _otPublishPlaybackState();
        otUpdateIcons();
    });
    otAudio.addEventListener('waiting', (ev) => {
        if (!isLive(ev)) return;
        _otWatchStall('waiting');
        _otPublishPlaybackState();
        otUpdateIcons();
    });
    otAudio.addEventListener('stalled', (ev) => {
        if (!isLive(ev)) return;
        _otWatchStall('stalled');
        _otPublishPlaybackState();
        otUpdateIcons();
    });
    // Page timers stop while the phone is locked. timeupdate still fires then.
    otAudio.addEventListener('timeupdate', (ev) => {
        if (!isLive(ev)) return;
        _otMaybeWarm();
    });
}

/** Ensure the shared Audio elements exist. Idempotent.
 *  Volume uses Web Audio API GainNode (set up on first user gesture via _otEnsureGain).
 *  iOS ignores HTMLMediaElement.volume but respects AudioContext gain. */
function _otEnsureAudio() {
    if (otAudio) return;
    otAudio = new Audio();
    _otPrepAudioEl(otAudio);
    // One element on a phone. A preload load() competes with the live file
    // and leaves it at readyState 0 while the preload gets the bytes.
    if (!_otIsMobile()) {
        _otNextAudio = new Audio();
        _otPrepAudioEl(_otNextAudio);
    }

    // Bind lock screen / Bluetooth media controls once, up front, so they exist before
    // the first track loads and never get torn down between tracks (iOS default path).
    _otBindMediaSessionHandlers();
    _otBindCurrentAudioEvents();

    // Set initial volume via .volume (works on desktop, ignored on iOS)
    // iOS ignores .volume — GainNode handles it (see _otEnsureGain)
    const initVol = typeof window._otVolume === 'number' ? window._otVolume : 0.3;
    otAudio.volume = initVol;
    if (_otNextAudio) _otNextAudio.volume = initVol;
    window._otVolume = initVol;

    // Foreground: resume suspended AudioContext (desktop) and retry playback if a
    // background/Bluetooth handoff left us silent with the clock still advancing.
    if (!window._otVisibilityBound) {
        window._otVisibilityBound = true;
        document.addEventListener('visibilitychange', () => {
            _otLogPlayer('visibility', document.hidden ? 'hidden' : 'visible');
            if (document.hidden) return;
            _otResumeCtx();
            _otRecoverPlaybackIfNeeded();
        });
        // Some WebViews fire pageshow on BFCache restore without visibilitychange.
        window.addEventListener('pageshow', () => {
            _otLogPlayer('visibility', 'pageshow');
            _otResumeCtx();
            _otRecoverPlaybackIfNeeded();
        });
    }
}

/** Start playback on otAudio; on rejection while we intended to play, flag resume. */
function _otPlayCurrent(reason) {
    if (!otAudio || !otAudio.src) return Promise.resolve();
    _otWantAudio = true;
    _otClearFailStatus();
    return otAudio.play().then(() => {
        _otNeedsResume = false;
        if (typeof showMiniPlayer === 'function') showMiniPlayer();
    }).catch(err => {
        const rejected = (err && err.name) ? String(err.name) : 'rejected';
        _otLogPlayer('play-blocked', rejected);
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

function _otFileAt(index) {
    const track = otTracks[index];
    return (track && track.filename) ? String(track.filename) : '';
}

function _otUpNextValid() {
    return _otUpNext >= 0 && _otUpNext < otTracks.length
        && (otTracks.length <= 1 || _otUpNext !== otIndex);
}

/** Choose up next once per track. Previous still walks played history. */
function _otEnsureUpNext() {
    if (_otUpNextValid()) return;
    if (!otTracks.length) {
        _otUpNext = -1;
        return;
    }
    const next = _otPickNextIndex();
    _otUpNext = next;
    if (_otWarmFor !== next) _otAbortWarm();
    _otWarmFor = -1;
    _otLogPlayer('upnext', _otFileAt(next));
}

function _otDropUpNext() {
    _otUpNext = -1;
    _otAbortWarm();
    _otWarmFor = -1;
}

function _otAbortWarm() {
    if (!_otWarming && !_otWarmAbort) return;
    _otWarmGen += 1;
    _otWarming = false;
    _otWarmFor = -1;
    const abort = _otWarmAbort;
    _otWarmAbort = null;
    try { if (abort) abort.abort(); } catch (e) {}
}

function _otLiveLoadInProgress() {
    if (_otBuffering || _otSrcDepth > 0) return true;
    if (!otAudio || otAudio.readyState < 4) return true;
    return false;
}

function _otFullyBuffered() {
    if (!otAudio || otAudio.readyState < 4) return false;
    const dur = otAudio.duration;
    if (!isFinite(dur) || dur <= 0) return false;
    return _otBufferEnd(otAudio) >= dur - 0.25;
}

function _otWarmWindowOpen() {
    if (!otAudio) return false;
    const dur = otAudio.duration;
    if (!isFinite(dur) || dur <= 0) return false;
    const pos = (otAudio.currentTime && isFinite(otAudio.currentTime)) ? otAudio.currentTime : 0;
    return pos >= dur - OT_WARM_LEAD_S;
}

function _otMaybeWarm() {
    if (_otWarming || _otWarmFor === _otUpNext) return;
    if (!_otUpNextValid() || _otUpNext === otIndex) return;
    if (_otLiveLoadInProgress()) return;
    if (!_otFullyBuffered() || !_otWarmWindowOpen()) return;
    _otStartWarm(_otUpNext);
}

function _otLogWarm(gen, file, started, status) {
    if (gen !== _otWarmGen) return;
    _otLogPlayer('warm', file + ' ' + (Date.now() - started) + ' ' + status);
    _otWarming = false;
}

/** First megabyte of up next. No second audio element. Discard the body. */
function _otStartWarm(index) {
    const track = otTracks[index];
    const url = track ? otGetAudioUrl(track) : '';
    const file = _otFileAt(index);
    if (!url || !file) return;
    const gen = _otWarmGen;
    const started = Date.now();
    _otWarmFor = index;
    _otWarming = true;
    let signal;
    try {
        if (typeof AbortController === 'function') {
            _otWarmAbort = new AbortController();
            signal = _otWarmAbort.signal;
        }
    } catch (e) {
        _otWarmAbort = null;
    }
    const opts = { headers: { Range: 'bytes=0-1048575' } };
    if (signal) opts.signal = signal;
    let pending;
    try {
        pending = fetch(url, opts);
    } catch (e) {
        _otLogWarm(gen, file, started, 'error');
        return;
    }
    Promise.resolve(pending).then(function (res) {
        if (gen !== _otWarmGen || _otLiveLoadInProgress()) {
            if (gen === _otWarmGen) {
                _otWarming = false;
                _otWarmFor = -1;
            }
            return null;
        }
        const body = (res && typeof res.arrayBuffer === 'function') ? res.arrayBuffer() : null;
        return Promise.resolve(body).then(function () { return res; });
    }).then(function (res) {
        if (!res || gen !== _otWarmGen) return;
        const status = res.status ? res.status : (res.ok ? 200 : 0);
        _otLogPlayer('warm', file + ' ' + (Date.now() - started) + ' ' + status);
    }).catch(function () {
        if (gen !== _otWarmGen) return;
        _otLogPlayer('warm', file + ' ' + (Date.now() - started) + ' error');
    }).then(function () {
        if (gen === _otWarmGen) _otWarming = false;
    });
}

/** Preload a following track onto _otNextAudio without touching the live element. */
function _otPreloadIndexTrack(index) {
    if (_otIsMobile() || !_otNextAudio || index < 0 || index >= otTracks.length) return;
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

function _otCancelPreload() {
    if (_otPreloadTimer) {
        clearTimeout(_otPreloadTimer);
        _otPreloadTimer = null;
    }
}

function _otSchedulePreload() {
    _otCancelPreload();
    // Phones keep a single element. Desktop preloads only after the live
    // track is actually playing and has stayed audible, never while it is waiting.
    if (_otIsMobile()) return;
    if (!otAudio || !otTracks.length || otAudio.paused || !_otWantAudio) return;
    if (_otBuffering || otAudio.readyState < 2) return;
    _otPreloadTimer = setTimeout(() => {
        _otPreloadTimer = null;
        if (_otIsMobile() || !otIsPlaying || !otAudio || otAudio.paused || !_otWantAudio) return;
        if (_otBuffering || otAudio.readyState < 2) return;
        if (!_otUpNextValid()) return;
        _otPreloadIndexTrack(_otUpNext);
    }, OT_PRELOAD_DELAY_MS);
}

/**
 * Swap preloaded next → current and play. Returns true if handoff ran.
 * Keeps Bluetooth/lock audio session warmer than assigning .src on otAudio cold.
 */
function _otHandoffToPreloaded() {
    if (_otIsMobile() || _otPendingPlay) return false;
    if (!_otNextAudio || _otPreloadIndex < 0 || _otPreloadIndex >= otTracks.length) return false;
    // Require the buffer to have data — HAVE_CURRENT_DATA (2)+ 
    if (!(_otNextAudio.readyState >= 2 && _otNextAudio.src)) return false;

    const nextIndex = _otPreloadIndex;
    const retiring = otAudio;
    const incoming = _otNextAudio;

    // Suppress pause analytics on the element we are deliberately stopping.
    // The flag must not stay set once this element is live again, or a car
    // pause is ignored and unlock starts the music by itself.
    // _otEventsBound stays as it is: listeners are already on the node.
    if (retiring) retiring._otIgnorePause = true;
    try { if (retiring && !retiring.paused) retiring.pause(); } catch (e) {}

    // Swap pointers BEFORE clearing the retiring buffer
    otAudio = incoming;
    otAudio._otIgnorePause = false;
    _otNextAudio = retiring || new Audio();
    if (!_otNextAudio.parentNode) _otPrepAudioEl(_otNextAudio);
    // Clear retiring buffer so it can preload the track after this one
    try {
        _otNextAudio.removeAttribute('src');
        _otNextAudio.src = '';
        if (typeof _otNextAudio.load === 'function') _otNextAudio.load();
    } catch (e) {}
    _otPreloadIndex = -1;

    // First time this element is live, attach listeners. A recycled one already has them.
    _otBindCurrentAudioEvents();

    // Volume parity (desktop GainNode stays on first element only — mobile uses .volume)
    const vol = typeof window._otVolume === 'number' ? window._otVolume : 0.3;
    try { otAudio.volume = vol; } catch (e) {}
    try { _otNextAudio.volume = vol; } catch (e) {}

    // Sync UI + media session to the handoff track, then play without reassigning .src
    _otApplyTrackUI(nextIndex, { skipSrc: true });
    _otLogPlayer('handoff', 'preload');
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

function _otHaltBuffers() {
    [otAudio, _otNextAudio].forEach(el => {
        if (!el) return;
        try { el.pause(); } catch (e) {}
    });
    const frame = document.getElementById('drop-frame');
    if (frame && frame.getAttribute('src') && frame.getAttribute('src') !== 'about:blank') {
        try { frame.src = 'about:blank'; } catch (e) {}
    }
}

function otSetPlaylist(tracks, opts) {
    opts = opts || {};
    if (!tracks || tracks.length === 0) return;

    const startAt = (opts.startIndex == null || opts.startIndex < 0) ? 0 : opts.startIndex;

    const live = !!(otAudio && !otAudio.paused && otAudio.src);
    // Opening Tune, Playlists, or Drop is not Play. One rule for all three.
    // A Bluetooth pause is parked, not a new queue. Check that before keep-loaded,
    // or a matching start file replaces the list and the mini-player label.
    if (live && opts.autoplay !== true) {
        _otPaintPreview(opts.mountId, tracks, opts);
        return;
    }
    const parked = !!(otAudio && otAudio.paused && otAudio.src);
    if (parked && opts.autoplay !== true) {
        _otPaintPreview(opts.mountId, tracks, opts);
        return;
    }

    // A chosen queue gets a fresh failure budget. Browsing a parked player does not.
    _otForgiveFailures();

    if (_otTrackIsLoaded(tracks[startAt])) {
        _otKeepLoaded(tracks, opts, startAt);
        return;
    }

    window._otPreview = null;
    _otResetPlayHistory();
    _otDropUpNext();

    // Same as original LC Hermes tuner: Play starts this queue, one engine.
    _otPendingPlay = false;
    _otNeedsResume = false;
    _otPreloadIndex = -1;
    _otHaltBuffers();
    if (_otNextAudio) {
        try {
            _otNextAudio.removeAttribute('src');
            _otNextAudio.src = '';
        } catch (e) {}
    }

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
    _otEnsureUpNext();
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
    // This list is not the one making sound. Its button shows play.
    // The mini bar keeps the real play or pause icon.
    document.querySelectorAll('.ot-play-icon').forEach(el => {
        el.innerHTML = '<polygon points="5,3 19,12 5,21"/>';
    });
    const mpIcon = document.getElementById('mpPlayIcon');
    if (mpIcon) {
        mpIcon.innerHTML = _otAudible()
            ? '<rect x="5" y="4" width="4" height="16"/><rect x="15" y="4" width="4" height="16"/>'
            : '<polygon points="5,3 19,12 5,21"/>';
    }
    // Favorites heart
    _otUpdateFavUI();
}

function otLoadTrack(index, autoplay) {
    if (index < 0 || index >= otTracks.length) return;
    _otEnsureAudio();
    if (autoplay) _otWantAudio = true;
    // Same file already loaded: play from where it is. Do not seek to 0:00.
    if (_otTrackIsLoaded(otTracks[index])) {
        _otApplyTrackUI(index, { skipSrc: true, keepTime: true });
        if (autoplay && otAudio.paused) _otPlayCurrent('same-track');
        else otUpdateIcons();
        if (otAudio && otAudio.currentTime) otUpdateProgressUI();
        return;
    }
    // If this index is already warm on the preload buffer, hand off without a cold src set.
    // Mobile never has that buffer: ended, next, and previous set src here and play().
    if (autoplay && _otPreloadIndex === index && _otHandoffToPreloaded()) return;
    _otWithSrcChange(() => {
        _otApplyTrackUI(index, { skipSrc: false });
        if (autoplay) _otPlayCurrent('load');
    });
}

/**
 * Apply track index to UI + media session. Optionally assign otAudio.src.
 * skipSrc=true is used after dual-buffer handoff (src already on the element).
 */
function _otApplyTrackUI(index, opts) {
    opts = opts || {};
    if (index < 0 || index >= otTracks.length) return;
    _otPendingPlay = false;  // Explicit load = no longer pending
    if (!opts.keepTime) {
        _otCurrentTrackLogged = false;  // Reset for new track
        _otPlayStartTime = null;
    }
    otIndex = index;
    _otRememberPlayed(index);
    const track = otTracks[index];
    if (!opts.skipSrc) {
        // Assigning .src triggers the load automatically. Calling .load() on top of it
        // resets the element and, on iOS, drops the audio session so the next track is
        // blocked while the screen is locked and the lock screen controls go dead.
        otAudio.src = otGetAudioUrl(track);
        _otLogPlayer('load', _otLoadFile());
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
    if (!opts.keepTime) {
        document.querySelectorAll('.ot-progress-bar').forEach(el => el.style.width = '0%');
        document.querySelectorAll('.ot-time-elapsed').forEach(el => el.textContent = '0:00');
        document.querySelectorAll('.ot-time-duration').forEach(el => {
            el.textContent = (otAudio && otAudio.duration) ? otFmtTime(otAudio.duration) : '0:00';
        });
    }

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
    document.querySelectorAll('.ot-cover-img').forEach(el => {
        if (_otInPreview(el)) return;
        el.src = coverUrl; el.alt = track.folder.replace(/_/g, ' '); el.style.display = 'block';
    });
    document.querySelectorAll('.ot-track-title').forEach(el => {
        if (_otInPreview(el)) return;
        el.textContent = track.title;
    });
    document.querySelectorAll('.ot-track-signal').forEach(el => {
        if (_otInPreview(el)) return;
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
    if (!otAudio.src) {
        _otResumeAfterStop();
        return;
    }
    // Icon follows the element, not a flag that can stick after a buffer swap.
    if (_otAudible()) {
        _otNeedsResume = false;
        otAudio.pause();
    } else {
        _otForgiveFailures();
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
    // Play the song stored as up next. A desktop buffer is used only when it
    // is that same index. Then playing picks the following one.
    const idx = _otUpNextValid() ? _otUpNext : _otPickNextIndex();
    _otUpNext = -1;
    _otAbortWarm();
    if (_otPreloadIndex === idx && _otHandoffToPreloaded()) return;
    if (otTracks.length <= 1) { otLoadTrack(0, true); return; }
    otLoadTrack(idx, true);
}

function otPrev() {
    if (otAudio && otAudio.currentTime > 3) {
        otAudio.currentTime = 0;
        _otPublishPosition();
        return;
    }
    // Track skip on prev
    if (otIsPlaying && _otPlayStartTime) {
        const dur = (Date.now() - _otPlayStartTime) / 1000;
        _otTrackEvent('echo_skip', { play_duration: Math.round(dur * 10) / 10 });
        _otPlayStartTime = null;
        _otCurrentTrackLogged = false;
    }
    // Random play makes index-1 a different track than the one just heard.
    // Walk back through tracks that actually played. No history: restart this one.
    let prior = -1;
    if (_otPlayHistory.length >= 2 && _otPlayHistory[_otPlayHistory.length - 1] === otIndex) {
        _otPlayHistory.pop();
        prior = _otPlayHistory[_otPlayHistory.length - 1];
    }
    if (prior < 0 || prior >= otTracks.length) {
        if (otAudio) {
            try { otAudio.currentTime = 0; } catch (e) {}
        }
        _otPublishPosition();
        return;
    }
    otLoadTrack(prior, true);
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
    const audible = _otAudible();
    const loading = _otLoadingSound();
    const svg = audible ? pausePath : playPath;
    document.querySelectorAll('.ot-play-icon').forEach(el => {
        if (_otInPreview(el)) return;
        el.innerHTML = svg;
    });
    document.querySelectorAll('.ot-btn.ot-play, #mpPlayBtn').forEach(el => {
        if (_otInPreview(el)) return;
        el.classList.toggle('is-loading', loading);
        el.title = audible ? 'Pause' : (loading ? 'Loading' : 'Play');
    });
    // Mini player icon
    const mpIcon = document.getElementById('mpPlayIcon');
    if (mpIcon) mpIcon.innerHTML = svg;
    _otPaintLoadStatus();
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
    _otMaybeWarm();
    if (!otAudio || !otAudio.duration) return;
    const pct = (otAudio.currentTime / otAudio.duration) * 100;
    // If pending, only update mini player — tab player shows new playlist at 0%
    document.querySelectorAll('.ot-progress-bar').forEach(el => {
        if (_otInPreview(el)) return;
        el.style.width = pct + '%';
    });
    document.querySelectorAll('.ot-time-elapsed').forEach(el => {
        if (_otInPreview(el)) return;
        el.textContent = otFmtTime(otAudio.currentTime);
    });
    document.querySelectorAll('.ot-time-duration').forEach(el => {
        if (_otInPreview(el)) return;
        el.textContent = otFmtTime(otAudio.duration);
    });
    // Mini player always updates (shows what's actually playing)
    const mpProg = document.getElementById('mpProgress');
    if (mpProg) mpProg.style.width = pct + '%';
    // External callback
    if (_otOnProgress) _otOnProgress(pct, otAudio.currentTime, otAudio.duration);
    _otPublishPosition();
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
        _otLogPlayer('media-session', 'play');
        _otResumeCtx();  // desktop safety; no-op on mobile (no context)
        _otForgiveFailures();
        if (!otAudio || !otAudio.src) {
            _otResumeAfterStop();
            return;
        }
        _otPlayCurrent('mediasession').then(() => {
            _otPublishPlaybackState();
        });
    });
    ms.setActionHandler('pause', () => {
        _otLogPlayer('media-session', 'pause');
        if (!otAudio) return;
        otAudio.pause();
        ms.playbackState = 'paused';
    });
    ms.setActionHandler('previoustrack', () => {
        _otLogPlayer('media-session', 'previoustrack');
        otPrev();
    });
    ms.setActionHandler('nexttrack', () => {
        _otLogPlayer('media-session', 'nexttrack');
        otNext();
    });
    // Null out seek so the OS shows prev/next buttons instead of scrubbers.
    try { ms.setActionHandler('seekbackward', null); } catch(e){}
    try { ms.setActionHandler('seekforward', null); } catch(e){}
    try { ms.setActionHandler('seekto', d => {
        _otLogPlayer('media-session', 'seekto');
        if (d.seekTime != null && otAudio) { otAudio.currentTime = d.seekTime; otUpdateProgressUI(); }
    }); } catch(e){}
}

/** Update the now-playing metadata for the current track. Handlers are bound once in
 *  _otBindMediaSessionHandlers; this only refreshes title/artwork. */
function otSetupMediaSession(track) {
    if (!('mediaSession' in navigator) || !track) return;
    const folder = String(track.folder || '');
    navigator.mediaSession.metadata = new MediaMetadata({
        title: track.title || '',
        artist: 'LUCID TUNER',
        album: folder.replace(/_/g, ' '),
        artwork: [{ src: otGetCoverUrl(track.folder, track.cdnBase), sizes: '512x512', type: 'image/png' }],
    });
    _otPublishPosition();
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
    containers.forEach(c => {
        if (_otInPreview(c)) return;
        c.innerHTML = html;
    });
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

_otLoadPlayerLog();
_otBindPlayerLogCopy();
