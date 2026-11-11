// Behavioral checks for the car player. Run: node tests/player_car_harness.js
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const panelPath = process.env.PANEL || path.join(__dirname, '../src/dashboard/static/js/tuning-panel.js');

function makeClassList() {
  const set = new Set();
  return {
    add(c) { set.add(c); },
    remove(c) { set.delete(c); },
    contains(c) { return set.has(c); },
    toggle(c, on) { if (on) set.add(c); else set.delete(c); },
  };
}

function makeNode(id) {
  return {
    id: id,
    textContent: '',
    innerHTML: '',
    hidden: true,
    value: '',
    style: {},
    classList: makeClassList(),
    getAttribute() { return null; },
    setAttribute() {},
    addEventListener() {},
    remove() {},
    select() {},
    appendChild(child) { return child; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
  };
}

const audios = [];
let live = null;
let now = 0;
let nextTimer = 1;
const timers = [];
const nodes = {};
const vis = [];
const pageShow = [];
const storage = {};

class FakeAudio {
  constructor() {
    this._src = '';
    this.paused = true;
    this.currentTime = 0;
    this.duration = 180;
    this.readyState = 0;
    this.networkState = 0;
    this.volume = 1;
    this.playbackRate = 1;
    this.playCount = 0;
    this.listeners = {};
    this.parentNode = null;
    this.style = {};
    this._otIgnorePause = false;
    this._otEventsBound = false;
    this._pauseAfterPlay = false;
    this.buffered = { length: 0, end() { return 0; } };
  }
  get src() { return this._src; }
  set src(v) {
    const next = String(v || '');
    const changed = next !== this._src;
    const replacing = changed && !!this._src && !!next;
    if (changed) this.currentTime = 0;
    this._src = next;
    this.readyState = next ? 4 : 0;
    this.networkState = next ? 2 : 0;
    if (replacing) {
      if (!this.paused) this._pauseAfterPlay = true;
      this.dispatch('abort');
      this.dispatch('emptied');
    }
    if (changed && next) {
      this.dispatch('loadstart');
      this.dispatch('loadedmetadata');
      this.dispatch('canplay');
    }
  }
  getAttribute(name) { return name === 'src' ? this._src : null; }
  setAttribute(name, value) { if (name === 'src') this.src = value; }
  removeAttribute(name) {
    if (name !== 'src') return;
    if (this._src && !this.paused) this._pauseAfterPlay = true;
    this._src = '';
  }
  addEventListener(name, fn) {
    (this.listeners[name] = this.listeners[name] || []).push(fn);
  }
  removeEventListener(name, fn) {
    const list = this.listeners[name] || [];
    this.listeners[name] = list.filter((item) => item !== fn);
  }
  dispatch(name) {
    const ev = { target: this, type: name };
    (this.listeners[name] || []).slice().forEach((fn) => fn(ev));
  }
  play() {
    this.playCount += 1;
    live = this;
    this.paused = false;
    if (this._src) this.readyState = 4;
    this.dispatch('play');
    this.dispatch('playing');
    // iPhone delivers the src-swap pause after play and waiting, same turn.
    if (this._pauseAfterPlay) {
      this._pauseAfterPlay = false;
      this.dispatch('pause');
    }
    return Promise.resolve();
  }
  pause() {
    const was = this.paused;
    this.paused = true;
    if (!was) this.dispatch('pause');
  }
  load() { this.readyState = this._src ? 4 : 0; }
}

function setTimeout(fn, ms) {
  const id = nextTimer++;
  timers.push({ id: id, at: now + (ms || 0), fn: fn });
  return id;
}
function clearTimeout(id) {
  const i = timers.findIndex((t) => t.id === id);
  if (i >= 0) timers.splice(i, 1);
}
function flush(ms) {
  now += ms;
  let guard = 0;
  while (guard++ < 300) {
    let idx = -1;
    let best = Infinity;
    for (let i = 0; i < timers.length; i++) {
      if (timers[i].at <= now && timers[i].at < best) {
        best = timers[i].at;
        idx = i;
      }
    }
    if (idx < 0) break;
    const t = timers.splice(idx, 1)[0];
    t.fn();
  }
}

const media = {
  metadata: null,
  playbackState: 'none',
  position: null,
  handlers: {},
  setActionHandler(name, fn) { this.handlers[name] = fn; },
  setPositionState(state) { this.position = state; },
};

const document = {
  hidden: false,
  getElementById(id) {
    if (!nodes[id]) nodes[id] = makeNode(id);
    return nodes[id];
  },
  querySelectorAll() { return []; },
  querySelector() { return null; },
  createElement() { return makeNode('el'); },
  addEventListener(name, fn) {
    if (name === 'visibilitychange') vis.push(fn);
  },
  execCommand() { return false; },
  body: {
    classList: makeClassList(),
    appendChild(el) {
      el.parentNode = document.body;
      if (el && typeof el.play === 'function' && typeof el.dispatch === 'function') audios.push(el);
      return el;
    },
  },
};

const sandbox = {
  console: console,
  Math: Math,
  JSON: JSON,
  Date: Date,
  Promise: Promise,
  Number: Number,
  String: String,
  Array: Array,
  Object: Object,
  URL: URL,
  isFinite: isFinite,
  isNaN: isNaN,
  parseInt: parseInt,
  encodeURIComponent: encodeURIComponent,
  setTimeout: setTimeout,
  clearTimeout: clearTimeout,
  setInterval() { return 0; },
  clearInterval() {},
  Audio: FakeAudio,
  MediaMetadata: function MediaMetadata(data) { return data; },
  location: { href: 'https://app.lucidtuner.com/' },
  localStorage: {
    getItem(key) { return Object.prototype.hasOwnProperty.call(storage, key) ? storage[key] : null; },
    setItem(key, value) { storage[key] = String(value); },
  },
  navigator: { mediaSession: media },
  document: document,
  esc(s) { return String(s == null ? '' : s); },
  fetch: async () => ({ ok: true, json: async () => ({ favorites: [] }) }),
  audios: audios,
  nodes: nodes,
  media: media,
  storage: storage,
  flush: flush,
  liveAudio() { return live; },
  fireVisibility() { vis.slice().forEach((fn) => fn()); },
  addEventListener(name, fn) {
    if (name === 'pageshow') pageShow.push(fn);
  },
};
sandbox.window = sandbox;

vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(panelPath, 'utf8'), sandbox, { filename: panelPath });

const tests = `
function tracks(prefix, n) {
  var out = [];
  for (var i = 0; i < n; i++) {
    out.push({
      title: prefix + i,
      filename: prefix + i + '.mp3',
      folder: 'Raw_Signal',
      principle: prefix + i,
    });
  }
  return out;
}
function title() { return nodes.mpTitle.textContent; }
function plays() {
  return audios.reduce(function (n, a) { return n + a.playCount; }, 0);
}
function fail(msg) { throw new Error(msg); }
function hasLog(event, reason) {
  return otPlayerLogEntries().some(function (e) {
    return e.event === event && (reason == null || e.reason === reason);
  });
}
function countLog(event, reason) {
  return otPlayerLogEntries().filter(function (e) {
    return e.event === event && (reason == null || e.reason === reason);
  }).length;
}

// 3. Previous walks played history, not index-1.
otSetPlaylist(tracks('H', 5), { autoplay: true, startIndex: 0, label: 'H' });
otLoadTrack(2, true);
otLoadTrack(4, true);
liveAudio().currentTime = 0;
otPrev();
if (title() !== 'H2') fail('prev should return to H2, got ' + title());
otLoadTrack(4, true);
liveAudio().currentTime = 10;
otPrev();
if (title() !== 'H4') fail('prev inside a track should stay on H4, got ' + title());
if (liveAudio().currentTime !== 0) fail('prev inside a track should restart at 0');

otSetPlaylist(tracks('S', 5), { autoplay: true, startIndex: 0, label: 'S' });
liveAudio().currentTime = 0;
otPrev();
if (title() !== 'S0') fail('no history should restart S0, got ' + title());

// 5. Parked Bluetooth pause keeps the loaded track, list, and position.
var parkedTitle = title();
var parkedSrc = liveAudio().src;
var parkedLabel = nodes.mpFreq.textContent;
liveAudio().currentTime = 42;
flush(0);
liveAudio().pause();
otSetPlaylist(tracks('P', 4), { autoplay: false, startIndex: 0, label: 'PARKED-LIST' });
if (title() !== parkedTitle) fail('parked browse changed the title to ' + title());
if (nodes.mpFreq.textContent !== parkedLabel) fail('parked browse changed the mini label');
if (liveAudio().currentTime !== 42) fail('parked browse moved the playhead');
if (liveAudio().src !== parkedSrc) fail('parked browse changed the loaded file');
if (!liveAudio().paused) fail('parked browse started playback');
media.handlers.play();
if (title() !== parkedTitle) fail('car play changed the title to ' + title());
if (liveAudio().currentTime !== 42) fail('car play restarted at ' + liveAudio().currentTime);
if (liveAudio().paused) fail('car play did not resume');
if (liveAudio().src !== parkedSrc) fail('car play loaded a different file');

// 1. Ignore-pause flag does not survive two buffer swaps.
var beforeA = audios[0].playCount;
var beforeB = audios[1].playCount;
otSetPlaylist(tracks('C', 3), { autoplay: true, startIndex: 0, label: 'C' });
flush(3000);
otNext();
flush(3000);
otNext();
flush(0);
if (audios[1].playCount !== beforeB + 1) fail('preload buffer never went live');
if (audios[0].playCount !== beforeA + 2) fail('retired buffer did not become live again');
if (liveAudio() !== audios[0]) fail('live element after two handoffs is not the original');
if (liveAudio()._otIgnorePause) fail('live element still ignores pause');
var playsAtPause = plays();
media.handlers.pause();
if (!liveAudio().paused) fail('car pause did not pause the element');
document.hidden = false;
fireVisibility();
if (plays() !== playsAtPause) fail('unlock resumed a track whose pause was ignored');
if (media.playbackState !== 'paused') fail('media session stayed ' + media.playbackState);

// 2. One retry, then the next track, then stop. Works while hidden.
otSetPlaylist(tracks('R', 3), { autoplay: true, startIndex: 0, label: 'R' });
document.hidden = true;
var retryTitle = title();
var playsBeforeRetry = plays();
liveAudio().dispatch('error');
flush(3000);
if (title() !== retryTitle) fail('first fault left the track early: ' + title());
if (plays() !== playsBeforeRetry + 1) fail('first fault did not retry the same track');
if (!_otWantAudio) fail('retry pause cleared _otWantAudio');
if (!hasLog('error', '')) fail('missing error log');
if (!hasLog('retry', 'same')) fail('missing same-track retry log');

var playsBeforeNext = plays();
liveAudio().dispatch('error');
flush(3000);
if (title() === retryTitle) fail('second fault did not advance');
if (plays() !== playsBeforeNext + 1) fail('second fault did not start the next track');
if (!hasLog('retry', 'next')) fail('missing next-track retry log');

var stoppedTitle = title();
var playsBeforeStop = plays();
liveAudio().dispatch('error');
flush(3000);
if (title() !== stoppedTitle) fail('cap advanced again to ' + title());
if (plays() !== playsBeforeStop) fail('cap issued another play');
if (nodes.mpStatus.hidden !== false) fail('cap did not show the stop message');
if (nodes.mpStatus.textContent !== 'The stream failed three times. Playback stopped.') {
  fail('stop message was ' + nodes.mpStatus.textContent);
}
if (!hasLog('error', 'cap')) fail('missing cap log');
fireVisibility();
if (plays() !== playsBeforeStop) fail('unlock resumed after the cap');

// A slow buffer that grows again within 15s does not retry, skip, or stop.
otSetPlaylist(tracks('W', 3), { autoplay: true, startIndex: 0, label: 'W' });
document.hidden = true;
var slowTitle = title();
var playsBeforeSlow = plays();
var retriesBeforeSlow = countLog('retry');
liveAudio().dispatch('waiting');
var waitingEntry = otPlayerLogEntries().filter(function (e) { return e.event === 'waiting'; }).pop();
if (!waitingEntry) fail('waiting was not logged');
if (waitingEntry.hidden !== true) fail('waiting log lost document.hidden');
flush(10000);
liveAudio().buffered = { length: 1, end: function () { return 12; } };
flush(5000);
if (title() !== slowTitle) fail('slow buffer changed track to ' + title());
if (plays() !== playsBeforeSlow) fail('slow buffer reloaded or skipped');
if (countLog('retry') !== retriesBeforeSlow) fail('slow buffer logged a retry');
if (!nodes.mpStatus.hidden && nodes.mpStatus.textContent.indexOf('three times') !== -1) fail('slow buffer stopped playback');
if (!hasLog('waiting', 'growing')) fail('slow buffer did not log growth');

// A dead stream with no growth for 15s reloads the same track at its position.
otSetPlaylist(tracks('D', 3), { autoplay: true, startIndex: 0, label: 'D' });
document.hidden = true;
var deadTitle = title();
var playsBeforeDead = plays();
liveAudio().currentTime = 40;
liveAudio().buffered = { length: 1, end: function () { return 40; } };
liveAudio().dispatch('stalled');
flush(15000);
if (title() !== deadTitle) fail('dead stream left the track: ' + title());
if (plays() !== playsBeforeDead + 1) fail('dead stream did not reload the same track');
if (Math.abs(liveAudio().currentTime - 40) > 0.2) fail('reload lost position ' + liveAudio().currentTime);
if (!nodes.mpStatus.hidden && nodes.mpStatus.textContent.indexOf('three times') !== -1) fail('dead stream spent the error cap');
if (!hasLog('retry', 'stall')) fail('dead reload was not logged');
if (!hasLog('stalled', '')) fail('stalled was not logged');

// A phone has one element. Preload must not run in the old 800ms window or after it.
navigator.maxTouchPoints = 1;
var idle = audios.filter(function (a) { return a !== liveAudio(); });
var idlePlays = idle.map(function (a) { return a.playCount; });
var mobileLogAt = otPlayerLogEntries().length;
otSetPlaylist(tracks('M', 4), { autoplay: true, startIndex: 0, label: 'M' });
var mobileEl = liveAudio();
flush(800);
flush(5000);
idle.forEach(function (a, i) {
  if (a.src) fail('mobile preloaded ' + a.src);
  if (a.playCount !== idlePlays[i]) fail('mobile handoff played a second element');
});
var mobileLogs = otPlayerLogEntries().slice(mobileLogAt);
if (mobileLogs.some(function (e) { return e.event === 'handoff'; })) fail('mobile logged a handoff');
if (!mobileLogs.some(function (e) { return e.event === 'loadstart' && e.reason.indexOf('M0.mp3') !== -1; })) {
  fail('load did not log the file name');
}
var mobileSrc = mobileEl.src;
var mobilePlays = mobileEl.playCount;
otNext();
if (liveAudio() !== mobileEl) fail('mobile next left the live element');
if (mobileEl.playCount !== mobilePlays + 1) fail('mobile next did not play the same element');
if (!mobileEl.src || mobileEl.src === mobileSrc) fail('mobile next did not load a new file');
navigator.maxTouchPoints = 0;

// After the src-swap pause has settled, a real pause still counts.
otSetPlaylist(tracks('U', 2), { autoplay: true, startIndex: 0, label: 'U' });
flush(0);
if (!_otWantAudio) fail('playback did not want audio');
liveAudio().pause();
if (_otWantAudio) fail('user pause did not clear _otWantAudio');

// 4. Now playing includes genre art and a position.
otSetupMediaSession({ title: 'Genre Echo', folder: 'Genre_Album', cdnBase: 'https://cdn.example/g' });
var art = media.metadata && media.metadata.artwork && media.metadata.artwork[0] && media.metadata.artwork[0].src;
if (art !== 'https://cdn.example/g/Genre_Album/Cover.png') fail('artwork was ' + art);
if (media.metadata.artist !== 'LUCID TUNER') fail('artist changed');
if (media.metadata.title !== 'Genre Echo') fail('title was not set');
if (!media.position || !(media.position.duration > 0)) fail('position was not published');

// Log shape: last 200, allowed fields only, saved on the phone.
for (var n = 0; n < 250; n++) _otLogPlayer('waiting', 'n' + n);
var entries = otPlayerLogEntries();
if (entries.length !== 200) fail('log length ' + entries.length);
var allowed = { time: 1, event: 1, title: 1, reason: 1, hidden: 1, readyState: 1, networkState: 1 };
entries.forEach(function (e) {
  Object.keys(e).forEach(function (k) {
    if (!allowed[k]) fail('unexpected log field ' + k);
  });
});
var saved = storage.lt_player_log || '';
if (!saved) fail('log was not saved');
if (/email|account|token|tier|presence/i.test(saved)) fail('log saved account data');
if (otPlayerLogText().indexOf('time\\tevent\\ttitle') !== 0) fail('copy text has no header');
`;

try {
  vm.runInContext(tests, sandbox, { filename: 'player-car-tests.js' });
  process.stdout.write(JSON.stringify({
    ok: true,
    plays: audios.reduce((n, a) => n + a.playCount, 0),
    log: sandbox.otPlayerLogEntries().length,
  }));
} catch (err) {
  console.error(err && err.stack ? err.stack : err);
  process.exit(1);
}
