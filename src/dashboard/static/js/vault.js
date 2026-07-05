// =============================================================================
// Vault — Operator knowledge base and system reference
// =============================================================================
// Dashboard cards view for browsing vault docs. Loaded as Action Board tab.
// Docs are served as HTML for rich display. API provides metadata + content.
// =============================================================================

let _abVaultLoaded = false;
let _vaultDocOpen = false;

// =============================================================================
// Vault home — dashboard cards
// =============================================================================
async function loadABVault() {
    if (_abVaultLoaded) return;
    const container = document.getElementById('ab-vault-home');
    if (!container) return;

    try {
        const res = await fetch('/api/vault/overview');
        if (!res.ok) throw new Error(`${res.status}`);
        const data = await res.json();
        renderVaultHome(container, data.categories || []);
    } catch (e) {
        // API not built yet — show seed data
        renderVaultHome(container, getSeedVaultCategories());
    }
    _abVaultLoaded = true;
}

function renderVaultHome(container, categories) {
    if (categories.length === 0) {
        container.innerHTML = '<div class="ab-empty">No vault docs yet.</div>';
        return;
    }

    let html = '<div class="vault-grid">';
    categories.forEach(cat => {
        // Support both color_freq (from API) and color (from seed)
        const iconColor = cat.color_freq && typeof lpColor === 'function'
            ? lpColor(cat.color_freq)
            : (cat.color || 'var(--accent)');
        const docCount = cat.docs ? cat.docs.length : (cat.doc_count || 0);
        const updated = cat.last_updated || '';

        html += `
        <div class="vault-card" onclick="openVaultCategory('${esc(cat.id)}')">
            <div class="vault-card-icon" style="color:${iconColor};border-color:${iconColor};">${cat.icon || ''}</div>
            <div class="vault-card-info">
                <h3 class="vault-card-title">${esc(cat.name)}</h3>
                <p class="vault-card-desc">${esc(cat.description || '')}</p>
                <div class="vault-card-meta">
                    <span>${docCount} doc${docCount !== 1 ? 's' : ''}</span>
                    ${updated ? `<span>Updated ${esc(updated)}</span>` : ''}
                </div>
            </div>
        </div>`;
    });
    html += '</div>';

    container.innerHTML = html;
}

// =============================================================================
// Category drill-down — list docs in a category
// =============================================================================
async function openVaultCategory(catId) {
    const container = document.getElementById('ab-vault-home');
    if (!container) return;

    let docs;
    try {
        const res = await fetch(`/api/vault/category/${catId}`);
        if (!res.ok) throw new Error(`${res.status}`);
        const data = await res.json();
        docs = data.docs || [];
    } catch (e) {
        // Seed fallback
        const cats = getSeedVaultCategories();
        const cat = cats.find(c => c.id === catId);
        docs = cat ? cat.docs : [];
    }

    let html = `<div class="vault-breadcrumb">
        <button class="vault-back" onclick="vaultGoHome()">Vault</button>
        <span class="vault-sep">/</span>
        <span class="vault-current">${esc(catId.replace(/-/g, ' ').replace(/\b\w/g, c => c.toUpperCase()))}</span>
    </div>`;

    if (!docs || docs.length === 0) {
        html += '<div class="ab-empty">No docs in this category yet.</div>';
    } else {
        html += '<div class="vault-doc-list">';
        docs.forEach(doc => {
            const status = doc.status || 'current';
            const statusColor = status === 'current' ? 'var(--green)' : status === 'stale' ? 'var(--yellow)' : 'var(--dim)';
            html += `
            <div class="vault-doc-row" onclick="openVaultDoc('${esc(doc.id)}')">
                <span class="vault-doc-status" style="background:${statusColor};"></span>
                <div class="vault-doc-info">
                    <div class="vault-doc-title">${esc(doc.title)}</div>
                    <div class="vault-doc-desc">${esc(doc.description || '')}</div>
                </div>
                <span class="vault-doc-updated">${esc(doc.updated || '')}</span>
            </div>`;
        });
        html += '</div>';
    }

    container.innerHTML = html;
}

function vaultGoHome() {
    _abVaultLoaded = false;
    loadABVault();
}

// =============================================================================
// Document viewer — render HTML doc in an overlay
// =============================================================================
async function openVaultDoc(docId) {
    let content;
    try {
        const res = await fetch(`/api/vault/doc/${docId}`);
        if (!res.ok) throw new Error(`${res.status}`);
        const data = await res.json();
        content = data.html || data.content || '<p>No content</p>';
    } catch (e) {
        // Seed fallback
        content = getSeedDocContent(docId);
    }

    const overlay = document.createElement('div');
    overlay.className = 'vault-overlay';
    overlay.onclick = e => { if (e.target === overlay) overlay.remove(); };
    overlay.innerHTML = `<div class="vault-doc-viewer">
        <div class="vault-doc-toolbar">
            <button class="vault-doc-close" onclick="this.closest('.vault-overlay').remove()">&times;</button>
        </div>
        <div class="vault-doc-content">${content}</div>
    </div>`;
    document.body.appendChild(overlay);
}

// =============================================================================
// Seed data — replaced by API once vault routes are built
// =============================================================================
function getSeedVaultCategories() {
    const fColor = typeof lpColor === 'function' ? lpColor : () => 'var(--accent)';
    return [
        {
            id: 'operations',
            name: 'Operations',
            description: 'Runbooks, deploy procedures, infrastructure commands',
            icon: '⚙️',
            color: fColor('Integration'),
            doc_count: 4,
            last_updated: 'today',
            docs: [
                { id: 'runbooks', title: 'Ops Runbooks', description: 'Deploy, restart, migration procedures', status: 'current', updated: 'May 12' },
                { id: 'ssh-commands', title: 'SSH & Command Reference', description: 'All infrastructure commands, copy-paste ready', status: 'current', updated: 'May 12' },
                { id: 'container-map', title: 'Container Map', description: 'Docker services, ports, volumes, networking', status: 'current', updated: 'May 12' },
                { id: 'deploy-safety', title: 'Deploy Safety Rules', description: 'Pre-deploy checks, rescue procedures, agent change handling', status: 'current', updated: 'May 12' },
            ],
        },
        {
            id: 'memory',
            name: 'Memory & Context',
            description: 'Working memory, context map, session history',
            icon: '🧠',
            color: fColor('Clarity'),
            doc_count: 4,
            last_updated: 'today',
            docs: [
                { id: 'working-memory', title: 'Working Memory', description: 'Current sprint, decisions, system state', status: 'current', updated: 'today' },
                { id: 'context-map', title: 'Context Map', description: 'Full workspace navigation — every system, folder, command', status: 'current', updated: 'May 12' },
                { id: 'session-index', title: 'Session Index', description: 'Searchable index of all past sessions by topic', status: 'current', updated: 'May 12' },
                { id: 'decisions-log', title: 'Active Decisions', description: 'Constraints that govern current work', status: 'current', updated: 'today' },
            ],
        },
        {
            id: 'system',
            name: 'System State',
            description: 'Agent status, infrastructure health, service map',
            icon: '🖥️',
            color: fColor('Trust'),
            doc_count: 3,
            last_updated: 'today',
            docs: [
                { id: 'agent-registry', title: 'Agent Registry', description: 'All agents — roles, frequencies, status', status: 'current', updated: 'May 11' },
                { id: 'service-map', title: 'P620 Services', description: 'Hardware, Docker, networking, storage', status: 'current', updated: 'May 10' },
                { id: 'vps-services', title: 'VPS Services', description: 'Socrates, LT, team agents, LTP pipeline', status: 'current', updated: 'May 10' },
            ],
        },
        {
            id: 'specs',
            name: 'Specs & Architecture',
            description: 'Product specs, team operations, color system',
            icon: '📐',
            color: fColor('Momentum'),
            doc_count: 5,
            last_updated: 'May 12',
            docs: [
                { id: 'team-ops', title: 'Team Operations', description: 'Workflow patterns, delegation, approval tiers', status: 'current', updated: 'May 12' },
                { id: 'color-system', title: 'LP Color System', description: '14 frequencies, 7 signals, 9 semantic roles', status: 'current', updated: 'May 10' },
                { id: 'brand-arch', title: 'Brand Architecture', description: 'Lucid Cove, Lucid Tuner, The Lucid Path — naming rules', status: 'current', updated: 'May 13' },
                { id: 'action-board-spec', title: 'Action Board Spec', description: 'Actions, Flows, Vault — board architecture', status: 'draft', updated: 'May 11' },
                { id: 'ltp-spec', title: 'LTP Echo & Media Spec', description: 'Tuning pipeline, audio analysis, echo format', status: 'current', updated: 'May 10' },
            ],
        },
        {
            id: 'brand',
            name: 'Brand & Product',
            description: 'Lucid Cove product, roadmap, affiliate model',
            icon: '🏠',
            color: fColor('Peace'),
            doc_count: 3,
            last_updated: 'May 13',
            docs: [
                { id: 'roadmap', title: 'Product Roadmap', description: 'Lucid Cove build phases and milestones', status: 'current', updated: 'May 11' },
                { id: 'about', title: 'About Lucid Principles', description: 'Who we are, what we build, business model', status: 'current', updated: 'May 11' },
                { id: 'mercer-spec', title: 'Mercer Commerce Spec', description: 'Affiliate program, revenue, marketplace', status: 'draft', updated: 'May 10' },
            ],
        },
        {
            id: 'framework',
            name: 'Framework & Canon',
            description: 'Tuning keys, principles, manifesto, glossary',
            icon: '🎵',
            color: fColor('Connection'),
            doc_count: 4,
            last_updated: 'May 10',
            docs: [
                { id: 'tuning-keys', title: 'Tuning Keys', description: 'All 22 principles with tuning keys and frequency maps', status: 'current', updated: 'May 10' },
                { id: 'manifesto', title: 'Manifesto', description: 'The Lucid Principles framework architecture', status: 'current', updated: 'May 8' },
                { id: 'glossary', title: 'Framework Glossary', description: 'Terms, definitions, usage guidelines', status: 'current', updated: 'May 8' },
                { id: 'canon', title: 'The Canon', description: '22 Lucid Principles — sacred text, never paraphrase', status: 'current', updated: 'locked' },
            ],
        },
    ];
}

function getSeedDocContent(docId) {
    // Placeholder content for seed mode — API will serve real HTML docs
    const titles = {
        'runbooks': 'Ops Runbooks',
        'working-memory': 'Working Memory',
        'context-map': 'Context Map',
        'agent-registry': 'Agent Registry',
        'brand-arch': 'Brand Architecture',
    };
    const title = titles[docId] || docId.replace(/-/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
    return `<div style="padding:1.5rem;">
        <h1 style="color:var(--accent);margin-bottom:0.5rem;">${title}</h1>
        <p style="color:var(--dim);margin-bottom:1.5rem;">This document will be served from the vault API once the route is built. For now, the .md source lives in the LP-Vault.</p>
        <p>To populate this view, the vault route will read the source doc and serve it as styled HTML with navigation, status indicators, and interactive elements.</p>
    </div>`;
}
