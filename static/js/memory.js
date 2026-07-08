import * as state from './state.js';
import { escapeHtml } from './utils.js';

// --- 记忆 ---
export async function loadMemories() {
    try {
        const url = state.currentSession ? `/memories?session_id=${state.currentSession}` : '/memories';
        const resp = await fetch(url);
        const data = await resp.json();
        const panel = document.getElementById('memoryPanel');
        if (!data.memories || data.memories.length === 0) {
            panel.innerHTML = '<p style="color: #999;">暂无记忆</p>';
            return;
        }
        panel.innerHTML = data.memories.map(m => `
            <div class="memory-item">
                <div class="memory-content">${escapeHtml(m.content)}</div>
                <div class="memory-meta">
                    <span>${m.timestamp}</span>
                    ${m.tags ? `<span class="memory-tag">${escapeHtml(m.tags)}</span>` : ''}
                    <button class="btn btn-danger btn-sm" onclick="deleteMemory(${m.id})">删除</button>
                </div>
            </div>
        `).join('');
    } catch (e) {
        console.error('加载记忆失败:', e);
    }
}

export async function deleteMemory(id) {
    if (!confirm('确定删除这条记忆？')) return;
    try {
        await fetch('/delete_memory', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id })
        });
        loadMemories();
    } catch (e) {
        alert('删除失败: ' + e.message);
    }
}

// --- 人物 ---
export async function loadPeople() {
    try {
        const resp = await fetch(`/api/people/list?session_id=${state.currentSession}`);
        const data = await resp.json();
        const panel = document.getElementById('peoplePanel');

        let html = '<div class="add-section">';
        html += '<input type="text" id="newPersonName" placeholder="名字" class="input-sm">';
        html += '<input type="text" id="newPersonDesc" placeholder="描述（如：妈妈、朋友）" class="input-sm">';
        html += '<button class="btn btn-primary btn-sm" onclick="addPerson()">添加</button>';
        html += '</div>';

        if (!data.people || data.people.length === 0) {
            html += '<p style="color: #999; margin-top: 12px;">暂无人物</p>';
        } else {
            html += data.people.map(p => `
                <div class="person-item">
                    <div class="person-name">${escapeHtml(p.name)}</div>
                    <div class="person-desc">${escapeHtml(p.description || '无描述')}</div>
                    <button class="btn btn-danger btn-sm" onclick="deletePerson(${p.id})">删除</button>
                </div>
            `).join('');
        }
        panel.innerHTML = html;
    } catch (e) {
        console.error('加载人物失败:', e);
    }
}

export async function addPerson() {
    const name = document.getElementById('newPersonName').value.trim();
    const desc = document.getElementById('newPersonDesc').value.trim();
    if (!name) { alert('请输入名字'); return; }
    try {
        await fetch('/api/people/add', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, description: desc, session_id: state.currentSession })
        });
        loadPeople();
    } catch (e) {
        alert('添加失败: ' + e.message);
    }
}

export async function deletePerson(id) {
    if (!confirm('确定删除？相关关系也会被删除')) return;
    try {
        await fetch('/api/people/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id })
        });
        loadPeople();
        const { loadRelations } = await import('./memory.js');
        loadRelations();
    } catch (e) {
        alert('删除失败: ' + e.message);
    }
}

// --- 关系 ---
export async function loadRelations() {
    try {
        const resp = await fetch(`/api/relations/list?session_id=${state.currentSession}`);
        const data = await resp.json();
        const panel = document.getElementById('relationsPanel');

        const peopleResp = await fetch(`/api/people/list?session_id=${state.currentSession}`);
        const peopleData = await peopleResp.json();
        const people = peopleData.people || [];

        let html = '<div class="add-section">';
        html += '<select id="relPersonA" class="input-sm"><option value="">选择人物A</option>';
        people.forEach(p => { html += `<option value="${p.id}">${escapeHtml(p.name)}</option>`; });
        html += '</select>';
        html += '<input type="text" id="relType" placeholder="关系（如：妈妈、朋友）" class="input-sm">';
        html += '<select id="relPersonB" class="input-sm"><option value="">选择人物B</option>';
        people.forEach(p => { html += `<option value="${p.id}">${escapeHtml(p.name)}</option>`; });
        html += '</select>';
        html += '<button class="btn btn-primary btn-sm" onclick="addRelation()">添加</button>';
        html += '</div>';

        if (!data.relations || data.relations.length === 0) {
            html += '<p style="color: #999; margin-top: 12px;">暂无关系</p>';
        } else {
            html += data.relations.map(r => `
                <div class="relation-item">
                    <span>${escapeHtml(r.person_a_name)} → ${escapeHtml(r.relation_type)} → ${escapeHtml(r.person_b_name)}</span>
                    <button class="btn btn-danger btn-sm" onclick="deleteRelation(${r.id})">删除</button>
                </div>
            `).join('');
        }
        panel.innerHTML = html;
    } catch (e) {
        console.error('加载关系失败:', e);
    }
}

export async function addRelation() {
    const personA = document.getElementById('relPersonA').value;
    const type = document.getElementById('relType').value.trim();
    const personB = document.getElementById('relPersonB').value;
    if (!personA || !type || !personB) { alert('请填写完整'); return; }
    try {
        await fetch('/api/relations/add', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                person_a_id: parseInt(personA),
                relation_type: type,
                person_b_id: parseInt(personB),
                session_id: state.currentSession
            })
        });
        loadRelations();
    } catch (e) {
        alert('添加失败: ' + e.message);
    }
}

export async function deleteRelation(id) {
    if (!confirm('确定删除？')) return;
    try {
        await fetch('/api/relations/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id })
        });
        loadRelations();
    } catch (e) {
        alert('删除失败: ' + e.message);
    }
}

// --- 提示词 ---
export async function loadSystemPrompt() {
    try {
        const resp = await fetch('/api/system_prompt');
        const data = await resp.json();
        document.getElementById('systemPromptInput').value = data.system_prompt || '';
    } catch (e) {
        console.error('加载系统提示词失败:', e);
    }
}

export async function saveSystemPrompt() {
    const prompt = document.getElementById('systemPromptInput').value;
    try {
        await fetch('/api/system_prompt', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ system_prompt: prompt })
        });
        alert('保存成功');
    } catch (e) {
        alert('保存失败: ' + e.message);
    }
}

export async function loadTemplates() {
    try {
        const resp = await fetch('/api/templates');
        const data = await resp.json();
        const select = document.getElementById('templateSelect');
        select.innerHTML = '<option value="">选择模板...</option>';
        if (data.templates) {
            data.templates.forEach(t => {
                const option = document.createElement('option');
                option.value = t.id;
                option.textContent = t.name;
                select.appendChild(option);
            });
        }
    } catch (e) {
        console.error('加载模板失败:', e);
    }
}

export async function loadTemplate() {
    const id = document.getElementById('templateSelect').value;
    if (!id) return;
    try {
        const resp = await fetch('/api/templates');
        const data = await resp.json();
        const template = data.templates.find(t => t.id === id);
        if (template) {
            document.getElementById('templatePrompt').value = template.prompt;
        }
    } catch (e) {
        console.error('加载模板失败:', e);
    }
}

export async function useTemplate() {
    const prompt = document.getElementById('templatePrompt').value.trim();
    if (!prompt) { alert('请先选择或输入提示词'); return; }
    document.getElementById('systemPromptInput').value = prompt;
    await saveSystemPrompt();
    alert('模板已应用到当前会话');
}

export function showAddTemplateModal() {
    document.getElementById('addTemplateModal').style.display = 'flex';
    document.getElementById('templateName').value = '';
    document.getElementById('templatePrompt').value = document.getElementById('systemPromptInput').value;
}

export function hideAddTemplateModal() {
    document.getElementById('addTemplateModal').style.display = 'none';
}

export async function saveTemplate() {
    const name = document.getElementById('templateName').value.trim();
    const prompt = document.getElementById('templatePrompt').value.trim();
    if (!name || !prompt) { alert('请填写名称和提示词'); return; }
    try {
        await fetch('/api/templates/add', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, prompt })
        });
        hideAddTemplateModal();
        loadTemplates();
        alert('模板已保存');
    } catch (e) {
        alert('保存失败: ' + e.message);
    }
}

// --- 搜索 ---
export async function searchChat() {
    const query = document.getElementById('searchInput').value.trim();
    if (!query) return;
    try {
        const resp = await fetch(`/api/search_chat?q=${encodeURIComponent(query)}`);
        const data = await resp.json();
        const panel = document.getElementById('searchResults');
        if (!data.results || data.results.length === 0) {
            panel.innerHTML = '<p style="color: #555; margin-top: 12px;">未找到相关记录</p>';
            return;
        }
        panel.innerHTML = data.results.map(r => `
            <div class="search-item" onclick="jumpToSession('${r.session_id}')">
                <div class="search-session">${escapeHtml(r.session_title)}</div>
                <div class="search-role">${r.role === 'user' ? '用户' : 'AI'}</div>
                <div class="search-content">${escapeHtml(r.content.substring(0, 100))}${r.content.length > 100 ? '...' : ''}</div>
                <div class="search-time">${r.timestamp}</div>
            </div>
        `).join('');
    } catch (e) {
        console.error('搜索失败:', e);
    }
}

export async function jumpToSession(sessionId) {
    try {
        const resp = await fetch('/switch_chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: sessionId })
        });
        const data = await resp.json();
        state.setCurrentSession(sessionId);
        const { renderMessages } = await import('./chat.js');
        renderMessages(data.history);
        const { loadSessions } = await import('./sessions.js');
        loadSessions();
    } catch (e) {
        console.error('跳转失败:', e);
    }
}

// --- LLM 选择器 ---
export async function loadCustomLLMs() {
    try {
        const resp = await fetch('/api/llms');
        const data = await resp.json();
        const select = document.getElementById('llmSelect');
        select.innerHTML = '';
        if (data.llms && data.llms.length > 0) {
            data.llms.forEach(llm => {
                const option = document.createElement('option');
                option.value = llm.id;
                option.textContent = llm.name;
                select.appendChild(option);
            });
            const saved = localStorage.getItem('selected_llm');
            if (saved && data.llms.some(l => l.id === saved)) {
                select.value = saved;
            }
        }
        select.addEventListener('change', () => {
            localStorage.setItem('selected_llm', select.value);
        });
    } catch (e) {
        console.error('加载 LLM 列表失败:', e);
    }
}
