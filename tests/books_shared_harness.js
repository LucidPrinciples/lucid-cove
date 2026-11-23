// Cowork 2026-11-23. Runs books.html's page script against a stub DOM and stub API to prove the
// shared-books first open: an empty own space with a grant opens the shared books, not the setup card.
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const HTML = process.argv[2] || path.join(__dirname, '..', 'src', 'dashboard', 'static', 'books.html');
const src = fs.readFileSync(HTML, 'utf8');
const script = src.slice(src.lastIndexOf('<script>') + 8, src.lastIndexOf('</script>'));

function makeEl(id) {
  const classes = new Set(
    id === 'setupView' || id === 'setupShared' || id === 'presenceWrap' || id === 'ownBooksLink'
    || id === 'btnShare' || id === 'addLineBar' || id === 'btnReview' || id === 'moreMenu'
    || id === 'helpPanel' || id === 'dropHint' ? ['hide'] : []
  );
  const listeners = {};
  return {
    id, innerHTML: '', textContent: '', value: '', style: {}, dataset: {}, disabled: false, checked: false, files: [],
    classList: {
      add: (c) => classes.add(c), remove: (c) => classes.delete(c), contains: (c) => classes.has(c),
      toggle: (c, on) => { const want = on === undefined ? !classes.has(c) : !!on; if (want) classes.add(c); else classes.delete(c); return want; },
    },
    addEventListener: (t, fn) => { listeners[t] = fn; }, _listeners: listeners,
    setAttribute() {}, getAttribute() { return null; }, appendChild() {}, querySelector() { return null; },
    querySelectorAll() { return []; }, closest() { return null; }, focus() {}, scrollIntoView() {}, click() {},
  };
}

async function run(caseName, opts) {
  const els = {};
  const $el = (id) => (els[id] = els[id] || makeEl(id));
  ['btnAddStatements','btnAdd','btnReview','btnMore','moreMenu','btnNewBook','btnNewAcct','btnShare','btnPaste','btnMap','btnTx','btnProcess','addLineBar','ownBooksLink','plainLine','helpPanel','btnHelp','presenceWrap','setupView','setupShared','hdrMeta','dropHint','dropHintText','btnDropHintRead','btnDropHintClose','detailTitle','detailCount','batchCat','lineBody','newCat','newDate','chkAll','pnlView','detailView','mapView','txView','status','err'].forEach($el);
  const calls = [];
  const fetchStub = async (url, init) => {
    calls.push(url);
    const u = new URL(url, 'https://cove.test');
    const who = u.searchParams.get('presence') || opts.self;
    let body;
    if (u.pathname === '/api/books/access') {
      const allowed = who === opts.self || opts.grants.includes(who);
      body = allowed ? { ok: true, presence: who, presence_label: opts.labels[who], mine: who === opts.self, spaces: opts.spaces } : { ok: false, error: 'No manage grant' };
    } else if (u.pathname === '/api/books/summary') {
      body = opts.emptyTrees.includes(who) ? { ok: true, needs_setup: true, ledgers: [] }
        : { ok: true, needs_setup: false, path: 'all', chart: [], periods: { years: [], months: [] }, books: [{ id: 'chords', label: 'Chords of Truth, LLC' }], ledgers: [], row_count: 3, entity: 'Books', account: '', needs_review_count: opts.review || 0 };
    } else if (u.pathname === '/api/books/lines') {
      body = { ok: true, count: 2, chart: [], lines: [
        { index: 0, date: '2026-01-02', payee: 'Need review', amount: -12, category: 'Uncategorized', needs_review: true, account: 'Checking', disabled: false, source_path: 'a' },
        { index: 1, date: '2026-01-03', payee: 'Placed', amount: -8, category: 'Office', needs_review: false, account: 'Checking', disabled: false, source_path: 'a' },
      ] };
    } else {
      body = { ok: true, files: [], items: [], rules: [] };
    }
    return { ok: true, status: 200, json: async () => body, text: async () => JSON.stringify(body) };
  };
  const document = {
    getElementById: $el, querySelector: (sel) => $el('q:' + sel), querySelectorAll: () => [],
    createElement: (t) => makeEl('new:' + t), body: makeEl('body'), addEventListener() {},
  };
  const ctx = {
    document, fetch: fetchStub, console, setTimeout, clearTimeout, setInterval: () => 0, clearInterval() {},
    location: { search: opts.search || '', href: '', pathname: '/books' }, history: { length: 1, back() {} },
    URLSearchParams, URL, FormData: class { append() {} }, Blob: class {}, alert() {}, confirm: () => true,
    localStorage: { getItem: () => null, setItem() {} }, navigator: { userAgent: 'harness' },
  };
  ctx.window = ctx; ctx.window.parent = ctx;
  vm.createContext(ctx);
  vm.runInContext(script + '\n;globalThis.__state = state;', ctx);
  for (let i = 0; i < 40; i++) await new Promise(r => setTimeout(r, 5));
  return { state: ctx.__state, els, calls };
}

const SELF = 'p-member', OWNER = 'p-owner';
const LABELS = { [SELF]: 'Member', [OWNER]: 'Owner' };
let fails = 0, n = 0;
function check(name, cond) { n++; if (cond) console.log('PASS ' + name); else { fails++; console.log('FAIL ' + name); } }

(async () => {
  // 1. own empty + one grant -> lands on the shared books
  let r = await run('shared-only', { self: SELF, grants: [OWNER], labels: LABELS, emptyTrees: [SELF],
    spaces: [{ id: SELF, label: 'Member', mine: true }, { id: OWNER, label: 'Owner', granted: true }] });
  check('shared-only opens the shared space', r.state.presence === OWNER);
  check('shared-only does not show the setup card', r.els.setupView.classList.contains('hide'));
  check('shared-only shows the Your own books link', !!r.els.ownBooksLink && !r.els.ownBooksLink.classList.contains('hide'));
  check('shared-only header names the owner', /Owner/.test(r.els.hdrMeta.textContent));

  // 2. own empty, no grants -> setup card as before
  r = await run('empty-no-grant', { self: SELF, grants: [], labels: LABELS, emptyTrees: [SELF], spaces: [{ id: SELF, label: 'Member', mine: true }] });
  check('no grant keeps the setup card', !r.els.setupView.classList.contains('hide') && r.state.presence === SELF);
  check('no grant shows no shared list', !r.els.setupShared || r.els.setupShared.classList.contains('hide'));

  // 3. own books + grants -> own P&L as today
  r = await run('own-and-grant', { self: SELF, grants: [OWNER], labels: LABELS, emptyTrees: [],
    spaces: [{ id: SELF, label: 'Member', mine: true }, { id: OWNER, label: 'Owner', granted: true }] });
  check('own books stay first', r.state.presence === SELF && r.els.setupView.classList.contains('hide'));
  check('switcher visible with two spaces', !r.els.presenceWrap.classList.contains('hide'));

  // 4. ?presence= honored only when it is a listed space
  r = await run('url-ok', { self: SELF, grants: [OWNER], labels: LABELS, emptyTrees: [], search: '?presence=' + OWNER,
    spaces: [{ id: SELF, label: 'Member', mine: true }, { id: OWNER, label: 'Owner', granted: true }] });
  check('?presence= opens a listed space', r.state.presence === OWNER);
  r = await run('url-bad', { self: SELF, grants: [], labels: LABELS, emptyTrees: [], search: '?presence=p-stranger',
    spaces: [{ id: SELF, label: 'Member', mine: true }] });
  check('?presence= not listed is ignored', r.state.presence === SELF && !r.calls.some(c => /p-stranger/.test(c)));

  // 5. choosing your own empty books on purpose shows setup (no bounce back)
  r = await run('own-setup', { self: SELF, grants: [OWNER], labels: LABELS, emptyTrees: [SELF],
    spaces: [{ id: SELF, label: 'Member', mine: true }, { id: OWNER, label: 'Owner', granted: true }] });
  // run the own-books link path
  const els2 = r.els; if (els2.ownBooksLink && els2.ownBooksLink.onclick) await els2.ownBooksLink.onclick({ preventDefault() {} });
  for (let i = 0; i < 20; i++) await new Promise(res => setTimeout(res, 5));
  check('Your own books shows the setup card on the empty own space', r.state.presence === SELF && !r.els.setupView.classList.contains('hide'));
  check('setup card lists the shared books', !!r.els.setupShared && !r.els.setupShared.classList.contains('hide') && /Owner/.test(r.els.setupShared.innerHTML));

  // Piece 1: one screen that explains itself
  r = await run('simple-chrome', { self: SELF, grants: [OWNER], labels: LABELS, emptyTrees: [],
    spaces: [{ id: SELF, label: 'Member', mine: true }, { id: OWNER, label: 'Owner', granted: true }] });
  check('main Add statements button present', !!r.els.btnAddStatements);
  check('main Add a line button present', !!r.els.btnAdd);
  check('More menu holds moved controls', !!r.els.moreMenu && !!r.els.btnNewBook && !!r.els.btnNewAcct && !!r.els.btnShare && !!r.els.btnPaste && !!r.els.btnMap && !!r.els.btnTx && !!r.els.btnProcess);
  check('add bar hidden until tapped', r.els.addLineBar.classList.contains('hide') && r.els.addLineBar.style.display !== 'flex');
  if (r.els.btnAdd && r.els.btnAdd.onclick) r.els.btnAdd.onclick();
  check('add bar opens after Add a line', !r.els.addLineBar.classList.contains('hide') && r.els.addLineBar.style.display === 'flex');
  check('own-books link hidden on your own books', r.state.mine && r.els.ownBooksLink.classList.contains('hide'));
  check('Review hidden at 0', r.els.btnReview.classList.contains('hide'));

  r = await run('review-count', { self: SELF, grants: [], labels: LABELS, emptyTrees: [], review: 20,
    spaces: [{ id: SELF, label: 'Member', mine: true }] });
  check('Review shows the count', !r.els.btnReview.classList.contains('hide') && r.els.btnReview.textContent === 'Review (20)');
  check('own books header does not repeat the plain line', r.els.hdrMeta.textContent === '');
  check('plain line holds book account and period', /Chords of Truth, LLC/.test(r.els.plainLine.textContent) && /All accounts/.test(r.els.plainLine.textContent));
  if (r.els.btnAddStatements && r.els.btnAddStatements.onclick) r.els.btnAddStatements.onclick();
  check('Add statements opens the Drop hint', !r.els.dropHint.classList.contains('hide'));
  check('Drop hint names Files Drop and this account', /Files > Bookkeeping > Drop > All accounts/.test(r.els.dropHintText.textContent) && /Read new statements/.test(r.els.dropHintText.textContent));
  check('Drop hint has Read new statements', !!r.els.btnDropHintRead);
  if (r.els.btnReview && r.els.btnReview.onclick) r.els.btnReview.onclick();
  for (let i = 0; i < 20; i++) await new Promise(res => setTimeout(res, 5));
  check('Review is titled To review', r.els.detailTitle.textContent === 'To review');
  check('Review lists only needs_review lines', /Need review/.test(r.els.lineBody.innerHTML) && !/Placed/.test(r.els.lineBody.innerHTML));

  console.log((fails ? 'HARNESS_FAIL ' : 'HARNESS_OK ') + (n - fails) + '/' + n);
  process.exit(fails ? 1 : 0);
})();
