// =============================================================================
// Voice — conversation mode + TTS
// =============================================================================
// Two input modes: Text (type) and Voice (continuous mic → send → TTS).
// Stop in Voice is dictation: words land in the box to edit or Send.
// Depends on chat.js being loaded first (uses sendMessage, addSystemMessage, etc.)
// =============================================================================

let chatMode = 'type';     // 'type' | 'voice'  (legacy 'dictate' falls back to type)
let ttsAudio = null;       // currently playing Audio element
const CHAT_MODE_KEY = 'lp_chat_mode';
// voicePhase is the only source of truth for the Voice session.
let voicePhase = 'idle';   // idle | listening | flushing | thinking | speaking | listen-playback
let voiceTransition = false;
let voiceListenResume = false;
let ttsRunId = 0;
let voiceListenGen = 0;
const VOICE_LOG_MAX = 30;
const voiceLog = [];
const VOICE_SESSION_SETTLE_MS = 50;
const VOICE_GUM_RETRY_MS = 300;
const VOICE_TRACK_END_WAIT_MS = 500;

function normalizeChatMode(mode) {
    return mode === 'voice' ? 'voice' : 'type';
}

// The mic (getUserMedia) only exists in a SECURE CONTEXT (HTTPS or localhost). On a
// plain-http self-host (http://box:8204) navigator.mediaDevices is undefined → a
// cryptic crash. Return a clear, actionable message instead (#209e). Empty = OK.
function micSecureContextIssue() {
    if (window.isSecureContext && navigator.mediaDevices && navigator.mediaDevices.getUserMedia) return '';
    return 'Voice needs a secure connection. Set your Cove’s address in the setup checklist to turn on HTTPS, or open this Cove over https:// (or localhost). Then the mic will work.';
}

function voiceLogPush(msg) {
    voiceLog.push({ t: Date.now(), phase: voicePhase, msg: String(msg || '') });
    if (voiceLog.length > VOICE_LOG_MAX) voiceLog.shift();
}

function voiceLogText() {
    return voiceLog.map((row) => {
        const d = new Date(row.t);
        const hh = String(d.getHours()).padStart(2, '0');
        const mm = String(d.getMinutes()).padStart(2, '0');
        const ss = String(d.getSeconds()).padStart(2, '0');
        return hh + ':' + mm + ':' + ss + ' [' + row.phase + '] ' + row.msg;
    }).join('\n');
}

function copyVoiceLog() {
    const text = voiceLogText() || '(empty)';
    const done = () => {
        if (typeof addSystemMessage === 'function') addSystemMessage('Voice log copied.');
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done).catch(() => {
            try { window.prompt('Voice log', text); } catch (e) {}
        });
        return;
    }
    try { window.prompt('Voice log', text); } catch (e) {}
}

function voiceAttachCopyLog(afterEl) {
    if (!afterEl || !afterEl.parentNode) return;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'voice-copy-log';
    btn.textContent = 'Copy voice log';
    btn.addEventListener('click', (ev) => {
        ev.preventDefault();
        copyVoiceLog();
    });
    afterEl.parentNode.insertBefore(btn, afterEl.nextSibling);
}

function voiceFailLine(reason) {
    voiceLogPush('fail ' + reason);
    if (typeof addSystemMessage === 'function') addSystemMessage(reason);
    const box = document.getElementById('chat-messages');
    const last = box ? box.lastElementChild : null;
    voiceAttachCopyLog(last);
}

function setVoicePhase(next, why) {
    const prev = voicePhase;
    voicePhase = next;
    // Derived — assigned only here so they cannot disagree with the phase.
    voiceActive = next !== 'idle';
    ttsPlaying = (next === 'speaking' || next === 'listen-playback');
    voiceHoldTranscripts = (next === 'thinking' || next === 'speaking' || next === 'flushing');
    voiceIgnoringMicLoss = (next !== 'listening');
    voiceFlushInFlight = (next === 'flushing' || voiceTransition);
    voiceLogPush(prev + ' -> ' + next + (why ? ' ' + why : ''));
    if (next === 'flushing') updateMicState('transcribing');
    else if (next === 'listen-playback') updateMicState(chatMode === 'voice' ? 'speaking' : 'idle');
    else updateMicState(next);
}

function voiceCanAcceptTap() {
    return !voiceTransition;
}

async function setVoiceAudioSession(type) {
    try {
        if (!navigator.audioSession) return;
        navigator.audioSession.type = type;
    } catch (e) {}
}

function voiceWait(ms) {
    return new Promise((r) => setTimeout(r, ms));
}

const voiceMic = {
    async open() {
        await setVoiceAudioSession('play-and-record');
        await voiceWait(VOICE_SESSION_SETTLE_MS);
        const _secIssue = micSecureContextIssue();
        if (_secIssue) throw new Error(_secIssue);
        let stream = null;
        try {
            stream = await navigator.mediaDevices.getUserMedia({
                audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true }
            });
        } catch (e1) {
            await voiceWait(VOICE_GUM_RETRY_MS);
            stream = await navigator.mediaDevices.getUserMedia({
                audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true }
            });
        }
        micStream = stream;
        micAudioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
        bindVoiceInterruptWatchers(micStream);
        const src = micAudioCtx.createMediaStreamSource(micStream);
        micProcessor = micAudioCtx.createScriptProcessor(4096, 1, 1);
        micProcessor.onaudioprocess = (e) => {
            if (!micRecording || !micWs || micWs.readyState !== WebSocket.OPEN) return;
            const f32 = e.inputBuffer.getChannelData(0);
            const i16 = new Int16Array(f32.length);
            for (let i = 0; i < f32.length; i++) {
                let s = Math.max(-1, Math.min(1, f32[i]));
                i16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
            }
            micWs.send(i16.buffer);
            if (voicePhase !== 'listening') return;
            const rms = Math.sqrt(f32.reduce((sum, v) => sum + v * v, 0) / f32.length);
            if (rms > SILENCE_THRESHOLD) {
                speechSeen = true;
                silenceStart = 0;
            } else if (speechSeen) {
                if (!silenceStart) {
                    silenceStart = Date.now();
                } else if (Date.now() - silenceStart > SILENCE_DURATION) {
                    silenceStart = 0;
                    speechSeen = false;
                    micWs.send(JSON.stringify({ type: 'end_audio', mode: 'transcribe' }));
                    voiceChunkPending = true;
                    updateMicState('transcribing');
                }
            }
        };
        src.connect(micProcessor);
        micProcessor.connect(micAudioCtx.destination);
        micRecording = true;
        speechSeen = false;
        silenceStart = 0;
    },
    async pause() {
        return this._teardown();
    },
    async close() {
        return this._teardown();
    },
    async _teardown() {
        unbindVoiceInterruptWatchers();
        micRecording = false;
        if (micProcessor) { try { micProcessor.disconnect(); } catch (e) {} micProcessor = null; }
        if (micAudioCtx) { try { micAudioCtx.close(); } catch (e) {} micAudioCtx = null; }
        const stream = micStream;
        micStream = null;
        const tracks = (stream && stream.getTracks) ? stream.getTracks() : [];
        await Promise.all(tracks.map((t) => new Promise((res) => {
            let done = false;
            const finish = () => { if (done) return; done = true; res(); };
            if (!t || t.readyState === 'ended') { finish(); return; }
            try { t.addEventListener('ended', finish); } catch (e) {}
            try { t.stop(); } catch (e) { finish(); return; }
            setTimeout(finish, VOICE_TRACK_END_WAIT_MS);
        })));
    },
};

async function voiceFailToIdle(reason) {
    voiceTransition = true;
    try {
        ttsStopPlayback();
        await voiceMic.close();
        parkVoiceDraft();
        if (micWs) { try { micWs.close(); } catch (e) {} micWs = null; }
        setVoicePhase('idle', reason);
        voiceFailLine(reason);
    } finally {
        voiceTransition = false;
        voiceFlushInFlight = false;
        voiceListenResume = false;
    }
}

function switchChatMode(mode) {
    mode = normalizeChatMode(mode);
    if (mode === chatMode) {
        try { localStorage.setItem(CHAT_MODE_KEY, mode); } catch (e) {}
        return;
    }

    // Stop any active recording, voice conversation, or TTS
    if (voicePhase !== 'idle') voiceStop();
    else if (micRecording) voiceMic.close();
    ttsStopPlayback();

    chatMode = mode;
    try { localStorage.setItem(CHAT_MODE_KEY, mode); } catch (e) {}

    // Update mode bar buttons
    document.querySelectorAll('.chat-mode-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.mode === mode);
    });

    const composer = document.querySelector('.chat-input-area');
    if (composer) composer.classList.toggle('chat-voice-composer', mode === 'voice');

    const micBtn = getMicBtn();
    const sendBtn = document.getElementById('chat-send');
    const input = document.getElementById('chat-input');
    const micLabel = document.getElementById('mic-label');

    if (mode === 'type') {
        if (input) input.style.display = '';
        if (micBtn) micBtn.style.display = 'none';
        if (sendBtn) sendBtn.style.display = '';
        if (input) input.placeholder = `Ask ${_getActiveAIName()} anything...`;
        setVoiceListenHint(false);
    } else {
        if (input) input.style.display = '';
        if (micBtn) micBtn.style.display = '';
        if (sendBtn) sendBtn.style.display = '';
        if (micLabel) micLabel.textContent = 'Tap to Talk';
    }
}

function restoreChatMode() {
    let saved = 'type';
    try { saved = localStorage.getItem(CHAT_MODE_KEY) || 'type'; } catch (e) {}
    const next = normalizeChatMode(saved);
    if (next === chatMode) {
        try { localStorage.setItem(CHAT_MODE_KEY, next); } catch (e) {}
        return;
    }
    switchChatMode(next);
}

function setVoiceListenHint(on) {
    const hint = document.getElementById('voice-listen-hint');
    if (!hint) return;
    if (on) hint.removeAttribute('hidden');
    else hint.setAttribute('hidden', '');
}

// =============================================================================
// Voice conversation mode — hands-free with trigger word
// =============================================================================
// Tap once to start. Speak naturally. Say "Over" to send.
// Agent responds in text + audio. Auto-resumes listening.
// Tap again to end the conversation.
// =============================================================================
let voiceActive = false;            // true while voice conversation is running
let voicePartial = '';              // accumulated text between trigger sends
let silenceStart = 0;               // timestamp when silence began
let speechSeen = false;             // whether speech detected in current chunk
const SILENCE_THRESHOLD = 0.015;    // RMS below this = silence
const SILENCE_DURATION = 2500;      // ms of silence before auto-chunk (was 1800 — too aggressive for mobile)
const TRIGGER_PHRASES = ['send it', 'sendit', 'over'];
const VOICE_END_FILLERS = ['um', 'uh', 'er', 'ah', 'please', 'yeah', 'okay', 'ok'];
const VOICE_FLUSH_TIMEOUT_MS = 8000;
let voiceIgnoringMicLoss = false;   // true while we tear down the mic ourselves
let voiceFlushInFlight = false;
let voiceChunkPending = false;      // end_audio sent, transcript not yet back
let _voiceFlushWaiter = null;
let _voiceVisHandler = null;
let _voicePageHideHandler = null;
let _voiceTrackEnded = [];          // [{track, fn}, ...]
let _voiceCtxHandler = null;
let _voiceBoundCtx = null;

// AudioContext for TTS — created on user gesture for mobile compatibility
let ttsAudioCtx = null;
let ttsSource = null;
let ttsResolve = null;              // promise resolver for playTTS completion

function ensureTTSAudioCtx() {
    if (ttsAudioCtx && ttsAudioCtx.state === 'closed') {
        ttsAudioCtx = null;
    }
    if (!ttsAudioCtx) {
        ttsAudioCtx = new (window.AudioContext || window.webkitAudioContext)();
    }
    // iOS parks this as suspended or interrupted after the mic is released.
    if (ttsAudioCtx.state === 'suspended' || ttsAudioCtx.state === 'interrupted') {
        try { ttsAudioCtx.resume(); } catch (e) {}
    }
    // Play silent buffer to fully unlock audio on mobile
    try {
        const silent = ttsAudioCtx.createBuffer(1, 1, 22050);
        const src = ttsAudioCtx.createBufferSource();
        src.buffer = silent;
        src.connect(ttsAudioCtx.destination);
        src.start(0);
    } catch (e) {}
    return ttsAudioCtx;
}

async function resumeTTSAudioCtx(timeoutMs, runId) {
    const ctx = ensureTTSAudioCtx();
    if (!ctx || ctx.state === 'running') return ctx;
    const deadline = Date.now() + (timeoutMs || TTS_CTX_RESUME_MS);
    while (Date.now() < deadline && ctx.state !== 'running') {
        if (runId != null && runId !== ttsRunId) return ctx;
        if (ctx.state === 'suspended' || ctx.state === 'interrupted') {
            try { await ctx.resume(); } catch (e) {}
        }
        if (ctx.state === 'running') return ctx;
        await new Promise((r) => setTimeout(r, 50));
    }
    return ctx;
}

function decodeTTSAudioData(ctx, arrayBuffer) {
    return new Promise((resolve, reject) => {
        let settled = false;
        const finish = (fn, val) => {
            if (settled) return;
            settled = true;
            clearTimeout(timer);
            fn(val);
        };
        const timer = setTimeout(() => {
            ttsLastError = ttsLastError || 'timeout';
            finish(reject, new Error('decode timeout'));
        }, TTS_DECODE_TIMEOUT_MS);
        let p;
        try {
            p = ctx.decodeAudioData(arrayBuffer.slice(0));
        } catch (e) {
            ttsLastError = ttsLastError || 'timeout';
            finish(reject, e);
            return;
        }
        Promise.resolve(p).then(
            (buf) => finish(resolve, buf),
            (err) => {
                ttsLastError = ttsLastError || 'timeout';
                finish(reject, err);
            }
        );
    });
}

function ttsStopPlayback() {
    ttsRunId += 1;
    ttsCancelled = true;
    // Abort any in-flight TTS fetch requests immediately
    if (ttsAbort) { ttsAbort.abort(); ttsAbort = null; }
    if (ttsSource) {
        try { ttsSource.stop(); } catch (e) {}
        ttsSource = null;
    }
    ttsQueue = [];
    ttsAudio = null;
    if (ttsResolve) { ttsResolve(); ttsResolve = null; }
    const voiceBtn = document.getElementById('mode-voice');
    if (voiceBtn) voiceBtn.classList.remove('playing');
}

// ── Chunked TTS — split into sentences, pipeline fetch + playback ────────────
// Prefetch is BOUNDED (not all-at-once). Full-parallel fetch stampeded single-
// threaded Piper and dropped middle sentences (timeout → null → skip). Lookahead
// of 1 keeps sentence N+1 synthesizing while N plays without thrashing the host.
// Each chunk has a hard timeout — a hung voice host must never pin the chat UI
// on Stop / "speaking" (parents + mesh path). Fail open: keep the text reply.

const TTS_CHUNK_TIMEOUT_MS = 15000;   // hard cap per sentence fetch
const TTS_DECODE_TIMEOUT_MS = 8000;   // decodeAudioData must not hang speaking
const TTS_CTX_RESUME_MS = 1500;       // if still not running, fail open
const TTS_PLAY_PAD_MS = 2000;         // each buffer: duration + this, not a 60s cap
const TTS_PREFETCH = 1;               // synthesize this many sentences ahead of play
const TTS_MAX_CHARS = 900;            // hard cap spoken text (rest stays on screen)
const TTS_MAX_SENTENCES = 8;          // don't queue endless TTS for long replies

let ttsQueue = [];       // array of Promises that resolve to AudioBuffer (sparse)
let ttsPlaying = false;  // true while audio chain is playing
let ttsCancelled = false;
let ttsAbort = null;     // AbortController — cancels pending TTS fetches on stop
let ttsLastError = '';   // last fail reason for UI ('timeout' | 'unreachable' | 'empty' | '')

/** Strip markdown / chat chrome so Piper doesn't read asterisks and list markers. */
function prepareSpeakText(text) {
    if (!text) return '';
    let t = String(text);
    // Fenced code → short spoken stub (don't dump source)
    t = t.replace(/```[\s\S]*?```/g, ' Code block. ');
    t = t.replace(/`([^`]+)`/g, '$1');
    // Links: keep label
    t = t.replace(/\[([^\]]+)\]\([^)]+\)/g, '$1');
    // Headings / bold / italic markers
    t = t.replace(/^#{1,6}\s+/gm, '');
    t = t.replace(/(\*\*|__)(.*?)\1/g, '$2');
    t = t.replace(/(\*|_)(.*?)\1/g, '$2');
    // List bullets
    t = t.replace(/^\s*[-*+]\s+/gm, '');
    t = t.replace(/^\s*\d+\.\s+/gm, '');
    // Collapse whitespace
    t = t.replace(/\r\n/g, '\n').replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n');
    t = t.replace(/[ \t]{2,}/g, ' ').trim();
    if (t.length > TTS_MAX_CHARS) {
        // Cut on a sentence boundary when we can
        let cut = t.slice(0, TTS_MAX_CHARS);
        const m = cut.match(/^[\s\S]*[.!?](?=\s|$)/);
        if (m && m[0].length > 40) cut = m[0];
        t = cut.trim();
    }
    return t;
}

function splitSentences(text) {
    // Split on sentence-ending punctuation or paragraph breaks.
    // Keep short fragments together (under 20 chars) with the previous sentence.
    // Also merge when a chunk would leave a tiny orphan after abbreviations is hard;
    // we only coalesce very short tails.
    const cleaned = prepareSpeakText(text);
    if (!cleaned) return [];
    const raw = cleaned.match(/[^.!?\n]+[.!?]+[\s]*|[^.!?\n]+(?:\n+|$)/g) || [cleaned];
    const merged = [];
    for (const chunk of raw) {
        const trimmed = chunk.replace(/\s+/g, ' ').trim();
        if (!trimmed) continue;
        if (merged.length > 0 && trimmed.length < 20) {
            merged[merged.length - 1] += ' ' + trimmed;
        } else {
            merged.push(trimmed);
        }
    }
    if (merged.length === 0) return cleaned ? [cleaned] : [];
    if (merged.length > TTS_MAX_SENTENCES) {
        return merged.slice(0, TTS_MAX_SENTENCES);
    }
    return merged;
}

function fetchTTSChunk(text) {
    // Prefer same-origin proxy so speak works whenever the Cove page loads —
    // browser never has to reach voice.{domain} on its own (mesh/laptop hang).
    // Fall back to direct voice URL only if the proxy path is somehow missing.
    const directBase = (typeof MC !== 'undefined' && MC.voiceUrl) ? MC.voiceUrl('http') : '';
    const ttsUrls = ['/api/voice/tts'];
    if (directBase) ttsUrls.push(`${directBase.replace(/\/+$/, '')}/api/tts`);

    // Use active chat agent (Stuart/Mercer tab) not hostname, so TTS voice matches who's talking
    const rawAgentName = (typeof activeAgent !== 'undefined' && activeAgent?.id) || location.hostname.split('.')[0] || '';
    // Normalize: strip -cove/_cove suffixes for consistent key lookup
    const agentName = rawAgentName.toLowerCase().replace(/-cove$/, '').replace(/_cove$/, '').trim();

    // Check for a stored voice preference from Settings
    // Try normalized key first, then raw key
    const voiceOverride = MC.features?.[`voice_${agentName}`]
        || MC.features?.[`voice_${rawAgentName}`]
        || '';

    const body = JSON.stringify({ text: text, agent: agentName, voice: voiceOverride });

    // Per-chunk controller so a timeout aborts this fetch without killing siblings.
    // Also aborts if the conversation-level ttsAbort fires (user stop / mode switch).
    const chunkAbort = new AbortController();
    let timedOut = false;
    const timer = setTimeout(() => {
        timedOut = true;
        try { chunkAbort.abort(); } catch (e) {}
    }, TTS_CHUNK_TIMEOUT_MS);

    const onParentAbort = () => {
        try { chunkAbort.abort(); } catch (e) {}
    };
    if (ttsAbort) {
        if (ttsAbort.signal.aborted) onParentAbort();
        else ttsAbort.signal.addEventListener('abort', onParentAbort, { once: true });
    }

    async function tryUrl(url) {
        const res = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body,
            signal: chunkAbort.signal,
            credentials: 'same-origin',
        });
        if (!res.ok) {
            const err = new Error(`TTS HTTP ${res.status}`);
            err.status = res.status;
            throw err;
        }
        const arrayBuffer = await res.arrayBuffer();
        const ctx = ensureTTSAudioCtx();
        return await decodeTTSAudioData(ctx, arrayBuffer);
    }

    // Try proxy first; on network/5xx fall through to direct voice host once.
    return (async () => {
        let lastErr = null;
        for (let i = 0; i < ttsUrls.length; i++) {
            if (chunkAbort.signal.aborted) break;
            try {
                return await tryUrl(ttsUrls[i]);
            } catch (err) {
                lastErr = err;
                // Don't fall through after user cancel / our timeout
                if (timedOut || (err && err.name === 'AbortError')) throw err;
                // 4xx from proxy (bad text etc.) won't help on direct either
                if (err && err.status && err.status >= 400 && err.status < 500) throw err;
                // else try next URL
            }
        }
        throw lastErr || new Error('TTS failed');
    })().catch((err) => {
        if (timedOut) ttsLastError = 'timeout';
        else if (err && err.name === 'AbortError') { /* user cancel — leave quiet */ }
        else ttsLastError = ttsLastError || 'fetch';
        return null;
    }).finally(() => {
        clearTimeout(timer);
        if (ttsAbort) ttsAbort.signal.removeEventListener('abort', onParentAbort);
    });
}

function playTTS(text, opts) {
    return startVoicePlayback(text, opts);
}

function startVoicePlayback(text, opts) {
    return new Promise(async (resolve) => {
        opts = opts || {};
        // Auto-speak only in Voice. Listen on a text reply passes {force:true}.
        if (!text || (chatMode !== 'voice' && !opts.force)) { resolve(); return; }

        // Always end the previous run first. A superseded loop checks ttsRunId after every await.
        ttsStopPlayback();
        const myId = ttsRunId;
        ttsResolve = resolve;
        ttsCancelled = false;
        ttsLastError = '';
        ttsAbort = new AbortController();

        const voiceBtn = document.getElementById('mode-voice');
        if (voiceBtn) voiceBtn.classList.add('playing');

        const ctxReady = await resumeTTSAudioCtx(TTS_CTX_RESUME_MS, myId);
        if (myId !== ttsRunId) { resolve(); return; }
        if (!ctxReady || ctxReady.state !== 'running') {
            const st = ctxReady ? ctxReady.state : 'missing';
            ttsLastError = 'context ' + st;
            if (typeof addSystemMessage === 'function') {
                addSystemMessage('Could not speak that reply. Text is on screen. (context ' + st + ')');
            }
            if (myId === ttsRunId) ttsStopPlayback();
            resolve();
            return;
        }

        const sentences = splitSentences(text);
        let playedAny = false;
        let skippedMiddle = 0;

        ttsQueue = new Array(sentences.length);
        function ensureFetch(idx) {
            if (idx < 0 || idx >= sentences.length) return;
            if (myId !== ttsRunId) return;
            if (!ttsQueue[idx]) {
                ttsQueue[idx] = fetchTTSChunk(sentences[idx]);
            }
        }
        for (let j = 0; j <= TTS_PREFETCH && j < sentences.length; j++) {
            ensureFetch(j);
        }

        for (let i = 0; i < sentences.length; i++) {
            if (myId !== ttsRunId) break;

            ensureFetch(i);
            ensureFetch(i + 1);
            for (let k = 2; k <= TTS_PREFETCH + 1; k++) ensureFetch(i + k);

            const audioBuffer = await ttsQueue[i];
            if (myId !== ttsRunId) break;
            if (!audioBuffer) {
                skippedMiddle += 1;
                continue;
            }

            playedAny = true;
            await new Promise((chunkDone) => {
                let finished = false;
                const done = () => {
                    if (finished) return;
                    finished = true;
                    clearTimeout(playTimer);
                    ttsSource = null;
                    chunkDone();
                };
                const playMs = Math.max(500, (audioBuffer.duration * 1000) + TTS_PLAY_PAD_MS);
                const playTimer = setTimeout(() => {
                    ttsLastError = ttsLastError || 'timeout';
                    done();
                }, playMs);
                if (myId !== ttsRunId) { done(); return; }
                try {
                    const ctx = ensureTTSAudioCtx();
                    if (ctx.state !== 'running') {
                        ttsLastError = ttsLastError || ('context ' + ctx.state);
                        done();
                        return;
                    }
                    ttsSource = ctx.createBufferSource();
                    ttsSource.buffer = audioBuffer;
                    ttsSource.connect(ctx.destination);
                    ttsSource.onended = done;
                    ttsSource.start(0);
                } catch (e) {
                    ttsLastError = ttsLastError || 'empty';
                    done();
                }
            });
        }

        if (myId !== ttsRunId) { resolve(); return; }
        ttsQueue = [];
        const err = ttsLastError;
        const voiceBtn2 = document.getElementById('mode-voice');
        if (voiceBtn2) voiceBtn2.classList.remove('playing');
        ttsAbort = null;
        ttsResolve = null;
        if (typeof addSystemMessage === 'function') {
            if (!playedAny && err) {
                addSystemMessage('Could not speak that reply. Text is on screen. (' + err + ')');
            } else if (playedAny && skippedMiddle > 0) {
                addSystemMessage('Some of that reply could not be spoken — full text is on screen. (' + err + ')');
            }
        }
        resolve();
    });
}

async function voicePreparePlayback() {
    // Capture must be fully ended before playback is set. Never same tick as stopping tracks.
    if (micStream || micRecording) {
        await voiceMic.pause();
    }
    await setVoiceAudioSession('playback');
}

// ── Voice conversation lifecycle ─────────────────────────────────────────────

function syncVoiceBox(text) {
    const input = document.getElementById('chat-input');
    const sendBtn = document.getElementById('chat-send');
    if (input) {
        if (typeof text === 'string') {
            input.value = text;
            input.style.height = 'auto';
            input.style.height = Math.min(input.scrollHeight, 150) + 'px';
        }
        input.style.display = '';
    }
    if (sendBtn) sendBtn.style.display = '';
    updatePartialDisplay('');
}

function parkVoiceDraft() {
    // Never drop heard words. Stop, a failed send, or a mic interrupt leaves
    // the transcript in the message box so it can be sent or edited.
    const draft = (typeof voicePartial === 'string') ? voicePartial.trim() : '';
    const input = document.getElementById('chat-input');
    if (input && draft && !String(input.value || '').trim()) {
        syncVoiceBox(draft);
    } else {
        syncVoiceBox();
    }
    voicePartial = '';
}

function voiceBoxHasWords() {
    const input = document.getElementById('chat-input');
    return !!(input && String(input.value || '').trim());
}

function voiceStopCaptureKeepSocket() {
    // Capture teardown is owned by voiceMic (getUserMedia + track.stop).
    return voiceMic.close();
}

function voiceWaitForFlushTranscript() {
    return new Promise((resolve) => {
        let settled = false;
        const finish = (text) => {
            if (settled) return;
            settled = true;
            if (_voiceFlushWaiter && _voiceFlushWaiter.timer) {
                clearTimeout(_voiceFlushWaiter.timer);
            }
            _voiceFlushWaiter = null;
            resolve(text || '');
        };
        _voiceFlushWaiter = {
            onTranscript(text) { finish(text); },
            onDone() { finish(''); },
            timer: setTimeout(() => finish(''), VOICE_FLUSH_TIMEOUT_MS),
        };
    });
}

async function voiceFlushOpenChunk() {
    // Audio is only transcribed after end_audio. Stop / Send / interrupt must
    // flush the open chunk and wait briefly before parking or sending.
    const wsOpen = micWs && micWs.readyState === WebSocket.OPEN;
    const needsEnd = !voiceChunkPending && (speechSeen || silenceStart);
    if (needsEnd && wsOpen) {
        updateMicState('transcribing');
        try {
            micWs.send(JSON.stringify({ type: 'end_audio', mode: 'transcribe' }));
            voiceChunkPending = true;
        } catch (e) {
            return '';
        }
        speechSeen = false;
        silenceStart = 0;
    } else if (voiceChunkPending && wsOpen) {
        updateMicState('transcribing');
    } else {
        return '';
    }
    return await voiceWaitForFlushTranscript();
}

let voiceHoldTranscripts = false;  // true from send until listening resumes

function voiceDropIncomingTranscript() {
    return voicePhase === 'thinking' || voicePhase === 'speaking' || voicePhase === 'flushing';
}

function voiceBeginSentTurn() {
    // Over or Send: ignore leftover audio until we are listening again.
    voiceHoldTranscripts = true;
    setVoicePhase('thinking', 'sent');
    voicePartial = '';
    speechSeen = false;
    silenceStart = 0;
    voiceChunkPending = false;
    if (typeof updatePartialDisplay === 'function') updatePartialDisplay('');
    syncVoiceBox('');
}

function voiceResumeListening() {
    voiceListenResume = false;
    voicePartial = '';
    speechSeen = false;
    silenceStart = 0;
    voiceChunkPending = false;
    syncVoiceBox('');
    const sendBtn = document.getElementById('chat-send');
    const stopBtn = document.getElementById('chat-stop');
    const micBtn = getMicBtn();
    if (stopBtn) {
        stopBtn.style.display = 'none';
        stopBtn.disabled = false;
        stopBtn.classList.remove('btn-busy');
        stopBtn.title = '';
    }
    if (sendBtn) {
        sendBtn.style.display = '';
        sendBtn.disabled = false;
    }
    if (micBtn) micBtn.style.display = '';
    if (typeof chatMode !== 'undefined' && chatMode === 'voice' && voicePhase !== 'idle') {
        setVoicePhase('listening', 'resume');
        if (!micRecording && typeof micStartContinuous === 'function') micStartContinuous();
    }
}

function absorbVoiceTail(text) {
    if (voiceDropIncomingTranscript()) return;
    if (!text || isHallucinatedTranscript(text)) return;
    voicePartial = (voicePartial + ' ' + text).trim();
    const input = document.getElementById('chat-input');
    const boxed = input ? String(input.value || '').trim() : '';
    if (!boxed || boxed === String(voicePartial).trim().slice(0, boxed.length)) {
        syncVoiceBox(voicePartial);
    } else {
        syncVoiceBox((boxed + ' ' + text).trim());
        voicePartial = String(input.value || '').trim();
    }
}

async function voiceInterruptSession(reason) {
    // Phone call / lost mic / backgrounded PWA: end the session and wait for a
    // tap. Do not auto-resume — iOS will not hand the mic back without a gesture.
    // Interrupt events are honored only in listening; other phases already closed capture.
    if (voicePhase !== 'listening') return;
    const tag = reason ? ` (${reason})` : '';
    await voiceStop();
    if (typeof addSystemMessage !== 'function') return;
    if (voiceBoxHasWords()) {
        addSystemMessage('Voice paused' + tag + ' — send or edit what’s in the box, then tap to talk.');
    } else {
        addSystemMessage('Voice paused' + tag + ' — nothing was captured. Tap to talk.');
    }
}

function unbindVoiceInterruptWatchers() {
    if (_voiceVisHandler) {
        document.removeEventListener('visibilitychange', _voiceVisHandler);
        _voiceVisHandler = null;
    }
    if (_voicePageHideHandler) {
        window.removeEventListener('pagehide', _voicePageHideHandler);
        _voicePageHideHandler = null;
    }
    _voiceTrackEnded.forEach(({ track, fn }) => {
        try { track.removeEventListener('ended', fn); } catch (e) {}
    });
    _voiceTrackEnded = [];
    if (_voiceBoundCtx && _voiceCtxHandler) {
        try { _voiceBoundCtx.removeEventListener('statechange', _voiceCtxHandler); } catch (e) {}
    }
    _voiceBoundCtx = null;
    _voiceCtxHandler = null;
}

function bindVoiceInterruptWatchers(stream) {
    unbindVoiceInterruptWatchers();
    _voiceVisHandler = () => {
        if (document.hidden) voiceInterruptSession('app in background');
    };
    document.addEventListener('visibilitychange', _voiceVisHandler);
    _voicePageHideHandler = () => { voiceInterruptSession('app in background'); };
    window.addEventListener('pagehide', _voicePageHideHandler);
    const tracks = (stream && stream.getAudioTracks) ? stream.getAudioTracks() : [];
    tracks.forEach((track) => {
        const fn = () => { voiceInterruptSession('mic ended'); };
        track.addEventListener('ended', fn);
        _voiceTrackEnded.push({ track, fn });
    });
    const ctx = micAudioCtx;
    if (ctx && typeof ctx.addEventListener === 'function') {
        _voiceCtxHandler = () => {
            if (voicePhase !== 'listening') return;
            if (ctx.state === 'interrupted') {
                voiceInterruptSession('audio interrupted');
                return;
            }
            if (ctx.state === 'suspended') {
                // iOS parks the context on a speech pause; resume and stay in session.
                const p = (typeof ctx.resume === 'function') ? ctx.resume() : Promise.resolve();
                Promise.resolve(p).catch(() => {}).then(() => {
                    if (ctx.state === 'suspended' && voicePhase === 'listening') {
                        voiceInterruptSession('audio interrupted');
                    }
                });
            }
        };
        ctx.addEventListener('statechange', _voiceCtxHandler);
        _voiceBoundCtx = ctx;
        if (ctx.state === 'suspended' && typeof ctx.resume === 'function') {
            ctx.resume().catch(() => {});
        }
    }
}

function voiceStart() {
    if (voicePhase !== 'idle' || sending || voiceTransition) return;
    if (!voiceCanAcceptTap()) return;

    ensureTTSAudioCtx();   // unlock audio on user gesture
    voicePartial = '';
    speechSeen = false;
    silenceStart = 0;
    voiceChunkPending = false;
    voiceListenResume = false;
    setVoicePhase('listening', 'tap');
    updatePartialDisplay('');
    bindVoiceInterruptWatchers(null);
    micStartContinuous();
}

async function voiceStop() {
    // Stop always wins and ends in idle, even during a transition.
    voiceTransition = true;
    voiceFlushInFlight = true;
    unbindVoiceInterruptWatchers();
    ttsStopPlayback();
    setVoicePhase('flushing', 'stop');
    try {
        const tail = await voiceFlushOpenChunk();
        await voiceStopCaptureKeepSocket();
        absorbVoiceTail(tail);
        parkVoiceDraft();
    } catch (e) {
        parkVoiceDraft();
        voiceFailLine('Voice stopped. (' + (e && e.message ? e.message : 'error') + ')');
    }
    speechSeen = false;
    silenceStart = 0;
    voiceChunkPending = false;
    voiceListenResume = false;
    if (micWs) { try { micWs.close(); } catch (e) {} micWs = null; }
    micProcessing = false;
    setVoicePhase('idle', 'stop');
    voiceTransition = false;
    voiceFlushInFlight = false;
}

function micPause() {
    // Stop mic input but keep state for auto-resume after a sent turn.
    // Unbind first so track-ended / AudioContext close is not treated as a call.
    voiceIgnoringMicLoss = true;
    unbindVoiceInterruptWatchers();
    return voiceStopCaptureKeepSocket();
}

async function voiceBeforeSend() {
    // Send-by-tap while listening: flush the open chunk into the box first.
    // Unlock TTS in this same tap — iOS will not resume audio later without it.
    if (typeof chatMode !== 'undefined' && chatMode === 'voice') ensureTTSAudioCtx();
    if (typeof chatMode === 'undefined' || chatMode !== 'voice' || voicePhase === 'idle') return;
    if (voicePhase === 'flushing' || voiceTransition) return;
    if (!speechSeen && !silenceStart && !voiceChunkPending) return;
    voiceTransition = true;
    voiceFlushInFlight = true;
    setVoicePhase('flushing', 'send');
    const sendBtn = document.getElementById('chat-send');
    if (sendBtn) sendBtn.disabled = true;
    unbindVoiceInterruptWatchers();
    try {
        const tailP = voiceFlushOpenChunk();
        await voiceStopCaptureKeepSocket();
        const tail = await tailP;
        absorbVoiceTail(tail);
        const stripped = stripVoiceEndTrigger(voicePartial);
        if (stripped !== null) {
            voicePartial = stripped;
            syncVoiceBox(stripped);
        }
        const input = document.getElementById('chat-input');
        if (input && voicePartial && !String(input.value || '').trim()) {
            syncVoiceBox(voicePartial);
        }
    } finally {
        voiceTransition = false;
        voiceFlushInFlight = false;
        if (sendBtn && !sending) sendBtn.disabled = false;
    }
}

function updateMicState(state) {
    const btn = getMicBtn();
    const label = document.getElementById('mic-label');
    if (!btn || !label) return;

    btn.classList.remove('recording', 'processing', 'speaking');
    // title tracks state so a long label still has a short hover/accessibility cue
    switch (state) {
        case 'idle':
            label.textContent = 'Tap to Talk';
            btn.title = 'Tap to talk';
            setVoiceListenHint(false);
            break;
        case 'listening':
            btn.classList.add('recording');
            label.textContent = 'Listening...';
            btn.title = 'Listening';
            setVoiceListenHint(true);
            break;
        case 'transcribing':
            btn.classList.add('processing');
            label.textContent = 'Transcribing...';
            btn.title = 'Transcribing';
            setVoiceListenHint(false);
            break;
        case 'thinking':
            btn.classList.add('processing');
            label.textContent = _getActiveAIName() + ' thinking...';
            btn.title = 'Thinking';
            setVoiceListenHint(false);
            break;
        case 'speaking':
            btn.classList.add('speaking');
            label.textContent = _getActiveAIName() + ' speaking...';
            btn.title = 'Speaking — tap Stop';
            setVoiceListenHint(false);
            break;
        case 'unavailable':
            btn.classList.add('processing');
            label.textContent = 'Voice unavailable';
            btn.title = 'Voice unavailable';
            setVoiceListenHint(false);
            break;
    }
}

function updatePartialDisplay(text) {
    const el = document.getElementById('voice-partial');
    if (!el) return;
    // Voice uses the message box for running text — do not also show the preview.
    if (typeof chatMode !== 'undefined' && chatMode === 'voice') {
        el.textContent = '';
        el.style.display = 'none';
        return;
    }
    el.textContent = text;
    el.style.display = text ? '' : 'none';
}

// ── Continuous mic with silence detection ────────────────────────────────────

async function micStartContinuous() {
    if (voicePhase === 'idle') return;

    // Connect WebSocket if needed
    if (!micWs || micWs.readyState !== WebSocket.OPEN) {
        micConnectVoice();
        try {
            await new Promise((resolve, reject) => {
                const check = setInterval(() => {
                    if (micWs && micWs.readyState === WebSocket.OPEN) {
                        clearInterval(check); resolve();
                    }
                }, 100);
                setTimeout(() => { clearInterval(check); reject(new Error('timeout')); }, 5000);
            });
        } catch (err) {
            await voiceFailToIdle('Voice: connection failed');
            return;
        }
    }

    // start_recording clears the server buffer so a new listen does not keep the last clip.
    if (micWs && micWs.readyState === WebSocket.OPEN) {
        micWs.send(JSON.stringify({ type: 'start_recording', mode: 'full' }));
    }

    try {
        await voiceMic.open();
    } catch (err) {
        const msg = (err && err.message) ? err.message : 'Voice: mic access failed — check browser permissions';
        if (msg.indexOf('Voice needs a secure') === 0) await voiceFailToIdle(msg);
        else await voiceFailToIdle('Voice: mic access failed — check browser permissions');
    }
}

function _setupScriptProcessorContinuous(src, handler) {
    micProcessor = micAudioCtx.createScriptProcessor(4096, 1, 1);
    micProcessor.onaudioprocess = (e) => {
        const f32 = e.inputBuffer.getChannelData(0);
        const rms = Math.sqrt(f32.reduce((sum, v) => sum + v * v, 0) / f32.length);
        // Manual resample if native rate != 16000
        const nativeRate = micAudioCtx.sampleRate;
        let samples = f32;
        if (nativeRate !== 16000) {
            const ratio = nativeRate / 16000;
            const newLen = Math.floor(f32.length / ratio);
            samples = new Float32Array(newLen);
            for (let i = 0; i < newLen; i++) {
                const srcIdx = i * ratio;
                const idx = Math.floor(srcIdx);
                const frac = srcIdx - idx;
                const next = Math.min(idx + 1, f32.length - 1);
                samples[i] = f32[idx] * (1 - frac) + f32[next] * frac;
            }
        }
        const i16 = new Int16Array(samples.length);
        for (let i = 0; i < samples.length; i++) {
            const s = Math.max(-1, Math.min(1, samples[i]));
            i16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }
        handler(i16.buffer, rms);
    };
    src.connect(micProcessor);
    micProcessor.connect(micAudioCtx.destination);
}

function micConnectVoice() {
    // listening only: voiceActive && !voiceFlushInFlight  (derived as voicePhase === 'listening')

    const voiceBase = MC.voiceUrl('ws');
    if (!voiceBase) { console.warn('[voice] no voice backend configured'); return; }
    micWs = new WebSocket(`${voiceBase}/ws`);
    micWs.binaryType = 'arraybuffer';

    micWs.onmessage = (e) => {
        if (typeof e.data !== 'string') return;
        try {
            const msg = JSON.parse(e.data);
            if (msg.type === 'transcript' && msg.text) {
                voiceChunkPending = false;
                if (voiceDropIncomingTranscript() && !_voiceFlushWaiter) {
                    return;
                }
                if (_voiceFlushWaiter) {
                    if (isHallucinatedTranscript(msg.text)) _voiceFlushWaiter.onDone();
                    else _voiceFlushWaiter.onTranscript(msg.text);
                } else if (!voiceActive) {
                    absorbVoiceTail(msg.text);
                } else {
                    handleVoiceTranscript(msg.text);
                }
            }
            if (msg.type === 'silence' || msg.type === 'done') {
                voiceChunkPending = false;
                if (_voiceFlushWaiter) {
                    _voiceFlushWaiter.onDone();
                } else if (voicePhase === 'listening') {
                    speechSeen = false;
                    silenceStart = 0;
                    updateMicState('listening');
                }
            }
            if (msg.type === 'error') {
                addSystemMessage('Voice: ' + (msg.message || 'error'));
            }
        } catch (ex) {}
    };

    micWs.onclose = () => { micWs = null; };
    micWs.onerror = () => { micWs = null; };
}

function isHallucinatedTranscript(text) {
    if (!text || !String(text).trim()) return true;
    const words = String(text).toLowerCase().match(/[a-z']+/g) || [];
    if (!words.length) return true;
    const n = words.length;
    const unique = new Set(words);
    if (n >= 8 && unique.size <= 3 && (unique.size / n) <= 0.25) return true;
    const maxPeriod = Math.min(4, Math.floor(n / 4));
    for (let period = 1; period <= maxPeriod; period++) {
        const unit = words.slice(0, period);
        const repeats = Math.floor(n / period);
        if (repeats < 4) continue;
        let ok = true;
        for (let r = 0; r < repeats * period; r++) {
            if (words[r] !== unit[r % period]) { ok = false; break; }
        }
        if (ok) return true;
    }
    const counts = {};
    let max = 0;
    for (const w of words) {
        counts[w] = (counts[w] || 0) + 1;
        if (counts[w] > max) max = counts[w];
    }
    if (max >= 8 && (max / n) >= 0.6) return true;
    let run = 1, maxRun = 1;
    for (let i = 1; i < n; i++) {
        run = words[i] === words[i - 1] ? run + 1 : 1;
        if (run > maxRun) maxRun = run;
    }
    return maxRun >= 6;
}

function voiceHeardWords(text) {
    return String(text || '').toLowerCase().match(/[a-z']+/g) || [];
}

function stripVoiceEndTrigger(text) {
    // Hands-free send: "Over" / "send it" only at the END of what was heard.
    // Mid-sentence must not fire — "come over here", "it's over there".
    // Tolerate trailing punctuation and one filler after the trigger
    // (um, uh, er, ah, please, yeah, okay, ok). Misses use visible Send.
    const words = voiceHeardWords(text);
    if (!words.length) return null;
    function matchAt(endIdx) {
        if (endIdx < 0) return null;
        const w = words[endIdx];
        if (w === 'over' || w === 'sendit') return { start: endIdx };
        if (w === 'it' && endIdx > 0 && words[endIdx - 1] === 'send') {
            return { start: endIdx - 1 };
        }
        return null;
    }
    let m = matchAt(words.length - 1);
    if (!m && words.length >= 2 && VOICE_END_FILLERS.indexOf(words[words.length - 1]) !== -1) {
        m = matchAt(words.length - 2);
    }
    if (!m) return null;
    const tail = words.slice(m.start);
    const tailRe = new RegExp(
        tail.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('[\\s.,!?;:]+') + '[\\s.,!?;:]*$',
        'i'
    );
    return String(text).replace(tailRe, '').replace(/[\s.,!?]+$/g, '').trim();
}

function handleVoiceTranscript(text) {
    if (voiceDropIncomingTranscript()) return;
    if (isHallucinatedTranscript(text)) {
        updateMicState('listening');
        return;
    }
    const combined = (voicePartial + ' ' + text).trim();
    const stripped = stripVoiceEndTrigger(combined);

    if (stripped !== null) {
        voicePartial = '';
        updatePartialDisplay('');
        if (stripped) {
            const input = document.getElementById('chat-input');
            if (input) input.value = stripped;
            micPause();
            updateMicState('thinking');
            sendMessage();
        } else {
            updateMicState('listening');
        }
    } else {
        // No end trigger — accumulate in the message box only (no extra preview)
        voicePartial = combined;
        syncVoiceBox(voicePartial);
        updateMicState('listening');
    }
}

let micRecording = false;
let micProcessing = false;
let micStream = null;
let micAudioCtx = null;
let micProcessor = null;
let micWs = null;

function getMicBtn() { return document.getElementById('chat-mic'); }

function micStop() {
    micProcessing = false;
    voiceMic.close();
}

let _micToggleLock = false;
function micToggle() {
    if (_micToggleLock || micProcessing) return;
    _micToggleLock = true;
    setTimeout(() => { _micToggleLock = false; }, 300);

    if (chatMode !== 'voice') return;
    const btn = getMicBtn();
    const speaking = ttsPlaying || (btn && btn.classList.contains('speaking'));
    if (voiceTransition && voicePhase !== 'speaking' && voicePhase !== 'listen-playback' && voicePhase !== 'listening') {
        voiceLogPush('ignored tap during transition');
        return;
    }
    if (speaking) {
        ttsStopPlayback();
        return;
    }
    if (voicePhase !== 'idle') { voiceStop(); } else { voiceStart(); }
}

function toggleListenReply(btn, text) {
    if (!btn) return;
    if (voicePhase === 'thinking' || voicePhase === 'speaking') {
        voiceLogPush('ignored Listen during ' + voicePhase);
        return;
    }
    if (voiceTransition && voicePhase !== 'listen-playback' && voicePhase !== 'listening') {
        voiceLogPush('ignored tap during transition');
        return;
    }
    ensureTTSAudioCtx();
    const already = btn.classList.contains('playing') && (voicePhase === 'listen-playback');
    document.querySelectorAll('.msg-listen.playing').forEach((el) => {
        el.classList.remove('playing');
        el.setAttribute('aria-pressed', 'false');
    });
    if (already) {
        ttsStopPlayback();
        return;
    }
    const fromListening = voicePhase === 'listening';
    if (fromListening) voiceListenResume = true;
    const myListen = ++voiceListenGen;
    (async () => {
        if (fromListening) {
            voiceTransition = true;
            try {
                if (speechSeen || silenceStart || voiceChunkPending) {
                    setVoicePhase('flushing', 'listen');
                    const tail = await voiceFlushOpenChunk();
                    absorbVoiceTail(tail);
                }
                await voiceMic.pause();
            } finally {
                voiceTransition = false;
            }
        }
        if (myListen !== voiceListenGen) return;
        await voicePreparePlayback();
        if (fromListening || voiceListenResume) setVoicePhase('listen-playback', 'listen');
        btn.classList.add('playing');
        btn.setAttribute('aria-pressed', 'true');
        await playTTS(text, { force: true });
        if (myListen !== voiceListenGen) return;
        btn.classList.remove('playing');
        btn.setAttribute('aria-pressed', 'false');
        if (voiceListenResume && chatMode === 'voice' && voicePhase !== 'idle') {
            await setVoiceAudioSession('play-and-record');
            await voiceWait(VOICE_SESSION_SETTLE_MS);
            if (myListen !== voiceListenGen) return;
            voiceResumeListening();
        }
    })();
}

if (document.getElementById('mode-type') || document.getElementById('mode-voice')) {
    restoreChatMode();
}
