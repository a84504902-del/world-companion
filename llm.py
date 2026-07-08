"""LLM 调用模块 - 统一多后端接口"""
import logging

from routes.custom_llm import get_llm_chat_func, get_llm_stream_func

logger = logging.getLogger("llm")


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


def chat(text, mode="deepseek", history=None, system_prompt=None, memory_text="", relation_text=""):
    """统一聊天接口"""
    from config import ENV

    # 默认系统提示词
    if not system_prompt:
        system_prompt = ENV.get("SYSTEM_PROMPT",
            "你是一个温柔体贴的AI伴侣，名叫夏雪。你会用温暖、关心的语气回复用户，像一个贴心的朋友或恋人。回复要自然、有感情，不要太长，像日常聊天一样。")

    # 添加记忆和关系到系统提示
    context = system_prompt
    if memory_text:
        context += f"\n\n相关记忆：\n{memory_text}"
    if relation_text:
        context += f"\n\n人物关系：\n{relation_text}"

    messages = [{"role": "system", "content": context}]

    # 添加历史记录
    if history:
        for msg in history[-10:]:
            messages.append({"role": msg["role"], "content": msg["content"]})

    messages.append({"role": "user", "content": text})

    # 调用对应后端
    chat_func = get_llm_chat_func(mode)
    if not chat_func:
        return "LLM 配置未找到，请在后台管理中配置"

    try:
        return chat_func(messages)
    except Exception as e:
        return f"抱歉，AI 出错了: {e}"


def stream_chat(text, mode="deepseek", history=None, system_prompt=None, memory_text="", relation_text=""):
    """流式聊天接口，yield 文本片段。不支持流式时返回 None。"""
    from config import ENV

    if not system_prompt:
        system_prompt = ENV.get("SYSTEM_PROMPT",
            "你是一个温柔体贴的AI伴侣，名叫夏雪。你会用温暖、关心的语气回复用户，像一个贴心的朋友或恋人。回复要自然、有感情，不要太长，像日常聊天一样。")

    context = system_prompt
    if memory_text:
        context += f"\n\n相关记忆：\n{memory_text}"
    if relation_text:
        context += f"\n\n人物关系：\n{relation_text}"

    messages = [{"role": "system", "content": context}]
    if history:
        for msg in history[-10:]:
            messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": text})

    stream_func = get_llm_stream_func(mode)
    if not stream_func:
        return None

    return stream_func(messages)
