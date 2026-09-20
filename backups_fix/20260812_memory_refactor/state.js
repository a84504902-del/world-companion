// 共享应用状态
export let currentSession = null;
export let currentAudio = null;
export let sessions = [];
export let ttsEnabled = true;
export let isListening = false;
export let autoMode = false;
export let isStreaming = false;

export function setCurrentSession(v) { currentSession = v; }
export function setCurrentAudio(v) { currentAudio = v; }
export function setSessions(v) { sessions = v; }
export function setTtsEnabled(v) { ttsEnabled = v; }
export function setIsListening(v) { isListening = v; }
export function setAutoMode(v) { autoMode = v; }
export function setIsStreaming(v) { isStreaming = v; }
