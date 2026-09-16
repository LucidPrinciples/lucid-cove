/* Public Tuner Action. Flows on/off mint Actions. No Links, Tools, or Tune. */
(function () {
  const STORE = "lt-tuner-daily-actions-v1";
  const ACTIVE_STORE = "lt-tuner-flow-active-v1";
  const FLOW_WALK = "walk-lucid-path";
  const FLOW_GRATITUDE = "gratitude";
  let openId = null;

  const WALKS = [
    {
      id: "pattern-break",
      title: "Pattern Break",
      blurb: "Catch the sentence that runs the day before it broadcasts.",
      teach: "Under {frequency}, {principle} is already in the room. Most of a day is a sentence you did not pick — a reflex that broadcasts before you speak. The Key is already pointing at it.",
      prompt: "The first snag hits. What do you do with the sentence?",
      options: [
        {
          id: "script",
          label: "Let the script run",
          landing: "The day agrees with whoever is broadcasting. You got proof, and the proof is the old loop. Notice that. You can still step out on the next snag.",
        },
        {
          id: "catch",
          label: "Catch it as a sound",
          landing: "Same sentence, new position. You heard it arrive instead of becoming it. That distance is the Observer. Stay there for one breath under the Key.",
        },
      ],
      close: "Sit with the Key. Do not add a second story. One different response is the whole walk.",
    },
    {
      id: "look-again",
      title: "Look Again",
      blurb: "Same moment, two broadcasts. Walk both if you want.",
      teach: "Today’s triad is {frequency} · {principle}. The first look is usually the old rendering. The second look is the practice — not a better argument, a different attention.",
      prompt: "You are in the moment. Which pass do you take?",
      options: [
        {
          id: "first",
          label: "Trust the first look",
          landing: "First look is fast because it is familiar. Name what it is protecting. You can still look again without throwing the first pass away.",
        },
        {
          id: "second",
          label: "Look again",
          landing: "Second look is slower on purpose. Ask what else is here that the first pass skipped. Hold that under the Key for three breaths.",
        },
      ],
      close: "If you only took one path, you can run this Walk again and take the other. Both are data.",
    },
    {
      id: "hold-or-move",
      title: "Hold or Move",
      blurb: "Constructive or corrective — same Key, different β.",
      teach: "The Love Equation is already running. Under {frequency} and {principle}, you are either feeding the Key (constructive) or fighting it (corrective). Corrective is recalibration, not failure.",
      prompt: "Where is your attention on this Key right now?",
      options: [
        {
          id: "hold",
          label: "Hold the broadcast",
          landing: "Stay with the Key as it is written. Do not improve it. One quiet minute of holding is the practice.",
        },
        {
          id: "move",
          label: "Recalibrate",
          landing: "Something is off. Name one interference without fixing the whole day. Then return to the Key as written — not a new slogan.",
        },
      ],
      close: "Either path still uses today’s triad. Do not start a second Tune to make it feel finished.",
    },
    {
      id: "not-same-again",
      title: "Not the Same Again",
      blurb: "One fork between repeating yesterday and taking a different step.",
      teach: "The Key is already a fork: {key} Let {frequency} and {principle} decide the next hour, not the whole year.",
      prompt: "What does the next hour do?",
      options: [
        {
          id: "repeat",
          label: "Repeat yesterday",
          landing: "Repeating is honest if you see it. Name the loop out loud once. Seeing it is already a different step.",
        },
        {
          id: "different",
          label: "Take one different step",
          landing: "Pick one small act that would not have happened on yesterday’s script. Do it before the hour is gone. That is the walk.",
        },
      ],
      close: "One hour. One Key. You can choose the other fork later today if you want to see it.",
    },
  ];

  function localDay() {
    if (typeof _tfTodayStr === "function") return _tfTodayStr();
    const d = new Date();
    const p = (n) => String(n).padStart(2, "0");
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate());
  }

  function loadStore() {
    try {
      const raw = JSON.parse(localStorage.getItem(STORE) || "null");
      if (!raw || raw.day !== localDay() || !Array.isArray(raw.actions)) {
        return { day: localDay(), actions: [] };
      }
      return { day: raw.day, actions: raw.actions };
    } catch (_) {
      return { day: localDay(), actions: [] };
    }
  }

  function saveStore(data) {
    try {
      localStorage.setItem(STORE, JSON.stringify(data));
    } catch (_) {}
  }

  function emptyActive() {
    const out = {};
    out[FLOW_WALK] = false;
    out[FLOW_GRATITUDE] = false;
    return out;
  }

  function loadActive() {
    const base = emptyActive();
    try {
      const raw = JSON.parse(localStorage.getItem(ACTIVE_STORE) || "null");
      if (!raw || typeof raw !== "object") return base;
      base[FLOW_WALK] = !!raw[FLOW_WALK];
      base[FLOW_GRATITUDE] = !!raw[FLOW_GRATITUDE];
      return base;
    } catch (_) {
      return base;
    }
  }

  function saveActive(active) {
    try {
      localStorage.setItem(ACTIVE_STORE, JSON.stringify(active));
    } catch (_) {}
  }

  function uid() {
    return "a-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 8);
  }

  function triadOf(drop) {
    if (!drop) return null;
    const frequency = String(drop.frequency || "").trim();
    const principle = String(drop.principle || "").trim();
    const tuning_key = String(drop.tuning_key || "").trim();
    if (!frequency && !principle && !tuning_key) return null;
    return { frequency, principle, tuning_key };
  }

  function isPro() {
    return !!(window.MC && window.MC.tier && window.MC.tier.level >= 5);
  }

  function openUpgrade() {
    if (typeof showUpgradeModal === "function") showUpgradeModal();
  }

  function fill(text, triad) {
    const t = triad || {};
    const map = {
      "{frequency}": t.frequency || "this frequency",
      "{principle}": t.principle || "this principle",
      "{key}": t.tuning_key || "today’s Key",
    };
    let out = String(text || "");
    Object.keys(map).forEach((token) => {
      out = out.split(token).join(map[token]);
    });
    return out;
  }

  function walkById(id) {
    return WALKS.filter((w) => w.id === id)[0] || null;
  }

  function defaultWalkState() {
    return { templateId: null, node: "choose", path: null };
  }

  function hasFlow(store, flow) {
    return store.actions.some((a) => a.flow === flow);
  }

  function addAction(flow, title, body, triad, extra) {
    const store = loadStore();
    if (hasFlow(store, flow)) return store;
    const row = {
      id: uid(),
      flow,
      title,
      body: String(body || ""),
      triad: triad || null,
      done: false,
      created_at: Date.now(),
    };
    if (extra && typeof extra === "object") {
      Object.keys(extra).forEach((k) => {
        row[k] = extra[k];
      });
    }
    store.actions.push(row);
    saveStore(store);
    return store;
  }

  function mintActive(store, triad) {
    if (!triad) return store;
    const active = loadActive();
    let next = store;
    if (active[FLOW_WALK] && !hasFlow(next, FLOW_WALK)) {
      next = addAction(FLOW_WALK, "Walk the Lucid Path", "", triad, { walk: defaultWalkState() });
    }
    if (active[FLOW_GRATITUDE] && !hasFlow(next, FLOW_GRATITUDE)) {
      next = addAction(FLOW_GRATITUDE, "Gratitude", "", triad, {});
    }
    return next;
  }

  function setFlowActive(flow, on, triad, drop) {
    const active = loadActive();
    active[flow] = !!on;
    saveActive(active);
    const store = mintActive(loadStore(), triad);
    paintAll(store, triad, drop);
  }

  function markDone(id) {
    const store = loadStore();
    store.actions.forEach((a) => {
      if (a.id === id) a.done = true;
    });
    saveStore(store);
    return store;
  }

  function patchAction(id, fn) {
    const store = loadStore();
    store.actions.forEach((a) => {
      if (a.id === id) fn(a);
    });
    saveStore(store);
    return store;
  }

  function tabFromUrl() {
    try {
      const q = new URLSearchParams(location.search).get("tab");
      if (q === "flows" || q === "actions") return q;
    } catch (_) {}
    return null;
  }

  function showTab(id, push) {
    const tab = id === "flows" ? "flows" : "actions";
    document.querySelectorAll(".ta-tab").forEach((t) => {
      t.classList.toggle("active", t.getAttribute("data-tab") === tab);
      t.setAttribute("aria-selected", t.getAttribute("data-tab") === tab ? "true" : "false");
    });
    const actions = document.getElementById("panel-actions");
    const flows = document.getElementById("panel-flows");
    if (actions) actions.hidden = tab !== "actions";
    if (flows) flows.hidden = tab !== "flows";
    if (push && typeof history !== "undefined") {
      const path = tab === "flows" ? "/action?tab=flows" : "/action";
      history.replaceState({ house: true }, "", path);
    }
  }

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function paintTriad(mount, triad, missing) {
    mount.replaceChildren();
    const box = el("div", "ta-triad");
    box.appendChild(el("p", "ta-triad-kicker", "Today’s Field triad"));
    if (!triad) {
      box.appendChild(el("p", "ta-sub", missing || "Today’s Field triad isn’t available yet."));
      mount.appendChild(box);
      return;
    }
    box.appendChild(el("p", "ta-triad-freq", triad.frequency || "—"));
    if (triad.principle) box.appendChild(el("p", "ta-triad-principle", triad.principle));
    if (triad.tuning_key) box.appendChild(el("p", "ta-triad-key", triad.tuning_key));
    mount.appendChild(box);
  }

  function closeRow(store, triad, drop, extraBtns) {
    const row = el("div", "ta-row");
    (extraBtns || []).forEach((b) => row.appendChild(b));
    const closeBtn = el("button", "ta-btn ta-btn-ghost", "Close");
    closeBtn.type = "button";
    closeBtn.addEventListener("click", () => {
      openId = null;
      paintAll(store, triad, drop);
    });
    row.appendChild(closeBtn);
    return row;
  }

  function paintWalkChoose(card, action, triad, drop) {
    card.appendChild(el("p", "ta-run-body", "Choose your Walk. Short lesson. Two forks. Same triad. Does not start a Tune."));
    const list = el("div", "ta-walk-list");
    WALKS.forEach((walk) => {
      const pick = el("article", "ta-walk-pick");
      pick.appendChild(el("h3", "", walk.title));
      pick.appendChild(el("p", "", walk.blurb));
      const btn = el("button", "ta-btn", "Start this Walk");
      btn.type = "button";
      btn.addEventListener("click", () => {
        const next = patchAction(action.id, (a) => {
          a.walk = { templateId: walk.id, node: "teach", path: null };
          a.done = false;
        });
        paintAll(next, triad, drop);
      });
      pick.appendChild(btn);
      list.appendChild(pick);
    });
    const pro = el("article", "ta-walk-pick ta-walk-pro");
    pro.appendChild(el("h3", "", "Personalized Walk"));
    pro.appendChild(
      el(
        "p",
        "",
        isPro()
          ? "Pro intelligence will write an enhanced walk from today’s triad. Templates are live now."
          : "Pro writes a personalized walk from today’s triad. Free keeps these templates.",
      ),
    );
    const proBtn = el("button", isPro() ? "ta-btn ta-btn-ghost" : "ta-btn", isPro() ? "Coming next" : "Upgrade");
    proBtn.type = "button";
    if (isPro()) {
      proBtn.disabled = true;
    } else {
      proBtn.addEventListener("click", openUpgrade);
    }
    pro.appendChild(proBtn);
    list.appendChild(pro);
    card.appendChild(list);
    card.appendChild(closeRow(loadStore(), triad, drop, []));
  }

  function paintWalkLesson(card, action, triad, drop, store) {
    const walk = walkById(action.walk && action.walk.templateId);
    if (!walk) {
      paintWalkChoose(card, action, triad, drop);
      return;
    }
    const node = (action.walk && action.walk.node) || "teach";
    card.appendChild(el("p", "ta-card-meta", walk.title));
    if (node === "teach") {
      card.appendChild(el("p", "ta-run-body", fill(walk.teach, triad)));
      const nextBtn = el("button", "ta-btn", "Continue");
      nextBtn.type = "button";
      nextBtn.addEventListener("click", () => {
        const next = patchAction(action.id, (a) => {
          a.walk = a.walk || defaultWalkState();
          a.walk.node = "choice";
        });
        paintAll(next, triad, drop);
      });
      card.appendChild(closeRow(store, triad, drop, [nextBtn]));
      return;
    }
    if (node === "choice") {
      card.appendChild(el("p", "ta-run-body", fill(walk.prompt, triad)));
      const choices = el("div", "ta-choice-list");
      (walk.options || []).forEach((opt) => {
        const btn = el("button", "ta-btn ta-btn-ghost ta-choice", opt.label);
        btn.type = "button";
        btn.addEventListener("click", () => {
          const next = patchAction(action.id, (a) => {
            a.walk = a.walk || defaultWalkState();
            a.walk.path = opt.id;
            a.walk.node = "landing";
          });
          paintAll(next, triad, drop);
        });
        choices.appendChild(btn);
      });
      card.appendChild(choices);
      card.appendChild(closeRow(store, triad, drop, []));
      return;
    }
    const picked = (walk.options || []).filter((o) => o.id === (action.walk && action.walk.path))[0];
    if (node === "landing") {
      if (picked) card.appendChild(el("p", "ta-run-body", fill(picked.landing, triad)));
      const nextBtn = el("button", "ta-btn", "Close the Walk");
      nextBtn.type = "button";
      nextBtn.addEventListener("click", () => {
        const next = patchAction(action.id, (a) => {
          a.walk = a.walk || defaultWalkState();
          a.walk.node = "close";
        });
        paintAll(next, triad, drop);
      });
      card.appendChild(closeRow(store, triad, drop, [nextBtn]));
      return;
    }
    card.appendChild(el("p", "ta-run-body", fill(walk.close, triad)));
    if (triad && triad.tuning_key) card.appendChild(el("p", "ta-run-key", triad.tuning_key));
    const doneBtn = el("button", "ta-btn", "Mark done");
    doneBtn.type = "button";
    doneBtn.addEventListener("click", () => {
      paintAll(markDone(action.id), triad, drop);
    });
    const again = el("button", "ta-btn ta-btn-ghost", "Choose another Walk");
    again.type = "button";
    again.addEventListener("click", () => {
      const next = patchAction(action.id, (a) => {
        a.walk = defaultWalkState();
        a.done = false;
      });
      paintAll(next, triad, drop);
    });
    card.appendChild(closeRow(store, triad, drop, action.done ? [again] : [doneBtn, again]));
  }

  function paintGratitudeRun(card, action, triad, drop, store) {
    card.appendChild(el("p", "ta-run-body", "Write one gratitude under today’s triad. Sit with the note."));
    const note = el("textarea", "ta-note");
    note.maxLength = 2000;
    note.placeholder = "What are you grateful for, under this Key?";
    note.value = action.body || "";
    card.appendChild(note);
    const extra = [];
    if (!action.done) {
      const saveBtn = el("button", "ta-btn", "Mark done");
      saveBtn.type = "button";
      saveBtn.addEventListener("click", () => {
        const body = String(note.value || "").trim();
        if (!body) {
          note.focus();
          return;
        }
        const next = patchAction(action.id, (a) => {
          a.body = body;
          a.done = true;
        });
        paintAll(next, triad, drop);
      });
      extra.push(saveBtn);
    }
    card.appendChild(closeRow(store, triad, drop, extra));
  }

  function paintActions(store, triad, drop) {
    const root = document.getElementById("ta-actions-list");
    if (!root) return;
    root.replaceChildren();
    if (!store.actions.length) {
      root.appendChild(
        el("div", "ta-empty", "Nothing to run today. Turn on a Flow — then come back here to run it."),
      );
      return;
    }
    const list = el("div", "ta-list");
    store.actions.forEach((action) => {
      const open = openId === action.id;
      const card = el("article", "ta-card" + (action.done ? " is-done" : "") + (open ? " is-open" : ""));
      card.appendChild(el("h2", "", action.title || "Action"));
      const meta = action.done ? "Done" : open ? "Running" : action.flow === FLOW_WALK ? "Ready — Choose your Walk" : "Ready to run";
      card.appendChild(el("p", "ta-card-meta", meta));
      if (open) {
        if (action.triad && action.triad.tuning_key && action.flow !== FLOW_WALK) {
          card.appendChild(el("p", "ta-run-key", action.triad.tuning_key));
        }
        if (action.flow === FLOW_WALK) {
          const walk = action.walk || defaultWalkState();
          if (!walk.templateId) paintWalkChoose(card, action, triad, drop);
          else paintWalkLesson(card, action, triad, drop, store);
        } else if (action.flow === FLOW_GRATITUDE) {
          paintGratitudeRun(card, action, triad, drop, store);
        } else {
          if (action.body) card.appendChild(el("p", "ta-run-body", action.body));
          const extra = [];
          if (!action.done) {
            const doneBtn = el("button", "ta-btn", "Mark done");
            doneBtn.type = "button";
            doneBtn.addEventListener("click", () => {
              paintAll(markDone(action.id), triad, drop);
            });
            extra.push(doneBtn);
          }
          card.appendChild(closeRow(store, triad, drop, extra));
        }
      } else {
        const runBtn = el("button", "ta-btn", action.done ? "Read again" : "Run");
        runBtn.type = "button";
        runBtn.addEventListener("click", () => {
          openId = action.id;
          paintAll(store, triad, drop);
        });
        card.appendChild(runBtn);
      }
      list.appendChild(card);
    });
    root.appendChild(list);
  }

  function paintFlowCard(list, spec, store, triad, drop) {
    const active = loadActive();
    const on = !!active[spec.id];
    const card = el("article", "ta-card" + (on ? " is-active" : ""));
    card.appendChild(el("h2", "", spec.title));
    card.appendChild(el("p", "", spec.blurb));
    const status = on
      ? triad
        ? "Active — Action is on today’s board."
        : "Active — waiting on today’s triad to mint."
      : "Off — no Action from this Flow today.";
    card.appendChild(el("p", "ta-card-meta", status));
    const btn = el("button", on ? "ta-btn ta-btn-ghost" : "ta-btn", on ? "Turn off" : "Turn on");
    btn.type = "button";
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    btn.addEventListener("click", () => {
      setFlowActive(spec.id, !on, triad, drop);
    });
    card.appendChild(btn);
    list.appendChild(card);
  }

  function paintFlows(store, triad, drop) {
    const root = document.getElementById("ta-flows-list");
    if (!root) return;
    root.replaceChildren();
    const list = el("div", "ta-list");
    paintFlowCard(
      list,
      {
        id: FLOW_WALK,
        title: "Walk the Lucid Path",
        blurb: "When this is on, today’s triad mints a Walk Action. Run it to Choose your Walk — a short forked lesson. Does not start a Tune.",
      },
      store,
      triad,
      drop,
    );
    paintFlowCard(
      list,
      {
        id: FLOW_GRATITUDE,
        title: "Gratitude",
        blurb: "When this is on, today’s triad mints a Gratitude Action. Write the note when you run it.",
      },
      store,
      triad,
      drop,
    );
    root.appendChild(list);
  }

  function paintAll(store, triad, drop) {
    window._taTriad = triad;
    window._taDrop = drop || null;
    const chrome = document.getElementById("ta-triad-chrome");
    if (chrome) paintTriad(chrome, triad);
    paintActions(store, triad, drop || window._taDrop);
    paintFlows(store, triad, drop || window._taDrop);
  }

  async function loadTunerAction() {
    let drop = null;
    if (typeof _tfFetchLatestDropTuning === "function") {
      try {
        drop = await _tfFetchLatestDropTuning();
      } catch (_) {
        drop = null;
      }
    }
    const triad = triadOf(drop);
    const store = mintActive(loadStore(), triad);
    const start = tabFromUrl() || (store.actions.length ? "actions" : "flows");
    showTab(start, false);
    paintAll(store, triad, drop);
  }

  window.loadTunerAction = loadTunerAction;

  document.querySelectorAll(".ta-tab").forEach((btn) => {
    btn.addEventListener("click", () => showTab(btn.getAttribute("data-tab"), true));
  });
})();
