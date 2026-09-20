// AI Companion - 主入口（ES Modules）
import * as state from './state.js';
import * as tts from './tts.js';
import * as chat from './chat.js';
import * as sessions from './sessions.js';
import * as memory from './memory.js';
import * as loops from './loops.js';
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
    setLengthMode: chat.setLengthMode,
    toggleGroupMode: chat.toggleGroupMode,
    loadPeople: memory.loadPeople,

    // 会话
    newChat: sessions.newChat,
    switchSession: sessions.switchSession,
    deleteSession: sessions.deleteSession,
    clearChat: sessions.clearChat,
    renameSession: sessions.renameSession,
    exportChat: sessions.exportChat,
    summarizeChat: sessions.summarizeChat,

    // 未完结事件（她在等的事）
    loadLoops: loops.loadLoops,
    addLoop: loops.addLoop,
    closeLoop: loops.closeLoop,
    deleteLoop: loops.deleteLoop,
    toggleShowClosed: loops.toggleShowClosed,

    // 记忆 / 人物 / 关系 / 模板 / 搜索
    loadMemories: memory.loadMemories,
    deleteMemory: memory.deleteMemory,
    loadPins: memory.loadPins,
    deletePin: memory.deletePin,
    saveFamilyTemplate: memory.saveFamilyTemplate,
    importFamilyTemplate: memory.importFamilyTemplate,
    deleteFamilyTemplate: memory.deleteFamilyTemplate,
    addPerson: memory.addPerson,
    deletePerson: memory.deletePerson,
    togglePersonEdit: memory.togglePersonEdit,
    savePersonEdit: memory.savePersonEdit,
    suggestRelCalls: memory.suggestRelCalls,
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
    toggleMoreMenu: ui.toggleMoreMenu,
    closeMoreMenu: ui.closeMoreMenu,
    openPromptModal: ui.openPromptModal,
    closePromptModal: ui.closePromptModal,
    openLoopsModal: ui.openLoopsModal,
    closeLoopsModal: ui.closeLoopsModal,
    openPersonConfigModal: ui.openPersonConfigModal,
    closePersonConfigModal: ui.closePersonConfigModal,
    exportPersonConfig: memory.exportPersonConfig,
    savePersonConfigAsTemplate: memory.savePersonConfigAsTemplate,
    importFromFile: memory.importFromFile,
    openSearchModal: ui.openSearchModal,
    closeSearchModal: ui.closeSearchModal,
});

// 点击别处收起"更多"菜单
document.addEventListener('click', (e) => {
    const menu = document.getElementById('moreMenu');
    if (menu && !menu.contains(e.target)) ui.closeMoreMenu();
    // 点遮罩关闭弹窗
    if (e.target && e.target.classList && e.target.classList.contains('modal')) {
        ui.closePromptModal();
        ui.closeSearchModal();
        ui.closeLoopsModal();
    }
});

// 回到这个页面时，看她有没有在你不在的时候留过言
window.addEventListener('focus', () => chat.checkProactive());
setInterval(() => {
    if (document.visibilityState === 'visible') chat.checkProactive();
}, 5 * 60 * 1000);   // 每 5 分钟兜底一次

// 初始化
document.addEventListener('DOMContentLoaded', () => {
    sessions.loadSessions();
    memory.loadCustomLLMs();
    setupTextarea();
    // 恢复群像模式按钮状态（localStorage 持久化）
    chat.restoreGroupModeButton();
    // 等会话就绪后再拉状态条（剧情时间 / 她在等什么）
    setTimeout(() => chat.loadStatusBar(), 900);
});

export function autoGrowTextarea(el) {
    if (!el) return;
    const MAX = 160;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, MAX) + 'px';
    el.style.overflowY = el.scrollHeight > MAX ? 'auto' : 'hidden';
}

function setupTextarea() {
    const input = document.getElementById('messageInput');
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            chat.sendMessage();
        }
    });
    // 随内容自动增高，超过上限才出滚动条
    input.addEventListener('input', () => autoGrowTextarea(input));
    autoGrowTextarea(input);
}
