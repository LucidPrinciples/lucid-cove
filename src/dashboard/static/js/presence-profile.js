// =============================================================================
// presence-profile.js — the unified Presence page (#176)
// =============================================================================
// ONE rich surface for a Presence (Operator + Agent, shown as a team), serving
// both the public showcase ("business card" — a connection point) and the owner's
// inline editor. Replaces the three fragmented surfaces: the old Team detail
// editor, the Market "Edit Profile" modal, and the seller view-modal.
//
//   PresenceProfile.open(handle, {canEdit, edit})  — view anyone; edit toggle if it's you
//   PresenceProfile.openMe({edit})                 — open your own (optionally straight to edit)
//
// Data: GET /api/profile/{handle} fuses operator + agent identity + offerings.
// Save: POST /api/profile/me (display_name, bio, skills — owner only) + avatar
// uploads, plus POST /api/profile/{handle}/persona for the agent persona (owner
// or Cove admin). The identity spine (archetype / frequency / tuning key) is
// locked post-spark and rendered read-only. The save path syncs the hub profile
// mirror (#173), so edits propagate cross-Cove.
// =============================================================================
(function () {
  'use strict';

  let MY_HANDLE = null;   // cached current-user handle ('' = not signed in / unknown)
  let TAXONOMY = null;    // cached skills taxonomy from /api/profile/me

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function injectStyles() {
    if (document.getElementById('pp-styles')) return;
    const css = `
    .pp-overlay{position:fixed;inset:0;z-index:9000;background:rgba(6,9,20,0.72);
      backdrop-filter:blur(4px);display:flex;align-items:flex-start;justify-content:center;
      overflow-y:auto;padding:32px 16px}
    .pp-panel{width:100%;max-width:860px;background:var(--bg,#0B1022);color:var(--text,#F6F1E7);
      border:1px solid var(--border,#23304d);border-radius:18px;box-shadow:0 24px 80px rgba(0,0,0,.5);
      overflow:hidden;animation:pp-in .18s ease-out}
    @keyframes pp-in{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
    .pp-banner{position:relative;padding:22px 130px 18px 26px;
      background:linear-gradient(135deg,rgba(92,225,230,.16),rgba(92,225,230,.02) 60%);
      border-bottom:1px solid var(--border,#23304d)}
    .pp-cove{font-size:.72rem;letter-spacing:.12em;text-transform:uppercase;color:var(--daily-freq,#5ce1e6);opacity:.9}
    .pp-presence-name{font-size:1.5rem;font-weight:700;margin-top:3px;line-height:1.2}
    .pp-role{display:inline-block;margin-top:6px;font-size:.7rem;letter-spacing:.08em;text-transform:uppercase;
      padding:2px 9px;border:1px solid var(--border,#23304d);border-radius:20px;opacity:.8}
    .pp-controls{position:absolute;top:14px;right:16px;display:flex;align-items:center;gap:10px;z-index:5}
    .pp-x{background:none;border:none;color:var(--text,#F6F1E7);font-size:1.5rem;line-height:1;cursor:pointer;opacity:.6;padding:2px 6px}
    .pp-x:hover{opacity:1}
    .pp-edit-btn{background:rgba(92,225,230,.14);border:1px solid var(--daily-freq,#5ce1e6);
      color:var(--daily-freq,#5ce1e6);border-radius:8px;padding:7px 16px;font-size:.82rem;font-weight:600;cursor:pointer}
    .pp-edit-btn:hover{background:rgba(92,225,230,.26)}
    .pp-grid{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:var(--border,#23304d)}
    @media (max-width:680px){.pp-grid{grid-template-columns:1fr}}
    .pp-col{background:var(--bg,#0B1022);padding:22px 24px}
    .pp-col-h{font-size:.7rem;letter-spacing:.1em;text-transform:uppercase;opacity:.5;margin-bottom:14px}
    .pp-ava-row{display:flex;align-items:center;gap:14px;margin-bottom:14px}
    .pp-ava{width:72px;height:72px;border-radius:50%;object-fit:cover;border:2px solid var(--daily-freq,#5ce1e6);flex:0 0 auto}
    .pp-ava.ph{background:rgba(255,255,255,.05);display:flex;align-items:center;justify-content:center;
      font-size:1.6rem;color:var(--daily-freq,#5ce1e6);font-weight:700}
    .pp-name{font-size:1.12rem;font-weight:600}
    .pp-handle{font-size:.82rem;opacity:.55}
    .pp-bio{font-size:.9rem;line-height:1.55;opacity:.9;margin:6px 0 14px;white-space:pre-wrap}
    .pp-facet{display:flex;gap:8px;font-size:.84rem;margin:5px 0}
    .pp-facet b{color:var(--daily-freq,#5ce1e6);font-weight:600;min-width:78px;opacity:.85}
    .pp-key{margin:12px 0 4px;padding:10px 14px;border-left:3px solid var(--daily-freq,#5ce1e6);
      background:rgba(92,225,230,.05);font-style:italic;font-size:.88rem;line-height:1.5;opacity:.92}
    .pp-chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
    .pp-chip{font-size:.74rem;padding:3px 10px;border:1px solid var(--border,#23304d);border-radius:20px;opacity:.85}
    .pp-chip.tog{cursor:pointer}.pp-chip.tog.on{background:var(--daily-freq,#5ce1e6);color:#0B1022;border-color:var(--daily-freq,#5ce1e6)}
    .pp-chipgroup{margin-bottom:8px}.pp-chipgroup-h{font-size:.66rem;letter-spacing:.06em;text-transform:uppercase;opacity:.45;margin:6px 0 4px}
    .pp-offers{padding:20px 24px;border-top:1px solid var(--border,#23304d)}
    .pp-offers-h{font-size:.7rem;letter-spacing:.1em;text-transform:uppercase;opacity:.5;margin-bottom:12px}
    .pp-offer-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px}
    .pp-offer{border:1px solid var(--border,#23304d);border-radius:12px;padding:12px 14px;display:flex;flex-direction:column;gap:6px}
    .pp-offer-t{font-weight:600;font-size:.92rem}
    .pp-offer-d{font-size:.8rem;opacity:.7;line-height:1.4;flex:1}
    .pp-offer-foot{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-top:4px}
    .pp-price{font-size:.82rem;opacity:.85}
    .pp-buy{background:var(--daily-freq,#5ce1e6);color:#0B1022;border:none;border-radius:8px;padding:5px 12px;font-weight:600;font-size:.8rem;cursor:pointer;text-decoration:none}
    .pp-buy:hover{opacity:.9}.pp-buy:disabled{opacity:.4;cursor:default}
    .pp-input,.pp-textarea{width:100%;background:rgba(255,255,255,.05);border:1px solid var(--border,#23304d);
      border-radius:8px;padding:8px 10px;color:inherit;font:inherit;margin-bottom:10px}
    .pp-flabel{font-size:.72rem;letter-spacing:.05em;text-transform:uppercase;opacity:.6;margin:8px 0 4px}
    .pp-file{font-size:.78rem;margin-bottom:8px}
    .pp-actions{display:flex;align-items:center;gap:12px;padding:16px 24px;border-top:1px solid var(--border,#23304d)}
    .pp-save{background:var(--daily-freq,#5ce1e6);color:#0B1022;border:none;border-radius:9px;padding:9px 18px;font-weight:600;cursor:pointer}
    .pp-cancel{background:none;border:1px solid var(--border,#23304d);color:inherit;border-radius:9px;padding:9px 16px;cursor:pointer}
    .pp-status{font-size:.82rem;opacity:.75}
    .pp-agent-empty{padding:6px 0}
    .pp-empty-t{font-weight:600;font-size:1rem;margin-bottom:6px}
    .pp-empty-d{font-size:.86rem;opacity:.7;line-height:1.55;margin-bottom:14px}
    .pp-upsell{background:rgba(92,225,230,.12);border:1px solid var(--daily-freq,#5ce1e6);color:var(--daily-freq,#5ce1e6);
      border-radius:9px;padding:8px 16px;font-weight:600;font-size:.84rem;cursor:pointer}
    .pp-upsell:hover{background:rgba(92,225,230,.22)}
    .pp-spin,.pp-empty{padding:40px;text-align:center;opacity:.6}`;
    const st = document.createElement('style');
    st.id = 'pp-styles'; st.textContent = css;
    document.head.appendChild(st);
  }

  async function loadMe() {
    if (MY_HANDLE !== null) return;
    try {
      const r = await fetch('/api/profile/me', { credentials: 'same-origin' });
      if (r.ok) { const d = await r.json(); MY_HANDLE = (d.handle || '').toLowerCase(); TAXONOMY = d.taxonomy || {}; }
    } catch (e) { /* ignore */ }
    if (MY_HANDLE === null) MY_HANDLE = '';
  }

  function mount() {
    const old = document.getElementById('pp-overlay');
    if (old) old.remove();
    const root = document.createElement('div');
    root.id = 'pp-overlay'; root.className = 'pp-overlay';
    root.innerHTML = '<div class="pp-panel"><div class="pp-spin">Loading…</div></div>';
    root.onclick = (e) => { if (e.target === root) root.remove(); };
    document.body.appendChild(root);
    return root;
  }

  function initials(s) { return (String(s || '?').trim()[0] || '?').toUpperCase(); }

  // Avatars use a stable filename ({handle}-{kind}.ext), so the URL doesn't change
  // when re-uploaded — bust the browser cache so the fresh image (and not a stale
  // 404) loads. #176.
  function bust(u) { return u ? u + (u.indexOf('?') < 0 ? '?t=' : '&t=') + Date.now() : u; }

  function avaHTML(url, name, cls, id) {
    const a = id ? ` id="${id}"` : '';
    return url
      ? `<img${a} class="pp-ava ${cls || ''}" src="${esc(bust(url))}" alt="">`
      : `<div${a} class="pp-ava ph ${cls || ''}">${esc(initials(name))}</div>`;
  }

  function chips(arr) {
    return (arr || []).map(s => `<span class="pp-chip">${esc(s)}</span>`).join('');
  }

  // Read-only personality dials (0-100) as bars. (CF-58)
  function dialsHTML(dials) {
    const keys = Object.keys(dials || {});
    if (!keys.length) return '';
    const rows = keys.map(k => {
      const v = Math.max(0, Math.min(100, parseInt(dials[k], 10) || 0));
      return `<div style="margin:6px 0"><div style="display:flex;justify-content:space-between;font-size:.72rem;opacity:.8"><span style="text-transform:capitalize">${esc(k.replace(/_/g,' '))}</span><span style="opacity:.5">${v}</span></div><div style="height:6px;background:var(--border,#23304d);border-radius:3px;overflow:hidden;margin-top:2px"><span style="display:block;height:100%;width:${v}%;background:var(--daily-freq,#5ce1e6)"></span></div></div>`;
    }).join('');
    return `<div style="margin-top:12px"><div style="font-size:.66rem;letter-spacing:.06em;text-transform:uppercase;opacity:.45;margin-bottom:4px">Personality dials</div>${rows}</div>`;
  }

  // Read-only lens: chips + statement + standing preferences (the specialized lines set in
  // Cove setup that ride the system prompt). (CF-58)
  function lensHTML(lens) {
    lens = lens || {};
    const lchips = lens.chips || [], stmt = lens.statement || '', prefs = lens.standing_preferences || [];
    if (!lchips.length && !stmt && !(prefs && prefs.length)) return '';
    let h = `<div style="margin-top:12px"><div style="font-size:.66rem;letter-spacing:.06em;text-transform:uppercase;opacity:.45;margin-bottom:4px">Lens &amp; specialized lines</div>`;
    if (lchips.length) h += `<div class="pp-chips">${lchips.map(c => `<span class="pp-chip">${esc(c)}</span>`).join('')}</div>`;
    if (stmt) h += `<div style="font-size:.78rem;opacity:.85;margin-top:6px;font-style:italic">${esc(stmt)}</div>`;
    if (prefs && prefs.length) h += `<ul style="margin:6px 0 0;padding-left:16px;font-size:.76rem;opacity:.85">${prefs.map(p => `<li style="margin:2px 0">${esc(p)}</li>`).join('')}</ul>`;
    return h + `</div>`;
  }

  // No agent yet (Operator/Free tier — agent is the Cove threshold). For the owner,
  // nudge an upgrade; for a viewer, a quiet line.
  function agentEmptyHTML(canEdit) {
    if (canEdit) {
      return `<div class="pp-agent-empty">
        <div class="pp-empty-t">No intelligence yet</div>
        <div class="pp-empty-d">An agent comes with a Cove — a tuned Presence that works alongside you. Upgrade to add your own intelligence.</div>
        <button class="pp-upsell" id="pp-add-intel">Add an intelligence →</button></div>`;
    }
    return '<div class="pp-bio" style="opacity:.4">Operator — no agent.</div>';
  }

  function wireAddIntel(root) {
    const ai = root.querySelector('#pp-add-intel');
    if (ai) ai.onclick = () => {
      root.remove();
      if (typeof window.showUpgradeModal === 'function') window.showUpgradeModal();
    };
  }

  function priceOf(t) {
    if (t.settlement === 'credits' && t.price_credits > 0) return t.price_credits + ' LPC';
    if (t.price_cents > 0) return '$' + (t.price_cents / 100).toFixed(2);
    return 'Free';
  }

  function offersHTML(offerings) {
    if (!offerings || !offerings.length) return '<div class="pp-empty" style="padding:14px">No offerings yet.</div>';
    return '<div class="pp-offer-grid">' + offerings.map(l => {
      if (l.link_url) {
        return `<div class="pp-offer"><div class="pp-offer-t">${esc(l.title)}</div>
          ${l.description ? `<div class="pp-offer-d">${esc(l.description)}</div>` : ''}
          <div class="pp-offer-foot"><span class="pp-price">External</span>
          <a class="pp-buy" href="${esc(l.link_url)}" target="_blank" rel="noopener noreferrer">Visit →</a></div></div>`;
      }
      const t = (l.tiers || [])[0];
      const price = t ? priceOf(t) : '';
      const buy = t && (t.settlement === 'credits' && t.price_credits > 0 || t.price_cents > 0 || price === 'Free')
        ? `<button class="pp-buy" data-pp-tier="${t.tier_id}">${price === 'Free' ? 'Get' : 'Buy'}</button>` : '';
      return `<div class="pp-offer"><div class="pp-offer-t">${esc(l.title)}</div>
        ${l.description ? `<div class="pp-offer-d">${esc(l.description)}</div>` : ''}
        <div class="pp-offer-foot"><span class="pp-price">${esc(price)}</span>${buy}</div></div>`;
    }).join('') + '</div>';
  }

  async function buyTier(tierId, btn) {
    const orig = btn.textContent; btn.disabled = true; btn.textContent = '…';
    try {
      const r = await fetch('/api/market/buy', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ tier_id: tierId }),
      });
      if (r.status === 402) { btn.disabled = false; btn.textContent = orig; alert('Not enough LPC for this — use Top Up to add credits.'); return; }
      const d = await r.json();
      if (d.checkout_url) { window.location.href = d.checkout_url; return; }
      if (d.granted) { btn.textContent = '✓ Owned'; return; }
      btn.disabled = false; btn.textContent = orig;
    } catch (e) { btn.disabled = false; btn.textContent = 'Retry'; }
  }

  function renderView(root, p, canEdit) {
    const op = p.operator || {}, ag = p.agent || {};
    const panel = root.querySelector('.pp-panel');
    // Door to this Presence's own Mission Control. Build it on the SAME host you're
    // reaching the Cove through RIGHT NOW, so it's always reachable: only jump to the
    // real {handle}.{domain} subdomain when you're ALREADY on the cove domain (which
    // proves its DNS resolves). On localhost / an IP / the mesh / a claimed-but-not-yet-
    // resolvable address, stay on this origin with ?as= — otherwise the link is a dead
    // subdomain (NXDOMAIN) even though an address was "set". Real reachability of the
    // subdomain itself is CF-90 (mesh-first).
    const _dom = (typeof MC !== 'undefined' && MC.config && MC.config.domain) || '';
    const _onDomain = _dom && location.host.endsWith(_dom);
    const mcHref = !p.handle ? '' : (_onDomain
      ? location.protocol + '//' + p.handle + '.' + _dom
      : location.origin + '/?as=' + encodeURIComponent(p.handle));
    const agentFacets = [
      ag.archetype ? `<div class="pp-facet"><b>Archetype</b><span>${esc(ag.archetype)}</span></div>` : '',
      ag.frequency ? `<div class="pp-facet"><b>Frequency</b><span>${esc(ag.frequency)}</span></div>` : '',
      ag.nickname ? `<div class="pp-facet"><b>Persona</b><span>${esc(ag.nickname)}</span></div>` : '',
    ].join('');
    panel.innerHTML = `
      <div class="pp-banner">
        <div class="pp-controls">
          ${mcHref ? `<a class="pp-edit-btn" style="text-decoration:none" href="${esc(mcHref)}" target="_blank" rel="noopener" title="Open ${esc(op.name || p.handle)}'s Mission Control">Open MC &#8599;</a>` : ''}
          ${canEdit ? '<button class="pp-edit-btn" id="pp-edit">Edit</button>' : ''}
          <button class="pp-x" title="Close">&times;</button>
        </div>
        ${ag.cove ? `<div class="pp-cove">${esc(ag.cove)}</div>` : ''}
        <div class="pp-presence-name">${esc(op.name || ('@' + p.handle))}${ag.name ? ` <span style="opacity:.5;font-weight:400">+ ${esc(ag.name)}</span>` : ''}</div>
        <span class="pp-role">${ag.name ? 'Presence' : 'Operator'}</span>
      </div>
      <div class="pp-grid">
        <div class="pp-col">
          <div class="pp-col-h">Operator</div>
          <div class="pp-ava-row">${avaHTML(op.avatar_url, op.name)}
            <div><div class="pp-name">${esc(op.name || ('@' + p.handle))}</div>
            <div class="pp-handle">@${esc(p.handle)}</div></div></div>
          ${op.bio ? `<div class="pp-bio">${esc(op.bio)}</div>` : '<div class="pp-bio" style="opacity:.4">No bio yet.</div>'}
          ${chips(op.skills) ? `<div class="pp-chips">${chips(op.skills)}</div>` : ''}
        </div>
        <div class="pp-col">
          <div class="pp-col-h">Agent</div>
          ${ag.name ? `<div class="pp-ava-row">${avaHTML(ag.avatar_url, ag.name, '')}
            <div><div class="pp-name">${esc(ag.name)}${ag.cove ? ` ${esc(ag.cove)}` : ''}</div>
            <div class="pp-handle">Personal intelligence</div></div></div>
          ${agentFacets}
          ${ag.tuning_key ? `<div class="pp-key">“${esc(ag.tuning_key)}”</div>` : ''}
          ${dialsHTML(ag.personality)}
          ${lensHTML(ag.lens)}
          ${chips(ag.skills) ? `<div class="pp-chips">${chips(ag.skills)}</div>` : ''}`
          : agentEmptyHTML(canEdit)}
        </div>
      </div>
      <div class="pp-offers"><div class="pp-offers-h">Offerings</div>${offersHTML(p.offerings)}</div>`;
    panel.querySelector('.pp-x').onclick = () => root.remove();
    const eb = panel.querySelector('#pp-edit');
    if (eb) eb.onclick = () => startEdit(root, p);
    panel.querySelectorAll('[data-pp-tier]').forEach(b => b.onclick = () => buyTier(parseInt(b.dataset.ppTier, 10), b));
    wireAddIntel(panel);
  }

  // Canonical dial keys (mirrors _render_personality poles server-side) — used
  // when a persona has no dials stored yet.
  const CANON_DIALS = ['directness', 'warmth', 'humor', 'challenge', 'formality'];

  // Editable persona controls (CF-29): sliders + shade + lens + nickname.
  function personaEditHTML(ag) {
    const stored = ag.personality || {};
    const dialKeys = Object.keys(stored).length ? Object.keys(stored) : CANON_DIALS;
    const sliderRows = dialKeys.map(k => {
      const v = Math.max(0, Math.min(100, parseInt(stored[k], 10) || (k in stored ? 0 : 50)));
      return `<div style="margin:6px 0"><div style="display:flex;justify-content:space-between;font-size:.72rem;opacity:.8"><span style="text-transform:capitalize">${esc(k.replace(/_/g, ' '))}</span><span data-dial-val="${esc(k)}" style="opacity:.6">${v}</span></div><input type="range" min="0" max="100" value="${v}" data-dial="${esc(k)}" style="width:100%"></div>`;
    }).join('');
    const lens = ag.lens || {};
    return `
      <div class="pp-flabel">Nickname</div>
      <input class="pp-input" id="pp-ps-nickname" maxlength="60" value="${esc(ag.nickname || '')}" placeholder="What they go by">
      <div class="pp-flabel">Shade <span style="opacity:.6;font-weight:normal;">(optional: a secondary energy your agent leans into, alongside its main archetype. Leave blank for none.)</span></div>
      <input class="pp-input" id="pp-ps-shade" maxlength="80" value="${esc(ag.shade || '')}">
      <div class="pp-flabel">Personality dials</div>
      <div id="pp-ps-dials">${sliderRows}</div>
      <div class="pp-flabel">Lens chips (comma-separated, max 8)</div>
      <input class="pp-input" id="pp-ps-chips" value="${esc((lens.chips || []).join(', '))}">
      <div class="pp-flabel">Lens statement</div>
      <textarea class="pp-textarea" id="pp-ps-stmt" rows="3" maxlength="280">${esc(lens.statement || '')}</textarea>
      <div class="pp-flabel">Standing preferences (one per line, max 8)</div>
      <textarea class="pp-textarea" id="pp-ps-prefs" rows="4">${esc((lens.standing_preferences || []).join('\n'))}</textarea>`;
  }

  function collectPersona(panel) {
    const g = (id) => { const el = panel.querySelector(id); return el ? el.value : ''; };
    const dials = {};
    panel.querySelectorAll('input[data-dial]').forEach(sl => {
      dials[sl.dataset.dial] = Math.max(0, Math.min(100, parseInt(sl.value, 10) || 0));
    });
    return {
      nickname: (g('#pp-ps-nickname') || '').trim(),
      shade: (g('#pp-ps-shade') || '').trim(),
      personality: dials,
      lens: {
        chips: String(g('#pp-ps-chips') || '').split(',').map(x => x.trim()).filter(Boolean).slice(0, 8),
        statement: (g('#pp-ps-stmt') || '').trim(),
        standing_preferences: String(g('#pp-ps-prefs') || '').split('\n').map(x => x.trim()).filter(Boolean).slice(0, 8),
      },
    };
  }

  function startEdit(root, p) {
    const op = p.operator || {}, ag = p.agent || {};
    const isOwn = !!MY_HANDLE && MY_HANDLE === (p.handle || '').toLowerCase();
    const isAdmin = !!(typeof MC !== 'undefined' && MC.config && MC.config.is_cove_admin);
    // Private persona rides p.agent (personality/lens/shade) when the server
    // assembled with include_private — its presence means the caller may see it.
    const hasPrivate = !!(ag && (ag.personality !== undefined || ag.lens !== undefined || ag.shade !== undefined));
    const showPersona = hasPrivate && !!ag.name && (isOwn || isAdmin);
    const showPresentation = isOwn;   // operator presentation stays owner-only
    const tax = TAXONOMY || p.taxonomy || {};
    const selected = new Set((op.skills || []).map(String));
    const panel = root.querySelector('.pp-panel');
    const chipRows = Object.keys(tax).map(group =>
      `<div class="pp-chipgroup"><div class="pp-chipgroup-h">${esc(group)}</div>` +
      tax[group].map(s => `<span class="pp-chip tog${selected.has(s) ? ' on' : ''}" data-skill="${esc(s)}">${esc(s)}</span>`).join('') +
      '</div>').join('');
    const opCol = showPresentation ? `
          <div class="pp-col-h">Operator</div>
          <div class="pp-ava-row">${avaHTML(op.avatar_url, op.name, '', 'pp-op-ava')}
            <div style="flex:1"><div class="pp-flabel">Profile photo</div>
            <input type="file" class="pp-file" id="pp-op-file" accept="image/png,image/jpeg,image/webp,image/gif"></div></div>
          <div class="pp-flabel">Display name</div>
          <input class="pp-input" id="pp-name" value="${esc(op.name || '')}" placeholder="Your name">
          <div class="pp-flabel">Bio</div>
          <textarea class="pp-textarea" id="pp-bio" rows="4" placeholder="Who you are, what you're about…">${esc(op.bio || '')}</textarea>
          <div class="pp-flabel">Skills</div>
          <div>${chipRows || '<span style="opacity:.5">No taxonomy.</span>'}</div>` : `
          <div class="pp-col-h">Operator</div>
          <div class="pp-ava-row">${avaHTML(op.avatar_url, op.name)}
            <div><div class="pp-name">${esc(op.name || ('@' + p.handle))}</div>
            <div class="pp-handle">@${esc(p.handle)}</div></div></div>
          <div class="pp-bio" style="opacity:.5">Presentation (photo, bio, skills) is edited by the owner.</div>`;
    // Locked spine: shown, never inputs. Changing WHO the agent is = a new agent.
    const spine = [ag.archetype, ag.frequency, ag.tuning_key].filter(Boolean).join(' · ');
    let agCol = '<div class="pp-col-h">Agent</div>';
    if (ag.name) {
      // The avatar upload endpoint writes the CALLER's own profile, so only offer
      // it on your own card (an admin uploading here would hit their own avatar).
      agCol += isOwn ? `<div class="pp-ava-row">${avaHTML(ag.avatar_url, ag.name, '', 'pp-ag-ava')}
            <div style="flex:1"><div class="pp-flabel">Agent avatar</div>
            <input type="file" class="pp-file" id="pp-ag-file" accept="image/png,image/jpeg,image/webp,image/gif"></div></div>`
        : `<div class="pp-ava-row">${avaHTML(ag.avatar_url, ag.name)}
            <div><div class="pp-name">${esc(ag.name)}</div>
            <div class="pp-handle">Personal intelligence</div></div></div>`;
      agCol += `<div class="pp-facet"><b>Name</b><span>${esc(ag.name)}${ag.cove ? ` ${esc(ag.cove)}` : ''}</span></div>`;
      if (spine) agCol += `<div style="font-size:.8rem;opacity:.7;margin:6px 0">\u{1F512} ${esc(spine)} <span style="opacity:.55">(locked)</span></div>`;
      agCol += showPersona ? personaEditHTML(ag)
        : `<div style="font-size:.76rem;opacity:.5;margin-top:10px">Agent identity (archetype, frequency, tuning key) is set during tuning — it's shown here, not edited.</div>`;
    } else {
      agCol += agentEmptyHTML(true);
    }
    panel.innerHTML = `
      <div class="pp-banner">
        <div class="pp-controls"><button class="pp-x" title="Close">&times;</button></div>
        ${ag.cove ? `<div class="pp-cove">${esc(ag.cove)}</div>` : ''}
        <div class="pp-presence-name">${isOwn ? (ag.name ? 'Edit your Presence' : 'Edit your profile') : 'Edit ' + esc(ag.name || ('@' + p.handle))}</div>
        <span class="pp-role">@${esc(p.handle)}</span>
      </div>
      <div class="pp-grid">
        <div class="pp-col">${opCol}
        </div>
        <div class="pp-col">${agCol}
        </div>
      </div>
      <div class="pp-actions">
        <button class="pp-save" id="pp-save">Save</button>
        <button class="pp-cancel" id="pp-cancel">Cancel</button>
        <span class="pp-status" id="pp-status"></span>
      </div>`;
    panel.querySelector('.pp-x').onclick = () => root.remove();
    panel.querySelector('#pp-cancel').onclick = () => open(p.handle, { canEdit: true });
    wireAddIntel(panel);
    panel.querySelectorAll('.pp-chip.tog').forEach(ch => ch.onclick = () => {
      const s = ch.dataset.skill;
      if (selected.has(s)) { selected.delete(s); ch.classList.remove('on'); }
      else { selected.add(s); ch.classList.add('on'); }
    });
    // Live value readout on the persona sliders.
    panel.querySelectorAll('input[data-dial]').forEach(sl => {
      sl.oninput = () => {
        const out = panel.querySelector(`[data-dial-val="${sl.dataset.dial}"]`);
        if (out) out.textContent = sl.value;
      };
    });
    const status = panel.querySelector('#pp-status');
    const wireUpload = (kind, fileId, avaId) => {
      const fi = panel.querySelector('#' + fileId); if (!fi) return;
      fi.onchange = async () => {
        const f = fi.files && fi.files[0]; if (!f) return;
        const fd = new FormData(); fd.append('file', f);
        status.textContent = 'Uploading…';
        try {
          const r = await fetch('/api/profile/avatar?kind=' + kind, { method: 'POST', credentials: 'same-origin', body: fd });
          if (!r.ok) { status.textContent = 'Upload failed (' + r.status + ').'; return; }
          const d = await r.json();
          status.textContent = '✓ Photo updated';
          if (d.url) {  // live-refresh the preview (cache-busted)
            const el = panel.querySelector('#' + avaId);
            if (el) {
              if (el.tagName === 'IMG') { el.src = bust(d.url); }
              else { const ni = document.createElement('img'); ni.id = avaId; ni.className = 'pp-ava'; ni.src = bust(d.url); el.replaceWith(ni); }
            }
          }
        } catch (e) { status.textContent = 'Upload failed — try again.'; }
      };
    };
    wireUpload('operator', 'pp-op-file', 'pp-op-ava');
    wireUpload('agent', 'pp-ag-file', 'pp-ag-ava');
    panel.querySelector('#pp-save').onclick = async () => {
      status.textContent = 'Saving…';
      let latest = null;   // freshest full profile returned by a save call
      try {
        if (showPresentation) {
          const r = await fetch('/api/profile/me', {
            method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              display_name: (panel.querySelector('#pp-name').value || '').trim(),
              bio: (panel.querySelector('#pp-bio').value || '').trim(),
              skills: Array.from(selected),
            }),
          });
          if (!r.ok) { status.textContent = 'Save failed (' + r.status + ').'; return; }
          latest = await r.json();
        }
        if (showPersona) {
          const r2 = await fetch('/api/profile/' + encodeURIComponent(p.handle) + '/persona', {
            method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(collectPersona(panel)),
          });
          if (!r2.ok) {
            let msg = 'Save failed (' + r2.status + ').';
            try { const e2 = await r2.json(); if (e2 && e2.detail) msg = String(e2.detail); } catch (e) { /* ignore */ }
            status.textContent = msg; return;
          }
          latest = await r2.json();
        }
        status.textContent = '✓ Saved';
        if (latest && latest.handle) setTimeout(() => renderView(root, latest, true), 400);
        else setTimeout(() => open(p.handle, { canEdit: true }), 500);
      } catch (e) { status.textContent = 'Save failed — try again.'; }
    };
  }

  async function open(handle, opts) {
    opts = opts || {};
    handle = (handle || '').replace(/^@/, '').toLowerCase();
    if (!handle) { alert('No profile to show.'); return; }
    injectStyles();
    const root = mount();
    let prof;
    try {
      const r = await fetch('/api/profile/' + encodeURIComponent(handle), { credentials: 'same-origin' });
      if (!r.ok) throw new Error(r.status);
      prof = await r.json();
    } catch (e) {
      root.querySelector('.pp-panel').innerHTML =
        '<div class="pp-banner"><button class="pp-x">&times;</button><div class="pp-presence-name">Profile</div></div>' +
        '<div class="pp-empty">This profile is unavailable right now.</div>';
      root.querySelector('.pp-x').onclick = () => root.remove();
      return;
    }
    await loadMe();
    const canEdit = !!opts.canEdit || (!!MY_HANDLE && MY_HANDLE === handle);
    if (opts.edit && canEdit) startEdit(root, prof);
    else renderView(root, prof, canEdit);
  }

  async function openMe(opts) {
    opts = opts || {};
    await loadMe();
    if (!MY_HANDLE) { alert('Sign in to view your profile.'); return; }
    return open(MY_HANDLE, Object.assign({ canEdit: true }, opts));
  }

  window.PresenceProfile = { open, openMe };
})();
