// 未完结事件（她在等的事）—— 右栏面板
import * as state from './state.js';

let showClosed = false;

function escapeHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
}

function fmtDue(due) {
    if (!due) return '无期限（不会被催）';
    return due.slice(5, 16);
}

function isOverdue(due) {
    if (!due) return false;
    return new Date(due.replace(' ', 'T')) < new Date();
}

function statusText(s) {
    return { fulfilled: '已做到', expired: '没做到', dropped: '不了了之' }[s] || s;
}

export async function loadLoops() {
    try {
        const resp = await fetch(
            `/api/loops/list?session_id=${state.currentSession}&all=${showClosed ? '1' : '0'}`);
        const data = await resp.json();
        const panel = document.getElementById('loopsPanel');
        if (!panel) return;

        let html = '<div class="memory-hint">💡 她记着你答应过、还没兑现的事。'
            + '到期之后她会在对话里自己提起来（每轮最多一次）。也可以在这里手动添加。</div>';

        html += '<div class="add-section">';
        html += '<input type="text" id="newLoopContent" placeholder="她还在等什么" class="input-sm">';
        html += '<select id="newLoopWeight" class="input-sm">';
        html += '<option value="2">一般</option>';
        html += '<option value="3">认真说的</option>';
        html += '<option value="4">很在意</option>';
        html += '<option value="5">核心承诺</option>';
        html += '</select>';
        html += '<button class="btn btn-primary btn-sm" onclick="addLoop()">添加</button>';
        html += '</div>';

        const rows = data.loops || [];
        if (rows.length === 0) {
            html += '<p style="color:#999;margin-top:12px;">还没有任何承诺</p>';
        } else {
            html += rows.map(lp => {
                const done = lp.status !== 'pending';
                const overdue = !done && isOverdue(lp.due_at);
                const tag = done
                    ? `<span class="loop-tag closed">${statusText(lp.status)}</span>`
                    : (overdue
                        ? '<span class="loop-tag overdue">已过期</span>'
                        : '<span class="loop-tag">等待中</span>');
                const reminded = lp.reminded_count ? ` · 提过 ${lp.reminded_count} 次` : '';
                const actions = done
                    ? `<button class="btn btn-danger btn-sm" onclick="deleteLoop(${lp.id})">删除</button>`
                    : `<button class="btn btn-sm" onclick="closeLoop(${lp.id},'fulfilled')">做到了</button>`
                      + `<button class="btn btn-sm" onclick="closeLoop(${lp.id},'expired')">没做到</button>`
                      + `<button class="btn btn-danger btn-sm" onclick="deleteLoop(${lp.id})">删除</button>`;

                return `<div class="loop-item">
                    <div class="loop-content">${escapeHtml(lp.content)}</div>
                    <div class="loop-meta">${tag} 在意 ${lp.weight}/5 · 到期 ${fmtDue(lp.due_at)}${reminded}</div>
                    <div class="loop-actions">${actions}</div>
                </div>`;
            }).join('');
        }

        html += '<div class="loop-toggle">'
            + `<label><input type="checkbox" ${showClosed ? 'checked' : ''} onchange="toggleShowClosed()">`
            + ' 显示已完成的</label></div>';

        panel.innerHTML = html;
    } catch (e) {
        console.error('加载未完结事件失败:', e);
    }
}

export async function addLoop() {
    const el = document.getElementById('newLoopContent');
    const content = el && el.value ? el.value.trim() : '';
    if (!content) return;
    const w = document.getElementById('newLoopWeight');
    await fetch('/api/loops/add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            content,
            weight: w ? w.value : 2,
            session_id: state.currentSession,
        }),
    });
    loadLoops();
}

export async function closeLoop(id, status) {
    await fetch('/api/loops/close', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id, status }),
    });
    loadLoops();
}

export async function deleteLoop(id) {
    await fetch('/api/loops/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id }),
    });
    loadLoops();
}

export function toggleShowClosed() {
    showClosed = !showClosed;
    loadLoops();
}
