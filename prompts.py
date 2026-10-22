"""统一提示词加载器。

为什么有这个文件：
- 功能型 prompt（摘要 / 状态卡 / 人物检测 / 关系检测 / 长度模式）以前散落在
  chat.py / llm.py 里，改一句要翻代码。现在集中放在 prompts/ 文件夹，改 prompt 只动那一个文件。
- llm.py 在没有会话 system_prompt 时，原来硬编码了一句「你是一个温柔体贴的AI伴侣…」，
  等于在人设之外又定义了第二份人设。现在由 persona_anchor.json 生成回退，彻底消除重复定义。

提示词的家（v0.16.0：单文件 prompts.json → 文件夹）：
- 所有提示词只住 prompts/ 文件夹，按域分文件：
  functional.json（功能型）/ chat.json（聊天注入）/ tasks.json（后台任务）/
  onboarding.json（引导）/ memory.json（记忆注入）
- key 一律带域名前缀（如 chat.group_rules），从源头避免跨文件撞名
- loader 合并全部文件：任一文件 mtime 变化即整体重读（热重载）；
  撞名打 warning 不静默覆盖来源提示；文件损坏跳过不崩服务

设计约定：
- 所有函数带缓存 + 缺省兜底：prompts/ 文件缺失或损坏都不会让服务崩，
  退化为调用方传入的 default。
- prompts/ 是「可编辑覆盖层」：存在就优先用它；不存在就回退到代码里的 default（即原内联文案）。
"""
import json
import logging
import os

logger = logging.getLogger("prompts")

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_PROMPTS_DIR = os.path.join(_BASE_DIR, "prompts")
_prompts_cache = None
_prompts_mtime = None          # {文件名: mtime}，任一文件变化就整体重读
_prompts_sources = {}          # {key: 来源文件名}，撞名告警 / 排查用
_placeholder_warned = set()    # {(key, 占位符名)}：未传占位符只警告一次
_anchor_cache = None
_anchor_mtime = None


def _load_prompts():
    """读 prompts/ 文件夹下全部 *.json 并合并。

    - 按文件名排序读入，同名 key 视为配置错误：打 warning（以先读到的为准时
      后面的会覆盖，来源可查 _prompts_sources）
    - 任一文件 mtime 变化就整体重读（改一个文件即生效，不用重启）
    - 文件缺失/损坏不崩服务：损坏的跳过，全部读不到就记空走 default
    """
    global _prompts_cache, _prompts_mtime, _prompts_sources
    mtimes = {}
    try:
        files = sorted(f for f in os.listdir(_PROMPTS_DIR) if f.endswith(".json"))
    except OSError:
        files = []
    for name in files:
        try:
            mtimes[name] = os.path.getmtime(os.path.join(_PROMPTS_DIR, name))
        except OSError:
            pass

    if _prompts_cache is not None and mtimes == _prompts_mtime:
        return _prompts_cache

    merged = {}
    sources = {}
    for name in files:
        try:
            with open(os.path.join(_PROMPTS_DIR, name), encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.warning("prompts/%s 读取失败，已跳过（%s）", name, e)
            continue
        if not isinstance(data, dict):
            logger.warning("prompts/%s 顶层不是 JSON 对象，已跳过", name)
            continue
        for key, val in data.items():
            if key in merged:
                logger.warning(
                    "提示词 key '%s' 在多个文件重复：%s 与 %s（后者覆盖前者）——"
                    "请删掉其中一份，避免改错文件", key, sources[key], name)
            merged[key] = val
            sources[key] = name
    _prompts_cache = merged
    _prompts_sources = sources
    _prompts_mtime = mtimes
    return _prompts_cache


def prompt_source(key):
    """某个提示词 key 现在住在哪个文件（排查/验收用；不存在返回 None）。"""
    _load_prompts()
    return _prompts_sources.get(key)


def get_prompt(key, default="", **variables):
    """读取功能型 prompt；key 不存在或文件缺失时返回 default。

    v0.13.1：支持 {{占位符}} 变量替换。原来 state_card 这类提示词要把当前角色名
    写进去，只能把整段文案硬编码在代码里（换角色就失效），或者在 prompts.json 里
    写死一个名字。现在文案留在 json，用 {{PROTAGONIST}} 之类占位，值从数据库取。

    v0.16.0：占位符防静默丢失——文案里有 {{NAME}} 但调用方没传对应 kwarg 时，
    打一次 warning（同 key+占位符只警告一次，不刷屏），然后按空串填入。
    这是搬迁验收和日后改提示词的安全网：拼错占位符名不会无声坏掉。
    """
    text = _load_prompts().get(key, default)
    if "{{" not in text:
        return text
    for name, value in variables.items():
        value = "" if value is None else str(value)
        text = text.replace("{{%s}}" % name, value)
    # 还有没填上的占位符（比如 json 里写了 {{FOO}} 但调用方没传）→ 警告一次 + 清空，
    # 免得把 "{{FOO}}" 原样发给模型
    import re as _re
    for leftover in sorted(set(_re.findall(r"\{\{([A-Z_0-9]+)\}\}", text))):
        ck = (key, leftover)
        if ck not in _placeholder_warned:
            _placeholder_warned.add(ck)
            logger.warning(
                "提示词 '%s' 用了 {{%s}} 但调用方没传该变量（本次按空串填入）。"
                "请核对调用方 kwargs 或 prompts/ 里的占位符拼写。", key, leftover)
    while "{{" in text:
        head, _, rest = text.partition("{{")
        _, _, tail = rest.partition("}}")
        text = head + tail
    return text


def persona_vars():
    """当前主角的提示词变量（取不到给安全兜底，绝不让服务崩）。

    prompts.json 里用 {{PROTAGONIST}} / {{TRAITS}} / {{USER_NAME}} / {{RELATION}}
    引用这些值——人格库里换主角后，这些提示词会自动跟着换。
    """
    fallback = {"PROTAGONIST": "她", "TRAITS": "以人设为准",
                "USER_NAME": "对方", "RELATION": ""}
    try:
        import persona_store
        pa = persona_store.active_persona() or {}
    except Exception:
        return fallback
    if not pa:
        return fallback
    traits = "、".join(pa.get("core_traits") or []) or fallback["TRAITS"]
    # 关系取 bond_origin 里的第一项（"林风的女朋友"这类），没有就留空
    relation = ""
    bond = pa.get("bond_origin") or {}
    for v in bond.values():
        if isinstance(v, str) and v.strip():
            relation = v.strip()
            break
    return {
        "PROTAGONIST": pa.get("name") or fallback["PROTAGONIST"],
        "TRAITS": traits,
        "USER_NAME": pa.get("user_name") or fallback["USER_NAME"],
        "RELATION": relation,
    }


def get_length_mode(mode):
    """读取长度模式指令；normal / 未知返回空串（不额外约束）。"""
    return _load_prompts().get("length_modes", {}).get(mode, "")


def load_anchor():
    """读取当前生效的人设（带缓存）。

    v0.12.0 起人设已搬进数据库（personas 表），优先走库；库空时回退读旧的
    JSON 文件（persona_local.json 优先，它是可编辑覆盖层）。
    返回结构与旧版一致：{'persona': 名字, 'core_traits': [...], ...}，
    另附完整人格字段（rules / examples / world_template_id 等）。
    """
    global _anchor_cache, _anchor_mtime
    # v0.13.1：同样按文件 mtime 自动重载，直接编辑 persona_local.json 也能生效
    path = os.path.join(_BASE_DIR, "persona_anchor.json")
    local_path = os.path.join(_BASE_DIR, "persona_local.json")
    if os.path.exists(local_path):
        path = local_path
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = None

    # 库里有数据时，锚点跟着库走（bump_cache 由 persona_store 管），不受文件 mtime 影响
    from_db = False
    try:
        import persona_store
        if persona_store.active_persona():
            from_db = True
    except Exception:
        pass

    if _anchor_cache is None or mtime != _anchor_mtime or not from_db:
        try:
            if not from_db:
                try:
                    with open(path, encoding="utf-8") as f:
                        _anchor_cache = json.load(f)
                except Exception:
                    _anchor_cache = {}
                _anchor_mtime = mtime
            else:
                # 库有数据但还没建过缓存（或被 reload_anchor 清了）→ 现取
                pass
        except Exception:
            _anchor_cache = {}

        if from_db:
            try:
                import persona_store
                pa = persona_store.active_persona()
                if pa:
                    _anchor_cache = {
                        "persona": pa.get("name", ""),
                        "core_traits": pa.get("core_traits", []),
                        "speech_style": pa.get("speech_style", ""),
                        "reply_format": pa.get("reply_format", ""),
                        "anti_pattern": pa.get("anti_pattern", ""),
                        "_rule_levels": pa.get("rule_levels", {}),
                        "rules": pa.get("rules", []),
                        "examples": pa.get("examples", []),
                        "content_mode": pa.get("content_mode", ""),
                        "memory_priority": pa.get("memory_priority", ""),
                        "_persona": pa,
                    }
            except Exception:
                pass
    return _anchor_cache


def reload_anchor():
    """人设被修改后强制下次重新读取（引导落库、后台改设定时调用）"""
    global _anchor_cache
    _anchor_cache = None


def fallback_system_prompt():
    """会话未设置 system_prompt 时的简短回退人设，由当前人格驱动。"""
    pa = load_anchor()
    if not pa:
        return "你是一个温柔体贴的AI伴侣，像日常聊天一样自然地回复用户。"
    name = pa.get("persona", "")
    traits = "、".join(pa.get("core_traits", []) or [])
    style = pa.get("speech_style", "")
    return f"你是{name}，{traits}。{style}。"
