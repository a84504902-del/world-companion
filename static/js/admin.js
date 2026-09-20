/* AI Companion - 后台管理逻辑 */

// 页面切换
document.querySelectorAll('.nav-item').forEach(item => {
    item.addEventListener('click', (e) => {
        e.preventDefault();
        const page = item.dataset.page;
        switchPage(page);
    });
});

function switchPage(page) {
    // 更新导航
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    document.querySelector(`[data-page="${page}"]`).classList.add('active');

    // 更新页面
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.getElementById(`page-${page}`).classList.add('active');

    // 加载数据
    loadPageData(page);
}

// 加载页面数据
function loadPageData(page) {
    switch(page) {
        case 'dashboard':
            loadDashboard();
            break;
        case 'llm':
            loadLLMConfig();
            break;
        case 'sessions':
            loadSessions();
            break;
        case 'memory':
            loadMemories();
            break;
        case 'database':
            loadDatabaseInfo();
            break;
    }
}

// 仪表盘
async function loadDashboard() {
    try {
        const resp = await fetch('/api/stats');
        const stats = await resp.json();
        document.getElementById('statSessions').textContent = stats.session_count;
        document.getElementById('statMessages').textContent = stats.message_count;
        document.getElementById('statMemories').textContent = stats.memory_count;
        document.getElementById('statPeople').textContent = stats.people_count;
    } catch (e) {
        console.error('加载统计失败:', e);
    }
}

// LLM 配置
async function loadLLMConfig() {
    try {
        const resp = await fetch('/api/llms');
        const data = await resp.json();

        if (!data.llms || data.llms.length === 0) {
            document.getElementById('llmList').innerHTML = '<p style="color: #999;">暂无 LLM 配置</p>';
            return;
        }

        document.getElementById('llmList').innerHTML = data.llms.map(llm => `
            <div class="llm-card">
                <div class="llm-info">
                    <h3>${escapeHtml(llm.name)}</h3>
                    <p>${escapeHtml(llm.desc)} · ${escapeHtml(llm.model)}</p>
                    <p style="font-size: 12px; color: #999;">${escapeHtml(llm.base_url)}</p>
                </div>
                <div style="display: flex; gap: 8px; align-items: center;">
                    <span class="badge ${llm.api_key ? 'badge-green' : 'badge-gray'}">
                        ${llm.api_key ? '已配置' : '未配置'}
                    </span>
                    <button class="btn btn-primary btn-sm" onclick="editLLM('${llm.id}')">编辑</button>
                    <button class="btn btn-danger btn-sm" onclick="deleteLLM('${llm.id}', ${!!llm.builtin})">${llm.builtin ? '清空' : '删除'}</button>
                </div>
            </div>
        `).join('');
    } catch (e) {
        console.error('加载 LLM 配置失败:', e);
    }
}

// 编辑 LLM
async function editLLM(id) {
    try {
        const resp = await fetch('/api/llms');
        const data = await resp.json();
        const llm = data.llms.find(m => m.id === id);
        if (!llm) return;

        document.getElementById('llmTestId').value = id;
        document.getElementById('llmName').value = llm.name;
        document.getElementById('llmBaseUrl').value = llm.base_url;
        document.getElementById('llmApiKey').value = llm.api_key || '';
        document.getElementById('llmModel').value = llm.model;
        document.getElementById('llmDesc').value = llm.desc || '';

        document.querySelector('#addLLMModal .modal-header h3').textContent = '编辑 LLM';
        document.getElementById('llmSubmitBtn').textContent = '保存';
        showAddLLMModal();
    } catch (e) {
        console.error('加载 LLM 信息失败:', e);
    }
}

// 删除/清空 LLM
async function deleteLLM(id, isBuiltin) {
    const action = isBuiltin ? '清空配置' : '删除';
    if (!confirm(`确定${action}？`)) return;
    try {
        await fetch('/api/llms/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id })
        });
        loadLLMConfig();
    } catch (e) {
        alert('操作失败: ' + e.message);
    }
}

// 会话管理
async function loadSessions() {
    try {
        const resp = await fetch('/api/sessions');
        const data = await resp.json();

        document.getElementById('sessionsTable').innerHTML = data.sessions.map(s => `
            <tr>
                <td><code>${s.id.substring(0, 16)}...</code></td>
                <td>${escapeHtml(s.title || '未命名对话')}</td>
                <td>${s.msg_count}</td>
                <td>${s.created || '-'}</td>
                <td>
                    <button class="btn btn-danger btn-sm" onclick="deleteSession('${s.id}')">删除</button>
                </td>
            </tr>
        `).join('');
    } catch (e) {
        console.error('加载会话失败:', e);
    }
}

// 记忆管理
async function loadMemories() {
    try {
        const resp = await fetch('/memories');
        const data = await resp.json();

        document.getElementById('memoryTable').innerHTML = data.memories.map(m => `
            <tr>
                <td>${m.id}</td>
                <td>${escapeHtml(m.content.substring(0, 50))}${m.content.length > 50 ? '...' : ''}</td>
                <td>${m.tags || '-'}</td>
                <td>${m.timestamp}</td>
                <td>
                    <button class="btn btn-danger btn-sm" onclick="deleteMemory(${m.id})">删除</button>
                </td>
            </tr>
        `).join('');
    } catch (e) {
        console.error('加载记忆失败:', e);
    }
}

// 数据库信息
async function loadDatabaseInfo() {
    try {
        const resp = await fetch('/api/database/info');
        const data = await resp.json();
        document.getElementById('dbFilePath').textContent = data.path || '-';
        document.getElementById('dbFileSize').textContent = data.size || '-';
        document.getElementById('dbTableCount').textContent = data.tables || '-';
    } catch (e) {
        console.error('加载数据库信息失败:', e);
    }
}

// 删除会话
async function deleteSession(id) {
    if (!confirm('确定删除这个会话？')) return;
    try {
        await fetch('/delete_session', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: id })
        });
        loadSessions();
    } catch (e) {
        alert('删除失败: ' + e.message);
    }
}

// 删除记忆
async function deleteMemory(id) {
    if (!confirm('确定删除这条记忆？')) return;
    try {
        await fetch('/delete_memory', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id: id })
        });
        loadMemories();
    } catch (e) {
        alert('删除失败: ' + e.message);
    }
}

// 压缩数据库
async function vacuumDatabase() {
    if (!confirm('确定压缩数据库？')) return;
    try {
        await fetch('/api/database/vacuum', { method: 'POST' });
        alert('压缩完成');
        loadDatabaseInfo();
    } catch (e) {
        alert('压缩失败: ' + e.message);
    }
}

// 导出数据
function exportDatabase() {
    window.open('/api/database/export');
}

// 显示添加 LLM 弹窗
function showAddLLMModal() {
    document.getElementById('addLLMModal').style.display = 'flex';
    document.getElementById('llmTestResult').innerHTML = '';
    if (!document.getElementById('llmTestId').value) {
        document.getElementById('llmName').value = '';
        document.getElementById('llmDesc').value = '';
        document.getElementById('llmBaseUrl').value = '';
        document.getElementById('llmApiKey').value = '';
        document.getElementById('llmModel').value = '';
        document.querySelector('#addLLMModal .modal-header h3').textContent = '添加 LLM';
        document.getElementById('llmSubmitBtn').textContent = '保存';
    }
}

// 隐藏添加 LLM 弹窗
function hideAddLLMModal() {
    document.getElementById('addLLMModal').style.display = 'none';
    document.getElementById('llmTestId').value = '';
}

// 测试 LLM
async function testLLM() {
    const baseUrl = document.getElementById('llmBaseUrl').value.trim();
    const apiKey = document.getElementById('llmApiKey').value.trim();
    const model = document.getElementById('llmModel').value.trim();

    if (!baseUrl || !model) {
        document.getElementById('llmTestResult').innerHTML = '<p style="color: red;">请填写 API 地址和模型名</p>';
        return;
    }

    document.getElementById('llmTestResult').innerHTML = '<p>正在测试...</p>';

    try {
        const resp = await fetch('/api/llms/test', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ base_url: baseUrl, api_key: apiKey, model })
        });
        const data = await resp.json();

        if (data.ok) {
            document.getElementById('llmTestResult').innerHTML = `<p style="color: green;">连接成功！回复: ${escapeHtml(data.reply)}</p>`;
        } else {
            document.getElementById('llmTestResult').innerHTML = `<p style="color: red;">${data.error}</p>`;
        }
    } catch (e) {
        document.getElementById('llmTestResult').innerHTML = `<p style="color: red;">${e.message}</p>`;
    }
}

// 保存 LLM
async function submitLLM() {
    const id = document.getElementById('llmTestId').value;
    const name = document.getElementById('llmName').value.trim();
    const desc = document.getElementById('llmDesc').value.trim();
    const baseUrl = document.getElementById('llmBaseUrl').value.trim();
    const apiKey = document.getElementById('llmApiKey').value.trim();
    const model = document.getElementById('llmModel').value.trim();

    if (!name || !baseUrl || !model) {
        document.getElementById('llmTestResult').innerHTML = '<p style="color: red;">请填写名称、API地址和模型名</p>';
        return;
    }

    try {
        const url = id ? '/api/llms/update' : '/api/llms/add';
        const body = { name, desc, base_url: baseUrl, api_key: apiKey, model };
        if (id) body.id = id;

        const resp = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        const data = await resp.json();

        if (data.ok) {
            hideAddLLMModal();
            loadLLMConfig();
        } else {
            document.getElementById('llmTestResult').innerHTML = `<p style="color: red;">${data.error}</p>`;
        }
    } catch (e) {
        document.getElementById('llmTestResult').innerHTML = `<p style="color: red;">${e.message}</p>`;
    }
}

// HTML 转义
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// 初始化
loadDashboard();
