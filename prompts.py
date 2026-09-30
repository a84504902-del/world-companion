"""统一提示词加载器。

为什么有这个文件：
- 功能型 prompt（摘要 / 状态卡 / 人物检测 / 关系检测 / 长度模式）以前散落在
  chat.py / llm.py 里，改一句要翻代码。现在集中放在 prompts.json，改 prompt 只动那一个文件。
- llm.py 在没有会话 system_prompt 时，原来硬编码了一句「你是一个温柔体贴的AI伴侣…」，
  等于在人设之外又定义了第二份人设。现在由 persona_anchor.json 生成回退，彻底消除重复定义。

设计约定：
- 所有函数带缓存 + 缺省兜底：prompts.json / persona_anchor.json 缺失或损坏都不会让服务崩，
  退化为调用方传入的 default。
- prompts.json 是「可编辑覆盖层」：存在就优先用它；不存在就回退到代码里的 default（即原内联文案）。
"""
import json
import os

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_prompts_cache = None
_anchor_cache = None


def _load_prompts():
    global _prompts_cache
    if _prompts_cache is None:
        path = os.path.join(_BASE_DIR, "prompts.json")
        try:
            with open(path, encoding="utf-8") as f:
                _prompts_cache = json.load(f)
        except Exception:
            _prompts_cache = {}
    return _prompts_cache


def get_prompt(key, default=""):
    """读取功能型 prompt；key 不存在或文件缺失时返回 default。"""
    return _load_prompts().get(key, default)


def get_length_mode(mode):
    """读取长度模式指令；normal / 未知返回空串（不额外约束）。"""
    return _load_prompts().get("length_modes", {}).get(mode, "")


def load_anchor():
    """读取 persona_anchor.json（带缓存）；失败返回空 dict。"""
    global _anchor_cache
    if _anchor_cache is None:
        path = os.path.join(_BASE_DIR, "persona_anchor.json")
        local_path = os.path.join(_BASE_DIR, "persona_local.json")
        if os.path.exists(local_path):
            path = local_path
        try:
            with open(path, encoding="utf-8") as f:
                _anchor_cache = json.load(f)
        except Exception:
            _anchor_cache = {}


def fallback_system_prompt():
    """会话未设置 system_prompt 时的简短回退人设，由 persona_anchor.json 驱动。"""
    pa = load_anchor()
    if not pa:
        return "你是一个温柔体贴的AI伴侣，名叫夏雪。像日常聊天一样自然地回复用户。"
    name = pa.get("persona", "夏雪")
    traits = "、".join(pa.get("core_traits", []) or [])
    style = pa.get("speech_style", "")
    return f"你是{name}，{traits}。{style}。"
