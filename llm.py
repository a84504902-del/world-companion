"""LLM 调用模块 - 统一多后端接口"""
import logging
from datetime import datetime

from routes.custom_llm import get_llm_chat_func, get_llm_stream_func
from prompts import fallback_system_prompt, get_length_mode

logger = logging.getLogger("llm")


def _time_prefix(ts, now):
    """把时间戳变成人类可感的相对时间，让模型知道这条消息是多久前说的。

    没有这个标注，模型会把跨越好几天的对话当成"刚刚发生的连续聊天"。
    """
    if not ts:
        return ""
    try:
        dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""

    delta = (now - dt).total_seconds()
    if delta < 300:            # 5 分钟内不标注，避免噪音
        return ""
    if delta < 3600:
        return f"{int(delta // 60)}分钟前"
    if delta < 86400:
        if dt.date() == now.date():
            return f"{int(delta // 3600)}小时前"
        return f"昨天{dt.strftime('%H:%M')}"
    days = int(delta // 86400)
    if days < 7:
        return f"{days}天前"
    return dt.strftime("%m月%d日")


GROUP_MODE_RULES = """【群像模式（当前开启）】
本场景允许多人物同场出场。规则：
1. 人物表（【当前人物】段）里列出的角色可以出场说话——他们不算「新角色」，
   此条优先于「禁止创建新角色」的规则
2. 台词格式：除夏雪以外的每个角色，台词前必须带名字（如「夏雪妈妈：……」）
3. 夏雪仍然是你扮演的唯一主角：以第一人称「我」说话，戏份占比最大，视角跟着她
4. 每个出场人物必须严格保持自己描述里的性格、说话方式和对林风的称呼
5. 人物之间可以有互动对话，但场景推进始终跟随林风的行动和选择
6. 禁止创造人物表之外的新人物"""


def _build_messages(text, history, system_prompt, memory_text, length_mode, group_mode=False, actor=""):
    """统一组装 messages（actor: 当前扮演角色，空 = 夏雪）"""
    from config import ENV

    if not system_prompt:
        # SYSTEM_PROMPT 环境变量可覆盖；否则由 persona_anchor.json 生成回退（单一人设来源，杜绝第二份人设定义）
        system_prompt = ENV.get("SYSTEM_PROMPT") or fallback_system_prompt()

    # 清洗历史记录：移除包含括号或第三人称的漂移消息
    if history:
        cleaned_history = []
        for msg in history:
            content = msg.get("content", "")
            # 跳过明显的漂移消息（包含新角色名或小说叙述）
            if any(name in content for name in ['赵刚', '陈默', '周凯', '夏海', '沈青']):
                continue
            cleaned_history.append(msg)
        history = cleaned_history[-30:]  # 保留最近30条（DeepSeek 128K 上下文，成本可忽略）
    else:
        history = []

    # 长度模式指令从 prompts.json 读取（normal / 未知返回空串，不额外约束）
    _lm = get_length_mode(length_mode)
    if _lm:
        system_prompt = _lm + "\n\n" + system_prompt

    # 群像模式：允许多人物同场（放在长度指令之后、人设主体之前，高注意力区）
    if group_mode:
        _group_text = GROUP_MODE_RULES
        if actor and actor != "夏雪":
            # 视角模式下，"唯一主角"是当前扮演的角色，不是夏雪
            _group_text = _group_text.replace("夏雪", actor)
        system_prompt = _group_text + "\n\n" + system_prompt

    context = system_prompt
    if memory_text:
        context += f"\n\n{memory_text}"

    messages = [{"role": "system", "content": context}]

    # 添加历史记录（最近 30 条），带相对时间标注
    if history:
        _now = datetime.now()
        for msg in history[-30:]:
            content = msg.get("content", "")
            role = msg.get("role", "user")
            # proactive（她主动发来的留言）对模型来说就是她说的话
            if role == "proactive":
                role = "assistant"
            prefix = _time_prefix(msg.get("timestamp"), _now)
            if prefix:
                content = f"[{prefix}] {content}"
            messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": text})
    return messages


def chat(text, mode="deepseek", history=None, system_prompt=None, memory_text="", length_mode="normal", group_mode=False, actor=""):
    """统一聊天接口（非流式）"""
    messages = _build_messages(text, history, system_prompt, memory_text, length_mode, group_mode, actor)

    chat_func = get_llm_chat_func(mode)
    if not chat_func:
        return "LLM 配置未找到，请在后台管理中配置"

    try:
        return chat_func(messages)
    except Exception as e:
        return f"抱歉，AI 出错了: {e}"


def stream_chat(text, mode="deepseek", history=None, system_prompt=None, memory_text="", length_mode="normal", group_mode=False, actor=""):
    """流式聊天接口"""
    messages = _build_messages(text, history, system_prompt, memory_text, length_mode, group_mode, actor)

    stream_func = get_llm_stream_func(mode)
    if not stream_func:
        return None

    return stream_func(messages)
