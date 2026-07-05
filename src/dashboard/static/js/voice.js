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
    } else {
        // Dictate or Voice — big mic button, hide textarea + send
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
// Sentences are sent to /api/tts in parallel. Audio buffers play sequentially
// so the user hears sentence 1 while sentences 2-N are still synthesizing.

let ttsQueue = [];       // array of Promises that resolve to AudioBuffer
let ttsPlaying = false;  // true while audio chain is playing
let ttsCancelled = false;
let ttsAbort = null;     // AbortController — cancels pending TTS fetches on stop

function splitSentences(text) {
    // Split on sentence-ending punctuation followed by space or end
    // Keep short fragments together (under 20 chars) with the previous sentence
    const raw = text.match(/[^.!?\n]+[.!?\n]+[\s]?|[^.!?\n]+$/g) || [text];
    const merged = [];
    for (const chunk of raw) {
        const trimmed = chunk.trim();
        if (!trimmed) continue;
        if (merged.length > 0 && trimmed.length < 20) {
            merged[merged.length - 1] += ' ' + trimmed;
        } else {
            merged.push(trimmed);
        }
    }
    return merged.length > 0 ? merged : [text];
}

function fetchTTSChunk(text) {
    const voiceBase = MC.voiceUrl('http');
    if (!voiceBase) return Promise.resolve(null);  // voice disabled (compute.voice off/unresolved)
    const ttsUrl = `${voiceBase}/api/tts`;
    // Use active chat agent (Stuart/Mercer tab) not hostname, so TTS voice matches who's talking
    const rawAgentName = (typeof activeAgent !== 'undefined' && activeAgent?.id) || location.hostname.split('.')[0] || '';
    // Normalize: strip -cove/_cove suffixes for consistent key lookup
    const agentName = rawAgentName.toLowerCase().replace(/-cove$/, '').replace(/_cove$/, '').trim();

    // Check for a stored voice preference from Settings
    // Try normalized key first, then raw key
    const voiceOverride = MC.features?.[`voice_${agentName}`]
        || MC.features?.[`voice_${rawAgentName}`]
        || '';

    const signal = ttsAbort ? ttsAbort.signal : undefined;
    return fetch(ttsUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: text, agent: agentName, voice: voiceOverride }),
        signal,
    }).then(async (res) => {
        if (!res.ok) return null;
        const arrayBuffer = await res.arrayBuffer();
        const ctx = ensureTTSAudioCtx();
        return await ctx.decodeAudioData(arrayBuffer);
    }).catch(() => null);
}

function playTTS(text) {
    return new Promise(async (resolve) => {
        if (!text || chatMode !== 'voice') { resolve(); return; }

        ttsResolve = resolve;
        ttsCancelled = false;
        ttsPlaying = true;
        ttsAbort = new AbortController();

        const voiceBtn = document.getElementById('mode-voice');
        if (voiceBtn) voiceBtn.classList.add('playing');

        const sentences = splitSentences(text);

        // Fire ALL TTS requests immediately (parallel fetch)
        ttsQueue = sentences.map(s => fetchTTSChunk(s));

        // Play sequentially as each resolves in order
        for (let i = 0; i < ttsQueue.length; i++) {
            if (ttsCancelled) break;

            const audioBuffer = await ttsQueue[i];
            if (!audioBuffer || ttsCancelled) continue;

            // Play this chunk and wait for it to finish
            await new Promise((chunkDone) => {
                const ctx = ensureTTSAudioCtx();
                ttsSource = ctx.createBufferSource();
                ttsSource.buffer = audioBuffer;
                ttsSource.connect(ctx.destination);
                ttsSource.onended = () => { ttsSource = null; chunkDone(); };
                ttsSource.start(0);
            });
        }

        ttsQueue = [];
        ttsPlaying = false;
        ttsStopPlayback();
    });
}

// ── Voice conversation lifecycle ─────────────────────────────────────────────

function voiceStart() {
    if (voiceActive || sending) return;

    ensureTTSAudioCtx();   // unlock audio on user gesture
    voiceActive = true;
    voicePartial = '';
    speechSeen = false;
    silenceStart = 0;

    updateMicState('listening');
    updatePartialDisplay('');
    micStartContinuous();
}

function voiceStop() {
    voiceActive = false;
    voicePartial = '';
    speechSeen = false;
    silenceStart = 0;
    ttsStopPlayback();

    // Full mic cleanup
    micRecording = false;
    if (micProcessor) { micProcessor.disconnect(); micProcessor = null; }
    if (micAudioCtx) { micAudioCtx.close(); micAudioCtx = null; }
    if (micStream) { micStream.getTracks().forEach(t => t.stop()); micStream = null; }
    if (micWs) { micWs.close(); micWs = null; }
    micProcessing = false;

    updateMicState('idle');
    updatePartialDisplay('');
}

function micPause() {
    // Stop mic input but keep state for auto-resume
    micRecording = false;
    if (micProcessor) { micProcessor.disconnect(); micProcessor = null; }
    if (micAudioCtx) { micAudioCtx.close(); micAudioCtx = null; }
    if (micStream) { micStream.getTracks().forEach(t => t.stop()); micStream = null; }
}

function updateMicState(state) {
    const btn = getMicBtn();
    const label = document.getElementById('mic-label');
    if (!btn || !label) return;

    btn.classList.remove('recording', 'processing', 'speaking');
    switch (state) {
        case 'idle':
            label.textContent = 'Tap to Talk';
            break;
        case 'listening':
            btn.classList.add('recording');
            label.textContent = "Listening... (say 'Over')";
            break;
        case 'transcribing':
            btn.classList.add('processing');
            label.textContent = 'Transcribing...';
            break;
        case 'thinking':
            btn.classList.add('processing');
            label.textContent = _getActiveAIName() + ' thinking...';
            break;
        case 'speaking':
            btn.classList.add('speaking');
            label.textContent = _getActiveAIName() + ' speaking...';
            break;
    }
}

function updatePartialDisplay(text) {
    const el = document.getElementById('voice-partial');
    if (!el) return;
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
                handleVoiceTranscript(msg.text);
            }
            if (msg.type === 'silence' || msg.type === 'done') {
                // No speech detected or processing complete — resume listening
                if (voiceActive) {
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

function handleVoiceTranscript(text) {
    const lower = text.toLowerCase().replace(/[.,!?;:]/g, '').trim();

    // Check for trigger phrase at end of transcript
    let triggered = false;
    let cleanText = text;
    for (const trigger of TRIGGER_PHRASES) {
        if (lower.endsWith(trigger) || lower === trigger) {
            // Remove trigger phrase from end
            const idx = lower.lastIndexOf(trigger);
            cleanText = text.substring(0, idx).replace(/[.,!?\s]+$/, '').trim();
            triggered = true;
            break;
        }
    }

    if (triggered) {
        // Combine partial + this chunk, send to agent
        const fullText = (voicePartial + ' ' + cleanText).trim();
        voicePartial = '';
        updatePartialDisplay('');

        if (fullText) {
            const input = document.getElementById('chat-input');
            if (input) input.value = fullText;
            micPause();
            updateMicState('thinking');
            sendMessage();
        } else {
            updateMicState('listening');
        }
    } else {
        // No trigger — accumulate and keep listening
        voicePartial = (voicePartial + ' ' + text).trim();
        updatePartialDisplay(voicePartial);
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
                const input = document.getElementById('chat-input');
                if (input) {
                    input.value = msg.text;
                    input.style.height = 'auto';
                    input.style.height = Math.min(input.scrollHeight, 150) + 'px';
                }
                sendMessage();
            }
            if (msg.type === 'done' || msg.type === 'silence' || msg.type === 'error') {
                // Only reset UI during processing phase (after user tapped stop).
                // Server sends silence/done during recording — ignore those.
                if (micProcessing) micStopProcessing();
            }
        } catch (ex) {}
    };

    micWs.onclose = () => { micWs = null; };
    micWs.onerror = (e) => { console.error('[mic] WS error'); micWs = null; micStopProcessing(); };
}

async function micStart() {
    const btn = getMicBtn();
    if (!btn || micProcessing || sending) return;

    // Connect WebSocket if not connected
    // Always create a fresh WebSocket per recording session.
    // Server resets audio buffer after end_audio — reusing a connection drops audio.
    if (micWs) { try { micWs.close(); } catch(e) {} micWs = null; }
    micConnect();
    {
        // Wait for connection
        await new Promise((resolve, reject) => {
            const check = setInterval(() => {
                if (micWs && micWs.readyState === WebSocket.OPEN) {
                    clearInterval(check); resolve();
                }
            }, 100);
            setTimeout(() => { clearInterval(check); reject(new Error('Voice server timeout')); }, 5000);
        }).catch(err => {
            console.error('[mic] WS connect FAILED:', err);
            return;
        });
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
