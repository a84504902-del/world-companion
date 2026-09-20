"""LLM 调用模块 - 统一多后端接口"""
import logging

from routes.custom_llm import get_llm_chat_func, get_llm_stream_func

logger = logging.getLogger("llm")

# 回复长度三档配置：
# - max_tokens 定硬上限（防爆长）
# - instruction 用"结构"代替"数字"引导（模型对字数无感，但对"有什么可写"有感）
# - 指令拼在 system prompt 末尾、紧贴用户消息的位置，模型最敏感
LENGTH_MODES = {
    "short": {
        "max_tokens": 300,
        "instruction": (
            "\n\n【回复要求】请用简短、口语化的方式回复，两三句话说完，"
            "不要铺垫、不要展开细节、不要列点。"
        ),
    },
    "normal": {
        "max_tokens": 2000,
        "instruction": "",
    },
    "long": {
        "max_tokens": 2000,
        "instruction": (
            "\n\n【回复要求】请详细展开你的回复，像写一封有温度的长信：\n"
            "1. 先具体回应当前话题，说出你的真实想法；\n"
            "2. 再展开你的情绪、感受，以及相关的前因后果和细节；\n"
            "3. 最后自然地表达接下来的打算或想聊的方向。\n"
            "内容要充实饱满，不要用一两句话敷衍，也不要刻意总结收尾。"
        ),
    },
}


def call_llm(messages, mode="deepseek"):
    """直接调用 LLM（传入完整 messages 数组）"""
    chat_func = get_llm_chat_func(mode)
    if not chat_func:
        return None
    try:
        return chat_func(messages)
    except Exception as e:
        logger.error("call_llm 失败: %s", e)
        return None


def _build_messages(text, history, system_prompt, memory_text, length_mode):
    """统一组装 messages：system prompt + 记忆上下文 + 长度指令（末尾）+ 历史 + 用户消息"""
    from config import ENV

    if not system_prompt:
        system_prompt = ENV.get("SYSTEM_PROMPT",
            "你是一个温柔体贴的AI伴侣，名叫夏雪。你会用温暖、关心的语气回复用户，像一个贴心的朋友或恋人。回复要自然、有感情，不要太长，像日常聊天一样。")

    length_cfg = LENGTH_MODES.get(length_mode, LENGTH_MODES["normal"])

    context = system_prompt
    if memory_text:
        context += f"\n\n{memory_text}"
    if length_cfg["instruction"]:
        context += length_cfg["instruction"]

    messages = [{"role": "system", "content": context}]

    # 添加历史记录（最近 10 条）
    if history:
        for msg in history[-10:]:
            messages.append({"role": msg["role"], "content": msg["content"]})

    messages.append({"role": "user", "content": text})
    return messages


def chat(text, mode="deepseek", history=None, system_prompt=None, memory_text="", length_mode="normal"):
    """统一聊天接口（非流式）"""
    messages = _build_messages(text, history, system_prompt, memory_text, length_mode)

    # 调用对应后端
    chat_func = get_llm_chat_func(mode)
    if not chat_func:
        return "LLM 配置未找到，请在后台管理中配置"

    max_tokens = LENGTH_MODES.get(length_mode, LENGTH_MODES["normal"])["max_tokens"]
    try:
        return chat_func(messages, max_tokens=max_tokens)
    except Exception as e:
        return f"抱歉，AI 出错了: {e}"


def stream_chat(text, mode="deepseek", history=None, system_prompt=None, memory_text="", length_mode="normal"):
    """流式聊天接口，yield 文本片段。不支持流式时返回 None。"""
    messages = _build_messages(text, history, system_prompt, memory_text, length_mode)

    stream_func = get_llm_stream_func(mode)
    if not stream_func:
        return None

    max_tokens = LENGTH_MODES.get(length_mode, LENGTH_MODES["normal"])["max_tokens"]
    return stream_func(messages, max_tokens=max_tokens)
