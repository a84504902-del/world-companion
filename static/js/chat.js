import * as state from './state.js';
import { escapeHtml, renderMarkdown } from './utils.js';

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

// 发送消息（支持流式输出）
export async function sendMessage() {
    const input = document.getElementById('messageInput');
    const text = input.value.trim();
    if (!text) return;

    const mode = document.getElementById('llmSelect').value;
    input.value = '';

    // 显示用户消息
    appendMessage('user', text);

    // 创建 AI 流式消息占位
    const streamEl = createStreamingMessage();
    const msgDiv = streamEl.closest('.message');
    let fullResponse = '';

    try {
        const resp = await fetch('/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text, mode })
        });

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let audioUrl = null;
        let summary = null;

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
                            streamEl.textContent = '错误: ' + eventData.error;
                        }
                        if (eventData.done) {
                            audioUrl = eventData.audio_url;
                            summary = eventData.summary;
                        }
                    } catch (e) {
                        // 忽略 JSON 解析错误
                    }
                }
            }
        }

        // 流完成，渲染最终 Markdown
        finalizeStreamingMessage(msgDiv, fullResponse, audioUrl);

        // 显示摘要
        if (summary) {
            appendMessage('assistant', `📝 **对话摘要：** ${summary}`);
        }

        // 自动播放 TTS
        if (audioUrl && state.ttsEnabled) {
            playAudio(audioUrl);
        }

    } catch (e) {
        streamEl.textContent = '发送失败: ' + e.message;
        finalizeStreamingMessage(msgDiv, '发送失败: ' + e.message, null);
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
    audio.play().catch(e => console.log('播放失败:', e));
}

export function stopAudio() {
    if (state.currentAudio) {
        state.currentAudio.pause();
        state.currentAudio.currentTime = 0;
        state.setCurrentAudio(null);
    }
}
