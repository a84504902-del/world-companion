import * as state from './state.js';
import { escapeHtml, renderMarkdown } from './utils.js';

export async function loadSessions() {
    try {
        const resp = await fetch('/chat_sessions');
        const data = await resp.json();
        state.setSessions(data.sessions);
        state.setCurrentSession(data.current);
        renderSessions();
        if (state.currentSession) {
            const { loadHistory } = await import('./chat.js');
            loadHistory();
        }
    } catch (e) {
        console.error('加载会话失败:', e);
    }
}

export function renderSessions() {
    const list = document.getElementById('sessionList');
    list.innerHTML = state.sessions.map(s => `
        <div class="session-item ${s.id === state.currentSession ? 'active' : ''}" onclick="switchSession('${s.id}')">
            <span class="session-title">${escapeHtml(s.title || '新对话')}</span>
            <button class="session-delete" onclick="event.stopPropagation(); deleteSession('${s.id}')">&times;</button>
        </div>
    `).join('');
}

export async function newChat() {
    try {
        const resp = await fetch('/new_chat', { method: 'POST' });
        const data = await resp.json();
        state.setCurrentSession(data.session_id);
        document.getElementById('chatMessages').innerHTML = '';
        loadSessions();
    } catch (e) {
        console.error('新建对话失败:', e);
    }
}

export async function switchSession(sessionId) {
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
        loadSessions();
    } catch (e) {
        console.error('切换对话失败:', e);
    }
}

export async function deleteSession(sessionId) {
    if (!confirm('确定删除这个对话？')) return;
    try {
        await fetch('/delete_session', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: sessionId })
        });
        loadSessions();
    } catch (e) {
        console.error('删除失败:', e);
    }
}

export async function clearChat() {
    if (!confirm('确定清空当前对话？')) return;
    try {
        await fetch('/clear_chat', { method: 'POST' });
        document.getElementById('chatMessages').innerHTML = '';
    } catch (e) {
        console.error('清空失败:', e);
    }
}

export async function renameSession() {
    const currentTitle = document.getElementById('chatTitle').textContent;
    const newTitle = prompt('输入新标题:', currentTitle);
    if (!newTitle || newTitle.trim() === '') return;
    try {
        await fetch('/api/rename_session', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title: newTitle.trim() })
        });
        document.getElementById('chatTitle').textContent = newTitle.trim();
        loadSessions();
    } catch (e) {
        alert('重命名失败: ' + e.message);
    }
}

export async function exportChat() {
    const format = document.getElementById('exportFormat').value;
    window.location.href = `/export_chat?format=${format}`;
}

export async function summarizeChat() {
    const mode = document.getElementById('llmSelect').value;
    try {
        const { appendMessage } = await import('./chat.js');
        appendMessage('assistant', '正在生成摘要...');
        const resp = await fetch(`/api/summarize?mode=${mode}`);
        const data = await resp.json();
        if (data.summary) {
            appendMessage('assistant', `📝 **对话摘要：**\n\n${data.summary}`);
        } else {
            appendMessage('assistant', `摘要生成失败: ${data.error}`);
        }
    } catch (e) {
        const { appendMessage } = await import('./chat.js');
        appendMessage('assistant', `摘要生成失败: ${e.message}`);
    }
}
