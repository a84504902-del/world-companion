import * as state from './state.js';

// ===== MediaRecorder 语音录入 =====
let mediaRecorder = null;
let recordedChunks = [];
let mediaStream = null;
let audioContext = null;
let analyserNode = null;
let sourceNode = null;
let silenceTimer = null;
let isRecording = false;

// 自动重连定时器
let _autoRetryTimer = null;
function clearAutoRetry() {
    if (_autoRetryTimer) {
        clearTimeout(_autoRetryTimer);
        _autoRetryTimer = null;
    }
}

export function toggleTTS() {
    state.setTtsEnabled(!state.ttsEnabled);
    const btn = document.getElementById('ttsBtn');
    btn.classList.toggle('active', state.ttsEnabled);
    btn.textContent = state.ttsEnabled ? '🔊' : '🔇';
}

// 初始化：获取麦克风权限，创建 MediaRecorder
async function initRecorder() {
    if (mediaRecorder) return true;
    try {
        mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });

        // AudioContext 用于静音检测
        audioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
        analyserNode = audioContext.createAnalyser();
        analyserNode.fftSize = 2048;
        sourceNode = audioContext.createMediaStreamSource(mediaStream);
        sourceNode.connect(analyserNode);

        // 选择支持的录音格式
        let mimeType = '';
        for (const type of ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4']) {
            if (MediaRecorder.isTypeSupported(type)) {
                mimeType = type;
                break;
            }
        }

        mediaRecorder = new MediaRecorder(mediaStream, mimeType ? { mimeType } : undefined);

        mediaRecorder.ondataavailable = (e) => {
            if (e.data.size > 0) recordedChunks.push(e.data);
        };

        mediaRecorder.onstop = async () => {
            const blob = new Blob(recordedChunks, { type: mediaRecorder.mimeType });
            recordedChunks = [];
            if (blob.size > 1000) {
                await sendToSTT(blob);
            } else {
                // 录音太短，自动模式下重新开始监听
                if (state.autoMode) scheduleAutoListen(500);
            }
        };

        return true;
    } catch (e) {
        console.error('麦克风初始化失败:', e);
        const btn = document.getElementById('voiceBtn');
        if (btn) btn.style.display = 'none';
        const autoBtn = document.getElementById('autoBtn');
        if (autoBtn) autoBtn.style.display = 'none';
        alert('无法访问麦克风: ' + e.message);
        return false;
    }
}

// 静音检测：连续静音超过阈值自动停止录音
function startSilenceDetection() {
    if (!analyserNode) return;
    const dataArray = new Uint8Array(analyserNode.fftSize);
    const SILENCE_THRESHOLD = 15;
    const SILENCE_DURATION = 2000; // 2 秒静音自动停止
    const MAX_RECORDING_TIME = 30000; // 最长 30 秒

    const checkSilence = () => {
        if (!isRecording) return;
        analyserNode.getByteTimeDomainData(dataArray);

        let sum = 0;
        for (let i = 0; i < dataArray.length; i++) {
            const v = (dataArray[i] - 128) / 128;
            sum += v * v;
        }
        const rms = Math.sqrt(sum / dataArray.length);

        if (rms < SILENCE_THRESHOLD / 128) {
            if (!silenceTimer) {
                silenceTimer = setTimeout(() => {
                    if (isRecording) stopRecording();
                }, SILENCE_DURATION);
            }
        } else {
            if (silenceTimer) {
                clearTimeout(silenceTimer);
                silenceTimer = null;
            }
        }
        requestAnimationFrame(checkSilence);
    };

    // 最长录音保护
    setTimeout(() => {
        if (isRecording) stopRecording();
    }, MAX_RECORDING_TIME);

    requestAnimationFrame(checkSilence);
}

// 开始录音
async function startRecording() {
    if (isRecording) return;
    const ok = await initRecorder();
    if (!ok) return;

    // 如果 AudioContext 被挂起（浏览器自动播放策略），恢复它
    if (audioContext && audioContext.state === 'suspended') {
        await audioContext.resume();
    }

    recordedChunks = [];
    mediaRecorder.start(250); // 每 250ms 产生一个数据块
    isRecording = true;

    // 打断：停止当前 TTS 播放和 LLM 生成
    const { stopAudio, cancelGeneration } = window;
    if (stopAudio) stopAudio();
    if (cancelGeneration) cancelGeneration();

    startSilenceDetection();
}

// 停止录音
function stopRecording() {
    if (!isRecording) return;
    isRecording = false;

    if (silenceTimer) {
        clearTimeout(silenceTimer);
        silenceTimer = null;
    }

    if (mediaRecorder && mediaRecorder.state !== 'inactive') {
        mediaRecorder.stop();
    }

    state.setIsListening(false);
    const btn = document.getElementById('voiceBtn');
    if (btn) btn.classList.remove('listening');
}

// 发送录音到后端进行语音识别
async function sendToSTT(blob) {
    const btn = document.getElementById('voiceBtn');
    if (btn) btn.textContent = '⏳';

    try {
        const formData = new FormData();
        formData.append('audio', blob, 'recording.webm');

        const resp = await fetch('/api/stt', {
            method: 'POST',
            body: formData
        });

        const data = await resp.json();

        if (data.text) {
            document.getElementById('messageInput').value = data.text;
            const { sendMessage } = window;
            if (sendMessage) sendMessage();
        } else if (data.error) {
            console.warn('语音识别:', data.error);
        }
    } catch (e) {
        console.error('语音识别请求失败:', e);
    } finally {
        if (btn) btn.textContent = '🎤';
        // 自动模式下继续监听
        if (state.autoMode) {
            scheduleAutoListen(500);
        }
    }
}

function scheduleAutoListen(delay) {
    clearAutoRetry();
    _autoRetryTimer = setTimeout(() => {
        _autoRetryTimer = null;
        startAutoListen();
    }, delay);
}

// ===== 手动语音按钮 =====
export async function toggleVoice() {
    if (isRecording) {
        stopRecording();
        return;
    }
    clearAutoRetry();
    await startRecording();
    if (isRecording) {
        state.setIsListening(true);
        const btn = document.getElementById('voiceBtn');
        if (btn) btn.classList.add('listening');
    }
}

// ===== 自动语音聊天 =====
export function toggleAutoChat() {
    state.setAutoMode(!state.autoMode);
    const btn = document.getElementById('autoBtn');
    if (btn) btn.classList.toggle('active', state.autoMode);

    if (!state.autoMode) {
        clearAutoRetry();
        if (isRecording) stopRecording();
    } else {
        // 自动语音模式必须开启 TTS
        if (!state.ttsEnabled) {
            state.setTtsEnabled(true);
            const ttsBtn = document.getElementById('ttsBtn');
            if (ttsBtn) {
                ttsBtn.classList.add('active');
                ttsBtn.textContent = '🔊';
            }
        }
        if (!isRecording && !state.currentAudio) {
            scheduleAutoListen(300);
        }
    }
}

export async function startAutoListen() {
    if (!state.autoMode || isRecording || state.currentAudio) return;
    clearAutoRetry();
    await startRecording();
    if (isRecording) {
        state.setIsListening(true);
        const btn = document.getElementById('voiceBtn');
        if (btn) btn.classList.add('listening');
    }
}
