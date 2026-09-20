"""剧情时间流 —— 用饱和函数替代常数倍数

## 为什么改

原来是 `剧情时间 = (现在 - 会话创建) × TIME_SCALE`，问题有三个：

1. **它是派生值**，不可暂停、不可回退、不可编辑 —— 你完全控制不了
2. **常数倍数没有上限**，离开 3 天世界就跑掉 72 天，"人就没了"
3. **离开和聊天用同一个比例**，可心理上这两者的时间根本不一样长

## 现在

- `story_now` 是**持久状态字段**，可推进、可回退、可手动快进
- 离开期间用**饱和函数**：`world = W_MAX * (1 - e^(-real/TAU))`
  短时间有加速效果，长时间自动封顶 —— 你离开一个月回来，世界也只走了一天多
- **你不在的时候推进得快，聊天时推进得慢**（每轮固定小步长）
"""
import math
import os
import logging
from datetime import datetime, timedelta

import db

logger = logging.getLogger("timeflow")

FMT = "%Y-%m-%d %H:%M:%S"

# 饱和曲线：离开再久，世界最多推进这么多小时
W_MAX_HOURS = float(os.environ.get("STORY_W_MAX_HOURS", "30"))
# 曲线陡峭程度（小时）：越小越快接近上限
TAU_HOURS = float(os.environ.get("STORY_TAU_HOURS", "12"))
# 小于这个间隔视为"一直在聊"，只走每轮步长（分钟）
MIN_GAP_MINUTES = float(os.environ.get("STORY_MIN_GAP_MINUTES", "5"))
# 对话中每轮推进的剧情分钟数
PER_TURN_MINUTES = float(os.environ.get("STORY_PER_TURN_MINUTES", "5"))

_WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def world_elapsed(real_hours):
    """饱和函数：现实离开 N 小时，世界推进多少小时（有天花板）"""
    if real_hours <= 0:
        return 0.0
    return W_MAX_HOURS * (1.0 - math.exp(-real_hours / TAU_HOURS))


def _parse(s):
    try:
        return datetime.strptime(s, FMT)
    except Exception:
        return None


def ensure_clock(session_id):
    """确保会话有剧情时钟；没有就初始化（剧情时间从会话创建时刻起算）"""
    clock = db.get_story_clock(session_id)
    if clock is None:
        return None

    now_str = datetime.now().strftime(FMT)
    if not clock.get("story_now"):
        start = clock.get("created") or now_str
        db.set_story_clock(session_id, start, now_str)
        return {"story_now": start, "last_seen": now_str, "created": clock.get("created")}
    if not clock.get("last_seen"):
        db.set_story_clock(session_id, clock["story_now"], now_str)
        clock["last_seen"] = now_str
    return clock


def settle(session_id):
    """结算一次时间。

    - 一直在聊（间隔 < MIN_GAP）→ 只推进每轮的小步长
    - 离开了一段时间 → 用饱和函数补上，但有上限

    返回 dict：story_now / advanced_hours / away_hours
    """
    clock = ensure_clock(session_id)
    if clock is None:
        return None

    now = datetime.now()
    story = _parse(clock["story_now"]) or now
    last = _parse(clock["last_seen"]) or now

    gap_minutes = (now - last).total_seconds() / 60.0
    if gap_minutes < 0:
        gap_minutes = 0.0

    if gap_minutes < MIN_GAP_MINUTES:
        advanced_hours = PER_TURN_MINUTES / 60.0
        away_hours = 0.0
    else:
        away_hours = gap_minutes / 60.0
        advanced_hours = world_elapsed(away_hours)

    story = story + timedelta(hours=advanced_hours)
    story_str = story.strftime(FMT)
    db.set_story_clock(session_id, story_str, now.strftime(FMT))

    logger.info("时间结算: 离开 %.1f 小时 → 剧情推进 %.1f 小时", away_hours, advanced_hours)
    return {"story_now": story_str, "advanced_hours": advanced_hours, "away_hours": away_hours}


def advance(session_id, hours):
    """手动快进（正数）或回退（负数）剧情时间，单位小时"""
    clock = ensure_clock(session_id)
    if clock is None:
        return None
    story = _parse(clock["story_now"]) or datetime.now()
    story = story + timedelta(hours=float(hours))
    story_str = story.strftime(FMT)
    db.set_story_clock(session_id, story_str, datetime.now().strftime(FMT))
    return story_str


def _period(hour):
    if 5 <= hour < 11:
        return "早上"
    if 11 <= hour < 14:
        return "中午"
    if 14 <= hour < 18:
        return "下午"
    if 18 <= hour < 23:
        return "晚上"
    return "深夜"


def _human_span(hours):
    if hours < 1:
        return f"{max(1, int(hours * 60))} 分钟"
    if hours < 24:
        return f"{int(hours)} 小时"
    days = int(hours // 24)
    rest = int(hours % 24)
    return f"{days} 天" + (f" {rest} 小时" if rest else "")


def peek(session_id):
    """只读查询：不结算、不推进时间，只返回当前时间状态（给界面状态条用）"""
    clock = ensure_clock(session_id)
    if clock is None:
        return None
    story = _parse(clock["story_now"]) or datetime.now()
    now = datetime.now()
    created = _parse(clock.get("created") or "") or story
    return {
        "story_weekday": _WEEKDAYS[story.weekday()],
        "story_time": story.strftime("%H:%M"),
        "story_period": _period(story.hour),
        "story_day": int((story - created).total_seconds() // 86400) + 1,
        "real_weekday": _WEEKDAYS[now.weekday()],
        "real_time": now.strftime("%H:%M"),
        "real_period": _period(now.hour),
        "span_days": int((now - created).total_seconds() // 86400),
    }


def describe(session_id):
    """结算并生成注入 prompt 的时间文本"""
    try:
        res = settle(session_id)
    except Exception as e:
        logger.error("时间结算失败: %s", e)
        return ""

    if res is None:
        return ""

    clock = db.get_story_clock(session_id) or {}
    story = _parse(res["story_now"]) or datetime.now()
    created = _parse(clock.get("created") or "") or story

    now = datetime.now()

    # 剧情里过了多少天（从会话创建算起）
    story_days = int((story - created).total_seconds() // 86400)

    lines = ["【时间感知】"]
    lines.append(
        f"现实：{_WEEKDAYS[now.weekday()]} {now.strftime('%H:%M')}，{_period(now.hour)}"
        f"（跟他打招呼用这个）")
    lines.append(
        f"剧情：第 {story_days + 1} 天，{_WEEKDAYS[story.weekday()]} {story.strftime('%H:%M')}，"
        f"{_period(story.hour)}（场景里的时间）")

    # 这段关系的整体跨度：没有它，模型会以为所有对话都发生在"最近"
    span_days = int((now - created).total_seconds() // 86400)
    if span_days >= 1:
        lines.append(
            f"这段关系从 {created.strftime('%Y-%m-%d')} 开始，现实里已经过了 {span_days} 天")

    if res["away_hours"] >= 1:
        lines.append(
            f"上次见面：{_human_span(res['away_hours'])}前（现实）；"
            f"这期间她那边只过了 {_human_span(res['advanced_hours'])}")
    elif res["away_hours"] > 0:
        lines.append(f"上次见面：{int(res['away_hours'] * 60)} 分钟前，一直都在")
    else:
        lines.append("你们正聊着，没分开过")

    return "\n".join(lines)
