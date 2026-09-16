/* Public Tuner Action. Flows create; Actions run. No Links, Tools, or Tune. */
(function () {
  const STORE = "lt-tuner-daily-actions-v1";
  const FLOW_PRACTICE = "todays-practice";
  const FLOW_GRATITUDE = "gratitude";
  let openId = null;

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

  function practiceSteps(drop) {
    if (!drop) return [];
    const objects = drop.practice_steps;
    if (Array.isArray(objects) && objects.length) {
      return objects
        .map((s) => {
          if (typeof s === "string") return s.trim();
          if (!s || typeof s !== "object") return "";
          const title = String(s.title || "").trim();
          const text = String(s.instruction || s.text || s.body || "").trim();
          if (title && text && title !== text && !/^\d+$/.test(title)) return title + " — " + text;
          return text || title;
        })
        .filter(Boolean);
    }
    const list = drop.universal_practice;
    if (Array.isArray(list) && list.length) {
      return list
        .map((s) => {
          if (typeof s === "string") return s.trim();
          if (!s || typeof s !== "object") return "";
          return String(s.instruction || s.text || s.title || s.body || "").trim();
        })
        .filter(Boolean);
    }
    return [];
  }

  function actionSteps(action) {
    if (action && Array.isArray(action.steps) && action.steps.length) {
      return action.steps.map((s) => String(s || "").trim()).filter(Boolean);
    }
    const body = String((action && action.body) || "").trim();
    return body ? body.split("\n").map((s) => s.trim()).filter(Boolean) : [];
  }

  function hasFlow(store, flow) {
    return store.actions.some((a) => a.flow === flow);
  }

  function addAction(flow, title, body, triad, steps) {
    const store = loadStore();
    if (hasFlow(store, flow)) return store;
    store.actions.push({
      id: uid(),
      flow,
      title,
      body: String(body || ""),
      steps: Array.isArray(steps) ? steps.slice() : [],
      triad: triad || null,
      done: false,
      created_at: Date.now(),
    });
    saveStore(store);
    return store;
  }

  function markDone(id) {
    const store = loadStore();
    store.actions.forEach((a) => {
      if (a.id === id) a.done = true;
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

  function paintStepList(steps) {
    const ol = el("ol", "ta-steps");
    steps.forEach((step) => {
      ol.appendChild(el("li", "", step));
    });
    return ol;
  }

  function paintActions(store, triad, drop) {
    const root = document.getElementById("ta-actions-list");
    if (!root) return;
    root.replaceChildren();
    if (!store.actions.length) {
      root.appendChild(
        el("div", "ta-empty", "Nothing to run today. Create from Flows — then come back here to run it."),
      );
      return;
    }
    const list = el("div", "ta-list");
    store.actions.forEach((action) => {
      const open = openId === action.id;
      const card = el("article", "ta-card" + (action.done ? " is-done" : "") + (open ? " is-open" : ""));
      card.appendChild(el("h2", "", action.title || "Action"));
      card.appendChild(el("p", "ta-card-meta", action.done ? "Done" : open ? "Running" : "Ready to run"));
      if (open) {
        if (action.triad && action.triad.tuning_key) {
          card.appendChild(el("p", "ta-run-key", action.triad.tuning_key));
        }
        const steps = actionSteps(action);
        if (action.flow === FLOW_PRACTICE && steps.length) {
          card.appendChild(paintStepList(steps));
        } else if (action.body) {
          card.appendChild(el("p", "ta-run-body", action.body));
        }
        const row = el("div", "ta-row");
        if (!action.done) {
          const doneBtn = el("button", "ta-btn", "Mark done");
          doneBtn.type = "button";
          doneBtn.addEventListener("click", () => {
            paintAll(markDone(action.id), triad, drop);
          });
          row.appendChild(doneBtn);
        }
        const closeBtn = el("button", "ta-btn ta-btn-ghost", "Close");
        closeBtn.type = "button";
        closeBtn.addEventListener("click", () => {
          openId = null;
          paintAll(store, triad, drop);
        });
        row.appendChild(closeBtn);
        card.appendChild(row);
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

  function paintFlows(store, triad, drop) {
    const root = document.getElementById("ta-flows-list");
    if (!root) return;
    root.replaceChildren();
    const list = el("div", "ta-list");
    const steps = practiceSteps(drop);

    const practiceCard = el("article", "ta-card");
    practiceCard.appendChild(el("h2", "", "Today’s practice"));
    practiceCard.appendChild(
      el("p", "", "Create a daily Action from the Field practice, bound to today’s triad. Does not start a Tune."),
    );
    if (steps.length) {
      practiceCard.appendChild(paintStepList(steps));
    } else {
      practiceCard.appendChild(el("p", "ta-card-meta", "Practice fills from today’s Drop when the triad is in."));
    }
    const practiceExists = hasFlow(store, FLOW_PRACTICE);
    const practiceBtn = el("button", "ta-btn", practiceExists ? "Already on Actions" : "Create Action");
    practiceBtn.type = "button";
    practiceBtn.disabled = practiceExists || !triad;
    practiceBtn.addEventListener("click", () => {
      if (!triad || hasFlow(loadStore(), FLOW_PRACTICE)) return;
      const nextSteps = practiceSteps(drop);
      const body = nextSteps.join("\n") || "Run today’s practice under this triad.";
      openId = null;
      paintAll(addAction(FLOW_PRACTICE, "Today’s practice", body, triad, nextSteps), triad, drop);
      showTab("actions", true);
    });
    practiceCard.appendChild(practiceBtn);
    list.appendChild(practiceCard);

    const gratitudeCard = el("article", "ta-card");
    gratitudeCard.appendChild(el("h2", "", "Gratitude"));
    gratitudeCard.appendChild(
      el("p", "", "Write one gratitude under today’s triad. Creating it puts it on Actions to run."),
    );
    const note = el("textarea", "ta-note");
    note.id = "ta-gratitude-note";
    note.maxLength = 2000;
    note.placeholder = "What are you grateful for, under this Key?";
    if (hasFlow(store, FLOW_GRATITUDE)) {
      const existing = store.actions.find((a) => a.flow === FLOW_GRATITUDE);
      if (existing && existing.body) note.value = existing.body;
      note.disabled = true;
    }
    gratitudeCard.appendChild(note);
    const gratitudeExists = hasFlow(store, FLOW_GRATITUDE);
    const gratitudeBtn = el("button", "ta-btn", gratitudeExists ? "Already on Actions" : "Create Action");
    gratitudeBtn.type = "button";
    gratitudeBtn.disabled = gratitudeExists || !triad;
    gratitudeBtn.addEventListener("click", () => {
      if (!triad || hasFlow(loadStore(), FLOW_GRATITUDE)) return;
      const body = String(note.value || "").trim();
      if (!body) {
        note.focus();
        return;
      }
      openId = null;
      paintAll(addAction(FLOW_GRATITUDE, "Gratitude", body, triad, [body]), triad, drop);
      showTab("actions", true);
    });
    gratitudeCard.appendChild(gratitudeBtn);
    list.appendChild(gratitudeCard);

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
    const store = loadStore();
    const start = tabFromUrl() || (store.actions.length ? "actions" : "flows");
    showTab(start, false);
    let drop = null;
    if (typeof _tfFetchLatestDropTuning === "function") {
      try {
        drop = await _tfFetchLatestDropTuning();
      } catch (_) {
        drop = null;
      }
    }
    paintAll(store, triadOf(drop), drop);
  }

  window.loadTunerAction = loadTunerAction;

  document.querySelectorAll(".ta-tab").forEach((btn) => {
    btn.addEventListener("click", () => showTab(btn.getAttribute("data-tab"), true));
  });
})();
