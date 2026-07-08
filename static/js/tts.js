import * as state from './state.js';

export function toggleTTS() {
    state.setTtsEnabled(!state.ttsEnabled);
    const btn = document.getElementById('ttsBtn');
    btn.classList.toggle('active', state.ttsEnabled);
    btn.textContent = state.ttsEnabled ? '🔊' : '🔇';
}

export function initSpeech() {
    if (!('webkitSpeechRecognition' in window) && !('SpeechRecognition' in window)) {
        const btn = document.getElementById('voiceBtn');
        if (btn) btn.style.display = 'none';
        return;
    }
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    const rec = new SpeechRecognition();
    rec.lang = 'zh-CN';
    rec.continuous = true;
    rec.interimResults = false;

    rec.onresult = (event) => {
        let text = '';
        for (let i = event.resultIndex; i < event.results.length; i++) {
            if (event.results[i].isFinal) {
                text = event.results[i][0].transcript.trim();
                break;
            }
        }
        if (!text) return;
        document.getElementById('messageInput').value = text;
        const { sendMessage } = window;
        if (sendMessage) sendMessage();
        try { rec.stop(); } catch (e) {}
    };

    rec.onerror = (event) => {
        console.error('语音识别错误:', event.error);
        state.setIsListening(false);
        const btn = document.getElementById('voiceBtn');
        if (btn) btn.classList.remove('listening');
    };

    rec.onend = () => {
        state.setIsListening(false);
        const btn = document.getElementById('voiceBtn');
        if (btn) btn.classList.remove('listening');
    };

    state.setRecognition(rec);
}

export function toggleVoice() {
    if (!state.recognition) {
        alert('浏览器不支持语音识别');
        return;
    }
    if (state.isListening) {
        state.recognition.stop();
        return;
    }
    try {
        state.recognition.start();
        state.setIsListening(true);
        const btn = document.getElementById('voiceBtn');
        if (btn) btn.classList.add('listening');
    } catch (e) {
        console.error('启动语音识别失败:', e);
    }
}

export function toggleAutoChat() {
    state.setAutoMode(!state.autoMode);
    const btn = document.getElementById('autoBtn');
    if (btn) btn.classList.toggle('active', state.autoMode);
    if (!state.autoMode) {
        if (state.isListening && state.recognition) state.recognition.stop();
    } else {
        if (!state.isListening && !state.currentAudio) setTimeout(startAutoListen, 300);
    }
}

export function startAutoListen() {
    if (!state.autoMode || !state.recognition || state.isListening || state.currentAudio) return;
    try {
        state.recognition.start();
        state.setIsListening(true);
        const btn = document.getElementById('voiceBtn');
        if (btn) btn.classList.add('listening');
    } catch (e) {
        console.error('自动启动语音识别失败:', e);
    }
}
