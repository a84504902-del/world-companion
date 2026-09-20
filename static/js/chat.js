import * as state from './state.js';
import { escapeHtml, renderMarkdown } from './utils.js';

// 当前流式请求的 AbortController
let abortController = null;

// 回复长度模式：short / normal / long
let lengthMode = localStorage.getItem('lengthMode') || 'normal';

export function setLengthMode(mode) {
    lengthMode = mode;
    localStorage.setItem('lengthMode', mode);
    document.querySelectorAll('.length-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.mode === mode);
    });
    // 显示模式提示
    const hint = document.getElementById('lengthModeHint');
    if (hint) {
        const labels = { short: '简短模式', normal: '标准模式', long: '详细模式' };
        hint.textContent = labels[mode] || '';
        hint.style.display = 'inline';
        setTimeout(() => hint.style.display = 'none', 2000);
    }
    // 更新共享状态
    state.setLengthModeState(mode);
}

export function getLengthMode() {
    return lengthMode;
}

// 群像模式：🎭 开关，开启后人物表里的角色可以同场出场说话
let groupMode = localStorage.getItem('groupMode') === '1';

export function toggleGroupMode() {
    groupMode = !groupMode;
    localStorage.setItem('groupMode', groupMode ? '1' : '0');
    const btn = document.getElementById('groupModeBtn');
    if (btn) {
        btn.classList.toggle('active', groupMode);
        btn.title = groupMode ? '群像模式：已开启（点此关闭）' : '群像模式：人物表里的角色可以同场出场说话';
    }
}

export function getGroupMode() {
    return groupMode;
}

// 页面加载时恢复 🎭 按钮的点亮状态
export function restoreGroupModeButton() {
    const btn = document.getElementById('groupModeBtn');
    if (btn && groupMode) {
        btn.classList.add('active');
        btn.title = '群像模式：已开启（点此关闭）';
    }
}

export async function loadHistory() {
    try {
        const resp = await fetch('/history');
        const data = await resp.json();
        renderMessages(data.history);
        // 恢复这个会话记住的模型（对话1 用 DeepSeek、对话2 用 agnes）
        const mem = await import('./memory.js');
        mem.applySessionModel(data.model);
    } catch (e) {
        console.error('加载历史失败:', e);
    }
    // 看她有没有在你不在的时候留过言（够了就补几条进来）
    checkProactive();
}

export async function checkProactive() {
    try {
        const mode = (document.getElementById('llmSelect') || {}).value || 'deepseek';
        const resp = await fetch('/api/catchup', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: state.currentSession || '', mode }),
        });
        const d = await resp.json();
        const msgs = d.messages || [];
        if (!msgs.length) return;

        console.log('[catchup] 她留了', msgs.length, '条言');
        // 重新加载历史，让留言按时间出现在正确的位置
        const r2 = await fetch('/history');
        const d2 = await r2.json();
        renderMessages(d2.history);
        loadStatusBar();
    } catch (e) {
        console.error('检查留言失败:', e);
    }
}

function dividerHtml(prevTs, curTs) {
    // 让"日子在过"看得见：跨天显示日期，同天间隔久显示几小时后
    if (!prevTs || !curTs) return '';
    const a = new Date(String(prevTs).replace(' ', 'T'));
    const b = new Date(String(curTs).replace(' ', 'T'));
    if (isNaN(a.getTime()) || isNaN(b.getTime())) return '';

    const diff = (b - a) / 1000;
    if (diff < 3600) return '';                       // 1 小时内不插

    if (a.toDateString() !== b.toDateString()) {
        const label = b.toLocaleDateString('zh-CN', { month: 'long', day: 'numeric' });
        return `<div class="time-divider"><span>${label}</span></div>`;
    }
    const h = Math.floor(diff / 3600);
    return `<div class="time-divider gap"><span>${h} 小时后</span></div>`;
}

export function renderMessages(history) {
    const container = document.getElementById('chatMessages');
    let html = '';
    let prevTs = null;
    for (const msg of history || []) {
        html += dividerHtml(prevTs, msg.timestamp);
        const fromHer = msg.role === 'assistant' || msg.role === 'proactive';
        const content = fromHer ? renderMarkdown(stripState(msg.content)) : escapeHtml(msg.content);
        const timeStr = msg.timestamp ? formatTime(msg.timestamp) : '';
        // proactive = 她单方面发来的留言，加个类做视觉区分
        const cls = msg.role === 'proactive'
            ? 'message assistant proactive'
            : `message ${msg.role}`;
        html += `<div class="${cls}"><div class="message-content">${content}</div>`
            + `${timeStr ? `<div class="message-time">${timeStr}</div>` : ''}</div>`;
        if (msg.timestamp) prevTs = msg.timestamp;
    }
    container.innerHTML = html;
    container.scrollTop = container.scrollHeight;
    loadStatusBar();
}

// 状态条数据 + 承诺轮播
let _sbTime = null;
let _sbPending = [];
let _sbScene = null;
let _sbIdx = 0;
let _sbTimer = null;

function renderStatusBar() {
    const bar = document.getElementById('statusBar');
    if (!bar) return;
    const t = _sbTime || {};
    const s = _sbScene || {};
    const parts = [];
    if (t.story_day) {
        parts.push(`<span class="sb-story">第 ${t.story_day} 天 · ${t.story_weekday || ''} `
            + `${t.story_time || ''} ${t.story_period || ''}</span>`);
    }
    // 场景：她在哪、在干嘛（模型每轮附带，后端解析）
    if (s.place) {
        const doing = s.her_doing ? ` · ${escapeHtml(s.her_doing)}` : '';
        parts.push(`<span>${escapeHtml(s.place)}${doing}</span>`);
    }
    if (t.real_time) parts.push(`<span>现实 ${t.real_weekday || ''} ${t.real_time}</span>`);
    if (t.span_days >= 1) parts.push(`<span>在一起 ${t.span_days} 天</span>`);
    if (_sbPending.length) {
        const p = _sbPending[_sbIdx % _sbPending.length];
        parts.push(`<span class="sb-pending">她还在等你：${escapeHtml(p)}</span>`);
    }
    bar.innerHTML = parts.join('<span class="sb-dot">·</span>');
}

export async function loadStatusBar() {
    const bar = document.getElementById('statusBar');
    if (!bar) return;
    try {
        const resp = await fetch(`/api/status?session_id=${state.currentSession || ''}`);
        const d = await resp.json();
        _sbTime = d.time || {};
        _sbPending = d.pending || [];
        _sbScene = d.scene || null;
        _sbIdx = 0;
        renderStatusBar();
        // 版本号（左栏底部）
        const verEl = document.getElementById('appVersion');
        if (verEl && d.version) verEl.textContent = d.version;
        // 多条承诺时轮播：innerHTML 重建会让 sb-pending 重新播淡入动画
        if (!_sbTimer) {
            _sbTimer = setInterval(() => {
                if (_sbPending.length > 1) {
                    _sbIdx = (_sbIdx + 1) % _sbPending.length;
                    renderStatusBar();
                }
            }, 5000);
        }
    } catch (e) {
        bar.innerHTML = '';
    }
}

function stripState(text) {
    // 去掉模型附带的状态标记（[[STATE]] 之后的内容），用户不该看到它
    if (!text) return text || '';
    const i = text.indexOf('[[STATE]]');
    return i >= 0 ? text.slice(0, i).replace(/\s+$/, '') : text;
}

function localStamp() {
    // 本地时间戳。注意：不能用 new Date().toISOString()，那是 UTC，会差 8 小时
    const d = new Date();
    const p = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} `
        + `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function formatTime(timestamp) {
    if (!timestamp) return '';
    try {
        const date = new Date(timestamp.replace(' ', 'T'));
        if (isNaN(date.getTime())) return timestamp;
        const now = new Date();
        const hm = date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        const sameDay = date.getFullYear() === now.getFullYear()
            && date.getMonth() === now.getMonth()
            && date.getDate() === now.getDate();
        // 跨天的消息带上日期，否则几天的对话看起来像连续发生
        if (sameDay) return hm;
        return `${date.getMonth() + 1}月${date.getDate()}日 ${hm}`;
    } catch {
        return timestamp;
    }
}

export function appendMessage(role, content, audioUrl, timestamp) {
    const container = document.getElementById('chatMessages');
    const div = document.createElement('div');
    div.className = `message ${role}`;

    let audioBtns = '';
    if (role === 'assistant' && audioUrl) {
        audioBtns = `
            <button class="btn-audio" onclick="playAudio('${audioUrl}')" title="重播">🔊</button>
            <button class="btn-audio btn-stop" onclick="stopAudio()" title="停止">⏹</button>
        `;
    }

    const timeStr = timestamp ? formatTime(timestamp) : '';
    const renderedContent = role === 'assistant' ? renderMarkdown(content) : escapeHtml(content);
    div.innerHTML = `<div class="message-content">${renderedContent}<div class="audio-btns">${audioBtns}</div></div>${timeStr ? `<div class="message-time">${timeStr}</div>` : ''}`;
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
    return div;
}

// 创建流式消息占位（显示光标动画）
function createStreamingMessage() {
    const container = document.getElementById('chatMessages');
    const div = document.createElement('div');
    div.className = 'message assistant';
    // 先显示「正在输入」，等首个 token 到了再换成正文和光标
    div.innerHTML = '<div class="message-content">'
        + '<span class="typing-indicator"><i></i><i></i><i></i></span>'
        + '<span class="streaming-text"></span>'
        + '<span class="streaming-cursor" style="display: none;">▌</span>'
        + '</div>';
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
    return div.querySelector('.streaming-text');
}

// 完成流式消息（移除光标，渲染 Markdown）
function finalizeStreamingMessage(div, fullText, audioUrl, timestamp) {
    const contentDiv = div.querySelector('.message-content');
    let audioBtns = '';
    if (audioUrl) {
        audioBtns = `
            <button class="btn-audio" onclick="playAudio('${audioUrl}')" title="重播">🔊</button>
            <button class="btn-audio btn-stop" onclick="stopAudio()" title="停止">⏹</button>
        `;
    }
    const timeStr = timestamp ? formatTime(timestamp) : '';
    contentDiv.innerHTML = renderMarkdown(stripState(fullText)) + `<div class="audio-btns">${audioBtns}</div>`;
    // 添加时间戳
    const timeDiv = div.querySelector('.message-time');
    if (!timeDiv && timeStr) {
        const newTimeDiv = document.createElement('div');
        newTimeDiv.className = 'message-time';
        newTimeDiv.textContent = timeStr;
        div.appendChild(newTimeDiv);
    } else if (timeDiv && timeStr) {
        timeDiv.textContent = timeStr;
    }
}

// 显示/隐藏停止生成按钮
function updateSendButton() {
    const sendBtn = document.querySelector('.btn-send');
    if (!sendBtn) return;
    if (state.isStreaming) {
        sendBtn.textContent = '停止';
        sendBtn.classList.add('btn-stop-gen');
        sendBtn.onclick = () => cancelGeneration();
    } else {
        sendBtn.textContent = '发送';
        sendBtn.classList.remove('btn-stop-gen');
        sendBtn.onclick = () => sendMessage();
    }
}

// 打断生成：停止 LLM 流式输出 + 停止 TTS
export async function cancelGeneration() {
    // 停止 TTS
    stopAudio();

    // 中止流式请求
    if (abortController) {
        abortController.abort();
        abortController = null;
    }

    // 通知后端取消
    try {
        await fetch('/api/cancel_chat', { method: 'POST' });
    } catch (e) {
        // 忽略
    }

    state.setIsStreaming(false);
    updateSendButton();
}

// 发送消息（支持流式输出 + 打断）
export async function sendMessage() {
    const input = document.getElementById('messageInput');
    const text = input.value.trim();
    if (!text) return;

    // 如果正在生成中，先打断
    if (state.isStreaming) {
        await cancelGeneration();
        // 短暂等待让中止生效
        await new Promise(r => setTimeout(r, 100));
    }

    // 停止当前 TTS 播放
    stopAudio();

    const mode = document.getElementById('llmSelect').value;
    input.value = '';
    // 输入框收回单行高度
    input.style.height = 'auto';
    input.style.overflowY = 'hidden';

    // 显示用户消息（带时间戳）
    appendMessage('user', text, null, localStamp());

    // 创建 AI 流式消息占位
    const streamEl = createStreamingMessage();
    const msgDiv = streamEl.closest('.message');
    let fullResponse = '';
    let responseTimestamp = '';
    let hasFirstToken = false;

    // 设置 AbortController
    abortController = new AbortController();
    state.setIsStreaming(true);
    updateSendButton();

    console.log('[sendMessage] 发送请求，参数:', { text, mode, length_mode: lengthMode, group_mode: groupMode });

    try {
        const resp = await fetch('/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text, mode, length_mode: lengthMode, group_mode: groupMode }),
            signal: abortController.signal
        });

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let audioUrl = null;
        let summary = null;
        let wasCancelled = false;
        let hasError = null;

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n\n');
            buffer = lines.pop() || '';

            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    try {
                        const eventData = JSON.parse(line.substring(6));
                        if (eventData.content) {
                            if (!hasFirstToken) {
                                hasFirstToken = true;
                                const t = msgDiv.querySelector('.typing-indicator');
                                if (t) t.remove();
                                const c = msgDiv.querySelector('.streaming-cursor');
                                if (c) c.style.display = '';
                            }
                            fullResponse += eventData.content;
                            streamEl.textContent = stripState(fullResponse);
                            const container = document.getElementById('chatMessages');
                            container.scrollTop = container.scrollHeight;
                        }
                        if (eventData.error) {
                            hasError = eventData.error;
                            streamEl.textContent = '错误: ' + eventData.error;
                        }
                        if (eventData.done) {
                            audioUrl = eventData.audio_url;
                            summary = eventData.summary;
                            wasCancelled = eventData.cancelled || false;
                            responseTimestamp = eventData.timestamp || '';
                        }
                    } catch (e) {
                        // 忽略 JSON 解析错误
                    }
                }
            }
        }

        // 流完成
        abortController = null;
        state.setIsStreaming(false);
        updateSendButton();
        loadStatusBar();      // 刷新状态条（场景/承诺可能变了）
        checkProactive();     // 她可能在你不在时留过言（无留言时几乎零开销）

        if (wasCancelled && fullResponse) {
            // 被打断：显示已生成的部分
            finalizeStreamingMessage(msgDiv, fullResponse + ' *(已打断)*', null);
        } else if (wasCancelled && !fullResponse) {
            // 还没生成内容就被打断：移除占位消息
            msgDiv.remove();
        } else if (hasError) {
            // 后端返回错误：显示错误信息，不覆盖为空白
            finalizeStreamingMessage(msgDiv, '⚠️ ' + hasError, null);
        } else {
            // 正常完成
            finalizeStreamingMessage(msgDiv, fullResponse, audioUrl, responseTimestamp);
            if (summary) {
                appendMessage('assistant', `📝 **对话摘要：** ${summary}`, null, responseTimestamp);
            }
            // 延迟检测人物提案（后端 LLM 调用需要时间）
            setTimeout(() => checkCharacterProposals(), 3000);
            if (audioUrl && state.ttsEnabled) {
                playAudio(audioUrl);
            } else if (state.autoMode) {
                // 没有音频或 TTS 未开启，但处于自动模式：延迟重启语音识别
                setTimeout(() => {
                    const { startAutoListen } = window;
                    if (startAutoListen) startAutoListen();
                }, 500);
            }
        }

    } catch (e) {
        abortController = null;
        state.setIsStreaming(false);
        updateSendButton();

        if (e.name === 'AbortError') {
            // 用户主动取消
            if (fullResponse) {
                finalizeStreamingMessage(msgDiv, fullResponse + ' *(已打断)*', null);
            } else {
                msgDiv.remove();
            }
        } else {
            finalizeStreamingMessage(msgDiv, '发送失败: ' + e.message, null);
        }
    }
}

// 保存消息为记忆
export async function saveMessageAsMemory(content) {
    try {
        await fetch('/api/save_memory_from_message', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                content: content,
                session_id: state.currentSession,
                tags: '手动标记'
            })
        });
        alert('已保存为记忆');
    } catch (e) {
        alert('保存失败: ' + e.message);
    }
}

export function playAudio(url) {
    stopAudio();
    const bustUrl = url + (url.includes('?') ? '&' : '?') + 't=' + Date.now();
    const audio = new Audio(bustUrl);
    state.setCurrentAudio(audio);
    audio.onended = () => {
        state.setCurrentAudio(null);
        if (state.autoMode) {
            const { startAutoListen } = window;
            if (startAutoListen) setTimeout(startAutoListen, 300);
        }
    };
    audio.onerror = () => {
        console.error('音频播放失败');
        state.setCurrentAudio(null);
        if (state.autoMode) {
            const { startAutoListen } = window;
            if (startAutoListen) setTimeout(startAutoListen, 500);
        }
    };
    audio.play().catch(e => {
        console.log('播放失败:', e);
        state.setCurrentAudio(null);
        if (state.autoMode) {
            const { startAutoListen } = window;
            if (startAutoListen) setTimeout(startAutoListen, 500);
        }
    });
}

export function stopAudio() {
    if (state.currentAudio) {
        state.currentAudio.pause();
        state.currentAudio.currentTime = 0;
        state.setCurrentAudio(null);
    }
}

// 检测人物提案（AI 后台检测新人物 → 用户确认后入库）
async function checkCharacterProposals() {
    try {
        const resp = await fetch('/api/proposals');
        const data = await resp.json();
        if (!data.proposals || data.proposals.length === 0) return;

        for (const p of data.proposals) {
            const desc = p.description ? `（${p.description}）` : '';
            const accept = confirm(`🔍 AI 检测到新人物：${p.name}${desc}\n\n是否添加到人物表？`);
            if (accept) {
                await fetch('/api/proposals/accept', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ proposal_id: p.id })
                });
                // 刷新人物面板
                const { loadPeople } = window;
                if (loadPeople) loadPeople();
            } else {
                await fetch('/api/proposals/reject', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ proposal_id: p.id })
                });
            }
        }
    } catch (e) {
        console.error('检查人物提案失败:', e);
    }
}
