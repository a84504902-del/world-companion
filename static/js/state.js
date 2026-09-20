// 共享应用状态
export let currentSession = null;
export let currentAudio = null;
export let sessions = [];
export let ttsEnabled = localStorage.getItem('ttsEnabled') !== 'false';
export let isListening = false;
export let autoMode = false;
export let isStreaming = false;
export let lengthMode = localStorage.getItem('lengthMode') || 'normal';

export function setCurrentSession(v) { currentSession = v; }
export function setCurrentAudio(v) { currentAudio = v; }
export function setSessions(v) { sessions = v; }
export function setTtsEnabled(v) { ttsEnabled = v; localStorage.setItem('ttsEnabled', v); }
export function setIsListening(v) { isListening = v; }
export function setAutoMode(v) { autoMode = v; }
export function setIsStreaming(v) { isStreaming = v; }
export function setLengthModeState(v) { lengthMode = v; localStorage.setItem('lengthMode', v); }
