// Behavior harness for Chat Voice playback and Listen (node, no browser).
// Loads src/dashboard/static/js/voice.js with stubbed browser APIs and drives it.
//   node tests/voice_listen_harness.js [path/to/voice.js]
// Prints one line per check and ends with HARNESS_OK or HARNESS_FAIL.
const fs = require('fs');
const vm = require('vm');
const path = require('path');
const SRC = process.argv[2] || path.join(__dirname, '..', 'src', 'dashboard', 'static', 'js', 'voice.js');

function makeWorld() {
    const log = [];                       // ordered events: 'gum', 'track-stop', 'src-start:<text>', 'src-stop:<text>', 'sys:<text>'
    const els = {};
    function el(id) {
        if (!els[id]) {
            const cls = new Set();
            els[id] = {
                id, value: '', textContent: '', title: '', style: {}, disabled: false, scrollHeight: 20, attrs: {},
                classList: { add: (...c) => c.forEach((x) => cls.add(x)), remove: (...c) => c.forEach((x) => cls.delete(x)),
                             contains: (c) => cls.has(c), toggle: (c, on) => { (on ? cls.add(c) : cls.delete(c)); } },
                setAttribute(k, v) { this.attrs[k] = v; }, removeAttribute(k) { delete this.attrs[k]; },
                addEventListener() {}, removeEventListener() {}, focus() {},
            };
        }
        return els[id];
    }
    const listenBtns = [];
    function listenBtn(name) { const b = el('listen-' + name); listenBtns.push(b); return b; }
    const sockets = [];
    class WS {
        constructor() { this.readyState = 0; this.sent = []; sockets.push(this); setTimeout(() => { this.readyState = 1; }, 5); }
        send(d) { this.sent.push(typeof d === 'string' ? JSON.parse(d) : 'audio'); if (typeof d === 'string') log.push('ws:' + JSON.parse(d).type); }
        close() { this.readyState = 3; if (this.onclose) this.onclose(); }
        reply(obj) { if (this.onmessage) this.onmessage({ data: JSON.stringify(obj) }); }
    }
    WS.OPEN = 1;
    const tracks = [];
    function makeTrack() {
        const t = { readyState: 'live', _l: [], addEventListener(ev, fn) { if (ev === 'ended') this._l.push(fn); }, removeEventListener() {},
            stop() { if (this.readyState === 'ended') return; log.push('track-stop'); setTimeout(() => { this.readyState = 'ended'; log.push('track-ended'); this._l.forEach((f) => f()); }, 30); } };
        tracks.push(t); return t;
    }
    class AC {
        constructor() { this.state = 'running'; this.destination = {}; }
        resume() { this.state = 'running'; return Promise.resolve(); }
        close() { this.state = 'closed'; return Promise.resolve(); }
        addEventListener() {} removeEventListener() {}
        createBuffer() { return {}; }
        createMediaStreamSource() { return { connect() {} }; }
        createScriptProcessor() { return { connect() {}, disconnect() {}, onaudioprocess: null }; }
        decodeAudioData(buf) { return Promise.resolve({ duration: 0.2, _text: Buffer.from(buf).toString() }); }
        createBufferSource() {
            const s = { buffer: null, onended: null, connect() {}, _stopped: false,
                start() { if (!this.buffer || !this.buffer._text) return; const liveTracks = tracks.filter((t) => t.readyState === 'live').length;
                          log.push('src-start:' + this.buffer._text + (liveTracks ? ' MIC_LIVE' : ''));
                          this._t = setTimeout(() => { if (!this._stopped && this.onended) this.onended(); }, this.buffer.duration * 1000); },
                stop() { this._stopped = true; clearTimeout(this._t); if (this.buffer && this.buffer._text) log.push('src-stop:' + this.buffer._text); if (this.onended) this.onended(); } };
            return s;
        }
    }
    // Screen Wake Lock stub: counts live locks; `wake.mode` = 'ok' | 'refuse' | 'slow' | 'missing'.
    const wake = { live: 0, asked: 0, mode: 'ok' };
    wake.api = { request: () => { wake.asked += 1;
        if (wake.mode === 'refuse') return Promise.reject(new Error('NotAllowedError'));
        const lock = { released: false, _l: [], addEventListener(ev, fn) { this._l.push(fn); },
            release() { if (this.released) return Promise.resolve(); this.released = true; wake.live -= 1; this._l.forEach((f) => f()); return Promise.resolve(); } };
        return new Promise((res) => setTimeout(() => { wake.live += 1; res(lock); }, wake.mode === 'slow' ? 150 : 5)); } };
    const ctx = {
        console: { log() {}, warn() {}, error() {} }, setTimeout, clearTimeout, setInterval, clearInterval, Promise, Date, Math, JSON, Int16Array, Array, String, Number, Error, RegExp, Object, Buffer,
        AbortController, URL,
        document: { getElementById: el, querySelector: () => el('composer'), querySelectorAll: (sel) => (sel.indexOf('msg-listen') >= 0 ? listenBtns.filter((b) => b.classList.contains('playing')) : []),
                    addEventListener() {}, removeEventListener() {}, hidden: false, visibilityState: 'visible' },
        navigator: { mediaDevices: { getUserMedia: () => { log.push('gum'); const t = makeTrack(); return Promise.resolve({ getTracks: () => [t], getAudioTracks: () => [t] }); } },
                     wakeLock: wake.api },
        localStorage: { getItem: () => null, setItem() {} },
        WebSocket: WS, MC: { voiceUrl: () => 'ws://voice', features: {} }, location: { hostname: 'atlas.cove' },
        fetch: (url, opts) => new Promise((res, rej) => {
            const text = JSON.parse(opts.body).text;
            const timer = setTimeout(() => res({ ok: true, arrayBuffer: () => Promise.resolve(Buffer.from(text)) }), 40);
            if (opts.signal) opts.signal.addEventListener('abort', () => { clearTimeout(timer); const e = new Error('aborted'); e.name = 'AbortError'; rej(e); });
        }),
        addSystemMessage: (m) => log.push('sys:' + m), _getActiveAIName: () => 'Atlas', sendMessage: () => { log.push('sendMessage'); },
        sending: false, activeAgent: { id: 'atlas' },
    };
    ctx.addEventListener = () => {}; ctx.removeEventListener = () => {};
    ctx.window = ctx; ctx.window.isSecureContext = true; ctx.AudioContext = AC; ctx.webkitAudioContext = AC;
    vm.createContext(ctx);
    vm.runInContext(fs.readFileSync(SRC, 'utf8') + '\n;this.__v = { get voiceActive(){return voiceActive;}, get micRecording(){return micRecording;}, get ttsPlaying(){return ttsPlaying;}, get voicePartial(){return voicePartial;}, setSpeech(){ speechSeen = true; }, setHold(v){ voiceHoldTranscripts = v; } };', ctx);
    return { ctx, log, el, listenBtn, sockets, tracks, wake };
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const results = [];
function check(name, ok, detail) { results.push(ok); console.log((ok ? 'PASS ' : 'FAIL ') + name + (ok ? '' : '  <<' + detail + '>>')); }
const starts = (log) => log.filter((x) => x.startsWith('src-start:')).map((x) => x.slice(10));

(async () => {
    // 1. Two playbacks never overlap; the second takes over; the first stops itself.
    { const w = makeWorld(); w.ctx.switchChatMode('voice');
      const pA = w.ctx.playTTS('Alpha sentence number one is here. Alpha sentence number two is here. Alpha sentence number three is here.', { force: true });
      await sleep(120);                                   // Alpha one is playing
      const pB = w.ctx.playTTS('Bravo sentence number one is here. Bravo sentence number two is here.', { force: true });
      await Promise.all([pA, pB]); await sleep(50);
      const s = starts(w.log); const iB = s.findIndex((x) => x.startsWith('Bravo'));
      const nA = s.filter((x) => x.startsWith('Alpha')).length, nB = s.filter((x) => x.startsWith('Bravo')).length;
      check('second playback takes over, first plays nothing more', iB > 0 && s.slice(iB).every((x) => x.startsWith('Bravo')) && nB >= 1 && nA < w.ctx.splitSentences('Alpha sentence number one is here. Alpha sentence number two is here. Alpha sentence number three is here.').length, s.join(' | '));
      check('all of the second reply is played', nB === w.ctx.splitSentences('Bravo sentence number one is here. Bravo sentence number two is here.').length, s.join(' | '));
      check('playback state is clean after both end', w.ctx.__v.ttsPlaying === false, 'ttsPlaying still true'); }

    // 2. Text mode: second tap on the same Listen stops it and does not restart it.
    { const w = makeWorld(); const b = w.listenBtn('a');
      w.ctx.toggleListenReply(b, 'One sentence that is long enough. Two sentence that is long enough.');
      await sleep(120);
      w.ctx.toggleListenReply(b, 'One sentence that is long enough. Two sentence that is long enough.');
      await sleep(400);
      const s = starts(w.log);
      check('Text: second tap stops the reply (no restart)', s.length === 1 && !b.classList.contains('playing') && w.ctx.__v.ttsPlaying === false, s.join(' | ') + ' playing=' + b.classList.contains('playing')); }

    // 3. Listen while Voice is listening: words kept, mic closed before any audio, mic reopened after.
    { const w = makeWorld(); w.ctx.switchChatMode('voice'); w.ctx.voiceStart(); await sleep(220);
      const ws = w.sockets[0];
      ws.reply({ type: 'transcript', text: 'first part of what I said' });      // an earlier pause-chunk, now in the box
      w.ctx.__v.setSpeech();                                                    // and more speech since then, not yet transcribed
      const b = w.listenBtn('a');
      const p = w.ctx.toggleListenReply(b, 'Reply sentence number one here.');
      await sleep(20); ws.reply({ type: 'transcript', text: 'and the last words' }); ws.reply({ type: 'done' });
      await p; await sleep(80);
      const box = w.el('chat-input').value;
      check('Listen mid-sentence keeps the words spoken before the tap', box === 'first part of what I said and the last words', 'box=' + box);
      const i1 = w.log.indexOf('track-ended'), i2 = w.log.findIndex((x) => x.startsWith('src-start:'));
      check('mic is fully closed before the reply plays', i1 >= 0 && i2 > i1 && !w.log.some((x) => x.indexOf('MIC_LIVE') >= 0), w.log.join(','));
      check('mic is reopened after Listen and the session is still listening', w.log.filter((x) => x === 'gum').length === 2 && w.ctx.__v.micRecording && w.ctx.__v.voiceActive, w.log.join(','));
      check('the words are still in the box after Listen', w.el('chat-input').value === 'first part of what I said and the last words', w.el('chat-input').value); }

    // 4. Listen on A then B before A ends: one at a time, mic reopened exactly once, after B.
    { const w = makeWorld(); w.ctx.switchChatMode('voice'); w.ctx.voiceStart(); await sleep(220);
      const a = w.listenBtn('a'), b = w.listenBtn('b');
      const pA = w.ctx.toggleListenReply(a, 'Alpha sentence number one. Alpha sentence number two. Alpha sentence number three.');
      await sleep(200);
      const pB = w.ctx.toggleListenReply(b, 'Bravo sentence number one.');
      await Promise.all([pA, pB]); await sleep(120);
      const s = starts(w.log); const iB = s.findIndex((x) => x.startsWith('Bravo'));
      check('A then B: B takes over, nothing of A after it', iB > 0 && s.slice(iB).every((x) => x.startsWith('Bravo')), s.join(' | '));
      const gums = w.log.map((x, i) => (x === 'gum' ? i : -1)).filter((i) => i >= 0); const lastStart = w.log.map((x, i) => (x.startsWith('src-start:') ? i : -1)).filter((i) => i >= 0).pop();
      check('A then B: mic reopened once, after B finished', gums.length === 2 && gums[1] > lastStart && w.ctx.__v.micRecording, w.log.join(','));
      check('A then B: neither button left marked playing', !a.classList.contains('playing') && !b.classList.contains('playing'), 'a=' + a.classList.contains('playing') + ' b=' + b.classList.contains('playing')); }

    // 5. Listen is ignored while a Voice turn is being sent, thought about or spoken.
    { const w = makeWorld(); w.ctx.switchChatMode('voice'); w.ctx.voiceStart(); await sleep(220);
      w.ctx.__v.setHold(true);
      const b = w.listenBtn('a'); await w.ctx.toggleListenReply(b, 'Should not play at all.'); await sleep(120);
      check('Listen during a Voice turn does nothing', starts(w.log).length === 0 && !b.classList.contains('playing'), w.log.join(',')); }

    // 5b. Listen tapped while the mic is still opening (right after Tap to Talk) is ignored.
    { const w = makeWorld(); w.ctx.switchChatMode('voice'); w.ctx.voiceStart(); await sleep(20);
      const b = w.listenBtn('a'); await w.ctx.toggleListenReply(b, 'Should not play over an opening mic.'); await sleep(300);
      check('Listen while the mic is opening does nothing', starts(w.log).length === 0 && w.ctx.__v.micRecording, w.log.join(',')); }

    // 6. Stop mid-sentence still keeps the tail (the rule from earlier today must not regress).
    { const w = makeWorld(); w.ctx.switchChatMode('voice'); w.ctx.voiceStart(); await sleep(220);
      const ws = w.sockets[0]; w.ctx.__v.setSpeech();
      const p = w.ctx.voiceStop(); await sleep(20); ws.reply({ type: 'transcript', text: 'words right before stop' }); await p;
      check('Stop mid-sentence parks the last words in the box', w.el('chat-input').value === 'words right before stop' && !w.ctx.__v.voiceActive, 'box=' + w.el('chat-input').value); }

    // 7. Stop during a Listen in a session ends the session and does not reopen the mic.
    { const w = makeWorld(); w.ctx.switchChatMode('voice'); w.ctx.voiceStart(); await sleep(220);
      const b = w.listenBtn('a'); const p = w.ctx.toggleListenReply(b, 'Alpha sentence number one. Alpha sentence number two.');
      await sleep(150); await w.ctx.voiceStop(); await p; await sleep(100);
      check('Stop during Listen: session ends, mic stays closed', !w.ctx.__v.voiceActive && !w.ctx.__v.micRecording && w.log.filter((x) => x === 'gum').length === 1, w.log.join(',')); }

    // 8. Screen wake lock: held for the whole Voice session, let go when it ends.
    { const w = makeWorld(); w.ctx.switchChatMode('voice'); w.ctx.voiceStart(); await sleep(220);
      check('wake lock is held while a Voice session is open', w.wake.live === 1, 'live=' + w.wake.live);
      const b = w.listenBtn('a'); await w.ctx.toggleListenReply(b, 'Reply sentence number one here.'); await sleep(80);
      check('wake lock is still held after a Listen inside the session', w.wake.live === 1 && w.wake.asked === 1, 'live=' + w.wake.live + ' asked=' + w.wake.asked);
      await w.ctx.voiceStop(); await sleep(20);
      check('wake lock is released when the session ends', w.wake.live === 0, 'live=' + w.wake.live); }
    { const w = makeWorld(); w.wake.mode = 'slow'; w.ctx.switchChatMode('voice'); w.ctx.voiceStart(); await sleep(40);
      await w.ctx.voiceStop(); await sleep(250);
      check('a lock granted after the session ended is released at once', w.wake.live === 0, 'live=' + w.wake.live); }
    { const w = makeWorld(); w.wake.mode = 'refuse'; w.ctx.switchChatMode('voice'); w.ctx.voiceStart(); await sleep(220);
      check('a refused wake lock does not stop Voice', w.ctx.__v.voiceActive && w.ctx.__v.micRecording, 'active=' + w.ctx.__v.voiceActive);
      await w.ctx.voiceStop(); }
    { const w = makeWorld(); delete w.ctx.navigator.wakeLock; w.ctx.switchChatMode('voice'); w.ctx.voiceStart(); await sleep(220);
      check('no Wake Lock API: Voice still starts', w.ctx.__v.voiceActive && w.ctx.__v.micRecording, 'active=' + w.ctx.__v.voiceActive);
      await w.ctx.voiceStop(); }
    { const w = makeWorld(); const b = w.listenBtn('a'); await w.ctx.toggleListenReply(b, 'Reply sentence number one here.'); await sleep(60);
      check('Listen in Text mode takes no wake lock', w.wake.asked === 0, 'asked=' + w.wake.asked); }

    const ok = results.every(Boolean);
    console.log((ok ? 'HARNESS_OK ' : 'HARNESS_FAIL ') + results.filter(Boolean).length + '/' + results.length);
    process.exit(ok ? 0 : 1);
})();
