/**
 * wizard-engine.js — Reusable step wizard for Cove tools.
 *
 * Each tool defines steps. The engine renders them one at a time,
 * tracks dependencies, collects data, and creates Actions for deferred work.
 *
 * Usage:
 *   const wizard = new WizardEngine({
 *     container: document.getElementById('wizard'),
 *     toolName: 'Site Builder',
 *     agentTag: 'ARCHIMEDES',
 *     steps: [ { id, title, description, type, depends_on, ... } ],
 *     renderers: { 'step-id': (step, data, engine) => html },
 *     onFinish: (summary) => { ... },
 *   });
 *   wizard.start();
 */

class WizardEngine {
    constructor(config) {
        this.container = config.container;
        this.toolName = config.toolName || 'Tool';
        this.agentTag = config.agentTag || '';
        this.steps = config.steps;
        this.renderers = config.renderers || {};
        this.onFinish = config.onFinish || (() => {});

        // State — three-state model: completed / pending / blocked
        // No "deferred" or "skipped" — you either did it or it's still pending.
        this.data = config.initialData || {};
        this.completed = new Set();
        this.currentIndex = 0;

        // Context label — what are we working on (shown above progress bar)
        this.contextLabel = config.contextLabel || '';

        // Persistence — save/restore wizard progress
        this.saveUrl = config.saveUrl || null;
        this.autoSave = config.autoSave !== false;

        // Expose for onclick handlers in rendered HTML
        window._wz = this;

        // Inject CSS once
        if (!document.getElementById('wz-styles')) {
            const style = document.createElement('style');
            style.id = 'wz-styles';
            style.textContent = WizardEngine.CSS;
            document.head.appendChild(style);
        }
    }

    // ── Public API ────────────────────────────────────────────────

    // ── Public API ────────────────────────────────────────────────

    /** Start the wizard. Optional fromStepId to jump directly to a step. */
    start(fromStepId) {
        if (fromStepId) {
            const idx = this.steps.findIndex(s => s.id === fromStepId);
            if (idx >= 0) {
                this.currentIndex = idx;
                this._directNav = true;  // show this step even if deps unmet
            }
        }
        this._renderCurrent();
    }

    /** Mark current step complete with collected data. */
    complete(stepId, stepData = {}) {
        this.completed.add(stepId);
        this.data[stepId] = stepData;
        Object.assign(this.data, stepData);
        this._advanceToNextPending();
        if (this.autoSave) this.save();
        this._renderCurrent();
    }

    /** Skip current step — leave it pending for next time. */
    skip(stepId) {
        this._advanceToNextPending();
        if (this.autoSave) this.save();
        this._renderCurrent();
    }

    /** Restore wizard state from a saved snapshot. Call before start(). */
    restore(state) {
        if (!state) return;
        if (state.data) Object.assign(this.data, state.data);
        if (state.completed) state.completed.forEach(id => this.completed.add(id));
        // Advance past completed steps to first pending
        this._advanceToNextPending();
    }

    /** Get a serializable snapshot for persistence. */
    getState() {
        return {
            completed: [...this.completed],
            data: this.data,
            currentStep: this.steps[this.currentIndex]?.id || null,
        };
    }

    /** Save wizard state to the backend (if saveUrl configured). */
    async save() {
        if (!this.saveUrl) return;
        try {
            await fetch(this.saveUrl, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ wizard_state: this.getState() }),
            });
        } catch (e) {
            console.warn('[wizard] Save failed:', e.message);
        }
    }

    /** Get all collected data. */
    getData() { return { ...this.data }; }

    /** Get data from a specific completed step. */
    getStepData(stepId) { return this.data[stepId] || null; }

    /** Check if a step is completed. */
    isCompleted(stepId) { return this.completed.has(stepId); }

    /** Check if a step is blocked (dependencies not met). */
    isBlocked(stepId) {
        const step = this.steps.find(s => s.id === stepId);
        if (!step || !step.depends_on) return false;
        return step.depends_on.some(d => !this.completed.has(d));
    }

    /** Check if a step is pending (not completed, not blocked). */
    isPending(stepId) {
        return !this.isCompleted(stepId) && !this.isBlocked(stepId);
    }

    /** Advance currentIndex to next non-completed step. */
    _advanceToNextPending() {
        this.currentIndex++;
        while (this.currentIndex < this.steps.length) {
            const step = this.steps[this.currentIndex];
            if (!this.completed.has(step.id)) break;
            this.currentIndex++;
        }
    }

    // ── Flow control ──────────────────────────────────────────────

    _renderCurrent() {
        if (this.currentIndex >= this.steps.length) {
            // Check if there are still pending (non-completed) steps
            const pending = this.steps.filter(s => !this.completed.has(s.id));
            if (pending.length > 0) {
                // There are still steps to do — show summary with option to continue
                this._renderSummary(true);
            } else {
                this._renderSummary(false);
            }
            return;
        }

        const step = this.steps[this.currentIndex];

        // If step is blocked and this isn't a direct navigation, skip to next
        if (this.isBlocked(step.id) && !this._directNav) {
            this.currentIndex++;
            this._renderCurrent();
            return;
        }

        this._directNav = false;
        this._renderStep(step);
        // Save state on every step render — if user leaves, state is persisted
        if (this.autoSave) this.save();
    }

    // ── Rendering ─────────────────────────────────────────────────

    _renderStep(step) {
        const progress = this._progressBar();
        // Context label updates dynamically (e.g. domain name becomes known mid-wizard)
        const ctxLabel = this.contextLabel || this.data.domain || '';
        const contextBanner = ctxLabel
            ? `<div class="wz-context-label">${_esc(ctxLabel)}</div>`
            : '';
        const header = `
            <div class="wz-step-header">
                ${contextBanner}
                <div class="wz-step-count">Step ${this.currentIndex + 1} of ${this.steps.length}</div>
                <h2 class="wz-step-title">${_esc(step.title)}</h2>
                ${step.description ? `<p class="wz-step-desc">${_esc(step.description)}</p>` : ''}
            </div>
        `;

        // Dependency/context notes
        let contextNote = '';
        // Blocked step opened via direct nav — show what's needed
        if (this.isBlocked(step.id)) {
            const unmet = (step.depends_on || []).filter(d => !this.completed.has(d));
            const names = unmet.map(id => {
                const s = this.steps.find(x => x.id === id);
                return s ? s.title : id;
            });
            contextNote = `<div class="wz-context-note wz-blocked-note">Needs ${names.join(' and ')} first. You can still fill this in, but it won't be saved until dependencies are done.</div>`;
        }
        // Benefits_from — nice to have but not blocking
        else if (step.benefits_from && step.benefits_from.length) {
            const missing = step.benefits_from.filter(id => !this.completed.has(id));
            if (missing.length > 0) {
                const names = missing.map(id => {
                    const s = this.steps.find(x => x.id === id);
                    return s ? s.title : id;
                });
                contextNote = `<div class="wz-context-note">Works best with ${names.join(' and ')} — not done yet, but you can still proceed.</div>`;
            }
        }

        // Step body
        let body = '';
        if (this.renderers[step.id]) {
            // Custom renderer — tool provides it
            body = this.renderers[step.id](step, this.data, this);
        } else {
            // Built-in type renderers
            switch (step.type) {
                case 'choice':  body = this._renderChoice(step); break;
                case 'chips':   body = this._renderChips(step); break;
                case 'form':    body = this._renderForm(step); break;
                case 'input':   body = this._renderInput(step); break;
                case 'auto':    body = this._renderAuto(step); break;
                case 'info':    body = this._renderInfo(step); break;
                default:        body = `<div class="wz-note">Step type "${step.type}" needs a custom renderer.</div>`;
            }
        }

        this.container.innerHTML = progress + header + contextNote + '<div class="wz-step-body">' + body + '</div>';

        // Auto steps execute immediately
        if (step.type === 'auto' && !this.renderers[step.id]) {
            this._executeAuto(step);
        }
    }

    // ── Built-in type: choice cards ───────────────────────────────

    _renderChoice(step) {
        const cards = (step.options || []).map(opt => `
            <div class="wz-choice-card" data-value="${_esc(opt.value)}"
                 onclick="_wz._selectChoice(this, '${_esc(step.id)}', '${_esc(opt.value)}')">
                ${opt.icon ? `<div class="wz-choice-icon">${opt.icon}</div>` : ''}
                <div class="wz-choice-label">${_esc(opt.label)}</div>
                ${opt.desc ? `<div class="wz-choice-desc">${_esc(opt.desc)}</div>` : ''}
            </div>
        `).join('');

        const deferBtn = step.deferrable !== false
            ? `<button class="wz-btn wz-btn-ghost" onclick="_wz.skip('${step.id}')">Not now</button>`
            : '';

        return `
            <div class="wz-choices">${cards}</div>
            <div class="wz-actions">
                <button class="wz-btn wz-btn-primary" id="wz-next" disabled
                    onclick="_wz._choiceNext('${step.id}', '${step.dataKey || step.id}')">Next →</button>
                ${deferBtn}
            </div>
        `;
    }

    _selectChoice(el, stepId, value) {
        el.closest('.wz-choices').querySelectorAll('.wz-choice-card').forEach(c => c.classList.remove('selected'));
        el.classList.add('selected');
        this._selectedValue = value;
        const btn = document.getElementById('wz-next');
        if (btn) btn.disabled = false;
    }

    _choiceNext(stepId, dataKey) {
        if (!this._selectedValue) return;
        this.complete(stepId, { [dataKey]: this._selectedValue });
        this._selectedValue = null;
    }

    // ── Built-in type: chips (multi or single select) ──────────────
    //
    // Step config:
    //   type: 'chips'
    //   chips: [{ value, label, icon?, desc? }]    — the options
    //   multiSelect: true|false (default true)     — multi or single select
    //   dataKey: 'pages'                           — key for collected values
    //   defaults: ['Home','About']                 — pre-selected values
    //   defaultsMap: { business: [...], personal: [...] }  — defaults by context key
    //   defaultsFrom: 'site_type'                  — which data key selects the defaults
    //   customInput: { key, label, placeholder }   — optional "add your own" field
    //   fields: [...]                              — optional extra form fields below chips

    _renderChips(step) {
        // Resolve defaults
        let defaults = step.defaults || [];
        if (step.defaultsMap && step.defaultsFrom) {
            const ctx = this.data[step.defaultsFrom] || '';
            defaults = step.defaultsMap[ctx] || defaults;
        }

        const multi = step.multiSelect !== false;
        const existing = this.data[step.dataKey || step.id];
        const selected = new Set(
            existing ? (Array.isArray(existing) ? existing : existing.split(',').map(s=>s.trim())) : defaults
        );

        const chipsHtml = (step.chips || []).map(c => {
            const val = typeof c === 'string' ? c : c.value;
            const label = typeof c === 'string' ? c : (c.label || c.value);
            const icon = (typeof c === 'object' && c.icon) ? `<span class="wz-chip-icon">${c.icon}</span>` : '';
            const desc = (typeof c === 'object' && c.desc) ? `<br><span class="wz-chip-desc">${_esc(c.desc)}</span>` : '';
            const sel = selected.has(val) ? ' selected' : '';
            return `<span class="wz-chip${sel}" data-value="${_esc(val)}" onclick="_wz._toggleChip(this, ${multi})">${icon}${_esc(label)}${desc}</span>`;
        }).join('');

        // Custom input field
        let customHtml = '';
        if (step.customInput) {
            const ci = step.customInput;
            const val = this.data[ci.key] || '';
            customHtml = `
                <div class="wz-field" style="margin-top:10px;">
                    <label>${_esc(ci.label || 'Custom')}</label>
                    <input type="text" id="wz-chip-custom" placeholder="${_esc(ci.placeholder || '')}" value="${_esc(val)}">
                </div>`;
        }

        // Extra form fields
        let fieldsHtml = '';
        if (step.fields && step.fields.length) {
            fieldsHtml = step.fields.map(f => {
                const id = `wz-cf-${f.key}`;
                const val = this.data[f.key] || '';
                let input = '';
                if (f.type === 'textarea') {
                    input = `<textarea id="${id}" rows="${f.rows || 2}" placeholder="${_esc(f.placeholder || '')}">${_esc(val)}</textarea>`;
                } else {
                    input = `<input type="${f.type || 'text'}" id="${id}" placeholder="${_esc(f.placeholder || '')}" value="${_esc(val)}">`;
                }
                return `<div class="wz-field">${f.label ? `<label for="${id}">${_esc(f.label)}</label>` : ''}${input}${f.hint ? `<div class="wz-hint">${_esc(f.hint)}</div>` : ''}</div>`;
            }).join('');
        }

        const skipBtn = step.deferrable !== false
            ? `<button class="wz-btn wz-btn-ghost" onclick="_wz.skip('${step.id}')">Not now</button>`
            : '';

        return `
            <div class="wz-chip-grid" id="wz-chips">${chipsHtml}</div>
            ${customHtml}
            ${fieldsHtml}
            <div class="wz-actions">
                <button class="wz-btn wz-btn-primary" onclick="_wz._chipsNext('${step.id}', '${step.dataKey || step.id}')">Next →</button>
                ${skipBtn}
            </div>
        `;
    }

    _toggleChip(el, multi) {
        if (multi) {
            el.classList.toggle('selected');
        } else {
            el.closest('.wz-chip-grid').querySelectorAll('.wz-chip').forEach(c => c.classList.remove('selected'));
            el.classList.add('selected');
        }
    }

    _chipsNext(stepId, dataKey) {
        const step = this.steps.find(s => s.id === stepId);
        const chips = document.querySelectorAll('#wz-chips .wz-chip.selected');
        const values = Array.from(chips).map(c => c.dataset.value);

        // Add custom input values
        const customEl = document.getElementById('wz-chip-custom');
        if (customEl && customEl.value.trim()) {
            const extras = customEl.value.split(',').map(s => s.trim()).filter(Boolean);
            values.push(...extras);
        }

        const data = { [dataKey]: step.multiSelect === false ? values[0] || '' : values };

        // Collect extra form fields
        if (step.fields) {
            step.fields.forEach(f => {
                const el = document.getElementById(`wz-cf-${f.key}`);
                if (el) data[f.key] = el.value.trim();
            });
        }

        // Custom input key
        if (step.customInput) {
            const ci = step.customInput;
            data[ci.key] = customEl?.value?.trim() || '';
        }

        this.complete(stepId, data);
    }

    // ── Built-in type: form (multiple fields) ─────────────────────

    _renderForm(step) {
        const fields = (step.fields || []).map(f => {
            const id = `wz-f-${f.key}`;
            const val = this.data[f.key] || '';
            let input = '';
            if (f.type === 'textarea') {
                input = `<textarea id="${id}" rows="${f.rows || 3}" placeholder="${_esc(f.placeholder || '')}">${_esc(val)}</textarea>`;
            } else {
                input = `<input type="${f.type || 'text'}" id="${id}" placeholder="${_esc(f.placeholder || '')}" value="${_esc(val)}">`;
            }
            return `
                <div class="wz-field">
                    <label for="${id}">${_esc(f.label)}</label>
                    ${input}
                    ${f.hint ? `<div class="wz-hint">${_esc(f.hint)}</div>` : ''}
                </div>
            `;
        }).join('');

        const deferBtn = step.deferrable !== false
            ? `<button class="wz-btn wz-btn-ghost" onclick="_wz.skip('${step.id}')">Not now</button>`
            : '';

        return `
            ${fields}
            <div class="wz-actions">
                <button class="wz-btn wz-btn-primary"
                    onclick="_wz._formNext('${step.id}')">Next →</button>
                ${deferBtn}
            </div>
        `;
    }

    _formNext(stepId) {
        const step = this.steps.find(s => s.id === stepId);
        const data = {};
        (step.fields || []).forEach(f => {
            const el = document.getElementById(`wz-f-${f.key}`);
            if (el) data[f.key] = el.value.trim();
        });
        this.complete(stepId, data);
    }

    // ── Built-in type: single input ───────────────────────────────

    _renderInput(step) {
        const f = step.field || {};
        const id = 'wz-input';
        const val = this.data[f.key || step.id] || '';

        const deferBtn = step.deferrable !== false
            ? `<button class="wz-btn wz-btn-ghost" onclick="_wz.skip('${step.id}')">Not now</button>`
            : '';

        return `
            <div class="wz-field">
                <label for="${id}">${_esc(f.label || step.title)}</label>
                <input type="${f.type || 'text'}" id="${id}" placeholder="${_esc(f.placeholder || '')}"
                       value="${_esc(val)}" oninput="_wz._validateInput('${step.id}')">
                ${f.hint ? `<div class="wz-hint">${_esc(f.hint)}</div>` : ''}
            </div>
            <div class="wz-actions">
                <button class="wz-btn wz-btn-primary" id="wz-next" disabled
                    onclick="_wz._inputNext('${step.id}', '${f.key || step.id}')">Next →</button>
                ${deferBtn}
            </div>
        `;
    }

    _validateInput(stepId) {
        const step = this.steps.find(s => s.id === stepId);
        const el = document.getElementById('wz-input');
        const val = (el?.value || '').trim();
        const valid = step.field?.validate ? step.field.validate(val) : val.length > 0;
        const btn = document.getElementById('wz-next');
        if (btn) btn.disabled = !valid;
    }

    _inputNext(stepId, dataKey) {
        const el = document.getElementById('wz-input');
        const val = (el?.value || '').trim();
        this.complete(stepId, { [dataKey]: val });
    }

    // ── Built-in type: auto (runs a function) ─────────────────────

    _renderAuto(step) {
        return `
            <div class="wz-auto-status" id="wz-auto-status">
                <div class="wz-processing">
                    <div class="wz-dots"><span></span><span></span><span></span></div>
                    <span>${_esc(step.processingText || 'Working...')}</span>
                </div>
            </div>
        `;
    }

    async _executeAuto(step) {
        if (step.action) {
            try {
                const result = await step.action(this.data, this);
                const el = document.getElementById('wz-auto-status');
                if (el) {
                    el.innerHTML = `<div class="wz-auto-done">✓ ${_esc(step.doneText || 'Done')}</div>`;
                }
                setTimeout(() => this.complete(step.id, result || {}), 800);
            } catch (e) {
                const el = document.getElementById('wz-auto-status');
                if (el) {
                    el.innerHTML = `<div class="wz-auto-fail">✗ ${_esc(e.message || 'Failed')}</div>`;
                }
            }
        } else {
            // No action — just mark complete
            setTimeout(() => this.complete(step.id, {}), 500);
        }
    }

    // ── Built-in type: info (read-only, just proceed) ─────────────

    _renderInfo(step) {
        return `
            ${step.content || ''}
            <div class="wz-actions">
                <button class="wz-btn wz-btn-primary"
                    onclick="_wz.complete('${step.id}')">Continue →</button>
            </div>
        `;
    }

    // ── Progress bar ──────────────────────────────────────────────

    _progressBar() {
        const dots = this.steps.map((step, i) => {
            let cls = 'wz-dot';
            if (this.completed.has(step.id)) cls += ' done';
            else if (this.isBlocked(step.id)) cls += ' blocked';
            else if (i === this.currentIndex) cls += ' current';
            else cls += ' pending';
            return `<div class="${cls}" title="${_esc(step.title)}"></div>`;
        }).join('');
        return `<div class="wz-progress">${dots}</div>`;
    }

    // ── Summary ───────────────────────────────────────────────────

    _renderSummary(hasPending) {
        const completed = this.steps.filter(s => this.completed.has(s.id));
        const pending = this.steps.filter(s => !this.completed.has(s.id) && !this.isBlocked(s.id));
        const blocked = this.steps.filter(s => this.isBlocked(s.id) && !this.completed.has(s.id));

        const completedHTML = completed.map(s =>
            `<div class="wz-summary-item done"><span class="wz-summary-dot done"></span>${_esc(s.title)}</div>`
        ).join('');

        const pendingHTML = pending.map(s =>
            `<div class="wz-summary-item pending"><span class="wz-summary-dot pending"></span>${_esc(s.title)}</div>`
        ).join('');

        const blockedHTML = blocked.map(s => {
            const deps = (s.depends_on || []).filter(d => !this.completed.has(d));
            const depNames = deps.map(d => { const x = this.steps.find(y => y.id === d); return x ? x.title : d; });
            return `<div class="wz-summary-item blocked"><span class="wz-summary-dot blocked"></span>${_esc(s.title)} <span class="wz-blocked-dep">needs ${depNames.join(', ')}</span></div>`;
        }).join('');

        const progress = this._progressBar();
        const ctxLabel = this.contextLabel || this.data.domain || '';
        const contextBanner = ctxLabel ? `<div class="wz-context-label">${_esc(ctxLabel)}</div>` : '';

        this.container.innerHTML = `
            ${progress}
            <div class="wz-summary">
                ${contextBanner}
                <h2 class="wz-step-title">Setup Summary</h2>
                <p class="wz-step-desc">${completed.length} of ${this.steps.length} completed.${hasPending ? ` ${pending.length + blocked.length} remaining.` : ''}</p>

                ${completedHTML ? `<div class="wz-summary-section"><div class="wz-summary-label">Completed</div>${completedHTML}</div>` : ''}
                ${pendingHTML ? `<div class="wz-summary-section"><div class="wz-summary-label">Still Needed</div>${pendingHTML}</div>` : ''}
                ${blockedHTML ? `<div class="wz-summary-section"><div class="wz-summary-label">Blocked</div>${blockedHTML}</div>` : ''}
            </div>
            <div class="wz-actions" style="margin-top:20px;">
                <button class="wz-btn wz-btn-primary" onclick="_wz._finish()">Done for now</button>
            </div>
        `;
    }

    _finish() {
        const pending = this.steps.filter(s => !this.completed.has(s.id));
        const summary = {
            completed: [...this.completed],
            deferred: pending.map(s => s.id),  // everything not completed = still pending
            data: this.data,
        };
        this.onFinish(summary);
    }

    // ── Action creation ───────────────────────────────────────────

    async _createAction(step, reason) {
        // Build context for the action
        const contextData = {};
        // Gather data from steps this one benefits from
        (step.benefits_from || []).forEach(id => {
            if (this.data[id]) contextData[id] = this.data[id];
        });
        // Add all collected data for full context
        Object.assign(contextData, this.data);

        const actionTitle = `${step.title} — ${this.toolName}`;
        const actionDesc = reason
            ? `${step.description || step.title}. ${reason}`
            : (step.description || step.title);

        const body = {
            title: actionTitle,
            description: actionDesc,
            source: 'wizard',
            notes: JSON.stringify({
                tool: this.toolName,
                step_id: step.id,
                context: contextData,
            }),
        };

        try {
            const res = await fetch('/api/action-board/tasks', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            if (res.ok) {
                const data = await res.json();
                this.createdActions.push({
                    stepId: step.id,
                    title: actionTitle,
                    actionId: data.id,
                });
            }
        } catch (e) {
            // Still track it even if API fails
            this.createdActions.push({
                stepId: step.id,
                title: actionTitle,
                actionId: null,
            });
        }
    }
}

// ── Utility ───────────────────────────────────────────────────────

function _esc(s) {
    if (typeof s !== 'string') return '';
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
}

// ── Embedded CSS ──────────────────────────────────────────────────

WizardEngine.CSS = `
/* ═══ Wizard Engine Styles ════════════════════════════════════════ */

.wz-progress {
    display: flex; gap: 6px; margin-bottom: 20px; justify-content: center;
}
.wz-dot {
    width: 10px; height: 10px; border-radius: 50%; background: #2a2d3a;
    transition: all 0.3s;
}
.wz-dot.done { background: #4caf50; }
.wz-dot.current { background: var(--accent, #5b9bd5); box-shadow: 0 0 8px var(--accent-glow, rgba(91,155,213,0.4)); }
.wz-dot.pending { background: #555; }
.wz-dot.blocked { background: #e6a23c; opacity: 0.5; }
.wz-blocked-note { background: rgba(230,162,60,0.1); border: 1px solid rgba(230,162,60,0.3); border-radius: 6px; padding: 8px 12px; font-size: 12px; color: #e6a23c; margin-bottom: 12px; }
.wz-blocked-dep { font-size: 10px; color: #888; font-style: italic; }

/* ── Chip selector ── */
.wz-chip-grid { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 8px; }
.wz-chip {
    padding: 6px 14px; border-radius: 6px; font-size: 12px; font-weight: 500;
    background: #12141c; border: 1px solid #2a2d3a; color: #aaa;
    cursor: pointer; transition: all 0.15s; user-select: none; text-align: center;
}
.wz-chip:hover { border-color: var(--accent, #5b9bd5); color: #d8d8e0; }
.wz-chip.selected { background: rgba(91,155,213,0.12); border-color: var(--accent, #5b9bd5); color: var(--accent, #5b9bd5); }
.wz-chip-icon { margin-right: 4px; }
.wz-chip-desc { font-size: 10px; opacity: 0.6; }

.wz-context-label { font-size: 13px; font-weight: 600; color: var(--accent, #5b9bd5); margin-bottom: 6px; }
.wz-step-header { margin-bottom: 20px; }
.wz-step-count { font-size: 11px; color: #555; text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 4px; }
.wz-step-title { font-size: 18px; font-weight: 700; color: #e1e4ea; margin: 0 0 4px 0; }
.wz-step-desc { font-size: 13px; color: #888; line-height: 1.5; margin: 0; }

.wz-step-body { animation: wzFadeIn 0.3s ease; }
@keyframes wzFadeIn { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }

.wz-context-note {
    font-size: 12px; color: #e6a23c; background: rgba(230,162,60,0.08);
    border: 1px solid rgba(230,162,60,0.2); border-radius: 6px;
    padding: 8px 12px; margin-bottom: 16px;
}

/* ── Choices ── */
.wz-choices { display: flex; gap: 10px; margin-bottom: 20px; flex-wrap: wrap; }
.wz-choice-card {
    flex: 1; min-width: 120px; background: #1a1d27; border: 1px solid #2a2d3a;
    border-radius: 8px; padding: 14px 12px; cursor: pointer; transition: all 0.15s; text-align: center;
}
.wz-choice-card:hover { border-color: var(--accent, #5b9bd5); }
.wz-choice-card.selected { border-color: var(--accent, #5b9bd5); background: var(--accent-subtle, rgba(91,155,213,0.1)); }
.wz-choice-icon { font-size: 22px; margin-bottom: 4px; }
.wz-choice-label { font-size: 13px; font-weight: 600; margin-bottom: 2px; }
.wz-choice-desc { font-size: 11px; color: #666; line-height: 1.3; }

/* ── Form fields ── */
.wz-field { margin-bottom: 14px; }
.wz-field label {
    display: block; font-size: 11px; font-weight: 600; text-transform: uppercase;
    letter-spacing: 0.05em; color: #888; margin-bottom: 6px;
}
.wz-field input, .wz-field select, .wz-field textarea {
    width: 100%; background: #1a1d27; border: 1px solid #2a2d3a; border-radius: 6px;
    padding: 10px 12px; color: #e1e4ea; font-size: 14px; font-family: inherit;
    outline: none; transition: border-color 0.15s;
}
.wz-field input:focus, .wz-field textarea:focus { border-color: var(--accent, #5b9bd5); }
.wz-field textarea { resize: vertical; min-height: 60px; }
.wz-hint { font-size: 11px; color: #555; margin-top: 4px; }

/* ── Buttons ── */
.wz-actions { display: flex; gap: 10px; margin-top: 16px; flex-wrap: wrap; align-items: center; }
.wz-btn {
    display: inline-block; padding: 10px 20px; border-radius: 6px;
    font-size: 13px; font-weight: 600; cursor: pointer; transition: all 0.15s;
    font-family: inherit; border: none;
}
.wz-btn-primary { background: var(--accent, #5b9bd5); color: #0a0a0f; }
.wz-btn-primary:hover { opacity: 0.85; }
.wz-btn-primary:disabled { opacity: 0.3; cursor: default; }
.wz-btn-ghost { background: transparent; border: 1px solid #2a2d3a; color: #888; }
.wz-btn-ghost:hover { border-color: var(--accent, #5b9bd5); color: #d8d8e0; }
.wz-btn-sm { padding: 5px 12px; font-size: 11px; }

/* ── Auto step ── */
.wz-processing { display: flex; align-items: center; gap: 10px; padding: 14px 0; }
.wz-dots { display: flex; gap: 4px; }
.wz-dots span {
    width: 6px; height: 6px; border-radius: 50%;
    background: var(--accent, #5b9bd5); opacity: 0.3; animation: wzPulse 1.2s infinite;
}
.wz-dots span:nth-child(2) { animation-delay: 0.2s; }
.wz-dots span:nth-child(3) { animation-delay: 0.4s; }
@keyframes wzPulse { 0%,80%,100% { opacity:0.3; } 40% { opacity:1; } }
.wz-auto-done { color: #4caf50; font-size: 14px; font-weight: 600; padding: 14px 0; }
.wz-auto-fail { color: #ef5350; font-size: 14px; font-weight: 600; padding: 14px 0; }

/* ── Summary ── */
.wz-summary { background: #12141c; border: 1px solid #1e2030; border-radius: 10px; padding: 20px; }
.wz-summary-section { margin-top: 16px; }
.wz-summary-label { font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; color: #555; margin-bottom: 8px; }
.wz-summary-item { display: flex; align-items: center; gap: 8px; font-size: 13px; padding: 4px 0; }
.wz-summary-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.wz-summary-dot.done { background: #4caf50; }
.wz-summary-dot.deferred { background: #e6a23c; }

.wz-note { font-size: 13px; color: #666; padding: 8px 0; }

/* ── Agent response (for custom renderers) ── */
.wz-agent { margin: 12px 0; padding: 14px; background: #12141c; border-radius: 8px; border-left: 3px solid var(--accent, #5b9bd5); }
.wz-agent-label { font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.1em; color: var(--accent, #5b9bd5); margin-bottom: 6px; }
.wz-agent-text { font-size: 13px; line-height: 1.6; color: #c0c0cc; }

/* ── Toast ── */
.wz-toast {
    position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%);
    background: #1a1d27; border: 1px solid #4caf50; color: #81c784;
    padding: 10px 20px; border-radius: 8px; font-size: 13px; z-index: 100;
    animation: wzFadeIn 0.3s ease;
}
.wz-toast.warn { border-color: #e6a23c; color: #e6a23c; }
`;
