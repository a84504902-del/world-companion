"""未完结事件（承诺与后果）—— 系统核心引擎

原则：**只锁过去，不锁未来。**
锁的是"你说过什么"，从不锁"接下来发生什么"。

主循环：
    你说了/答应了什么 → 记成未完结事件 → 到期或被勾起 → 产生后果 → 生成新的事 → 循环

设计文档见 docs/承诺与后果-设计.md
"""
import json
import logging
import re
from datetime import datetime, timedelta

import db

logger = logging.getLogger("loops")

# ============ 预判：命中才调 LLM，避免每轮都花钱 ============
_TIME_WORDS = [
    "明天", "明晚", "后天", "周末", "下周", "下个", "下次", "改天",
    "以后", "等我有空", "回头", "过几天", "到时候", "今晚", "今晚",
]
_PROMISE_WORDS = [
    "答应", "保证", "一定", "说好了", "说好", "带你", "给你",
    "陪你", "请你", "给你买", "一起", "我请你", "等着我",
]
_MIN_LEN = 4

# 提醒冷却：同一条承诺多久内不重复提（小时）
REMIND_COOLDOWN_HOURS = 6


def looks_like_promise(text):
    """快速预判这句话像不像一个承诺。不命中就直接跳过，不调 LLM。"""
    if not text or len(text) < _MIN_LEN:
        return False
    has_time = any(w in text for w in _TIME_WORDS)
    has_promise = any(w in text for w in _PROMISE_WORDS)
    return has_time or has_promise


_EXTRACT_PROMPT = """判断下面这句话里，有没有一件**他明确答应、约定要为她做**的事。

对方说的话：{user_text}

判断标准：
- 必须是**他明确说出来的约定**：带她去做什么、给她什么、陪她做什么、答应她什么。
- **只是陈述他自己的安排，不算** —— 即使这个安排可能影响见面：
  · "我明天要开会" -> 不算（他没说要陪她，也没约定任何事）
  · "明天开会，晚上不能陪你了" -> 算（他明确说了不陪她）
  · "今天好累" -> 不算
  · "我出门上班了" -> 不算
- 敷衍回应（好啊、嗯嗯、知道了、行吧）不算。
- 角色扮演里的台词不算。
- **严禁推理、严禁补充**：content 只能包含原话里出现过的信息，
  绝对不要添加"因此不能陪她""所以她会失望"这类推断出来的内容。

如果有，只输出一行 JSON，不要任何其他文字：
{{"has": true, "content": "用她的视角重写的约定，一句话", "due": "明天/周末/下次/等我有空/以后/无", "weight": 3}}

weight 取值 1-5：
1=随口一提  2=普通期待  3=认真说的  4=跟情绪挂钩  5=核心承诺

如果没有，只输出：
{{"has": false}}"""


_EXTRACT_SYSTEM = "你是一个严谨的信息抽取器。严格按要求的 JSON 格式输出，不要任何解释文字。"


def _is_error(text):
    """LLM 返回的是不是一条错误信息（而非正常结果）"""
    return (not text) or ("出错了" in text) or ("配置未找到" in text)


def make_llm_call(preferred_mode="deepseek"):
    """构造带失败回退的 LLM 调用函数。

    抽取任务优先用当前对话选的模型；失败则回退到另一个，
    避免某个服务的 key 失效时整个承诺捕获静默失效。
    """
    import llm as _llm

    def _call(messages):
        payload = messages[-1]["content"]
        text = _llm.chat(payload, mode=preferred_mode,
                         system_prompt=_EXTRACT_SYSTEM, history=[])
        if _is_error(text):
            fallback = "deepseek" if preferred_mode != "deepseek" else "agnes"
            if fallback != preferred_mode:
                logger.warning("抽取模型 %s 不可用，回退到 %s", preferred_mode, fallback)
                text = _llm.chat(payload, mode=fallback,
                                 system_prompt=_EXTRACT_SYSTEM, history=[])
        return text

    return _call


def _find_similar_pending(session_id, content, threshold=0.95):
    """找出已有的、几乎重复的未完结承诺（避免同一件事被记成多条）。

    ⚠️ 阈值必须 > 0.95，不能凭感觉定低。
    实测（本地 embedding 模型）：
      "他说明天带我去吃火锅" / "他答应明天带我去吃火锅"   -> 0.998  (同一件事)
      "他说明天带我去吃火锅" / "他明天带我去看电影"       -> 0.887  (不同的事！)
      "他答应周末陪我去海边看日出" / "他说周末去看日出"   -> 0.813  (同一件事，换说法)
    中文短句结构相同、只有动作不同时相似度虚高，
    阈值定 0.85 会把"吃火锅"和"看电影"误判成同一件事。
    所以宁可漏掉"换说法"的情况，也不能把两件事搞混。
    """
    try:
        import embedding
        if not embedding.is_ready():
            return None
        new_vec = embedding.embed_text(content)
        if not new_vec:
            return None
        best, best_score = None, 0.0
        for lp in db.get_open_loops(session_id, status="pending"):
            old_vec = embedding.embed_text(lp["content"])
            if not old_vec:
                continue
            score = embedding.cosine_similarity(new_vec, old_vec)
            if score > best_score:
                best, best_score = lp, score
        if best and best_score >= threshold:
            logger.info("发现重复承诺（%.3f）: %s", best_score, best["content"])
            return best
    except Exception as e:
        logger.warning("重复承诺检查失败（跳过去重）: %s", e)
    return None


def extract_loop(user_text, session_id, llm_call):
    """从用户的一句话里抽取承诺。

    llm_call: 接受 messages 列表、返回文本的函数（与 llm.chat 一致）
    返回 loop_id（新建的或命中的已有记录），或 None。
    """
    if not looks_like_promise(user_text):
        return None

    try:
        messages = [{"role": "user", "content": _EXTRACT_PROMPT.format(user_text=user_text[:500])}]
        raw = llm_call(messages)
        if not raw:
            return None

        m = re.search(r"\{[^{}]*\}", raw, re.S)
        if not m:
            return None
        data = json.loads(m.group(0))
        if not data.get("has"):
            return None

        content = (data.get("content") or "").strip()
        if not content:
            return None

        try:
            weight = max(1, min(5, int(data.get("weight") or 2)))
        except (TypeError, ValueError):
            weight = 2

        # 同一件事说多次 -> 只加权重、重置冷却，不新建（否则她会连着几天提同一件事）
        dup = _find_similar_pending(session_id, content)
        if dup:
            db.update_open_loop(
                dup["id"],
                weight=min(5, max(int(dup.get("weight") or 2), weight) + 1),
                last_reminded_at=None,
            )
            logger.info("承诺重复，升级权重: %s", dup["content"])
            return dup["id"]

        loop_id = db.save_open_loop(
            session_id, content,
            raw_quote=user_text[:200],
            promisor="user",
            due_at=_resolve_due(data.get("due") or "无"),
            weight=weight,
        )
        logger.info("捕获承诺: id=%s content=%s weight=%s", loop_id, content, weight)
        return loop_id
    except Exception as e:
        logger.error("承诺抽取失败: %s", e)
        return None


def _resolve_due(due_word):
    """把模糊时间词解析成具体时间点；无法识别返回 None（永不主动催）"""
    now = datetime.now()
    w = (due_word or "").strip()

    if "今晚" in w:
        target = now
    elif "明天" in w or "明晚" in w:
        target = now + timedelta(days=1)
    elif "后天" in w:
        target = now + timedelta(days=2)
    elif "周末" in w:
        delta = (5 - now.weekday()) % 7      # 本周六
        target = now + timedelta(days=delta if delta else 7)
    elif "下周" in w or "下个" in w:
        target = now + timedelta(days=7)
    elif "下次" in w:
        target = now + timedelta(days=7)
    elif ("有空" in w or "改天" in w or "过几天" in w
          or "回头" in w or "到时候" in w):
        target = now + timedelta(days=14)
    elif "以后" in w or "将来" in w:
        target = now + timedelta(days=90)
    else:
        return None

    return target.replace(hour=23, minute=59, second=0).strftime("%Y-%m-%d %H:%M:%S")


# ============ 注入：告诉她"她在等什么" ============
def _ago(created_at):
    """把创建时间变成人类语言"""
    try:
        dt = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return "之前"
    days = (datetime.now() - dt).days
    if days <= 0:
        hours = max(1, (datetime.now() - dt).seconds // 3600)
        return f"{hours} 小时前"
    if days == 1:
        return "昨天"
    if days < 7:
        return f"{days} 天前"
    if days < 60:
        return f"{days // 7} 周前"
    return "很久以前"


def _in_cooldown(loop):
    """提醒冷却期内不再重复提（weight 高的除外）"""
    if (loop.get("weight") or 0) >= 4:
        return False
    last = loop.get("last_reminded_at")
    if not last:
        return False
    try:
        dt = datetime.strptime(last, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return False
    return (datetime.now() - dt) < timedelta(hours=REMIND_COOLDOWN_HOURS)


def build_loops_prompt(session_id, max_items=1):
    """生成注入 prompt 的『她在等的事』段落。

    硬约束：最多 1 条。她每句话都在催，那就不是女朋友，是讨债公司。
    """
    try:
        loops = db.get_open_loops(session_id, status="pending")
    except Exception as e:
        logger.error("读取未完结事件失败: %s", e)
        return ""

    if not loops:
        return ""

    now = datetime.now()
    due_list = []
    for lp in loops:
        if not lp.get("due_at"):
            continue                              # 无期限的承诺，从不被催
        try:
            due_dt = datetime.strptime(lp["due_at"], "%Y-%m-%d %H:%M:%S")
        except Exception:
            continue
        if due_dt > now:
            continue                              # 还没到期
        if _in_cooldown(lp):
            continue
        due_list.append((lp, (now - due_dt).days))

    if not due_list:
        return ""

    due_list.sort(key=lambda x: (-x[0]["weight"], x[1]))
    picked = due_list[:max_items]

    lines = []
    for lp, days_late in picked:
        if lp["weight"] >= 4:
            tone = ("她一直记着这件事，心里是藏着的失落，不是在责怪。"
                    "你这轮要找个自然的时机提一次，语气轻轻的、带点委屈，说完就过，绝不追问。")
        else:
            tone = ("她记得这件事。你这轮随口提一次，像突然想起来的那种，"
                    "一句带过，不要展开。")
        late = f"已经过去 {days_late} 天" if days_late > 0 else "就是今天"
        lines.append(f"- {lp['content']}（{_ago(lp['created_at'])}答应的，{late}）。{tone}")

        try:
            db.update_open_loop(
                lp["id"],
                reminded_count=(lp.get("reminded_count") or 0) + 1,
                last_reminded_at=now.strftime("%Y-%m-%d %H:%M:%S"),
            )
        except Exception as e:
            logger.error("更新提醒计数失败: %s", e)

    if not lines:
        return ""

    header = (
        "【她在等的事 —— 本轮必须提一次，但只提一次】\n"
        "（注意：这条优先级高于「不主动推进剧情」的规则。"
        "提起你记得的、他答应过的事，属于回忆，不算推进剧情。）\n"
    )
    return header + "\n".join(lines)


# ============ 闭环与清理 ============
def mark_fulfilled(loop_id, outcome=""):
    """兑现：转成共同记忆"""
    db.close_open_loop(loop_id, "fulfilled", outcome)


def mark_expired(loop_id, outcome=""):
    """没做到：不删除，变成"她记得你没做到" """
    db.close_open_loop(loop_id, "expired", outcome)


def cleanup_stale_loops(session_id, days=30):
    """weight<=2 且长期没人提 → 不了了之。

    必须有这一步，否则承诺表会无限膨胀，
    半年后她会背着一万个没兑现的承诺跟你说话。
    """
    try:
        loops = db.get_open_loops(session_id, status="pending")
    except Exception:
        return 0
    now = datetime.now()
    n = 0
    for lp in loops:
        if (lp.get("weight") or 0) > 2:
            continue
        ref = lp.get("last_reminded_at") or lp.get("created_at")
        if not ref:
            continue
        try:
            dt = datetime.strptime(ref, "%Y-%m-%d %H:%M:%S")
        except Exception:
            continue
        if (now - dt) > timedelta(days=days):
            db.close_open_loop(lp["id"], "dropped", "双方都没再提起，自然淡了")
            n += 1
    if n:
        logger.info("清理不了了之的承诺: %s 条", n)
    return n
