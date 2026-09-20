import * as state from './state.js';
import { escapeHtml, renderMarkdown } from './utils.js';

// 当前流式请求的 AbortController
let abortController = null;

// 回复长度模式：short / normal / long
let lengthMode = 'normal';

export function setLengthMode(mode) {
    lengthMode = mode;
    document.querySelectorAll('.length-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.mode === mode);
    });
}

export function getLengthMode() {
    return lengthMode;
}

export async function loadHistory() {
    try {
        const resp = await fetch('/history');
        const data = await resp.json();
        renderMessages(data.history);
    } catch (e) {
        console.error('加载历史失败:', e);
    }
}

export function renderMessages(history) {
    const container = document.getElementById('chatMessages');
    container.innerHTML = history.map(msg => {
        const content = msg.role === 'assistant' ? renderMarkdown(msg.content) : escapeHtml(msg.content);
        return `<div class="message ${msg.role}"><div class="message-content">${content}</div></div>`;
    }).join('');
    container.scrollTop = container.scrollHeight;
}

export function appendMessage(role, content, audioUrl) {
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

    const renderedContent = role === 'assistant' ? renderMarkdown(content) : escapeHtml(content);
    div.innerHTML = `<div class="message-content">${renderedContent}<div class="audio-btns">${audioBtns}</div></div>`;
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
    return div;
}

// 创建流式消息占位（显示光标动画）
function createStreamingMessage() {
    const container = document.getElementById('chatMessages');
    const div = document.createElement('div');
    div.className = 'message assistant';
    div.innerHTML = '<div class="message-content"><span class="streaming-text"></span><span class="streaming-cursor">▌</span></div>';
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
    return div.querySelector('.streaming-text');
}

// 完成流式消息（移除光标，渲染 Markdown）
function finalizeStreamingMessage(div, fullText, audioUrl) {
    const contentDiv = div.querySelector('.message-content');
    let audioBtns = '';
    if (audioUrl) {
        audioBtns = `
            <button class="btn-audio" onclick="playAudio('${audioUrl}')" title="重播">🔊</button>
            <button class="btn-audio btn-stop" onclick="stopAudio()" title="停止">⏹</button>
        `;
    }
    contentDiv.innerHTML = renderMarkdown(fullText) + `<div class="audio-btns">${audioBtns}</div>`;
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

    // 显示用户消息
    appendMessage('user', text);

    // 创建 AI 流式消息占位
    const streamEl = createStreamingMessage();
    const msgDiv = streamEl.closest('.message');
    let fullResponse = '';

    // 设置 AbortController
    abortController = new AbortController();
    state.setIsStreaming(true);
    updateSendButton();

    try {
        const resp = await fetch('/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text, mode, length_mode: lengthMode }),
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
                            fullResponse += eventData.content;
                            streamEl.textContent = fullResponse;
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
            finalizeStreamingMessage(msgDiv, fullResponse, audioUrl);
            if (summary) {
                appendMessage('assistant', `📝 **对话摘要：** ${summary}`);
            }
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
