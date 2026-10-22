// 角色与世界模板管理（v0.12.0）
//
// 界面结构：
//   右侧面板加一个「角色」tab —— 角色列表 + 世界模板 + 入口按钮
//   「引导角色」按钮打开引导弹窗 —— 和 AI 一句句聊，AI 帮你把角色想清楚
//   「快速建对话」按钮 —— 只给一句话，AI 读全部上下文自己生成全套人设
//
// 为什么要模板：世界观抽出来才能复用。以后新建对话时选一个模板，
// 确定下来又能存成新模板 —— 越用越省事。

import { escapeHtml } from './utils.js';

let personasCache = [];
let templatesCache = [];
let onboardingDraftId = null;

// ================================================================
// API
// ================================================================
async function api(path, body) {
    const opts = body
        ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
        : {};
    const resp = await fetch(path, opts);
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || `请求失败(${resp.status})`);
    return data;
}

const mode = () => {
    // 聊天页有模型下拉框，引导页没有 —— 引导页上取不到就回落到上次用过的模型名
    const sel = document.getElementById('llmSelect');
    return sel ? sel.value : (localStorage.getItem('guide_model') || 'deepseek');
};

// ================================================================
// 面板渲染
// ================================================================
export async function loadPersonas() {
    const panel = document.getElementById('personasPanel');
    if (!panel) return;
    panel.innerHTML = '<div class="empty-hint">加载中…</div>';
    try {
        const [p, t] = await Promise.all([api('/api/personas'), api('/api/world_templates')]);
        personasCache = p.personas || [];
        templatesCache = t.templates || [];
        await renderPersonaPanel(p);
    } catch (e) {
        panel.innerHTML = `<div class="empty-hint">加载失败：${escapeHtml(e.message)}</div>`;
    }
}

async function renderPersonaPanel(data) {
    const panel = document.getElementById('personasPanel');

    // v0.15.3：查人设生效来源。file = 人格库为空、人设来自 JSON 种子兜底——
    // 这种情况必须显式警告（以前静默回退，改 JSON 不生效也没人知道）
    let sourceBanner = '';
    try {
        const src = await api('/api/personas/active');
        if (src.source === 'file') {
            sourceBanner = `
                <div class="persona-source-warn">
                    <div class="psw-title">⚠️ 当前人设来自 JSON 文件兜底，人格库是空的</div>
                    <div>此时改 JSON 文件不会立即生效，后台里也没有角色可编辑。点下面按钮把种子文件导入人格库（导入前会自动备份现有库），之后就能在界面上维护人设了。</div>
                    <div class="psw-actions">
                        <button class="btn btn-sm btn-primary" onclick="importSeedFromFile()">从种子文件导入人格库</button>
                    </div>
                </div>`;
        }
    } catch (e) { /* 查不到来源不阻塞面板渲染 */ }

    const labelOf = (scope) => {
        if (!scope) return '未归属世界';
        const t = templatesCache.find(x => x.id === scope);
        return t ? t.name : scope;
    };
    // v0.17.5：角色按世界分组——一个世界一摞卡，不再混排
    const groups = new Map();
    for (const p of personasCache) {
        const key = p.world_scope || '';
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(p);
    }
    const cardOf = (p) => `
        <div class="pcard ${p.is_protagonist ? 'is-pro' : ''}" data-id="${escapeHtml(p.id)}">
            <div class="pcard-head">
                <span class="pcard-name">${escapeHtml(p.name)}</span>
                ${p.is_protagonist ? '<span class="pcard-badge-pro">主角</span>' : ''}
                ${p.age ? `<span class="pcard-age">${p.age}岁</span>` : ''}
            </div>
            <div class="pcard-traits">${(p.core_traits || []).slice(0, 5)
                .map(t => `<span class="trait-chip">${escapeHtml(t)}</span>`).join('')}</div>
            ${p.summary ? `<div class="pcard-sum">${escapeHtml(p.summary)}</div>` : ''}
            <div class="pcard-meta">${p.rule_count} 条规则 · ${p.example_count} 组语气样例</div>
            <div class="pcard-actions">
                <button class="btn btn-sm" onclick="switchToPersona('${escapeHtml(p.name)}')">去聊天</button>
                ${p.is_protagonist ? '' :
                    `<button class="btn btn-sm" onclick="setAsProtagonist('${escapeHtml(p.id)}')">设为主角</button>`}
                <button class="btn btn-sm" onclick="viewPersona('${escapeHtml(p.id)}')">详情</button>
                ${p.is_protagonist ? '' :
                    `<button class="btn btn-sm btn-danger-text" onclick="deletePersona('${escapeHtml(p.id)}','${escapeHtml(p.name)}')">删</button>`}
            </div>
        </div>`;
    const personaCards = [...groups.entries()].map(([scope, members]) => `
        <div class="pcard-world-group">
            <div class="pcard-world-head">
                <span class="pcard-world-title">🌍 ${escapeHtml(labelOf(scope))}</span>
                <span class="pcard-world-count">${members.length} 个角色</span>
            </div>
            <div class="pcard-world-cards">${members.map(cardOf).join('')}</div>
        </div>`).join('');

    const tplCards = templatesCache.map(t => `
        <div class="tcard" data-id="${escapeHtml(t.id)}">
            <div class="tcard-head">
                <span class="tcard-name">${escapeHtml(t.name)}</span>
                ${t.is_builtin ? '<span class="tcard-badge">内置</span>' : ''}
            </div>
            ${t.genre ? `<div class="tcard-meta">题材：${escapeHtml(t.genre)}${t.era ? ' · ' + escapeHtml(t.era) : ''}</div>` : ''}
            ${t.summary ? `<div class="tcard-sum">${escapeHtml(t.summary)}</div>` : ''}
            <div class="tcard-meta">${(t.cast_names && t.cast_names.length)
                ? `旗下角色：${t.cast_names.map(escapeHtml).join('、')}`
                : (t.used_by ? `${t.used_by} 个角色在用` : '暂无角色使用')}</div>
            <div class="tcard-actions">
                <button class="btn btn-sm btn-primary" onclick="useTemplate('${escapeHtml(t.id)}')">💬 开对话</button>
                <button class="btn btn-sm" onclick="duplicateTemplate('${escapeHtml(t.id)}')">复制改造</button>
                <button class="btn btn-sm" onclick="editTemplate('${escapeHtml(t.id)}')">编辑</button>
                ${t.is_builtin ? '' :
                    `<button class="btn btn-sm btn-danger-text" onclick="deleteTemplate('${escapeHtml(t.id)}')">删</button>`}
            </div>
        </div>`).join('');

    panel.innerHTML = `
        ${sourceBanner}
        <div class="panel-section">
            <div class="panel-section-head">
                <h4>角色 <span class="hint">${personasCache.length} 个</span></h4>
            </div>
            ${personaCards || '<div class="empty-hint">还没有角色</div>'}
        </div>
        <div class="panel-section">
            <div class="panel-actions-row">
                <button class="btn btn-primary btn-sm" onclick="openOnboarding()">🎨 引导建对话</button>
                <button class="btn btn-sm" onclick="openQuickCreate()">⚡ 快速建</button>
            </div>
            <p class="hint-line">「引导」跟 AI 一句句聊，把它想清楚；「快速」只说一句话，AI 读完全部上下文自己生成全套人设。</p>
        </div>
        <div class="panel-section">
            <div class="panel-section-head">
                <h4>世界模板 <span class="hint">${templatesCache.length} 个</span></h4>
            </div>
            ${tplCards || '<div class="empty-hint">还没有模板</div>'}
            <div class="panel-actions-row">
                <button class="btn btn-sm" onclick="newTemplate()">+ 新建世界模板</button>
            </div>
        </div>`;
}

// ================================================================
// 角色操作
// ================================================================
export async function importSeedFromFile() {
    if (!confirm('把 JSON 种子文件导入人格库？\n\n导入前会自动备份现有角色库；若已有主角，会用种子内容覆盖更新（id 和数据库专属字段保留）。')) return;
    try {
        const r = await api('/api/personas/import-seed', {});
        alert(r.reply);
        await loadPersonas();
    } catch (e) {
        alert('导入失败：' + e.message);
    }
}

export async function switchToPersona(name) {
    try {
        const r = await api('/chat', { text: `切换视角：${name}` });
        const { appendMessage } = await import('./chat.js');
        if (r.response && !r.response.includes('无法切换')) {
            // 成功：提示进消息流，并立刻重载历史（换视角后角色设定变了）
            appendMessage('assistant', r.response);
            const sid = (await import('./state.js')).state.currentSession || '';
            const h = await api(`/history?session_id=${sid}&limit=200`);
            const { renderMessages } = await import('./chat.js');
            renderMessages(h.history);
        } else {
            alert(r.response || '切换失败');
        }
        closeAllModals();
    } catch (e) {
        alert('切换失败：' + e.message);
    }
}

export async function setAsProtagonist(id) {
    try {
        const r = await api(`/api/personas/${id}/protagonist`, {});
        alert(r.reply);
        loadPersonas();
        notifyRefresh();
    } catch (e) { alert(e.message); }
}

export async function viewPersona(id) {
    try {
        const r = await api(`/api/personas/${id}`);
        const p = r.persona;
        const box = document.getElementById('personaDetailBody');
        document.getElementById('personaDetailName').textContent = p.name;
        box.innerHTML = `
            <div class="detail-sec"><b>一句话</b><br>${escapeHtml(p.summary || '-')}</div>
            <div class="detail-sec"><b>性格</b><br>${(p.core_traits || []).map(escapeHtml).join('、') || '-'}</div>
            <div class="detail-sec"><b>说话方式</b><br>${escapeHtml(p.speech_style || '-')}</div>
            <div class="detail-sec"><b>爱好</b><br>${escapeHtml(p.likes || '-')}<br>
                <b>雷区</b><br>${escapeHtml(p.dislikes || '-')}</div>
            ${p.bond_origin && Object.keys(p.bond_origin).length ? `
            <div class="detail-sec"><b>关系起点</b><br>${
                Object.entries(p.bond_origin).filter(([, v]) => v)
                    .map(([k, v]) => `${escapeHtml(k)}：${escapeHtml(String(v))}`).join('<br>')}</div>` : ''}
            ${p.rules && p.rules.length ? `
            <div class="detail-sec"><b>规则（${p.rules.length} 条）</b>
                <ul class="detail-list">${p.rules.map(r => `<li>${escapeHtml(r)}</li>`).join('')}</ul></div>` : ''}
            ${p.examples && p.examples.length ? `
            <div class="detail-sec"><b>语气样例</b>
                <ul class="detail-list">${p.examples.map(e =>
                    `<li><pre>${escapeHtml(e)}</pre></li>`).join('')}</ul></div>` : ''}
            ${p.anti_pattern ? `
            <div class="detail-sec"><b>禁忌</b><br>${escapeHtml(p.anti_pattern)}</div>` : ''}`;
        document.getElementById('personaDetailModal').style.display = 'flex';
    } catch (e) { alert(e.message); }
}

export async function deletePersona(id, name) {
    if (!confirm(`确定删除角色「${name}」？\n（对应的人格设定会一起删掉，人物卡保留）`)) return;
    try {
        await api(`/api/personas/${id}/delete`, {});
        loadPersonas();
        notifyRefresh();
    } catch (e) { alert(e.message); }
}

// ================================================================
// 对话式引导
// ================================================================
export async function openOnboarding(presetTplId) {
    // presetTplId：从世界模板点「开对话」进来时带的预设模板。
    // 带上它 = 这次引导的世界直接套这个模板，不用再从零描述。
    let preset = (presetTplId || '').trim();
    document.getElementById('onboardingModal').style.display = 'flex';
    const log = document.getElementById('onboardingLog');
    const input = document.getElementById('onboardingInput');
    log.innerHTML = '';
    input.value = '';
    input.disabled = true;

    try {
        const st = await api('/api/onboarding/status');
        if (st.active && st.collected && Object.keys(st.collected).length) {
            // 有进行中的草稿 → 直接续上，别从头问。
            // 但带了预设模板又撞上旧草稿时，必须让用户选：续旧的（模板不生效），
            // 还是扔掉旧的按这个模板重新开。
            if (preset) {
                const fresh = confirm('有一个没聊完的引导草稿。\n\n'
                    + '「确定」= 扔掉它，用这个模板重新开始\n'
                    + '「取消」= 接着聊那个草稿（这个模板不生效）');
                if (fresh) {
                    await api('/api/onboarding/abort', { draft_id: st.draft_id });
                } else {
                    preset = '';
                }
            }
            if (!preset) {
                onboardingDraftId = st.draft_id;
                addObMsg('ai', `上次引导到第 ${st.current_layer} 层，我们接着聊。`);
                (st.history || []).forEach(h => addObMsg(h.role === 'user' ? 'user' : 'ai', h.content));
                if (!st.history || !st.history.length) {
                    const r = await api('/api/onboarding/reply', {
                        draft_id: st.draft_id, text: '继续', mode: mode() });
                    addObMsg('ai', r.reply);
                }
                input.disabled = false;
                input.focus();
                return;
            }
        }
    } catch (e) { /* 没草稿是正常的 */ }

    setObBusy(true);
    try {
        const r = await api('/api/onboarding/start', {
            mode: mode(), world_template_id: preset, force_new: !!preset });
        onboardingDraftId = r.draft_id;
        addObMsg('ai', r.reply);
        // 亮出预设横幅：让用户知道这个世界是随模板来的，不用自己描述
        const banner = document.getElementById('obTplBanner');
        if (banner) {
            const t = (templatesCache || []).find(x => x.id === preset);
            if (t) {
                banner.textContent = `正在套用世界模板：${t.name}（世界这层不用再聊，落库时直接用它）`;
                banner.style.display = '';
            } else {
                banner.style.display = 'none';
            }
        }
    } catch (e) {
        addObMsg('ai', `出错了：${e.message}`);
    } finally {
        setObBusy(false);
        input.disabled = false;
        input.focus();
    }
}

export async function onboardingSend() {
    const input = document.getElementById('onboardingInput');
    const text = input.value.trim();
    if (!text || !onboardingDraftId) return;
    addObMsg('user', text);
    input.value = '';
    setObBusy(true);
    try {
        if (text === '预览' || text === '完成') {
            const r = await api('/api/onboarding/preview', {
                draft_id: onboardingDraftId, mode: mode() });
            addObMsg('ai', r.reply);
            showObFinishBar(true);
        } else {
            const r = await api('/api/onboarding/reply', {
                draft_id: onboardingDraftId, text, mode: mode() });
            addObMsg('ai', r.reply);
            if (r.ready_preview) {
                addObMsg('sys', '信息收得差不多了。说「预览」看我整理的设定对不对，或者直接说「完成」落库。');
            }
        }
    } catch (e) {
        addObMsg('ai', `出错了：${e.message}`);
    } finally {
        setObBusy(false);
        input.focus();
    }
}

function setObBusy(busy) {
    const btn = document.getElementById('onboardingSendBtn');
    if (btn) btn.textContent = busy ? '…' : '发送';
    document.getElementById('onboardingInput').disabled = busy;
}

function addObMsg(who, text) {
    const log = document.getElementById('onboardingLog');
    if (!log) return;
    const div = document.createElement('div');
    div.className = `ob-msg ob-${who}`;
    div.textContent = text || '';
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
}

function showObFinishBar(show) {
    const bar = document.getElementById('onboardingFinishBar');
    if (bar) bar.style.display = show ? 'flex' : 'none';
}

export async function onboardingFinish(saveAsTemplate = false) {
    if (!onboardingDraftId) return;
    setObBusy(true);
    try {
        const r = await api('/api/onboarding/finish', {
            draft_id: onboardingDraftId, mode: mode(),
            save_as_template: saveAsTemplate });
        addObMsg('ai', r.reply);
        onboardingDraftId = null;
        showObFinishBar(false);
        notifyRefresh();
        setTimeout(() => { closeAllModals(); loadPersonas(); }, 1200);
    } catch (e) {
        addObMsg('ai', `落库失败：${e.message}`);
    } finally { setObBusy(false); }
}

export async function onboardingAbort() {
    if (!onboardingDraftId) { closeAllModals(); return; }
    try {
        await api('/api/onboarding/abort', { draft_id: onboardingDraftId });
        onboardingDraftId = null;
        closeAllModals();
    } catch (e) { alert(e.message); }
}

// ================================================================
// 快速建对话（一句话）
// ================================================================
export async function openQuickCreate(presetTplId) {
    document.getElementById('quickCreateModal').style.display = 'flex';
    const tplSel = document.getElementById('quickTplSelect');
    // 引导页可能直接进来没先 loadPersonas()，缓存是空的 → 现拉一次
    if (!templatesCache.length) {
        try {
            const t = await api('/api/world_templates');
            templatesCache = t.templates || [];
        } catch (e) { /* 拉不到就只留"全新世界"这一项 */ }
    }
    // v0.17.5：世界各自独立，不选模板就是开一个全新世界（不再"沿用主角的世界"）
    tplSel.innerHTML = '<option value="">（不选，开一个全新世界）</option>' +
        templatesCache.map(t => `<option value="${escapeHtml(t.id)}">${escapeHtml(t.name)}</option>`).join('');
    // 从世界模板点「开对话」进来：预选上，并亮出横幅说明正在套用哪个世界
    const preset = (presetTplId || '').trim();
    const banner = document.getElementById('quickTplBanner');
    const presetTpl = preset ? templatesCache.find(x => x.id === preset) : null;
    if (presetTpl) tplSel.value = preset;
    if (banner) {
        if (presetTpl) {
            banner.textContent = `正在套用世界模板：${presetTpl.name}（世界设定已就位，下面的下拉想换也能换）`;
            banner.style.display = '';
        } else {
            banner.style.display = 'none';
        }
    }
    document.getElementById('quickNameInput').value = '';
    document.getElementById('quickSummaryInput').value = '';
    document.getElementById('quickNameInput').focus();
}

export async function quickCreateSubmit() {
    const name = document.getElementById('quickNameInput').value.trim();
    const summary = document.getElementById('quickSummaryInput').value.trim();
    const tpl = document.getElementById('quickTplSelect').value;
    if (!name) { alert('起个名字'); return; }
    if (!summary) { alert('一句话描述：这个人是谁、跟你什么关系'); return; }

    const btn = document.getElementById('quickCreateBtn');
    btn.disabled = true;
    btn.textContent = 'AI 正在想…（要十几秒）';
    try {
        const r = await api('/api/onboarding/quick_create', {
            name, summary, world_template_id: tpl,
            mode: mode() });
        closeAllModals();
        loadPersonas();
        notifyRefresh();
        // 引导页上建完直接进对话——「套模板开对话」的终点就是开聊，
        // 别让用户再回引导页点一次卡片。聊天页上没这个入口，保持原来的提示。
        if (r.ok && r.session_id && document.getElementById('guideMain')) {
            location.href = `/chat?s=${encodeURIComponent(r.session_id)}`;
            return;
        }
        alert(r.reply);
    } catch (e) {
        alert('生成失败：' + e.message);
    } finally {
        btn.disabled = false;
        btn.textContent = '生成';
    }
}

// ================================================================
// 世界模板管理
// ================================================================

/** 从世界模板直接套用开对话：打开快速建弹窗，模板预选上。
 *  引导页模板列表、聊天页角色面板的模板卡都走这个入口。 */
export async function useTemplate(tplId) {
    await openQuickCreate(tplId);
}

export async function newTemplate() { editTemplate(null); }

export async function editTemplate(id) {
    const isNew = !id;
    const t = isNew ? { name: '', genre: '', era: '', summary: '', factions: [], boundaries: [], customs: [] }
                    : templatesCache.find(x => x.id === id);
    document.getElementById('tplFormTitle').textContent = isNew ? '新建世界模板' : '编辑世界模板';
    document.getElementById('tplIdInput').value = isNew ? '' : t.id;
    document.getElementById('tplNameInput').value = t.name || '';
    document.getElementById('tplGenreInput').value = t.genre || '';
    document.getElementById('tplEraInput').value = t.era || '';
    document.getElementById('tplSummaryInput').value = t.summary || '';
    document.getElementById('tplFactionsInput').value = arrToLines(t.factions);
    document.getElementById('tplCustomsInput').value = arrToLines(t.customs);
    document.getElementById('tplBoundariesInput').value = arrToLines(t.boundaries);
    document.getElementById('tplFormModal').style.display = 'flex';
}

function arrToLines(v) {
    if (!v) return '';
    return Array.isArray(v) ? v.join('\n') : String(v);
}
function linesToArr(s) {
    return String(s || '').split('\n').map(x => x.trim()).filter(Boolean);
}

export async function saveTemplateForm() {
    const id = document.getElementById('tplIdInput').value;
    const body = {
        id: id || undefined,
        name: document.getElementById('tplNameInput').value.trim(),
        genre: document.getElementById('tplGenreInput').value.trim(),
        era: document.getElementById('tplEraInput').value.trim(),
        summary: document.getElementById('tplSummaryInput').value.trim(),
        factions: linesToArr(document.getElementById('tplFactionsInput').value),
        customs: linesToArr(document.getElementById('tplCustomsInput').value),
        boundaries: linesToArr(document.getElementById('tplBoundariesInput').value),
    };
    if (!body.name) { alert('模板要有名字'); return; }
    try {
        await api('/api/world_templates' + (id ? '/' + id : ''), body);
        closeAllModals();
        loadPersonas();
        notifyRefresh();
    } catch (e) { alert(e.message); }
}

export async function duplicateTemplate(id) {
    const t = templatesCache.find(x => x.id === id);
    if (!t) return;
    const name = prompt('复制一份，起个新名字（用来做自己的世界）：', t.name + ' 副本');
    if (!name) return;
    try {
        const r = await api(`/api/world_templates/${id}/duplicate?name=${encodeURIComponent(name)}`, {});
        loadPersonas();
        notifyRefresh();
        setTimeout(() => editTemplate(r.id), 300);
    } catch (e) { alert(e.message); }
}

export async function deleteTemplate(id) {
    const t = templatesCache.find(x => x.id === id);
    if (!t || !confirm(`确定删除模板「${t.name}」？`)) return false;
    try {
        await api(`/api/world_templates/${id}/delete`, {});
        loadPersonas();
        return true;   // 引导页靠这个返回值决定要不要收起模板列表弹层
    } catch (e) { alert(e.message); return false; }
}

// ================================================================
export function closeAllModals() {
    ['onboardingModal', 'quickCreateModal', 'tplFormModal', 'personaDetailModal']
        .forEach(id => {
            const m = document.getElementById(id);
            if (m) m.style.display = 'none';
        });
}

// ================================================================
// 给引导页用：把已加载的数据交出去，避免引导页再请求一遍
// ================================================================
export function setTemplates(list) {
    if (Array.isArray(list)) templatesCache = list;
}

export function getPersonas() {
    return personasCache;
}

/** 落库/新建完成后通知引导页刷新（聊天页里没有这个事件，忽略即可） */
function notifyRefresh() {
    try {
        window.dispatchEvent(new CustomEvent('guide:refresh'));
    } catch (e) { /* 老浏览器没有 CustomEvent，忽略 */ }
}