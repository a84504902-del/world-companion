"""她主动发消息 —— 你不在的时候她给你留的话

## 这是整个项目最初那个痛点的终点

    "我出门了" …… 三小时 …… "我回来了"
                     ↑ 这段原来是空的

现在：回来时（打开页面 / 切回会话）触发一次检查，如果离开够久，
就生成 1-3 条她在这段时间里**单方面**发给你的消息，按时间分布写进历史。

## 为什么按时间分布

不能几条都堆在"现在"。第一条应该是刚分开不久发的，最后一条才是最近的——
这样你打开对话，看到的是"她这几小时里断断续续想你的痕迹"，而不是"她刚发了一串"。
"""
import json
import logging
import os
import re
from datetime import datetime, timedelta

import db

logger = logging.getLogger("proactive")

FMT = "%Y-%m-%d %H:%M:%S"

MIN_AWAY_HOURS = 2.0      # 离开不足这么久，不生成（否则太吵）
MAX_MESSAGES = 3          # 一次最多几条


def _parse(s):
    try:
        return datetime.strptime(s, FMT)
    except Exception:
        return None


def _human(hours):
    if hours < 1:
        return f"{int(hours * 60)} 分钟"
    if hours < 24:
        return f"{int(hours)} 小时"
    days = int(hours // 24)
    rest = int(hours % 24)
    return f"{days} 天" + (f" {rest} 小时" if rest else "")


def _persona_brief():
    """从 persona_anchor.json 取一小段人设，供生成时参考"""
    try:
        import config
        path = os.path.join(config.BASE_DIR, "persona_anchor.json")
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        bits = []
        if d.get("persona"):
            bits.append(str(d["persona"]))
        traits = d.get("core_traits") or []
        if traits:
            bits.append("性格：" + "、".join(str(t) for t in traits[:4]))
        if d.get("speech_style"):
            bits.append("说话风格：" + str(d["speech_style"]))
        return "；".join(bits)
    except Exception:
        return ""


_PROMPT = """你是一个角色扮演的辅助写手。请写出「她」在这段时间里主动发给对方的消息。

## 时间背景
- 现实里，对方已经 **{away_desc}** 没出现了
- 但剧情里，她那边只过了 **{story_desc}**（所以不要写成"好几天没见"）

## 她现在的状态
- 她在哪：{place}
- 她在做什么：{her_doing}

## 她惦记的事（最多一条消息提到，不要条条都在催）
{loops}

## 人设参考
{persona}

## 要求
- 写 **{n}** 条她**单方面**发给他的消息（他一直没有回复）
- 每条 1-2 句，像微信留言，短
- **要能感觉到时间在流动**：第一条是刚分开不久，最后一条是最近
- 内容可以是：想他、分享自己在做什么、问他怎么样了、提到惦记的事
- 最后一条适合收个尾（"我先睡了""看到记得回我"之类）
- 不要空洞的"在吗""你在吗"——没人回，就该自己往下生活
- 语气符合人设：温柔、粘人、有点小委屈但不会闹

## 输出
只输出 JSON 数组，不要任何解释文字：
[{{"content": "消息内容", "minutes_after": 从分开算起的分钟数}}]
"""

_SYSTEM = "你是一个角色扮演辅助写手。只输出要求的 JSON 数组，不要任何解释。"


def _is_error(text):
    return (not text) or ("出错了" in text) or ("配置未找到" in text)


def make_llm_call(preferred_mode="deepseek"):
    """带回退的 LLM 调用（跟承诺抽取、场景提取同一套模式）"""
    import llm as _llm

    def _call(messages):
        payload = messages[-1]["content"]
        text = _llm.chat(payload, mode=preferred_mode,
                         system_prompt=_SYSTEM, history=[])
        if _is_error(text):
            fallback = "deepseek" if preferred_mode != "deepseek" else "agnes"
            if fallback != preferred_mode:
                logger.warning("留言生成模型 %s 不可用，回退到 %s", preferred_mode, fallback)
                text = _llm.chat(payload, mode=fallback, system_prompt=_SYSTEM, history=[])
        return text

    return _call


def _how_many(away_hours):
    if away_hours < 6:
        return 2
    if away_hours < 24:
        return 3
    return 3


def maybe_generate(session_id, llm_call):
    """检查是否需要生成留言；需要则生成并写入。

    返回新生成的留言列表 [{"content":..., "timestamp":...}]，不需要则为空列表。
    """
    try:
        clock = db.get_story_clock(session_id)
        if not clock or not clock.get("last_seen"):
            return []

        now = datetime.now()
        last_seen = _parse(clock["last_seen"])
        if not last_seen:
            return []

        away_hours = (now - last_seen).total_seconds() / 3600.0
        if away_hours < MIN_AWAY_HOURS:
            return []

        # 这段时间已经生成过了就别重复
        lp = _parse(db.get_last_proactive(session_id) or "")
        if lp and lp >= last_seen:
            return []

        # 剧情里过了多久
        story_hours = away_hours
        try:
            import timeflow
            story_hours = timeflow.world_elapsed(away_hours)
        except Exception:
            pass

        sc = db.get_scene(session_id) or {}
        pending = db.get_open_loops(session_id, status="pending")
        loops_text = "\n".join(f"- {x['content']}" for x in pending[:3]) or "- （没有特别惦记的事）"

        n = _how_many(away_hours)
        prompt = _PROMPT.format(
            away_desc=_human(away_hours),
            story_desc=_human(story_hours),
            place=sc.get("place") or "家",
            her_doing=sc.get("her_doing") or "一个人待着",
            loops=loops_text,
            persona=_persona_brief() or "温柔、粘人的女朋友",
            n=n,
        )

        raw = llm_call([{"role": "user", "content": prompt}])
        if not raw:
            return []

        m = re.search(r"\[.*\]", raw, re.S)
        if not m:
            logger.warning("留言生成未返回数组: %s", raw[:150])
            return []

        items = json.loads(m.group(0))
        if not isinstance(items, list):
            return []

        away_minutes = away_hours * 60
        created = []
        for it in items[:MAX_MESSAGES]:
            content = (it.get("content") or "").strip()
            if not content:
                continue
            try:
                after = float(it.get("minutes_after") or 0)
            except (TypeError, ValueError):
                after = 0
            # 夹在 [1, 离开时长] 之内，并留 1 分钟余量，避免撞到"现在"
            after = max(1.0, min(after, away_minutes - 1)) if away_minutes > 2 else 1.0
            ts = (last_seen + timedelta(minutes=after)).strftime(FMT)
            db.save_message(session_id, "proactive", content, timestamp=ts)
            created.append({"content": content, "timestamp": ts})

        if created:
            db.set_last_proactive(session_id, now.strftime(FMT))
            logger.info("生成 %d 条主动留言（离开 %s）", len(created), _human(away_hours))

        return created
    except Exception as e:
        logger.error("生成留言失败: %s", e)
        return []
