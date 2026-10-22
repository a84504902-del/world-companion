import * as state from './state.js';
import { escapeHtml, renderMarkdown } from './utils.js';

/** 从引导页点进来的目标会话（?s=xxx）。加载完要用它覆盖默认的当前会话。 */
function requestedSessionId() {
    return new URLSearchParams(location.search).get('s') || '';
}

export async function loadSessions() {
    try {
        const resp = await fetch('/chat_sessions');
        const data = await resp.json();
        state.setSessions(data.sessions);

        // 引导页指定了会话就用它，否则用后端认定的当前会话
        const wanted = requestedSessionId();
        const exists = wanted && (data.sessions || []).some(s => s.id === wanted);
        state.setCurrentSession(exists ? wanted : data.current);
        renderSessions();

        if (state.currentSession) {
            // 指定了会话但后端当前指针还没切过去 → 补一次切换，
            // 让服务端的会话状态（摘要/摘要轮播等）也跟着对齐
            if (exists && wanted !== data.current) {
                try {
                    await fetch('/switch_chat', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ session_id: wanted })
                    });
                } catch (e) { /* 切不进去也能读历史，不卡住 */ }
            }
            const { loadHistory } = await import('./chat.js');
            loadHistory();
        }
    } catch (e) {
        console.error('加载会话失败:', e);
    }
}

/** 回引导页 */
export function goGuide() {
    location.href = '/';
}

/** 当前会话的名字（给弹窗标题用——原来是从顶部 #chatTitle 读的，那个元素已删） */
export function currentSessionTitle() {
    const list = state.sessions || [];
    const cur = list.find(s => s.id === state.currentSession);
    return (cur && cur.title) || '';
}

export function renderSessions() {
    const list = document.getElementById('sessionList');
    list.innerHTML = state.sessions.map(s => `
        <div class="session-item ${s.id === state.currentSession ? 'active' : ''}" onclick="switchSession('${s.id}')">
            <span class="session-title">${escapeHtml(s.title || '未命名对话')}</span>
            <button class="session-rename" onclick="event.stopPropagation(); renameSession('${s.id}')" title="改个名字">✏️</button>
            <button class="session-delete" onclick="event.stopPropagation(); deleteSession('${s.id}')">&times;</button>
        </div>
    `).join('');
}

export async function newChat() {
    // 不再弹输入框问标题：默认名用时间，想改随时点标题旁的 ✏️
    const sel = document.getElementById('llmSelect');
    const mode = sel ? sel.value : '';
    try {
        const resp = await fetch('/new_chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            // 新会话继承当前正在用的模型
            body: JSON.stringify(mode ? { model: mode } : {})
        });
        const data = await resp.json();
        state.setCurrentSession(data.session_id);
        document.getElementById('chatMessages').innerHTML = '';
        // 地址栏里可能还留着引导页传来的 ?s=xxx，不清掉刷新会跳回旧对话
        history.replaceState(null, '', location.pathname);
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
        // 侧栏切换也要清掉 ?s=，否则刷新又跳回引导页指定的那个
        history.replaceState(null, '', location.pathname);
        // 恢复这个会话记住的模型（否则会沿用上一个会话的）
        const mem = await import('./memory.js');
        mem.applySessionModel(data.model);
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
        await fetch('/clear_chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: state.currentSession || '' })
        });
        document.getElementById('chatMessages').innerHTML = '';
    } catch (e) {
        console.error('清空失败:', e);
    }
}

export async function renameSession(sessionId) {
    const sid = sessionId || state.currentSession;
    const cur = (state.sessions || []).find(s => s.id === sid);
    const currentTitle = cur ? (cur.title || '') : '';
    const newTitle = prompt('改个名字:', currentTitle);
    if (!newTitle || newTitle.trim() === '') return;
    try {
        await fetch('/api/rename_session', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: sid, title: newTitle.trim() })
        });
        loadSessions();
    } catch (e) {
        alert('重命名失败: ' + e.message);
    }
}

export async function exportChat() {
    const format = document.getElementById('exportFormat').value;
    window.location.href = `/export_chat?format=${format}&session_id=${state.currentSession || ''}`;
}

export async function summarizeChat() {
    const mode = document.getElementById('llmSelect').value;
    try {
        const { appendMessage } = await import('./chat.js');
        appendMessage('assistant', '正在生成摘要...');
        const resp = await fetch(`/api/summarize?mode=${mode}&session_id=${state.currentSession || ''}`);
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
