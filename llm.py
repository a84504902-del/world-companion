"""LLM 调用模块 - 统一多后端接口"""
import logging
import re
from datetime import datetime

from routes.custom_llm import get_llm_chat_func, get_llm_stream_func
from prompts import fallback_system_prompt, get_length_mode, get_prompt

logger = logging.getLogger("llm")

# ---- 历史漂移清洗（2026-10-13 重写）----
# 旧版：硬编码 5 个人名（赵刚/陈默/周凯/夏海/沈青），消息只要"提到"就整条丢弃
#   → 聊到这些人历史就凭空消失；切沈青视角时自己的上下文也被吃光
# 新版：只丢"模型跑偏写小说体"的消息（assistant 且命中叙述特征），
#   正常提到人名的历史全部保留。人称代词开头的不算（防误伤"我说/她说"）。
_NARRATION_PATTERNS = [
    # 「沈青道：」「夏雪妈妈低声说，」「婆婆笑道"」（排除"我道/他道"等人称）
    re.compile(r'(?<![我你他她])[一-龥]{2,5}(?:道|说道|问|答|说|笑道|叹道|怒道|冷冷道|低声说|轻声说|开口道|沉声道|嘀咕道|接着道)[：:，,、「」]'),
    # 第三人称动作叙述开头：「沈青抬起头」「夏雪皱起眉」（不含"转头"等第一人称常用词）
    re.compile(r'^[一-龥]{2,4}(?:抬起|垂下|皱起|皱紧|眨了|盯着|侧过|摇着|点过|闭上|张了)[一-龥]{0,3}(?:头|眼|眉|脸|手|肩|唇|眸|目光)'),
    # 括号动作描写（人设要求纯对话格式，出现括号动作=跑偏）
    re.compile(r'（[^）]{2,30}(?:抬起|垂下|皱眉|微笑|叹气|沉默|颤抖|愣|后退|靠近|握住|点头|摇头|叹了|顿了)）'),
    # 句首"她/他+动作"：assistant 是夏雪，自称"我"，以第三人称开头即跑偏叙述
    re.compile(r'^[她他](?:说|道|笑|叹|抬|垂|皱|点|摇|转|闭|张|眨|盯|侧|沉|愣|颤|退|靠|握|沉默|忽然|突然|这才|才又)'),
]


def _looks_like_narration(content, role):
    """这条消息是不是模型跑偏写的小说叙述（只查 assistant）"""
    if role != "assistant" or not content:
        return False
    return any(p.search(content) for p in _NARRATION_PATTERNS)


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


GROUP_MODE_RULES_TEMPLATE = """【群像模式（当前开启）】
本场景允许多人物同场出场。规则：
1. 人物表（【当前人物】段）里列出的角色可以出场说话——他们不算「新角色」，
   此条优先于「禁止创建新角色」的规则
2. 台词格式：除{PROTAGONIST}以外的每个角色，台词前必须带名字（如「{PROTAGONIST}妈妈：……」）
3. {PROTAGONIST}仍然是你扮演的唯一主角：以第一人称「我」说话，戏份占比最大，视角跟着她
4. 每个出场人物必须严格保持自己描述里的性格、说话方式和对{USER_NAME}的称呼
5. 人物之间可以有互动对话，但场景推进始终跟随{USER_NAME}的行动和选择
6. 禁止创造人物表之外的新人物"""

# v0.16.0：文案搬进 prompts/chat.json（key: chat.group_rules），此常量仅作 default 兜底。
# 改文案请改 prompts/chat.json，改完秒生效，不用重启。


def _current_protagonist_name():
    """当前主角名（从人格库取；库空回退文件；再不行用"主角"兜底）。

    v0.12.0 之前这里硬编码"夏雪"，导致换主角后群像规则失效。
    """
    try:
        import persona_store
        pa = persona_store.active_persona()
        if pa and pa.get("name"):
            return pa["name"]
    except Exception:
        pass
    return "主角"


def _current_user_name():
    """用户在这个世界里的称呼（默认"用户"）"""
    try:
        import persona_store
        pa = persona_store.active_persona()
        if pa:
            return pa.get("user_name") or "用户"
    except Exception:
        pass
    return "用户"


def _build_messages(text, history, system_prompt, memory_text, length_mode, group_mode=False, actor="", protagonist="", user_name=""):
    """统一组装 messages（actor: 当前扮演角色，空 = 主角）

    v0.17.5：protagonist / user_name 由调用方（chat.py）按会话所属世界传入，
    群像规则不再无脑取全局主角——多世界共存时不能串门。
    """
    from config import ENV

    if not system_prompt:
        # SYSTEM_PROMPT 环境变量可覆盖；否则由当前人格生成回退（单一人设来源，杜绝第二份人设定义）
        system_prompt = ENV.get("SYSTEM_PROMPT") or fallback_system_prompt()

    # 清洗历史记录：只丢模型跑偏写的小说叙述（提到人名 ≠ 漂移，不再按人名丢历史）
    if history:
        cleaned_history = []
        for msg in history:
            if _looks_like_narration(msg.get("content", ""), msg.get("role", "")):
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
        _protagonist_name = protagonist or _current_protagonist_name()
        # v0.16.0：文案在 prompts/chat.json（chat.group_rules），此处常量仅兜底
        _group_text = get_prompt(
            "chat.group_rules", default=GROUP_MODE_RULES_TEMPLATE,
            PROTAGONIST=_protagonist_name, USER_NAME=user_name or _current_user_name(),
        )
        # 视角模式下"唯一主角"是当前扮演的角色，把群像规则里的主角名替换成它
        if actor and actor != _protagonist_name:
            _group_text = _group_text.replace(_protagonist_name, actor)
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


def chat(text, mode="deepseek", history=None, system_prompt=None, memory_text="", length_mode="normal", group_mode=False, actor="", protagonist="", user_name=""):
    """统一聊天接口（非流式）"""
    messages = _build_messages(text, history, system_prompt, memory_text, length_mode, group_mode, actor, protagonist, user_name)

    chat_func = get_llm_chat_func(mode)
    if not chat_func:
        return "LLM 配置未找到，请在后台管理中配置"

    try:
        return chat_func(messages)
    except Exception as e:
        return f"抱歉，AI 出错了: {e}"


def stream_chat(text, mode="deepseek", history=None, system_prompt=None, memory_text="", length_mode="normal", group_mode=False, actor="", protagonist="", user_name=""):
    """流式聊天接口"""
    messages = _build_messages(text, history, system_prompt, memory_text, length_mode, group_mode, actor, protagonist, user_name)

    stream_func = get_llm_stream_func(mode)
    if not stream_func:
        return None

    return stream_func(messages)
