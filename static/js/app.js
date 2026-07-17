// AI Companion - 主入口（ES Modules）
import * as state from './state.js';
import * as tts from './tts.js';
import * as chat from './chat.js';
import * as sessions from './sessions.js';
import * as memory from './memory.js';
import * as ui from './ui.js';

// 将所有需要被 HTML onclick 调用的函数暴露到 window
Object.assign(window, {
    // TTS / 语音
    toggleTTS: tts.toggleTTS,
    toggleVoice: tts.toggleVoice,
    toggleAutoChat: tts.toggleAutoChat,
    startAutoListen: tts.startAutoListen,

    // 聊天
    sendMessage: chat.sendMessage,
    cancelGeneration: chat.cancelGeneration,
    playAudio: chat.playAudio,
    stopAudio: chat.stopAudio,
    saveMessageAsMemory: chat.saveMessageAsMemory,

    // 会话
    newChat: sessions.newChat,
    switchSession: sessions.switchSession,
    deleteSession: sessions.deleteSession,
    clearChat: sessions.clearChat,
    renameSession: sessions.renameSession,
    exportChat: sessions.exportChat,
    summarizeChat: sessions.summarizeChat,

    // 记忆 / 人物 / 关系 / 模板 / 搜索
    loadMemories: memory.loadMemories,
    deleteMemory: memory.deleteMemory,
    addPerson: memory.addPerson,
    deletePerson: memory.deletePerson,
    addRelation: memory.addRelation,
    deleteRelation: memory.deleteRelation,
    saveSystemPrompt: memory.saveSystemPrompt,
    loadTemplate: memory.loadTemplate,
    useTemplate: memory.useTemplate,
    showAddTemplateModal: memory.showAddTemplateModal,
    hideAddTemplateModal: memory.hideAddTemplateModal,
    saveTemplate: memory.saveTemplate,
    searchChat: memory.searchChat,
    jumpToSession: memory.jumpToSession,

    // UI
    switchTab: ui.switchTab,
});

// 初始化
document.addEventListener('DOMContentLoaded', () => {
    sessions.loadSessions();
    memory.loadCustomLLMs();
    setupTextarea();
});

function setupTextarea() {
    const input = document.getElementById('messageInput');
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            chat.sendMessage();
        }
    });
}
