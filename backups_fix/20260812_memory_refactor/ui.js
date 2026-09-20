export function switchTab(tab) {
    document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(panel => panel.classList.remove('active'));

    event.target.classList.add('active');
    document.getElementById(tab + 'Panel').classList.add('active');

    // Dynamic imports to load corresponding modules
    if (tab === 'memory') import('./memory.js').then(m => m.loadMemories());
    if (tab === 'people') import('./memory.js').then(m => m.loadPeople());
    if (tab === 'relations') import('./memory.js').then(m => m.loadRelations());
    if (tab === 'prompt') import('./memory.js').then(m => { m.loadSystemPrompt(); m.loadTemplates(); });
}
