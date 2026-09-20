import * as state from './state.js';
import { escapeHtml } from './utils.js';

// --- 钦点记忆（"记住：X"写入的常驻层，置顶展示） ---
export async function loadPins() {
    try {
        const resp = await fetch(`/api/pins/list?session_id=${state.currentSession || ''}`);
        const data = await resp.json();
        return data.pins || [];
    } catch (e) {
        console.error('加载钦点记忆失败:', e);
        return [];
    }
}

export async function deletePin(id) {
    if (!confirm('删除这条钦点记忆？删除后 AI 将不再每轮知道这件事。')) return;
    try {
        await fetch('/api/pins/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id })
        });
        loadMemories();
    } catch (e) {
        alert('删除失败: ' + e.message);
    }
}

// --- 记忆 ---
export async function loadMemories() {
    try {
        const url = state.currentSession ? `/memories?session_id=${state.currentSession}` : '/memories';
        const resp = await fetch(url);
        const data = await resp.json();
        const pins = await loadPins();
        const panel = document.getElementById('memoryPanel');
        const hintHtml = '<div class="memory-hint">💡 <b>记住：内容</b> = 刻在石头上（每轮必在场，100% 可靠）；<b>保存记忆：内容</b> = 写在沙滩上（聊到相关话题时可能想起）</div>';

        let pinsHtml = '';
        if (pins.length > 0) {
            pinsHtml += '<div style="font-weight:600; font-size:12px; color:var(--accent, #c98a72); margin:10px 0 6px;">📌 钦点记忆（常驻生效）</div>';
            pinsHtml += pins.map(p => `
                <div class="memory-item">
                    <span class="memory-tag">${escapeHtml(p.slot)}</span>
                    <div class="memory-content" title="${escapeHtml(p.created_at)}">${escapeHtml(p.content)}</div>
                    <button class="person-del" onclick="deletePin(${p.id})" title="删除">×</button>
                </div>
            `).join('');
        }

        if (!data.memories || data.memories.length === 0) {
            if (pins.length === 0) {
                panel.innerHTML = hintHtml + '<p style="color: #999;">暂无记忆</p>';
            } else {
                panel.innerHTML = hintHtml + pinsHtml;
            }
            return;
        }
        panel.innerHTML = hintHtml + pinsHtml + data.memories.map(m => `
            <div class="memory-item" title="${escapeHtml(m.timestamp || '')}">
                <div class="memory-content">${escapeHtml(m.content)}</div>
                ${m.tags ? `<span class="memory-tag">${escapeHtml(m.tags)}</span>` : ''}
                <button class="person-del" onclick="deleteMemory(${m.id})" title="删除">×</button>
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

// --- 人物/关系模板（存一套配置，新对话一键导入） ---
export async function loadFamilyTemplates() {
    try {
        const resp = await fetch('/api/family_template/list');
        const data = await resp.json();
        return data.templates || [];
    } catch (e) {
        console.error('加载模板失败:', e);
        return [];
    }
}

export async function saveFamilyTemplate() {
    const name = document.getElementById('familyTplName')?.value.trim();
    if (!name) { alert('先给模板起个名字（如：两家家庭）'); return; }
    try {
        const resp = await fetch('/api/family_template/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, session_id: state.currentSession })
        });
        const data = await resp.json();
        if (data.ok) {
            alert(`已保存模板「${name}」：${data.people} 个人物、${data.relations} 条关系`);
            loadPeople();
        } else {
            alert(data.error || '保存失败');
        }
    } catch (e) {
        alert('保存失败: ' + e.message);
    }
}

export async function importFamilyTemplate() {
    const select = document.getElementById('familyTplSelect');
    const name = select?.value;
    if (!name) { alert('先选择要导入的模板'); return; }
    try {
        const resp = await fetch('/api/family_template/import', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, session_id: state.currentSession })
        });
        const data = await resp.json();
        if (data.ok) {
            const skip = data.skipped_people ? `（跳过已存在 ${data.skipped_people} 人）` : '';
            alert(`导入完成：新增 ${data.added_people} 人、${data.added_relations} 条关系${skip}`);
            loadPeople();
        } else {
            alert(data.error || '导入失败');
        }
    } catch (e) {
        alert('导入失败: ' + e.message);
    }
}

export async function deleteFamilyTemplate() {
    const select = document.getElementById('familyTplSelect');
    const name = select?.value;
    if (!name) { alert('先选择要删除的模板'); return; }
    if (!confirm(`删除模板「${name}」？（不影响已导入的人物和关系）`)) return;
    try {
        await fetch('/api/family_template/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name })
        });
        loadPeople();
    } catch (e) {
        alert('删除失败: ' + e.message);
    }
}

// --- 人物配置模块（导入/导出弹窗） ---
export async function loadPersonConfigModal() {
    const templates = await loadFamilyTemplates();
    const select = document.getElementById('pcTplSelect');
    if (select) {
        select.innerHTML = templates.map(t =>
            `<option value="${escapeHtml(t.name)}">${escapeHtml(t.name)}（${t.people_count}人/${t.relations_count}关系）</option>`
        ).join('') || '<option value="">暂无已保存的模板</option>';
    }
    const status = document.getElementById('pcStatus');
    if (status) status.textContent = '';
}

export function pcStatus(msg) {
    const el = document.getElementById('pcStatus');
    if (el) el.textContent = msg;
}

// 导出：下载 JSON 配置文件 + 同时存为模板
export function exportPersonConfig() {
    const name = document.getElementById('pcExportName')?.value.trim();
    if (!name) { alert('先给导出的配置起个名（会作为文件名）'); return; }
    pcStatus('正在导出…');
    fetch(`/api/family_template/save`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, session_id: state.currentSession })
    }).then(r => r.json()).then(data => {
        if (!data.ok) { alert(data.error || '导出失败'); return; }
        // 触发浏览器下载同名配置文件
        const a = document.createElement('a');
        a.href = `/api/family_template/export?session_id=${state.currentSession}&t=${Date.now()}`;
        a.download = `${name}.json`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        pcStatus(`✅ 已导出「${name}」（${data.people} 人 / ${data.relations} 关系），文件下载中…`);
    }).catch(e => pcStatus('导出失败: ' + e.message));
}

// 导出时顺便存为模板（复用保存接口，不触发下载）
export function savePersonConfigAsTemplate() {
    const name = document.getElementById('pcExportName')?.value.trim();
    if (!name) { alert('先给配置起个名'); return; }
    fetch('/api/family_template/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, session_id: state.currentSession })
    }).then(r => r.json()).then(data => {
        pcStatus(data.ok ? `✅ 已存为模板「${name}」（${data.people} 人 / ${data.relations} 关系）`
                         : (data.error || '保存失败'));
        loadPersonConfigModal();
    }).catch(e => pcStatus('保存失败: ' + e.message));
}

// 从上传的 JSON 文件导入
export function importFromFile(input) {
    // HTML onchange 可能不传参，自己找元素
    if (!input) input = document.getElementById('pcFileInput');
    const file = input && input.files && input.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = async () => {
        try {
            const data = JSON.parse(reader.result);
            const resp = await fetch('/api/family_template/import_data', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session_id: state.currentSession, data })
            });
            const result = await resp.json();
            if (result.ok) {
                pcStatus(`✅ 文件导入完成：新增 ${result.added_people} 人、${result.added_relations} 条关系`);
                await loadPeople();
            } else {
                pcStatus('❌ ' + (result.error || '导入失败'));
            }
        } catch (e) {
            pcStatus('❌ 文件解析失败：' + e.message);
        }
    };
    reader.readAsText(file, 'utf-8');
}

// --- 人物 ---
// 展开/收起人物卡编辑表单
export function togglePersonEdit(id) {
    const box = document.getElementById(`personEdit-${id}`);
    if (box) box.style.display = box.style.display === 'none' ? 'block' : 'none';
}

// 保存人物卡（只发有值的字段，空值不发——避免清空没填的字段）
export async function savePersonEdit(id) {
    const get = suffix => {
        const el = document.getElementById(`pe-${suffix}-${id}`);
        return el ? el.value.trim() : null;
    };
    const payload = { id };
    const age = get('age');
    if (age !== null && age !== '') payload.age = parseInt(age, 10) || 0;
    for (const f of ['birthday', 'occupation', 'personality', 'speech_style', 'likes', 'dislikes']) {
        const v = get(f);
        if (v !== null && v !== '') payload[f] = v;
    }
    const desc = get('desc');
    if (desc !== null && desc !== '') payload.description = desc;

    try {
        const resp = await fetch('/api/people/update', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await resp.json();
        if (data.ok) {
            await loadPeople();
        } else {
            alert(data.error || '保存失败');
        }
    } catch (e) {
        alert('保存失败: ' + e.message);
    }
}

// --- 人物 ---
export async function loadPeople() {
    try {
        const resp = await fetch(`/api/people/list?session_id=${state.currentSession}`);
        const data = await resp.json();
        const panel = document.getElementById('peoplePanel');

        // 添加区：名字+按钮一行，描述独占一行（窄面板塞三个框会挤爆）
        let html = `
            <div class="add-section">
                <input type="text" id="newPersonName" placeholder="名字" class="input-sm" style="flex:1;">
                <button class="btn btn-primary btn-sm" onclick="addPerson()" style="flex-shrink:0;">添加</button>
            </div>
            <div class="add-section">
                <input type="text" id="newPersonDesc" placeholder="描述：身份+性格+关系（如：夏雪姐姐的丈夫，话少稳重）" class="input-sm" style="width:100%;">
            </div>
            <div class="memory-hint">💡 描述写<b>身份+性格+和谁是什么关系</b>，AI 对话时会参考这些设定</div>
        `;

        if (!data.people || data.people.length === 0) {
            html += '<p style="color: #999; margin-top: 12px;">暂无人物</p>';
        } else {
            html += data.people.map(p => `
                <div class="person-item" onclick="togglePersonEdit(${p.id})" style="cursor:pointer;" title="点击编辑人物卡">
                    <div class="person-info">
                        <span class="person-name">${escapeHtml(p.name)}</span>
                        <span class="person-desc">${escapeHtml(p.description || '点击补全人物卡')}</span>
                    </div>
                    <button class="person-del" onclick="event.stopPropagation();deletePerson(${p.id})" title="删除">×</button>
                </div>
                <div class="person-edit" id="personEdit-${p.id}" style="display:none;">
                    <div class="add-section">
                        <input type="number" id="pe-age-${p.id}" placeholder="年龄" class="input-sm" value="${p.age || ''}" style="flex:1;">
                        <input type="text" id="pe-birthday-${p.id}" placeholder="生日（如：3月12日）" class="input-sm" value="${escapeHtml(p.birthday || '')}" style="flex:1.4;">
                    </div>
                    <div class="add-section">
                        <input type="text" id="pe-occupation-${p.id}" placeholder="身份/职业（如：工厂退休工人）" class="input-sm" value="${escapeHtml(p.occupation || '')}">
                    </div>
                    <div class="add-section">
                        <input type="text" id="pe-personality-${p.id}" placeholder="性格（如：严肃，话少，疼女儿）" class="input-sm" value="${escapeHtml(p.personality || '')}">
                    </div>
                    <div class="add-section">
                        <input type="text" id="pe-speech-${p.id}" placeholder="说话方式/口头禅（如：声如洪钟，爱讲道理）" class="input-sm" value="${escapeHtml(p.speech_style || '')}">
                    </div>
                    <div class="add-section">
                        <input type="text" id="pe-likes-${p.id}" placeholder="爱好（如：下棋、钓鱼）" class="input-sm" value="${escapeHtml(p.likes || '')}">
                    </div>
                    <div class="add-section">
                        <input type="text" id="pe-dislikes-${p.id}" placeholder="雷区（如：别提退休金）" class="input-sm" value="${escapeHtml(p.dislikes || '')}">
                    </div>
                    <div class="add-section">
                        <textarea id="pe-desc-${p.id}" placeholder="备注：结构装不下的都写这" class="input-sm" rows="2" style="height:auto; padding:8px 10px;">${escapeHtml(p.description || '')}</textarea>
                    </div>
                    <div class="add-section" style="justify-content:flex-end; margin-bottom:12px;">
                        <button class="btn btn-primary btn-sm" onclick="savePersonEdit(${p.id})">保存人物卡</button>
                    </div>
                </div>
            `).join('');
        }

        // 模板/导入导出已独立成「人物配置」弹窗，这里只留入口
        html += `
            <div style="margin-top:14px; border-top:1px solid var(--border); padding-top:10px;">
                <button class="btn btn-sm" style="width:100%;" onclick="openPersonConfigModal()">
                    📁 人物配置导入导出（模板 / 文件）
                </button>
            </div>
        `;
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

        let html = '<div class="memory-hint">💡 先在<b>「人物」tab</b>加人，再来这里连线。称呼会自动注入提示词，AI 照着叫</div>';

        // 两行布局：一行放不下降级为两行（右栏 292px，4 控件一行必挤爆）
        html += '<div class="add-section">';
        html += '<select id="relPersonA" class="input-sm" style="flex:1;"><option value="">人物A</option>';
        people.forEach(p => { html += `<option value="${p.id}">${escapeHtml(p.name)}</option>`; });
        html += '</select>';
        html += '<select id="relPersonB" class="input-sm" style="flex:1;"><option value="">人物B</option>';
        people.forEach(p => { html += `<option value="${p.id}">${escapeHtml(p.name)}</option>`; });
        html += '</select>';
        html += '</div>';
        html += '<div class="add-section">';
        html += '<input type="text" id="relType" placeholder="关系（如：父女、姐妹、夫妻）" class="input-sm" style="flex:1;" oninput="suggestRelCalls()">';
        html += '<button class="btn btn-primary btn-sm" onclick="addRelation()" style="flex-shrink:0;">添加</button>';
        html += '</div>';
        // 称呼：可手填，选常见关系词时自动预填建议值
        html += '<div class="add-section">';
        html += `<input type="text" id="relCallA" placeholder="A叫B（如：女儿）" class="input-sm" style="flex:1;">`;
        html += `<input type="text" id="relCallB" placeholder="B叫A（如：爸）" class="input-sm" style="flex:1;">`;
        html += '</div>';

        if (!data.relations || data.relations.length === 0) {
            html += '<p style="color: #999; margin-top: 12px;">暂无关系</p>';
        } else {
            html += data.relations.map(r => `
                <div class="relation-item">
                    <span>${escapeHtml(r.person_a_name)}<span class="rel-arrow">→</span><span class="relation-type">${escapeHtml(r.relation_type)}</span><span class="rel-arrow">→</span>${escapeHtml(r.person_b_name)}</span>
                    <button class="person-del" onclick="deleteRelation(${r.id})" title="删除">×</button>
                </div>
            `).join('');
        }
        panel.innerHTML = html;
    } catch (e) {
        console.error('加载关系失败:', e);
    }
}

// 常见关系的称呼建议值（选关系词时自动预填，可改；方向按 A→B 顺序假设）
const REL_CALL_SUGGESTIONS = {
    '父女': { a: '女儿', b: '爸' },
    '父子': { a: '儿子', b: '爸' },
    '母女': { a: '女儿', b: '妈' },
    '母子': { a: '儿子', b: '妈' },
    '兄妹': { a: '妹妹', b: '哥' },
    '姐弟': { a: '弟弟', b: '姐' },
    '姐妹': { a: '妹妹', b: '姐' },
    '夫妻': { a: '老婆', b: '老公' },
    '翁婿': { a: '小周', b: '爸' },
};

export function suggestRelCalls() {
    const type = document.getElementById('relType')?.value.trim() || '';
    const hit = REL_CALL_SUGGESTIONS[type];
    if (hit) {
        const a = document.getElementById('relCallA');
        const b = document.getElementById('relCallB');
        // 只在空着或已是建议值时才覆盖，不冲掉用户手填的
        if (a && (!a.value || Object.values(REL_CALL_SUGGESTIONS).some(s => s.a === a.value))) a.value = hit.a;
        if (b && (!b.value || Object.values(REL_CALL_SUGGESTIONS).some(s => s.b === b.value))) b.value = hit.b;
    }
}

export async function addRelation() {
    const personA = document.getElementById('relPersonA').value;
    const type = document.getElementById('relType').value.trim();
    const personB = document.getElementById('relPersonB').value;
    const callA = document.getElementById('relCallA')?.value.trim() || '';
    const callB = document.getElementById('relCallB')?.value.trim() || '';
    if (!personA || !type || !personB) { alert('请填写完整'); return; }
    try {
        await fetch('/api/relations/add', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                person_a_id: parseInt(personA),
                relation_type: type,
                person_b_id: parseInt(personB),
                call_a_to_b: callA,
                call_b_to_a: callB,
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
// 模型偏好是**会话级**的：对话1 可以固定用 DeepSeek、对话2 固定用 agnes。
// localStorage 里的 selected_llm 只作为"全局默认"（新建会话时继承）。
let _pendingModel = '';

function _tryApplyModel() {
    const select = document.getElementById('llmSelect');
    if (!select || !_pendingModel) return;
    if (Array.from(select.options).some(o => o.value === _pendingModel)) {
        select.value = _pendingModel;
        _pendingModel = '';
    }
}

export function applySessionModel(model) {
    if (!model) return;
    // 下拉框的选项可能还没加载完（会话数据和 /api/llms 是并行的），
    // 先记下来，等选项就绪后再应用
    _pendingModel = model;
    _tryApplyModel();
}

export async function saveSessionModel(model) {
    try {
        const { state } = await import('./state.js');
        await fetch('/api/session_model', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: state.currentSession || '', model })
        });
    } catch (e) {
        console.error('保存会话模型偏好失败:', e);
    }
}

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
            // 全局默认（新建会话时继承）+ 本会话记住的模型
            localStorage.setItem('selected_llm', select.value);
            saveSessionModel(select.value);
        });
        // 会话级模型优先于全局默认（会话数据可能早于这个列表就绪）
        _tryApplyModel();
    } catch (e) {
        console.error('加载 LLM 列表失败:', e);
    }
}
