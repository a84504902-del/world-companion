"""场景状态 —— 她在哪、在干嘛、你在哪

## 为什么用独立 LLM 调用，而不是让主模型附带输出

先试过在 system prompt 里要求主模型在回复末尾附带 `[[STATE]]` 标记（零额外调用），
但本项目的人设很长（`persona_anchor` 30+ 条规则 + 时间 + 人物 + 关系 + 摘要 + 检索记忆），
指令被稀释，**实测遵守率只有 20%**，而且放最前、放最后都一样。

所以改成后台单独判断：多一次很短的调用，但可靠。跟承诺抽取是同一个模式。
"""
import json
import logging
import re

import db

logger = logging.getLogger("scene")

_SCENE_PROMPT = """根据下面这段对话，判断"现在"的场景状态。

当前已知场景：{current}

最近的对话：
{convo}

只输出一行 JSON，不要任何解释：
{{"place": "她在哪", "her_doing": "她正在做的具体事情", "user_place": "你在哪"}}

要求：
- 只根据对话内容判断，不要编造没提到的事
- place 填具体地点（家/公司/超市/餐厅/路上）。**对话没提到她在哪时，默认她在"家"**
- her_doing 写**具体的动作**（做饭/看电视/收拾房间/等你回来/热牛奶），
  不要写态度或情绪（比如"担心叮嘱""关心"这种不算动作）
- user_place 写你在哪（在外面/公司/地铁上/家）
- 每项都很短，不超过 10 字
"""

_EXTRACT_SYSTEM = "你是一个严谨的场景状态提取器。只输出一行 JSON，不要任何解释文字。"

# 这些占位词不算有效值，过滤掉以免污染状态条
_BAD_VALUES = {"未知", "不清楚", "不明", "无", "没有", "none", "n/a", "-", "null"}


def _clean(v):
    v = (v or "").strip()[:20]
    return "" if v.lower() in _BAD_VALUES else v


def _is_error(text):
    return (not text) or ("出错了" in text) or ("配置未找到" in text)


def make_llm_call(preferred_mode="deepseek"):
    """构造带回退的 LLM 调用（优先当前对话的模型，失败自动换一个）"""
    import llm as _llm

    def _call(messages):
        payload = messages[-1]["content"]
        text = _llm.chat(payload, mode=preferred_mode,
                         system_prompt=_EXTRACT_SYSTEM, history=[])
        if _is_error(text):
            fallback = "deepseek" if preferred_mode != "deepseek" else "agnes"
            if fallback != preferred_mode:
                logger.warning("场景提取模型 %s 不可用，回退到 %s", preferred_mode, fallback)
                text = _llm.chat(payload, mode=fallback,
                                 system_prompt=_EXTRACT_SYSTEM, history=[])
        return text

    return _call


def extract_scene(session_id, llm_call):
    """读最近几条对话，判断场景并写回。返回新场景 dict 或 None。"""
    try:
        messages = db.load_messages(session_id)
    except Exception as e:
        logger.error("读取对话失败: %s", e)
        return None

    if not messages:
        return None

    recent = messages[-6:]
    lines = []
    for m in recent:
        who = "用户（林风）" if m.get("role") == "user" else "她（夏雪）"
        lines.append(f"{who}：{str(m.get('content',''))[:120]}")
    convo = "\n".join(lines)

    cur = db.get_scene(session_id) or {}
    current = (f"地点={cur.get('place') or '未知'} | "
               f"她在={cur.get('her_doing') or '未知'} | "
               f"你={cur.get('user_place') or '未知'}")

    try:
        prompt = _SCENE_PROMPT.format(current=current, convo=convo)
        raw = llm_call([{"role": "user", "content": prompt}])
        if not raw:
            return None

        m = re.search(r"\{[^{}]*\}", raw, re.S)
        if not m:
            logger.warning("场景提取未返回 JSON: %s", raw[:120])
            return None

        data = json.loads(m.group(0))
        place = _clean(data.get("place"))
        her_doing = _clean(data.get("her_doing"))
        user_place = _clean(data.get("user_place"))

        if not (place or her_doing or user_place):
            return None

        db.set_scene(session_id, place=place, her_doing=her_doing, user_place=user_place)
        logger.info("场景更新: 地点=%s | 她在=%s | 你=%s", place, her_doing, user_place)
        return {"place": place, "her_doing": her_doing, "user_place": user_place}
    except Exception as e:
        logger.error("场景提取失败: %s", e)
        return None
