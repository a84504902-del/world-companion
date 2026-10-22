"""聊天相关路由"""
import json
import os
import hashlib
import asyncio
import threading
import logging
import re
from datetime import datetime
from aiohttp import web, ClientConnectionResetError

import db
import embedding
import llm
import loops
import memory_retriever
import scene
from prompts import get_prompt, persona_vars
from routes.custom_llm import get_llm_chat_func

logger = logging.getLogger("chat")

# 道晚安的短句匹配：晚安 / 早点睡吧 / 我睡了 / 先睡了 / 困了睡了 等。
# 允许「没事，早点睡吧」这类带一两个前置字的开头，但整体必须是短句，
# 避免把剧情里聊到的"睡觉"误判成下线。
_GOODNIGHT_RE = re.compile(
    r"^(?:嗯+|好|好的?|行|没事|那|好吧)?[，,]?\s*(?:我)?(?:先)?(?:早点)?(?:晚安|睡(?:觉|了?)?|困了[,，]?(?:先)?睡了?)"
    r"[吧啦哦呀~～。！!！]*$"
)


class SessionState:
    """单个会话的运行时状态"""
    def __init__(self):
        self.conversation_history = []
        self.message_count = 0
        self.cancel_event = threading.Event()  # 打断信号


# 按 session_id 隔离的会话状态缓存
_session_states = {}
_current_session_id = None
AUTO_SUMMARY_THRESHOLD = 15  # 自动摘要阈值

# TTS 懒生成缓存：text_hash -> 回复原文。
# 播放时 audio_handler 用它现场合成（进程重启后丢失，降级为从 DB 消息反查）
_tts_text_cache = {}

# ---- 后台检测节流（省钱：修复"每条消息 2-4 次 LLM 调用"）----
# 每类检测记录每个会话上次跑时的消息条数，不够间隔就跳过。
# 场景/人物变化慢，3-5 轮一次完全够用；承诺抽取自己有关键词预过滤，不在此列。
_DETECT_MIN_INTERVAL = {"characters": 4, "relationships": 6, "scene": 3}
_detect_last_run = {}  # (session_id, kind) -> 上次跑时的 message_count


def _resolve_session(request):
    """取出本次请求该用哪个会话。

    优先级：请求显式传的 session_id → 全局当前会话 → 最近活跃的会话。
    第三个兜底很重要：服务重启后 _current_session_id 是空的，
    此时不兜底就会去查一个空会话，看起来像"历史又丢了"。
    """
    sid = request.query.get("session_id") or request.query.get("sid")
    if sid:
        return sid
    if _current_session_id:
        return _current_session_id
    sessions = db.get_all_sessions()
    return sessions[0]["id"] if sessions else None


def _get_protagonist_name(session_id=None):
    """当前主角的名字（视角切换"切回"的目标）。

    v0.12.0 之前全项目硬编码"夏雪"，换个主角这套指令就全失效。
    这里从人格库动态取，取不到兜底"主角"（不会因库空而崩）。
    v0.17.5：传 session_id 时取该会话所属世界的主角（世界隔离，
    不同世界的"切回"回到各自的伴侣，不会串门）。
    """
    try:
        import persona_store
        pa = persona_store.active_persona(session_id)
        if pa and pa.get("name"):
            return pa["name"]
    except Exception:
        pass
    return "主角"


def _detect_due(session_id, kind):
    """这类检测现在该跑吗（按消息条数节流）"""
    state = get_state(session_id)
    if state is None:
        return False
    count = state.message_count
    last = _detect_last_run.get((session_id, kind))
    if last is None or count - last >= _DETECT_MIN_INTERVAL[kind]:
        _detect_last_run[(session_id, kind)] = count
        return True
    return False

# ---- 场景状态 ----
# 曾经试过让主模型在回复末尾附带 [[STATE]] 标记（零额外 LLM 调用），
# 但本项目人设太长（30+ 条规则），指令被稀释，**实测遵守率只有 20%**。
# 已改为后台独立判断（见 scene.py）。这里只保留剥离函数，清理可能残留的标记。
_SCENE_RE = re.compile(r"\[\[STATE\]\]\s*(.*)")


def _extract_scene(text):
    """从回复里剥离 [[STATE]] 标记，返回 (正文, 场景dict 或 None)"""
    if not text:
        return text, None
    m = _SCENE_RE.search(text)
    if not m:
        return text, None
    body = text[:m.start()].rstrip()
    raw = m.group(1).strip()
    scene = {}
    for part in raw.split("|"):
        if "=" in part:
            k, v = part.split("=", 1)
            scene[k.strip()] = v.strip()
    return body, (scene or None)


def _run_bg(fn, name):
    """提交后台任务，并确保异常被记录。

    run_in_executor 返回 Future，函数内部的异常发生在 Future 里，
    外层的 try/except 抓不到——不处理就会静默丢失
    （日志里只会留一句 "Future exception was never retrieved"）。
    """
    try:
        loop = asyncio.get_event_loop()
        fut = loop.run_in_executor(None, fn)

        def _done(f):
            try:
                exc = f.exception()
            except Exception:
                return
            if exc:
                logger.error("%s 后台任务失败: %s", name, exc)

        fut.add_done_callback(_done)
    except Exception as e:
        logger.error("%s 提交失败: %s", name, e)


def default_session_title():
    """新会话的默认名字：用时间。

    不能用"新对话"——它会跟左栏那个「+ 新对话」**按钮**撞名，
    用户会以为标题栏也是个按钮（踩过）。
    """
    now = datetime.now()
    h = now.hour
    if h < 5:
        seg = "凌晨"
    elif h < 9:
        seg = "早上"
    elif h < 12:
        seg = "上午"
    elif h < 14:
        seg = "中午"
    elif h < 18:
        seg = "下午"
    elif h < 23:
        seg = "晚上"
    else:
        seg = "深夜"
    return f"{now.month}月{now.day}日 {seg}"


def title_from_first_message(text):
    """从第一句话里取会话标题；太短或没意义就返回 None（保持默认名）。

    "2" / "22" / "在吗" 这类不该成为标题——原来会照收，
    结果左栏出现一堆没意义的会话名。
    """
    t = (text or "").strip()
    if len(t) < 6:
        return None
    if re.fullmatch(r"[\d\s\W_]+", t):      # 纯数字/符号
        return None
    t = t.replace("\n", " ")
    return t[:50] + ("..." if len(t) > 50 else "")


def get_state(session_id):
    """获取指定会话的状态，不存在则自动创建"""
    if session_id and session_id not in _session_states:
        _session_states[session_id] = SessionState()
    return _session_states.get(session_id)


def _classify_pin_slot(content):
    """钦点记忆的槽位分类 —— 纯关键词规则，机械可审计，不让 LLM 凭感觉判"""
    if re.search(r"出差|明天|后天|下周|周末|晚上|约会|纪念日|提醒|带.*去|要.*陪", content):
        return "约定安排"
    if re.search(r"讨厌|不喜欢|雷区|过敏|别再|不要再", content):
        return "雷区偏好"
    if re.search(r"同意|共识|默契|愿意|答应过你|我们的关系", content):
        return "关系共识"
    if re.search(r"朋友|同事|同学|哥|姐|爸|妈|妹妹|弟弟", content):
        return "人物印象"
    return "其他"


def _handle_pin_command(session_id, content, mode="deepseek"):
    """处理"记住：X"钦点指令：归槽 → 去重（相似度≥0.95 视为同一条，替换旧的）→
    写常驻层 → 让夏雪本人回一句确认（回执，替代旧的机械"已保存记忆"）"""
    slot = _classify_pin_slot(content)

    # 去重：与现有生效条目相似度过高 → 替换（同槽位 ≥0.95，跨槽位 ≥0.97）
    try:
        new_vec = embedding.embed_text(content)
    except Exception:
        new_vec = None
    for pin in db.list_active_pins(session_id):
        if new_vec:
            try:
                old_vec = embedding.embed_text(pin["content"])
                sim = embedding.cosine_similarity(new_vec, old_vec) if old_vec else 0
            except Exception:
                sim = 0
        else:
            sim = 1.0 if pin["content"] == content else 0
        if sim >= (0.95 if pin["slot"] == slot else 0.97):
            db.supersede_pin(pin["id"])

    db.add_pinned(session_id, content, slot)
    logger.info("钦点记忆已写入: session=%s, slot=%s, 字数=%d", session_id, slot, len(content))

    # 回执：让角色本人确认（写入成功她才知道），失败则降级为模板句
    try:
        chat_func = get_llm_chat_func(mode)
        if chat_func:
            # v0.12.0：角色名/称呼从人格库动态取，换主角后回执也跟着变
            # v0.17.5：按会话所属世界取（世界隔离）
            _pn = _get_protagonist_name(session_id)
            try:
                import persona_store as _ps3
                _pp = _ps3.active_persona(session_id) or {}
            except Exception:
                _pp = {}
            _un = _pp.get("user_name") or "对方"
            _ptraits = "、".join(_pp.get("core_traits") or []) or "大方、活泼、直接"
            confirm_messages = [
                {"role": "system", "content":
                    f"你是{_pn}，{_un}的女朋友。性格：{_ptraits}。"
                    f"{_un}刚才对你说了一句要我记住的话。"
                    f"请以{_pn}的身份自然回应，1-2句，表示记住了。"
                    "禁止出现「命令」「系统」「已保存」「记录」这类词，就像日常聊天一样答应下来。"},
                {"role": "user", "content": f"记住：{content}"}
            ]
            reply = (chat_func(confirm_messages) or "").strip()
            if reply:
                return reply
    except Exception as e:
        logger.error("钦点记忆回执生成失败: %s", e)
    return f"嗯嗯，我记住了：{content}"


async def chat_handler(request):
    """处理聊天请求（支持流式输出）"""
    global _current_session_id

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "无效的JSON"}, status=400)

    text = data.get("text", "").strip()
    mode = data.get("mode", "deepseek")
    # v0.15.2：默认长度 normal → long。原来默认 normal 而 prompts.json 里
    # normal 没有定义，等于不发送任何长度引导，回复容易变得干巴巴的短。
    length_mode = data.get("length_mode", "long")
    if length_mode not in ("short", "normal", "long"):
        length_mode = "long"
    group_mode = bool(data.get("group_mode", False))
    
    logger.info("收到聊天请求: 字数=%d, mode=%s, length_mode=%s", len(text), mode, length_mode)

    if not text:
        return web.json_response({"error": "消息不能为空"}, status=400)

    # ===== 会话隔离（修复"两个标签页串会话"）=====
    # 优先用请求体带的 session_id（前端每个标签页带自己的 state.currentSession），
    # 没带才退到全局兜底。同时把全局同步过来，让 /history 等老接口行为不变。
    session_id = (data.get("session_id") or "").strip() or _current_session_id
    _current_session_id = session_id

    state = get_state(session_id)

    # ===== 晚安跳转：用户道晚安 = 剧情直接过一夜，明早见 =====
    # 解决"现实深夜想下线，剧情里却是早上"的时间错位——
    # 说晚安就该是过夜，下次回来时间自然对上。
    _goodnight_note = ""
    if len(text) <= 20 and _GOODNIGHT_RE.match(text):
        try:
            import timeflow as _tf
            _jump = _tf.skip_to_morning(session_id)
            if _jump:
                _goodnight_note = (
                    f"\n【晚安跳转】用户跟你道晚安下线了，剧情时间已直接跳到明天早上 "
                    f"{_jump['story_now'][11:16]}。用一句自然的晚安收尾"
                    f"（可以带个小睡前动作），不要开新话题，不要追问，不要长篇")
                logger.info("晚安跳转触发: → %s", _jump["story_now"])
        except Exception as _ge:
            logger.error("晚安跳转失败: %s", _ge)

    # ===== 视角切换指令（"切换视角：X" / "扮演：X"，切回主角传主角名或"切回"）=====
    # v0.12.0：主角名从人格库动态取，换主角后这些指令自动跟着变
    # v0.17.5：按本会话所属世界取主角（切回 = 回本世界的伴侣，不串门）
    _PROTAGONIST = _get_protagonist_name(session_id)
    _POV_PREFIXES = ("切换视角：", "切换视角:", "扮演：", "扮演:", "视角：", "视角:")
    _txt = text.strip()
    _back_words = {"切回", f"切回{_PROTAGONIST}"}
    _pov_matched = next((p for p in _POV_PREFIXES if text.startswith(p)), None)
    if _pov_matched or _txt in _back_words:
        if _txt in _back_words:
            _pov_name = _PROTAGONIST
        else:
            _pov_name = text[len(_pov_matched):].strip(" ：:　 ")
        if not _pov_name or _pov_name == _PROTAGONIST:
            db.set_actor(session_id, "")
            return web.json_response(
                {"response": f"（视角已切回：{_PROTAGONIST}）", "audio_url": None})
        # 可切换对象 = 本会话人物表里的人 + 本世界的角色（v0.17.5：按 world_scope 过滤，不串门）
        _valid = {p["name"] for p in db.get_people(session_id)}
        try:
            import persona_store as _ps2
            _scope = _ps2.session_world_scope(session_id)
            _valid |= {p["name"] for p in _ps2.list_personas_by_scope(_scope) if p.get("name")}
        except Exception:
            pass
        if _pov_name not in _valid:
            return web.json_response(
                {"response": f"（人物表里没有「{_pov_name}」，无法切换。当前可选：{'、'.join(sorted(_valid)) or '暂无'}）",
                 "audio_url": None})
        # 防呆：用户自己的名字不能当扮演角色（否则主次颠倒，AI 把你自己演了）
        try:
            import persona_store as _ps3
            _my_uname2 = (_ps3.active_persona(session_id) or {}).get("user_name") or ""
        except Exception:
            _my_uname2 = ""
        if _my_uname2 and _pov_name == _my_uname2:
            return web.json_response(
                {"response": f"（{_my_uname2}就是你自己呀，不用切换——你一直都在。"
                 f"说「切回{_PROTAGONIST}」可回到{_PROTAGONIST}的视角）",
                 "audio_url": None})
        db.set_actor(session_id, _pov_name)
        return web.json_response(
            {"response": f"（视角已切换：现在由「{_pov_name}」与你对话。说「切回{_PROTAGONIST}」可切回）",
             "audio_url": None})

    # ===== 钦点记忆指令（"记住：X"等 → 常驻注入层，100% 可靠）=====
    _PIN_PREFIXES = ("记住：", "记住:", "帮我记着", "帮我记住", "别忘了：", "别忘了:")
    _pin_matched = next((p for p in _PIN_PREFIXES if text.startswith(p)), None)
    if _pin_matched:
        pin_content = text[len(_pin_matched):].strip(" ：:　 ")
        if pin_content:
            _pin_reply = _handle_pin_command(session_id, pin_content, mode=mode)
            return web.json_response({"response": _pin_reply, "audio_url": None})
        return web.json_response({"response": "想让我记住什么呀？格式是：记住：内容", "audio_url": None})

    # 处理保存记忆指令（存检索池：聊到相关话题时可能想起，非常驻）
    if text.startswith("保存记忆：") or text.startswith("保存记忆:"):
        memory_content = text.split("：", 1)[-1] if "：" in text else text.split(":", 1)[-1]
        memory_retriever.store_memory_with_embedding(
            memory_content.strip(), session_id=session_id
        )
        return web.json_response({"response": "已保存记忆", "audio_url": None})

    # 获取会话的系统提示词
    session = db.get_session(session_id)
    system_prompt = session.get("system_prompt", "") if session else ""

    # ===== 加载持久化人格锚定（每次对话必注入，优先级最高）=====
    # v0.12.0：人设走人格库（personas 表），库空自动回退旧 JSON 文件
    _persona_text = ""
    _format_rule = ""
    _protagonist_name = ""
    try:
        import persona_store as _ps
        # v0.17.5：取本会话绑定的角色（世界隔离——多世界共存时，
        # 聊天必须用这个会话自己的伴侣，不能拿全局主角顶替）
        _pa = _ps.active_persona(session_id)
        if _pa:
            _persona_text, _format_rule = _ps.persona_to_text(_pa)
            _protagonist_name = _pa.get("name", "")
    except Exception as _e:
        pass
    # v0.17.5：本会话的称呼（群像规则等用，跟随会话角色）
    try:
        _session_user_name = (_pa or {}).get("user_name") or ""
    except Exception:
        _session_user_name = ""

    # ===== 多角色扮演：视角不在主角时，加载该角色的完整人格 =====
    _actor = db.get_actor(session_id)
    # 防呆：历史脏数据兜底——扮演用户自己 = 主次颠倒（用户的我、AI的我都是林风），
    # 遇到直接清掉，回默认主角
    if _actor:
        try:
            _my_uname = (_ps.active_persona(session_id) or {}).get("user_name") or ""
        except Exception:
            _my_uname = ""
        if _my_uname and _actor == _my_uname:
            logger.warning("发现非法视角 %r（= 用户自己），已清空回主角", _actor)
            _actor = ""
            db.set_actor(session_id, "")
    if _actor and _actor != _protagonist_name:
        _ap = next((p for p in db.get_people(session_id) if p["name"] == _actor), None)
        # v0.12.0：优先用人格库里该角色的完整人格（引导生成的全套设定）；
        # 没有才退回原来的「临场拼人设」，保证旧数据不受影响
        _actor_persona = None
        try:
            _actor_persona = (_ps.get_persona(_ap.get("persona_id")) if _ap else None) \
                or _ps.get_persona_by_name(_actor)
        except Exception:
            _actor_persona = None

        if _actor_persona:
            _core, _fmt = _ps.persona_to_text(_actor_persona)
            _user_name = _actor_persona.get("user_name") or "用户"
            # v0.16.0：骨架文案在 prompts/chat.json（chat.pov_full），此处 f-string 仅兜底
            _persona_text = get_prompt(
                "chat.pov_full",
                default=("【绝对不可违反的核心设定 · 角色扮演模式】\n"
                         f"你现在是「{_actor}」，不是主角{_protagonist_name}。\n"
                         + _core
                         + f"用户发来的「我」= {_user_name}，你回复里的「我」= {_actor}。\n"
                           f"全程以{_actor}的第一人称说话，禁止用第三人称描述自己。\n"
                           f"注意：对话历史里 assistant 的发言属于{_protagonist_name}视角，"
                           f"不是你说过的话，不要延续。\n"),
                ACTOR=_actor, PROTAGONIST=_protagonist_name,
                USER_NAME=_user_name, PERSONA_TEXT=_core,
            )
            _format_rule = _fmt
        elif _ap:
            _head = []
            if _ap.get("age"):
                _head.append(f"{_ap['age']}岁")
            if (_ap.get("occupation") or "").strip():
                _head.append(_ap["occupation"].strip())
            _traits = []
            if (_ap.get("personality") or "").strip():
                _traits.append(f"性格：{_ap['personality'].strip()}")
            if (_ap.get("speech_style") or "").strip():
                _traits.append(f"说话方式：{_ap['speech_style'].strip()}")
            if (_ap.get("likes") or "").strip():
                _traits.append(f"爱好：{_ap['likes'].strip()}")
            if (_ap.get("dislikes") or "").strip():
                _traits.append(f"雷区：{_ap['dislikes'].strip()}")
            _desc = (_ap.get("description") or "").strip()
            _profile = f"（{'·'.join(_head)}）" if _head else ""
            _profile_detail = ""
            if _desc:
                _profile_detail += "身份：" + _desc + "。\n"
            if _traits:
                _profile_detail += "性格与说话：" + "；".join(_traits) + "。\n"
            # v0.16.0：骨架文案在 prompts/chat.json（chat.pov_lite），此处 f-string 仅兜底
            _persona_text = get_prompt(
                "chat.pov_lite",
                default=("【绝对不可违反的核心设定 · 角色扮演模式】\n"
                         f"你现在是「{_actor}」{_profile}。\n"
                         + _profile_detail
                         + f"用户发来的「我」= 用户本人，你回复里的「我」= {_actor}。\n"
                           f"全程以{_actor}的第一人称说话，禁止用第三人称描述自己。\n"
                           f"注意：对话历史里 assistant 的发言属于{_protagonist_name}视角，"
                           f"不是你说过的话，不要延续。\n"
                           "通用规则：\n"
                           "- 回复为纯对话格式，不写括号，不写小说叙述，不自己推进剧情，等用户下一步指令\n"
                           "- 禁止说「作为AI」「我不能」等出戏的话\n"
                           "- 称呼严格按【人物称呼】段执行\n"),
                ACTOR=_actor, PROFILE=_profile, PROFILE_DETAIL=_profile_detail,
                PROTAGONIST=_protagonist_name,
            )
            _format_rule = ""

    # ===== 时间维度（剧情时钟：持久状态 + 饱和函数，替代旧的常数 TIME_SCALE）=====
    _time_text = ""
    try:
        import timeflow
        _time_text = timeflow.describe(session_id) or ""
        if _goodnight_note:
            _time_text += _goodnight_note
    except Exception as _te:
        logger.error("时间维度计算失败: %s", _te)
        _time_text = _goodnight_note

    # ===== 组装每轮注入的上下文（人格锚定 → 人物卡 → 关系 → 世界状态 → 摘要 → 检索记忆）=====
    context_parts = []

    # 0. 人格锚定（放在所有上下文最前面，确保优先级最高）
    if _persona_text:
        context_parts.insert(0, _persona_text)

    # 0.5. 时间维度（紧接人格锚定之后）
    if _time_text:
        context_parts.insert(1, _time_text)

    # 0.55. 钦点记忆（用户"记住：X"写入的，常驻注入 —— 位置在第2位：
    #   末尾会被 30+ 条人设淹没（教训），最顶又会被过度执行（实测），第2位刚好）
    _pins = db.list_active_pins(session_id)
    if _pins:
        _pin_lines = "\n".join(f"・{_p['slot']}：{_p['content']}" for _p in _pins)
        # v0.16.0：标题骨架在 prompts/chat.json（chat.pin_memory）。
        # 顺手修掉原硬编码「林风」——现在跟随人格库 user_name（换主角自动跟着变）
        try:
            _pin_user = (_ps.active_persona(session_id) or {}).get("user_name") or "用户"
        except Exception:
            _pin_user = "用户"
        _pins_text = get_prompt(
            "chat.pin_memory",
            default=("【你与林风之间已确立的事实（背景设定。聊到相关话题时自然体现即可，"
                     "禁止主动罗列、禁止每轮复述）】\n"),
            USER_NAME=_pin_user,
        ) + _pin_lines
        context_parts.insert(2, _pins_text)

    # 0.6. 强制格式规则（如果有，插入到system_prompt之前）
    if _format_rule:
        if system_prompt:
            system_prompt = f"{_format_rule}\n\n{system_prompt}"
        else:
            system_prompt = _format_rule

    # 0.7. 场景状态输出要求 —— 不在这里加，见下方 memory_text 末尾
    # （试过放 system 最前面，会被 30+ 条人设规则稀释掉，模型直接忽略）

    # 1. 人物卡（结构化字段渲染：只渲染填了的字段，空字段不出现）
    people = db.get_people(session_id)
    if people:
        people_lines = []
        for p in people:
            head_parts = []
            if p.get("age"):
                head_parts.append(f"{p['age']}岁")
            if (p.get("occupation") or "").strip():
                head_parts.append(p["occupation"].strip())
            head = f"（{'·'.join(head_parts)}）" if head_parts else ""

            traits = []
            if (p.get("personality") or "").strip():
                traits.append(f"性格：{p['personality'].strip()}")
            if (p.get("speech_style") or "").strip():
                traits.append(f"说话：{p['speech_style'].strip()}")
            if (p.get("likes") or "").strip():
                traits.append(f"爱好：{p['likes'].strip()}")
            if (p.get("dislikes") or "").strip():
                traits.append(f"雷区：{p['dislikes'].strip()}")
            desc = (p.get("description") or "").strip()
            if desc:
                traits.append(desc)
            if (p.get("birthday") or "").strip():
                traits.append(f"生日：{p['birthday'].strip()}")

            line = f"- {p['name']}{head}"
            if traits:
                line += "｜" + "｜".join(traits)
            people_lines.append(line)
        context_parts.append("【当前人物】\n" + "\n".join(people_lines))

    # 2. 人物关系（方向正确的模板：A 与 B 是X关系，修复"夏雪 是 夏海 的父女"病句）
    relations = db.get_relations(session_id)
    if relations:
        people_map = {p["id"]: p["name"] for p in people}
        rel_lines = []
        for r in relations:
            name_a = people_map.get(r["person_a_id"], "未知")
            name_b = people_map.get(r["person_b_id"], "未知")
            line = f"- {name_a} 与 {name_b} 是{r['relation_type']}关系"
            # 称呼是数据不是推理（v0.11.26）：填了就明写，模型照着念，不做亲属推理题
            calls = []
            if (r.get("call_a_to_b") or "").strip():
                calls.append(f"{name_a}称{name_b}「{r['call_a_to_b'].strip()}」")
            if (r.get("call_b_to_a") or "").strip():
                calls.append(f"{name_b}称{name_a}「{r['call_b_to_a'].strip()}」")
            if calls:
                line += f"（{'；'.join(calls)}）"
            rel_lines.append(line)
        context_parts.append("【人物关系】\n" + "\n".join(rel_lines))

    # 3. 世界状态卡（覆盖式维护的当前事实，解决"100轮前的关系第101轮被当陌生人"）
    state_card = db.get_session_state(session_id)
    if state_card:
        context_parts.append("【当前世界状态（已确认事实，必须遵循，不可遗忘）】\n" + state_card)

    # 3.5 她在等的事（未完结的承诺 —— 核心引擎，本轮最多提一条）
    # 位置紧跟人格锚定和时间之后：放在末尾会被 30+ 条人设规则淹没，模型直接忽略
    try:
        _loops_text = loops.build_loops_prompt(session_id)
        if _loops_text:
            context_parts.insert(min(2, len(context_parts)), _loops_text)
    except Exception as e:
        logger.error("未完结事件注入失败: %s", e)

    # 4. 周期摘要（历史事件压缩，修复"摘要只存不注入"）
    summaries = db.get_recent_summaries(session_id, limit=2)
    if summaries:
        summary_lines = [f"- {s['summary']}" for s in summaries]
        context_parts.append("【此前对话摘要】\n" + "\n".join(summary_lines))

    # 5. 语义检索：对话原文片段 + 手动保存的记忆
    retrieved_memory = ""
    try:
        retrieved_memory = await asyncio.get_event_loop().run_in_executor(
            None, lambda: memory_retriever.build_memory_context(text, session_id=session_id)
        )
    except Exception as e:
        logger.error("记忆检索失败: %s", e)

    if retrieved_memory:
        context_parts.append(retrieved_memory)

    memory_text = "\n\n".join(context_parts)

    # 准备 SSE 响应
    resp = web.StreamResponse()
    resp.headers["Content-Type"] = "text/event-stream; charset=utf-8"
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    await resp.prepare(request)

    full_response = ""

    # 尝试流式输出
    try:
        stream_gen = llm.stream_chat(text, mode, state.conversation_history,
                                      system_prompt=system_prompt, memory_text=memory_text,
                                      length_mode=length_mode, group_mode=group_mode,
                                      actor=_actor,
                                      protagonist=_protagonist_name, user_name=_session_user_name)
    except Exception:
        stream_gen = None

    if stream_gen is not None:
        # 流式：用 asyncio.Queue 桥接 executor 线程和 async handler
        state.cancel_event.clear()
        loop = asyncio.get_event_loop()
        queue = asyncio.Queue()

        def _run_stream():
            try:
                for chunk in stream_gen:
                    if state.cancel_event.is_set():
                        break
                    loop.call_soon_threadsafe(queue.put_nowait, ("chunk", chunk))
                if not state.cancel_event.is_set():
                    loop.call_soon_threadsafe(queue.put_nowait, ("done", None))
                else:
                    loop.call_soon_threadsafe(queue.put_nowait, ("cancelled", None))
            except Exception as e:
                loop.call_soon_threadsafe(queue.put_nowait, ("error", str(e)))

        asyncio.get_event_loop().run_in_executor(None, _run_stream)

        cancelled = False
        while True:
            kind, payload = await queue.get()
            if kind == "chunk":
                full_response += payload
                try:
                    # 清理特殊字符，避免latin-1编码错误
                    safe_payload = payload.encode('utf-8', errors='ignore').decode('utf-8', errors='ignore')
                    await resp.write(f"data: {json.dumps({'content': safe_payload}, ensure_ascii=False)}\n\n".encode("utf-8"))
                except (ClientConnectionResetError, ConnectionResetError, ConnectionError):
                    cancelled = True
                    break
            elif kind == "done":
                break
            elif kind == "cancelled":
                cancelled = True
                break
            elif kind == "error":
                try:
                    await resp.write(f"data: {json.dumps({'error': payload}, ensure_ascii=False)}\n\n".encode("utf-8"))
                    await resp.write_eof()
                except (ClientConnectionResetError, ConnectionResetError, ConnectionError):
                    pass
                return resp
    else:
        # 非流式回退
        cancelled = False
        try:
            full_response = await asyncio.get_event_loop().run_in_executor(
                None, lambda: llm.chat(text, mode, state.conversation_history,
                                       system_prompt=system_prompt, memory_text=memory_text,
                                       length_mode=length_mode, group_mode=group_mode,
                                       actor=_actor,
                                       protagonist=_protagonist_name, user_name=_session_user_name)
            )
        except Exception as e:
            try:
                await resp.write(f"data: {json.dumps({'error': f'LLM 调用失败: {e}'}, ensure_ascii=False)}\n\n".encode("utf-8"))
                await resp.write_eof()
            except (ClientConnectionResetError, ConnectionResetError, ConnectionError):
                pass
            return resp
        try:
            # 清理特殊字符，避免latin-1编码错误
            safe_response = full_response.encode('utf-8', errors='ignore').decode('utf-8', errors='ignore')
            await resp.write(f"data: {json.dumps({'content': safe_response}, ensure_ascii=False)}\n\n".encode("utf-8"))
        except (ClientConnectionResetError, ConnectionResetError, ConnectionError) as e:
            print(f"[DEBUG] 连接错误: {e}")
            cancelled = True

    # 被打断时：保存已有内容，跳过 TTS 和摘要
    if cancelled:
        if full_response:
            full_response, _sc = _extract_scene(full_response)
            if _sc:
                try:
                    db.set_scene(session_id, place=_sc.get("地点", ""),
                                 her_doing=_sc.get("她在", ""), user_place=_sc.get("你", ""))
                except Exception:
                    pass
            _ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            state.conversation_history.append({"role": "user", "content": text, "timestamp": _ts})
            state.conversation_history.append({"role": "assistant", "content": full_response, "timestamp": _ts})
            db.save_message(session_id, "user", text)
            db.save_message(session_id, "assistant", full_response)
            state.message_count += 2
        try:
            await resp.write(f"data: {json.dumps({'done': True, 'cancelled': True, 'audio_url': None, 'summary': None}, ensure_ascii=False)}\n\n".encode("utf-8"))
            await resp.write_eof()
        except (ClientConnectionResetError, ConnectionResetError, ConnectionError):
            pass
        return resp

    # 空回复兜底：模型未返回任何内容时，不保存空消息（避免污染历史），并明确提示
    if not full_response.strip():
        try:
            await resp.write(f"data: {json.dumps({'error': 'AI 没有生成回复，请换一种说法重试'}, ensure_ascii=False)}\n\n".encode("utf-8"))
            await resp.write_eof()
        except (ClientConnectionResetError, ConnectionResetError, ConnectionError):
            pass
        return resp

    # 剥离场景状态标记：正文里不该出现它，同时把场景存下来
    full_response, _scene = _extract_scene(full_response)
    if _scene:
        try:
            db.set_scene(session_id,
                         place=_scene.get("地点", ""),
                         her_doing=_scene.get("她在", ""),
                         user_place=_scene.get("你", ""))
            logger.info("场景更新: %s", _scene)
        except Exception as e:
            logger.error("场景状态保存失败: %s", e)

    # 保存消息到数据库（带时间戳）
    # timestamp 必须带上：模型要靠它判断"这条消息是多久前说的"，
    # 否则跨越几天的对话会被当成连续的、刚发生的聊天
    _ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    state.conversation_history.append({"role": "user", "content": text, "timestamp": _ts})
    state.conversation_history.append({"role": "assistant", "content": full_response, "timestamp": _ts})
    db.save_message(session_id, "user", text)
    db.save_message(session_id, "assistant", full_response)
    state.message_count += 2

    # 更新会话标题（用第一句话，太短/没意义的不用）
    if len(state.conversation_history) == 2:
        if not db.is_title_locked(session_id):
            title = title_from_first_message(text)
            if title:
                db.update_session(session_id, title)

    # 自动摘要
    summary = None
    if state.message_count >= AUTO_SUMMARY_THRESHOLD * 2:
        summary = await _auto_summarize(session_id)
        state.message_count = 0

    # 生成 TTS 音频（懒生成：不在这里合成，audio_url 指向 /audio/<hash>，
    # 前端点播放时 audio_handler 现场合成——修复 edge-tts 1-2s 卡在回复热路径）
    audio_url = None
    try:
        safe_text = full_response.encode('utf-8', errors='ignore').decode('utf-8', errors='ignore')
        text_hash = hashlib.md5(safe_text.encode('utf-8')).hexdigest()[:12]
        _tts_text_cache[text_hash] = full_response  # 供 audio_handler 命中合成
        audio_url = f"/audio/{text_hash}"
    except Exception:
        pass

    # 发送完成事件（带时间戳）
    from datetime import datetime as _dt
    _response_time = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
    done_data = {"done": True, "audio_url": audio_url, "summary": summary, "timestamp": _response_time}
    try:
        await resp.write(f"data: {json.dumps(done_data, ensure_ascii=False)}\n\n".encode("utf-8"))
        await resp.write_eof()
    except (ClientConnectionResetError, ConnectionResetError, ConnectionError):
        pass

    # ---- 后台任务 ----
    # 统一用 _run_bg 提交：run_in_executor 的异常在 Future 里，
    # 外层 try 是抓不到的，不处理就会静默丢失（只在日志里留一句 asyncio 警告）
    _run_bg(lambda: memory_retriever.index_conversation_chunks(session_id),
            "原文块索引")
    # 场景/人物/关系检测带节流（见 _detect_due）：不够间隔就跳过，直接省 LLM 调用
    if _detect_due(session_id, "characters"):
        _run_bg(lambda: _detect_new_characters(session_id, text, full_response), "人物检测")
    if _detect_due(session_id, "relationships"):
        _run_bg(lambda: _detect_relationships(session_id), "关系检测")
    _run_bg(lambda: loops.extract_loop(text, session_id, loops.make_llm_call(mode)),
            "承诺抽取")
    if _detect_due(session_id, "scene"):
        _run_bg(lambda: scene.extract_scene(session_id, scene.make_llm_call(mode)),
                "场景更新")
    # 定期清理不了了之的承诺（没有这一步，承诺表会无限膨胀）
    _run_bg(lambda: loops.cleanup_stale_loops(session_id), "承诺清理")

    try:
        await resp.write_eof()
    except (ClientConnectionResetError, ConnectionResetError, ConnectionError):
        pass
    return resp


async def _auto_summarize(session_id):
    """自动生成摘要 + 世界状态卡（每 15 轮一次）：
    - 摘要落库（summaries 表），修复"摘要只存不注入"——注入链路在 chat_handler 每轮读取
    - 世界状态卡覆盖式写入（session_states 表），维护"当前依然成立的事实"，解决长期未提及信息的遗忘
    """
    state = get_state(session_id)
    if not state or len(state.conversation_history) < AUTO_SUMMARY_THRESHOLD * 2:
        return None

    # 组装对话文本
    chat_text = ""
    for msg in state.conversation_history[-30:]:  # 最近30条
        role = "用户" if msg["role"] == "user" else "AI"
        chat_text += f"{role}: {msg['content'][:200]}\n"  # 截断长消息

    try:
        chat_func = get_llm_chat_func("deepseek")  # 用默认 LLM 生成摘要

        if not chat_func:
            return None

        # 1) 生成滚动摘要并落库（旧摘要参与新摘要，保证跨窗口连续性）
        prev_summaries = db.get_recent_summaries(session_id, limit=1)
        prev_summary = prev_summaries[0]["summary"] if prev_summaries else ""
        summary_input = chat_text
        if prev_summary:
            summary_input = f"【上一份摘要（更早的剧情，必须延续）】\n{prev_summary}\n\n【最近的对话】\n{chat_text}"
        messages = [
            {"role": "system", "content": get_prompt(
                "summary_rolling",
                "你是摘要助手。把「上一份摘要」和「最近的对话」合并成一份连贯的剧情摘要，200字以内。"
                "人物关系的变化、重要约定、关键事件必须保留；已完结的琐事可以压缩成一句话。只输出摘要正文。")},
            {"role": "user", "content": summary_input}
        ]
        summary = (chat_func(messages) or "").strip()

        if summary:
            db.save_summary(session_id, summary)

        # 2) 生成世界状态卡（覆盖式写入，只保留当前依然成立的长期事实）
        # v0.13.1：文案在 prompts.json，用 {{PROTAGONIST}}/{{TRAITS}} 占位，值从人格库取。
        # 之前这里硬编码"夏雪"且 get_prompt 是 json 优先 → 换个角色，状态卡还按夏雪的底色写。
        _pv = persona_vars()
        state_prompt = get_prompt(
            "state_card",
            "你是世界状态记录器。根据以下对话，提取【当前依然成立】的世界状态。"
            "只保留长期有效的信息：人物关系、剧情状态、约定承诺、偏好习惯、矛盾心结。"
            "丢弃一次性闲聊和已经结束的临时话题。\n"
            f"【重要】{_pv['PROTAGONIST']}的性格底色是{_pv['TRAITS']}（以人设为准）：\n"
            "- 不要把对话里被纠正时的道歉、犹豫、紧张写成「矛盾/心结」——那只是当时的反应，不是长期事实\n"
            "- 用户要求她改说话/性格风格时，写成正向描述（如「"
            f"{_pv['PROTAGONIST']}{_pv['TRAITS']}」），"
            "不要写成「她在努力克服某种缺点」这类保留了负面前提的表述\n"
            "严格只输出 JSON，不要任何其他文字：\n"
            '{"relations": ["A 和 B 是X关系"], "events": ["正在进行的剧情"], '
            '"promises": ["承诺/约定"], "preferences": ["偏好/习惯"], "conflicts": ["矛盾/心结"]}\n'
            "没有的项输出空数组。"
            , **_pv
        ) + "\n\n对话：\n" + chat_text
        state_raw = (chat_func([{"role": "user", "content": state_prompt}]) or "").strip()
        state_card = _format_state_card(state_raw)
        if state_card:
            db.save_session_state(session_id, state_card)

        # 摘要已存入 summaries 表，不再覆盖会话标题
        # （旧逻辑用摘要当标题是因为没有独立的摘要表，现在已不需要）

        return summary
    except Exception:
        return None


def _format_state_card(raw):
    """把 LLM 输出的状态 JSON 渲染成每轮注入的文本；解析失败则原样返回"""
    raw = raw.strip()
    try:
        # 去掉可能的 ```json 代码块围栏
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)
        labels = {
            "relations": "人物关系",
            "events": "进行中事件",
            "promises": "约定/承诺",
            "preferences": "偏好/习惯",
            "conflicts": "矛盾/心结",
        }
        lines = []
        for key, label in labels.items():
            items = data.get(key) or []
            for it in items:
                if isinstance(it, str) and it.strip():
                    lines.append(f"- {label}：{it.strip()}")
        return "\n".join(lines) if lines else ""
    except Exception:
        # 解析失败：原文长度足够才保留，避免把模型废话注入
        return raw if len(raw) > 10 else ""


def _detect_new_characters(session_id, user_text, ai_text):
    """后台检测对话中出现的新人物 → 存提案 → 前端通知用户确认（提案制，防止乱加）"""
    try:
        existing = db.get_people(session_id)
        existing_names = [p["name"] for p in existing]

        chat_func = get_llm_chat_func("deepseek")
        if not chat_func:
            return

        # 读取最近几轮对话作为上下文（解决单轮信息不足导致漏检）
        all_msgs = db.load_messages(session_id)
        recent_msgs = all_msgs[-10:] if len(all_msgs) > 10 else all_msgs
        context_lines = []
        for m in recent_msgs:
            role = "用户" if m["role"] == "user" else "AI"
            context_lines.append(f"{role}：{m['content'][:200]}")
        recent_context = "\n".join(context_lines) if context_lines else f"用户：{user_text[:500]}\nAI：{ai_text[:500]}"

        existing_list = "、".join(existing_names) if existing_names else "（无）"
        # v0.13.1：占位符统一交给 get_prompt 渲染；{{PROTAGONIST}} 让提取提示词跟着
        # 当前角色走（换主角后不会再把旧角色名当模板写死）。
        _pv = persona_vars()
        prompt = get_prompt(
            "detect_character",
            "从以下对话中提取所有出现的人物名字。\n\n"
            "已有人物列表：{{EXISTING_LIST}}\n\n"
            "最近对话：\n{{RECENT_CONTEXT}}\n\n"
            "提取要求：\n"
            "1. 提取所有出现的专有人名，包括：\n"
            f"   - AI自称的名字或用户对AI的称呼（当前扮演角色是{_pv['PROTAGONIST']}，"
            "但以对话中实际出现的名字为准），description写'AI扮演的角色'\n"
            "   - 对话中提到的其他人名\n"
            "2. 不提取：代词（他/她/它）、普通名词、用户本人的名字\n"
            "3. 已在列表中的不重复提取\n"
            "4. 没有人名则输出空数组\n\n"
            '只输出JSON：\n'
            '[{"name": "人名", "description": "身份/与用户关系"}]',
            EXISTING_LIST=existing_list,
            RECENT_CONTEXT=recent_context,
            **_pv
        )

        raw = (chat_func([{"role": "user", "content": prompt}]) or "").strip()
        # 去掉可能的 ```json 围栏
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        import json as _json
        characters = _json.loads(raw)
        if not isinstance(characters, list):
            return

        for ch in characters:
            name = (ch.get("name") or "").strip()
            desc = (ch.get("description") or "").strip()
            if name and name not in existing_names and len(name) <= 20:
                db.add_character_proposal(session_id, name, desc)
                logger.info("检测到新人物提案: %s - %s", name, desc[:50])
    except Exception as e:
        logger.error("人物检测失败: %s", e)


def _detect_relationships(session_id):
    """检测已有人物之间的关系 → 自动写入 relations 表（用户可在关系面板修改/删除）"""
    try:
        people = db.get_people(session_id)
        if len(people) < 2:
            return  # 少于2个人物不可能有关系

        existing_relations = db.get_relations(session_id)
        existing_pairs = set()
        for r in existing_relations:
            pair = tuple(sorted([r["person_a_id"], r["person_b_id"]]))
            existing_pairs.add(pair)

        chat_func = get_llm_chat_func("deepseek")
        if not chat_func:
            return

        all_msgs = db.load_messages(session_id)
        recent_msgs = all_msgs[-10:] if len(all_msgs) > 10 else all_msgs
        context_lines = []
        for m in recent_msgs:
            role = "用户" if m["role"] == "user" else "AI"
            context_lines.append(f"{role}：{m['content'][:200]}")
        recent_context = "\n".join(context_lines)

        name_to_id = {p["name"]: p["id"] for p in people}
        people_list = "\n".join([f"- {p['name']}：{p.get('description', '')}" for p in people])

        prompt = get_prompt(
            "detect_relationship",
            "根据以下对话和人物列表，判断这些人物之间存在什么关系。\n\n"
            "人物列表：\n{{PEOPLE_LIST}}\n\n"
            "最近对话：\n{{RECENT_CONTEXT}}\n\n"
            "要求：\n"
            "1. 只提取人物列表中已知人物之间的关系\n"
            "2. 关系类型用简洁的中文（如：父女、母女、姐妹、夫妻、朋友、同事、师生等）\n"
            "3. person_a 和 person_b 必须是人物列表中的名字\n"
            "4. 没有明确关系则输出空数组\n\n"
            '只输出JSON：\n'
            '[{"person_a": "人名", "relation": "关系类型", "person_b": "人名"}]',
            PEOPLE_LIST=people_list,
            RECENT_CONTEXT=recent_context,
        )

        raw = (chat_func([{"role": "user", "content": prompt}]) or "").strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        import json as _json
        rels = _json.loads(raw)
        if not isinstance(rels, list):
            return

        for rel in rels:
            name_a = (rel.get("person_a") or "").strip()
            name_b = (rel.get("person_b") or "").strip()
            rel_type = (rel.get("relation") or "").strip()

            if name_a not in name_to_id or name_b not in name_to_id:
                continue
            if not rel_type or len(rel_type) > 20:
                continue

            id_a = name_to_id[name_a]
            id_b = name_to_id[name_b]
            pair = tuple(sorted([id_a, id_b]))

            if pair in existing_pairs:
                continue  # 已有关系，跳过（不覆盖用户修改）

            db.add_relation(id_a, rel_type, id_b, session_id)
            existing_pairs.add(pair)
            logger.info("自动添加关系: %s -%s- %s", name_a, rel_type, name_b)
    except Exception as e:
        logger.error("关系检测失败: %s", e)


async def history_handler(request):
    """获取对话历史

    ⚠️ 必须无条件从数据库读：以前以内存 state 非空为前提，
    重启后内存是空的 → 首次打开页面历史全白（要点一下会话才恢复）。
    """
    session_id = _resolve_session(request)
    state = get_state(session_id)

    # 内存没有历史时（典型场景：刚重启），从数据库灌回内存——
    # 不光为了界面显示，LLM 的 conversation_history 也需要它，
    # 否则重启后第一轮聊天会"失忆"
    if state and not state.conversation_history:
        db_messages = db.load_messages(session_id)
        if db_messages:
            state.conversation_history = [
                {"role": m["role"], "content": m["content"], "timestamp": m.get("timestamp", "")}
                for m in db_messages
            ]
            state.message_count = len(state.conversation_history)

    # 分页参数：limit>0 时只返回最近 limit 条（offset 从最早开始数，供上滚加载更早）
    try:
        limit = int(request.query.get("limit") or 0)
        offset = int(request.query.get("offset") or 0)
    except ValueError:
        limit, offset = 0, 0

    db_messages = db.load_messages(session_id)
    total = len(db_messages)
    if limit > 0:
        db_messages = db_messages[offset:offset + limit]

    history = [{
        "role": m["role"],
        "content": m["content"],
        "timestamp": m.get("timestamp", "")
    } for m in db_messages]

    return web.json_response({
        "history": history,
        "total": total,
        # 当前会话记住的模型（首次打开页面时前端要恢复）
        "model": db.get_session_model(session_id),
    })


async def catchup_handler(request):
    """打开页面/切回会话时调用：她在这段时间里有没有留过言。

    这是"你不在的时候她给你发消息"的入口。
    离开够久才生成（>= proactive.MIN_AWAY_HOURS），并且同一段时间只生成一次。
    """
    global _current_session_id
    try:
        data = await request.json()
    except Exception:
        data = {}

    session_id = data.get("session_id") or _resolve_session(request)
    mode = data.get("mode", "deepseek")
    if not session_id:
        return web.json_response({"messages": []})

    try:
        import proactive
        loop = asyncio.get_event_loop()
        msgs = await loop.run_in_executor(
            None,
            lambda: proactive.maybe_generate(session_id, proactive.make_llm_call(mode)),
        )
        return web.json_response({"messages": msgs or []})
    except Exception as e:
        logger.error("catchup 失败: %s", e)
        return web.json_response({"messages": [], "error": str(e)})


async def sessions_handler(request):
    """获取会话列表"""
    sessions = db.get_all_sessions()
    result = []
    for s in sessions:
        msg_count = db.get_message_count(s["id"])
        result.append({
            "id": s["id"],
            "title": s["title"],
            "created": s["created"],
            "msg_count": msg_count
        })

    return web.json_response({
        "sessions": result,
        "current": _current_session_id
    })


async def new_chat_handler(request):
    """新建会话"""
    global _current_session_id

    # 保存当前会话
    if _current_session_id:
        state = get_state(_current_session_id)
        if state and state.conversation_history:
            db.update_session(_current_session_id)

    # 读取用户指定的标题（可选）和要继承的模型
    custom_title = None
    inherit_model = ""
    try:
        data = await request.json()
        custom_title = data.get("title", "").strip() or None
        # 新会话继承"当前正在用的模型"，体验上更连贯
        inherit_model = (data.get("model") or "").strip()
    except Exception:
        pass

    # 创建新会话（没给自定义标题就用时间当默认名，别用"新对话"跟按钮撞名）
    _current_session_id = db.new_session_id()
    db.create_session(_current_session_id,
                      title=custom_title or default_session_title(),
                      title_locked=bool(custom_title))
    db.create_message_table(_current_session_id)
    if inherit_model:
        db.set_session_model(_current_session_id, inherit_model)

    # 初始化新会话状态
    _session_states[_current_session_id] = SessionState()

    return web.json_response({"session_id": _current_session_id})


async def switch_chat_handler(request):
    """切换会话"""
    global _current_session_id

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "无效的JSON"}, status=400)

    session_id = data.get("session_id")
    if not session_id:
        return web.json_response({"error": "缺少 session_id"}, status=400)

    # 保存当前会话
    if _current_session_id:
        old_state = get_state(_current_session_id)
        if old_state and old_state.conversation_history:
            db.update_session(_current_session_id)

    # 切换到目标会话
    _current_session_id = session_id
    messages = db.load_messages(session_id)

    state = get_state(session_id)
    # ⚠️ timestamp 必须带上——丢了它 AI 就没有"日期感"，
    # 跨天的对话会被当成刚刚连续聊的（v0.4.2 修过一次，但这条路径漏了）
    state.conversation_history = [
        {"role": m["role"], "content": m["content"], "timestamp": m.get("timestamp", "")}
        for m in messages
    ]
    state.message_count = len(state.conversation_history)

    return web.json_response({
        "session_id": session_id,
        "history": state.conversation_history,
        # 这个会话记住的模型（前端切过去要恢复，否则会沿用上一个会话的）
        "model": db.get_session_model(session_id),
    })


async def set_model_handler(request):
    """记住"这个会话用哪个模型"。

    这样对话1 可以固定用 DeepSeek、对话2 固定用 agnes，
    切换会话时各自记着自己的，不用每次手动改。
    """
    global _current_session_id
    try:
        data = await request.json()
    except Exception:
        data = {}

    session_id = data.get("session_id") or _current_session_id
    model = (data.get("model") or "").strip()
    if not session_id:
        return web.json_response({"error": "缺少 session_id"}, status=400)

    db.set_session_model(session_id, model)
    return web.json_response({"ok": True, "model": model})


async def clear_chat_handler(request):
    """清空会话（支持指定 session_id，不带则用当前会话）"""
    try:
        data = await request.json()
    except Exception:
        data = {}
    session_id = data.get("session_id") or _resolve_session(request)
    if session_id:
        db.drop_message_table(session_id)
        db.create_message_table(session_id)
        state = get_state(session_id)
        if state:
            state.conversation_history = []
            state.message_count = 0

    return web.json_response({"ok": True})


async def delete_session_handler(request):
    """删除会话"""
    global _current_session_id

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "无效的JSON"}, status=400)

    session_id = data.get("session_id")
    if not session_id:
        return web.json_response({"error": "缺少 session_id"}, status=400)

    db.delete_session(session_id)
    db.delete_session_pins(session_id)

    # 清理状态缓存
    _session_states.pop(session_id, None)

    # 如果删除的是当前会话，切换到剩下的最后一个
    if _current_session_id == session_id:
        sessions = db.get_all_sessions()
        if sessions:
            _current_session_id = sessions[0]["id"]
            messages = db.load_messages(_current_session_id)
            state = get_state(_current_session_id)
            state.conversation_history = [{"role": m["role"], "content": m["content"]} for m in messages]
            state.message_count = len(state.conversation_history)
        else:
            # v0.13.0：删光了就不再自动补一个空会话。
            # 原来这里会新建，是为保证聊天页永远有会话可用；
            # 现在应用入口是引导页，没会话时 /chat 会自己跳回引导页，
            # 再自动造一个只会给引导页塞一张"空对话"卡片。
            _current_session_id = None

    return web.json_response({"ok": True, "remaining": len(db.get_all_sessions())})


async def search_history_handler(request):
    """搜索历史消息（按会话隔离）"""
    query = request.query.get("q", "")
    if not query:
        return web.json_response({"results": []})

    session_id = _resolve_session(request)
    results = db.search_messages(query, session_id=session_id)
    return web.json_response({"results": results[:50]})


async def _latest_summary(session_id):
    """取最近一条摘要（导出时当剧情概要用）。

    v0.13.1 新增：summaries 表在自动摘要时已经写过，正常情况下直接复用，不花 LLM。
    只有"聊得还不够长、还没触发过自动摘要"的会话才会现场生成一次——
    用 prompts.json 的 summary 键（简洁版，100字），因为导出文件开头只需要一句概括，
    不值得生成详细版。生成失败就返回空串，导出格式退回原样，不阻断下载。
    """
    if not session_id:
        return ""
    try:
        rows = db.get_recent_summaries(session_id, limit=1)
        if rows and (rows[0]["summary"] or "").strip():
            return rows[0]["summary"].strip()
    except Exception:
        return ""

    # 没有现成摘要 → 现场生成一次简版（导出是用户主动操作，等一下可接受）
    try:
        msgs = db.load_messages(session_id)
        if len(msgs) < 4:
            return ""                      # 消息太少，概括不出来
        chat_text = "".join(
            f"{'用户' if m['role'] == 'user' else 'AI'}：{(m['content'] or '')[:200]}\n"
            for m in msgs[-30:]
        )
        # LLM 调用放线程池，别阻塞 aiohttp 事件循环（导出请求本来就同步等着）
        chat_func = get_llm_chat_func("deepseek")
        if not chat_func:
            return ""
        out = (await asyncio.to_thread(chat_func, [
            {"role": "system", "content": get_prompt(
                "summary",
                "你是摘要助手。请用简洁中文总结以下对话的核心情节与关键信息，100字以内。"
                "人物关系的变化和重要约定必须保留。只输出摘要正文。"
            )},
            {"role": "user", "content": chat_text},
        ]) or "").strip()
        if out:
            db.save_summary(session_id, out)
        return out
    except Exception as e:
        logger.warning("导出时生成概要失败（不影响导出）: %s", e)
        return ""


async def export_chat_handler(request):
    """导出聊天记录"""
    try:
        fmt = request.query.get("format", "json")
        session_id = _resolve_session(request)

        # 获取会话信息
        session = db.get_session(session_id) if session_id else None
        title = (session.get("title") if session else None) or "未命名对话"

        # 获取消息：内存里有就直接用（省一次查询），否则从数据库加载
        state = get_state(session_id)
        if state and state.conversation_history:
            all_messages = state.conversation_history
        else:
            if not session_id:
                return web.json_response({"error": "未选择会话"}, status=400)
            db_messages = db.load_messages(session_id)
            all_messages = [{"role": m["role"], "content": m["content"]} for m in db_messages]

        # 只导出 AI 回复，过滤掉用户消息和摘要内容
        messages = [m for m in all_messages if m["role"] == "assistant"]

        # v0.13.1：导出时带上剧情概要。
        # 数据来源是已有的 summaries 表（自动摘要时已经写过）——不额外花 LLM 调用。
        # 之前导出的 md/txt 只是一堆干巴巴的回复，没有上下文，
        # 隔几天再看根本想不起来这段在聊什么。
        overview = await _latest_summary(session_id)

        if fmt == "markdown":
            content = f"# {title}\n\n"
            if overview:
                content += f"> **剧情概要**：{overview}\n\n"
            for msg in messages:
                content += f"{msg['content']}\n\n---\n\n"
            return web.Response(
                body=content.encode("utf-8"),
                content_type="text/markdown",
                headers={"Content-Disposition": f"attachment; filename={title}.md"}
            )
        elif fmt == "txt":
            content = f"{title}\n{'='*40}\n\n"
            if overview:
                content += f"【剧情概要】{overview}\n\n{'─'*40}\n\n"
            for msg in messages:
                content += f"{msg['content']}\n\n{'─'*40}\n\n"
            return web.Response(
                body=content.encode("utf-8"),
                content_type="text/plain",
                headers={"Content-Disposition": f"attachment; filename={title}.txt"}
            )
        else:
            data = {
                "session_id": session_id,
                "title": title,
                "summary": overview,
                "messages": [{"role": "assistant", "content": m["content"]} for m in messages],
                "export_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            return web.Response(
                body=json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"),
                content_type="application/json",
                headers={"Content-Disposition": f"attachment; filename={title}.json"}
            )
    except Exception as e:
        logger.error("导出失败: %s", e)
        return web.json_response({"error": str(e)}, status=500)


async def rename_session_handler(request):
    """重命名会话"""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "无效的JSON"}, status=400)

    session_id = data.get("session_id", _current_session_id)
    new_title = data.get("title", "").strip()

    if not new_title:
        return web.json_response({"error": "标题不能为空"}, status=400)

    db.rename_session(session_id, new_title)
    return web.json_response({"ok": True})


async def update_system_prompt_handler(request):
    """更新系统提示词"""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "无效的JSON"}, status=400)

    session_id = data.get("session_id", _current_session_id)
    system_prompt = data.get("system_prompt", "")

    db.update_system_prompt(session_id, system_prompt)
    return web.json_response({"ok": True})


async def get_system_prompt_handler(request):
    """获取系统提示词"""
    session_id = request.query.get("session_id", _current_session_id)
    session = db.get_session(session_id)
    if session:
        return web.json_response({"system_prompt": session.get("system_prompt", "")})
    return web.json_response({"system_prompt": ""})


async def search_chat_handler(request):
    """搜索聊天记录"""
    query = request.query.get("q", "").strip()
    if not query:
        return web.json_response({"results": []})

    results = db.search_messages(query)
    return web.json_response({"results": results[:50]})


async def summarize_handler(request):
    """生成聊天摘要"""
    session_id = _resolve_session(request)
    state = get_state(session_id)
    if not state or not state.conversation_history:
        return web.json_response({"summary": "暂无聊天内容"})

    # 组装对话文本
    chat_text = ""
    for msg in state.conversation_history[-20:]:  # 最近20条
        role = "用户" if msg["role"] == "user" else "AI"
        chat_text += f"{role}: {msg['content']}\n"

    # 调用 LLM 生成摘要
    try:
        session = db.get_session(session_id)
        llm_mode = request.query.get("mode", "deepseek")
        chat_func = get_llm_chat_func(llm_mode)

        if not chat_func:
            return web.json_response({"summary": "LLM 未配置，无法生成摘要"})

        messages = [
            # v0.13.1：原来这行文案硬编码在这里，prompts.json 的 summary / summary_detail
            # 成了没人读的死配置（改了没效果）。改成走配置层，两边不再维护两份。
            {"role": "system", "content": get_prompt(
                "summary_detail",
                "你是一个摘要助手。请用简洁的中文总结以下对话的主要内容，"
                "包括：1.对话主题 2.关键信息 3.用户的需求或关注点。摘要控制在100字以内。"
            )},
            {"role": "user", "content": chat_text}
        ]
        summary = chat_func(messages)

        # 摘要保存到 summaries 表，不再覆盖会话标题
        if summary:
            db.save_summary(session_id, summary)

        return web.json_response({"summary": summary})
    except Exception as e:
        return web.json_response({"error": f"生成摘要失败: {e}"}, status=500)


async def cancel_chat_handler(request):
    """打断当前正在进行的 LLM 生成"""
    try:
        data = await request.json()
    except Exception:
        data = {}
    session_id = data.get("session_id") or _resolve_session(request)
    state = get_state(session_id)
    if state:
        state.cancel_event.set()
    return web.json_response({"ok": True})


async def get_proposals_handler(request):
    """获取指定会话的待确认人物提案"""
    session_id = _resolve_session(request)
    proposals = db.get_pending_proposals(session_id)
    return web.json_response({"proposals": proposals})


async def accept_proposal_handler(request):
    """接受人物提案 → 写入 people 表 → 标记提案 accepted"""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "无效的JSON"}, status=400)
    proposal_id = data.get("proposal_id")
    if not proposal_id:
        return web.json_response({"error": "缺少 proposal_id"}, status=400)

    result = db.accept_proposal(proposal_id)
    if not result:
        return web.json_response({"error": "提案不存在或已处理"}, status=404)

    # 写入人物表
    db.add_person(result["name"], description=result["description"], session_id=result["session_id"])
    return web.json_response({"ok": True, "name": result["name"]})


async def reject_proposal_handler(request):
    """拒绝人物提案"""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "无效的JSON"}, status=400)
    proposal_id = data.get("proposal_id")
    if not proposal_id:
        return web.json_response({"error": "缺少 proposal_id"}, status=400)
    db.reject_proposal(proposal_id)
    return web.json_response({"ok": True})
