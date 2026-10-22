// 引导页（v0.13.0）
//
// 为什么要单独一页：
// 原来一打开就是聊天框 + 左栏会话列表，左栏被压在最左边，
// 想"回到上次那个对话"得先用眼睛去找那条。引导页把选择摆到台面上——
// 四块：继续上次 / 和谁聊 / 全部对话 / 新建。点哪张卡进哪个对话页。
//
// 引导、建对话、管模板这几个弹窗原本在聊天页里，现在引导页也有一套，
// 所以从 personas.js 里复用同一份实现（那边导出，这里导入）。

import { escapeHtml } from './utils.js';
import {
    openQuickCreate, quickCreateSubmit,
    newTemplate, editTemplate, saveTemplateForm,
    duplicateTemplate, viewPersona,
    setAsProtagonist, deletePersona, deleteTemplate,
    closeAllModals, setTemplates,
} from './personas.js';

let DATA = { sessions: [], personas: [], templates: [] };
let _lastModel = '';

// ================================================================
// 基础
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

/** 时间显示：今天只给时分，更早给日期 */
function fmtTime(ts) {
    if (!ts) return '';
    const d = new Date(String(ts).replace(' ', 'T'));
    if (isNaN(d.getTime())) return ts;
    const now = new Date();
    const sameYear = d.getFullYear() === now.getFullYear();
    const hm = d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
    if (sameYear && d.toDateString() === now.toDateString()) return `今天 ${hm}`;
    if (sameYear && d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth()) {
        return `${d.getMonth() + 1}月${d.getDate()}日 ${hm}`;
    }
    return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}`;
}

/** 进入某个对话页（统一出口：所有入口都走这里） */
async function enterSession(payload) {
    try {
        const r = await api('/api/guide/enter', payload);
        location.href = `/chat?s=${encodeURIComponent(r.session_id)}`;
    } catch (e) {
        alert('进入对话失败：' + e.message);
    }
}

// ================================================================
// 渲染
// ================================================================

function render() {
    renderContinue();
    renderPersonas();
    renderSessions();
    renderStats();
}

function renderContinue() {
    const sec = document.getElementById('secContinue');
    // 只挑真正聊过天的会话。空会话当"新建"用，不该占"继续上次"的位置。
    const usable = DATA.sessions.filter(s => s.msg_count > 0);
    if (!usable.length) { sec.style.display = 'none'; return; }
    sec.style.display = '';

    const s = usable[0];   // get_all_sessions 已按创建时间倒序，第一条即最新
    document.getElementById('ccTitle').textContent = s.title;
    document.getElementById('ccPreview').textContent = s.preview || '（还没有内容）';
    document.getElementById('ccMeta').textContent =
        `${fmtTime(s.created)} · ${s.msg_count} 条消息`;
    document.getElementById('ccBtn').onclick = () => enterSession({ session_id: s.id });
}

function renderPersonas() {
    const grid = document.getElementById('personaGrid');
    document.getElementById('pCount').textContent = `${DATA.personas.length} 个`;
    if (!DATA.personas.length) {
        grid.innerHTML = '<div class="empty-hint">还没有角色。用下面的「引导建对话」或「一句话建对话」建一个就能开聊。</div>';
        return;
    }
    // v0.17.5：按世界分组——一个世界一摞卡（模板 id 显示模板名），
    // 角色不再混排，避免看着像同一锅粥
    const labelOf = (scope) => {
        if (!scope) return '未归属世界';
        const t = DATA.templates.find(x => x.id === scope);
        return t ? t.name : scope;
    };
    const groups = new Map();
    for (const p of DATA.personas) {
        const key = p.world_scope || '';
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(p);
    }
    const cardOf = (p) => `
        <div class="gp-card ${p.is_protagonist ? 'is-pro' : ''}">
            <div class="gp-top">
                <span class="gp-name">${escapeHtml(p.name)}</span>
                ${p.is_protagonist ? '<span class="gp-badge">主角</span>' : ''}
            </div>
            ${p.summary ? `<div class="gp-sum">${escapeHtml(p.summary)}</div>` : ''}
            <div class="gp-traits">${(p.core_traits || []).slice(0, 4)
                .map(t => `<span class="trait-chip">${escapeHtml(t)}</span>`).join('')}
                ${p.session_count ? `<span class="trait-chip gp-chat-count">${p.session_count} 个对话</span>` : ''}
            </div>
            <div class="gp-actions">
                <button class="btn btn-primary btn-sm" onclick="talkTo('${escapeHtml(p.id)}')">开聊</button>
                <button class="btn btn-sm" onclick="viewPersona('${escapeHtml(p.id)}')">详情</button>
                ${p.is_protagonist ? '' :
                    `<button class="btn btn-sm" onclick="setAsProtagonist('${escapeHtml(p.id)}')">设为主角</button>`}
                ${p.is_protagonist ? '' :
                    `<button class="btn btn-sm btn-danger-text" onclick="deletePersona('${escapeHtml(p.id)}','${escapeHtml(p.name)}')">删</button>`}
            </div>
        </div>`;
    grid.innerHTML = [...groups.entries()].map(([scope, members]) => `
        <div class="gp-world-group">
            <div class="gp-world-head">
                <span class="gp-world-title">🌍 ${escapeHtml(labelOf(scope))}</span>
                <span class="gp-world-count">${members.length} 个角色</span>
            </div>
            <div class="gp-world-cards">${members.map(cardOf).join('')}</div>
        </div>`).join('');
}

function renderSessions() {
    const grid = document.getElementById('sessionGrid');
    document.getElementById('sCount').textContent = `${DATA.sessions.length} 个`;
    if (!DATA.sessions.length) {
        grid.innerHTML = '<div class="empty-hint">还没有对话记录。</div>';
        return;
    }
    grid.innerHTML = DATA.sessions.map(s => `
        <div class="gs-card ${s.msg_count ? '' : 'is-empty'}" onclick="openSession('${escapeHtml(s.id)}')">
            <div class="gs-head">
                <span class="gs-title">${escapeHtml(s.title)}</span>
                <span class="gs-del" title="删除这个对话"
                      onclick="event.stopPropagation(); deleteSessionGuide('${escapeHtml(s.id)}')">&times;</span>
                <span class="gs-count">${s.msg_count} 条</span>
            </div>
            <div class="gs-preview">${escapeHtml(s.preview || '（空对话）')}</div>
            <div class="gs-time">${fmtTime(s.created)}</div>
        </div>`).join('');
}

/** 删除对话卡片右上角的 ×：确认 → 删 → 刷新整页数据。
 *  与聊天页左栏的删除同接口（/delete_session），这边删完留在引导页。 */
export async function deleteSessionGuide(sessionId) {
    const s = DATA.sessions.find(x => x.id === sessionId);
    const title = s ? s.title : '这个对话';
    if (!confirm(`确定删除对话「${title}」？删除后不可恢复。`)) return;
    try {
        await api('/delete_session', { session_id: sessionId });
        await loadHome();
    } catch (e) {
        alert('删除失败：' + e.message);
    }
}

function renderStats() {
    const total = DATA.sessions.reduce((n, s) => n + s.msg_count, 0);
    document.getElementById('guideStats').textContent =
        `${DATA.sessions.length} 个对话 · ${DATA.personas.length} 个角色 · `
        + `${(DATA.worlds || []).length} 个世界 · ${DATA.templates.length} 个世界模板 · 共 ${total} 条消息`;
    const sub = document.getElementById('guideSub');
    if (DATA.protagonist_name) {
        sub.textContent = `当前主角：${DATA.protagonist_name}`;
    }
}

// ================================================================
// 入口动作（给 HTML 的 onclick 用）
// ================================================================

export function openSession(sessionId) {
    enterSession({ session_id: sessionId });
}

export function talkTo(personaId) {
    enterSession({ persona_id: personaId, model: _lastModel });
}

async function loadHome() {
    const main = document.getElementById('guideMain');
    try {
        DATA = await api('/api/guide/home');
        setTemplates(DATA.templates);
        render();
        // 记住上次用的模型，新会话默认沿用（引导页没有模型下拉框）
        try {
            const llms = await api('/api/llms');
            if (llms && llms.llms && llms.llms.length) _lastModel = llms.llms[0].id || '';
        } catch (e) { /* 拿不到就用默认 */ }
        if (_lastModel) {
            try { localStorage.setItem('guide_model', _lastModel); } catch (e) { /* 隐私模式 */ }
        }
        const st = await api('/api/status');
        const verEl = document.getElementById('appVersion');
        if (verEl && st.version) verEl.textContent = st.version;
    } catch (e) {
        main.innerHTML = `<div class="empty-hint">引导页加载失败：${escapeHtml(e.message)}</div>`;
    }
}

// ================================================================
// 表单建世界（v0.17.2）
// 设计原则：提问权收归人——五步固定字段，必填标 *，选留空 AI 补；
// 对话式引导已下线（AI 自由判断必漂，实测三轮全翻车）。
// ================================================================
const FW_STEPS = ['话题', '你自己', '对话对象', '关系', '初始人物'];
let fwStep = 1;
let fwDraftId = null;

function fwVal(id) {
    const el = document.getElementById(id);
    return el ? el.value.trim() : '';
}

function fwRender() {
    for (let i = 1; i <= FW_STEPS.length; i++) {
        document.getElementById('fwStep' + i).style.display = i === fwStep ? '' : 'none';
    }
    document.getElementById('fwStepper').textContent =
        `第 ${fwStep} / ${FW_STEPS.length} 步 · ${FW_STEPS[fwStep - 1]}`;
    document.getElementById('fwBackBtn').style.visibility = fwStep === 1 ? 'hidden' : '';
    document.getElementById('fwNextBtn').textContent =
        fwStep === FW_STEPS.length ? '生成预览 ✨' : '下一步 →';
}

function fwOpen() {
    fwStep = 1;
    fwDraftId = null;
    ['fwTitle', 'fwWorldName', 'fwPlace', 'fwUserName', 'fwUserRole',
     'fwCharName', 'fwCharTraits', 'fwRelationNote', 'fwCastDesc'].forEach(id => {
        document.getElementById(id).value = '';
    });
    document.getElementById('fwGenre').value = '';
    document.getElementById('fwRelation').value = '';
    document.getElementById('fwPreviewPanel').style.display = 'none';
    document.getElementById('fwPreviewText').textContent = '';
    document.getElementById('formWizardModal').style.display = 'flex';
    fwRender();
}

function fwClose() {
    document.getElementById('formWizardModal').style.display = 'none';
}

/** 当前步的必填校验；返回 false 时拦下 */
function fwValidateStep() {
    const need = fwStep === 1 ? [['fwTitle', '话题题目'], ['fwGenre', '题材']]
        : fwStep === 2 ? [['fwUserName', '你的名字']]
        : fwStep === 3 ? [['fwCharName', '对话对象的名字'], ['fwCharTraits', '性格气质']]
        : fwStep === 4 ? [['fwRelation', '关系']] : [];
    for (const [id, label] of need) {
        if (!fwVal(id)) { alert(`「${label}」还没填`); document.getElementById(id).focus(); return false; }
    }
    // 第 3 步防呆：对话对象不能跟用户自己重名（主次颠倒 = 今晚修过的 bug）
    if (fwStep === 3) {
        const uname = fwVal('fwUserName');
        if (uname && fwVal('fwCharName') === uname) {
            alert('对话对象不能和你自己同名——这个角色是 AI 来演的，不是你。');
            document.getElementById('fwCharName').focus();
            return false;
        }
    }
    return true;
}

function fwBack() {
    if (fwStep > 1) { fwStep--; fwRender(); }
}

function fwCollect() {
    return {
        L1: { title: fwVal('fwTitle'), world_name: fwVal('fwWorldName'),
              genre: fwVal('fwGenre'), place: fwVal('fwPlace') },
        L2: { user_name: fwVal('fwUserName'), user_role: fwVal('fwUserRole') },
        L3: { name: fwVal('fwCharName'), core_traits: fwVal('fwCharTraits') },
        L4: { relation: fwVal('fwRelation'), how_met: fwVal('fwRelationNote') },
        L5: { cast_desc: fwVal('fwCastDesc') },
    };
}

async function fwNext() {
    if (!fwValidateStep()) return;
    if (fwStep < FW_STEPS.length) { fwStep++; fwRender(); return; }
    await fwPreview();
}

/** 存草稿 → AI 生成预览（补全所有空格） */
async function fwPreview() {
    const btn = document.getElementById('fwNextBtn');
    btn.disabled = true; btn.textContent = 'AI 补全中…';
    try {
        const d = await api('/api/onboarding/form_draft', { collected: fwCollect() });
        fwDraftId = d.draft_id;
        const r = await api('/api/onboarding/preview', { draft_id: fwDraftId });
        document.getElementById('fwPreviewText').textContent = r.reply || '（AI 没返回内容）';
        document.getElementById('fwPreviewPanel').style.display = '';
        btn.textContent = '确认建好 ✅';
        btn.onclick = fwFinish;
    } catch (e) {
        alert(`预览失败：${e.message}`);
        btn.textContent = '生成预览 ✨';
    } finally {
        btn.disabled = false;
    }
}

/** 落库 → 成功面板 → 一键开聊 */
async function fwFinish() {
    const btn = document.getElementById('fwNextBtn');
    btn.disabled = true; btn.textContent = '落库中…';
    try {
        const r = await api('/api/onboarding/finish', { draft_id: fwDraftId });
        if (!r.ok && r.reply) { alert(r.reply); btn.textContent = '确认建好 ✅'; return; }
        document.getElementById('fwPreviewText').textContent = r.reply || '建好了';
        btn.textContent = '开始聊天 →';
        btn.onclick = () => { fwClose(); openSession(r.session_id); };
        window.dispatchEvent(new Event('guide:refresh'));
    } catch (e) {
        alert(`落库失败：${e.message}`);
        btn.textContent = '确认建好 ✅';
    } finally {
        btn.disabled = false;
    }
}

// 暴露给 HTML 内联 onclick
Object.assign(window, { fwOpen, fwClose, fwNext, fwBack });

document.addEventListener('DOMContentLoaded', async () => {
    await loadHome();

    document.getElementById('btnNewChat').onclick = () => enterSession({ model: _lastModel });
    document.getElementById('btnGuide').onclick = () => fwOpen();
    document.getElementById('btnQuick').onclick = () => openQuickCreate();
    document.getElementById('btnTemplate').onclick = () => showTemplates();

    // 建完角色/模板后回引导页刷新，不用手动 F5
    window.addEventListener('guide:refresh', loadHome);
});

// ================================================================
// 世界模板：引导页上一个轻量列表（完整编辑还是复用 personas.js 的弹窗）
// ================================================================

async function showTemplates() {
    closeAllModals();
    const box = document.createElement('div');
    box.className = 'modal';
    box.id = 'tplListModal';   // useTemplate 要收掉这个临时弹层，给个 id
    box.style.display = 'flex';
    box.innerHTML = `
        <div class="modal-content" style="width: 560px; max-height: 86vh; overflow-y: auto;">
            <div class="modal-header">
                <h3>🌍 世界模板</h3>
                <button class="modal-close" onclick="this.closest('.modal').remove()">&times;</button>
            </div>
            <div class="modal-body">
                <p class="prompt-hint">点「开对话」直接套用这个模板建一段新对话：填个名字、一句话描述，AI 会按这个世界把人物补全。</p>
                <div class="tpl-list">
                    ${DATA.templates.map(t => `
                        <div class="tpl-row">
                            <div class="tpl-info">
                                <div class="tpl-name">${escapeHtml(t.name)}
                                    ${t.genre ? `<span class="hint">${escapeHtml(t.genre)}</span>` : ''}</div>
                                ${t.summary ? `<div class="tpl-sum">${escapeHtml(t.summary)}</div>` : ''}
                                ${(t.cast_names && t.cast_names.length)
                                    ? `<div class="tpl-cast">旗下角色：${t.cast_names.map(escapeHtml).join('、')}</div>`
                                    : ''}
                            </div>
                            <div class="tpl-btns">
                                <button class="btn btn-sm btn-primary" onclick="useTemplate('${escapeHtml(t.id)}')">💬 开对话</button>
                                <button class="btn btn-sm" onclick="duplicateTemplate('${escapeHtml(t.id)}')">复制改造</button>
                                <button class="btn btn-sm" onclick="editTemplate('${escapeHtml(t.id)}')">编辑</button>
                                ${t.is_builtin ? '' :
                                    `<button class="btn btn-sm btn-danger-text" onclick="deleteTemplateGuide('${escapeHtml(t.id)}')">删</button>`}
                            </div>                        </div>`).join('') || '<div class="empty-hint">还没有模板</div>'}
                </div>
                <div class="form-actions">
                    <button class="btn btn-primary" onclick="newTemplate()">+ 新建世界模板</button>
                    <button class="btn" onclick="this.closest('.modal').remove()">关闭</button>
                </div>
            </div>
        </div>`;
    document.body.appendChild(box);
    // 点遮罩关闭这个临时列表
    box.addEventListener('click', (e) => { if (e.target === box) box.remove(); });
}

/** 套用模板开对话：先收掉模板列表这个临时弹层（不在 closeAllModals 管的四个之内），
 *  再打开预选了该模板的快速建弹窗。 */
function useTemplate(tplId) {
    const box = document.getElementById('tplListModal');
    if (box) box.remove();
    openQuickCreate(tplId);
}

/** 引导页上的模板删除：复用 personas.js 的删除逻辑（confirm + 接口 + 刷新事件），
 *  成功后收掉这个临时列表弹层；失败（内置/有角色在用）就留着让用户重试。 */
async function deleteTemplateGuide(tplId) {
    const ok = await deleteTemplate(tplId);
    if (ok) {
        const box = document.getElementById('tplListModal');
        if (box) box.remove();
    }
}

// 暴露给 HTML 内联 onclick
Object.assign(window, {
    openSession, talkTo, useTemplate, deleteTemplateGuide,
    deleteSessionGuide,
    openQuickCreate, quickCreateSubmit,
    newTemplate, editTemplate, saveTemplateForm,
    duplicateTemplate, viewPersona,
    setAsProtagonist, deletePersona, closeAllModals,
});
