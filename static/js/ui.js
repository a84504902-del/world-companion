export function toggleMoreMenu(ev) {
    if (ev) ev.stopPropagation();
    const dd = document.getElementById('moreDropdown');
    if (dd) dd.style.display = (dd.style.display === 'none') ? 'flex' : 'none';
}

export function closeMoreMenu() {
    const dd = document.getElementById('moreDropdown');
    if (dd) dd.style.display = 'none';
}

export function openPromptModal() {
    closeMoreMenu();
    // 标题带上会话名，避免"不知道在编辑谁的设定"
    // （原来从顶部 #chatTitle 读，那个元素已删，改成从会话数据取）
    const label = document.getElementById('promptModalSession');
    import('./sessions.js').then(mod => {
        if (label) label.textContent = mod.currentSessionTitle() || '当前对话';
    });

    const m = document.getElementById('promptModal');
    if (m) m.style.display = 'flex';
    import('./memory.js').then(mod => {
        if (mod.loadSystemPrompt) mod.loadSystemPrompt();
        if (mod.loadTemplates) mod.loadTemplates();
    });
}

export function closePromptModal() {
    const m = document.getElementById('promptModal');
    if (m) m.style.display = 'none';
}

export function openSearchModal() {
    closeMoreMenu();
    const m = document.getElementById('searchModal');
    if (m) m.style.display = 'flex';
    const input = document.getElementById('searchInput');
    if (input) input.focus();
}

export function closeSearchModal() {
    const m = document.getElementById('searchModal');
    if (m) m.style.display = 'none';
}

export function openLoopsModal() {
    closeMoreMenu();
    const label = document.getElementById('loopsModalSession');
    import('./sessions.js').then(mod => {
        if (label) label.textContent = mod.currentSessionTitle() || '当前对话';
    });

    const m = document.getElementById('loopsModal');
    if (m) m.style.display = 'flex';
    import('./loops.js').then(mod => mod.loadLoops());
}

export function closeLoopsModal() {
    const m = document.getElementById('loopsModal');
    if (m) m.style.display = 'none';
}

export function openPersonConfigModal() {
    closeMoreMenu();
    const label = document.getElementById('personConfigSession');
    import('./sessions.js').then(mod => {
        if (label) label.textContent = mod.currentSessionTitle() || '当前对话';
    });
    const m = document.getElementById('personConfigModal');
    if (m) m.style.display = 'flex';
    import('./memory.js').then(mod => mod.loadPersonConfigModal());
}

export function closePersonConfigModal() {
    const m = document.getElementById('personConfigModal');
    if (m) m.style.display = 'none';
}

export function switchTab(tab) {
    document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(panel => panel.classList.remove('active'));

    event.target.classList.add('active');
    document.getElementById(tab + 'Panel').classList.add('active');

    // Dynamic imports to load corresponding modules
    if (tab === 'memory') import('./memory.js').then(m => m.loadMemories());
    if (tab === 'people') import('./memory.js').then(m => m.loadPeople());
    if (tab === 'relations') import('./memory.js').then(m => m.loadRelations());
}
