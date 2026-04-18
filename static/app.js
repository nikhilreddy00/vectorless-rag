/**
 * Vectorless RAG — Frontend Application
 * 
 * Handles:
 * - Loading and displaying indexed SEC filings
 * - Rendering interactive tree structure visualization
 * - Streaming query pipeline via Server-Sent Events
 * - Real-time UI updates for each pipeline step
 */

// ─── State ─────────────────────────────────────
let state = {
    filings: [],
    selectedFiling: null,
    treeData: null,
    isQuerying: false,
    timerInterval: null,
    timerStart: 0,
};

// ─── Initialize ────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    loadFilings();
    loadUsage();
    setupEventListeners();
});

function setupEventListeners() {
    const input = document.getElementById('query-input');
    const btn = document.getElementById('query-btn');
    
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !state.isQuerying && state.selectedFiling) {
            runQuery();
        }
    });
    
    btn.addEventListener('click', () => {
        if (!state.isQuerying && state.selectedFiling) runQuery();
    });
    
    // Example chips
    document.querySelectorAll('.example-chip').forEach(chip => {
        chip.addEventListener('click', () => {
            const q = chip.getAttribute('data-q');
            document.getElementById('query-input').value = q;
            if (state.selectedFiling) runQuery();
        });
    });
}

// ─── Filings ───────────────────────────────────
async function loadFilings() {
    const res = await fetch('/api/filings');
    state.filings = await res.json();
    
    document.getElementById('filing-count').textContent = state.filings.length;
    renderFilingList();
    
    // Auto-select latest filing
    if (state.filings.length > 0) {
        selectFiling(state.filings[state.filings.length - 1].name);
    }
}

function renderFilingList() {
    const list = document.getElementById('filing-list');
    list.innerHTML = state.filings.map(f => `
        <div class="filing-item ${f.name === state.selectedFiling ? 'active' : ''}" 
             data-filing="${f.name}" onclick="selectFiling('${f.name}')">
            <div class="filing-icon">FY${f.year ? f.year.slice(2) : '??'}</div>
            <div class="filing-info">
                <div class="filing-name">${f.ticker} 10-K (${f.year})</div>
                <div class="filing-detail">Filed ${formatDate(f.date)}</div>
            </div>
            <div class="filing-nodes">${f.node_count} nodes</div>
        </div>
    `).join('');
}

function formatDate(dateStr) {
    if (!dateStr || dateStr.length !== 8) return dateStr;
    const y = dateStr.slice(0, 4), m = dateStr.slice(4, 6), d = dateStr.slice(6, 8);
    const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    return `${months[parseInt(m)-1]} ${parseInt(d)}, ${y}`;
}

async function selectFiling(name) {
    state.selectedFiling = name;
    renderFilingList();
    
    // Enable query input
    document.getElementById('query-input').disabled = false;
    document.getElementById('query-btn').disabled = false;
    
    // Load tree
    await loadTree(name);
}

// ─── Tree Visualization ────────────────────────
async function loadTree(filingName) {
    const container = document.getElementById('tree-container');
    container.innerHTML = '<div class="tree-placeholder"><div class="step-icon" style="background:var(--accent-blue);width:24px;height:24px;border-radius:50%;animation:spin 0.6s linear infinite"></div><p>Loading tree...</p></div>';
    
    const res = await fetch(`/api/tree/${filingName}`);
    const data = await res.json();
    state.treeData = data;
    
    const meta = document.getElementById('tree-meta');
    meta.textContent = `${data.line_count} lines`;
    
    container.innerHTML = '';
    if (data.tree && data.tree.length > 0) {
        data.tree.forEach(node => {
            container.appendChild(renderTreeNode(node, 0));
        });
    }
}

function renderTreeNode(node, depth) {
    const div = document.createElement('div');
    div.className = 'tree-node';
    div.setAttribute('data-node-id', node.node_id);
    
    const hasChildren = node.children && node.children.length > 0;
    const isToc = !node.is_content;
    const sizeStr = node.text_length > 0 ? formatSize(node.text_length) : '';
    
    // Clean up title
    let title = node.title || 'Untitled';
    // Remove table-of-contents pipe formatting
    title = title.replace(/\|.*$/, '').trim();
    
    const inner = document.createElement('div');
    inner.className = `tree-node-inner${isToc ? ' toc-node' : ''}`;
    inner.innerHTML = `
        <span class="tree-toggle ${hasChildren ? '' : 'leaf'}${depth < 2 ? ' expanded' : ''}">▶</span>
        <span class="tree-node-id">${node.node_id}</span>
        <span class="tree-node-title">${escapeHtml(title)}</span>
        ${sizeStr ? `<span class="tree-node-size ${node.is_content ? 'has-content' : ''}">${sizeStr}</span>` : ''}
    `;
    
    inner.addEventListener('click', (e) => {
        const toggle = inner.querySelector('.tree-toggle');
        const children = div.querySelector('.tree-children');
        if (children) {
            const isCollapsed = children.classList.toggle('collapsed');
            toggle.classList.toggle('expanded', !isCollapsed);
        }
    });
    
    div.appendChild(inner);
    
    if (hasChildren) {
        const childContainer = document.createElement('div');
        childContainer.className = `tree-children${depth >= 2 ? ' collapsed' : ''}`;
        node.children.forEach(child => {
            childContainer.appendChild(renderTreeNode(child, depth + 1));
        });
        div.appendChild(childContainer);
    }
    
    return div;
}

function formatSize(chars) {
    if (chars < 1000) return `${chars}c`;
    if (chars < 10000) return `${(chars/1000).toFixed(1)}K`;
    return `${Math.round(chars/1000)}K`;
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

// Highlight nodes in the tree
function highlightNodes(nodeIds) {
    // Clear previous highlights
    document.querySelectorAll('.tree-node-inner.highlighted').forEach(el => {
        el.classList.remove('highlighted');
    });
    
    nodeIds.forEach(id => {
        const node = document.querySelector(`[data-node-id="${id}"]`);
        if (node) {
            const inner = node.querySelector('.tree-node-inner');
            if (inner) {
                inner.classList.add('highlighted');
                // Expand parents to make visible
                let parent = node.parentElement;
                while (parent) {
                    if (parent.classList && parent.classList.contains('tree-children') && parent.classList.contains('collapsed')) {
                        parent.classList.remove('collapsed');
                        const toggle = parent.parentElement?.querySelector('.tree-toggle');
                        if (toggle) toggle.classList.add('expanded');
                    }
                    parent = parent.parentElement;
                }
                // Scroll into view
                inner.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }
        }
    });
}

// ─── Query Pipeline ────────────────────────────
function runQuery() {
    const question = document.getElementById('query-input').value.trim();
    if (!question || !state.selectedFiling || state.isQuerying) return;
    
    state.isQuerying = true;
    
    // UI Updates
    const btn = document.getElementById('query-btn');
    btn.classList.add('loading');
    btn.querySelector('span').textContent = 'Processing';
    
    document.getElementById('how-it-works').style.display = 'none';
    document.getElementById('pipeline-section').style.display = 'block';
    document.getElementById('answer-section').style.display = 'none';
    document.getElementById('pipeline-steps').innerHTML = '';
    
    // Start timer
    state.timerStart = performance.now();
    state.timerInterval = setInterval(updateTimer, 50);
    
    // Clear tree highlights
    highlightNodes([]);
    
    // SSE via POST fetch with streaming
    fetch('/api/query', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            filing: state.selectedFiling,
            question: question
        })
    }).then(response => {
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        
        function processStream() {
            reader.read().then(({ done, value }) => {
                if (done) {
                    finishQuery();
                    return;
                }
                
                buffer += decoder.decode(value, { stream: true });
                
                // Parse SSE events
                const events = buffer.split('\n\n');
                buffer = events.pop(); // Keep incomplete event in buffer
                
                events.forEach(eventStr => {
                    if (!eventStr.trim()) return;
                    
                    const lines = eventStr.split('\n');
                    let eventType = '';
                    let eventData = '';
                    
                    lines.forEach(line => {
                        if (line.startsWith('event: ')) {
                            eventType = line.slice(7);
                        } else if (line.startsWith('data: ')) {
                            eventData = line.slice(6);
                        }
                    });
                    
                    if (eventType && eventData) {
                        try {
                            const data = JSON.parse(eventData);
                            handleEvent(eventType, data);
                        } catch (e) {
                            console.error('Parse error:', e, eventData);
                        }
                    }
                });
                
                processStream();
            });
        }
        
        processStream();
    }).catch(err => {
        console.error('Query error:', err);
        finishQuery();
    });
}

function handleEvent(type, data) {
    if (type === 'step') {
        handleStep(data);
    } else if (type === 'result') {
        handleResult(data);
    } else if (type === 'error') {
        handleError(data);
    }
}

function handleStep(data) {
    const container = document.getElementById('pipeline-steps');
    const stepId = `step-${data.step}`;
    
    let card = document.getElementById(stepId);
    
    if (!card) {
        card = document.createElement('div');
        card.id = stepId;
        card.className = 'step-card';
        container.appendChild(card);
    }
    
    card.className = `step-card ${data.status}`;
    
    const iconContent = data.status === 'done' ? '✓' : data.status === 'running' ? '◉' : (data.step + 1);
    
    let detailHtml = '';
    if (data.detail) {
        // Truncate long reasoning text
        const detail = data.detail.length > 300 ? data.detail.slice(0, 300) + '...' : data.detail;
        detailHtml = `<div class="step-detail">${escapeHtml(detail)}</div>`;
    }
    
    // Selected nodes badges
    let nodesHtml = '';
    if (data.selected_nodes && data.selected_nodes.length > 0) {
        nodesHtml = `<div class="step-nodes">${data.selected_nodes.map(id => 
            `<span class="step-node-tag">⬡ Node ${id}</span>`
        ).join('')}</div>`;
        
        // Highlight in tree
        highlightNodes(data.selected_nodes);
    }
    
    // Retrieved sections
    let sectionsHtml = '';
    if (data.sections && data.sections.length > 0) {
        sectionsHtml = `<div class="step-sections">${data.sections.map(s => `
            <div class="step-section-item">
                <div class="step-section-title">${escapeHtml(s.title)}</div>
                <div class="step-section-meta">Node ${s.node_id} · Line ${s.line_num} · ${formatSize(s.text_length)} chars</div>
                ${s.preview ? `<div class="step-section-preview">${escapeHtml(s.preview.slice(0, 200))}</div>` : ''}
            </div>
        `).join('')}</div>`;
    }
    
    const timeStr = data.time ? `${data.time}s` : '';
    
    card.innerHTML = `
        <div class="step-header">
            <div class="step-icon">${iconContent}</div>
            <div class="step-title">${data.title}</div>
            ${timeStr ? `<div class="step-time">${timeStr}</div>` : ''}
        </div>
        ${detailHtml}
        ${nodesHtml}
        ${sectionsHtml}
    `;
}

function handleResult(data) {
    // Show answer
    document.getElementById('answer-section').style.display = 'block';
    
    const remaining = data.queries_remaining !== undefined ? data.queries_remaining : '?';
    document.getElementById('answer-meta').textContent = 
        `${data.confidence} confidence · ${data.total_time}s total · ${data.retrieved_nodes.length} sections · ${remaining} queries left`;
    
    // Convert markdown-like formatting
    let answerHtml = escapeHtml(data.answer);
    answerHtml = answerHtml.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    answerHtml = answerHtml.replace(/\*(.+?)\*/g, '<em>$1</em>');
    answerHtml = answerHtml.replace(/\n/g, '<br>');
    
    document.getElementById('answer-body').innerHTML = answerHtml;
    
    // Update usage badge
    updateUsageBadge(remaining);
    
    // Disable if no queries left
    if (remaining <= 0) {
        document.getElementById('query-input').disabled = true;
        document.getElementById('query-btn').disabled = true;
        document.getElementById('query-input').placeholder = 'Demo limit reached — thank you for trying!';
    }
    
    // Scroll to answer
    document.getElementById('answer-section').scrollIntoView({ behavior: 'smooth', block: 'start' });
    
    finishQuery();
}

function handleError(data) {
    document.getElementById('answer-section').style.display = 'block';
    document.getElementById('answer-meta').textContent = '';
    
    const isRateLimit = data.message && data.message.includes('Demo limit');
    
    if (isRateLimit) {
        document.getElementById('answer-body').innerHTML = `
            <div style="text-align:center;padding:20px 0">
                <div style="font-size:2.5rem;margin-bottom:12px">🎉</div>
                <div style="font-size:1.1rem;font-weight:600;color:var(--accent-amber);margin-bottom:8px">Demo Limit Reached</div>
                <div style="color:var(--text-secondary);line-height:1.6">
                    Thank you for trying <strong style="color:var(--accent-cyan)">Vectorless RAG</strong>!<br>
                    All 5 demo queries have been used.<br><br>
                    To run unlimited queries, clone the repo and use your own API key.
                </div>
            </div>`;
        document.getElementById('query-input').disabled = true;
        document.getElementById('query-btn').disabled = true;
        document.getElementById('query-input').placeholder = 'Demo limit reached — thank you for trying!';
        updateUsageBadge(0);
    } else {
        document.getElementById('answer-body').innerHTML = `<span style="color:var(--accent-red)">Error: ${escapeHtml(data.message)}</span>`;
    }
    finishQuery();
}

function finishQuery() {
    state.isQuerying = false;
    
    if (state.timerInterval) {
        clearInterval(state.timerInterval);
        state.timerInterval = null;
    }
    
    const btn = document.getElementById('query-btn');
    btn.classList.remove('loading');
    btn.querySelector('span').textContent = 'Ask';
}

function updateTimer() {
    const elapsed = ((performance.now() - state.timerStart) / 1000).toFixed(2);
    document.getElementById('pipeline-timer').textContent = `${elapsed}s`;
}

// ─── Usage / Rate Limiting ─────────────────────
async function loadUsage() {
    try {
        const res = await fetch('/api/usage');
        const data = await res.json();
        updateUsageBadge(data.remaining);
        
        if (data.remaining <= 0) {
            document.getElementById('query-input').disabled = true;
            document.getElementById('query-btn').disabled = true;
            document.getElementById('query-input').placeholder = 'Demo limit reached — thank you for trying!';
        }
    } catch(e) {}
}

function updateUsageBadge(remaining) {
    const badge = document.getElementById('usage-badge');
    if (badge) {
        badge.textContent = `${remaining} queries left`;
        badge.style.color = remaining > 2 ? 'var(--accent-green-light)' : 
                           remaining > 0 ? 'var(--accent-amber)' : 'var(--accent-red)';
        badge.style.borderColor = remaining > 2 ? 'rgba(16,185,129,0.2)' : 
                                  remaining > 0 ? 'rgba(245,158,11,0.2)' : 'rgba(239,68,68,0.2)';
        badge.style.background = remaining > 2 ? 'var(--accent-green-glow)' : 
                                 remaining > 0 ? 'var(--accent-amber-glow)' : 'rgba(239,68,68,0.1)';
    }
}
