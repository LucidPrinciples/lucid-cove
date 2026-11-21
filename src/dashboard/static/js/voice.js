// =============================================================================
// Voice — Dictation, voice conversation, TTS
// =============================================================================
// Three input modes: Type (default, no voice), Dictate (mic→text→send),
// Voice (continuous mic→text→send→TTS→auto-resume).
// Depends on chat.js being loaded first (uses sendMessage, addSystemMessage, etc.)
// =============================================================================

// =============================================================================
// Chat modes — Type / Dictate / Voice
// =============================================================================
// Type:    text input → text response (default)
// Dictate: mic input → text fills input → text response
// Voice:   mic input → text fills input → text response + TTS audio playback
// =============================================================================
let chatMode = 'type';     // 'type' | 'dictate' | 'voice'
let ttsAudio = null;       // currently playing Audio element

// The mic (getUserMedia) only exists in a SECURE CONTEXT (HTTPS or localhost). On a
// plain-http self-host (http://box:8204) navigator.mediaDevices is undefined → a
// cryptic crash. Return a clear, actionable message instead (#209e). Empty = OK.
function micSecureContextIssue() {
    if (window.isSecureContext && navigator.mediaDevices && navigator.mediaDevices.getUserMedia) return '';
    return 'Voice needs a secure connection. Set your Cove’s address in the setup checklist to turn on HTTPS, or open this Cove over https:// (or localhost). Then the mic will work.';
}

function switchChatMode(mode) {
    if (mode === chatMode) return;

    // Stop any active recording, voice conversation, or TTS
    if (voiceActive) voiceStop();
    else if (micRecording) micStop();
    ttsStopPlayback();

    chatMode = mode;

    // Update mode bar buttons
    document.querySelectorAll('.chat-mode-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.mode === mode);
    });

    const composer = document.querySelector('.chat-input-area');
    if (composer) composer.classList.toggle('chat-voice-composer', mode === 'voice');

    // Toggle between textarea (Type) and big mic button (Dictate/Voice)
    const micBtn = getMicBtn();
    const sendBtn = document.getElementById('chat-send');
    const input = document.getElementById('chat-input');
    const micLabel = document.getElementById('mic-label');

    if (mode === 'type') {
        if (input) input.style.display = '';
        if (micBtn) micBtn.style.display = 'none';
        if (sendBtn) sendBtn.style.display = '';
        if (input) input.placeholder = `Ask ${_getActiveAIName()} anything...`;
    } else if (mode === 'voice') {
        // Hands-free: keep the message box and Send so send does not depend on
        // hearing "Over". Dictate (below) still hides both.
        if (input) input.style.display = '';
        if (micBtn) micBtn.style.display = '';
        if (sendBtn) sendBtn.style.display = '';
        if (micLabel) micLabel.textContent = 'Tap to Talk';
    } else {
        // Dictate — big mic button, hide textarea + send
        if (input) input.style.display = 'none';
        if (micBtn) micBtn.style.display = '';
        if (sendBtn) sendBtn.style.display = 'none';
        if (micLabel) micLabel.textContent = 'Tap to Talk';
    }
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
    if (!ttsAudioCtx) {
        ttsAudioCtx = new (window.AudioContext || window.webkitAudioContext)();
    }
    if (ttsAudioCtx.state === 'suspended') {
        ttsAudioCtx.resume();
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

function ttsStopPlayback() {
    ttsCancelled = true;
    // Abort any in-flight TTS fetch requests immediately
    if (ttsAbort) { ttsAbort.abort(); ttsAbort = null; }
    if (ttsSource) {
        try { ttsSource.stop(); } catch (e) {}
        ttsSource = null;
    }
    ttsQueue = [];
    ttsPlaying = false;
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
const TTS_PLAY_TIMEOUT_MS = 60000;    // safety cap for a single buffer's playback
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
        // slice(): some browsers detach the buffer during decode under load
        return await ctx.decodeAudioData(arrayBuffer.slice(0));
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
        else ttsLastError = ttsLastError || 'unreachable';
        return null;
    }).finally(() => {
        clearTimeout(timer);
        if (ttsAbort) ttsAbort.signal.removeEventListener('abort', onParentAbort);
    });
}

function playTTS(text) {
    return new Promise(async (resolve) => {
        // Only auto-speak in Voice conversation mode. Dictate / Type leave text on screen.
        if (!text || chatMode !== 'voice') { resolve(); return; }

        ttsResolve = resolve;
        ttsCancelled = false;
        ttsPlaying = true;
        ttsLastError = '';
        ttsAbort = new AbortController();

        const voiceBtn = document.getElementById('mode-voice');
        if (voiceBtn) voiceBtn.classList.add('playing');

        const sentences = splitSentences(text);
        let playedAny = false;
        let skippedMiddle = 0;

        // Bounded pipeline: start only the first chunk + TTS_PREFETCH ahead.
        // Kick further chunks as play advances so Piper is never stampeded.
        ttsQueue = new Array(sentences.length);
        function ensureFetch(idx) {
            if (idx < 0 || idx >= sentences.length) return;
            if (ttsCancelled) return;
            if (!ttsQueue[idx]) {
                ttsQueue[idx] = fetchTTSChunk(sentences[idx]);
            }
        }
        for (let j = 0; j <= TTS_PREFETCH && j < sentences.length; j++) {
            ensureFetch(j);
        }

        // Play sequentially as each resolves in order
        for (let i = 0; i < sentences.length; i++) {
            if (ttsCancelled) break;

            // Prefetch upcoming sentence(s) while this one is awaited/played
            ensureFetch(i);
            ensureFetch(i + 1);
            for (let k = 2; k <= TTS_PREFETCH + 1; k++) ensureFetch(i + k);

            const audioBuffer = await ttsQueue[i];
            if (!audioBuffer || ttsCancelled) {
                if (!audioBuffer && !ttsCancelled) skippedMiddle += 1;
                continue;
            }

            playedAny = true;
            // Play this chunk; hard-cap playback so a stuck AudioContext can't pin UI
            await new Promise((chunkDone) => {
                let finished = false;
                const done = () => {
                    if (finished) return;
                    finished = true;
                    clearTimeout(playTimer);
                    ttsSource = null;
                    chunkDone();
                };
                const playTimer = setTimeout(done, TTS_PLAY_TIMEOUT_MS);
                try {
                    const ctx = ensureTTSAudioCtx();
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

        ttsQueue = [];
        ttsPlaying = false;
        const err = ttsLastError;
        const wasCancelled = ttsCancelled;
        // Capture before stop — ttsStopPlayback sets ttsCancelled and resolves waiters.
        ttsStopPlayback();

        // Fail open: text already on screen. One quiet system line if nothing played
        // or if middle chunks dropped (partial speak is still useful — note once).
        if (!wasCancelled && typeof addSystemMessage === 'function') {
            if (!playedAny && err) {
                if (err === 'timeout') {
                    addSystemMessage('Voice timed out — reply is on screen.');
                } else if (err === 'unreachable') {
                    addSystemMessage('Voice unavailable — reply is on screen.');
                } else {
                    addSystemMessage('Could not speak that reply — text is on screen.');
                }
            } else if (playedAny && skippedMiddle > 0) {
                addSystemMessage('Some of that reply could not be spoken — full text is on screen.');
            }
        }
        resolve();
    });
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
    micRecording = false;
    if (micProcessor) { micProcessor.disconnect(); micProcessor = null; }
    if (micAudioCtx) { try { micAudioCtx.close(); } catch (e) {} micAudioCtx = null; }
    if (micStream) { micStream.getTracks().forEach(t => t.stop()); micStream = null; }
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

function absorbVoiceTail(text) {
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

async function voiceInterruptSession() {
    // Phone call / lost mic / backgrounded PWA: end the session and wait for a
    // tap. Do not auto-resume — iOS will not hand the mic back without a gesture.
    if (!voiceActive || voiceIgnoringMicLoss || voiceFlushInFlight) return;
    await voiceStop();
    if (typeof addSystemMessage !== 'function') return;
    if (voiceBoxHasWords()) {
        addSystemMessage('Voice paused — send or edit what’s in the box, then tap to talk.');
    } else {
        addSystemMessage('Voice paused — nothing was captured. Tap to talk.');
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
        if (document.hidden) voiceInterruptSession();
    };
    document.addEventListener('visibilitychange', _voiceVisHandler);
    _voicePageHideHandler = () => { voiceInterruptSession(); };
    window.addEventListener('pagehide', _voicePageHideHandler);
    const tracks = (stream && stream.getAudioTracks) ? stream.getAudioTracks() : [];
    tracks.forEach((track) => {
        const fn = () => { voiceInterruptSession(); };
        track.addEventListener('ended', fn);
        _voiceTrackEnded.push({ track, fn });
    });
    const ctx = micAudioCtx;
    if (ctx && typeof ctx.addEventListener === 'function') {
        _voiceCtxHandler = () => {
            if (ctx.state === 'interrupted' || ctx.state === 'suspended') {
                voiceInterruptSession();
            }
        };
        ctx.addEventListener('statechange', _voiceCtxHandler);
        _voiceBoundCtx = ctx;
    }
}

function voiceStart() {
    if (voiceActive || sending || voiceFlushInFlight) return;

    ensureTTSAudioCtx();   // unlock audio on user gesture
    voiceActive = true;
    voicePartial = '';
    speechSeen = false;
    silenceStart = 0;
    voiceChunkPending = false;

    updateMicState('listening');
    updatePartialDisplay('');
    bindVoiceInterruptWatchers(null);
    micStartContinuous();
}

async function voiceStop() {
    if (voiceFlushInFlight) return;
    voiceIgnoringMicLoss = true;
    voiceFlushInFlight = true;
    unbindVoiceInterruptWatchers();
    ttsStopPlayback();
    voiceStopCaptureKeepSocket();
    const tail = await voiceFlushOpenChunk();
    absorbVoiceTail(tail);
    parkVoiceDraft();
    voiceActive = false;
    speechSeen = false;
    silenceStart = 0;
    voiceChunkPending = false;
    if (micWs) { try { micWs.close(); } catch (e) {} micWs = null; }
    micProcessing = false;
    updateMicState('idle');
    voiceFlushInFlight = false;
    voiceIgnoringMicLoss = false;
}

function micPause() {
    // Stop mic input but keep state for auto-resume after a sent turn.
    // Unbind first so track-ended / AudioContext close is not treated as a call.
    voiceIgnoringMicLoss = true;
    unbindVoiceInterruptWatchers();
    voiceStopCaptureKeepSocket();
    voiceIgnoringMicLoss = false;
}

async function voiceBeforeSend() {
    // Send-by-tap while listening: flush the open chunk into the box first.
    if (typeof chatMode === 'undefined' || chatMode !== 'voice' || !voiceActive) return;
    if (voiceFlushInFlight) return;
    if (!speechSeen && !silenceStart && !voiceChunkPending) return;
    voiceIgnoringMicLoss = true;
    voiceFlushInFlight = true;
    unbindVoiceInterruptWatchers();
    voiceStopCaptureKeepSocket();
    const tail = await voiceFlushOpenChunk();
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
    voiceFlushInFlight = false;
    voiceIgnoringMicLoss = false;
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
            break;
        case 'listening':
            btn.classList.add('recording');
            label.textContent = "Listening... (say 'Over')";
            btn.title = 'Listening — say Over to send, or tap to end';
            break;
        case 'transcribing':
            btn.classList.add('processing');
            label.textContent = 'Transcribing...';
            btn.title = 'Transcribing';
            break;
        case 'thinking':
            btn.classList.add('processing');
            label.textContent = _getActiveAIName() + ' thinking...';
            btn.title = 'Thinking';
            break;
        case 'speaking':
            btn.classList.add('speaking');
            label.textContent = _getActiveAIName() + ' speaking...';
            btn.title = 'Speaking — tap to stop';
            break;
        case 'unavailable':
            btn.classList.add('processing');
            label.textContent = 'Voice unavailable';
            btn.title = 'Voice unavailable';
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
    if (!voiceActive) return;

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
            addSystemMessage('Voice: connection failed');
            voiceStop();
            return;
        }
    }

    const _secIssue = micSecureContextIssue();
    if (_secIssue) { addSystemMessage(_secIssue); return; }

    micRecording = true;
    speechSeen = false;
    silenceStart = 0;

    try {
        micStream = await navigator.mediaDevices.getUserMedia({
            audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true }
        });
        // Request 16kHz — desktop Chrome honors it (no resampling needed).
        // Mobile may ignore it — AudioWorklet resamples as fallback.
        micAudioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
        bindVoiceInterruptWatchers(micStream);
        const src = micAudioCtx.createMediaStreamSource(micStream);

        // ScriptProcessor with silence detection for voice mode
        micProcessor = micAudioCtx.createScriptProcessor(4096, 1, 1);
        micProcessor.onaudioprocess = (e) => {
            if (!micRecording || !micWs || micWs.readyState !== WebSocket.OPEN) return;
            const f32 = e.inputBuffer.getChannelData(0);

            // Send audio to server
            const i16 = new Int16Array(f32.length);
            for (let i = 0; i < f32.length; i++) {
                let s = Math.max(-1, Math.min(1, f32[i]));
                i16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
            }
            micWs.send(i16.buffer);

            // Silence detection — auto-chunk when speech pauses
            if (voiceActive) {
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
            }
        };
        src.connect(micProcessor);
        micProcessor.connect(micAudioCtx.destination);
    } catch (err) {
        addSystemMessage('Voice: mic access failed — check browser permissions');
        voiceStop();
    }
}

// ScriptProcessor fallback for browsers without AudioWorklet (voice mode)
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

// ScriptProcessor fallback for browsers without AudioWorklet (dictate mode)
function _setupScriptProcessorDictate(src) {
    micProcessor = micAudioCtx.createScriptProcessor(4096, 1, 1);
    micProcessor.onaudioprocess = (e) => {
        if (!micRecording || !micWs || micWs.readyState !== WebSocket.OPEN) return;
        const f32 = e.inputBuffer.getChannelData(0);
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
        micWs.send(i16.buffer);
    };
    src.connect(micProcessor);
    micProcessor.connect(micAudioCtx.destination);
}

function micConnectVoice() {
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
                } else if (voiceActive && !voiceFlushInFlight) {
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

// =============================================================================
// Dictation mic — tap-to-record, transcribe, auto-submit
// =============================================================================
let micRecording = false;
let micProcessing = false;
let micStream = null;
let micAudioCtx = null;
let micProcessor = null;
let micWs = null;

function getMicBtn() { return document.getElementById('chat-mic'); }

function micConnect() {
    // Connect to pipecat-voice WebSocket for STT (host resolved from compute.voice).
    const voiceBase = MC.voiceUrl('ws');
    if (!voiceBase) { console.warn('[voice] no voice backend configured'); return; }
    micWs = new WebSocket(`${voiceBase}/ws`);
    micWs.binaryType = 'arraybuffer';

    micWs.onmessage = (e) => {
        if (typeof e.data !== 'string') return;
        try {
            const msg = JSON.parse(e.data);
            if (msg.type === 'transcript' && msg.text) {
                // Only process transcript AFTER user tapped stop (micProcessing=true).
                // Ignores server-side partials during recording that caused premature sends.
                if (!micProcessing) return;
                if (isHallucinatedTranscript(msg.text)) {
                    addSystemMessage("Didn't catch that — tap to talk again.");
                    micStopProcessing();
                    return;
                }
                const input = document.getElementById('chat-input');
                if (input) {
                    input.value = msg.text;
                    input.style.height = 'auto';
                    input.style.height = Math.min(input.scrollHeight, 150) + 'px';
                }
                sendMessage();
            }
            if (msg.type === 'silence') {
                if (micProcessing) {
                    addSystemMessage("Didn't catch that — tap to talk again.");
                    micStopProcessing();
                }
            }
            if (msg.type === 'done' || msg.type === 'error') {
                // Only reset UI during processing phase (after user tapped stop).
                // Server sends silence/done during recording — ignore those.
                if (micProcessing) {
                    if (msg.type === 'error') {
                        addSystemMessage('Voice: ' + (msg.message || 'error'));
                    }
                    micStopProcessing();
                }
            }
        } catch (ex) {}
    };

    micWs.onclose = () => { micWs = null; };
    micWs.onerror = (e) => { console.error('[mic] WS error'); micWs = null; micStopProcessing(); };
}

async function micStart() {
    const btn = getMicBtn();
    if (!btn || micProcessing || sending) return;

    // Reuse an open socket. start_recording clears the server buffer so a
    // second tap does not need a new WebSocket (that was doubling /ws).
    if (!micWs || micWs.readyState !== WebSocket.OPEN) {
        if (micWs) { try { micWs.close(); } catch(e) {} micWs = null; }
        micConnect();
        try {
            await new Promise((resolve, reject) => {
                const check = setInterval(() => {
                    if (micWs && micWs.readyState === WebSocket.OPEN) {
                        clearInterval(check); resolve();
                    }
                }, 100);
                setTimeout(() => { clearInterval(check); reject(new Error('Voice server timeout')); }, 5000);
            });
        } catch (err) {
            console.error('[mic] WS connect FAILED:', err);
            return;
        }
    }

    // Tell server which mode we're in so it doesn't auto-clear the buffer
    if (micWs && micWs.readyState === WebSocket.OPEN) {
        micWs.send(JSON.stringify({ type: 'start_recording', mode: chatMode === 'voice' ? 'full' : 'transcribe' }));
    }

    // Unlock AudioContext for TTS on this user gesture (Voice mode)
    if (chatMode === 'voice') ensureTTSAudioCtx();

    const _secIssue2 = micSecureContextIssue();
    if (_secIssue2) { addSystemMessage(_secIssue2); return; }

    micRecording = true;
    btn.classList.add('recording');
    btn.title = 'Tap to stop';
    const micLabel = document.getElementById('mic-label');
    if (micLabel) micLabel.textContent = 'Tap to Stop';

    try {
        micStream = await navigator.mediaDevices.getUserMedia({
            audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true }
        });
        micAudioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
        const src = micAudioCtx.createMediaStreamSource(micStream);

        // ScriptProcessor — proven to work with pipecat-voice.
        // AudioWorklet migration deferred (buffer transfer issues with WebSocket).
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
        };
        src.connect(micProcessor);
        micProcessor.connect(micAudioCtx.destination);
    } catch (err) {
        console.error('[mic] getUserMedia FAILED:', err);
        addSystemMessage(micSecureContextIssue() || 'Mic access failed — check browser permissions');
        micStop();
    }
}

function micStop() {
    micRecording = false;
    const btn = getMicBtn();

    // Clean up audio
    if (micProcessor) { micProcessor.disconnect(); micProcessor = null; }
    if (micAudioCtx) { micAudioCtx.close(); micAudioCtx = null; }
    if (micStream) { micStream.getTracks().forEach(t => t.stop()); micStream = null; }

    // Send end_audio with transcribe mode
    const micLabel = document.getElementById('mic-label');
    if (micWs && micWs.readyState === WebSocket.OPEN) {
        micWs.send(JSON.stringify({ type: 'end_audio', mode: 'transcribe' }));
        micProcessing = true;
        if (btn) {
            btn.classList.remove('recording');
            btn.classList.add('processing');
            btn.title = 'Transcribing...';
        }
        if (micLabel) micLabel.textContent = 'Transcribing...';
    } else {
        if (btn) {
            btn.classList.remove('recording');
            btn.title = 'Tap to talk';
        }
        if (micLabel) micLabel.textContent = 'Tap to Talk';
    }
}

function micStopProcessing() {
    micProcessing = false;
    const btn = getMicBtn();
    if (btn) {
        btn.classList.remove('recording', 'processing');
        btn.title = 'Tap to talk';
    }
    const micLabel = document.getElementById('mic-label');
    if (micLabel) micLabel.textContent = 'Tap to Talk';
}

let _micToggleLock = false;
function micToggle() {
    if (_micToggleLock || micProcessing) return;
    _micToggleLock = true;
    setTimeout(() => { _micToggleLock = false; }, 300);

    if (chatMode === 'voice') {
        if (voiceActive) { voiceStop(); } else { voiceStart(); }
    } else {
        if (micRecording) { micStop(); } else { micStart(); }
    }
}
